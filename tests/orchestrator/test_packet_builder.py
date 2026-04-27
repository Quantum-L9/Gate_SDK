from __future__ import annotations

from constellation_node_sdk.orchestrator.packet_builder import build_step_packet
from constellation_node_sdk.transport.packet import create_transport_packet


def test_build_step_packet_targets_gate_and_preserves_root_lineage() -> None:
    parent = create_transport_packet(
        action="workflow-execute",
        payload={"workflow": "full_pipeline"},
        tenant="tenant-a",
        destination_node="orchestrator",
        source_node="gate",
        reply_to="gate",
    )

    child = build_step_packet(
        parent=parent,
        action="enrich",
        payload={"entity_id": "42"},
        source_node="orchestrator",
        reply_to="orchestrator",
    )

    assert child.header.packet_type == "request"
    assert child.header.action == "enrich"
    assert child.address.source_node == "orchestrator"
    assert child.address.destination_node == "gate"
    assert child.address.reply_to == "orchestrator"

    assert child.lineage.root_id == parent.lineage.root_id
    assert child.lineage.parent_id == parent.header.packet_id
    assert child.lineage.generation == parent.lineage.generation + 1

    assert child.provenance.origin_kind == "node"
    assert child.provenance.requested_action == "enrich"
    assert child.provenance.resolved_by_gate is False
    assert child.provenance.original_source_node == "orchestrator"


def test_build_step_packet_requires_non_blank_action() -> None:
    parent = create_transport_packet(
        action="workflow-execute",
        payload={},
        tenant="tenant-a",
        destination_node="orchestrator",
        source_node="gate",
        reply_to="gate",
    )

    try:
        build_step_packet(
            parent=parent,
            action="   ",
            payload={},
            source_node="orchestrator",
            reply_to="orchestrator",
        )
    except ValueError as exc:
        assert "action must not be empty" in str(exc)
    else:
        raise AssertionError("expected ValueError")
