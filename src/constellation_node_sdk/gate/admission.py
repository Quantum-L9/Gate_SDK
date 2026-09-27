"""
Consumer admission: the client half of "configured -> admitted -> usable".

Gate alone decides whether a signing key is admitted and which actions it may
invoke (its verifying keyring and per-key action scopes). A consumer used to
learn that only from a 403 on a real call. :meth:`GateClient.activate` asks Gate
up front with a signed probe to ``POST /v1/admission`` and returns Gate's answer
as a typed :class:`ConsumerAccessReceipt`. The SDK never grants anything.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .errors import GateResponseError

#: Reserved action of the admission probe. Gate refuses it on /v1/execute and
#: refuses any node that tries to register it.
ADMISSION_ACTION = "gate.admission"
ADMISSION_PATH = "/v1/admission"
ADMISSION_SCHEMA = "l9.gate.admission.v1"


class ConsumerAccessReceipt(BaseModel):
    """Gate's answer to an admission probe, as seen by the consumer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    gate_node: str
    key_id: str | None
    source_node: str
    scope: Literal["restricted", "unrestricted"]
    scoped_actions: tuple[str, ...] | None = None
    granted_actions: tuple[str, ...] = Field(default_factory=tuple)
    routable_actions: tuple[str, ...] = Field(default_factory=tuple)
    required_actions: tuple[str, ...] = Field(default_factory=tuple)

    @property
    def missing_actions(self) -> tuple[str, ...]:
        """Required actions Gate did not grant (empty when fully admitted)."""
        granted = set(self.granted_actions)
        return tuple(a for a in self.required_actions if a not in granted)

    @property
    def admitted(self) -> bool:
        return not self.missing_actions

    def permits(self, action: str) -> bool:
        return action.strip().lower() in self.granted_actions


def parse_admission_payload(
    payload: dict[str, Any], *, required_actions: tuple[str, ...]
) -> ConsumerAccessReceipt:
    """Validate Gate's admission payload into a receipt, or raise GateResponseError."""
    if payload.get("schema") != ADMISSION_SCHEMA:
        raise GateResponseError(
            f"Gate admission response has schema {payload.get('schema')!r}, "
            f"expected {ADMISSION_SCHEMA!r}",
            body=payload,
        )
    fields = {k: v for k, v in payload.items() if k != "schema"}
    try:
        return ConsumerAccessReceipt.model_validate(
            {**fields, "required_actions": required_actions}
        )
    except ValidationError as exc:
        raise GateResponseError(
            f"Gate admission response is malformed: {exc}", body=payload
        ) from exc


__all__ = [
    "ADMISSION_ACTION",
    "ADMISSION_PATH",
    "ADMISSION_SCHEMA",
    "ConsumerAccessReceipt",
    "parse_admission_payload",
]
