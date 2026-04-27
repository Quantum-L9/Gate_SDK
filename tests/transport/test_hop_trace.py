from __future__ import annotations

import pytest

from constellation_node_sdk.transport.errors import TransportIntegrityError
from constellation_node_sdk.transport.hop_trace import (
    compute_hop_hash,
    last_hop_hash,
    make_dispatch_hop,
    make_execution_hop,
    make_ingress_hop,
    make_response_hop,
    validate_hop_trace,
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

    with pytest.raises(TransportIntegrityError):
        validate_hop_trace(tampered_packet)
