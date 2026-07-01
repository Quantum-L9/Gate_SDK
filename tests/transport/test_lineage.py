from __future__ import annotations

import pytest

from constellation_node_sdk.transport.lineage import derive_lineage, validate_parent_child_lineage
from constellation_node_sdk.transport.packet import create_transport_packet


def test_derive_lineage_matches_parent_relationship() -> None:
    parent = create_transport_packet(
        action="workflow.execute",
        payload={"workflow": "full_pipeline"},
        tenant="tenant-a",
        destination_node="gate",
        source_node="client",
        reply_to="client",
    )

    lineage = derive_lineage(parent)

    assert lineage.parent_id == parent.header.packet_id
    assert lineage.root_id == parent.lineage.root_id
    assert lineage.generation == parent.lineage.generation + 1


def test_validate_parent_child_lineage_accepts_valid_child() -> None:
    parent = create_transport_packet(
        action="workflow.execute",
        payload={},
        tenant="tenant-a",
        destination_node="gate",
        source_node="client",
        reply_to="client",
    )
    child = parent.derive(
        action="enrich",
        source_node="orchestrator",
        destination_node="gate",
        reply_to="orchestrator",
        payload={"entity_id": "42"},
    )

    validate_parent_child_lineage(parent, child)


def test_validate_parent_child_lineage_rejects_invalid_child() -> None:
    parent = create_transport_packet(
        action="workflow.execute",
        payload={},
        tenant="tenant-a",
        destination_node="gate",
        source_node="client",
        reply_to="client",
    )
    child = create_transport_packet(
        action="enrich",
        payload={"entity_id": "42"},
        tenant="tenant-a",
        destination_node="gate",
        source_node="orchestrator",
        reply_to="orchestrator",
    )

    with pytest.raises((ValueError, Exception)):
        validate_parent_child_lineage(parent, child)
