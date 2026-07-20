from __future__ import annotations

import pytest
from pydantic import ValidationError

from constellation_node_sdk.orchestrator.state import OrchestratorState
from constellation_node_sdk.transport.packet import create_transport_packet


def _root_packet():
    return create_transport_packet(
        action="workflow.execute",
        payload={"entity_id": "42"},
        tenant="tenant-a",
        destination_node="orchestrator",
        source_node="gate",
        reply_to="gate",
    )


def test_from_packet_builds_initial_state_from_root_packet() -> None:
    packet = _root_packet()

    state = OrchestratorState.from_packet(
        packet=packet,
        orchestrator_node="Orchestrator",
        workflow_name="  entity-enrichment  ",
    )

    assert state.root_id == packet.lineage.root_id
    assert state.current_packet_id == packet.header.packet_id
    assert state.current_generation == packet.lineage.generation
    assert state.orchestrator_node == "orchestrator"
    assert state.workflow_name == "entity-enrichment"
    assert state.accumulated_payload == {"entity_id": "42"}
    assert state.step_results == ()
    assert state.completed_steps == ()
    assert state.failed_steps == ()


def test_from_packet_defaults_workflow_name_to_none() -> None:
    packet = _root_packet()

    state = OrchestratorState.from_packet(packet=packet, orchestrator_node="orchestrator")

    assert state.workflow_name is None


def test_with_step_success_appends_result_and_advances_generation() -> None:
    packet = _root_packet()
    state = OrchestratorState.from_packet(packet=packet, orchestrator_node="orchestrator")

    response = packet.derive(
        packet_type="response",
        action="enrich",
        source_node="gate",
        destination_node="orchestrator",
        reply_to="gate",
        payload={"status": "completed", "industry": "fintech"},
    )

    next_state = state.with_step_success(
        step_name="  Enrich  ",
        response_packet=response,
        merged_payload={"entity_id": "42", "industry": "fintech"},
    )

    assert next_state.current_packet_id == response.header.packet_id
    assert next_state.current_generation == response.lineage.generation
    assert next_state.accumulated_payload == {"entity_id": "42", "industry": "fintech"}
    assert next_state.completed_steps == ("enrich",)
    assert len(next_state.step_results) == 1
    snapshot = next_state.step_results[0]
    assert snapshot["step_name"] == "enrich"
    assert snapshot["packet_id"] == str(response.header.packet_id)
    assert snapshot["action"] == response.header.action
    assert snapshot["payload"] == {"status": "completed", "industry": "fintech"}

    # Original state is untouched (immutable transitions).
    assert state.completed_steps == ()
    assert state.accumulated_payload == {"entity_id": "42"}


def test_with_step_failure_appends_failed_step_without_mutating_success_state() -> None:
    packet = _root_packet()
    state = OrchestratorState.from_packet(packet=packet, orchestrator_node="orchestrator")

    failed_state = state.with_step_failure(step_name="  Score  ")

    assert failed_state.failed_steps == ("score",)
    assert failed_state.completed_steps == ()
    assert failed_state.step_results == ()


def test_orchestrator_state_is_frozen() -> None:
    packet = _root_packet()
    state = OrchestratorState.from_packet(packet=packet, orchestrator_node="orchestrator")

    with pytest.raises(ValidationError):
        state.workflow_name = "changed"  # type: ignore[misc]


def test_orchestrator_state_rejects_negative_generation() -> None:
    packet = _root_packet()

    with pytest.raises(ValidationError):
        OrchestratorState(
            root_id=packet.lineage.root_id,
            current_packet_id=packet.header.packet_id,
            current_generation=-1,
            orchestrator_node="orchestrator",
        )
