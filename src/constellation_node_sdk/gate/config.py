from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _env_optional_int(name: str) -> int | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    return int(raw)


def _env_verifying_keys(name: str) -> dict[str, str]:
    """Parse a JSON object of ``key_id -> key material`` from the environment.

    Blank/unset yields ``{}``. Anything that is not a flat string->string object
    is a configuration error and fails fast rather than silently disabling
    response verification.
    """
    raw = os.getenv(name, "").strip()
    if not raw or raw == "{}":
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{name} must be a JSON object")
    result: dict[str, str] = {}
    for key_id, key_value in parsed.items():
        if not isinstance(key_id, str) or not isinstance(key_value, str):
            raise ValueError(f"{name} keys and values must all be strings")
        result[key_id] = key_value
    return result


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class GateClientConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    gate_url: str
    local_node: str
    # Default operation budget for GateClient.execute() when the caller does not
    # supply one. It is not a second deadline: the budget is written into the
    # packet header, and the actual network deadline is derived from that header.
    timeout_seconds: float = Field(default=30.0, gt=0.0)
    # Optional hard ceiling on any operation budget, for deployments whose
    # synchronous caller cannot outlive a fixed window. None means no ceiling.
    max_timeout_ms: int | None = Field(default=None, ge=1)
    # Explicit slice of the operation budget reserved for the SDK to raise a
    # typed timeout before the caller's own deadline elapses. Default 0: the
    # network deadline equals the advertised budget, with no hidden reservation.
    transport_margin_ms: int = Field(default=0, ge=0)
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

    def admission_problems(self) -> list[str]:
        """
        Why this configuration cannot prove an identity to Gate (empty when it can).

        Gate verifies a consumer by the signing key id on its packets, so an
        unsigned or half-configured client can never be admitted. Checked before
        any network call so the mistake is reported as configuration, not as a
        Gate rejection.
        """
        problems: list[str] = []
        if self.signing_key is None:
            problems.append("no signing key (L9_SIGNING_KEY) is configured")
        if self.signing_key is not None and self.signing_key_id is None:
            problems.append("a signing key is configured without a key id (L9_SIGNING_KEY_ID)")
        if self.signing_key is not None and self.signing_algorithm is None:
            problems.append("a signing key is configured without an algorithm")
        if not self.verifying_keys:
            # The receipt is only worth trusting if Gate's signature on it can be
            # verified; activation always requires that, whatever the
            # execute-path default of verify_response_signatures.
            problems.append(
                "no verifying keys (L9_VERIFYING_KEYS_JSON) to authenticate Gate's "
                "admission receipt"
            )
        return problems

    def resolve_verifying_key(self, key_id: str | None) -> str | bytes | None:
        if key_id is None:
            return None
        if key_id in self.verifying_keys:
            return self.verifying_keys[key_id]
        if (
            self.signing_key_id is not None
            and key_id == self.signing_key_id
            and self.signing_key is not None
        ):
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


def get_gate_client_config_from_env(**overrides: Any) -> GateClientConfig:
    """
    Build a :class:`GateClientConfig` from the environment.

    Keyword ``overrides`` (any ``GateClientConfig`` field) win over the
    environment, so an application whose settings come from elsewhere never has
    to write to ``os.environ`` to build a client. ``GATE_URL`` is only required
    when ``gate_url`` is not overridden.
    """
    unknown = set(overrides) - set(GateClientConfig.model_fields)
    if unknown:
        raise ValueError(f"unknown GateClientConfig fields: {', '.join(sorted(unknown))}")
    gate_url = overrides.get("gate_url") or os.getenv("GATE_URL", "").strip()
    if not gate_url:
        raise ValueError("GATE_URL is required")

    readers: dict[str, Callable[[], Any]] = {
        "local_node": lambda: (
            os.getenv("L9_NODE_NAME", "unknown-node").strip().lower() or "unknown-node"
        ),
        "timeout_seconds": lambda: float(os.getenv("GATE_CLIENT_TIMEOUT_SECONDS", "30.0")),
        "require_signature": lambda: _env_bool("L9_REQUIRE_SIGNATURE", False),
        "signing_key": lambda: os.getenv("L9_SIGNING_KEY") or os.getenv("L9_SIGNING_SECRET"),
        "signing_key_id": lambda: os.getenv("L9_SIGNING_KEY_ID"),
        "signing_algorithm": lambda: os.getenv("L9_SIGNING_ALGORITHM"),
        "verify_response_signatures": lambda: _env_bool("L9_REQUIRE_SIGNATURE", False),
        # Gate signs the responses it authors with *its* key id, so a node that
        # requires signatures must be able to resolve that id. The same
        # L9_VERIFYING_KEYS_JSON the worker runtime reads applies here; an
        # empty map used to make every env-configured node reject every signed
        # Gate response with "no verifying key available".
        "verifying_keys": lambda: _env_verifying_keys("L9_VERIFYING_KEYS_JSON"),
        "verify_hop_signatures": lambda: _env_bool("L9_VERIFY_HOP_SIGNATURES", False),
        "allowed_gate_destination": lambda: os.getenv("GATE_ALLOWED_DESTINATION", "gate"),
        "max_timeout_ms": lambda: _env_optional_int("GATE_CLIENT_MAX_TIMEOUT_MS"),
        "transport_margin_ms": lambda: int(os.getenv("GATE_CLIENT_TRANSPORT_MARGIN_MS", "0")),
    }
    # An override is applied instead of reading its variable, so a malformed or
    # stale environment value never blocks a caller that supplies its own.
    values: dict[str, Any] = {
        name: overrides[name] if name in overrides else read() for name, read in readers.items()
    }
    values["gate_url"] = gate_url
    values.update(overrides)
    return GateClientConfig(**values)


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
