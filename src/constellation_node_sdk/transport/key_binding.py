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
from typing import TYPE_CHECKING

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key, load_pem_public_key

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


def own_verifying_key(
    signing_key: str | bytes | None, signing_algorithm: str | None
) -> str | bytes | None:
    """
    The material that verifies a signature this party made with ``signing_key``.

    For HMAC that is the shared secret itself, returned unchanged. For ed25519 it
    is the public key derived from the private key: a private key is never
    verifying material, so an ed25519 party resolving its own key id to its
    private key could not validate a single packet it had just signed.
    """
    if signing_key is None:
        return None
    if _normalize_algorithm(signing_algorithm) != "ed25519":
        return signing_key
    raw = coerce_key_bytes(signing_key)
    if raw.startswith(b"-----BEGIN"):
        loaded = load_pem_private_key(raw, password=None)
        if not isinstance(loaded, Ed25519PrivateKey):
            raise TransportAuthenticationError("PEM private key is not Ed25519")
        private_key = loaded
    elif len(raw) == 32:
        private_key = Ed25519PrivateKey.from_private_bytes(raw)
    else:
        raise TransportAuthenticationError("unsupported ed25519 private key format")
    return private_key.public_key().public_bytes_raw()


def classify_key_material(material: str | bytes) -> str | None:
    """
    The one algorithm a verifying key's material can serve, or None when ambiguous.

    * A PEM Ed25519 public key can only verify ed25519.
    * Raw material that is not exactly 32 bytes cannot be an ed25519 public key,
      so it can only be an HMAC secret.
    * Exactly 32 raw bytes may be either a raw ed25519 public key or a 32-byte
      shared secret, and any other PEM is not a supported verifying key: both
      are ambiguous and need a declared algorithm.

    Classification never consults the verifying node's own signing algorithm:
    a peer's key is bound by what the key is, not by what the node uses.
    """
    raw = coerce_key_bytes(material)
    if raw.startswith(b"-----BEGIN"):
        try:
            loaded = load_pem_public_key(raw)
        except ValueError:
            return None
        return "ed25519" if isinstance(loaded, Ed25519PublicKey) else None
    if len(raw) == 32:
        return None
    return "hmac-sha256"


def bind_verifying_keys(
    verifying_keys: Mapping[str, str],
    declared_algorithms: Mapping[str, str] | None = None,
) -> dict[str, VerifyingKey | str]:
    """
    Bind each verifying key id to exactly one algorithm where that is knowable.

    A declared algorithm wins; otherwise the material is classified. A key
    whose material is ambiguous and that has no declaration stays unbound (its
    raw value is returned) so the caller can refuse it. A declaration for an
    unknown key id, an unsupported algorithm, or a declaration that contradicts
    what the material can serve is a configuration error (ValueError).
    """
    declared = dict(declared_algorithms or {})
    unknown = sorted(set(declared) - set(verifying_keys))
    if unknown:
        raise ValueError(f"verifying key algorithms declared for unknown key ids: {unknown}")
    bound: dict[str, VerifyingKey | str] = {}
    for key_id, material in verifying_keys.items():
        classified = classify_key_material(material)
        if key_id in declared:
            algorithm = _normalize_algorithm(declared[key_id])
            if algorithm is None:
                raise ValueError(
                    f"verifying key {key_id!r}: algorithm must be one of "
                    f"{sorted(_SUPPORTED_ALGORITHMS)}"
                )
            if classified is not None and classified != algorithm:
                raise ValueError(
                    f"verifying key {key_id!r} is declared {algorithm!r} but its material "
                    f"can only be {classified!r}"
                )
            bound[key_id] = VerifyingKey(algorithm, material)
        elif classified is not None:
            bound[key_id] = VerifyingKey(classified, material)
        else:
            bound[key_id] = material
    return bound


def verifying_key_resolver(
    *,
    verifying_keys: Mapping[str, str],
    declared_algorithms: Mapping[str, str] | None = None,
    own_key_id: str | None = None,
    own_key: str | bytes | None = None,
    own_algorithm: str | None = None,
    own_key_for_unknown_ids: bool = False,
    require_binding: bool = False,
) -> Callable[[str | None], KeyMaterial | None]:
    """
    A key resolver that binds every key id it resolves to exactly one algorithm.

    * Peer key ids are bound by :func:`bind_verifying_keys` (declaration, else
      material). The node's own signing algorithm never binds a peer key.
    * The node's own key id is bound to its own signing algorithm, resolving
      ed25519 to the public key (:func:`own_verifying_key`).
    * ``own_key_for_unknown_ids`` keeps the runtime's shared-secret mode (no
      verifying keys: every key id verifies with the node's own secret), which
      is still the node's own key and so carries its own algorithm.
    * With ``require_binding``, a key that cannot be bound is refused here
      instead of letting the packet choose its algorithm (fail closed).
    """
    bound = bind_verifying_keys(verifying_keys, declared_algorithms)
    own_material: str | bytes | None = None
    own_bound: str | None = _normalize_algorithm(own_algorithm)
    if own_key is not None:
        own_material = own_verifying_key(own_key, own_algorithm)

    def _own() -> KeyMaterial | None:
        if own_material is None:
            return None
        if own_bound is not None:
            return VerifyingKey(own_bound, own_material)
        return own_material

    def resolve(key_id: str | None) -> KeyMaterial | None:
        if key_id is not None and key_id in bound:
            value = bound[key_id]
            if isinstance(value, VerifyingKey):
                return value
            if key_id == own_key_id and own_bound is not None:
                return VerifyingKey(own_bound, value)
            if require_binding:
                raise TransportAuthenticationError(
                    f"verifying key {key_id!r} has no declared signature algorithm and its "
                    "material is ambiguous; declare it in L9_VERIFYING_KEY_ALGORITHMS_JSON"
                )
            return value
        if key_id is not None and key_id == own_key_id:
            return _own()
        if own_key_for_unknown_ids and not bound:
            return _own()
        return None

    return resolve


class BoundVerifyingKeys:
    """
    Verifying-key resolution shared by the SDK's signing configurations.

    A configuration that mixes this in exposes ``verifying_keys``,
    ``verifying_key_algorithms``, ``signing_key_id`` and ``signing_algorithm``,
    and overrides :meth:`_own_signing_material` and :meth:`_binding_required`
    where its own key or its signature policy differ.
    """

    if TYPE_CHECKING:
        verifying_keys: dict[str, str]
        verifying_key_algorithms: dict[str, str]
        signing_key_id: str | None
        signing_algorithm: str | None

    def _own_signing_material(self) -> str | bytes | None:
        return getattr(self, "signing_key", None)

    def _binding_required(self) -> bool:
        return False

    def _resolver(self, *, require_binding: bool) -> Callable[[str | None], KeyMaterial | None]:
        return verifying_key_resolver(
            verifying_keys=self.verifying_keys,
            declared_algorithms=self.verifying_key_algorithms,
            own_key_id=self.signing_key_id,
            own_key=self._own_signing_material(),
            own_algorithm=self.signing_algorithm,
            require_binding=require_binding,
        )

    def bound_key_resolver(self) -> Callable[[str | None], KeyMaterial | None]:
        """A resolver binding every key id to exactly one signature algorithm (D-1).

        Peer keys bind by their declaration (``verifying_key_algorithms``) or, where
        unambiguous, by their material; the own signing algorithm binds only the
        own key id (ed25519 resolves to the derived public key). When a signature
        is required, a key that cannot be bound is refused (fail closed).
        """
        return self._resolver(require_binding=self._binding_required())

    def resolve_verifying_key(self, key_id: str | None) -> str | bytes | None:
        """The raw verifying material for ``key_id`` (unchanged contract).

        Verification uses :meth:`bound_key_resolver`, which also carries each
        key's algorithm binding. This accessor never refuses an unbound key.
        """
        value = self._resolver(require_binding=False)(key_id)
        return value.material if isinstance(value, VerifyingKey) else value
