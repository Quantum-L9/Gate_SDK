"""
Track B — one specimen that proves most of the transport contract at once.

``test_canonical_rail_conformance.py`` walks the rail in process. This file
walks it through ``GateClient``, which is the boundary Odoo actually uses:
the request is serialized to JSON bytes by the SDK's own client, the Gate
and worker legs run against those bytes, and the response is parsed back by
the SDK's own client. Everything asserted here therefore survived four
generations and three serialization boundaries.

Gate's behavior is modelled with the SDK's own primitives rather than
imported. No application or routing repository is a dependency of this test.
The payload shapes are illustrative transport data; nothing here gives the
SDK knowledge of what they mean.
"""

from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any

import httpx
import pytest

from constellation_node_sdk.gate.client import GateClient
from constellation_node_sdk.gate.config import GateClientConfig
from constellation_node_sdk.runtime.execution import execute_transport_packet
from constellation_node_sdk.runtime.handlers import clear_handlers, register_handler
from constellation_node_sdk.transport.codec import (
    decode_transport_packet,
    encode_transport_packet,
)
from constellation_node_sdk.transport.hop_trace import make_ingress_hop
from constellation_node_sdk.transport.packet import TransportPacket, create_transport_packet
from constellation_node_sdk.transport.provenance import RoutingProvenance
from constellation_node_sdk.transport.tenant import TenantContext

# Stable across the whole specimen: one logical request keeps one of each.
CORRELATION_ID = "odoo:enrichment:corr-9f31"
IDEMPOTENCY_KEY = "odoo:enrichment:res.partner:55"
TIMEOUT_MS = 45_000

REQUEST_PAYLOAD: dict[str, Any] = {
    "entity": {
        "id": "res.partner:55",
        "_odoo_entity_id": "res.partner:55",
        "name": "Acme",
    },
    "object_type": "organization",
    "objective": "enrich",
    "max_variations": 5,
}

RESPONSE_PAYLOAD: dict[str, Any] = {
    "state": "completed",
    "fields": {"website": "https://example.com"},
}

# Vocabulary the SDK must never introduce on the application's behalf. The
# application owns the names on both sides of the rail; transport carries
# whichever it was handed.
FORBIDDEN_MANUFACTURED_KEYS = (
    "entity_snapshot",
    "entity_id",
    "final_fields",
    "writeback",
)


@dataclass
class Rail:
    """Every packet the specimen produced, in the order the rail produced it."""

    root: TransportPacket
    worker_request: TransportPacket
    worker_response: TransportPacket
    client_response: TransportPacket
    handler_saw: dict[str, Any]
    request_bytes: bytes
    generations: list[tuple[str, TransportPacket]] = field(default_factory=list)


def _over_the_wire(packet: TransportPacket) -> TransportPacket:
    """Serialize and parse through real JSON text, exactly as an HTTP hop does."""
    return decode_transport_packet(json.loads(json.dumps(encode_transport_packet(packet))))


def _tenant() -> TenantContext:
    return TenantContext(
        actor="test-actor",
        on_behalf_of="test-actor",
        originator="test-actor",
        org_id="test-org",
        user_id="test-user",
    )


async def _run_rail(route_gate_http: Any) -> Rail:
    """Drive one logical request from the application node and back."""
    handler_saw: dict[str, Any] = {}
    captured: dict[str, Any] = {}

    clear_handlers()

    @register_handler("converge")
    async def handle_converge(_org_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        handler_saw["payload"] = copy.deepcopy(payload)
        return copy.deepcopy(RESPONSE_PAYLOAD)

    async def gate(request: httpx.Request, _attempt: int) -> httpx.Response:
        """Model Gate: observe ingress, derive to the worker, return the response."""
        inbound = decode_transport_packet(json.loads(request.content))

        observed = inbound.with_hop(
            make_ingress_hop(
                packet=inbound,
                node="gate",
                action=inbound.header.action,
                status="validated",
            )
        )
        worker_request = _over_the_wire(
            observed.derive(
                source_node="gate",
                destination_node="worker",
                reply_to="gate",
                payload=dict(observed.payload),
                provenance=RoutingProvenance(
                    origin_kind="gate",
                    requested_action=observed.header.action,
                    resolved_by_gate=True,
                    original_source_node=observed.address.source_node,
                ),
            )
        )

        worker_response = _over_the_wire(
            await execute_transport_packet(worker_request, node_name="worker", dev_mode=True)
        )

        client_response = worker_response.derive(
            packet_type="response",
            source_node="gate",
            destination_node=inbound.address.reply_to,
            reply_to="gate",
            payload=dict(worker_response.payload),
            provenance=RoutingProvenance(
                origin_kind="gate",
                requested_action=inbound.header.action,
                resolved_by_gate=True,
                original_source_node=inbound.address.source_node,
            ),
        )

        captured["worker_request"] = worker_request
        captured["worker_response"] = worker_response
        return httpx.Response(200, json=encode_transport_packet(client_response))

    root = create_transport_packet(
        action="converge",
        payload=copy.deepcopy(REQUEST_PAYLOAD),
        tenant=_tenant(),
        source_node="odoo",
        destination_node="gate",
        reply_to="odoo",
        correlation_id=CORRELATION_ID,
        idempotency_key=IDEMPOTENCY_KEY,
        timeout_ms=TIMEOUT_MS,
        provenance=RoutingProvenance(
            origin_kind="node",
            requested_action="converge",
            resolved_by_gate=False,
            original_source_node="odoo",
        ),
    )

    client = GateClient(
        GateClientConfig(gate_url="http://gate:8000", local_node="odoo", timeout_seconds=5.0)
    )
    with route_gate_http(gate) as transport:
        client_response = await client.send_to_gate(root)
        request_bytes = bytes(transport.requests[0].content)

    clear_handlers()

    rail = Rail(
        root=root,
        worker_request=captured["worker_request"],
        worker_response=captured["worker_response"],
        client_response=client_response,
        handler_saw=handler_saw,
        request_bytes=request_bytes,
    )
    rail.generations = [
        ("root", rail.root),
        ("worker_request", rail.worker_request),
        ("worker_response", rail.worker_response),
        ("client_response", rail.client_response),
    ]
    return rail


@pytest.fixture(scope="module")
def rail(route_gate_http: Any) -> Rail:
    """
    Run the specimen once; every test below reads the same evidence.

    Built synchronously so the module-scoped result is independent of any
    per-test event loop.
    """
    return asyncio.run(_run_rail(route_gate_http))


# ---------------------------------------------------------------------------
# Root packet — what the application handed to transport
# ---------------------------------------------------------------------------


def test_root_packet_carries_the_application_transport_metadata(rail: Rail) -> None:
    """Action, payload, idempotency key, timeout, and correlation are stored verbatim."""
    header = rail.root.header

    assert header.action == "converge"
    assert rail.root.address.destination_node == "gate"
    assert rail.root.payload == REQUEST_PAYLOAD
    assert header.idempotency_key == IDEMPOTENCY_KEY
    assert header.timeout_ms == TIMEOUT_MS
    assert header.correlation_id == CORRELATION_ID
    assert header.causation_id is None
    assert rail.root.lineage.parent_id is None
    assert rail.root.lineage.root_id == header.packet_id
    assert rail.root.lineage.generation == 0


def test_the_bytes_on_the_wire_are_the_root_packets_own_serialization(rail: Rail) -> None:
    """GateClient adds nothing to and removes nothing from the canonical form."""
    sent = json.loads(rail.request_bytes)
    assert sent == encode_transport_packet(rail.root)


# ---------------------------------------------------------------------------
# Derived worker packet — what the routing authority produced
# ---------------------------------------------------------------------------


def test_derived_worker_packet_keeps_the_logical_request_identity(rail: Rail) -> None:
    """A new packet, still the same logical request."""
    root = rail.root.header
    worker = rail.worker_request.header

    assert worker.packet_id != root.packet_id
    assert worker.correlation_id == root.correlation_id
    assert worker.causation_id == root.packet_id
    assert worker.idempotency_key == IDEMPOTENCY_KEY
    assert worker.timeout_ms == TIMEOUT_MS, "timeout must survive derive without an override"

    assert rail.worker_request.lineage.parent_id == root.packet_id
    assert rail.worker_request.lineage.root_id == rail.root.lineage.root_id
    assert rail.worker_request.lineage.generation == 1

    assert rail.worker_request.payload == rail.root.payload
    assert rail.worker_request.address.destination_node == "worker"


# ---------------------------------------------------------------------------
# Worker — what the handler received and returned
# ---------------------------------------------------------------------------


def test_worker_handler_receives_the_exact_opaque_payload(rail: Rail) -> None:
    """
    The handler is handed the application's dict, key for key.

    Equality alone would pass if the SDK had added a key and the fixture had
    been updated to match, so the key set is asserted separately.
    """
    assert rail.handler_saw["payload"] == REQUEST_PAYLOAD
    assert set(rail.handler_saw["payload"]) == set(REQUEST_PAYLOAD)
    assert rail.handler_saw["payload"]["entity"] == REQUEST_PAYLOAD["entity"]


def test_worker_handler_is_selected_by_the_header_action(rail: Rail) -> None:
    """Dispatch followed ``header.action``, and the response kept that action."""
    assert rail.worker_request.header.action == "converge"
    assert rail.worker_response.header.action == "converge"


def test_response_payload_reflects_the_handler_response_exactly(rail: Rail) -> None:
    """``state`` stays ``state`` and ``fields`` stays ``fields``, all the way back."""
    assert rail.worker_response.payload == RESPONSE_PAYLOAD
    assert rail.client_response.payload == RESPONSE_PAYLOAD
    assert set(rail.client_response.payload) == {"state", "fields"}
    assert rail.client_response.payload["state"] == "completed"
    assert rail.client_response.payload["fields"] == {"website": "https://example.com"}


def test_response_is_a_canonical_packet_type(rail: Rail) -> None:
    """The application receives a response packet, not a bare application dict."""
    assert isinstance(rail.client_response, TransportPacket)
    assert rail.client_response.header.packet_type == "response"
    assert rail.client_response.address.destination_node == "odoo"


# ---------------------------------------------------------------------------
# Tracing — correlation, causation, lineage across every generation
# ---------------------------------------------------------------------------


def test_canonical_round_trip_preserves_lineage_and_causation(rail: Rail) -> None:
    """Each generation names its immediate parent; the root never moves."""
    root_id = rail.root.lineage.root_id

    for index, (label, packet) in enumerate(rail.generations):
        assert packet.lineage.root_id == root_id, f"{label} lost the root"
        assert packet.lineage.generation == index, f"{label} has the wrong generation"

    for (parent_label, parent), (child_label, child) in pairwise(rail.generations):
        assert child.header.causation_id == parent.header.packet_id, (
            f"{child_label} does not name {parent_label} as its cause"
        )
        assert child.lineage.parent_id == parent.header.packet_id


def test_every_generation_mints_a_new_packet_id_under_one_correlation(rail: Rail) -> None:
    """
    Replay and tracing depend on this: distinct packet ids, one correlation.

    P1 -> P2 -> P3 -> P4 each get a fresh identity while the whole chain
    remains one logical request.
    """
    packet_ids = [packet.header.packet_id for _, packet in rail.generations]
    assert len(set(packet_ids)) == len(packet_ids), "a generation reused a packet id"

    correlations = {packet.header.correlation_id for _, packet in rail.generations}
    assert correlations == {CORRELATION_ID}

    trace_ids = {packet.header.trace_id for _, packet in rail.generations}
    assert len(trace_ids) == 1, "trace_id drifted across the rail"


def test_idempotency_key_and_timeout_survive_every_generation(rail: Rail) -> None:
    """The two fields Gate caches and bounds execution on are never rewritten."""
    for label, packet in rail.generations:
        assert packet.header.idempotency_key == IDEMPOTENCY_KEY, f"{label} lost the key"
        assert packet.header.timeout_ms == TIMEOUT_MS, f"{label} rewrote the timeout"


def test_tenant_context_is_identical_at_every_generation(rail: Rail) -> None:
    """Tenant is immutable across derivation — asserted on the specimen, not in isolation."""
    for label, packet in rail.generations:
        assert packet.tenant == rail.root.tenant, f"{label} mutated the tenant"


# ---------------------------------------------------------------------------
# Neutrality — what the SDK did not do
# ---------------------------------------------------------------------------


def test_sdk_manufactures_no_domain_vocabulary_anywhere_on_the_rail(rail: Rail) -> None:
    """
    No packet on the rail acquired application vocabulary the caller never sent.

    This is the expensive failure to catch early: a transport layer that
    renames ``entity`` to ``entity_snapshot`` or ``fields`` to
    ``final_fields`` surfaces as a mismatched contract inside an application
    repository days after the change that caused it.
    """
    for label, packet in rail.generations:
        for forbidden in FORBIDDEN_MANUFACTURED_KEYS:
            assert forbidden not in packet.payload, (
                f"{label} payload gained a manufactured key: {forbidden}"
            )

    assert "status" not in rail.client_response.payload, (
        "the runtime reads payload['status'] for the observational hop; "
        "it must never write one back"
    )
    assert "entity_id" not in rail.client_response.payload
