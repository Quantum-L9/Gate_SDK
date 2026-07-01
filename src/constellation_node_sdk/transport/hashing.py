from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


def _canonicalize(value: Any) -> Any:
    """Recursively convert supported values into stable JSON-serializable forms."""
    if isinstance(value, BaseModel):
        return _canonicalize(value.model_dump(mode="python", exclude_none=False))
    if isinstance(value, dict):
        return {
            str(k): _canonicalize(v)
            for k, v in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonicalize(v) for v in value]
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=datetime.now().astimezone().tzinfo)
        return value.astimezone().isoformat().replace("+00:00", "Z")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    return value


def canonical_json(value: Any) -> bytes:
    """Produce stable canonical JSON bytes for hashing."""
    normalized = _canonicalize(value)
    return json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def compute_payload_hash(payload: dict[str, Any]) -> str:
    """Compute the canonical SHA-256 hash of a transport payload."""
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def compute_transport_hash(packet: Any) -> str:
    """
    Compute the canonical SHA-256 hash of a transport packet.

    The transport hash intentionally excludes mutable hop trace data and
    excludes signature fields. It covers the stable transport contract:
    header, address, tenant, payload, governance, delegation_chain,
    lineage, attachments, provenance, and payload_hash.
    """
    envelope_payload = {
        "header": packet.header,
        "address": packet.address,
        "tenant": packet.tenant,
        "payload": packet.payload,
        "governance": packet.governance,
        "delegation_chain": packet.delegation_chain,
        "lineage": packet.lineage,
        "attachments": packet.attachments,
        "provenance": packet.provenance,
        "payload_hash": packet.security.payload_hash,
    }
    return hashlib.sha256(canonical_json(envelope_payload)).hexdigest()
