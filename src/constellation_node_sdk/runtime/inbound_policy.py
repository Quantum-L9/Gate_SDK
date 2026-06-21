from __future__ import annotations

from constellation_node_sdk.transport.packet import TransportPacket


def assert_gate_source(packet: TransportPacket, *, gate_node_name: str = "gate") -> None:
    expected = gate_node_name.strip().lower()
    actual = packet.address.source_node.strip().lower()
    if actual != expected:
        raise ValueError(f"inbound packets must originate from {expected!r}, got {actual!r}")


def assert_packet_targets_local_node(packet: TransportPacket, *, local_node: str) -> None:
    expected = local_node.strip().lower()
    actual = packet.address.destination_node.strip().lower()
    if actual != expected:
        raise ValueError(
            f"inbound packet destination_node {actual!r} does not match local node {expected!r}"
        )


def assert_gate_mediated_provenance(
    packet: TransportPacket,
    *,
    allow_route_kinds: tuple[str, ...],
    require_route_kind: bool = True,
) -> None:
    if not packet.provenance.resolved_by_gate:
        raise ValueError("inbound packet provenance.resolved_by_gate must be true")

    if packet.provenance.origin_kind not in {"client", "node", "gate"}:
        raise ValueError("invalid inbound provenance.origin_kind")

    route_kind = packet.provenance.route_kind
    if route_kind is None:
        if require_route_kind:
            raise ValueError("inbound packet provenance.route_kind is required")
        return

    normalized_route_kind = route_kind.strip().lower()
    normalized_allowed = tuple(value.strip().lower() for value in allow_route_kinds)
    if normalized_route_kind not in normalized_allowed:
        raise ValueError(
            f"inbound packet provenance.route_kind must be one of "
            f"{normalized_allowed}, got {normalized_route_kind!r}"
        )


def validate_execute_ingress_packet(
    packet: TransportPacket,
    *,
    local_node: str,
    gate_node_name: str = "gate",
    require_route_kind: bool = True,
) -> None:
    assert_gate_source(packet, gate_node_name=gate_node_name)
    assert_packet_targets_local_node(packet, local_node=local_node)
    assert_gate_mediated_provenance(
        packet,
        allow_route_kinds=("external_ingress",),
        require_route_kind=require_route_kind,
    )


def validate_relay_ingress_packet(
    packet: TransportPacket,
    *,
    local_node: str,
    gate_node_name: str = "gate",
    require_route_kind: bool = True,
    allowed_actions: tuple[str, ...] | None = None,
    allowed_packet_types: tuple[str, ...] | None = None,
) -> None:
    assert_gate_source(packet, gate_node_name=gate_node_name)
    assert_packet_targets_local_node(packet, local_node=local_node)
    assert_gate_mediated_provenance(
        packet,
        allow_route_kinds=("gate_relay",),
        require_route_kind=require_route_kind,
    )

    if allowed_actions and packet.header.action not in allowed_actions:
        raise ValueError(f"relay action {packet.header.action!r} is not allowed")

    if allowed_packet_types and packet.header.packet_type not in allowed_packet_types:
        raise ValueError(f"relay packet_type {packet.header.packet_type!r} is not allowed")
