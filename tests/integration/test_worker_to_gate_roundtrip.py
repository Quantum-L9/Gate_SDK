from __future__ import annotations

import httpx
import pytest

from constellation_node_sdk.gate.client import GateClient
from constellation_node_sdk.gate.config import GateClientConfig
from constellation_node_sdk.transport.packet import create_transport_packet
from constellation_node_sdk.transport.provenance import RoutingProvenance


class FakeGateTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = request.read().decode("utf-8")
        assert request.url.path == "/v1/execute"
        assert '"destination_node":"gate"' in body

        response_packet = create_transport_packet(
            action="score",
            payload={"status": "completed", "score": 91},
            tenant="tenant-a",
            destination_node="worker-a",
            source_node="gate",
            reply_to="gate",
        )
        return httpx.Response(
            status_code=200,
            json=response_packet.model_dump_json_dict(),
            request=request,
        )


@pytest.mark.asyncio
async def test_worker_to_gate_roundtrip() -> None:
    original_async_client = httpx.AsyncClient

    class PatchedAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = FakeGateTransport()
            super().__init__(*args, **kwargs)

    httpx.AsyncClient = PatchedAsyncClient  # type: ignore[assignment]
    try:
        client = GateClient(
            GateClientConfig(
                gate_url="http://gate:9000",
                local_node="worker-a",
                timeout_seconds=5.0,
                require_signature=False,
                verify_response_signatures=False,
            )
        )

        request_packet = create_transport_packet(
            action="score",
            payload={"entity_id": "42"},
            tenant="tenant-a",
            destination_node="gate",
            source_node="worker-a",
            reply_to="worker-a",
            provenance=RoutingProvenance(
                origin_kind="node",
                requested_action="score",
                resolved_by_gate=False,
                original_source_node="worker-a",
            ),
        )

        response = await client.send_to_gate(request_packet)
        assert response.payload["status"] == "completed"
        assert response.payload["score"] == 91
    finally:
        httpx.AsyncClient = original_async_client  # type: ignore[assignment]
