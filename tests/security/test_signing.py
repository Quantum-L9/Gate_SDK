from __future__ import annotations

from constellation_node_sdk.security.signing import recompute_transport_core, sign_transport_packet
from constellation_node_sdk.transport.packet import create_transport_packet


def test_recompute_transport_core_clears_signature_and_preserves_hash_domain() -> None:
    packet = create_transport_packet(
        action="score",
        payload={"entity_id": "42"},
        tenant="tenant-a",
        destination_node="gate",
        source_node="client",
        reply_to="client",
    )

    recomputed = recompute_transport_core(packet)

    assert recomputed.security.payload_hash == packet.security.payload_hash
    assert recomputed.security.transport_hash == packet.security.transport_hash
    assert recomputed.security.signature is None


def test_sign_transport_packet_with_hmac_signs_transport_hash() -> None:
    packet = create_transport_packet(
        action="score",
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

    assert signed.security.signature is not None
    assert signed.security.signature_algorithm == "hmac-sha256"
    assert signed.security.signing_key_id == "hmac-key-1"
