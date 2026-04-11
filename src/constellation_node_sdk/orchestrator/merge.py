from __future__ import annotations

from typing import Any

from constellation_node_sdk.transport.packet import TransportPacket


def _payload_copy(packet: TransportPacket) -> dict[str, Any]:
    return dict(packet.payload)


def _response_payload(response: TransportPacket) -> dict[str, Any]:
    return dict(response.payload)


def merge_identity(current: TransportPacket, response: TransportPacket) -> dict[str, Any]:
    """
    Replace accumulated payload with the full response payload.
    """
    del current
    return _response_payload(response)


def merge_results(current: TransportPacket, response: TransportPacket) -> dict[str, Any]:
    """
    Merge response.payload["data"] into current payload when present.
    Fall back to merging the full response payload.
    """
    merged = _payload_copy(current)
    response_payload = _response_payload(response)

    response_data = response_payload.get("data")
    if isinstance(response_data, dict):
        merged.update(response_data)
        return merged

    merged.update(response_payload)
    return merged


def merge_payload(current: TransportPacket, response: TransportPacket) -> dict[str, Any]:
    """
    Merge the full response payload into current payload.
    """
    merged = _payload_copy(current)
    merged.update(_response_payload(response))
    return merged
