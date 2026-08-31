"""
SDK7 / SDK9 / SDK10 — correlation, causation, and lineage across the rail.

Four-repo traceability during the production canary depends on a single
correlation_id spanning one logical request/response, with each generation
naming its immediate parent through causation_id and lineage.parent_id.
"""

from __future__ import annotations

from constellation_node_sdk.transport.codec import (
    decode_transport_packet,
    encode_transport_packet,
)
from constellation_node_sdk.transport.packet import create_transport_packet
from constellation_node_sdk.transport.tenant import TenantContext


def test_root_packet_seeds_correlation_and_has_no_cause(
    tenant: TenantContext,
    domain_payload: dict,
) -> None:
    root = create_transport_packet(
        action="converge",
        payload=domain_payload,
        tenant=tenant,
    )

    assert root.header.correlation_id == str(root.header.packet_id)
    assert root.header.causation_id is None
    assert root.lineage.parent_id is None
    assert root.lineage.root_id == root.header.packet_id
    assert root.lineage.generation == 0


def test_explicit_correlation_id_is_carried_unchanged(
    tenant: TenantContext,
    domain_payload: dict,
) -> None:
    root = create_transport_packet(
        action="converge",
        payload=domain_payload,
        tenant=tenant,
        correlation_id="odoo-request-7788",
    )

    assert root.header.correlation_id == "odoo-request-7788"
    assert root.derive().header.correlation_id == "odoo-request-7788"


def test_three_generation_chain_keeps_one_correlation_and_walks_causation(
    tenant: TenantContext,
    domain_payload: dict,
) -> None:
    """
    P1 (odoo -> gate) -> P2 (gate -> eie) -> P3 (eie -> gate response).

    correlation_id is constant; causation_id and lineage.parent_id step forward
    one generation at a time; root_id never moves.
    """
    p1 = create_transport_packet(
        action="converge",
        payload=domain_payload,
        tenant=tenant,
        source_node="odoo",
        destination_node="gate",
        reply_to="odoo",
    )
    correlation = p1.header.correlation_id

    p2 = p1.derive(source_node="gate", destination_node="eie", reply_to="gate")
    p3 = p2.derive(
        packet_type="response",
        source_node="eie",
        destination_node="gate",
        reply_to="eie",
        payload={"state": "completed"},
    )

    assert p2.header.correlation_id == correlation
    assert p3.header.correlation_id == correlation

    assert p2.header.causation_id == p1.header.packet_id
    assert p3.header.causation_id == p2.header.packet_id

    assert p2.lineage.parent_id == p1.header.packet_id
    assert p3.lineage.parent_id == p2.header.packet_id

    assert p1.lineage.root_id == p2.lineage.root_id == p3.lineage.root_id
    assert (p1.lineage.generation, p2.lineage.generation, p3.lineage.generation) == (0, 1, 2)

    packet_ids = {p1.header.packet_id, p2.header.packet_id, p3.header.packet_id}
    assert len(packet_ids) == 3


def test_correlation_and_causation_survive_the_serialization_boundary(
    tenant: TenantContext,
    domain_payload: dict,
) -> None:
    """Every generation crosses a real HTTP hop, so prove it through the codec."""
    p1 = create_transport_packet(
        action="converge",
        payload=domain_payload,
        tenant=tenant,
        source_node="odoo",
    )
    at_gate = decode_transport_packet(encode_transport_packet(p1))
    p2 = at_gate.derive(source_node="gate", destination_node="eie", reply_to="gate")
    at_worker = decode_transport_packet(encode_transport_packet(p2))

    assert at_worker.header.correlation_id == p1.header.correlation_id
    assert at_worker.header.causation_id == p1.header.packet_id
    assert at_worker.lineage.parent_id == p1.header.packet_id
    assert at_worker.lineage.root_id == p1.header.packet_id


def test_trace_id_is_carried_unchanged_across_generations(
    tenant: TenantContext,
    domain_payload: dict,
) -> None:
    root = create_transport_packet(
        action="converge",
        payload=domain_payload,
        tenant=tenant,
        trace_id="trace-abc-123",
    )

    assert root.derive().derive().header.trace_id == "trace-abc-123"
