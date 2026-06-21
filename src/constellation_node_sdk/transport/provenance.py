from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator


class RoutingProvenance(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    origin_kind: str
    requested_action: str
    resolved_by_gate: bool = False
    route_kind: str | None = None
    original_source_node: str | None = None

    @field_validator("origin_kind")
    @classmethod
    def validate_origin_kind(cls, value: str) -> str:
        normalized = value.strip().lower()
        allowed = {"client", "node", "gate"}
        if normalized not in allowed:
            msg = f"origin_kind must be one of {sorted(allowed)}"
            raise ValueError(msg)
        return normalized

    @field_validator("requested_action")
    @classmethod
    def validate_requested_action(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("requested_action must not be empty")
        return normalized

    @field_validator("route_kind")
    @classmethod
    def validate_route_kind(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        allowed = {"external_ingress", "gate_relay"}
        if normalized not in allowed:
            msg = f"route_kind must be one of {sorted(allowed)}"
            raise ValueError(msg)
        return normalized

    @field_validator("original_source_node")
    @classmethod
    def validate_original_source_node(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("original_source_node must not be blank")
        return normalized
