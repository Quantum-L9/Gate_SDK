from __future__ import annotations

import pytest

from constellation_node_sdk.gate.policy import validate_outbound_gate_packet
from constellation_node_sdk.transport.packet import create_transport_packet
from constellation_node_sdk.transport.provenance import RoutingProvenance


def test_gate_only_egress_rejects_peer_target() -> None:
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
        validate_outbound_gate_packet(
            packet,
            local_node="orchestrator",
            gate_node_name="gate",
        )


def test_gate_only_egress_allows_gate_target() -> None:
    packet = create_transport_packet(
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

    validate_outbound_gate_packet(
        packet,
        local_node="orchestrator",
        gate_node_name="gate",
    )
