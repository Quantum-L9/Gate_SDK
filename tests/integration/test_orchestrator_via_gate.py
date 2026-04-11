from __future__ import annotations

import pytest

from constellation_node_sdk.orchestrator.step_executor import StepExecutor
from constellation_node_sdk.transport.packet import TransportPacket, create_transport_packet


class FakeGateClient:
    def __init__(self, responses: list[TransportPacket]) -> None:
        self._responses = list(responses)
        self.calls: list[TransportPacket] = []

    async def send_to_gate(self, packet: TransportPacket) -> TransportPacket:
        self.calls.append(packet)
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_orchestrator_executes_multiple_steps_via_gate() -> None:
    parent = create_transport_packet(
        action="full_pipeline",
        payload={"entity_id": "42"},
        tenant="tenant-a",
        destination_node="orchestrator",
        source_node="gate",
        reply_to="gate",
    )

    enrich_response = create_transport_packet(
        action="enrich",
        payload={"status": "completed", "data": {"industry": "fintech"}},
        tenant="tenant-a",
        destination_node="orchestrator",
        source_node="gate",
        reply_to="gate",
    )
    score_response = create_transport_packet(
        action="score",
        payload={"status": "completed", "score": 91},
        tenant="tenant-a",
        destination_node="orchestrator",
        source_node="gate",
        reply_to="gate",
    )

    gate_client = FakeGateClient([enrich_response, score_response])
    executor = StepExecutor(gate_client=gate_client, source_node="orchestrator")

    first = await executor.execute_step(
        parent=parent,
        action="enrich",
        payload={"entity_id": "42"},
    )
    second = await executor.execute_step(
        parent=parent,
        action="score",
        payload={"entity_id": "42", **first.payload},
    )

    assert first.payload["status"] == "completed"
    assert second.payload["score"] == 91
    assert len(gate_client.calls) == 2
    assert all(call.address.destination_node == "gate" for call in gate_client.calls)
