"""
Executable release-set compatibility (ADR-SDK-010).

A Gate_SDK revision is not release-compatible merely because it installs. The
rail below is the one real traffic takes, and it is exercised end to end with
the SDK's own primitives standing in for each participant:

    application execute
      -> canonical root packet
      -> Gate ingress validation
      -> Gate observation hop
      -> Gate child derivation
      -> worker runtime validation + execution
      -> canonical response
      -> originating client validation

The domain payload here is copied from a real consumer and stays an opaque
dictionary throughout: the SDK must carry it, never interpret it.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from gate_client_helpers import RecordingTransport, make_client_config

from constellation_node_sdk.gate.client import GateClient
from constellation_node_sdk.runtime.execution import execute_transport_packet
from constellation_node_sdk.runtime.handlers import clear_handlers, register_handler
from constellation_node_sdk.security.validation import validate_transport_packet
from constellation_node_sdk.transport.hop_trace import make_dispatch_hop, make_ingress_hop
from constellation_node_sdk.transport.packet import TransportPacket
from constellation_node_sdk.transport.provenance import RoutingProvenance

# Copied verbatim from a consumer's converge payload. Opaque to the SDK.
CONSUMER_PAYLOAD: dict[str, Any] = {
    "entity": {
        "id": "org-4711",
        "name": "Acme Recycling",
        "domain": "acme.test",
        "fields": {"website": None, "phone": "+1-555-0100"},
    },
    "requested_fields": ["website", "linkedin_url", "employee_count"],
    "state": "pending",
    "run_id": "durable-run-4711",
}


class GateSimulator:
    """
    A Gate stand-in that behaves the way Constellation.Gate behaves on the wire.

    It validates ingress, appends an observation hop, derives a semantic child
    for the worker, dispatches it through the real worker runtime, and answers
    with a canonical response packet. Every step uses the SDK primitives Gate
    itself uses, so a drift in those semantics fails here.
    """

    def __init__(self, *, worker_node: str = "enrichment-engine") -> None:
        self.worker_node = worker_node
        self.ingress_packets: list[TransportPacket] = []
        self.worker_packets: list[TransportPacket] = []
        self.worker_responses: list[TransportPacket] = []

    async def __call__(self, request: httpx.Request, _attempt: int) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))

        # 1. Gate ingress validation of the producer's root packet.
        inbound = TransportPacket.model_validate(body)
        validate_transport_packet(inbound, dev_mode=True)
        self.ingress_packets.append(inbound)

        # 2. Gate observes ingress without disturbing transport identity.
        observed = inbound.with_hop(
            make_ingress_hop(packet=inbound, node="gate", action=inbound.header.action)
        )

        # 3. Gate derives a semantic child addressed to the resolved worker.
        dispatch_base = observed.derive(
            packet_type=observed.header.packet_type,
            action=observed.header.action,
            source_node="gate",
            destination_node=self.worker_node,
            reply_to="gate",
            payload=dict(observed.payload),
            provenance=RoutingProvenance(
                origin_kind="gate",
                requested_action=observed.header.action,
                resolved_by_gate=True,
                original_source_node=observed.address.source_node,
            ),
        )
        # The dispatch hop is keyed to the freshly derived packet's id: derive
        # mints a new packet_id, so a hop built from the pre-derive packet is
        # rejected. This mirrors Constellation.Gate's own dispatch sequence.
        worker_packet = dispatch_base.with_hop(
            make_dispatch_hop(
                packet=dispatch_base,
                node="gate",
                action=dispatch_base.header.action,
                target_node=self.worker_node,
                status="delegated",
            )
        )
        self.worker_packets.append(worker_packet)

        # 4. The real worker runtime validates and executes it.
        worker_response = await execute_transport_packet(
            worker_packet, node_name=self.worker_node, dev_mode=True
        )
        self.worker_responses.append(worker_response)

        # 5. Gate returns a response addressed back to the originator.
        gate_response = worker_response.derive(
            packet_type="response",
            source_node="gate",
            destination_node=inbound.address.reply_to,
            reply_to="gate",
        )
        return httpx.Response(200, json=gate_response.model_dump_json_dict())


@pytest.fixture()
def converge_worker() -> Any:
    clear_handlers()

    @register_handler("converge")
    async def handle(_org_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        # A worker owns its domain. It reads the payload it was given and
        # returns its own; the SDK is not involved in either shape.
        return {
            "state": "completed",
            "run_id": payload["run_id"],
            "fields": {"website": "https://acme.test", "employee_count": 240},
        }

    yield
    clear_handlers()


@pytest.mark.asyncio
async def test_full_release_set_round_trip(converge_worker: Any) -> None:
    """One application call survives the whole rail and comes back validated."""
    gate = GateSimulator()
    transport = RecordingTransport(gate)
    client = GateClient(make_client_config(local_node="odoo"), transport=transport)

    response = await client.execute(
        action="converge",
        payload=CONSUMER_PAYLOAD,
        tenant="tenant-a",
        idempotency_key="odoo:enrichment:durable-run-4711",
        timeout_ms=25_000,
        correlation_id="corr-4711",
    )

    assert response.payload["state"] == "completed"
    assert response.payload["run_id"] == "durable-run-4711"
    assert len(transport.requests) == 1


@pytest.mark.asyncio
async def test_the_domain_payload_crosses_every_hop_unchanged(converge_worker: Any) -> None:
    """
    ADR-SDK-009: the SDK transports domain payloads, it does not translate them.

    No key is renamed, supplemented, or coerced between the producer and the
    worker's own view of the request.
    """
    gate = GateSimulator()
    client = GateClient(make_client_config(local_node="odoo"), transport=RecordingTransport(gate))

    await client.execute(action="converge", payload=CONSUMER_PAYLOAD, tenant="tenant-a")

    assert gate.ingress_packets[0].payload == CONSUMER_PAYLOAD
    assert gate.worker_packets[0].payload == CONSUMER_PAYLOAD


@pytest.mark.asyncio
async def test_transport_metadata_survives_gate_derivation(converge_worker: Any) -> None:
    """Correlation, idempotency, tenant, and budget reach the worker intact."""
    gate = GateSimulator()
    client = GateClient(make_client_config(local_node="odoo"), transport=RecordingTransport(gate))

    await client.execute(
        action="converge",
        payload=CONSUMER_PAYLOAD,
        tenant="tenant-a",
        idempotency_key="odoo:enrichment:durable-run-4711",
        correlation_id="corr-4711",
        timeout_ms=25_000,
    )

    worker_packet = gate.worker_packets[0]
    assert worker_packet.header.idempotency_key == "odoo:enrichment:durable-run-4711"
    assert worker_packet.header.correlation_id == "corr-4711"
    assert worker_packet.header.timeout_ms == 25_000
    assert worker_packet.tenant == gate.ingress_packets[0].tenant


@pytest.mark.asyncio
async def test_gate_derivation_produces_correct_lineage(converge_worker: Any) -> None:
    """
    ADR-SDK-011: a child is a new packet with correct ancestry, not a copy.

    Causation points at the parent, the root is preserved, and the generation
    advances by exactly one per semantic change.
    """
    gate = GateSimulator()
    client = GateClient(make_client_config(local_node="odoo"), transport=RecordingTransport(gate))

    await client.execute(action="converge", payload=CONSUMER_PAYLOAD, tenant="tenant-a")

    root = gate.ingress_packets[0]
    child = gate.worker_packets[0]

    assert child.header.packet_id != root.header.packet_id
    assert child.header.causation_id == root.header.packet_id
    assert child.lineage.parent_id == root.header.packet_id
    assert child.lineage.root_id == root.lineage.root_id
    assert child.lineage.generation == root.lineage.generation + 1
    assert child.provenance.resolved_by_gate is True


@pytest.mark.asyncio
async def test_an_observation_hop_does_not_disturb_transport_identity(
    converge_worker: Any,
) -> None:
    """
    ADR-SDK-011: observation appends, it does not mutate.

    ``hop_trace`` is excluded from ``transport_hash``, so Gate observing a
    packet must leave both hashes and the packet id exactly as they were.
    """
    gate = GateSimulator()
    client = GateClient(make_client_config(local_node="odoo"), transport=RecordingTransport(gate))

    await client.execute(action="converge", payload=CONSUMER_PAYLOAD, tenant="tenant-a")

    inbound = gate.ingress_packets[0]
    observed = inbound.with_hop(
        make_ingress_hop(packet=inbound, node="gate", action=inbound.header.action)
    )

    assert observed.header.packet_id == inbound.header.packet_id
    assert observed.security.transport_hash == inbound.security.transport_hash
    assert observed.security.payload_hash == inbound.security.payload_hash
    assert len(observed.hop_trace) == len(inbound.hop_trace) + 1


@pytest.mark.asyncio
async def test_a_gate_derived_child_validates_in_the_worker_runtime(
    converge_worker: Any,
) -> None:
    """
    The child Gate builds must pass the worker's own validation, not just Gate's.

    A parent's observation hops are bound to the parent's packet id, so carrying
    them into a child would fail hop-trace validation here.
    """
    gate = GateSimulator()
    client = GateClient(make_client_config(local_node="odoo"), transport=RecordingTransport(gate))

    await client.execute(action="converge", payload=CONSUMER_PAYLOAD, tenant="tenant-a")

    child = gate.worker_packets[0]
    validate_transport_packet(child, dev_mode=True)
    for hop in child.hop_trace:
        assert hop.packet_id == child.header.packet_id


@pytest.mark.asyncio
async def test_a_signed_rail_round_trips(converge_worker: Any) -> None:
    """Signing and response verification stay active across the full rail."""
    gate = GateSimulator()
    config = make_client_config(
        local_node="odoo",
        require_signature=True,
        signing_key="shared-secret",
        signing_key_id="odoo-key-1",
        signing_algorithm="hmac-sha256",
        verifying_keys={"odoo-key-1": "shared-secret"},
    )
    client = GateClient(config, transport=RecordingTransport(gate))

    response = await client.execute(action="converge", payload=CONSUMER_PAYLOAD, tenant="tenant-a")

    assert gate.ingress_packets[0].security.signature is not None
    assert gate.ingress_packets[0].security.signing_key_id == "odoo-key-1"
    assert response.payload["state"] == "completed"


@pytest.mark.asyncio
async def test_the_rail_performs_exactly_one_execution(converge_worker: Any) -> None:
    """
    ADR-SDK-005 across the whole rail: one application call, one worker run.

    A hidden retry anywhere in the chain would double-execute a domain
    operation, which is the failure the no-retry law exists to prevent.
    """
    gate = GateSimulator()
    transport = RecordingTransport(gate)
    client = GateClient(make_client_config(local_node="odoo"), transport=transport)

    await client.execute(action="converge", payload=CONSUMER_PAYLOAD, tenant="tenant-a")

    assert len(transport.requests) == 1
    assert len(gate.worker_responses) == 1
