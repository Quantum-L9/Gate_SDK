from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from constellation_node_sdk.security.errors import TransportAuthenticationError
from constellation_node_sdk.security.signing import sign_transport_packet
from constellation_node_sdk.security.verification import verify_transport_packet_signature
from constellation_node_sdk.transport.packet import create_transport_packet


def test_verify_transport_packet_signature_returns_true_for_valid_hmac_signature() -> None:
    packet = create_transport_packet(
        action="enrich",
        payload={"entity_id": "42"},
        tenant="tenant-a",
        destination_node="gate",
        source_node="client",
        reply_to="client",
    )
    signed = sign_transport_packet(
        packet,
        key="super-secret",
        key_id="hmac-key-1",
        algorithm="hmac-sha256",
    )

    assert (
        verify_transport_packet_signature(
            signed,
            key_resolver={"hmac-key-1": "super-secret"},
        )
        is True
    )


def test_verify_transport_packet_signature_returns_false_for_unsigned_packet() -> None:
    packet = create_transport_packet(
        action="enrich",
        payload={"entity_id": "42"},
        tenant="tenant-a",
        destination_node="gate",
        source_node="client",
        reply_to="client",
    )

    assert (
        verify_transport_packet_signature(
            packet,
            key_resolver={"hmac-key-1": "super-secret"},
        )
        is False
    )


def _signed_packet_and_key(algorithm: str) -> tuple:
    packet = create_transport_packet(
        action="enrich",
        payload={"entity_id": "42"},
        tenant="tenant-a",
        destination_node="gate",
        source_node="client",
        reply_to="client",
    )
    if algorithm == "ed25519":
        private_key = Ed25519PrivateKey.generate()
        raw_private = private_key.private_bytes_raw()
        raw_public = private_key.public_key().public_bytes_raw()
        signed = sign_transport_packet(
            packet, key=raw_private, key_id="ed25519-key-1", algorithm="ed25519"
        )
        return signed, raw_public
    signed = sign_transport_packet(
        packet, key="super-secret", key_id="hmac-key-1", algorithm="hmac-sha256"
    )
    return signed, "super-secret"


def test_verify_transport_packet_signature_returns_true_for_valid_ed25519_signature() -> None:
    signed, public_key = _signed_packet_and_key("ed25519")

    assert (
        verify_transport_packet_signature(
            signed,
            key_resolver={"ed25519-key-1": public_key},
        )
        is True
    )


def test_verify_transport_packet_signature_raises_for_tampered_ed25519_signature() -> None:
    signed, public_key = _signed_packet_and_key("ed25519")
    tampered = signed.model_copy(
        update={
            "security": signed.security.model_copy(
                update={"signature": "00" * 64},
            )
        }
    )

    with pytest.raises(TransportAuthenticationError):
        verify_transport_packet_signature(
            tampered,
            key_resolver={"ed25519-key-1": public_key},
        )


def test_verify_transport_packet_signature_raises_when_no_key_available() -> None:
    signed, _public_key = _signed_packet_and_key("hmac-sha256")

    with pytest.raises(TransportAuthenticationError):
        verify_transport_packet_signature(signed, key_resolver={})


def test_verify_transport_packet_signature_raises_for_unsupported_algorithm() -> None:
    signed, _key = _signed_packet_and_key("hmac-sha256")
    # model_copy(update=...) bypasses field validation, letting us simulate an
    # unsupported algorithm value arriving over the wire (e.g. from a future SDK version).
    unsupported = signed.model_copy(
        update={
            "security": signed.security.model_copy(
                update={"signature_algorithm": "rsa-pss"},
            )
        }
    )

    with pytest.raises(TransportAuthenticationError, match="unsupported signature algorithm"):
        verify_transport_packet_signature(unsupported, key_resolver={"hmac-key-1": "super-secret"})


def test_verify_transport_packet_signature_accepts_callable_key_resolver() -> None:
    signed, key = _signed_packet_and_key("hmac-sha256")

    def resolver(key_id: str | None) -> str | None:
        assert key_id == "hmac-key-1"
        return key

    assert verify_transport_packet_signature(signed, key_resolver=resolver) is True


def test_verify_transport_packet_signature_accepts_direct_string_key_resolver() -> None:
    signed, key = _signed_packet_and_key("hmac-sha256")

    assert verify_transport_packet_signature(signed, key_resolver=key) is True
