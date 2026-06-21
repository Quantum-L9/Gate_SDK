import pytest

from constellation_node_sdk.runtime.inbound_policy import validate_execute_ingress_packet
from constellation_node_sdk.transport.packet import create_transport_packet
from constellation_node_sdk.transport.provenance import RoutingProvenance


def test_execute_ingress_rejects_non_gate_source():
    packet = create_transport_packet(
        action="ping",
        payload={"ok": True},
        tenant="acme",
        source_node="client",
        destination_node="worker-a",
        reply_to="gate",
        provenance=RoutingProvenance(
            origin_kind="client",
            requested_action="ping",
            resolved_by_gate=True,
            route_kind="external_ingress",
            original_source_node=None,
        ),
    )
    with pytest.raises(ValueError, match="originate from 'gate'"):
        validate_execute_ingress_packet(
            packet, local_node="worker-a", gate_node_name="gate"
        )


def test_execute_ingress_accepts_gate_source_with_external_ingress_route():
    packet = create_transport_packet(
        action="ping",
        payload={"ok": True},
        tenant="acme",
        source_node="gate",
        destination_node="worker-a",
        reply_to="gate",
        provenance=RoutingProvenance(
            origin_kind="client",
            requested_action="ping",
            resolved_by_gate=True,
            route_kind="external_ingress",
            original_source_node=None,
        ),
    )
    validate_execute_ingress_packet(
        packet, local_node="worker-a", gate_node_name="gate"
    )
