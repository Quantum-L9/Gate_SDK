from fastapi.testclient import TestClient

from constellation_node_sdk.runtime.app import create_node_app
from constellation_node_sdk.runtime.config import NodeRuntimeConfig
from constellation_node_sdk.transport.packet import create_transport_packet
from constellation_node_sdk.transport.provenance import RoutingProvenance


def test_external_direct_to_execute_is_blocked():
    app = create_node_app(
        config=NodeRuntimeConfig(
            environment="test",
            node_name="worker-a",
            service_name="worker-a",
            service_version="1.0.0",
            attachment_allowed_schemes=(),
            max_attachments=0,
            max_attachment_size_bytes=0,
        ),
        auto_register_with_gate=False,
    )
    client = TestClient(app)
    packet = create_transport_packet(
        action="ping",
        payload={"ok": True},
        tenant="acme",
        source_node="client",
        destination_node="worker-a",
        reply_to="client",
        provenance=RoutingProvenance(
            origin_kind="client",
            requested_action="ping",
            resolved_by_gate=False,
            route_kind="external_ingress",
            original_source_node=None,
        ),
    )
    response = client.post("/v1/execute", json=packet.model_dump_json_dict())
    body = response.json()
    assert body["payload"]["status"] == "failed"
    assert body["payload"]["error"] == "ValueError"
