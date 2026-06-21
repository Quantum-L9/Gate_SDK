from __future__ import annotations

import json
import os
from functools import lru_cache

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_ALLOWED_ENVIRONMENTS = {"local", "dev", "test", "staging", "prod"}
_ALLOWED_SIGNING_ALGORITHMS = {"hmac-sha256", "ed25519"}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_tuple(name: str) -> tuple[str, ...]:
    raw = os.getenv(name, "")
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _env_json_map(name: str) -> dict[str, str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError(f"{name} must be a JSON object")
    result: dict[str, str] = {}
    for key, value in parsed.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError(f"{name} must map strings to strings")
        result[key] = value
    return result


class NodeRuntimeConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    environment: str
    node_name: str
    service_name: str
    service_version: str

    dev_mode: bool = False
    require_signature: bool = False
    expose_internal_errors: bool = False
    return_transport_errors: bool = True

    signing_algorithm: str = "hmac-sha256"
    signing_key: str | None = None
    signing_private_key: str | None = None
    signing_key_id: str | None = None
    verifying_keys: dict[str, str] = Field(default_factory=dict)

    allowed_actions: tuple[str, ...] = ()
    allowed_packet_types: tuple[str, ...] = ("request", "command", "delegation", "replay_request")
    require_idempotency_for_actions: tuple[str, ...] = ()

    allowed_clock_skew_seconds: int = Field(default=30, ge=0)
    max_packet_bytes: int = Field(default=262_144, ge=1024)
    max_hop_depth: int = Field(default=64, ge=1)
    max_delegation_depth: int = Field(default=8, ge=1)
    max_attachments: int = Field(default=32, ge=0)
    max_attachment_size_bytes: int = Field(default=10_485_760, ge=0)
    attachment_allowed_schemes: tuple[str, ...] = ()
    allow_private_attachment_hosts: bool = False

    replay_enabled: bool = True
    verify_hop_signatures: bool = False

    gate_node_name: str = "gate"
    enforce_gate_only_ingress: bool = True
    enable_relay_route: bool = True
    require_gate_mediation_provenance: bool = True

    execute_require_signature: bool | None = None
    execute_verify_hop_signatures: bool | None = None
    execute_allowed_actions: tuple[str, ...] = ()
    execute_allowed_packet_types: tuple[str, ...] = ()

    relay_require_signature: bool | None = None
    relay_verify_hop_signatures: bool | None = None
    relay_allowed_actions: tuple[str, ...] = ()
    relay_allowed_packet_types: tuple[str, ...] = (
        "request", "command", "delegation", "replay_request",
    )

    gate_url: str | None = None
    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1, le=65535)

    @field_validator(
        "environment",
        "node_name",
        "gate_node_name",
        "service_name",
        "service_version",
        "signing_algorithm",
        "host",
    )
    @classmethod
    def validate_required_strings(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("field must not be empty")
        return normalized

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in _ALLOWED_ENVIRONMENTS:
            raise ValueError(f"environment must be one of {sorted(_ALLOWED_ENVIRONMENTS)}")
        return normalized

    @field_validator("signing_algorithm")
    @classmethod
    def validate_signing_algorithm(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in _ALLOWED_SIGNING_ALGORITHMS:
            raise ValueError(
                f"signing_algorithm must be one of {sorted(_ALLOWED_SIGNING_ALGORITHMS)}"
            )
        return normalized

    @field_validator(
        "allowed_actions",
        "allowed_packet_types",
        "require_idempotency_for_actions",
        "attachment_allowed_schemes",
        "execute_allowed_actions",
        "execute_allowed_packet_types",
        "relay_allowed_actions",
        "relay_allowed_packet_types",
    )
    @classmethod
    def validate_string_tuples(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip().lower() for item in value if item.strip())
        if len(set(normalized)) != len(normalized):
            raise ValueError("tuple entries must not contain duplicates")
        return normalized

    @field_validator("signing_key_id")
    @classmethod
    def validate_optional_key_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("signing_key_id must not be blank")
        return normalized

    @field_validator("verifying_keys")
    @classmethod
    def validate_verifying_keys(cls, value: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for key_id, key_value in value.items():
            normalized_key = key_id.strip()
            normalized_value = key_value.strip()
            if not normalized_key or not normalized_value:
                raise ValueError("verifying_keys must not contain blank keys or values")
            normalized[normalized_key] = normalized_value
        return normalized

    @field_validator("gate_url")
    @classmethod
    def validate_gate_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().rstrip("/")
        if not normalized:
            raise ValueError("gate_url must not be blank")
        if not (normalized.startswith("http://") or normalized.startswith("https://")):
            raise ValueError("gate_url must start with http:// or https://")
        return normalized

    @model_validator(mode="after")
    def validate_security_profile(self) -> NodeRuntimeConfig:
        if self.environment in {"staging", "prod"} and self.dev_mode:
            raise ValueError("dev_mode cannot be enabled in staging or prod")

        if self.require_signature:
            if self.signing_algorithm == "hmac-sha256" and not self.signing_key:
                raise ValueError("hmac-sha256 requires signing_key when require_signature=true")
            if self.signing_algorithm == "ed25519" and not self.signing_private_key:
                raise ValueError("ed25519 requires signing_private_key when require_signature=true")

        if self.signing_algorithm == "ed25519" and self.signing_key is not None:
            raise ValueError("signing_key must not be used with ed25519")

        invalid_idempotency = set(self.require_idempotency_for_actions) - set(self.allowed_actions)
        if invalid_idempotency:
            invalid = ", ".join(sorted(invalid_idempotency))
            raise ValueError(f"require_idempotency_for_actions contains unknown actions: {invalid}")

        if self.max_attachment_size_bytes > self.max_packet_bytes:
            raise ValueError("max_attachment_size_bytes must not exceed max_packet_bytes")

        if self.max_attachments > 0 and not self.attachment_allowed_schemes:
            raise ValueError(
                "attachment_allowed_schemes must be configured when attachments are enabled"
            )

        execute_actions = (
            set(self.execute_allowed_actions)
            if self.execute_allowed_actions
            else set(self.allowed_actions)
        )
        invalid_execute_idempotency = (
            set(self.require_idempotency_for_actions) - execute_actions
        )
        if invalid_execute_idempotency:
            invalid = ", ".join(sorted(invalid_execute_idempotency))
            raise ValueError(
                f"require_idempotency_for_actions contains unknown execute actions: {invalid}"
            )

        return self

    def resolve_verifying_key(self, key_id: str | None) -> str | bytes | None:
        if key_id is None:
            return None
        if key_id in self.verifying_keys:
            return self.verifying_keys[key_id]
        if self.signing_key_id is not None and key_id == self.signing_key_id:
            if self.signing_algorithm == "hmac-sha256":
                return self.signing_key
        return None


@lru_cache
def get_runtime_config() -> NodeRuntimeConfig:
    environment = os.getenv("L9_ENVIRONMENT", "local").strip().lower() or "local"
    dev_mode = _env_bool("L9_DEV_MODE", False)
    return NodeRuntimeConfig(
        environment=environment,
        node_name=os.getenv("L9_NODE_NAME", "unknown-node"),
        service_name=os.getenv("L9_SERVICE_NAME", os.getenv("L9_NODE_NAME", "unknown-node")),
        service_version=os.getenv("L9_SERVICE_VERSION", "1.0.0"),
        dev_mode=dev_mode,
        require_signature=_env_bool("L9_REQUIRE_SIGNATURE", False),
        expose_internal_errors=_env_bool("L9_EXPOSE_INTERNAL_ERRORS", False),
        return_transport_errors=_env_bool("L9_RETURN_TRANSPORT_ERRORS", True),
        signing_algorithm=os.getenv("L9_SIGNING_ALGORITHM", "hmac-sha256"),
        signing_key=os.getenv("L9_SIGNING_KEY") or os.getenv("L9_SIGNING_SECRET"),
        signing_private_key=os.getenv("L9_SIGNING_PRIVATE_KEY"),
        signing_key_id=os.getenv("L9_SIGNING_KEY_ID"),
        verifying_keys=_env_json_map("L9_VERIFYING_KEYS_JSON"),
        allowed_actions=_env_tuple("L9_ALLOWED_ACTIONS"),
        allowed_packet_types=(
            _env_tuple("L9_ALLOWED_PACKET_TYPES")
            or ("request", "command", "delegation", "replay_request")
        ),
        require_idempotency_for_actions=_env_tuple("L9_REQUIRE_IDEMPOTENCY_FOR_ACTIONS"),
        allowed_clock_skew_seconds=int(os.getenv("L9_ALLOWED_CLOCK_SKEW_SECONDS", "30")),
        max_packet_bytes=int(os.getenv("L9_MAX_PACKET_BYTES", "262144")),
        max_hop_depth=int(os.getenv("L9_MAX_HOP_DEPTH", "64")),
        max_delegation_depth=int(os.getenv("L9_MAX_DELEGATION_DEPTH", "8")),
        max_attachments=int(os.getenv("L9_MAX_ATTACHMENTS", "32")),
        max_attachment_size_bytes=int(os.getenv("L9_MAX_ATTACHMENT_SIZE_BYTES", "10485760")),
        attachment_allowed_schemes=_env_tuple("L9_ATTACHMENT_ALLOWED_SCHEMES"),
        allow_private_attachment_hosts=_env_bool("L9_ALLOW_PRIVATE_ATTACHMENT_HOSTS", False),
        replay_enabled=_env_bool("L9_REPLAY_ENABLED", True),
        verify_hop_signatures=_env_bool("L9_VERIFY_HOP_SIGNATURES", False),
        gate_node_name=os.getenv("L9_GATE_NODE_NAME", "gate"),
        enforce_gate_only_ingress=_env_bool("L9_ENFORCE_GATE_ONLY_INGRESS", True),
        enable_relay_route=_env_bool("L9_ENABLE_RELAY_ROUTE", True),
        require_gate_mediation_provenance=_env_bool("L9_REQUIRE_GATE_MEDIATION_PROVENANCE", True),
        execute_require_signature=(
            _env_bool("L9_EXECUTE_REQUIRE_SIGNATURE", False)
            if os.getenv("L9_EXECUTE_REQUIRE_SIGNATURE") is not None
            else None
        ),
        execute_verify_hop_signatures=(
            _env_bool("L9_EXECUTE_VERIFY_HOP_SIGNATURES", False)
            if os.getenv("L9_EXECUTE_VERIFY_HOP_SIGNATURES") is not None
            else None
        ),
        execute_allowed_actions=_env_tuple("L9_EXECUTE_ALLOWED_ACTIONS"),
        execute_allowed_packet_types=_env_tuple("L9_EXECUTE_ALLOWED_PACKET_TYPES"),
        relay_require_signature=(
            _env_bool("L9_RELAY_REQUIRE_SIGNATURE", False)
            if os.getenv("L9_RELAY_REQUIRE_SIGNATURE") is not None
            else None
        ),
        relay_verify_hop_signatures=(
            _env_bool("L9_RELAY_VERIFY_HOP_SIGNATURES", False)
            if os.getenv("L9_RELAY_VERIFY_HOP_SIGNATURES") is not None
            else None
        ),
        relay_allowed_actions=_env_tuple("L9_RELAY_ALLOWED_ACTIONS"),
        relay_allowed_packet_types=(
            _env_tuple("L9_RELAY_ALLOWED_PACKET_TYPES")
            or ("request", "command", "delegation", "replay_request")
        ),
        gate_url=os.getenv("GATE_URL"),
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
    )
