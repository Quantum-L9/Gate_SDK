from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator


class GateDispatchTransportConfig(BaseModel):
    """
    Configuration for Gate's worker-dispatch transport.

    Note what is absent: no worker URL, no target node, no timeout. Those are
    per-dispatch values that Gate resolves from its registry and its own
    deadline arithmetic — the SDK is told them, it never holds or discovers
    them.

    There is also no default operation budget here, unlike ``GateClientConfig``.
    A dispatch budget is never invented by the transport: it comes from the
    packet Gate derived, so that the deadline the worker is told about and the
    deadline Gate actually waits are the same number.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # The Gate node's own identity. Every dispatched packet must be sourced from
    # and replied to this node, or it is not a Gate-authored dispatch.
    local_gate_node: str = "gate"

    require_signature: bool = False
    signing_key: str | bytes | None = None
    signing_key_id: str | None = None
    signing_algorithm: str | None = None

    verify_response_signatures: bool = False
    verifying_keys: dict[str, str] = {}
    verify_hop_signatures: bool = False

    @field_validator("local_gate_node")
    @classmethod
    def validate_local_gate_node(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("local_gate_node must not be empty")
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
        if (
            self.signing_key_id is not None
            and key_id == self.signing_key_id
            and self.signing_key is not None
        ):
            return self.signing_key
        return None
