from __future__ import annotations

from datetime import timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from constellation_node_sdk.transport.errors import (
    TransportAuthenticationError,
    TransportIntegrityError,
    TransportValidationError,
)
from constellation_node_sdk.transport.hop_trace import (
    compute_hop_hash,
    last_hop_hash,
    make_dispatch_hop,
    make_execution_hop,
    make_ingress_hop,
    make_response_hop,
    sign_hop,
    validate_hop_trace,
    verify_hop_signature,
)
from constellation_node_sdk.transport.packet import create_transport_packet


def test_make_ingress_hop_appends_hash_chained_hop() -> None:
    packet = create_transport_packet(
        action="enrich",
        payload={"entity_id": "42"},
        tenant="tenant-a",
        destination_node="gate",
        source_node="client",
        reply_to="client",
    )

    hop = make_ingress_hop(
        packet=packet,
        node="gate",
        action="enrich",
        status="validated",
    )
    hopped = packet.with_hop(hop)

    assert hop.packet_id == packet.header.packet_id
    assert hop.direction == "ingress"
    assert hop.status == "validated"
    assert hop.previous_hop_hash is None
    assert hop.hop_hash == compute_hop_hash(
        transport_hash=packet.security.transport_hash,
        hop=hop,
    )
    assert last_hop_hash(hopped) == hop.hop_hash

    validate_hop_trace(hopped)


def test_hop_chain_links_previous_hashes() -> None:
    packet = create_transport_packet(
        action="score",
        payload={"entity_id": "42"},
        tenant="tenant-a",
        destination_node="gate",
        source_node="orchestrator",
        reply_to="orchestrator",
    )

    ingress = make_ingress_hop(
        packet=packet,
        node="gate",
        action="score",
        status="validated",
    )
    packet = packet.with_hop(ingress)

    dispatch = make_dispatch_hop(
        packet=packet,
        node="gate",
        action="score",
        target_node="score",
        status="delegated",
    )
    packet = packet.with_hop(dispatch)

    execution = make_execution_hop(
        packet=packet,
        node="score",
        action="score",
        status="processing",
    )
    packet = packet.with_hop(execution)

    response = make_response_hop(
        packet=packet,
        node="score",
        action="score",
        status="completed",
    )
    packet = packet.with_hop(response)

    assert packet.hop_trace[0].previous_hop_hash is None
    assert packet.hop_trace[1].previous_hop_hash == packet.hop_trace[0].hop_hash
    assert packet.hop_trace[2].previous_hop_hash == packet.hop_trace[1].hop_hash
    assert packet.hop_trace[3].previous_hop_hash == packet.hop_trace[2].hop_hash

    validate_hop_trace(packet)


def test_validate_hop_trace_detects_tampering() -> None:
    packet = create_transport_packet(
        action="enrich",
        payload={"entity_id": "42"},
        tenant="tenant-a",
        destination_node="gate",
        source_node="client",
        reply_to="client",
    )
    hop = make_ingress_hop(
        packet=packet,
        node="gate",
        action="enrich",
        status="validated",
    )
    packet = packet.with_hop(hop)

    tampered_hop = packet.hop_trace[0].model_copy(update={"status": "failed"})
    tampered_packet = packet.model_copy(update={"hop_trace": (tampered_hop,)})

    with pytest.raises((ValueError, Exception)):
        validate_hop_trace(tampered_packet)


def _base_packet():
    return create_transport_packet(
        action="enrich",
        payload={"entity_id": "42"},
        tenant="tenant-a",
        destination_node="gate",
        source_node="client",
        reply_to="client",
    )


def test_sign_hop_with_hmac_sets_signature_fields() -> None:
    packet = _base_packet()
    hop = make_ingress_hop(packet=packet, node="gate", action="enrich")

    signed = sign_hop(hop, key="super-secret", key_id="hmac-key-1", algorithm="HMAC-SHA256")

    assert signed.hop_signature is not None
    assert signed.hop_signature_algorithm == "hmac-sha256"
    assert signed.hop_signing_key_id == "hmac-key-1"


def test_sign_hop_with_unsupported_algorithm_raises() -> None:
    packet = _base_packet()
    hop = make_ingress_hop(packet=packet, node="gate", action="enrich")

    with pytest.raises(TransportAuthenticationError, match="unsupported hop signature algorithm"):
        sign_hop(hop, key="super-secret", key_id="key-1", algorithm="rsa-pss")


def test_make_ingress_hop_can_sign_directly_with_hmac() -> None:
    packet = _base_packet()

    hop = make_ingress_hop(
        packet=packet,
        node="gate",
        action="enrich",
        key="super-secret",
        key_id="hmac-key-1",
        algorithm="hmac-sha256",
    )

    assert hop.hop_signature is not None
    assert verify_hop_signature(hop, key_resolver={"hmac-key-1": "super-secret"}) is True


def test_verify_hop_signature_returns_false_for_unsigned_hop() -> None:
    packet = _base_packet()
    hop = make_ingress_hop(packet=packet, node="gate", action="enrich")

    assert verify_hop_signature(hop, key_resolver={"hmac-key-1": "super-secret"}) is False


def test_verify_hop_signature_raises_when_no_key_available() -> None:
    packet = _base_packet()
    hop = make_ingress_hop(
        packet=packet,
        node="gate",
        action="enrich",
        key="super-secret",
        key_id="hmac-key-1",
        algorithm="hmac-sha256",
    )

    with pytest.raises(TransportAuthenticationError):
        verify_hop_signature(hop, key_resolver={})


def test_verify_hop_signature_raises_for_unsupported_algorithm() -> None:
    packet = _base_packet()
    hop = make_ingress_hop(
        packet=packet,
        node="gate",
        action="enrich",
        key="super-secret",
        key_id="hmac-key-1",
        algorithm="hmac-sha256",
    )
    unsupported = hop.model_copy(update={"hop_signature_algorithm": "rsa-pss"})

    with pytest.raises(TransportAuthenticationError, match="unsupported hop signature algorithm"):
        verify_hop_signature(unsupported, key_resolver={"hmac-key-1": "super-secret"})


def test_hop_signing_and_verification_roundtrip_with_ed25519() -> None:
    packet = _base_packet()
    private_key = Ed25519PrivateKey.generate()
    raw_private = private_key.private_bytes_raw()
    raw_public = private_key.public_key().public_bytes_raw()

    hop = make_ingress_hop(
        packet=packet,
        node="gate",
        action="enrich",
        key=raw_private,
        key_id="ed25519-key-1",
        algorithm="ed25519",
    )

    assert hop.hop_signature_algorithm == "ed25519"
    assert verify_hop_signature(hop, key_resolver={"ed25519-key-1": raw_public}) is True


def test_verify_hop_signature_raises_for_tampered_ed25519_signature() -> None:
    packet = _base_packet()
    private_key = Ed25519PrivateKey.generate()
    raw_private = private_key.private_bytes_raw()
    raw_public = private_key.public_key().public_bytes_raw()

    hop = make_ingress_hop(
        packet=packet,
        node="gate",
        action="enrich",
        key=raw_private,
        key_id="ed25519-key-1",
        algorithm="ed25519",
    )
    tampered = hop.model_copy(update={"hop_signature": "00" * 64})

    with pytest.raises(TransportAuthenticationError):
        verify_hop_signature(tampered, key_resolver={"ed25519-key-1": raw_public})


def test_validate_hop_trace_rejects_first_hop_with_previous_hash_set() -> None:
    packet = _base_packet()
    hop = make_ingress_hop(packet=packet, node="gate", action="enrich")
    bad_first_hop = hop.model_copy(update={"previous_hop_hash": "0" * 64})
    tampered_packet = packet.model_copy(update={"hop_trace": (bad_first_hop,)})

    with pytest.raises(TransportIntegrityError, match="first hop must set previous_hop_hash"):
        validate_hop_trace(tampered_packet)


def test_validate_hop_trace_rejects_hop_with_mismatched_packet_id() -> None:
    packet = _base_packet()
    other_packet = _base_packet()
    foreign_hop = make_ingress_hop(packet=other_packet, node="gate", action="enrich")
    tampered_packet = packet.model_copy(update={"hop_trace": (foreign_hop,)})

    with pytest.raises(TransportValidationError, match="hop packet_id does not match"):
        validate_hop_trace(tampered_packet)


def test_validate_hop_trace_rejects_non_monotonic_timestamps() -> None:
    packet = _base_packet()
    first = make_ingress_hop(packet=packet, node="gate", action="enrich")
    packet = packet.with_hop(first)

    second = make_dispatch_hop(packet=packet, node="gate", action="enrich", target_node="worker")
    earlier_timestamp = first.timestamp - timedelta(seconds=5)
    backdated = second.model_copy(update={"timestamp": earlier_timestamp})
    # Recompute hop_hash so only the timestamp-ordering check fails, not hash integrity.
    recomputed_hash = compute_hop_hash(transport_hash=packet.security.transport_hash, hop=backdated)
    backdated = backdated.model_copy(update={"hop_hash": recomputed_hash})
    tampered_packet = packet.model_copy(update={"hop_trace": packet.hop_trace + (backdated,)})

    with pytest.raises(TransportValidationError, match="non-decreasing"):
        validate_hop_trace(tampered_packet)


def test_validate_hop_trace_detects_broken_hash_chain_continuity() -> None:
    packet = _base_packet()
    first = make_ingress_hop(packet=packet, node="gate", action="enrich")
    packet = packet.with_hop(first)
    second = make_dispatch_hop(packet=packet, node="gate", action="enrich", target_node="worker")
    packet = packet.with_hop(second)

    broken_second = second.model_copy(update={"previous_hop_hash": "1" * 64})
    tampered_packet = packet.model_copy(update={"hop_trace": (packet.hop_trace[0], broken_second)})

    with pytest.raises(TransportIntegrityError, match="hop chain continuity violation"):
        validate_hop_trace(tampered_packet)


def test_validate_hop_trace_with_signature_verification_enabled() -> None:
    packet = _base_packet()
    hop = make_ingress_hop(
        packet=packet,
        node="gate",
        action="enrich",
        key="super-secret",
        key_id="hmac-key-1",
        algorithm="hmac-sha256",
    )
    packet = packet.with_hop(hop)

    validate_hop_trace(
        packet,
        verify_hop_signatures=True,
        hop_key_resolver={"hmac-key-1": "super-secret"},
    )


def test_validate_hop_trace_with_signature_verification_enabled_rejects_bad_signature() -> None:
    packet = _base_packet()
    hop = make_ingress_hop(
        packet=packet,
        node="gate",
        action="enrich",
        key="super-secret",
        key_id="hmac-key-1",
        algorithm="hmac-sha256",
    )
    packet = packet.with_hop(hop)

    with pytest.raises(TransportAuthenticationError, match="invalid hop signature"):
        validate_hop_trace(
            packet,
            verify_hop_signatures=True,
            hop_key_resolver={"hmac-key-1": "wrong-secret"},
        )
