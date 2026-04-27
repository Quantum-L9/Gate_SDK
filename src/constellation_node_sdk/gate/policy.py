from __future__ import annotations

from constellation_node_sdk.transport.packet import TransportPacket


def assert_node_origin_packet(packet: TransportPacket) -> None:
    """
    Require that a packet sent through the Gate client is node-originated.

    External clients should speak to Gate directly without going through the node SDK.
    """
    source = packet.address.source_node.strip().lower()
    if source in {"", "client"}:
        raise ValueError(
            "Gate client only accepts node-originated packets with non-client source_node"
        )

    provenance_origin = packet.provenance.origin_kind.strip().lower()
    if provenance_origin != "node":
        raise ValueError("Gate client requires packet.provenance.origin_kind == 'node'")


def assert_gate_only_destination(packet: TransportPacket, *, gate_node_name: str = "gate") -> None:
    """
    Enforce the architectural rule that node-originated packets target Gate only.
    """
    normalized_gate = gate_node_name.strip().lower()
    destination = packet.address.destination_node.strip().lower()

    if destination != normalized_gate:
        raise ValueError(
            f"node-originated packets must target {normalized_gate!r}, got {destination!r}"
        )

    requested_action = packet.provenance.requested_action.strip().lower()
    if requested_action != packet.header.action:
        raise ValueError("packet.provenance.requested_action must match packet.header.action")


def assert_local_node_identity(packet: TransportPacket, *, local_node: str) -> None:
    """
    Ensure the packet source matches the local runtime node identity.
    """
    normalized_local = local_node.strip().lower()
    if packet.address.source_node != normalized_local:
        raise ValueError(
            f"packet source_node {packet.address.source_node!r} "
            f"does not match local node {normalized_local!r}"
        )

    original_source = packet.provenance.original_source_node
    if original_source is not None and original_source != normalized_local:
        raise ValueError(
            "packet.provenance.original_source_node must match "
            "local node for node-originated packets"
        )


def validate_outbound_gate_packet(
    packet: TransportPacket,
    *,
    local_node: str,
    gate_node_name: str = "gate",
) -> None:
    """
    Apply the full outbound routing policy for the Gate client.
    """
    assert_node_origin_packet(packet)
    assert_local_node_identity(packet, local_node=local_node)
    assert_gate_only_destination(packet, gate_node_name=gate_node_name)
