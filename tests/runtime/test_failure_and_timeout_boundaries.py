"""
Track C — a failing or overrunning handler must not produce a success.

PR #37 proved at the ``execute_transport_packet`` level that a raising or
timing-out handler is invoked once and is not retried. This file takes the
same two failures to the HTTP boundary, where the node actually answers
Gate, and asserts what comes back on the wire: a failure packet that still
carries the request's correlation, and never a manufactured domain result.

The distinction matters because ``return_transport_errors`` makes the node
answer ``200`` with a failure packet. A caller that read only the status
code would see success; what makes it a failure is the packet, so the
packet is what is asserted.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from constellation_node_sdk.runtime.app import create_node_app
from constellation_node_sdk.runtime.config import NodeRuntimeConfig
from constellation_node_sdk.runtime.execution import execute_transport_packet
from constellation_node_sdk.runtime.handlers import clear_handlers, register_handler
from constellation_node_sdk.transport.codec import encode_transport_packet
from constellation_node_sdk.transport.packet import TransportPacket, create_transport_packet
from constellation_node_sdk.transport.provenance import RoutingProvenance
from constellation_node_sdk.transport.tenant import TenantContext

CORRELATION_ID = "corr-failure-boundary-1"
IDEMPOTENCY_KEY = "odoo:enrichment:res.partner:77"

# The shape a caller would wrongly conclude "it worked" from.
SUCCESS_SHAPE_KEYS = ("state", "fields")


class HandlerExploded(RuntimeError):
    """A domain failure raised from inside an application handler."""


def _worker_config(node_name: str = "worker") -> NodeRuntimeConfig:
    return NodeRuntimeConfig(
        environment="test",
        node_name=node_name,
        service_name="worker-node",
        service_version="1.0.0",
        dev_mode=True,
        require_signature=False,
        expose_internal_errors=False,
        return_transport_errors=True,
        signing_algorithm="hmac-sha256",
        signing_key=None,
        allowed_actions=("converge",),
        allowed_packet_types=("request",),
        enforce_gate_only_ingress=False,
        max_packet_bytes=262_144,
        max_attachments=0,
        max_attachment_size_bytes=0,
    )


def _request(tenant: TenantContext, *, timeout_ms: int = 5_000) -> TransportPacket:
    return create_transport_packet(
        action="converge",
        payload={"entity": {"id": "res.partner:77"}, "objective": "enrich"},
        tenant=tenant,
        source_node="gate",
        destination_node="worker",
        reply_to="gate",
        correlation_id=CORRELATION_ID,
        idempotency_key=IDEMPOTENCY_KEY,
        timeout_ms=timeout_ms,
        provenance=RoutingProvenance(
            origin_kind="gate",
            requested_action="converge",
            resolved_by_gate=True,
            original_source_node="odoo",
        ),
    )


def _post(request: TransportPacket) -> dict[str, Any]:
    app = create_node_app(config=_worker_config(), auto_register_with_gate=False)
    with TestClient(app) as client:
        response = client.post("/v1/execute", json=encode_transport_packet(request))
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, dict)
    return body


def _assert_not_a_success(body: dict[str, Any]) -> None:
    """The response is a failure packet, and carries no manufactured result."""
    assert body["header"]["packet_type"] == "failure"
    assert body["payload"]["status"] == "failed"
    for key in SUCCESS_SHAPE_KEYS:
        assert key not in body["payload"], (
            f"a failed execution produced {key!r}, which reads as a domain result"
        )


# ---------------------------------------------------------------------------
# Handler failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_raising_handler_produces_no_response_packet(tenant: TenantContext) -> None:
    """Called directly, the failure propagates instead of becoming a result."""
    calls: list[int] = []

    clear_handlers()

    @register_handler("converge")
    async def handle(_org_id: str, _payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(1)
        raise HandlerExploded("provider unavailable")

    request = _request(tenant)
    with pytest.raises(HandlerExploded):
        await execute_transport_packet(request, node_name="worker", dev_mode=True)

    assert len(calls) == 1, "the runtime retried a failing handler"


def test_a_raising_handler_answers_gate_with_a_failure_packet(tenant: TenantContext) -> None:
    """At the HTTP boundary the node answers with a failure packet, not a result."""
    clear_handlers()

    @register_handler("converge")
    async def handle(_org_id: str, _payload: dict[str, Any]) -> dict[str, Any]:
        raise HandlerExploded("provider unavailable")

    request = _request(tenant)
    body = _post(request)

    _assert_not_a_success(body)
    assert body["payload"]["error"] == "HandlerExploded"


def test_the_failure_packet_stays_correlated_to_the_request(tenant: TenantContext) -> None:
    """
    A failure the caller cannot correlate is a failure the caller cannot act on.

    Gate matches responses by correlation and caches by idempotency key;
    both must survive the error path exactly as they survive the happy one.
    """
    clear_handlers()

    @register_handler("converge")
    async def handle(_org_id: str, _payload: dict[str, Any]) -> dict[str, Any]:
        raise HandlerExploded("provider unavailable")

    request = _request(tenant)
    body = _post(request)

    assert body["header"]["correlation_id"] == CORRELATION_ID
    assert body["header"]["idempotency_key"] == IDEMPOTENCY_KEY
    assert body["header"]["causation_id"] == str(request.header.packet_id)
    assert body["lineage"]["root_id"] == str(request.lineage.root_id)
    assert body["payload"]["packet_id"] == str(request.header.packet_id)


def test_internal_error_text_is_not_leaked_to_the_caller(tenant: TenantContext) -> None:
    """With ``expose_internal_errors`` off, the class name is the whole story."""
    clear_handlers()

    @register_handler("converge")
    async def handle(_org_id: str, _payload: dict[str, Any]) -> dict[str, Any]:
        raise HandlerExploded("secret upstream detail")

    body = _post(_request(tenant))

    assert "secret upstream detail" not in str(body)
    assert body["payload"]["message"] == "HandlerExploded"


def test_an_unregistered_action_does_not_produce_a_result(tenant: TenantContext) -> None:
    """No handler is a failure, not an empty success."""
    clear_handlers()

    body = _post(_request(tenant))

    _assert_not_a_success(body)


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runtime_enforces_the_packet_timeout_budget(tenant: TenantContext) -> None:
    """
    The budget enforced is the packet's own, not a constant.

    The handler would finish well within any default; it only overruns the
    50ms this packet asked for, so a runtime ignoring ``timeout_ms`` would
    let it through.
    """
    calls: list[int] = []

    clear_handlers()

    @register_handler("converge")
    async def handle(_org_id: str, _payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(1)
        await asyncio.sleep(2)
        return {"state": "completed", "fields": {}}

    request = _request(tenant, timeout_ms=50)
    with pytest.raises(TimeoutError) as caught:
        await execute_transport_packet(request, node_name="worker", dev_mode=True)

    assert "50ms" in str(caught.value)
    assert len(calls) == 1, "the runtime retried a timing-out handler"


def test_an_overrunning_handler_answers_gate_with_a_failure_packet(
    tenant: TenantContext,
) -> None:
    """A timeout at the HTTP boundary is a failure packet, never a partial result."""
    clear_handlers()

    @register_handler("converge")
    async def handle(_org_id: str, _payload: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(2)
        return {"state": "completed", "fields": {"website": "https://example.com"}}

    body = _post(_request(tenant, timeout_ms=50))

    _assert_not_a_success(body)
    assert body["payload"]["error"] == "TimeoutError"
    assert body["header"]["correlation_id"] == CORRELATION_ID


@pytest.mark.asyncio
async def test_a_handler_inside_the_budget_still_succeeds(tenant: TenantContext) -> None:
    """
    Guard against a vacuous timeout suite.

    If every handler timed out, the assertions above would pass while
    proving nothing about the budget.
    """
    clear_handlers()

    @register_handler("converge")
    async def handle(_org_id: str, _payload: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(0.01)
        return {"state": "completed", "fields": {"website": "https://example.com"}}

    response = await execute_transport_packet(
        _request(tenant, timeout_ms=5_000), node_name="worker", dev_mode=True
    )

    assert response.header.packet_type == "response"
    assert response.payload == {"state": "completed", "fields": {"website": "https://example.com"}}
