from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from constellation_node_sdk.transport.key_binding import (
    BoundVerifyingKeys,
    bind_verifying_keys,
)


class GateDispatchTransportConfig(BoundVerifyingKeys, BaseModel):
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
    # Per-key signature algorithm for verifying keys whose material is ambiguous
    # (exactly 32 raw bytes): {key_id: "ed25519" | "hmac-sha256"}. A PEM Ed25519
    # public key binds to ed25519 and any other raw value to hmac-sha256 without
    # a declaration. The node's own signing algorithm never binds a peer key.
    verifying_key_algorithms: dict[str, str] = {}
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

    def _binding_required(self) -> bool:
        return self.require_signature or self.verify_response_signatures

    @model_validator(mode="after")
    def validate_verifying_key_algorithms(self) -> GateDispatchTransportConfig:
        """Every declaration names a known key id, a supported algorithm and agrees with the key."""
        bind_verifying_keys(self.verifying_keys, self.verifying_key_algorithms)
        return self
