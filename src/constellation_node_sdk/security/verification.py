from __future__ import annotations

import hashlib
import hmac

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key

from constellation_node_sdk.transport.errors import TransportAuthenticationError
from constellation_node_sdk.transport.key_binding import (
    KeyResolver,
    VerifyingKey,
    resolve_bound_verifying_key,
)
from constellation_node_sdk.transport.packet import TransportPacket

__all__ = ["KeyResolver", "VerifyingKey", "verify_transport_packet_signature"]


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
    key_resolver: KeyResolver,
    key_algorithm: str | None = None,
) -> bool:
    """
    Verify the packet signature against packet.security.transport_hash.

    ``key_algorithm`` binds raw key material returned by ``key_resolver`` to one
    algorithm; a resolver may instead bind per key by returning
    :class:`VerifyingKey`. A packet whose declared algorithm differs from its
    key's binding is refused before any cryptographic check.

    Returns False for unsigned packets.
    Raises TransportAuthenticationError for key resolution or verification failures.
    """
    if packet.security.signature is None or packet.security.signature_algorithm is None:
        return False

    key = resolve_bound_verifying_key(
        key_resolver,
        packet.security.signing_key_id,
        declared_algorithm=packet.security.signature_algorithm,
        key_algorithm=key_algorithm,
    )
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
