from __future__ import annotations

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

    assert verify_transport_packet_signature(
        signed,
        key_resolver={"hmac-key-1": "super-secret"},
    ) is True


def test_verify_transport_packet_signature_returns_false_for_unsigned_packet() -> None:
    packet = create_transport_packet(
        action="enrich",
        payload={"entity_id": "42"},
        tenant="tenant-a",
        destination_node="gate",
        source_node="client",
        reply_to="client",
    )

    assert verify_transport_packet_signature(
        packet,
        key_resolver={"hmac-key-1": "super-secret"},
    ) is False
