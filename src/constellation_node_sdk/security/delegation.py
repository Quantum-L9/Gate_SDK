from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import UTC, datetime
from typing import Any

from constellation_node_sdk.transport.hashing import canonical_json
from constellation_node_sdk.transport.packet import TransportPacket


def _coerce_bytes(value: str | bytes) -> bytes:
    if isinstance(value, bytes):
        return value
    normalized = value.strip()
    try:
        return base64.b64decode(normalized, validate=True)
    except Exception:
        return normalized.encode("utf-8")


def compute_delegation_proof(
    *,
    packet: TransportPacket,
    delegator: str,
    delegatee: str,
    scope: tuple[str, ...],
    granted_at: datetime,
    key: bytes | str,
    expires_at: datetime | None = None,
    constraints: dict[str, Any] | None = None,
) -> str:
    """
    Compute an HMAC proof for a delegation grant.

    Delegation proofs are bound to:
    - packet_id
    - transport_hash
    - delegator/delegatee
    - scope
    - grant time
    - optional expiry/constraints
    """
    raw_key = _coerce_bytes(key)
    normalized_scope = tuple(item.strip().lower() for item in scope if item.strip())
    message = {
        "packet_id": str(packet.header.packet_id),
        "transport_hash": packet.security.transport_hash,
        "delegator": delegator.strip().lower(),
        "delegatee": delegatee.strip().lower(),
        "scope": list(normalized_scope),
        "granted_at": granted_at.astimezone(UTC).isoformat(),
        "expires_at": expires_at.astimezone(UTC).isoformat() if expires_at else None,
        "constraints": constraints or {},
    }
    return hmac.new(raw_key, canonical_json(message), hashlib.sha256).hexdigest()
