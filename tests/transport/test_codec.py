from __future__ import annotations

import pytest

from constellation_node_sdk.transport.codec import decode_transport_packet, encode_transport_packet
from constellation_node_sdk.transport.packet import create_transport_packet


def test_encode_decode_transport_packet_round_trip() -> None:
    packet = create_transport_packet(
        action="enrich",
        payload={"entity_id": "42"},
        tenant="tenant-a",
        destination_node="gate",
        source_node="client",
        reply_to="client",
    )

    encoded = encode_transport_packet(packet)
    decoded = decode_transport_packet(encoded)

    assert decoded.header.packet_id == packet.header.packet_id
    assert decoded.security.transport_hash == packet.security.transport_hash
    assert decoded.payload == packet.payload


def test_decode_transport_packet_rejects_non_object() -> None:
    with pytest.raises(ValueError):
        decode_transport_packet(["not", "an", "object"])  # type: ignore[arg-type]
