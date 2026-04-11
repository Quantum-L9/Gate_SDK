from __future__ import annotations

import pytest

from constellation_node_sdk.orchestrator.retry import RetryPolicy
from constellation_node_sdk.orchestrator.step_executor import StepExecutionError, StepExecutor
from constellation_node_sdk.transport.packet import TransportPacket, create_transport_packet


class FakeGateClient:
    def __init__(self, responses: list[TransportPacket | Exception]) -> None:
        self._responses = list(responses)
        self.calls: list[TransportPacket] = []

    async def send_to_gate(self, packet: TransportPacket) -> TransportPacket:
        self.calls.append(packet)
        next_item = self._responses.pop(0)
        if isinstance(next_item, Exception):
            raise next_item
        return next_item


@pytest.mark.asyncio
async def test_step_executor_builds_gate_bound_step_packet() -> None:
    parent = create_transport_packet(
        action="workflow.execute",
        payload={"entity_id": "42"},
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
    gate_client = FakeGateClient([response])
    executor = StepExecutor(gate_client=gate_client, source_node="orchestrator")

    result = await executor.execute_step(
        parent=parent,
        action="enrich",
        payload={"entity_id": "42"},
    )

    assert result.payload["status"] == "completed"
    assert len(gate_client.calls) == 1
    step_packet = gate_client.calls[0]
    assert step_packet.address.source_node == "orchestrator"
    assert step_packet.address.destination_node == "gate"
    assert step_packet.address.reply_to == "orchestrator"
    assert step_packet.provenance.origin_kind == "node"
    assert step_packet.provenance.requested_action == "enrich"


@pytest.mark.asyncio
async def test_step_executor_retries_then_succeeds() -> None:
    parent = create_transport_packet(
        action="workflow.execute",
        payload={"entity_id": "42"},
        tenant="tenant-a",
        destination_node="orchestrator",
        source_node="gate",
        reply_to="gate",
    )
    response = create_transport_packet(
        action="score",
        payload={"status": "completed", "score": 91},
        tenant="tenant-a",
        destination_node="orchestrator",
        source_node="gate",
        reply_to="gate",
    )
    gate_client = FakeGateClient([TimeoutError("temporary"), response])
    executor = StepExecutor(gate_client=gate_client, source_node="orchestrator")

    result = await executor.execute_step(
        parent=parent,
        action="score",
        payload={"entity_id": "42"},
        retry_policy=RetryPolicy(max_attempts=2, initial_delay_seconds=0.0, max_delay_seconds=0.0),
    )

    assert result.payload["score"] == 91
    assert len(gate_client.calls) == 2


@pytest.mark.asyncio
async def test_step_executor_raises_after_retry_exhaustion() -> None:
    parent = create_transport_packet(
        action="workflow.execute",
        payload={"entity_id": "42"},
        tenant="tenant-a",
        destination_node="orchestrator",
        source_node="gate",
        reply_to="gate",
    )
    gate_client = FakeGateClient([TimeoutError("temporary"), TimeoutError("temporary")])
    executor = StepExecutor(gate_client=gate_client, source_node="orchestrator")

    with pytest.raises(StepExecutionError):
        await executor.execute_step(
            parent=parent,
            action="score",
            payload={"entity_id": "42"},
            retry_policy=RetryPolicy(max_attempts=2, initial_delay_seconds=0.0, max_delay_seconds=0.0),
        )


@pytest.mark.asyncio
async def test_step_executor_raises_on_failure_packet() -> None:
    parent = create_transport_packet(
        action="workflow.execute",
        payload={"entity_id": "42"},
        tenant="tenant-a",
        destination_node="orchestrator",
        source_node="gate",
        reply_to="gate",
    )
    failure_packet = create_transport_packet(
        action="score",
        payload={"status": "failed", "error": "boom"},
        tenant="tenant-a",
        destination_node="orchestrator",
        source_node="gate",
        reply_to="gate",
    ).derive(
        packet_type="failure",
        source_node="gate",
        destination_node="orchestrator",
        reply_to="gate",
        payload={"status": "failed", "error": "boom"},
    )

    gate_client = FakeGateClient([failure_packet])
    executor = StepExecutor(gate_client=gate_client, source_node="orchestrator")

    with pytest.raises(StepExecutionError):
        await executor.execute_step(
            parent=parent,
            action="score",
            payload={"entity_id": "42"},
            retry_policy=RetryPolicy(max_attempts=1, initial_delay_seconds=0.0, max_delay_seconds=0.0),
        )
