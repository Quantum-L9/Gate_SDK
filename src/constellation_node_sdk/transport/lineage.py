from __future__ import annotations

from constellation_node_sdk.transport.errors import TransportValidationError
from constellation_node_sdk.transport.models import TransportLineage
from constellation_node_sdk.transport.packet import TransportPacket


def derive_lineage(parent: TransportPacket) -> TransportLineage:
    """
    Derive lineage for a semantic child packet.
    """
    return TransportLineage(
        parent_id=parent.header.packet_id,
        root_id=parent.lineage.root_id,
        generation=parent.lineage.generation + 1,
    )


def validate_parent_child_lineage(parent: TransportPacket, child: TransportPacket) -> None:
    """
    Validate that a child packet was correctly derived from a parent packet.
    """
    if child.lineage.parent_id != parent.header.packet_id:
        raise TransportValidationError("child parent_id does not match parent packet_id")
    if child.lineage.root_id != parent.lineage.root_id:
        raise TransportValidationError("child root_id does not match parent root_id")
    if child.lineage.generation != parent.lineage.generation + 1:
        raise TransportValidationError("child generation must increment by 1")
