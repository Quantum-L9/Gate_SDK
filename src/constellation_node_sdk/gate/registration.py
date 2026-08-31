from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx
import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import GateRegistrationConfig, get_gate_registration_config_from_env

_DEFAULT_RETRY_BASE_SECONDS = 1.0
_DEFAULT_HEALTH_ENDPOINT = "/v1/health"
_GENERATED_BY = "constellation-node-sdk"

# Keys the SDK derives itself. A caller cannot smuggle a different value for
# these through `metadata`, so `metadata` stays control-plane metadata rather
# than becoming a generic payload escape hatch.
# Maps each reserved metadata key to the NodeRegistration field that sets it,
# so the rejection tells the caller what to do instead of only what not to do.
_RESERVED_METADATA_KEYS: dict[str, str] = {
    "owner": "owner",
    "version": "version",
    "type": "node_type",
    "generated_by": "(derived by the SDK; not settable)",
}


class NodeRegistration(BaseModel):
    """
    A node's Gate control-plane registration, expressed without bespoke HTTP.

    This is transport/control-plane metadata only. It is not a domain payload
    surface: ``metadata`` values are plain strings and the keys the SDK derives
    are reserved.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    node_name: str
    internal_url: str
    supported_actions: tuple[str, ...]
    priority_class: str = "P2"
    max_concurrent: int = Field(default=50, ge=1)
    health_endpoint: str = _DEFAULT_HEALTH_ENDPOINT
    timeout_ms: int = Field(default=30_000, ge=1)
    version: str = "1.0.0"
    node_type: str = "custom"
    # Gate resolves the semantic owner of a canonical action from
    # `metadata.owner` first, then from a recognizable node name. A node whose
    # name Gate cannot map to an owner MUST set this or Gate rejects the
    # registration of any canonical action.
    owner: str | None = None
    metadata: Mapping[str, str] = Field(default_factory=dict)

    @field_validator("node_name")
    @classmethod
    def validate_node_name(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("node_name must not be blank")
        return normalized

    @field_validator("internal_url")
    @classmethod
    def validate_internal_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if not normalized:
            raise ValueError("internal_url must not be blank")
        if not (normalized.startswith("http://") or normalized.startswith("https://")):
            raise ValueError("internal_url must start with http:// or https://")
        return normalized

    @field_validator("supported_actions", mode="before")
    @classmethod
    def validate_supported_actions(cls, value: Any) -> tuple[str, ...]:
        if isinstance(value, str) or not isinstance(value, Sequence):
            raise ValueError("supported_actions must be a sequence of action names")
        normalized = tuple(str(item).strip().lower() for item in value if str(item).strip())
        if not normalized:
            raise ValueError("supported_actions must contain at least one non-blank action")
        if len(set(normalized)) != len(normalized):
            raise ValueError("supported_actions must not contain duplicates")
        return normalized

    @field_validator("priority_class")
    @classmethod
    def validate_priority_class(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in {"P0", "P1", "P2", "P3"}:
            raise ValueError("priority_class must be one of P0, P1, P2, P3")
        return normalized

    @field_validator("health_endpoint")
    @classmethod
    def validate_health_endpoint(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized.startswith("/"):
            raise ValueError("health_endpoint must start with /")
        return normalized

    @field_validator("version", "node_type")
    @classmethod
    def validate_required_strings(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("version and node_type must not be blank")
        return normalized

    @field_validator("owner")
    @classmethod
    def validate_owner(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("owner must not be blank when provided")
        return normalized

    @field_validator("metadata", mode="before")
    @classmethod
    def validate_metadata(cls, value: Any) -> dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("metadata must be a mapping of string keys to string values")
        normalized: dict[str, str] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key).strip()
            if not key:
                raise ValueError("metadata keys must not be blank")
            if key in _RESERVED_METADATA_KEYS:
                raise ValueError(
                    f"metadata key {key!r} is derived by the SDK; set "
                    f"NodeRegistration.{_RESERVED_METADATA_KEYS[key]} instead"
                )
            if not isinstance(raw_value, str):
                raise ValueError(
                    f"metadata[{key!r}] must be a string; Gate accepts string metadata values only"
                )
            normalized[key] = raw_value.strip()
        return normalized

    def to_payload(self) -> dict[str, dict[str, Any]]:
        """Render the Gate admin-registration body, keyed by node name."""
        metadata: dict[str, str] = {
            "version": self.version,
            "type": self.node_type,
            "generated_by": _GENERATED_BY,
        }
        if self.owner is not None:
            metadata["owner"] = self.owner
        metadata.update(self.metadata)

        return {
            self.node_name: {
                "internal_url": self.internal_url,
                "supported_actions": list(self.supported_actions),
                "priority_class": self.priority_class,
                "max_concurrent": self.max_concurrent,
                "health_endpoint": self.health_endpoint,
                "timeout_ms": self.timeout_ms,
                "metadata": metadata,
            }
        }


def load_node_spec(spec_path: str) -> dict[str, Any]:
    """
    Load node spec.yaml and require a top-level mapping.
    """
    path = Path(spec_path)
    if not path.exists():
        raise FileNotFoundError(f"node spec not found: {path.resolve()}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"node spec must be a YAML mapping: {path}")
    return raw


def build_node_registration(spec: dict[str, Any]) -> NodeRegistration:
    """
    Convert spec.yaml into a :class:`NodeRegistration`.

    Required:
    - node.id
    - node.actions
    """
    node = spec.get("node", {})
    if not isinstance(node, dict) or not node:
        raise ValueError("spec.yaml missing required node block")

    node_id = str(node.get("id", "")).strip().lower()
    if not node_id:
        raise ValueError("spec.yaml node.id is required")

    actions_raw = node.get("actions", [])
    if not isinstance(actions_raw, list) or not actions_raw:
        raise ValueError(f"spec.yaml node.actions must not be empty (node: {node_id})")

    owner_raw = node.get("owner")
    metadata_raw = node.get("metadata") or {}
    if not isinstance(metadata_raw, dict):
        raise ValueError(f"spec.yaml node.metadata must be a mapping (node: {node_id})")

    return NodeRegistration(
        node_name=node_id,
        internal_url=str(node.get("internal_url", f"http://{node_id}:8000")),
        supported_actions=tuple(str(action) for action in actions_raw),
        priority_class=str(node.get("priority_class", "P2")).strip() or "P2",
        max_concurrent=int(node.get("max_concurrent", 50)),
        health_endpoint=str(node.get("health_endpoint", _DEFAULT_HEALTH_ENDPOINT)).strip()
        or _DEFAULT_HEALTH_ENDPOINT,
        timeout_ms=int(node.get("timeout_ms", 30_000)),
        version=str(node.get("version", "1.0.0")).strip() or "1.0.0",
        node_type=str(node.get("type", "custom")).strip() or "custom",
        owner=None if owner_raw is None else str(owner_raw),
        metadata={str(k): str(v) for k, v in metadata_raw.items()},
    )


def build_registration_payload(spec: dict[str, Any]) -> dict[str, Any]:
    """
    Convert spec.yaml into a Gate admin registration payload.

    Retained as the spec-driven entry point; ``build_node_registration`` is the
    typed equivalent and ``NodeRegistration.to_payload`` renders the same body.
    """
    return build_node_registration(spec).to_payload()


async def register_node(
    *,
    gate_url: str,
    registration: NodeRegistration,
    admin_token: str | None = None,
    retries: int = 3,
    overwrite: bool = True,
) -> bool:
    """
    Register a node with Gate from an in-process :class:`NodeRegistration`.

    This is the programmatic registration surface: a node whose identity comes
    from application settings rather than a ``spec.yaml`` on disk needs no
    bespoke HTTP client of its own.

    Registration is control-plane reconciliation, not application execution, so
    a bounded retry with visible backoff is permitted here. That exemption does
    not extend to ``GateClient.execute``.

    Returns True on success, False on Gate rejection or retry exhaustion.
    Registration failure is intentionally non-fatal for node startup.
    """
    payload = registration.to_payload()
    url = f"{gate_url.rstrip('/')}/v1/admin/register"
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if admin_token:
        headers["X-Admin-Token"] = admin_token
    params = {"overwrite": "true" if overwrite else "false"}

    for attempt in range(1, retries + 1):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    url,
                    json=payload,
                    headers=headers,
                    params=params,
                )

            if response.status_code == 200:
                return True

            if response.status_code in {400, 401, 403, 409, 422}:
                return False

        except httpx.TransportError:
            pass

        if attempt < retries:
            backoff = _DEFAULT_RETRY_BASE_SECONDS * (2 ** (attempt - 1))
            await asyncio.sleep(backoff)

    return False


async def register_with_gate(
    *,
    gate_url: str,
    admin_token: str | None = None,
    spec_path: str,
    retries: int = 3,
    overwrite: bool = True,
) -> bool:
    """
    Register the current node with Gate via POST /v1/admin/register, from spec.yaml.

    Returns True on success, False on rejection or after retry exhaustion.
    Registration failure is intentionally non-fatal for node startup.
    """
    try:
        spec = load_node_spec(spec_path)
        registration = build_node_registration(spec)
    except (FileNotFoundError, ValueError):
        return False

    return await register_node(
        gate_url=gate_url,
        registration=registration,
        admin_token=admin_token,
        retries=retries,
        overwrite=overwrite,
    )


async def register_from_env() -> bool:
    """
    Convenience wrapper for Gate registration using environment-derived config.
    """
    config: GateRegistrationConfig = get_gate_registration_config_from_env()
    if not config.registration_enabled:
        return False

    return await register_with_gate(
        gate_url=config.gate_url,
        admin_token=config.admin_token,
        spec_path=config.spec_path,
        retries=config.retries,
        overwrite=config.overwrite,
    )
