"""
D-1 regression: a peer's verifying key is bound by the peer key, never by the node's own algorithm.

Gate_SDK 1.3.3 bound every verifying key to the verifying node's OWN
``signing_algorithm`` (``runtime/execution.py`` passed
``key_algorithm=signing_algorithm``). A node that signs hmac-sha256 then refused
Gate's ed25519 signature (GATE-KEY-01; reproduced by CEG
``test_ed25519_gate_signature_accepted_when_trust_required``). The fix keeps the
D-1 invariant, one key id to exactly one algorithm, but takes the algorithm from
the key itself:

* a declared algorithm (``verifying_key_algorithms`` /
  ``L9_VERIFYING_KEY_ALGORITHMS_JSON``);
* else the material where it is unambiguous: a PEM Ed25519 public key is
  ed25519, raw material that is not 32 bytes is an HMAC secret;
* exactly 32 raw bytes with no declaration is ambiguous and is refused when a
  signature is required (fail closed), never defaulted to the node's algorithm.
"""

from __future__ import annotations

import asyncio
import base64
import os

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from constellation_node_sdk import VerifyingKey
from constellation_node_sdk.gate.client import GateClient
from constellation_node_sdk.gate.config import GateClientConfig, get_gate_client_config_from_env
from constellation_node_sdk.runtime.config import NodeRuntimeConfig, get_runtime_config
from constellation_node_sdk.runtime.execution import execute_transport_packet
from constellation_node_sdk.runtime.handlers import register_handler
from constellation_node_sdk.security.errors import TransportAuthenticationError
from constellation_node_sdk.security.signing import sign_transport_packet
from constellation_node_sdk.transport.key_binding import (
    bind_verifying_keys,
    classify_key_material,
)
from constellation_node_sdk.transport.packet import TransportPacket, create_transport_packet
from constellation_node_sdk.transport.provenance import RoutingProvenance

GATE = "gate-gl"
NODE_HMAC = "ceg-secret-0123456789abcdef01234567890"  # 38 bytes: can only be HMAC


def _ed25519() -> tuple[Ed25519PrivateKey, str, str, str]:
    """(key, base64 raw private, base64 raw public, PEM public)."""
    key = Ed25519PrivateKey.generate()
    public_pem = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return (
        key,
        base64.b64encode(key.private_bytes_raw()).decode(),
        base64.b64encode(key.public_key().public_bytes_raw()).decode(),
        public_pem,
    )


def _gate_packet(*, key: str, algorithm: str, key_id: str = GATE) -> TransportPacket:
    packet = create_transport_packet(
        action="match",
        payload={"entity_id": "42"},
        tenant="acme",
        destination_node="graph",
        source_node="gate",
        reply_to="gate",
    )
    return sign_transport_packet(packet, key=key, key_id=key_id, algorithm=algorithm)


def _execute(packet: TransportPacket, **node: object) -> TransportPacket:
    register_handler("match", lambda tenant, payload: {"status": "completed"})
    return asyncio.run(
        execute_transport_packet(packet, node_name="graph", require_signature=True, **node)  # type: ignore[arg-type]
    )


HMAC_NODE: dict[str, object] = {
    "signing_key": NODE_HMAC,
    "signing_key_id": "ceg-e2e",
    "signing_algorithm": "hmac-sha256",
}


# --- an hmac node and an ed25519 Gate ------------------------------------------


def test_hmac_node_accepts_ed25519_gate_key_declared_ed25519() -> None:
    _, gate_private, gate_public, _ = _ed25519()

    response = _execute(
        _gate_packet(key=gate_private, algorithm="ed25519"),
        verifying_keys={GATE: gate_public},
        verifying_key_algorithms={GATE: "ed25519"},
        **HMAC_NODE,
    )

    assert response.payload["status"] == "completed"


def test_hmac_node_accepts_ed25519_gate_pem_key_without_a_declaration() -> None:
    """The CEG GATE-KEY-01 reproducer's shape: a PEM key is self-describing."""
    _, gate_private, _, gate_pem = _ed25519()

    response = _execute(
        _gate_packet(key=gate_private, algorithm="ed25519"),
        verifying_keys={GATE: gate_pem},
        **HMAC_NODE,
    )

    assert response.payload["status"] == "completed"


@pytest.mark.parametrize("declared", [True, False])
def test_hmac_node_refuses_the_gate_key_presented_as_hmac(declared: bool) -> None:
    """D-1 still holds: the Gate's public key is never an HMAC secret."""
    _, _, gate_public, gate_pem = _ed25519()
    material = gate_public if declared else gate_pem
    forged = _gate_packet(key=material, algorithm="hmac-sha256")
    declarations = {GATE: "ed25519"} if declared else {}

    with pytest.raises(TransportAuthenticationError, match="does not match the algorithm"):
        _execute(
            forged,
            verifying_keys={GATE: material},
            verifying_key_algorithms=declarations,
            **HMAC_NODE,
        )


# --- an ed25519 node and an hmac peer ------------------------------------------


@pytest.mark.parametrize(
    ("secret", "declarations"),
    [
        ("gate-secret-0123456789abcdef0123456789", {}),
        (base64.b64encode(os.urandom(32)).decode(), {GATE: "hmac-sha256"}),
    ],
    ids=["unambiguous-secret", "32-byte-secret-declared"],
)
def test_ed25519_node_accepts_hmac_peer_key_bound_to_hmac(
    secret: str, declarations: dict[str, str]
) -> None:
    _, node_private, _, _ = _ed25519()

    response = _execute(
        _gate_packet(key=secret, algorithm="hmac-sha256"),
        signing_private_key=node_private,
        signing_key_id="ceg-gl",
        signing_algorithm="ed25519",
        verifying_keys={GATE: secret},
        verifying_key_algorithms=declarations,
    )

    assert response.payload["status"] == "completed"


# --- ambiguity fails closed ------------------------------------------------------


def test_undeclared_32_byte_key_is_refused_when_signatures_are_required() -> None:
    _, gate_private, gate_public, _ = _ed25519()

    with pytest.raises(TransportAuthenticationError, match="no declared signature algorithm"):
        _execute(
            _gate_packet(key=gate_private, algorithm="ed25519"),
            verifying_keys={GATE: gate_public},
            **HMAC_NODE,
        )


def test_node_algorithm_never_binds_a_peer_key() -> None:
    """The 1.3.3 regression: the node's hmac must not stand in for the peer's binding."""
    _, gate_private, gate_public, _ = _ed25519()
    packet = _gate_packet(key=gate_private, algorithm="ed25519")

    with pytest.raises(TransportAuthenticationError) as refused:
        _execute(packet, verifying_keys={GATE: gate_public}, **HMAC_NODE)

    assert "hmac-sha256" not in str(refused.value)


# --- conflicting or invalid bindings are refused -------------------------------


def test_declaration_contradicting_the_material_is_refused() -> None:
    _, _, _, gate_pem = _ed25519()

    with pytest.raises(ValueError, match="can only be 'ed25519'"):
        bind_verifying_keys({GATE: gate_pem}, {GATE: "hmac-sha256"})
    with pytest.raises(ValidationError, match="can only be 'ed25519'"):
        NodeRuntimeConfig(
            environment="local",
            node_name="graph",
            service_name="graph",
            service_version="1.0.0",
            verifying_keys={GATE: gate_pem},
            verifying_key_algorithms={GATE: "hmac-sha256"},
        )


def test_declaration_for_an_unknown_key_id_is_refused() -> None:
    with pytest.raises(ValidationError, match="unknown key ids"):
        GateClientConfig(
            gate_url="http://gate:8000",
            local_node="eie",
            verifying_keys={GATE: NODE_HMAC},
            verifying_key_algorithms={"other": "hmac-sha256"},
        )


def test_unsupported_declared_algorithm_is_refused() -> None:
    with pytest.raises(ValueError, match="algorithm must be one of"):
        bind_verifying_keys({GATE: NODE_HMAC}, {GATE: "rsa"})


def test_resolver_verifying_key_conflicting_with_a_caller_binding_is_refused() -> None:
    _, _, gate_public, _ = _ed25519()
    from constellation_node_sdk.security.verification import verify_transport_packet_signature

    packet = _gate_packet(key=gate_public, algorithm="hmac-sha256")
    resolver = {GATE: VerifyingKey("ed25519", gate_public)}
    with pytest.raises(TransportAuthenticationError, match="conflicting algorithm bindings"):
        verify_transport_packet_signature(
            packet, key_resolver=resolver, key_algorithm="hmac-sha256"
        )


# --- classification ----------------------------------------------------------------


def test_classification_of_key_material() -> None:
    _, _, gate_public, gate_pem = _ed25519()

    assert classify_key_material(gate_pem) == "ed25519"
    assert classify_key_material(NODE_HMAC) == "hmac-sha256"
    assert classify_key_material(gate_public) is None
    assert classify_key_material(base64.b64encode(os.urandom(32)).decode()) is None


# --- the GateClient and the env layout ----------------------------------------------


class _Ed25519Gate(httpx.AsyncBaseTransport):
    def __init__(self, gate_private: str) -> None:
        self._gate_private = gate_private

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = sign_transport_packet(
            create_transport_packet(
                action="enrich",
                payload={"status": "completed"},
                tenant="tenant-a",
                destination_node="eie",
                source_node="gate",
                reply_to="gate",
            ),
            key=self._gate_private,
            key_id=GATE,
            algorithm="ed25519",
        )
        return httpx.Response(200, json=response.model_dump_json_dict(), request=request)


@pytest.mark.asyncio
async def test_hmac_gate_client_verifies_an_ed25519_gate_response() -> None:
    _, gate_private, _, gate_pem = _ed25519()
    config = GateClientConfig(
        gate_url="http://gate:8000",
        local_node="eie",
        require_signature=True,
        signing_key=NODE_HMAC,
        signing_key_id="eie-hmac",
        signing_algorithm="hmac-sha256",
        verify_response_signatures=True,
        verifying_keys={GATE: gate_pem},
    )
    packet = create_transport_packet(
        action="enrich",
        payload={"entity_id": "42"},
        tenant="tenant-a",
        destination_node="gate",
        source_node="eie",
        reply_to="eie",
        provenance=RoutingProvenance(
            origin_kind="node",
            requested_action="enrich",
            resolved_by_gate=False,
            original_source_node="eie",
        ),
    )

    response = await GateClient(config, transport=_Ed25519Gate(gate_private)).send_to_gate(packet)

    assert response.payload["status"] == "completed"


def test_runtime_and_client_read_declarations_from_the_same_env_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, gate_public, _ = _ed25519()
    monkeypatch.setenv("GATE_URL", "http://gate:8000")
    monkeypatch.setenv("L9_VERIFYING_KEYS_JSON", f'{{"{GATE}": "{gate_public}"}}')
    monkeypatch.setenv("L9_VERIFYING_KEY_ALGORITHMS_JSON", f'{{"{GATE}": "ed25519"}}')
    get_runtime_config.cache_clear()
    try:
        assert get_runtime_config().verifying_key_algorithms == {GATE: "ed25519"}
        assert get_gate_client_config_from_env().verifying_key_algorithms == {GATE: "ed25519"}
    finally:
        get_runtime_config.cache_clear()


# --- the runtime config's resolvers and the remaining binding branches -----------


def _runtime_config(**fields: object) -> NodeRuntimeConfig:
    values: dict[str, object] = {
        "environment": "local",
        "node_name": "graph",
        "service_name": "graph",
        "service_version": "1.0.0",
    }
    values.update(fields)
    return NodeRuntimeConfig(**values)  # type: ignore[arg-type]


def test_runtime_config_bound_resolver_and_raw_accessor() -> None:
    _, _, gate_public, _ = _ed25519()
    config = _runtime_config(
        require_signature=True,
        signing_key=NODE_HMAC,
        signing_key_id="ceg-e2e",
        verifying_keys={GATE: gate_public, "ambiguous": base64.b64encode(os.urandom(32)).decode()},
        verifying_key_algorithms={GATE: "ed25519"},
    )
    resolve = config.bound_key_resolver()

    assert resolve(GATE) == VerifyingKey("ed25519", gate_public)
    assert resolve("ceg-e2e") == VerifyingKey("hmac-sha256", NODE_HMAC)
    assert resolve("unknown") is None
    with pytest.raises(TransportAuthenticationError, match="no declared signature algorithm"):
        resolve("ambiguous")
    # The raw accessor keeps its contract: material, never a refusal.
    assert config.resolve_verifying_key(GATE) == gate_public
    assert config.resolve_verifying_key("ambiguous") is not None


def test_own_key_id_binds_to_the_own_algorithm_even_when_listed_as_ambiguous() -> None:
    from constellation_node_sdk.transport.key_binding import verifying_key_resolver

    _, node_private, node_public, _ = _ed25519()
    resolve = verifying_key_resolver(
        verifying_keys={"ceg-gl": node_public},
        own_key_id="ceg-gl",
        own_key=node_private,
        own_algorithm="ed25519",
        require_binding=True,
    )

    assert resolve("ceg-gl") == VerifyingKey("ed25519", node_public)


def test_unbound_key_is_returned_raw_when_signatures_are_optional() -> None:
    from constellation_node_sdk.transport.key_binding import verifying_key_resolver

    material = base64.b64encode(os.urandom(32)).decode()
    resolve = verifying_key_resolver(verifying_keys={GATE: material}, require_binding=False)

    assert resolve(GATE) == material


def test_pem_that_is_not_an_ed25519_public_key_is_ambiguous() -> None:
    from cryptography.hazmat.primitives.asymmetric.ec import SECP256R1, generate_private_key

    ec_pem = (
        generate_private_key(SECP256R1())
        .public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )

    assert classify_key_material(ec_pem) is None
    assert (
        classify_key_material("-----BEGIN PUBLIC KEY-----\nnot-a-key\n-----END PUBLIC KEY-----")
        is None
    )
