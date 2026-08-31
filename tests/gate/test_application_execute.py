"""
The application execution surface (ADR-SDK-002).

A normal application-to-Gate operation must be expressible through one
``GateClient`` call, using business inputs only. If any of these tests needs the
caller to build, inspect, or repair a ``TransportPacket``, the abstraction is
not closed and consumers will rebuild it themselves.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from gate_client_helpers import RecordingTransport, gate_echo_responder, make_client_config

from constellation_node_sdk.gate.client import GateClient
from constellation_node_sdk.gate.config import GateClientConfig
from constellation_node_sdk.gate.errors import (
    GateConfigurationError,
    GateHTTPError,
    GatePolicyError,
)
from constellation_node_sdk.transport.packet import TransportPacket
from constellation_node_sdk.transport.tenant import TenantContext


@pytest.fixture()
def config() -> GateClientConfig:
    return make_client_config(local_node="odoo")


@pytest.mark.asyncio
async def test_one_call_carries_business_inputs_only(
    config: GateClientConfig, domain_payload: dict[str, Any]
) -> None:
    """
    The whole application integration: action, payload, tenant, identity, budget.

    No packet construction, no destination, no signing call, no HTTP.
    """
    transport = RecordingTransport(gate_echo_responder({"state": "completed"}))
    client = GateClient(config, transport=transport)

    response = await client.execute(
        action="converge",
        payload=domain_payload,
        tenant="tenant-a",
        idempotency_key="odoo:enrichment:run-77",
        timeout_ms=25_000,
        correlation_id="corr-77",
    )

    assert isinstance(response, TransportPacket)
    assert response.payload["state"] == "completed"
    assert len(transport.requests) == 1
    assert str(transport.requests[0].url) == "http://gate:8000/v1/execute"


@pytest.mark.asyncio
async def test_execute_forces_the_gate_destination(
    config: GateClientConfig, domain_payload: dict[str, Any]
) -> None:
    """Intent is the action. Gate resolves ownership; the caller never names a node."""
    transport = RecordingTransport(gate_echo_responder({"state": "completed"}))
    client = GateClient(config, transport=transport)

    await client.execute(action="converge", payload=domain_payload, tenant="tenant-a")

    sent = transport.sent_packet(0)
    assert sent["address"]["destination_node"] == "gate"
    assert sent["provenance"]["origin_kind"] == "node"
    assert sent["provenance"]["resolved_by_gate"] is False


@pytest.mark.asyncio
async def test_execute_derives_source_and_reply_from_config(
    domain_payload: dict[str, Any],
) -> None:
    """Node identity comes from client configuration, never from the call site."""
    transport = RecordingTransport(gate_echo_responder({"state": "completed"}))
    client = GateClient(make_client_config(local_node="enrichment-engine"), transport=transport)

    await client.execute(action="converge", payload=domain_payload, tenant="tenant-a")

    sent = transport.sent_packet(0)
    assert sent["address"]["source_node"] == "enrichment-engine"
    assert sent["address"]["reply_to"] == "enrichment-engine"
    assert sent["provenance"]["original_source_node"] == "enrichment-engine"


@pytest.mark.asyncio
async def test_execute_exposes_no_destination_parameter() -> None:
    """
    There is no parameter through which an application could target a peer.

    ADR-SDK-002/003: the high-level API must not accept ``destination_node``,
    ``peer_url``, or ``worker_url``.
    """
    import inspect

    parameters = set(inspect.signature(GateClient.execute).parameters)
    forbidden = {"destination_node", "destination", "peer_url", "worker_url", "url", "node"}
    assert not (parameters & forbidden)


@pytest.mark.asyncio
async def test_execute_preserves_the_domain_payload_byte_for_byte(
    config: GateClientConfig,
) -> None:
    """The SDK transports domain payloads; it never interprets or supplements them."""
    payload = {
        "entity": {"id": "org-1", "fields": {"website": "example.test"}},
        "state": "pending",
        "nested": [{"a": 1}, {"b": [True, None, 2.5]}],
    }
    transport = RecordingTransport(gate_echo_responder({"state": "completed"}))
    client = GateClient(config, transport=transport)

    await client.execute(action="converge", payload=payload, tenant="tenant-a")

    assert transport.sent_packet(0)["payload"] == payload


@pytest.mark.asyncio
async def test_execute_carries_caller_idempotency_verbatim(
    config: GateClientConfig, domain_payload: dict[str, Any]
) -> None:
    """
    ADR-SDK-006: the caller owns business identity; the SDK owns its transport slot.

    No payload hash may silently replace the caller's operation identity.
    """
    key = "odoo:enrichment:durable-run-4711"
    transport = RecordingTransport(gate_echo_responder({"state": "completed"}))
    client = GateClient(config, transport=transport)

    await client.execute(
        action="converge", payload=domain_payload, tenant="tenant-a", idempotency_key=key
    )

    assert transport.sent_packet(0)["header"]["idempotency_key"] == key


@pytest.mark.asyncio
async def test_execute_without_idempotency_key_invents_nothing(
    config: GateClientConfig, domain_payload: dict[str, Any]
) -> None:
    """An absent business identity stays absent. The SDK does not fabricate one."""
    transport = RecordingTransport(gate_echo_responder({"state": "completed"}))
    client = GateClient(config, transport=transport)

    await client.execute(action="converge", payload=domain_payload, tenant="tenant-a")

    assert transport.sent_packet(0)["header"].get("idempotency_key") is None


@pytest.mark.asyncio
async def test_execute_propagates_correlation_and_trace(
    config: GateClientConfig, domain_payload: dict[str, Any]
) -> None:
    transport = RecordingTransport(gate_echo_responder({"state": "completed"}))
    client = GateClient(config, transport=transport)

    await client.execute(
        action="converge",
        payload=domain_payload,
        tenant="tenant-a",
        correlation_id="corr-9",
        trace_id="trace-9",
    )

    header = transport.sent_packet(0)["header"]
    assert header["correlation_id"] == "corr-9"
    assert header["trace_id"] == "trace-9"


@pytest.mark.asyncio
async def test_execute_defaults_correlation_to_the_packet_id(
    config: GateClientConfig, domain_payload: dict[str, Any]
) -> None:
    transport = RecordingTransport(gate_echo_responder({"state": "completed"}))
    client = GateClient(config, transport=transport)

    await client.execute(action="converge", payload=domain_payload, tenant="tenant-a")

    header = transport.sent_packet(0)["header"]
    assert header["correlation_id"] == header["packet_id"]
    assert header["trace_id"] == header["packet_id"]


@pytest.mark.asyncio
async def test_execute_normalizes_the_action(
    config: GateClientConfig, domain_payload: dict[str, Any]
) -> None:
    transport = RecordingTransport(gate_echo_responder({"state": "completed"}))
    client = GateClient(config, transport=transport)

    await client.execute(action="  CONVERGE  ", payload=domain_payload, tenant="tenant-a")

    sent = transport.sent_packet(0)
    assert sent["header"]["action"] == "converge"
    # provenance.requested_action must match, or Gate-only policy rejects the packet.
    assert sent["provenance"]["requested_action"] == "converge"


@pytest.mark.asyncio
async def test_execute_accepts_every_tenant_form(
    config: GateClientConfig, domain_payload: dict[str, Any], tenant: TenantContext
) -> None:
    """A tenant id, a mapping, and a TenantContext are all valid business inputs."""
    forms: list[Any] = [
        "tenant-a",
        {"actor": "tenant-a", "on_behalf_of": "tenant-a", "originator": "odoo"},
        tenant,
    ]
    for form in forms:
        transport = RecordingTransport(gate_echo_responder({"state": "completed"}))
        client = GateClient(config, transport=transport)
        await client.execute(action="converge", payload=domain_payload, tenant=form)
        assert transport.sent_packet(0)["tenant"]


@pytest.mark.asyncio
async def test_execute_rejects_a_blank_action(
    config: GateClientConfig, domain_payload: dict[str, Any]
) -> None:
    client = GateClient(config, transport=RecordingTransport(gate_echo_responder({})))
    with pytest.raises(GateConfigurationError):
        await client.execute(action="   ", payload=domain_payload, tenant="tenant-a")


@pytest.mark.asyncio
async def test_execute_cannot_produce_a_destination_policy_violation(
    domain_payload: dict[str, Any],
) -> None:
    """
    ``execute()`` targets the configured Gate identity, so it always passes policy.

    This is the closure that matters: an application cannot construct a
    peer-targeted packet through the high-level surface even by accident,
    because it never supplies a destination at all. Deployments that name Gate
    something other than ``"gate"`` are followed, not fought.
    """
    config = make_client_config(local_node="odoo", allowed_gate_destination="edge-gate")
    transport = RecordingTransport(gate_echo_responder({"state": "completed"}))
    client = GateClient(config, transport=transport)

    await client.execute(action="converge", payload=domain_payload, tenant="tenant-a")

    assert transport.sent_packet(0)["address"]["destination_node"] == "edge-gate"


@pytest.mark.asyncio
async def test_send_to_gate_rejects_a_peer_targeted_packet_with_a_typed_error(
    config: GateClientConfig,
) -> None:
    """
    The packet-native primitive still fails closed, now with a typed error.

    A packet-native caller can still aim at a peer; that rejection is a
    ``GatePolicyError``, not a bare ``ValueError`` the caller has to interpret.
    """
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
    transport = RecordingTransport(gate_echo_responder({}))
    client = GateClient(config, transport=transport)

    with pytest.raises(GatePolicyError):
        await client.send_to_gate(packet)

    # Rejected locally: the peer-targeted packet never reached the network.
    assert transport.requests == []


@pytest.mark.asyncio
async def test_execute_performs_exactly_one_request(
    config: GateClientConfig, domain_payload: dict[str, Any]
) -> None:
    """
    ADR-SDK-005: no hidden application retry.

    Absence of retry code proves nothing; a request count of one proves it.
    """

    def fail(_request: httpx.Request, _attempt: int) -> httpx.Response:
        return httpx.Response(503, json={"detail": "unavailable"})

    transport = RecordingTransport(fail)
    client = GateClient(config, transport=transport)

    with pytest.raises(GateHTTPError):
        await client.execute(action="converge", payload=domain_payload, tenant="tenant-a")

    assert len(transport.requests) == 1
