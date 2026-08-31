"""
Objective I — the canonical proof surface for the coordinated node rail.

One test walks the whole production shape:

    application node
      -> create_transport_packet(action="converge")
      -> serialize / parse             (node -> Gate HTTP hop)
      -> Gate-style ingress hop + derive worker packet
      -> serialize / parse             (Gate -> worker HTTP hop)
      -> SDK worker runtime handler
      -> response packet
      -> serialize / parse             (worker -> Gate -> node HTTP hops)

The Gate-side steps are modelled here with the SDK's own primitives rather than
imported from Constellation.Gate: this file must prove the SDK's transport
guarantees without taking a dependency on any application or routing repository.
The payload shapes are illustrative transport data only. Nothing in this test
gives the SDK knowledge of what they mean.
"""

from __future__ import annotations

import copy

import pytest

from constellation_node_sdk.runtime.execution import execute_transport_packet
from constellation_node_sdk.runtime.handlers import register_handler
from constellation_node_sdk.transport.codec import (
    decode_transport_packet,
    encode_transport_packet,
)
from constellation_node_sdk.transport.hop_trace import make_ingress_hop
from constellation_node_sdk.transport.packet import TransportPacket, create_transport_packet
from constellation_node_sdk.transport.provenance import RoutingProvenance
from constellation_node_sdk.transport.tenant import TenantContext

APPLICATION_IDEMPOTENCY_KEY = "odoo:enrichment:123"
APPLICATION_TIMEOUT_MS = 30_000

# Keys no transport layer may invent on the application's behalf.
FORBIDDEN_MANUFACTURED_KEYS = (
    "entity_snapshot",
    "entity_id",
    "final_fields",
    "writeback",
)


def _over_the_wire(packet: TransportPacket) -> TransportPacket:
    """Serialize and parse, exactly as a real HTTP hop would."""
    return decode_transport_packet(encode_transport_packet(packet))


def _gate_derives_worker_packet(packet: TransportPacket, *, target: str) -> TransportPacket:
    """
    Model the routing authority's derivation without importing it.

    Gate observes ingress as a hop, then derives a worker-targeted child that
    carries gate-resolved provenance. The payload is forwarded untouched.
    """
    observed = packet.with_hop(
        make_ingress_hop(
            packet=packet,
            node="gate",
            action=packet.header.action,
            status="validated",
        )
    )
    return observed.derive(
        source_node="gate",
        destination_node=target,
        reply_to="gate",
        payload=dict(observed.payload),
        provenance=RoutingProvenance(
            origin_kind="gate",
            requested_action=observed.header.action,
            resolved_by_gate=True,
            original_source_node=observed.address.source_node,
        ),
    )


@pytest.mark.asyncio
async def test_domain_payload_survives_the_full_node_rail_unchanged(
    tenant: TenantContext,
    domain_payload: dict,
    domain_response_payload: dict,
) -> None:
    request_domain_payload = copy.deepcopy(domain_payload)
    response_domain_payload = copy.deepcopy(domain_response_payload)

    handler_saw: dict = {}

    @register_handler("converge")
    async def handle_converge(_org_id: str, payload: dict) -> dict:
        handler_saw["payload"] = copy.deepcopy(payload)
        return copy.deepcopy(response_domain_payload)

    # --- application node mints the request -------------------------------
    request = create_transport_packet(
        action="converge",
        payload=domain_payload,
        tenant=tenant,
        source_node="odoo",
        destination_node="gate",
        reply_to="odoo",
        timeout_ms=APPLICATION_TIMEOUT_MS,
        idempotency_key=APPLICATION_IDEMPOTENCY_KEY,
        provenance=RoutingProvenance(
            origin_kind="node",
            requested_action="converge",
            resolved_by_gate=False,
            original_source_node="odoo",
        ),
    )
    correlation_id = request.header.correlation_id

    # --- node -> Gate, then Gate derives the worker packet -----------------
    at_gate = _over_the_wire(request)
    worker_packet = _gate_derives_worker_packet(at_gate, target="eie")
    at_worker = _over_the_wire(worker_packet)

    # The worker sees the caller's payload, the caller's action, the caller's
    # transport budget, and the caller's idempotency key.
    assert at_worker.header.action == "converge"
    assert at_worker.payload == request_domain_payload
    assert at_worker.header.idempotency_key == APPLICATION_IDEMPOTENCY_KEY
    assert at_worker.header.timeout_ms == APPLICATION_TIMEOUT_MS
    assert at_worker.header.correlation_id == correlation_id
    assert at_worker.header.causation_id == at_gate.header.packet_id
    assert at_worker.lineage.parent_id == at_gate.header.packet_id
    assert at_worker.lineage.root_id == request.lineage.root_id

    # --- worker runtime executes the handler -------------------------------
    response = await execute_transport_packet(
        at_worker,
        node_name="eie",
        allowed_actions=("converge",),
        allowed_packet_types=("request",),
        max_attachments=0,
        max_attachment_size_bytes=0,
        allowed_attachment_schemes=(),
        dev_mode=True,
    )
    at_caller = _over_the_wire(response)

    # Identity stayed inside the payload; the SDK never lifted it into a header
    # or restated it as a top-level transport field.
    assert handler_saw["payload"] == request_domain_payload
    assert handler_saw["payload"]["entity"]["id"] == "res.partner:55"

    # The application's response crosses back verbatim.
    assert at_caller.payload == response_domain_payload
    assert at_caller.payload["state"] == "completed"
    assert at_caller.payload["fields"]["website"] == "https://example.com"
    assert [key for key in FORBIDDEN_MANUFACTURED_KEYS if key in at_caller.payload] == []

    # Transport identity is intact end to end.
    assert at_caller.header.packet_type == "response"
    assert at_caller.header.correlation_id == correlation_id
    assert at_caller.header.causation_id == at_worker.header.packet_id
    assert at_caller.lineage.parent_id == at_worker.header.packet_id
    assert at_caller.lineage.root_id == request.lineage.root_id
    assert at_caller.header.idempotency_key == APPLICATION_IDEMPOTENCY_KEY
    assert at_caller.header.timeout_ms == APPLICATION_TIMEOUT_MS
    assert at_caller.address.destination_node == "gate"
    assert at_caller.tenant == request.tenant

    # The caller's original dict was never mutated in place.
    assert domain_payload == request_domain_payload


@pytest.mark.asyncio
async def test_rail_carries_a_structurally_unrelated_payload_identically(
    tenant: TenantContext,
) -> None:
    """
    Neutrality, proved on a shape that resembles no enrichment contract at all.

    If the rail above passed only because its keys happen to be familiar, this
    test would not.
    """
    request_payload = {"sql": "select 1", "params": [1, None, "x"], "flags": {"dry_run": True}}
    response_payload = {"rows": [[1]], "truncated": False, "cursor": None}

    @register_handler("query")
    async def handle_query(_org_id: str, payload: dict) -> dict:
        assert payload == request_payload
        return copy.deepcopy(response_payload)

    request = create_transport_packet(
        action="query",
        payload=copy.deepcopy(request_payload),
        tenant=tenant,
        source_node="odoo",
        destination_node="gate",
        reply_to="odoo",
        idempotency_key="odoo:query:9",
    )
    worker_packet = _gate_derives_worker_packet(_over_the_wire(request), target="warehouse")

    response = await execute_transport_packet(
        _over_the_wire(worker_packet),
        node_name="warehouse",
        allowed_actions=("query",),
        allowed_packet_types=("request",),
        max_attachments=0,
        max_attachment_size_bytes=0,
        allowed_attachment_schemes=(),
        dev_mode=True,
    )

    assert _over_the_wire(response).payload == response_payload
    assert response.header.idempotency_key == "odoo:query:9"
    assert response.header.correlation_id == request.header.correlation_id
