from __future__ import annotations

from constellation_node_sdk.transport.packet import TransportPacket, create_transport_packet
from constellation_node_sdk.transport.provenance import RoutingProvenance


def test_create_transport_packet_builds_canonical_root_packet() -> None:
    packet = create_transport_packet(
        action="enrich",
        payload={"entity_id": "42"},
        tenant="tenant-a",
        destination_node="gate",
        source_node="client",
        reply_to="client",
    )

    assert isinstance(packet, TransportPacket)
    assert packet.header.packet_type == "request"
    assert packet.header.action == "enrich"
    assert packet.address.source_node == "client"
    assert packet.address.destination_node == "gate"
    assert packet.lineage.parent_id is None
    assert packet.lineage.root_id == packet.header.packet_id
    assert packet.lineage.generation == 0
    assert packet.provenance.origin_kind == "client"
    assert packet.security.payload_hash
    assert packet.security.transport_hash


def test_transport_packet_derive_creates_child_with_incremented_lineage() -> None:
    parent = create_transport_packet(
        action="workflow-execute",
        payload={"workflow": "full_pipeline"},
        tenant="tenant-a",
        destination_node="gate",
        source_node="client",
        reply_to="client",
    )

    child = parent.derive(
        packet_type="request",
        action="enrich",
        source_node="orchestrator",
        destination_node="gate",
        reply_to="orchestrator",
        payload={"entity_id": "42"},
        provenance=RoutingProvenance(
            origin_kind="node",
            requested_action="enrich",
            resolved_by_gate=False,
            original_source_node="orchestrator",
        ),
    )

    assert child.header.packet_id != parent.header.packet_id
    assert child.lineage.parent_id == parent.header.packet_id
    assert child.lineage.root_id == parent.lineage.root_id
    assert child.lineage.generation == parent.lineage.generation + 1
    assert child.address.source_node == "orchestrator"
    assert child.address.destination_node == "gate"
    assert child.security.transport_hash != parent.security.transport_hash


def test_transport_packet_with_hop_preserves_transport_hash() -> None:
    from constellation_node_sdk.transport.hop_trace import make_ingress_hop

    packet = create_transport_packet(
        action="enrich",
        payload={"entity_id": "42"},
        tenant="tenant-a",
        destination_node="gate",
        source_node="client",
        reply_to="client",
    )

    original_transport_hash = packet.security.transport_hash
    hopped = packet.with_hop(
        make_ingress_hop(
            packet=packet,
            node="gate",
            action="enrich",
            status="validated",
        )
    )

    assert len(hopped.hop_trace) == 1
    assert hopped.security.transport_hash == original_transport_hash
    assert hopped.security.payload_hash == packet.security.payload_hash
