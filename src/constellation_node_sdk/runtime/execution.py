from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable

from constellation_node_sdk.security.signing import sign_transport_packet
from constellation_node_sdk.security.validation import validate_transport_packet
from constellation_node_sdk.transport.hop_trace import make_execution_hop, make_response_hop
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
    verifying_keys: dict[str, str] | None,
) -> Callable[[str | None], str | bytes | None] | dict[str, str] | None:
    if verifying_keys:
        return verifying_keys
    if signing_key is None:
        return None
    return lambda _key_id: signing_key


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
        verifying_keys=verifying_keys,
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

    response_packet = response_packet.with_hop(
        make_response_hop(
            packet=response_packet,
            node=normalized_node_name,
            action=response_packet.header.action,
            status=payload_status,
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
    client_message = str(error) if expose_internal_errors else error.__class__.__name__

    response_packet = packet.derive(
        packet_type="failure",
        source_node=normalized_node_name,
        destination_node=packet.address.reply_to,
        reply_to=normalized_node_name,
        payload={
            "status": "failed",
            "error": error.__class__.__name__,
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
            error_code=error.__class__.__name__,
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
