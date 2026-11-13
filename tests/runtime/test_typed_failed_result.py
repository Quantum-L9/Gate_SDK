"""A handler's typed ``status: failed`` result is a response, not a transport failure.

Regression: the runtime built the response hop for a handler-returned payload with
``status == "failed"`` without ``error_code``/``error_message``. ``TransportHop``
rejects such a hop, so the handler's typed problem (for example l9-state's
``StateProblem``) was replaced by a generic transport failure packet.
"""

from __future__ import annotations

from typing import Any

import pytest

from constellation_node_sdk.runtime.execution import (
    create_error_transport_packet,
    execute_transport_packet,
)
from constellation_node_sdk.runtime.handlers import clear_handlers, register_handler
from constellation_node_sdk.security.signing import sign_transport_packet
from constellation_node_sdk.security.validation import validate_transport_packet
from constellation_node_sdk.security.verification import verify_transport_packet_signature
from constellation_node_sdk.transport.hashing import compute_transport_hash
from constellation_node_sdk.transport.packet import TransportPacket, create_transport_packet

_KEY = "super-secret"
_KEY_ID = "hmac-key-1"
_KEYS = {_KEY_ID: _KEY}

# Opaque, consumer-shaped problem payload (mirrors l9-state's StateProblem rendering).
_PROBLEM: dict[str, Any] = {
    "status": "failed",
    "code": "INVALID_REQUEST",
    "message": "transport identity does not match operation_id",
    "operation_id": "op.3",
    "retryable": False,
}


def _signed_request() -> TransportPacket:
    packet = create_transport_packet(
        action="state.create",
        payload={"object_id": "object.3", "operation_id": "op.3"},
        tenant="tenant-a",
        destination_node="l9-state",
        source_node="gate",
        reply_to="gate",
        idempotency_key="other",
    )
    return sign_transport_packet(packet, key=_KEY, key_id=_KEY_ID, algorithm="hmac-sha256")


async def _execute(packet: TransportPacket) -> TransportPacket:
    return await execute_transport_packet(
        packet,
        node_name="l9-state",
        signing_key=_KEY,
        signing_key_id=_KEY_ID,
        signing_algorithm="hmac-sha256",
        verifying_keys=_KEYS,
        require_signature=True,
        allowed_actions=("state.create",),
        allowed_packet_types=("request",),
        max_attachments=0,
        max_attachment_size_bytes=0,
        allowed_attachment_schemes=(),
    )


@pytest.mark.asyncio
async def test_typed_failed_handler_result_is_a_signed_response_with_payload_intact() -> None:
    clear_handlers()

    @register_handler("state.create")
    async def handle_create(_tenant: str, _payload: dict[str, Any]) -> dict[str, Any]:
        return dict(_PROBLEM)

    request = _signed_request()
    request_snapshot = request.model_dump(mode="json")

    response = await _execute(request)

    # The handler's typed problem is carried through verbatim, as a response.
    assert response.header.packet_type == "response"
    assert response.payload == _PROBLEM
    assert response.lineage.parent_id == request.header.packet_id

    # The response hop records the failure using the handler's own declared fields.
    assert len(response.hop_trace) == 1
    hop = response.hop_trace[0]
    assert hop.direction == "response"
    assert hop.status == "failed"
    assert hop.error_code == "INVALID_REQUEST"
    # The domain message stays in the payload only; hop metadata is bounded.
    assert hop.error_message is None
    assert hop.packet_id == response.header.packet_id

    # Signed, verifiable, and the hop is excluded from the transport hash.
    assert verify_transport_packet_signature(response, key_resolver=_KEYS) is True
    assert response.security.transport_hash == compute_transport_hash(response)
    validate_transport_packet(
        response, key_resolver=_KEYS, require_signature=True, replay_enabled=False
    )

    # The inbound packet was not mutated in place.
    assert request.model_dump(mode="json") == request_snapshot
    clear_handlers()


@pytest.mark.asyncio
async def test_failed_result_without_declared_code_records_a_fixed_hop_marker() -> None:
    clear_handlers()

    @register_handler("state.create")
    async def handle_create(_tenant: str, _payload: dict[str, Any]) -> dict[str, Any]:
        return {"status": "failed", "detail": {"nested": True}}

    response = await _execute(_signed_request())

    assert response.header.packet_type == "response"
    assert response.payload == {"status": "failed", "detail": {"nested": True}}
    hop = response.hop_trace[0]
    assert hop.status == "failed"
    assert hop.error_code == "handler_reported_failure"
    assert hop.error_message is None
    clear_handlers()


@pytest.mark.asyncio
async def test_large_domain_message_is_not_duplicated_into_the_hop() -> None:
    """A failed result whose payload fits the packet ceiling must still fit with its hop."""
    clear_handlers()
    big_message = "x" * 200_000

    @register_handler("state.create")
    async def handle_create(_tenant: str, _payload: dict[str, Any]) -> dict[str, Any]:
        return {"status": "failed", "code": "E" * 500, "message": big_message}

    response = await _execute(_signed_request())

    assert response.header.packet_type == "response"
    assert response.payload["message"] == big_message
    hop = response.hop_trace[0]
    assert hop.status == "failed"
    # An over-long declared code is not copied either: the fixed marker is used.
    assert hop.error_code == "handler_reported_failure"
    assert hop.error_message is None
    validate_transport_packet(
        response, key_resolver=_KEYS, require_signature=True, replay_enabled=False
    )
    clear_handlers()


@pytest.mark.asyncio
async def test_real_handler_exception_is_still_a_transport_failure() -> None:
    clear_handlers()

    class _Boom(RuntimeError):
        pass

    @register_handler("state.create")
    async def handle_create(_tenant: str, _payload: dict[str, Any]) -> dict[str, Any]:
        raise _Boom("database unreachable")

    request = _signed_request()
    request_snapshot = request.model_dump(mode="json")

    with pytest.raises(_Boom) as excinfo:
        await _execute(request)

    failure = create_error_transport_packet(
        request,
        excinfo.value,
        node_name="l9-state",
        signing_key=_KEY,
        signing_key_id=_KEY_ID,
        signing_algorithm="hmac-sha256",
    )
    assert failure.header.packet_type == "failure"
    assert failure.payload["error"] == "_Boom"
    assert "database unreachable" not in str(failure.payload)
    assert failure.hop_trace[0].status == "failed"
    assert verify_transport_packet_signature(failure, key_resolver=_KEYS) is True
    assert request.model_dump(mode="json") == request_snapshot
    clear_handlers()
