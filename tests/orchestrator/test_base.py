from __future__ import annotations

import pytest

from constellation_node_sdk.gate.client import GateClient
from constellation_node_sdk.gate.config import GateClientConfig
from constellation_node_sdk.orchestrator.base import BaseOrchestrator
from constellation_node_sdk.orchestrator.state import OrchestratorState
from constellation_node_sdk.orchestrator.step_executor import StepExecutor
from constellation_node_sdk.transport.packet import TransportPacket, create_transport_packet


class _EchoOrchestrator(BaseOrchestrator):
    """Minimal concrete orchestrator used only to exercise BaseOrchestrator."""

    async def execute(self, packet: TransportPacket) -> TransportPacket:
        return packet


def _gate_client() -> GateClient:
    return GateClient(
        GateClientConfig(
            gate_url="http://gate:8000",
            local_node="orchestrator",
        )
    )


def test_base_orchestrator_normalizes_source_node_and_exposes_properties() -> None:
    client = _gate_client()
    orchestrator = _EchoOrchestrator(gate_client=client, source_node="  Orchestrator  ")

    assert orchestrator.source_node == "orchestrator"
    assert orchestrator.gate_client is client
    assert isinstance(orchestrator.step_executor, StepExecutor)


def test_base_orchestrator_rejects_blank_source_node() -> None:
    client = _gate_client()

    with pytest.raises(ValueError, match="source_node must not be empty"):
        _EchoOrchestrator(gate_client=client, source_node="   ")


def test_initial_state_builds_orchestrator_state_from_packet() -> None:
    client = _gate_client()
    orchestrator = _EchoOrchestrator(gate_client=client, source_node="orchestrator")

    packet = create_transport_packet(
        action="workflow.execute",
        payload={"entity_id": "42"},
        tenant="tenant-a",
        destination_node="orchestrator",
        source_node="gate",
        reply_to="gate",
    )

    state = orchestrator.initial_state(packet)

    assert isinstance(state, OrchestratorState)
    assert state.root_id == packet.lineage.root_id
    assert state.current_packet_id == packet.header.packet_id
    assert state.orchestrator_node == "orchestrator"
    assert state.accumulated_payload == {"entity_id": "42"}


@pytest.mark.asyncio
async def test_concrete_orchestrator_can_execute() -> None:
    client = _gate_client()
    orchestrator = _EchoOrchestrator(gate_client=client, source_node="orchestrator")
    packet = create_transport_packet(
        action="workflow.execute",
        payload={"entity_id": "42"},
        tenant="tenant-a",
        destination_node="orchestrator",
        source_node="gate",
        reply_to="gate",
    )

    result = await orchestrator.execute(packet)

    assert result is packet


def test_base_orchestrator_cannot_be_instantiated_directly() -> None:
    client = _gate_client()
    with pytest.raises(TypeError):
        BaseOrchestrator(gate_client=client, source_node="orchestrator")  # type: ignore[abstract]
