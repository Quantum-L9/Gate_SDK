from __future__ import annotations

import pytest

from constellation_node_sdk.runtime.execution import (
    create_error_transport_packet,
    execute_transport_packet,
)
from constellation_node_sdk.runtime.handlers import clear_handlers, register_handler
from constellation_node_sdk.transport.packet import create_transport_packet


@pytest.mark.asyncio
async def test_execute_transport_packet_runs_handler_and_returns_response() -> None:
    clear_handlers()

    @register_handler("score")
    async def handle_score(_tenant: str, payload: dict) -> dict:
        return {
            "status": "completed",
            "score": 91,
            "entity_id": payload["entity_id"],
        }

    packet = create_transport_packet(
        action="score",
        payload={"entity_id": "42"},
        tenant="tenant-a",
        destination_node="score",
        source_node="gate",
        reply_to="gate",
    )

    response = await execute_transport_packet(
        packet,
        node_name="score",
        allowed_actions=("score",),
        allowed_packet_types=("request",),
        max_attachments=0,
        max_attachment_size_bytes=0,
        allowed_attachment_schemes=(),
        dev_mode=True,
    )

    assert response.header.packet_type == "response"
    assert response.address.source_node == "score"
    assert response.address.destination_node == "gate"
    assert response.payload["status"] == "completed"
    assert response.payload["score"] == 91
    assert response.payload["entity_id"] == "42"
    assert len(response.hop_trace) == 2
    assert response.hop_trace[0].direction == "execution"
    assert response.hop_trace[1].direction == "response"


@pytest.mark.asyncio
async def test_execute_transport_packet_rejects_unregistered_action() -> None:
    clear_handlers()

    packet = create_transport_packet(
        action="unknown",
        payload={"entity_id": "42"},
        tenant="tenant-a",
        destination_node="worker-a",
        source_node="gate",
        reply_to="gate",
    )

    with pytest.raises(ValueError, match="no handler registered"):
        await execute_transport_packet(
            packet,
            node_name="worker-a",
            allowed_actions=("unknown",),
            allowed_packet_types=("request",),
            max_attachments=0,
            max_attachment_size_bytes=0,
            allowed_attachment_schemes=(),
            dev_mode=True,
        )


def test_create_error_transport_packet_builds_failure_response() -> None:
    packet = create_transport_packet(
        action="score",
        payload={"entity_id": "42"},
        tenant="tenant-a",
        destination_node="score",
        source_node="gate",
        reply_to="gate",
    )

    failure = create_error_transport_packet(
        packet,
        RuntimeError("boom"),
        node_name="score",
        expose_internal_errors=False,
    )

    assert failure.header.packet_type == "failure"
    assert failure.address.source_node == "score"
    assert failure.address.destination_node == "gate"
    assert failure.payload["status"] == "failed"
    assert failure.payload["error"] == "RuntimeError"
    assert failure.payload["message"] == "RuntimeError"
    assert len(failure.hop_trace) == 1
    assert failure.hop_trace[0].direction == "response"
    assert failure.hop_trace[0].status == "failed"
