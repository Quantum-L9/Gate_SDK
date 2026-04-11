from __future__ import annotations

from typing import Any

from constellation_node_sdk.transport.packet import TransportPacket


def encode_transport_packet(packet: TransportPacket) -> dict[str, Any]:
    """
    Encode a TransportPacket into a JSON-safe dict.
    """
    return packet.model_dump_json_dict()


def decode_transport_packet(payload: dict[str, Any]) -> TransportPacket:
    """
    Decode a JSON object into a canonical TransportPacket.
    """
    if not isinstance(payload, dict):
        raise ValueError("transport payload must be a JSON object")
    return TransportPacket.model_validate(payload)
