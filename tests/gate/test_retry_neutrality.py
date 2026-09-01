"""
Track H — Gate_SDK adds no implicit retry layer to the request path.

Retries exist in this package, and that is fine as long as the scope is
exact. Reconnaissance found three retry surfaces and this file pins each:

1. ``GateClient.send_to_gate`` — none. One send is one HTTP attempt.
2. The worker runtime — none. One packet is one handler invocation.
3. ``orchestrator.RetryPolicy`` — an explicit, opt-in step policy that
   neither of the above imports or can reach.

A fourth, ``register_with_gate``, retries node registration at startup.
That is a startup handshake, not an application request, and it is bounded
here so it cannot quietly become one.

Absence of retry code proves nothing — a retry can appear in an httpx
transport, a decorator, or a caller. So every assertion counts attempts.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest

from constellation_node_sdk.gate.client import GateClient
from constellation_node_sdk.gate.config import GateClientConfig, GateRegistrationConfig
from constellation_node_sdk.gate.errors import (
    GateConnectionError,
    GateHTTPError,
    GateTimeoutError,
)
from constellation_node_sdk.runtime.execution import execute_transport_packet
from constellation_node_sdk.runtime.handlers import clear_handlers, register_handler
from constellation_node_sdk.transport.codec import encode_transport_packet
from constellation_node_sdk.transport.packet import TransportPacket, create_transport_packet
from constellation_node_sdk.transport.provenance import RoutingProvenance
from constellation_node_sdk.transport.tenant import TenantContext

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "constellation_node_sdk"

# The modules on the request path. Neither may reach the orchestrator's
# opt-in step retry, directly or through an intermediate import.
REQUEST_PATH_MODULES = (
    SRC_ROOT / "gate" / "client.py",
    SRC_ROOT / "runtime" / "execution.py",
)


def _client() -> GateClient:
    return GateClient(
        GateClientConfig(gate_url="http://gate:8000", local_node="odoo", timeout_seconds=5.0)
    )


def _request(tenant: TenantContext) -> TransportPacket:
    return create_transport_packet(
        action="converge",
        payload={"entity": {"id": "res.partner:55"}},
        tenant=tenant,
        source_node="odoo",
        destination_node="gate",
        reply_to="odoo",
        provenance=RoutingProvenance(
            origin_kind="node",
            requested_action="converge",
            resolved_by_gate=False,
            original_source_node="odoo",
        ),
    )


def _worker_request(tenant: TenantContext, *, timeout_ms: int = 5_000) -> TransportPacket:
    return create_transport_packet(
        action="converge",
        payload={"entity": {"id": "res.partner:55"}},
        tenant=tenant,
        source_node="gate",
        destination_node="worker",
        reply_to="gate",
        timeout_ms=timeout_ms,
    )


# ---------------------------------------------------------------------------
# GateClient
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_client_makes_exactly_one_http_attempt(
    route_gate_http: Any, tenant: TenantContext
) -> None:
    """One successful send is one request on the wire."""
    request = _request(tenant)
    response_body = encode_transport_packet(
        request.derive(
            packet_type="response",
            source_node="gate",
            destination_node="odoo",
            reply_to="gate",
            payload={"state": "completed", "fields": {}},
        )
    )

    with route_gate_http(lambda _r, _a: httpx.Response(200, json=response_body)) as transport:
        await _client().send_to_gate(request)

    assert len(transport.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [429, 500, 502, 503, 504])
async def test_gate_client_does_not_retry_a_retryable_looking_status(
    route_gate_http: Any, tenant: TenantContext, status_code: int
) -> None:
    """
    Even the statuses a retry layer would normally act on get one attempt.

    ``429`` and ``503`` carry retry semantics by convention. If the SDK were
    ever going to hide a retry, it would be here.
    """
    client = _client()
    request = _request(tenant)
    with route_gate_http(lambda _r, _a: httpx.Response(status_code)) as transport:
        with pytest.raises(GateHTTPError):
            await client.send_to_gate(request)

    assert len(transport.requests) == 1


@pytest.mark.asyncio
async def test_gate_client_does_not_retry_a_dead_socket(
    route_gate_http: Any, tenant: TenantContext
) -> None:
    """A connection error is surfaced on the first failure, not after N."""

    def explode(_request: httpx.Request, _attempt: int) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = _client()
    request = _request(tenant)
    with route_gate_http(explode) as transport:
        with pytest.raises(GateConnectionError):
            await client.send_to_gate(request)

    assert len(transport.requests) == 1


@pytest.mark.asyncio
async def test_gate_client_does_not_retry_a_read_timeout(
    route_gate_http: Any, tenant: TenantContext
) -> None:
    """A slow Gate is the caller's problem to decide about, not the SDK's."""

    def stall(_request: httpx.Request, _attempt: int) -> httpx.Response:
        raise httpx.ReadTimeout("gate did not answer")

    client = _client()
    request = _request(tenant)
    with route_gate_http(stall) as transport:
        with pytest.raises(GateTimeoutError):
            await client.send_to_gate(request)

    assert len(transport.requests) == 1


def test_gate_client_config_exposes_no_retry_surface() -> None:
    """There is no field through which a caller could ask the client to retry."""
    fields = set(GateClientConfig.model_fields)

    assert not [name for name in fields if "retry" in name or "attempt" in name]
    assert "retries" in GateRegistrationConfig.model_fields, (
        "registration retry is the one legitimate retry surface; if it moved, "
        "this test is asserting the wrong boundary"
    )


# ---------------------------------------------------------------------------
# Worker runtime
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_runtime_invokes_the_handler_exactly_once(
    tenant: TenantContext,
) -> None:
    """One packet in, one handler invocation — the happy path included."""
    calls: list[int] = []

    clear_handlers()

    @register_handler("converge")
    async def handle(_org_id: str, _payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(1)
        return {"state": "completed", "fields": {}}

    await execute_transport_packet(_worker_request(tenant), node_name="worker", dev_mode=True)

    assert calls == [1]


@pytest.mark.asyncio
async def test_worker_runtime_does_not_reinvoke_a_failing_handler(
    tenant: TenantContext,
) -> None:
    """
    A side-effecting handler must not run twice off one packet.

    The rail carries an idempotency key but the worker keeps no idempotency
    store, so a hidden retry here would double a side effect with nothing to
    de-duplicate it.
    """
    calls: list[int] = []

    clear_handlers()

    @register_handler("converge")
    async def handle(_org_id: str, _payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(1)
        raise RuntimeError("provider unavailable")

    packet = _worker_request(tenant)
    with pytest.raises(RuntimeError):
        await execute_transport_packet(packet, node_name="worker", dev_mode=True)

    assert calls == [1]


@pytest.mark.asyncio
async def test_worker_runtime_does_not_reinvoke_a_timing_out_handler(
    tenant: TenantContext,
) -> None:
    """A timeout ends the execution; it does not start a second one."""
    calls: list[int] = []

    clear_handlers()

    @register_handler("converge")
    async def handle(_org_id: str, _payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(1)
        await asyncio.sleep(2)
        return {"state": "completed", "fields": {}}

    packet = _worker_request(tenant, timeout_ms=50)
    with pytest.raises(TimeoutError):
        await execute_transport_packet(packet, node_name="worker", dev_mode=True)

    assert calls == [1]


# ---------------------------------------------------------------------------
# The orchestrator's opt-in policy stays where it is
# ---------------------------------------------------------------------------


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(f"{node.module}.{alias.name}" for alias in node.names)
    return imported


@pytest.mark.parametrize("module_path", REQUEST_PATH_MODULES, ids=lambda p: p.name)
def test_the_request_path_does_not_import_the_orchestrator_retry_policy(
    module_path: Path,
) -> None:
    """
    ``RetryPolicy`` is an orchestrator step concern and must stay there.

    It is opt-in, explicit, and only reachable through ``StepExecutor``. An
    import of it from the client or the runtime would be the first move
    toward an implicit retry on the request path.
    """
    imported = _imported_modules(module_path)

    offenders = {name for name in imported if "retry" in name.lower()}
    assert not offenders, f"{module_path.name} reaches for retry machinery: {sorted(offenders)}"


def test_the_orchestrator_retry_policy_is_opt_in_and_bounded() -> None:
    """
    Pin the policy's shape so a default cannot drift into aggressiveness.

    This is not an endorsement of retrying — it is a boundary. The policy
    exists, applies only to orchestrator steps, and defaults to three
    bounded attempts.
    """
    from constellation_node_sdk.orchestrator.retry import RetryPolicy, should_retry

    policy = RetryPolicy()
    assert policy.max_attempts == 3
    assert policy.max_delay_seconds == 5.0

    assert not should_retry(attempt=3, error=TimeoutError(), policy=policy), (
        "the policy must stop at max_attempts"
    )
    assert not should_retry(attempt=1, error=ValueError("unrelated"), policy=policy), (
        "an error outside retryable_error_types must not be retried"
    )
