from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import Callable, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key

from constellation_node_sdk.transport.errors import TransportAuthenticationError
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


def _load_ed25519_public_key(raw: bytes) -> Ed25519PublicKey:
    if raw.startswith(b"-----BEGIN"):
        loaded = load_pem_public_key(raw)
        if not isinstance(loaded, Ed25519PublicKey):
            raise TransportAuthenticationError("PEM public key is not Ed25519")
        return loaded
    if len(raw) == 32:
        return Ed25519PublicKey.from_public_bytes(raw)
    raise TransportAuthenticationError("unsupported ed25519 public key format")


def verify_transport_packet_signature(
    packet: TransportPacket,
    *,
    key_resolver: Callable[[str | None], str | bytes | None]
    | Mapping[str, str | bytes]
    | str
    | bytes
    | None,
) -> bool:
    """
    Verify the packet signature against packet.security.transport_hash.

    Returns False for unsigned packets.
    Raises TransportAuthenticationError for key resolution or verification failures.
    """
    if packet.security.signature is None or packet.security.signature_algorithm is None:
        return False

    key = _resolve_key(key_resolver, packet.security.signing_key_id)
    if key is None:
        raise TransportAuthenticationError(
            "no verifying key available for transport signature verification"
        )

    if packet.security.signature_algorithm == "hmac-sha256":
        expected = hmac.new(
            key,
            packet.security.transport_hash.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(packet.security.signature, expected)

    if packet.security.signature_algorithm == "ed25519":
        public_key = _load_ed25519_public_key(key)
        try:
            public_key.verify(
                bytes.fromhex(packet.security.signature),
                packet.security.transport_hash.encode("utf-8"),
            )
            return True
        except (ValueError, InvalidSignature) as exc:
            raise TransportAuthenticationError("invalid ed25519 transport signature") from exc

    raise TransportAuthenticationError(
        f"unsupported signature algorithm: {packet.security.signature_algorithm}"
    )
