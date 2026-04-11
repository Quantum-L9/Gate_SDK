from __future__ import annotations

import httpx
import pytest

from constellation_node_sdk.gate.client import GateClient
from constellation_node_sdk.gate.config import GateClientConfig
from constellation_node_sdk.transport.packet import create_transport_packet
from constellation_node_sdk.transport.provenance import RoutingProvenance


class MockTransport(httpx.AsyncBaseTransport):
    def __init__(self, response_body: dict) -> None:
        self._response_body = response_body
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(
            status_code=200,
            json=self._response_body,
            request=request,
        )


@pytest.mark.asyncio
async def test_gate_client_sends_canonical_packet_to_gate() -> None:
    response_packet = create_transport_packet(
        action="enrich",
        payload={"status": "completed"},
        tenant="tenant-a",
        destination_node="orchestrator",
        source_node="gate",
        reply_to="gate",
    )
    transport = MockTransport(response_packet.model_dump_json_dict())

    original_async_client = httpx.AsyncClient

    class PatchedAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    httpx.AsyncClient = PatchedAsyncClient  # type: ignore[assignment]
    try:
        config = GateClientConfig(
            gate_url="http://gate:8000",
            local_node="orchestrator",
            timeout_seconds=5.0,
            require_signature=False,
            signing_key=None,
            signing_key_id=None,
            signing_algorithm=None,
            verify_response_signatures=False,
            verifying_keys={},
            verify_hop_signatures=False,
            allowed_gate_destination="gate",
        )
        client = GateClient(config)

        packet = create_transport_packet(
            action="enrich",
            payload={"entity_id": "42"},
            tenant="tenant-a",
            destination_node="gate",
            source_node="orchestrator",
            reply_to="orchestrator",
            provenance=RoutingProvenance(
                origin_kind="node",
                requested_action="enrich",
                resolved_by_gate=False,
                original_source_node="orchestrator",
            ),
        )

        response = await client.send_to_gate(packet)

        assert response.payload["status"] == "completed"
        assert len(transport.requests) == 1
        assert str(transport.requests[0].url) == "http://gate:8000/v1/execute"
    finally:
        httpx.AsyncClient = original_async_client  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_gate_client_rejects_peer_targeted_packet_before_send() -> None:
    config = GateClientConfig(
        gate_url="http://gate:8000",
        local_node="orchestrator",
        timeout_seconds=5.0,
        require_signature=False,
        signing_key=None,
        signing_key_id=None,
        signing_algorithm=None,
        verify_response_signatures=False,
        verifying_keys={},
        verify_hop_signatures=False,
        allowed_gate_destination="gate",
    )
    client = GateClient(config)

    packet = create_transport_packet(
        action="enrich",
        payload={"entity_id": "42"},
        tenant="tenant-a",
        destination_node="enrich",
        source_node="orchestrator",
        reply_to="orchestrator",
        provenance=RoutingProvenance(
            origin_kind="node",
            requested_action="enrich",
            resolved_by_gate=False,
            original_source_node="orchestrator",
        ),
    )

    with pytest.raises(ValueError):
        await client.send_to_gate(packet)
