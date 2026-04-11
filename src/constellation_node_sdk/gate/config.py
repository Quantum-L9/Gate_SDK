from __future__ import annotations

import os

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class GateClientConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    gate_url: str
    local_node: str
    timeout_seconds: float = Field(default=30.0, gt=0.0)
    require_signature: bool = False
    signing_key: str | bytes | None = None
    signing_key_id: str | None = None
    signing_algorithm: str | None = None
    verify_response_signatures: bool = False
    verifying_keys: dict[str, str] = Field(default_factory=dict)
    verify_hop_signatures: bool = False
    allowed_gate_destination: str = "gate"

    @field_validator("gate_url")
    @classmethod
    def validate_gate_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if not normalized:
            raise ValueError("gate_url must not be empty")
        if not (normalized.startswith("http://") or normalized.startswith("https://")):
            raise ValueError("gate_url must start with http:// or https://")
        return normalized

    @field_validator("local_node", "allowed_gate_destination")
    @classmethod
    def validate_node_fields(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("node fields must not be empty")
        return normalized

    @field_validator("signing_key_id", "signing_algorithm")
    @classmethod
    def validate_optional_strings(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("optional string fields must not be blank")
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

    def resolve_verifying_key(self, key_id: str | None) -> str | bytes | None:
        if key_id is None:
            return None
        if key_id in self.verifying_keys:
            return self.verifying_keys[key_id]
        if self.signing_key_id is not None and key_id == self.signing_key_id and self.signing_key is not None:
            return self.signing_key
        return None


class GateRegistrationConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    gate_url: str
    admin_token: str | None = None
    spec_path: str = "engine/spec.yaml"
    registration_enabled: bool = True
    retries: int = Field(default=3, ge=1)
    overwrite: bool = True

    @field_validator("gate_url")
    @classmethod
    def validate_gate_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if not normalized:
            raise ValueError("gate_url must not be empty")
        if not (normalized.startswith("http://") or normalized.startswith("https://")):
            raise ValueError("gate_url must start with http:// or https://")
        return normalized

    @field_validator("admin_token", "spec_path")
    @classmethod
    def validate_optional_strings(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("optional string fields must not be blank")
        return normalized


def get_gate_client_config_from_env() -> GateClientConfig:
    gate_url = os.getenv("GATE_URL", "").strip()
    if not gate_url:
        raise ValueError("GATE_URL is required")

    local_node = os.getenv("L9_NODE_NAME", "unknown-node").strip().lower() or "unknown-node"
    signing_key = os.getenv("L9_SIGNING_KEY") or os.getenv("L9_SIGNING_SECRET")
    signing_key_id = os.getenv("L9_SIGNING_KEY_ID")
    signing_algorithm = os.getenv("L9_SIGNING_ALGORITHM")
    verify_response_signatures = _env_bool("L9_REQUIRE_SIGNATURE", False)

    return GateClientConfig(
        gate_url=gate_url,
        local_node=local_node,
        timeout_seconds=float(os.getenv("GATE_CLIENT_TIMEOUT_SECONDS", "30.0")),
        require_signature=_env_bool("L9_REQUIRE_SIGNATURE", False),
        signing_key=signing_key,
        signing_key_id=signing_key_id,
        signing_algorithm=signing_algorithm,
        verify_response_signatures=verify_response_signatures,
        verifying_keys={},
        verify_hop_signatures=_env_bool("L9_VERIFY_HOP_SIGNATURES", False),
        allowed_gate_destination=os.getenv("GATE_ALLOWED_DESTINATION", "gate"),
    )


def get_gate_registration_config_from_env() -> GateRegistrationConfig:
    gate_url = os.getenv("GATE_URL", "").strip()
    if not gate_url:
        raise ValueError("GATE_URL is required")

    return GateRegistrationConfig(
        gate_url=gate_url,
        admin_token=os.getenv("GATE_ADMIN_TOKEN") or None,
        spec_path=os.getenv("GATE_NODE_SPEC_PATH", "engine/spec.yaml"),
        registration_enabled=_env_bool("GATE_REGISTRATION_ENABLED", True),
        retries=int(os.getenv("GATE_REGISTER_RETRIES", "3")),
        overwrite=_env_bool("GATE_REGISTER_OVERWRITE", True),
    )
