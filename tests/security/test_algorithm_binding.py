"""
D-1: a key id is bound to exactly one signature algorithm.

Before this binding the verifier chose its algorithm from the packet's own
``security.signature_algorithm``. A packet declaring ``hmac-sha256`` under an
ed25519 key id turned that key's *public* bytes into the HMAC secret, so anyone
holding a participant's public key could forge its packets. These tests pin the
refusal, that it happens before any cryptographic operation, and that genuine
HMAC and ed25519 signatures still verify.
"""

from __future__ import annotations

import base64
import os

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import constellation_node_sdk.security.verification as verification_module
import constellation_node_sdk.transport.hop_trace as hop_trace_module
from constellation_node_sdk import VerifyingKey
from constellation_node_sdk.runtime.execution import execute_transport_packet
from constellation_node_sdk.runtime.handlers import register_handler
from constellation_node_sdk.security.errors import TransportAuthenticationError
from constellation_node_sdk.security.signing import sign_transport_packet
from constellation_node_sdk.security.validation import validate_transport_packet
from constellation_node_sdk.security.verification import verify_transport_packet_signature
from constellation_node_sdk.transport.hop_trace import (
    make_execution_hop,
    validate_hop_trace,
    verify_hop_signature,
)
from constellation_node_sdk.transport.packet import TransportPacket, create_transport_packet

ED_KEY_ID = "odoo-gl"


def _packet(destination: str = "gate") -> TransportPacket:
    return create_transport_packet(
        action="match",
        payload={"entity_id": "42"},
        tenant="tenant-a",
        destination_node=destination,
        source_node="odoo",
        reply_to="odoo",
    )


def _ed25519_identity() -> tuple[str, str]:
    """(base64 raw private key, base64 raw public key), the rail's env layout."""
    private_key = Ed25519PrivateKey.generate()
    private_b64 = base64.b64encode(private_key.private_bytes_raw()).decode()
    public_b64 = base64.b64encode(private_key.public_key().public_bytes_raw()).decode()
    return private_b64, public_b64


def _forged_with_public_key(public_b64: str, destination: str = "gate") -> TransportPacket:
    """HMAC over transport_hash keyed with the participant's PUBLIC key."""
    return sign_transport_packet(
        _packet(destination), key=public_b64, key_id=ED_KEY_ID, algorithm="hmac-sha256"
    )


def _forged_with_random_secret(destination: str = "gate") -> TransportPacket:
    return sign_transport_packet(
        _packet(destination),
        key=base64.b64encode(os.urandom(32)).decode(),
        key_id=ED_KEY_ID,
        algorithm="hmac-sha256",
    )


@pytest.fixture
def no_hmac(monkeypatch: pytest.MonkeyPatch) -> list[bytes]:
    """Record every HMAC computed (the hmac module is shared, so this includes signing)."""
    calls: list[bytes] = []
    real_new = verification_module.hmac.new

    def recording_new(key: bytes, *args: object, **kwargs: object) -> object:
        calls.append(key)
        return real_new(key, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(verification_module.hmac, "new", recording_new)
    return calls


# --- the forgery vector -----------------------------------------------------


def test_forged_hmac_with_public_key_is_refused_by_caller_binding(no_hmac: list[bytes]) -> None:
    _, public_b64 = _ed25519_identity()
    forged = _forged_with_public_key(public_b64)
    no_hmac.clear()  # forging computed one; only verification is under test

    with pytest.raises(TransportAuthenticationError, match="does not match the algorithm"):
        verify_transport_packet_signature(
            forged, key_resolver={ED_KEY_ID: public_b64}, key_algorithm="ed25519"
        )
    assert no_hmac == []


def test_forged_hmac_with_public_key_is_refused_by_verifying_key_binding(
    no_hmac: list[bytes],
) -> None:
    _, public_b64 = _ed25519_identity()
    forged = _forged_with_public_key(public_b64)
    no_hmac.clear()  # forging computed one; only verification is under test

    with pytest.raises(TransportAuthenticationError, match="does not match the algorithm"):
        verify_transport_packet_signature(
            forged, key_resolver={ED_KEY_ID: VerifyingKey("ed25519", public_b64)}
        )
    assert no_hmac == []


def test_forged_hmac_with_public_key_is_refused_by_validate_transport_packet() -> None:
    _, public_b64 = _ed25519_identity()
    forged = _forged_with_public_key(public_b64)

    with pytest.raises(TransportAuthenticationError, match="does not match the algorithm"):
        validate_transport_packet(
            forged,
            key_resolver={ED_KEY_ID: public_b64}.get,
            key_algorithm="ed25519",
            require_signature=True,
        )


def test_random_secret_control_is_refused_with_and_without_binding() -> None:
    _, public_b64 = _ed25519_identity()
    control = _forged_with_random_secret()

    with pytest.raises(TransportAuthenticationError, match="does not match the algorithm"):
        verify_transport_packet_signature(
            control, key_resolver={ED_KEY_ID: public_b64}, key_algorithm="ed25519"
        )
    # Unbound, the random secret simply does not reproduce the signature.
    assert verify_transport_packet_signature(control, key_resolver={ED_KEY_ID: public_b64}) is False


def test_worker_runtime_refuses_the_forgery_under_ed25519() -> None:
    """The runtime binds verifying keys to its configured algorithm."""
    gate_private, gate_public = _ed25519_identity()
    node_private, _ = _ed25519_identity()
    register_handler("match", lambda tenant, payload: {"ok": True})
    forged = sign_transport_packet(
        _packet(destination="graph"), key=gate_public, key_id="gate-gl", algorithm="hmac-sha256"
    )

    with pytest.raises(TransportAuthenticationError, match="does not match the algorithm"):
        import asyncio

        asyncio.run(
            execute_transport_packet(
                forged,
                node_name="graph",
                signing_private_key=node_private,
                signing_key_id="ceg-gl",
                signing_algorithm="ed25519",
                verifying_keys={"gate-gl": gate_public},
                require_signature=True,
            )
        )
    assert gate_private  # the genuine key was never needed to forge


# --- genuine signatures still verify ---------------------------------------


def test_genuine_ed25519_signature_verifies_under_its_binding() -> None:
    private_b64, public_b64 = _ed25519_identity()
    signed = sign_transport_packet(
        _packet(), key=private_b64, key_id=ED_KEY_ID, algorithm="ed25519"
    )

    assert (
        verify_transport_packet_signature(
            signed, key_resolver={ED_KEY_ID: public_b64}, key_algorithm="ed25519"
        )
        is True
    )
    assert (
        verify_transport_packet_signature(
            signed, key_resolver={ED_KEY_ID: VerifyingKey("ed25519", public_b64)}
        )
        is True
    )


def test_genuine_hmac_signature_verifies_bound_and_unbound() -> None:
    secret = base64.b64encode(os.urandom(32)).decode()
    signed = sign_transport_packet(
        _packet(), key=secret, key_id="hmac-key-1", algorithm="hmac-sha256"
    )

    for kwargs in (
        {"key_resolver": {"hmac-key-1": secret}},
        {"key_resolver": {"hmac-key-1": secret}, "key_algorithm": "hmac-sha256"},
        {"key_resolver": {"hmac-key-1": VerifyingKey("HMAC-SHA256", secret)}},
    ):
        assert verify_transport_packet_signature(signed, **kwargs) is True  # type: ignore[arg-type]


def test_ed25519_packet_against_hmac_bound_key_is_refused() -> None:
    private_b64, _ = _ed25519_identity()
    signed = sign_transport_packet(
        _packet(), key=private_b64, key_id="hmac-key-1", algorithm="ed25519"
    )

    with pytest.raises(TransportAuthenticationError, match="does not match the algorithm"):
        verify_transport_packet_signature(
            signed, key_resolver={"hmac-key-1": "secret"}, key_algorithm="hmac-sha256"
        )


# --- binding configuration errors fail closed -------------------------------


def test_conflicting_bindings_are_refused() -> None:
    _, public_b64 = _ed25519_identity()
    forged = _forged_with_public_key(public_b64)

    with pytest.raises(TransportAuthenticationError, match="conflicting algorithm bindings"):
        verify_transport_packet_signature(
            forged,
            key_resolver={ED_KEY_ID: VerifyingKey("ed25519", public_b64)},
            key_algorithm="hmac-sha256",
        )


def test_unsupported_caller_binding_is_refused() -> None:
    _, public_b64 = _ed25519_identity()
    with pytest.raises(TransportAuthenticationError, match="unsupported bound key algorithm"):
        verify_transport_packet_signature(
            _forged_with_public_key(public_b64),
            key_resolver={ED_KEY_ID: public_b64},
            key_algorithm="rsa",
        )


def test_verifying_key_rejects_unsupported_algorithm() -> None:
    with pytest.raises(ValueError, match="VerifyingKey algorithm must be one of"):
        VerifyingKey("none", "material")


# --- hop signatures share the binding ---------------------------------------


def test_forged_hop_signature_with_public_key_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _, public_b64 = _ed25519_identity()
    packet = _packet()
    hop = make_execution_hop(
        packet=packet,
        node="odoo",
        action="match",
        key=public_b64,
        key_id=ED_KEY_ID,
        algorithm="hmac-sha256",
    )
    calls: list[bytes] = []
    real_new = hop_trace_module.hmac.new

    def recording_new(key: bytes, *args: object, **kwargs: object) -> object:
        calls.append(key)
        return real_new(key, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(hop_trace_module.hmac, "new", recording_new)

    with pytest.raises(TransportAuthenticationError, match="hop signature algorithm"):
        verify_hop_signature(hop, key_resolver={ED_KEY_ID: public_b64}, key_algorithm="ed25519")
    with pytest.raises(TransportAuthenticationError, match="hop signature algorithm"):
        validate_hop_trace(
            packet.with_hop(hop),
            verify_hop_signatures=True,
            hop_key_resolver={ED_KEY_ID: VerifyingKey("ed25519", public_b64)},
        )
    assert calls == []
