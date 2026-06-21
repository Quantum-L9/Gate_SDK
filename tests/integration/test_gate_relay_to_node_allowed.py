from fastapi.testclient import TestClient

from constellation_node_sdk.runtime.app import create_node_app
from constellation_node_sdk.runtime.config import NodeRuntimeConfig
from constellation_node_sdk.runtime.handlers import clear_handlers, register_handler
from constellation_node_sdk.transport.packet import create_transport_packet
from constellation_node_sdk.transport.provenance import RoutingProvenance


def test_gate_relay_route_allows_gate_mediated_internal_packet():
    clear_handlers()

    def fanout_handler(_org_id, payload):
        return {"status": "completed", "echo": payload["value"]}

    register_handler("fanout", fanout_handler)

    app = create_node_app(
        config=NodeRuntimeConfig(
            environment="test",
            node_name="worker-a",
            service_name="worker-a",
            service_version="1.0.0",
            attachment_allowed_schemes=(),
            max_attachments=0,
            max_attachment_size_bytes=0,
            relay_allowed_actions=("fanout",),
            relay_allowed_packet_types=("request",),
        ),
        auto_register_with_gate=False,
    )
    client = TestClient(app)
    packet = create_transport_packet(
        action="fanout",
        payload={"value": "x"},
        tenant="acme",
        source_node="gate",
        destination_node="worker-a",
        reply_to="gate",
        provenance=RoutingProvenance(
            origin_kind="node",
            requested_action="fanout",
            resolved_by_gate=True,
            route_kind="gate_relay",
            original_source_node="orchestrator-a",
        ),
    )
    response = client.post("/v1/relay", json=packet.model_dump_json_dict())
    assert response.status_code == 200
    body = response.json()
    assert body["payload"]["echo"] == "x"
