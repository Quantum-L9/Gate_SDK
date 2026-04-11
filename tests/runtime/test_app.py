from __future__ import annotations

from fastapi.testclient import TestClient

from constellation_node_sdk.runtime.app import create_node_app
from constellation_node_sdk.runtime.config import NodeRuntimeConfig
from constellation_node_sdk.runtime.handlers import clear_handlers, register_handler
from constellation_node_sdk.runtime.lifecycle import NoOpLifecycle
from constellation_node_sdk.transport.packet import create_transport_packet


def test_node_app_health_and_execute_endpoints_work() -> None:
    clear_handlers()

    @register_handler("score")
    async def handle_score(_tenant: str, payload: dict) -> dict:
        return {
            "status": "completed",
            "score": 91,
            "entity_id": payload["entity_id"],
        }

    config = NodeRuntimeConfig(
        environment="test",
        node_name="score",
        service_name="score-node",
        service_version="1.0.0",
        dev_mode=True,
        require_signature=False,
        expose_internal_errors=False,
        return_transport_errors=True,
        signing_algorithm="hmac-sha256",
        signing_key=None,
        signing_private_key=None,
        signing_key_id=None,
        verifying_keys={},
        allowed_actions=("score",),
        allowed_packet_types=("request",),
        require_idempotency_for_actions=(),
        allowed_clock_skew_seconds=30,
        max_packet_bytes=262_144,
        max_hop_depth=64,
        max_delegation_depth=8,
        max_attachments=0,
        max_attachment_size_bytes=0,
        attachment_allowed_schemes=(),
        allow_private_attachment_hosts=False,
        replay_enabled=True,
        verify_hop_signatures=False,
        gate_url="http://gate:8000",
        host="127.0.0.1",
        port=8001,
    )

    app = create_node_app(
        service_name="score-node",
        version="1.0.0",
        lifecycle_hook=NoOpLifecycle(),
        config=config,
        auto_register_with_gate=False,
    )

    with TestClient(app) as client:
        health = client.get("/v1/health")
        assert health.status_code == 200
        assert health.json()["ready"] is True
        assert health.json()["node_name"] == "score"

        packet = create_transport_packet(
            action="score",
            payload={"entity_id": "42"},
            tenant="tenant-a",
            destination_node="score",
            source_node="gate",
            reply_to="gate",
        )
        response = client.post("/v1/execute", json=packet.model_dump_json_dict())

        assert response.status_code == 200
        body = response.json()
        assert body["header"]["packet_type"] == "response"
        assert body["address"]["source_node"] == "score"
        assert body["address"]["destination_node"] == "gate"
        assert body["payload"]["status"] == "completed"
        assert body["payload"]["score"] == 91
        assert body["payload"]["entity_id"] == "42"


def test_node_app_returns_failure_packet_for_handler_error() -> None:
    clear_handlers()

    @register_handler("explode")
    async def handle_explode(_tenant: str, _payload: dict) -> dict:
        raise RuntimeError("boom")

    config = NodeRuntimeConfig(
        environment="test",
        node_name="worker-a",
        service_name="worker-a",
        service_version="1.0.0",
        dev_mode=True,
        require_signature=False,
        expose_internal_errors=False,
        return_transport_errors=True,
        signing_algorithm="hmac-sha256",
        signing_key=None,
        signing_private_key=None,
        signing_key_id=None,
        verifying_keys={},
        allowed_actions=("explode",),
        allowed_packet_types=("request",),
        require_idempotency_for_actions=(),
        allowed_clock_skew_seconds=30,
        max_packet_bytes=262_144,
        max_hop_depth=64,
        max_delegation_depth=8,
        max_attachments=0,
        max_attachment_size_bytes=0,
        attachment_allowed_schemes=(),
        allow_private_attachment_hosts=False,
        replay_enabled=True,
        verify_hop_signatures=False,
        gate_url="http://gate:8000",
        host="127.0.0.1",
        port=8002,
    )

    app = create_node_app(
        service_name="worker-a",
        version="1.0.0",
        lifecycle_hook=NoOpLifecycle(),
        config=config,
        auto_register_with_gate=False,
    )

    with TestClient(app) as client:
        packet = create_transport_packet(
            action="explode",
            payload={"entity_id": "42"},
            tenant="tenant-a",
            destination_node="worker-a",
            source_node="gate",
            reply_to="gate",
        )
        response = client.post("/v1/execute", json=packet.model_dump_json_dict())

        assert response.status_code == 200
        body = response.json()
        assert body["header"]["packet_type"] == "failure"
        assert body["payload"]["status"] == "failed"
        assert body["payload"]["error"] == "RuntimeError"
