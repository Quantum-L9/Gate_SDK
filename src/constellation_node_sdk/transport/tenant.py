from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from .errors import TenantMutationError


class TenantContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: str
    on_behalf_of: str
    originator: str
    org_id: str
    user_id: str | None = None

    @field_validator("actor", "on_behalf_of", "originator", "org_id")
    @classmethod
    def validate_required_strings(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("tenant fields must not be empty")
        return normalized

    @field_validator("user_id")
    @classmethod
    def validate_optional_string(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("user_id must not be blank")
        return normalized


def ensure_tenant_context(value: str | dict[str, Any] | TenantContext) -> TenantContext:
    """Normalize a string, dict, or TenantContext into a canonical TenantContext."""
    if isinstance(value, TenantContext):
        return value

    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            raise ValueError("tenant string must not be empty")
        return TenantContext(
            actor=normalized,
            on_behalf_of=normalized,
            originator=normalized,
            org_id=normalized,
            user_id=None,
        )

    payload = dict(value or {})
    if "tenant_id" in payload and "org_id" not in payload:
        payload["org_id"] = payload["tenant_id"]

    org_id = str(payload.get("org_id") or payload.get("actor") or "default").strip()
    actor = str(payload.get("actor") or org_id).strip()
    on_behalf_of = str(payload.get("on_behalf_of") or actor).strip()
    originator = str(payload.get("originator") or actor).strip()

    return TenantContext(
        actor=actor,
        on_behalf_of=on_behalf_of,
        originator=originator,
        org_id=org_id,
        user_id=payload.get("user_id"),
    )


def assert_tenant_immutable(parent: TenantContext, child: TenantContext) -> None:
    """Raise if a derived packet mutates tenant context."""
    if parent != child:
        raise TenantMutationError("tenant context is immutable across derived packets")
