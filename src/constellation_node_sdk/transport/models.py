from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_ALLOWED_PACKET_TYPES = {
    "request",
    "response",
    "event",
    "command",
    "delegation",
    "failure",
    "replay_request",
    "replay_response",
    "compensation",
}
_ALLOWED_CLASSIFICATIONS = {"public", "internal", "confidential", "restricted"}
_ALLOWED_ENCRYPTION = {"plaintext", "encrypted", "envelope_only"}
_ALLOWED_SIGNATURE_ALGORITHMS = {"hmac-sha256", "ed25519"}
_ALLOWED_HOP_DIRECTIONS = {"ingress", "dispatch", "execution", "response"}
_ALLOWED_HOP_STATUSES = {"received", "validated", "processing", "delegated", "completed", "failed"}
_ACTION_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def utc_now() -> datetime:
    """Return the current UTC time as a timezone-aware datetime."""
    return datetime.now(UTC)


def ensure_utc(value: datetime | None) -> datetime | None:
    """Normalize a datetime to UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def normalize_optional_string(value: str | None) -> str | None:
    """Normalize an optional string, forbidding blank-but-present values."""
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        raise ValueError("optional string fields must not be blank")
    return normalized


def validate_sha256_hex(value: str, *, field_name: str) -> str:
    """Validate that a value is a 64-character lowercase SHA-256 hex digest."""
    normalized = value.strip().lower()
    if len(normalized) != 64 or any(ch not in "0123456789abcdef" for ch in normalized):
        raise ValueError(f"{field_name} must be a 64-char lowercase sha256 hex digest")
    return normalized


class TransportHeader(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    packet_id: UUID = Field(default_factory=uuid4)
    packet_type: str
    action: str
    priority: int = 2
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime | None = None
    timeout_ms: int = Field(default=30_000, ge=1)

    schema_version: str = "1.0"
    idempotency_key: str | None = None
    trace_id: str | None = None
    correlation_id: str | None = None
    causation_id: UUID | None = None
    retry_count: int = Field(default=0, ge=0)
    replay_mode: bool = False
    not_before: datetime | None = None

    @field_validator("packet_type")
    @classmethod
    def validate_packet_type(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in _ALLOWED_PACKET_TYPES:
            msg = f"packet_type must be one of {sorted(_ALLOWED_PACKET_TYPES)}"
            raise ValueError(msg)
        return normalized

    @field_validator("action")
    @classmethod
    def validate_action(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _ACTION_RE.match(normalized):
            raise ValueError(
                "action must be lowercase alphanumeric with dots,"
                " underscores, or dashes, max 64 chars"
            )
        return normalized

    @field_validator("priority", mode="before")
    @classmethod
    def coerce_priority(cls, value: Any) -> int:
        if isinstance(value, str):
            rendered = value.strip().upper()
            if rendered in {"P0", "P1", "P2", "P3"}:
                return int(rendered[-1])
            return int(rendered)
        return int(value)

    @field_validator("created_at", "expires_at", "not_before")
    @classmethod
    def normalize_datetimes(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value)

    @field_validator("idempotency_key", "trace_id", "correlation_id", "schema_version")
    @classmethod
    def validate_optional_strings(cls, value: str | None) -> str | None:
        return normalize_optional_string(value)

    @model_validator(mode="after")
    def validate_header(self) -> TransportHeader:
        if not 0 <= self.priority <= 3:
            raise ValueError("priority must be between 0 and 3")
        if self.expires_at is not None and self.expires_at <= self.created_at:
            raise ValueError("expires_at must be later than created_at")
        if self.not_before is not None and self.not_before < self.created_at:
            raise ValueError("not_before must not be earlier than created_at")
        return self


class TransportAddress(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_node: str
    destination_node: str
    reply_to: str

    @field_validator("source_node", "destination_node", "reply_to")
    @classmethod
    def validate_node_name(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("address fields must not be empty")
        return normalized


class TransportSecurity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    payload_hash: str
    transport_hash: str
    signature: str | None = None
    signature_algorithm: str | None = None
    signing_key_id: str | None = None
    classification: str = "internal"
    encryption_status: str = "plaintext"
    pii_fields: tuple[str, ...] = ()

    @field_validator("payload_hash", "transport_hash")
    @classmethod
    def validate_hashes(cls, value: str) -> str:
        return validate_sha256_hex(value, field_name="hash field")

    @field_validator("signature", "signing_key_id")
    @classmethod
    def validate_optional_strings(cls, value: str | None) -> str | None:
        return normalize_optional_string(value)

    @field_validator("signature_algorithm")
    @classmethod
    def validate_signature_algorithm(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if normalized not in _ALLOWED_SIGNATURE_ALGORITHMS:
            msg = f"signature_algorithm must be one of {sorted(_ALLOWED_SIGNATURE_ALGORITHMS)}"
            raise ValueError(msg)
        return normalized

    @field_validator("classification")
    @classmethod
    def validate_classification(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in _ALLOWED_CLASSIFICATIONS:
            msg = f"classification must be one of {sorted(_ALLOWED_CLASSIFICATIONS)}"
            raise ValueError(msg)
        return normalized

    @field_validator("encryption_status")
    @classmethod
    def validate_encryption_status(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in _ALLOWED_ENCRYPTION:
            msg = f"encryption_status must be one of {sorted(_ALLOWED_ENCRYPTION)}"
            raise ValueError(msg)
        return normalized

    @field_validator("pii_fields", mode="before")
    @classmethod
    def coerce_pii_fields(cls, value: Any) -> Any:
        if isinstance(value, (list, tuple)):
            return tuple(value)
        return value

    @field_validator("pii_fields")
    @classmethod
    def validate_pii_fields(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(path.strip() for path in value if path.strip())
        if len(set(normalized)) != len(normalized):
            raise ValueError("pii_fields must not contain duplicates")
        return normalized

    @model_validator(mode="after")
    def validate_signature_state(self) -> TransportSecurity:
        if self.signature is not None and self.signature_algorithm is None:
            raise ValueError("signature_algorithm is required when signature is present")
        if self.signature_algorithm is not None and self.signature is None:
            raise ValueError("signature is required when signature_algorithm is present")
        return self


class TransportGovernance(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    intent: str
    compliance_tags: tuple[str, ...] = ()
    retention_days: int = Field(default=90, ge=0)
    redaction_applied: bool = False
    audit_required: bool = False
    data_subject_id: str | None = None

    @field_validator("intent")
    @classmethod
    def validate_intent(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("intent must not be empty")
        return normalized

    @field_validator("compliance_tags", mode="before")
    @classmethod
    def coerce_compliance_tags(cls, value: Any) -> Any:
        if isinstance(value, (list, tuple)):
            return tuple(value)
        return value

    @field_validator("compliance_tags")
    @classmethod
    def validate_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(tag.strip().upper() for tag in value if tag.strip())
        if len(set(normalized)) != len(normalized):
            raise ValueError("compliance_tags must not contain duplicates")
        return normalized

    @field_validator("data_subject_id")
    @classmethod
    def validate_data_subject_id(cls, value: str | None) -> str | None:
        return normalize_optional_string(value)


class TransportLineage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    parent_id: UUID | None = None
    root_id: UUID
    generation: int = Field(default=0, ge=0)


class TransportAttachment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    attachment_id: UUID = Field(default_factory=uuid4)
    media_type: str
    uri: str
    content_hash: str
    encrypted: bool = True
    size_bytes: int = Field(ge=0)

    @field_validator("media_type", "uri")
    @classmethod
    def validate_required_strings(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("attachment fields must not be empty")
        return normalized

    @field_validator("content_hash")
    @classmethod
    def validate_content_hash(cls, value: str) -> str:
        return validate_sha256_hex(value, field_name="attachment content_hash")


class TransportHop(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    hop_id: UUID = Field(default_factory=uuid4)
    packet_id: UUID
    node: str
    action: str
    direction: str
    status: str
    timestamp: datetime = Field(default_factory=utc_now)

    attempt: int | None = Field(default=None, ge=1)
    target_node: str | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    queue_ms: int | None = Field(default=None, ge=0)
    network_ms: int | None = Field(default=None, ge=0)
    error_code: str | None = None
    error_message: str | None = None

    previous_hop_hash: str | None = None
    hop_hash: str
    hop_signature: str | None = None
    hop_signature_algorithm: str | None = None
    hop_signing_key_id: str | None = None

    @field_validator("node", "action")
    @classmethod
    def validate_required_strings(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("hop fields must not be empty")
        return normalized

    @field_validator("direction")
    @classmethod
    def validate_direction(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in _ALLOWED_HOP_DIRECTIONS:
            msg = f"direction must be one of {sorted(_ALLOWED_HOP_DIRECTIONS)}"
            raise ValueError(msg)
        return normalized

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in _ALLOWED_HOP_STATUSES:
            msg = f"status must be one of {sorted(_ALLOWED_HOP_STATUSES)}"
            raise ValueError(msg)
        return normalized

    @field_validator("timestamp")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        normalized = ensure_utc(value)
        if normalized is None:
            raise ValueError("timestamp must not be null")
        return normalized

    @field_validator(
        "target_node", "error_code", "error_message",
        "hop_signature", "hop_signing_key_id",
    )
    @classmethod
    def validate_optional_strings(cls, value: str | None) -> str | None:
        return normalize_optional_string(value)

    @field_validator("previous_hop_hash", "hop_hash")
    @classmethod
    def validate_hop_hashes(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_sha256_hex(value, field_name="hop hash")

    @field_validator("hop_signature_algorithm")
    @classmethod
    def validate_hop_signature_algorithm(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if normalized not in _ALLOWED_SIGNATURE_ALGORITHMS:
            msg = f"hop_signature_algorithm must be one of {sorted(_ALLOWED_SIGNATURE_ALGORITHMS)}"
            raise ValueError(msg)
        return normalized

    @model_validator(mode="after")
    def validate_hop(self) -> TransportHop:
        if self.direction == "dispatch" and self.target_node is None:
            raise ValueError("dispatch hops require target_node")
        if self.hop_signature is not None:
            if self.hop_signature_algorithm is None:
                raise ValueError(
                    "hop_signature_algorithm is required when hop_signature is present"
                )
            if self.hop_signing_key_id is None:
                raise ValueError("hop_signing_key_id is required when hop_signature is present")
        if self.hop_signature_algorithm is not None and self.hop_signature is None:
            raise ValueError("hop_signature is required when hop_signature_algorithm is present")
        if self.hop_signing_key_id is not None and self.hop_signature is None:
            raise ValueError("hop_signature is required when hop_signing_key_id is present")
        if self.status == "failed" and self.error_code is None and self.error_message is None:
            raise ValueError("failed hops require error_code or error_message")
        return self


class DelegationLink(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    delegator: str
    delegatee: str
    scope: tuple[str, ...]
    granted_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime | None = None
    constraints: dict[str, Any] | None = None
    proof: str | None = None

    @field_validator("delegator", "delegatee")
    @classmethod
    def validate_nodes(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("delegation node fields must not be empty")
        return normalized

    @field_validator("scope", mode="before")
    @classmethod
    def coerce_scope(cls, value: Any) -> Any:
        if isinstance(value, (list, tuple)):
            return tuple(value)
        return value

    @field_validator("scope")
    @classmethod
    def validate_scope(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip().lower() for item in value if item.strip())
        if not normalized:
            raise ValueError("scope must not be empty")
        if len(set(normalized)) != len(normalized):
            raise ValueError("scope must not contain duplicates")
        return normalized

    @field_validator("granted_at", "expires_at")
    @classmethod
    def normalize_datetimes(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value)

    @field_validator("proof")
    @classmethod
    def validate_proof(cls, value: str | None) -> str | None:
        return normalize_optional_string(value)

    @model_validator(mode="after")
    def validate_expiry(self) -> DelegationLink:
        if self.expires_at is not None and self.expires_at <= self.granted_at:
            raise ValueError("expires_at must be later than granted_at")
        return self
