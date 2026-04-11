from __future__ import annotations

import base64
import hashlib
import hmac

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from constellation_node_sdk.transport.hashing import compute_payload_hash, compute_transport_hash
from constellation_node_sdk.transport.models import TransportSecurity
from constellation_node_sdk.transport.packet import TransportPacket

from .verification import TransportAuthenticationError


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


def _load_ed25519_private_key(raw: bytes) -> Ed25519PrivateKey:
    if raw.startswith(b"-----BEGIN"):
        loaded = load_pem_private_key(raw, password=None)
        if not isinstance(loaded, Ed25519PrivateKey):
            raise TransportAuthenticationError("PEM private key is not Ed25519")
        return loaded
    if len(raw) == 32:
        return Ed25519PrivateKey.from_private_bytes(raw)
    raise TransportAuthenticationError("unsupported ed25519 private key format")


def recompute_transport_core(packet: TransportPacket) -> TransportPacket:
    """
    Recompute payload_hash and transport_hash while preserving hop_trace.

    hop_trace is intentionally excluded from transport_hash.
    Any existing packet signature is cleared and must be re-applied by the caller.
    """
    payload_hash = compute_payload_hash(packet.payload)

    provisional = packet.model_copy(
        update={
            "security": packet.security.model_copy(
                update={
                    "payload_hash": payload_hash,
                    "transport_hash": "0" * 64,
                    "signature": None,
                    "signature_algorithm": None,
                    "signing_key_id": None,
                }
            )
        }
    )

    transport_hash = compute_transport_hash(provisional)
    finalized = provisional.model_copy(
        update={
            "security": provisional.security.model_copy(
                update={
                    "transport_hash": transport_hash,
                }
            )
        }
    )

    return TransportPacket.model_validate(finalized)


def sign_transport_packet(
    packet: TransportPacket,
    *,
    key: bytes | str,
    key_id: str,
    algorithm: str,
) -> TransportPacket:
    """
    Sign a transport packet over its stable transport_hash.

    The packet's hop_trace is preserved, but is not included in the signature domain.
    """
    normalized_algorithm = algorithm.strip().lower()
    raw_key = _coerce_bytes(key)
    normalized = recompute_transport_core(packet)

    if normalized_algorithm == "hmac-sha256":
        signature = hmac.new(
            raw_key,
            normalized.security.transport_hash.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
    elif normalized_algorithm == "ed25519":
        private_key = _load_ed25519_private_key(raw_key)
        signature = private_key.sign(normalized.security.transport_hash.encode("utf-8")).hex()
    else:
        raise TransportAuthenticationError(f"unsupported signature algorithm: {normalized_algorithm}")

    signed = normalized.model_copy(
        update={
            "security": TransportSecurity(
                payload_hash=normalized.security.payload_hash,
                transport_hash=normalized.security.transport_hash,
                signature=signature,
                signature_algorithm=normalized_algorithm,
                signing_key_id=key_id.strip(),
                classification=normalized.security.classification,
                encryption_status=normalized.security.encryption_status,
                pii_fields=normalized.security.pii_fields,
            )
        }
    )
    return TransportPacket.model_validate(signed)
