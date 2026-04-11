from __future__ import annotations

from constellation_node_sdk.orchestrator.merge import merge_identity, merge_payload, merge_results
from constellation_node_sdk.transport.packet import create_transport_packet


def test_merge_identity_replaces_accumulated_payload() -> None:
    current = create_transport_packet(
        action="workflow.execute",
        payload={"a": 1, "b": 2},
        tenant="tenant-a",
        destination_node="orchestrator",
        source_node="gate",
        reply_to="gate",
    )
    response = create_transport_packet(
        action="enrich",
        payload={"status": "completed", "data": {"industry": "fintech"}},
        tenant="tenant-a",
        destination_node="orchestrator",
        source_node="gate",
        reply_to="gate",
    )

    merged = merge_identity(current, response)
    assert merged == {"status": "completed", "data": {"industry": "fintech"}}


def test_merge_results_prefers_response_data_dict() -> None:
    current = create_transport_packet(
        action="workflow.execute",
        payload={"entity_id": "42", "name": "Example Corp"},
        tenant="tenant-a",
        destination_node="orchestrator",
        source_node="gate",
        reply_to="gate",
    )
    response = create_transport_packet(
        action="enrich",
        payload={"status": "completed", "data": {"industry": "manufacturing", "confidence": 0.91}},
        tenant="tenant-a",
        destination_node="orchestrator",
        source_node="gate",
        reply_to="gate",
    )

    merged = merge_results(current, response)
    assert merged == {
        "entity_id": "42",
        "name": "Example Corp",
        "industry": "manufacturing",
        "confidence": 0.91,
    }


def test_merge_payload_merges_full_response_payload() -> None:
    current = create_transport_packet(
        action="workflow.execute",
        payload={"entity_id": "42"},
        tenant="tenant-a",
        destination_node="orchestrator",
        source_node="gate",
        reply_to="gate",
    )
    response = create_transport_packet(
        action="score",
        payload={"status": "completed", "score": 88, "explanation": "high fit"},
        tenant="tenant-a",
        destination_node="orchestrator",
        source_node="gate",
        reply_to="gate",
    )

    merged = merge_payload(current, response)
    assert merged == {
        "entity_id": "42",
        "status": "completed",
        "score": 88,
        "explanation": "high fit",
    }
