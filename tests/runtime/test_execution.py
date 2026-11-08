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
    # derive() resets hop_trace; only the response hop binds to the child packet_id
    assert len(response.hop_trace) == 1
    assert response.hop_trace[0].direction == "response"
    assert response.hop_trace[0].packet_id == response.header.packet_id


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


class _StructuredProblemError(Exception):
    """Mirrors a handler exception that carries an explicit application problem."""

    def __init__(self, problem: object) -> None:
        super().__init__("internal detail that must not leak")
        self.problem = problem


def _failure_request_packet():
    return create_transport_packet(
        action="score",
        payload={"entity_id": "42"},
        tenant="tenant-a",
        destination_node="score",
        source_node="gate",
        reply_to="gate",
    )


@pytest.mark.parametrize("expose_internal_errors", [False, True])
def test_create_error_transport_packet_preserves_structured_problem_code(
    expose_internal_errors: bool,
) -> None:
    error = _StructuredProblemError(
        {"code": "INVALID_REQUEST", "message": "entity_id must be an integer"}
    )

    failure = create_error_transport_packet(
        _failure_request_packet(),
        error,
        node_name="score",
        expose_internal_errors=expose_internal_errors,
    )

    assert failure.header.packet_type == "failure"
    assert failure.payload["status"] == "failed"
    assert failure.payload["error"] == "INVALID_REQUEST"
    assert failure.payload["message"] == "entity_id must be an integer"
    assert len(failure.hop_trace) == 1
    assert failure.hop_trace[0].status == "failed"
    assert failure.hop_trace[0].error_code == "INVALID_REQUEST"


def test_create_error_transport_packet_keeps_unstructured_validation_error_generic() -> None:
    from pydantic import BaseModel, ValidationError

    class _Payload(BaseModel):
        entity_id: int

    with pytest.raises(ValidationError) as caught:
        _Payload.model_validate({"entity_id": "not-an-int"})

    failure = create_error_transport_packet(
        _failure_request_packet(),
        caught.value,
        node_name="score",
        expose_internal_errors=False,
    )

    assert failure.payload["error"] == "ValidationError"
    assert failure.payload["message"] == "ValidationError"
    assert failure.hop_trace[0].error_code == "ValidationError"
    assert failure.hop_trace[0].error_message == "ValidationError"


@pytest.mark.parametrize(
    "problem",
    [
        None,
        "INVALID_REQUEST",
        {"code": "", "message": "empty code"},
        {"code": 400, "message": "non-string code"},
        {"code": "INVALID_REQUEST"},
        {"code": "INVALID_REQUEST", "message": None},
    ],
)
def test_create_error_transport_packet_ignores_malformed_problem(problem: object) -> None:
    failure = create_error_transport_packet(
        _failure_request_packet(),
        _StructuredProblemError(problem),
        node_name="score",
        expose_internal_errors=False,
    )

    assert failure.payload["error"] == "_StructuredProblemError"
    assert failure.payload["message"] == "_StructuredProblemError"
    assert failure.hop_trace[0].error_code == "_StructuredProblemError"


def test_create_error_transport_packet_signs_structured_problem_failure() -> None:
    from constellation_node_sdk.security.validation import validate_transport_packet
    from constellation_node_sdk.security.verification import verify_transport_packet_signature

    failure = create_error_transport_packet(
        _failure_request_packet(),
        _StructuredProblemError({"code": "INVALID_REQUEST", "message": "bad request"}),
        node_name="score",
        signing_key="super-secret",
        signing_key_id="hmac-key-1",
        signing_algorithm="hmac-sha256",
    )

    assert failure.payload["error"] == "INVALID_REQUEST"
    assert failure.security.signature_algorithm == "hmac-sha256"
    assert (
        verify_transport_packet_signature(
            failure,
            key_resolver={"hmac-key-1": "super-secret"},
        )
        is True
    )
    validate_transport_packet(
        failure,
        key_resolver={"hmac-key-1": "super-secret"},
        require_signature=True,
        replay_enabled=False,
    )
