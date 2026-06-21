import pytest

from constellation_node_sdk.runtime.inbound_policy import validate_relay_ingress_packet
from constellation_node_sdk.transport.packet import create_transport_packet
from constellation_node_sdk.transport.provenance import RoutingProvenance


def test_relay_ingress_accepts_gate_relay_packet():
    packet = create_transport_packet(
        action="fanout",
        payload={"ok": True},
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
    validate_relay_ingress_packet(
        packet,
        local_node="worker-a",
        gate_node_name="gate",
        allowed_actions=("fanout",),
        allowed_packet_types=("request",),
    )


def test_relay_ingress_rejects_wrong_route_kind():
    packet = create_transport_packet(
        action="fanout",
        payload={"ok": True},
        tenant="acme",
        source_node="gate",
        destination_node="worker-a",
        reply_to="gate",
        provenance=RoutingProvenance(
            origin_kind="node",
            requested_action="fanout",
            resolved_by_gate=True,
            route_kind="external_ingress",
            original_source_node="orchestrator-a",
        ),
    )
    with pytest.raises(ValueError, match="route_kind"):
        validate_relay_ingress_packet(
            packet, local_node="worker-a", gate_node_name="gate"
        )
