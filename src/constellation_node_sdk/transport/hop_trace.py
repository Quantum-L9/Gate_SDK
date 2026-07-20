from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import Callable, Mapping
from datetime import datetime
from uuid import uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key, load_pem_public_key

from constellation_node_sdk.transport.errors import (
    TransportAuthenticationError,
    TransportIntegrityError,
    TransportValidationError,
)
from constellation_node_sdk.transport.hashing import canonical_json
from constellation_node_sdk.transport.models import TransportHop, ensure_utc, utc_now
from constellation_node_sdk.transport.packet import TransportPacket


def _coerce_bytes(value: str | bytes) -> bytes:
    if isinstance(value, bytes):
        return value
    normalized = value.strip()
    if normalized.startswith("-----BEGIN"):
        return normalized.encode("utf-8")
    try:
        return base64.b64decode(normalized, validate=True)
    except Exception:
        return normalized.encode("utf-8")


def _resolve_key(
    key_resolver: Callable[[str | None], str | bytes | None]
    | Mapping[str, str | bytes]
    | str
    | bytes
    | None,
    key_id: str | None,
) -> bytes | None:
    if key_resolver is None:
        return None
    if isinstance(key_resolver, (bytes, str)):
        return _coerce_bytes(key_resolver)
    if callable(key_resolver):
        value = key_resolver(key_id)
    else:
        if key_id is None:
            return None
        value = key_resolver.get(key_id)
    if value is None:
        return None
    return _coerce_bytes(value)


def _load_ed25519_private_key(raw: bytes) -> Ed25519PrivateKey:
    if raw.startswith(b"-----BEGIN"):
        loaded = load_pem_private_key(raw, password=None)
        if not isinstance(loaded, Ed25519PrivateKey):
            raise TransportAuthenticationError("PEM private key is not Ed25519")
        return loaded
    if len(raw) == 32:
        return Ed25519PrivateKey.from_private_bytes(raw)
    raise TransportAuthenticationError("unsupported ed25519 private key format")


def _load_ed25519_public_key(raw: bytes) -> Ed25519PublicKey:
    if raw.startswith(b"-----BEGIN"):
        loaded = load_pem_public_key(raw)
        if not isinstance(loaded, Ed25519PublicKey):
            raise TransportAuthenticationError("PEM public key is not Ed25519")
        return loaded
    if len(raw) == 32:
        return Ed25519PublicKey.from_public_bytes(raw)
    raise TransportAuthenticationError("unsupported ed25519 public key format")


def last_hop_hash(packet: TransportPacket) -> str | None:
    if not packet.hop_trace:
        return None
    return packet.hop_trace[-1].hop_hash


def compute_hop_hash(*, transport_hash: str, hop: TransportHop) -> str:
    payload = {
        "transport_hash": transport_hash,
        "hop_id": str(hop.hop_id),
        "packet_id": str(hop.packet_id),
        "node": hop.node,
        "action": hop.action,
        "direction": hop.direction,
        "status": hop.status,
        "timestamp": hop.timestamp,
        "attempt": hop.attempt,
        "target_node": hop.target_node,
        "duration_ms": hop.duration_ms,
        "queue_ms": hop.queue_ms,
        "network_ms": hop.network_ms,
        "error_code": hop.error_code,
        "error_message": hop.error_message,
        "previous_hop_hash": hop.previous_hop_hash,
    }
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def sign_hop(
    hop: TransportHop,
    *,
    key: bytes | str,
    key_id: str,
    algorithm: str,
) -> TransportHop:
    normalized_algorithm = algorithm.strip().lower()
    raw_key = _coerce_bytes(key)

    if normalized_algorithm == "hmac-sha256":
        signature = hmac.new(raw_key, hop.hop_hash.encode("utf-8"), hashlib.sha256).hexdigest()
    elif normalized_algorithm == "ed25519":
        private_key = _load_ed25519_private_key(raw_key)
        signature = private_key.sign(hop.hop_hash.encode("utf-8")).hex()
    else:
        raise TransportAuthenticationError(
            f"unsupported hop signature algorithm: {normalized_algorithm}"
        )

    return hop.model_copy(
        update={
            "hop_signature": signature,
            "hop_signature_algorithm": normalized_algorithm,
            "hop_signing_key_id": key_id.strip(),
        }
    )


def verify_hop_signature(
    hop: TransportHop,
    *,
    key_resolver: Callable[[str | None], str | bytes | None]
    | Mapping[str, str | bytes]
    | str
    | bytes
    | None,
) -> bool:
    if hop.hop_signature is None or hop.hop_signature_algorithm is None:
        return False

    key = _resolve_key(key_resolver, hop.hop_signing_key_id)
    if key is None:
        raise TransportAuthenticationError(
            "no verifying key available for hop signature verification"
        )

    if hop.hop_signature_algorithm == "hmac-sha256":
        expected = hmac.new(key, hop.hop_hash.encode("utf-8"), hashlib.sha256).hexdigest()
        return hmac.compare_digest(hop.hop_signature, expected)

    if hop.hop_signature_algorithm == "ed25519":
        public_key = _load_ed25519_public_key(key)
        try:
            public_key.verify(bytes.fromhex(hop.hop_signature), hop.hop_hash.encode("utf-8"))
            return True
        except (ValueError, InvalidSignature) as exc:
            raise TransportAuthenticationError("invalid ed25519 hop signature") from exc

    raise TransportAuthenticationError(
        f"unsupported hop signature algorithm: {hop.hop_signature_algorithm}"
    )


def _finalize_hop(
    *,
    packet: TransportPacket,
    node: str,
    action: str,
    direction: str,
    status: str,
    attempt: int | None = None,
    target_node: str | None = None,
    duration_ms: int | None = None,
    queue_ms: int | None = None,
    network_ms: int | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    timestamp: datetime | None = None,
    key: bytes | str | None = None,
    key_id: str | None = None,
    algorithm: str | None = None,
) -> TransportHop:
    provisional = TransportHop(
        hop_id=uuid4(),
        packet_id=packet.header.packet_id,
        node=node.strip().lower(),
        action=action.strip().lower(),
        direction=direction.strip().lower(),
        status=status.strip().lower(),
        timestamp=ensure_utc(timestamp) or utc_now(),
        attempt=attempt,
        target_node=None if target_node is None else target_node.strip().lower(),
        duration_ms=duration_ms,
        queue_ms=queue_ms,
        network_ms=network_ms,
        error_code=error_code,
        error_message=error_message,
        previous_hop_hash=last_hop_hash(packet),
        hop_hash="0" * 64,
        hop_signature=None,
        hop_signature_algorithm=None,
        hop_signing_key_id=None,
    )
    hop_hash = compute_hop_hash(transport_hash=packet.security.transport_hash, hop=provisional)
    finalized = provisional.model_copy(update={"hop_hash": hop_hash})

    if key is not None:
        if key_id is None or algorithm is None:
            raise TransportValidationError("key_id and algorithm are required when signing a hop")
        finalized = sign_hop(finalized, key=key, key_id=key_id, algorithm=algorithm)

    return TransportHop.model_validate(finalized)


def make_ingress_hop(
    *,
    packet: TransportPacket,
    node: str,
    action: str,
    status: str = "validated",
    attempt: int | None = None,
    queue_ms: int | None = None,
    key: bytes | str | None = None,
    key_id: str | None = None,
    algorithm: str | None = None,
) -> TransportHop:
    return _finalize_hop(
        packet=packet,
        node=node,
        action=action,
        direction="ingress",
        status=status,
        attempt=attempt,
        queue_ms=queue_ms,
        key=key,
        key_id=key_id,
        algorithm=algorithm,
    )


def make_dispatch_hop(
    *,
    packet: TransportPacket,
    node: str,
    action: str,
    target_node: str,
    status: str = "delegated",
    attempt: int | None = None,
    network_ms: int | None = None,
    key: bytes | str | None = None,
    key_id: str | None = None,
    algorithm: str | None = None,
) -> TransportHop:
    return _finalize_hop(
        packet=packet,
        node=node,
        action=action,
        direction="dispatch",
        status=status,
        attempt=attempt,
        target_node=target_node,
        network_ms=network_ms,
        key=key,
        key_id=key_id,
        algorithm=algorithm,
    )


def make_execution_hop(
    *,
    packet: TransportPacket,
    node: str,
    action: str,
    status: str = "processing",
    attempt: int | None = None,
    duration_ms: int | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    key: bytes | str | None = None,
    key_id: str | None = None,
    algorithm: str | None = None,
) -> TransportHop:
    return _finalize_hop(
        packet=packet,
        node=node,
        action=action,
        direction="execution",
        status=status,
        attempt=attempt,
        duration_ms=duration_ms,
        error_code=error_code,
        error_message=error_message,
        key=key,
        key_id=key_id,
        algorithm=algorithm,
    )


def make_response_hop(
    *,
    packet: TransportPacket,
    node: str,
    action: str,
    status: str = "completed",
    attempt: int | None = None,
    duration_ms: int | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    key: bytes | str | None = None,
    key_id: str | None = None,
    algorithm: str | None = None,
) -> TransportHop:
    return _finalize_hop(
        packet=packet,
        node=node,
        action=action,
        direction="response",
        status=status,
        attempt=attempt,
        duration_ms=duration_ms,
        error_code=error_code,
        error_message=error_message,
        key=key,
        key_id=key_id,
        algorithm=algorithm,
    )


def validate_hop_trace(
    packet: TransportPacket,
    *,
    verify_hop_signatures: bool = False,
    hop_key_resolver: Callable[[str | None], str | bytes | None]
    | Mapping[str, str | bytes]
    | str
    | bytes
    | None = None,
    require_monotonic_timestamps: bool = True,
) -> None:
    previous_hash: str | None = None
    previous_timestamp: datetime | None = None

    for index, hop in enumerate(packet.hop_trace):
        if hop.packet_id != packet.header.packet_id:
            raise TransportValidationError("hop packet_id does not match packet header packet_id")

        if index == 0:
            if hop.previous_hop_hash is not None:
                raise TransportIntegrityError("first hop must set previous_hop_hash to null")
        elif hop.previous_hop_hash != previous_hash:
            raise TransportIntegrityError("hop chain continuity violation detected")

        recomputed = compute_hop_hash(transport_hash=packet.security.transport_hash, hop=hop)
        if hop.hop_hash != recomputed:
            raise TransportIntegrityError("hop_hash does not match recomputed hop hash")

        if (
            require_monotonic_timestamps
            and previous_timestamp is not None
            and hop.timestamp < previous_timestamp
        ):
            raise TransportValidationError("hop timestamps must be non-decreasing")

        if verify_hop_signatures and hop.hop_signature is not None:
            if not verify_hop_signature(hop, key_resolver=hop_key_resolver):
                raise TransportAuthenticationError("invalid hop signature")

        previous_hash = hop.hop_hash
        previous_timestamp = hop.timestamp
