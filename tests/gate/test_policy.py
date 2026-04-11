from __future__ import annotations

import pytest

from constellation_node_sdk.gate.policy import (
    assert_gate_only_destination,
    assert_local_node_identity,
    assert_node_origin_packet,
    validate_outbound_gate_packet,
)
from constellation_node_sdk.transport.packet import create_transport_packet
from constellation_node_sdk.transport.provenance import RoutingProvenance


def _node_packet():
    return create_transport_packet(
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


def test_assert_node_origin_packet_accepts_node_packet() -> None:
    packet = _node_packet()
    assert_node_origin_packet(packet)


def test_assert_node_origin_packet_rejects_client_origin() -> None:
    packet = create_transport_packet(
        action="enrich",
        payload={"entity_id": "42"},
        tenant="tenant-a",
        destination_node="gate",
        source_node="client",
        reply_to="client",
    )

    with pytest.raises(ValueError):
        assert_node_origin_packet(packet)


def test_assert_gate_only_destination_rejects_peer_destination() -> None:
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
        assert_gate_only_destination(packet, gate_node_name="gate")


def test_validate_outbound_gate_packet_checks_full_policy() -> None:
    packet = _node_packet()
    validate_outbound_gate_packet(
        packet,
        local_node="orchestrator",
        gate_node_name="gate",
    )


def test_assert_local_node_identity_rejects_mismatch() -> None:
    packet = _node_packet()

    with pytest.raises(ValueError):
        assert_local_node_identity(packet, local_node="different-node")
