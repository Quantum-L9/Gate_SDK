from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable, Mapping
from typing import Final, cast

from constellation_node_sdk.security.signing import sign_transport_packet
from constellation_node_sdk.security.validation import validate_transport_packet
from constellation_node_sdk.transport.hop_trace import make_execution_hop, make_response_hop
from constellation_node_sdk.transport.key_binding import KeyMaterial, verifying_key_resolver
from constellation_node_sdk.transport.packet import TransportPacket

from .handlers import get_handler


def _resolve_signing_material(
    *,
    signing_key: bytes | str | None,
    signing_private_key: str | None,
    signing_algorithm: str | None,
) -> bytes | str | None:
    if signing_algorithm is None:
        return signing_key if signing_key is not None else signing_private_key
    normalized = signing_algorithm.strip().lower()
    if normalized == "hmac-sha256":
        return signing_key
    if normalized == "ed25519":
        return signing_private_key
    raise ValueError(f"unsupported signing algorithm: {normalized}")


def _build_key_resolver(
    *,
    signing_key: bytes | str | None,
    signing_private_key: str | None,
    signing_key_id: str | None,
    signing_algorithm: str | None,
    verifying_keys: dict[str, str] | None,
    verifying_key_algorithms: dict[str, str] | None,
    require_signature: bool,
) -> Callable[[str | None], KeyMaterial | None] | None:
    """
    Bind every verifying key id to exactly one signature algorithm (D-1).

    Peer keys are bound by their declared algorithm or, where unambiguous, by
    their material; the node's own signing algorithm binds only its own key id.
    With no verifying keys, an HMAC node keeps its shared-secret mode (every key
    id verifies with its own secret, under its own algorithm).
    """
    if not verifying_keys and signing_key is None:
        return None
    own_key = _resolve_signing_material(
        signing_key=signing_key,
        signing_private_key=signing_private_key,
        signing_algorithm=signing_algorithm,
    )
    return verifying_key_resolver(
        verifying_keys=verifying_keys or {},
        declared_algorithms=verifying_key_algorithms,
        own_key_id=signing_key_id,
        own_key=own_key,
        own_algorithm=signing_algorithm,
        own_key_for_unknown_ids=signing_key is not None,
        require_binding=require_signature,
    )


async def _invoke_handler(handler: Callable[..., object], packet: TransportPacket) -> object:
    parameters = list(inspect.signature(handler).parameters.values())

    if len(parameters) == 1:
        candidate = handler(packet)
    elif len(parameters) == 2:
        candidate = handler(packet.tenant.org_id, packet.payload)
    elif len(parameters) == 0:
        candidate = handler()
    else:
        candidate = handler(packet.tenant.org_id, packet.payload, packet)

    if inspect.isawaitable(candidate):
        return await candidate
    return candidate


def _extract_payload(result: object) -> dict[str, object]:
    if isinstance(result, dict):
        return result
    raise TypeError(f"handler must return dict or TransportPacket, got {type(result)!r}")


async def execute_transport_packet(
    packet: TransportPacket,
    *,
    node_name: str,
    signing_key: bytes | str | None = None,
    signing_private_key: str | None = None,
    signing_key_id: str | None = None,
    signing_algorithm: str | None = None,
    verifying_keys: dict[str, str] | None = None,
    verifying_key_algorithms: dict[str, str] | None = None,
    require_signature: bool = False,
    allowed_actions: tuple[str, ...] | None = None,
    allowed_packet_types: tuple[str, ...] | None = None,
    required_idempotency_actions: tuple[str, ...] | None = None,
    replay_enabled: bool = True,
    dev_mode: bool = False,
    verify_hop_signatures: bool = False,
    allowed_clock_skew_seconds: int = 30,
    max_packet_bytes: int = 262_144,
    max_hop_depth: int = 64,
    max_delegation_depth: int = 8,
    max_attachments: int = 32,
    max_attachment_size_bytes: int = 10_485_760,
    allowed_attachment_schemes: tuple[str, ...] = (),
    allow_private_attachment_hosts: bool = False,
) -> TransportPacket:
    normalized_node_name = node_name.strip().lower()
    key_resolver = _build_key_resolver(
        signing_key=signing_key,
        signing_private_key=signing_private_key,
        signing_key_id=signing_key_id,
        signing_algorithm=signing_algorithm,
        verifying_keys=verifying_keys,
        verifying_key_algorithms=verifying_key_algorithms,
        require_signature=require_signature,
    )

    validate_transport_packet(
        packet,
        key_resolver=key_resolver,
        require_signature=require_signature,
        max_packet_bytes=max_packet_bytes,
        max_hop_depth=max_hop_depth,
        max_delegation_depth=max_delegation_depth,
        max_attachments=max_attachments,
        max_attachment_size_bytes=max_attachment_size_bytes,
        allowed_attachment_schemes=allowed_attachment_schemes,
        allow_private_attachment_hosts=allow_private_attachment_hosts,
        allowed_clock_skew_seconds=allowed_clock_skew_seconds,
        local_node=normalized_node_name,
        allowed_actions=allowed_actions,
        allowed_packet_types=allowed_packet_types,
        required_idempotency_actions=required_idempotency_actions,
        replay_enabled=replay_enabled,
        dev_mode=dev_mode,
        verify_hop_signatures=verify_hop_signatures,
        hop_key_resolver=key_resolver,
    )

    handler = get_handler(packet.header.action)
    if handler is None:
        raise ValueError(f"no handler registered for action: {packet.header.action}")

    processing_packet = packet.with_hop(
        make_execution_hop(
            packet=packet,
            node=normalized_node_name,
            action=packet.header.action,
            status="processing",
        )
    )

    try:
        result = await asyncio.wait_for(
            _invoke_handler(handler, processing_packet),
            timeout=processing_packet.header.timeout_ms / 1000,
        )
    except TimeoutError as exc:
        raise TimeoutError(
            f"handler timeout after {processing_packet.header.timeout_ms}ms"
        ) from exc

    if isinstance(result, TransportPacket):
        response_packet = result
    else:
        payload = _extract_payload(result)
        response_packet = processing_packet.derive(
            packet_type="response",
            source_node=normalized_node_name,
            destination_node=processing_packet.address.reply_to,
            reply_to=normalized_node_name,
            payload=payload,
        )

    payload_status = str(response_packet.payload.get("status", "completed")).strip().lower()
    if payload_status not in {
        "received",
        "validated",
        "processing",
        "delegated",
        "completed",
        "failed",
    }:
        payload_status = "completed"

    hop_error_code: str | None = None
    hop_error_message: str | None = None
    if payload_status == "failed":
        hop_error_code, hop_error_message = _declared_result_failure(response_packet.payload)

    response_packet = response_packet.with_hop(
        make_response_hop(
            packet=response_packet,
            node=normalized_node_name,
            action=response_packet.header.action,
            status=payload_status,
            error_code=hop_error_code,
            error_message=hop_error_message,
        )
    )

    resolved_signing_key = _resolve_signing_material(
        signing_key=signing_key,
        signing_private_key=signing_private_key,
        signing_algorithm=signing_algorithm,
    )
    if resolved_signing_key is not None:
        if signing_key_id is None or signing_algorithm is None:
            raise ValueError(
                "signing_key_id and signing_algorithm are required when signing responses"
            )
        response_packet = sign_transport_packet(
            response_packet,
            key=resolved_signing_key,
            key_id=signing_key_id,
            algorithm=signing_algorithm,
        )

    return response_packet


HANDLER_REPORTED_FAILURE: Final = "handler_reported_failure"
_MAX_HOP_ERROR_CODE_CHARS: Final = 128


def _declared_result_failure(payload: Mapping[str, object]) -> tuple[str, str | None]:
    """Return the hop ``(error_code, error_message)`` for a handler's ``status: failed`` result.

    A failed hop must carry an error code or message. The handler's result is a typed
    response, not a transport failure, so the payload is carried through untouched; only
    the hop records the failure. The code is taken from the result's declared ``code``
    (or the SDK failure-payload ``error``) when it is a non-empty string no longer than
    ``_MAX_HOP_ERROR_CODE_CHARS``; otherwise the hop carries the fixed
    ``handler_reported_failure`` marker.

    The domain ``message`` is never copied into the hop. It already travels in the
    payload, and duplicating an arbitrary-length string into ``hop_trace`` could push a
    response that fits the packet size ceiling over it and turn a typed result back into
    a transport failure. Hop metadata stays bounded.
    """
    code = payload.get("code")
    if not isinstance(code, str) or not code.strip():
        code = payload.get("error")
    if isinstance(code, str) and code.strip() and len(code) <= _MAX_HOP_ERROR_CODE_CHARS:
        return code, None
    return HANDLER_REPORTED_FAILURE, None


def _structured_problem(error: Exception) -> tuple[str, str] | None:
    """Return ``(code, message)`` when *error* carries an explicit application problem.

    The contract is an ``error.problem`` mapping whose ``code`` is a non-empty string and
    whose ``message`` is a string the handler has declared safe for the caller. Anything
    else returns ``None`` so the generic class-name fallback applies; no code is inferred.
    """
    problem: object = getattr(error, "problem", None)
    if not isinstance(problem, Mapping):
        return None
    fields = cast(Mapping[object, object], problem)
    code = fields.get("code")
    message = fields.get("message")
    if not isinstance(code, str) or not code or not isinstance(message, str):
        return None
    return code, message


def create_error_transport_packet(
    packet: TransportPacket,
    error: Exception,
    *,
    node_name: str,
    signing_key: bytes | str | None = None,
    signing_private_key: str | None = None,
    signing_key_id: str | None = None,
    signing_algorithm: str | None = None,
    expose_internal_errors: bool = False,
) -> TransportPacket:
    normalized_node_name = node_name.strip().lower()
    problem = _structured_problem(error)
    if problem is not None:
        error_code, client_message = problem
    else:
        error_code = error.__class__.__name__
        client_message = str(error) if expose_internal_errors else error_code

    response_packet = packet.derive(
        packet_type="failure",
        source_node=normalized_node_name,
        destination_node=packet.address.reply_to,
        reply_to=normalized_node_name,
        payload={
            "status": "failed",
            "error": error_code,
            "message": client_message,
            "packet_id": str(packet.header.packet_id),
        },
    )

    response_packet = response_packet.with_hop(
        make_response_hop(
            packet=response_packet,
            node=normalized_node_name,
            action=response_packet.header.action,
            status="failed",
            error_code=error_code,
            error_message=str(error) if expose_internal_errors else client_message,
        )
    )

    resolved_signing_key = _resolve_signing_material(
        signing_key=signing_key,
        signing_private_key=signing_private_key,
        signing_algorithm=signing_algorithm,
    )
    if resolved_signing_key is not None:
        if signing_key_id is None or signing_algorithm is None:
            raise ValueError(
                "signing_key_id and signing_algorithm are required when signing error responses"
            )
        response_packet = sign_transport_packet(
            response_packet,
            key=resolved_signing_key,
            key_id=signing_key_id,
            algorithm=signing_algorithm,
        )

    return response_packet
