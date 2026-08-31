"""
SDK2 / SDK3 / SDK16 — GateClient is the only egress, and it adds nothing.

The client needs one URL: Gate's. It serializes the canonical packet, parses a
canonical packet back, and layers no retry policy of its own. Provider retries
belong to the application node; routing-level retry belongs to Gate.
"""

from __future__ import annotations

import json
from contextlib import contextmanager

import httpx
import pytest
from pydantic import ValidationError

from constellation_node_sdk.gate.client import GateClient
from constellation_node_sdk.gate.config import GateClientConfig
from constellation_node_sdk.transport.packet import TransportPacket, create_transport_packet
from constellation_node_sdk.transport.provenance import RoutingProvenance
from constellation_node_sdk.transport.tenant import TenantContext


class RecordingTransport(httpx.AsyncBaseTransport):
    """Records every attempt so a hidden retry layer cannot hide."""

    def __init__(self, responder) -> None:
        self._responder = responder
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.requests.append(request)
        return self._responder(request, len(self.requests))


@contextmanager
def patched_transport(transport: httpx.AsyncBaseTransport):
    original = httpx.AsyncClient

    class PatchedAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    httpx.AsyncClient = PatchedAsyncClient  # type: ignore[assignment]
    try:
        yield
    finally:
        httpx.AsyncClient = original  # type: ignore[assignment]


def _gate_config() -> GateClientConfig:
    return GateClientConfig(gate_url="http://gate:8000", local_node="odoo", timeout_seconds=5.0)


def _node_packet(tenant: TenantContext, payload: dict, **kwargs) -> TransportPacket:
    return create_transport_packet(
        action="converge",
        payload=payload,
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
        **kwargs,
    )


def test_gate_client_config_exposes_no_peer_url_surface() -> None:
    """SDK2: there is no field through which a node could name another node."""
    fields = set(GateClientConfig.model_fields)

    assert "gate_url" in fields
    url_fields = {name for name in fields if "url" in name}
    assert url_fields == {"gate_url"}


@pytest.mark.asyncio
async def test_gate_client_sends_the_canonical_packet_verbatim(
    tenant: TenantContext,
    domain_payload: dict,
) -> None:
    """The bytes on the wire are the packet's own canonical serialization."""
    request_packet = _node_packet(
        tenant,
        domain_payload,
        timeout_ms=45_000,
        idempotency_key="odoo:enrichment:123",
    )
    response_packet = create_transport_packet(
        action="converge",
        payload={"state": "completed", "fields": {"website": "https://example.com"}},
        tenant=tenant,
        source_node="gate",
        destination_node="odoo",
        reply_to="gate",
    )

    transport = RecordingTransport(
        lambda request, _attempt: httpx.Response(
            status_code=200,
            json=response_packet.model_dump_json_dict(),
            request=request,
        )
    )

    with patched_transport(transport):
        response = await GateClient(_gate_config()).send_to_gate(request_packet)

    assert len(transport.requests) == 1
    assert str(transport.requests[0].url) == "http://gate:8000/v1/execute"

    sent = json.loads(transport.requests[0].content.decode("utf-8"))
    assert sent == request_packet.model_dump_json_dict()
    assert sent["payload"] == domain_payload
    assert sent["header"]["idempotency_key"] == "odoo:enrichment:123"
    assert sent["header"]["timeout_ms"] == 45_000
    assert sent["address"]["destination_node"] == "gate"

    # The response is parsed as a canonical packet with its payload untouched.
    assert isinstance(response, TransportPacket)
    assert response.payload == {
        "state": "completed",
        "fields": {"website": "https://example.com"},
    }


@pytest.mark.asyncio
async def test_gate_client_does_not_retry_a_transport_failure(
    tenant: TenantContext,
    domain_payload: dict,
) -> None:
    """SDK16: one send is one attempt. Retry policy lives above or below, not here."""

    def _fail(request: httpx.Request, _attempt: int) -> httpx.Response:
        raise httpx.ConnectError("gate unreachable", request=request)

    transport = RecordingTransport(_fail)
    packet = _node_packet(tenant, domain_payload)

    with patched_transport(transport):
        client = GateClient(_gate_config())
        with pytest.raises(httpx.ConnectError):
            await client.send_to_gate(packet)

    assert len(transport.requests) == 1


@pytest.mark.asyncio
async def test_gate_client_does_not_retry_a_server_error(
    tenant: TenantContext,
    domain_payload: dict,
) -> None:
    transport = RecordingTransport(
        lambda request, _attempt: httpx.Response(status_code=503, json={}, request=request)
    )
    packet = _node_packet(tenant, domain_payload)

    with patched_transport(transport):
        client = GateClient(_gate_config())
        with pytest.raises(httpx.HTTPStatusError):
            await client.send_to_gate(packet)

    assert len(transport.requests) == 1


@pytest.mark.asyncio
async def test_gate_client_rejects_a_non_canonical_response_body(
    tenant: TenantContext,
    domain_payload: dict,
) -> None:
    """Fail closed: a bare application dict is not a transport response."""
    transport = RecordingTransport(
        lambda request, _attempt: httpx.Response(
            status_code=200,
            json={"state": "completed"},
            request=request,
        )
    )

    packet = _node_packet(tenant, domain_payload)

    with patched_transport(transport):
        client = GateClient(_gate_config())
        with pytest.raises(ValidationError):
            await client.send_to_gate(packet)

    assert len(transport.requests) == 1
