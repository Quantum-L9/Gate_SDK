"""
Typed transport failure closure (ADR-SDK-007).

The property under test is not "errors have nice names". It is that a consumer
can classify every Gate failure using Gate_SDK types alone — no ``httpx``
import, no ``str(exc)`` token matching. A real consumer shipped a token-matching
classifier against these exact failures and recorded a blank reason when an
httpx timeout stringified to "". That is the defect this closes.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import httpx
import pytest
from gate_client_helpers import (
    RecordingTransport,
    gate_echo_responder,
    make_client_config,
    make_root_packet,
)

from constellation_node_sdk.gate import errors as gate_errors
from constellation_node_sdk.gate.client import GateClient
from constellation_node_sdk.gate.errors import (
    GateClientError,
    GateConfigurationError,
    GateConnectionError,
    GateHTTPError,
    GatePolicyError,
    GateResponseError,
    GateSecurityError,
    GateTimeoutError,
)

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "constellation_node_sdk"


def _client(responder: Any, **config_overrides: Any) -> tuple[GateClient, RecordingTransport]:
    transport = RecordingTransport(responder)
    return GateClient(make_client_config(**config_overrides), transport=transport), transport


# ---------------------------------------------------------------------------
# Every category is reachable and distinct
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connection_failure_is_typed(domain_payload: dict[str, Any]) -> None:
    def explode(_request: httpx.Request, _attempt: int) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client, _ = _client(explode)
    with pytest.raises(GateConnectionError):
        await client.execute(action="converge", payload=domain_payload, tenant="tenant-a")


@pytest.mark.asyncio
async def test_timeout_is_typed_and_not_confused_with_connection_failure(
    domain_payload: dict[str, Any],
) -> None:
    """
    A timeout is a distinct outcome, not a generic connection failure.

    ``httpx.TimeoutException`` subclasses ``TransportError``, so an
    order-of-except mistake would silently collapse the two.
    """

    def stall(_request: httpx.Request, _attempt: int) -> httpx.Response:
        raise httpx.ReadTimeout("")

    client, _ = _client(stall)
    with pytest.raises(GateTimeoutError) as caught:
        await client.execute(action="converge", payload=domain_payload, tenant="tenant-a")

    assert not isinstance(caught.value, GateConnectionError)


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 409, 422, 429, 500, 502, 503, 504])
@pytest.mark.asyncio
async def test_http_rejection_carries_the_status(
    status_code: int, domain_payload: dict[str, Any]
) -> None:
    """
    The status is a structured attribute, not something to parse out of a message.

    ``is_client_error`` / ``is_server_error`` are the distinction a caller
    actually acts on: a 4xx will not succeed on retry, a 5xx might.
    """

    def reject(_request: httpx.Request, _attempt: int) -> httpx.Response:
        return httpx.Response(status_code, json={"detail": "no"})

    client, _ = _client(reject)
    with pytest.raises(GateHTTPError) as caught:
        await client.execute(action="converge", payload=domain_payload, tenant="tenant-a")

    assert caught.value.status_code == status_code
    assert caught.value.is_client_error is (400 <= status_code < 500)
    assert caught.value.is_server_error is (500 <= status_code < 600)
    assert caught.value.response_text is not None


@pytest.mark.asyncio
async def test_non_canonical_response_is_typed(domain_payload: dict[str, Any]) -> None:
    def wrong_shape(_request: httpx.Request, _attempt: int) -> httpx.Response:
        return httpx.Response(200, json={"state": "completed"})

    client, _ = _client(wrong_shape)
    with pytest.raises(GateResponseError):
        await client.execute(action="converge", payload=domain_payload, tenant="tenant-a")


@pytest.mark.asyncio
async def test_integrity_failure_is_a_security_error_not_a_shape_error(
    domain_payload: dict[str, Any],
) -> None:
    """
    A tampered response is untrusted, not merely malformed.

    Collapsing the two would let a caller treat a broken hash as a Gate dialect
    problem and retry into it.
    """
    import json

    def tamper(request: httpx.Request, _attempt: int) -> httpx.Response:
        sent = json.loads(request.content.decode("utf-8"))
        from constellation_node_sdk.transport.packet import TransportPacket

        response = gate_echo_responder({"state": "completed"})(request, 1)
        body = json.loads(response.content.decode("utf-8"))
        TransportPacket.model_validate(body)  # sanity: it was canonical before tampering
        body["payload"]["state"] = "mutated"
        assert sent
        return httpx.Response(200, json=body)

    client, _ = _client(tamper)
    with pytest.raises(GateSecurityError) as caught:
        await client.execute(action="converge", payload=domain_payload, tenant="tenant-a")

    assert caught.value.direction == "inbound"
    assert not isinstance(caught.value, GateResponseError)


@pytest.mark.asyncio
async def test_misconfigured_signing_is_a_configuration_error(
    domain_payload: dict[str, Any],
) -> None:
    """A local misconfiguration is not a Gate failure and never reaches the wire."""
    client, transport = _client(
        gate_echo_responder({"state": "completed"}),
        signing_key="secret",
        signing_key_id=None,
        signing_algorithm=None,
    )

    with pytest.raises(GateConfigurationError):
        await client.execute(action="converge", payload=domain_payload, tenant="tenant-a")

    assert transport.requests == []


@pytest.mark.asyncio
async def test_policy_rejection_is_typed_and_never_reaches_the_wire() -> None:
    from constellation_node_sdk.transport.packet import create_transport_packet
    from constellation_node_sdk.transport.provenance import RoutingProvenance

    packet = create_transport_packet(
        action="converge",
        payload={"entity_id": "42"},
        tenant="tenant-a",
        source_node="odoo",
        destination_node="enrich",
        reply_to="odoo",
        provenance=RoutingProvenance(
            origin_kind="node",
            requested_action="converge",
            resolved_by_gate=False,
            original_source_node="odoo",
        ),
    )
    client, transport = _client(gate_echo_responder({}))

    with pytest.raises(GatePolicyError):
        await client.send_to_gate(packet)

    assert transport.requests == []


# ---------------------------------------------------------------------------
# Closure properties
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error_name",
    [
        "GateConfigurationError",
        "GateConnectionError",
        "GateHTTPError",
        "GatePolicyError",
        "GateRegistrationError",
        "GateResponseError",
        "GateSecurityError",
        "GateTimeoutError",
    ],
)
def test_every_gate_error_descends_from_one_base(error_name: str) -> None:
    """``except GateClientError`` is a complete catch for Gate transport failures."""
    error_type = getattr(gate_errors, error_name)
    assert issubclass(error_type, GateClientError)


@pytest.mark.asyncio
async def test_a_consumer_can_classify_without_importing_httpx(
    domain_payload: dict[str, Any],
) -> None:
    """
    The whole point: retryable vs permanent decided from SDK types alone.

    This is the classifier a consumer would otherwise ship as ~40 lines of
    substring matching over ``str(exc)``.
    """

    def classify(exc: GateClientError) -> str:
        if isinstance(exc, GateTimeoutError | GateConnectionError):
            return "retryable"
        if isinstance(exc, GateHTTPError):
            return "retryable" if exc.is_server_error else "permanent"
        return "permanent"

    cases: list[tuple[Any, str]] = [
        (lambda _r, _a: (_ for _ in ()).throw(httpx.ConnectError("")), "retryable"),
        (lambda _r, _a: (_ for _ in ()).throw(httpx.ReadTimeout("")), "retryable"),
        (lambda _r, _a: httpx.Response(503, json={}), "retryable"),
        (lambda _r, _a: httpx.Response(403, json={}), "permanent"),
        (lambda _r, _a: httpx.Response(200, json={"nope": True}), "permanent"),
    ]

    for responder, expected in cases:
        client, _ = _client(responder)
        with pytest.raises(GateClientError) as caught:
            await client.execute(action="converge", payload=domain_payload, tenant="tenant-a")
        assert classify(caught.value) == expected


@pytest.mark.asyncio
async def test_no_gate_failure_message_is_empty(domain_payload: dict[str, Any]) -> None:
    """
    Every typed failure names something, even when the cause stringifies to "".

    A consumer stored ``validation_issues=[""]`` because httpx's ConnectTimeout
    carried no text. The type name is always available; use it.
    """
    responders: list[Any] = [
        lambda _r, _a: (_ for _ in ()).throw(httpx.ConnectTimeout("")),
        lambda _r, _a: (_ for _ in ()).throw(httpx.ConnectError("")),
        lambda _r, _a: httpx.Response(500, content=b""),
        lambda _r, _a: httpx.Response(200, content=b"<html/>", headers={"Content-Type": "text/html"}),
    ]

    for responder in responders:
        client, _ = _client(responder)
        with pytest.raises(GateClientError) as caught:
            await client.execute(action="converge", payload=domain_payload, tenant="tenant-a")
        assert str(caught.value).strip()


@pytest.mark.asyncio
async def test_typed_failures_chain_the_underlying_cause(
    domain_payload: dict[str, Any],
) -> None:
    """Structured context is preserved, not flattened away."""

    def explode(_request: httpx.Request, _attempt: int) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client, _ = _client(explode)
    with pytest.raises(GateConnectionError) as caught:
        await client.execute(action="converge", payload=domain_payload, tenant="tenant-a")

    assert isinstance(caught.value.__cause__, httpx.ConnectError)


@pytest.mark.asyncio
async def test_send_to_gate_shares_the_same_taxonomy() -> None:
    """The packet-native primitive is not a second, untyped failure surface."""

    def reject(_request: httpx.Request, _attempt: int) -> httpx.Response:
        return httpx.Response(500, json={})

    client, _ = _client(reject)
    with pytest.raises(GateHTTPError):
        await client.send_to_gate(make_root_packet())


@pytest.mark.asyncio
async def test_health_failures_are_typed_too() -> None:
    """``health()`` is part of the client surface and obeys the same taxonomy."""

    def explode(_request: httpx.Request, _attempt: int) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client, _ = _client(explode)
    with pytest.raises(GateConnectionError):
        await client.health()


def test_backward_compatible_value_error_aliases() -> None:
    """
    The three categories that previously surfaced as ``ValueError`` still do.

    Callers written against the pre-taxonomy SDK keep working; new callers get
    the specific type.
    """
    for error_type in (GatePolicyError, GateResponseError, GateConfigurationError):
        assert issubclass(error_type, ValueError)
    assert issubclass(GateTimeoutError, TimeoutError)


def test_the_client_module_raises_no_bare_valueerror() -> None:
    """
    Static guard: no ``raise ValueError`` survives in the client or its policy.

    A single bare raise reopens the string-matching hole this closes.
    """
    for module in ("gate/client.py", "gate/policy.py"):
        source = (SRC_ROOT / module).read_text(encoding="utf-8")
        assert "raise ValueError(" not in source, module


def test_execute_documents_the_failures_it_raises() -> None:
    """The taxonomy is part of the published contract, not folklore."""
    doc = inspect.getdoc(GateClient.execute) or ""
    for name in (
        "GateConfigurationError",
        "GatePolicyError",
        "GateSecurityError",
        "GateConnectionError",
        "GateTimeoutError",
        "GateHTTPError",
        "GateResponseError",
    ):
        assert name in doc, name


# ---------------------------------------------------------------------------
# Outbound ordering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_routing_policy_is_checked_before_signing() -> None:
    """
    A packet that must not leave is rejected before any crypto runs.

    Signing a packet the client is about to refuse to send is wasted work, and
    it would surface a signing-configuration error where a routing violation is
    the real problem.
    """
    from constellation_node_sdk.transport.packet import create_transport_packet
    from constellation_node_sdk.transport.provenance import RoutingProvenance

    peer_targeted = create_transport_packet(
        action="converge",
        payload={"entity_id": "42"},
        tenant="tenant-a",
        source_node="odoo",
        destination_node="enrich",
        reply_to="odoo",
        provenance=RoutingProvenance(
            origin_kind="node",
            requested_action="converge",
            resolved_by_gate=False,
            original_source_node="odoo",
        ),
    )
    # Signing configuration is simultaneously broken: a signing key with no key
    # id. Policy must still win, because it is the actual defect.
    client, transport = _client(
        gate_echo_responder({}),
        signing_key="secret",
        signing_key_id=None,
        signing_algorithm=None,
    )

    with pytest.raises(GatePolicyError):
        await client.send_to_gate(peer_targeted)

    assert transport.requests == []


@pytest.mark.asyncio
async def test_outbound_transport_validation_judges_the_signed_packet(
    domain_payload: dict[str, Any],
) -> None:
    """
    With ``require_signature=True`` a self-signing node can actually send.

    Validating before signing checked an artifact that differed from the one
    sent, and rejected every packet for a signature it was about to add.
    """
    import json

    transport = RecordingTransport(gate_echo_responder({"state": "completed"}))
    client = GateClient(
        make_client_config(
            require_signature=True,
            signing_key="shared-secret",
            signing_key_id="odoo-key-1",
            signing_algorithm="hmac-sha256",
            verifying_keys={"odoo-key-1": "shared-secret"},
        ),
        transport=transport,
    )

    await client.execute(action="converge", payload=domain_payload, tenant="tenant-a")

    sent = json.loads(transport.requests[0].content.decode("utf-8"))
    assert sent["security"]["signature"] is not None
    assert sent["security"]["signing_key_id"] == "odoo-key-1"
