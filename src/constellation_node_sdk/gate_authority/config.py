from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from constellation_node_sdk.transport.key_binding import (
    KeyMaterial,
    VerifyingKey,
    bind_verifying_keys,
    verifying_key_resolver,
)


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

    def bound_key_resolver(self) -> Callable[[str | None], KeyMaterial | None]:
        """A resolver binding every key id to exactly one signature algorithm (D-1).

        Peer keys bind by their declaration (``verifying_key_algorithms``) or, where
        unambiguous, by their material; the own signing algorithm binds only the
        own key id (ed25519 resolves to the derived public key). When a signature
        is required, a key that cannot be bound is refused (fail closed).
        """
        return verifying_key_resolver(
            verifying_keys=self.verifying_keys,
            declared_algorithms=self.verifying_key_algorithms,
            own_key_id=self.signing_key_id,
            own_key=self.signing_key,
            own_algorithm=self.signing_algorithm,
            require_binding=self.require_signature or self.verify_response_signatures,
        )

    def resolve_verifying_key(self, key_id: str | None) -> str | bytes | None:
        """The raw verifying material for ``key_id`` (unchanged contract).

        Verification uses :meth:`bound_key_resolver`, which also carries each
        key's algorithm binding. This accessor never refuses an unbound key.
        """
        own_key = self.signing_key
        value = verifying_key_resolver(
            verifying_keys=self.verifying_keys,
            declared_algorithms=self.verifying_key_algorithms,
            own_key_id=self.signing_key_id,
            own_key=own_key,
            own_algorithm=self.signing_algorithm,
        )(key_id)
        return value.material if isinstance(value, VerifyingKey) else value

    @model_validator(mode="after")
    def validate_verifying_key_algorithms(self) -> GateDispatchTransportConfig:
        """Every declaration names a known key id, a supported algorithm and agrees with the key."""
        bind_verifying_keys(self.verifying_keys, self.verifying_key_algorithms)
        return self
