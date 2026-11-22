"""
Key-id to signature-algorithm binding for transport and hop verification.

A signature is only as strong as the rule that decides how its key material is
interpreted. If that rule is the packet's own ``signature_algorithm``, a packet
declaring ``hmac-sha256`` turns an ed25519 *public* key into an HMAC secret,
and anyone can forge it. This module binds each key to exactly one algorithm
and refuses a mismatch before any cryptographic check runs.

It lives under ``transport`` (not ``security``) so that hop verification and
packet verification share one implementation without an import cycle.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from constellation_node_sdk.transport.errors import TransportAuthenticationError

_SUPPORTED_ALGORITHMS = frozenset({"hmac-sha256", "ed25519"})


@dataclass(frozen=True)
class VerifyingKey:
    """
    Key material bound to exactly one signature algorithm.

    A key resolver may return this instead of raw material. The verifier then
    refuses any signature whose declared algorithm differs from ``algorithm``
    before it performs any cryptographic check, so the packet can never choose
    how its own key material is interpreted.
    """

    algorithm: str
    material: str | bytes

    def __post_init__(self) -> None:
        normalized = _normalize_algorithm(self.algorithm)
        if normalized is None:
            raise ValueError(
                f"VerifyingKey algorithm must be one of {sorted(_SUPPORTED_ALGORITHMS)}"
            )
        object.__setattr__(self, "algorithm", normalized)


KeyMaterial = str | bytes | VerifyingKey
KeyResolver = (
    Callable[[str | None], KeyMaterial | None] | Mapping[str, KeyMaterial] | str | bytes | None
)


def coerce_key_bytes(value: str | bytes) -> bytes:
    if isinstance(value, bytes):
        return value
    normalized = value.strip()
    if normalized.startswith("-----BEGIN"):
        return normalized.encode("utf-8")
    try:
        return base64.b64decode(normalized, validate=True)
    except Exception:
        return normalized.encode("utf-8")


def _normalize_algorithm(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized not in _SUPPORTED_ALGORITHMS:
        return None
    return normalized


def _lookup_key(key_resolver: KeyResolver, key_id: str | None) -> KeyMaterial | None:
    if key_resolver is None:
        return None
    if isinstance(key_resolver, (bytes, str)):
        return key_resolver
    if callable(key_resolver):
        return key_resolver(key_id)
    if key_id is None:
        return None
    return key_resolver.get(key_id)


def resolve_bound_verifying_key(
    key_resolver: KeyResolver,
    key_id: str | None,
    *,
    declared_algorithm: str,
    key_algorithm: str | None = None,
    subject: str = "transport",
) -> bytes | None:
    """
    Resolve key material for ``key_id`` and enforce its algorithm binding.

    The binding is the resolved :class:`VerifyingKey`'s algorithm, else the
    caller's ``key_algorithm``. When a binding exists, a ``declared_algorithm``
    that differs from it is refused here, before any cryptographic operation:
    an ed25519 public key is never used as an HMAC secret. Two bindings that
    disagree are a configuration conflict and are refused as well.

    Returns ``None`` when no key material is available.
    """
    caller_binding: str | None = None
    if key_algorithm is not None:
        caller_binding = _normalize_algorithm(key_algorithm)
        if caller_binding is None:
            raise TransportAuthenticationError(f"unsupported bound key algorithm: {key_algorithm}")

    value = _lookup_key(key_resolver, key_id)
    if value is None:
        return None

    if isinstance(value, VerifyingKey):
        if caller_binding is not None and caller_binding != value.algorithm:
            raise TransportAuthenticationError(
                f"conflicting algorithm bindings for {subject} signing key id {key_id!r}"
            )
        binding: str | None = value.algorithm
        material = value.material
    else:
        binding = caller_binding
        material = value

    if binding is not None and declared_algorithm != binding:
        raise TransportAuthenticationError(
            f"{subject} signature algorithm {declared_algorithm!r} does not match the "
            f"algorithm {binding!r} bound to signing key id {key_id!r}"
        )
    return coerce_key_bytes(material)
