from __future__ import annotations

from typing import Any

from constellation_node_sdk.transport.packet import TransportPacket
from constellation_node_sdk.transport.provenance import RoutingProvenance


def build_step_packet(
    *,
    parent: TransportPacket,
    action: str,
    payload: dict[str, Any],
    source_node: str,
    reply_to: str,
) -> TransportPacket:
    """
    Build a child step packet for orchestrator -> Gate execution.

    This function enforces the architectural constraint that every step packet
    emitted by an orchestrator targets Gate, never a peer node.
    """
    normalized_action = action.strip().lower()
    if not normalized_action:
        raise ValueError("action must not be empty")

    normalized_source = source_node.strip().lower()
    normalized_reply_to = reply_to.strip().lower()
    if not normalized_source:
        raise ValueError("source_node must not be empty")
    if not normalized_reply_to:
        raise ValueError("reply_to must not be empty")

    provenance = RoutingProvenance(
        origin_kind="node",
        requested_action=normalized_action,
        resolved_by_gate=False,
        original_source_node=normalized_source,
    )

    return parent.derive(
        packet_type="request",
        action=normalized_action,
        source_node=normalized_source,
        destination_node="gate",
        reply_to=normalized_reply_to,
        payload=dict(payload),
        provenance=provenance,
    )
