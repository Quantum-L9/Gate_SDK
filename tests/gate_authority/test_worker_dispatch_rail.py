"""
The Gate→worker rail, end to end, through the real SDK worker runtime.

The worker half of every test here is ``execute_transport_packet`` — the same
entry point a deployed node uses. A hand-written responder that agreed only with
the client under test would prove the client consistent with itself and nothing
about whether a real worker accepts what Gate sends.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from gate_client_helpers import (
    RecordingTransport,
    derive_gate_dispatch,
    make_dispatch_config,
    make_gate_dispatch_packet,
    worker_runtime_responder,
)

from constellation_node_sdk.gate_authority import GateDispatchTransport
from constellation_node_sdk.runtime import execution
from constellation_node_sdk.runtime.handlers import clear_handlers, register_handler
from constellation_node_sdk.security.validation import validate_transport_packet
from constellation_node_sdk.transport.packet import TransportPacket, create_transport_packet
from constellation_node_sdk.transport.provenance import RoutingProvenance

WORKER = "enrichment-engine"

CONSUMER_PAYLOAD: dict[str, Any] = {
    "entity": {"id": "org-4711", "fields": {"website": None}},
    "requested_fields": ["website", "employee_count"],
    "run_id": "durable-run-4711",
}


@pytest.fixture()
def converge_worker() -> Any:
    clear_handlers()

    @register_handler("converge")
    async def handle(_org_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "state": "completed",
            "run_id": payload["run_id"],
            "fields": {"website": "https://acme.test"},
        }

    yield
    clear_handlers()


def _transport() -> tuple[GateDispatchTransport, RecordingTransport]:
    recording = RecordingTransport(worker_runtime_responder(WORKER))
    return GateDispatchTransport(make_dispatch_config(), transport=recording), recording


# ---------------------------------------------------------------------------
# The rail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_authored_packet_reaches_a_real_worker(converge_worker: Any) -> None:
    """One dispatch, through real worker ingress and execution, back validated."""
    dispatch, recording = _transport()
    packet = make_gate_dispatch_packet(target_node=WORKER, payload=CONSUMER_PAYLOAD)

    response = await dispatch.send_gate_authored_packet(
        packet=packet,
        target_node=WORKER,
        worker_base_url="http://enrichment-engine:8000",
    )

    assert isinstance(response, TransportPacket)
    assert response.payload["state"] == "completed"
    assert response.payload["run_id"] == "durable-run-4711"
    assert len(recording.requests) == 1
    assert str(recording.requests[0].url) == "http://enrichment-engine:8000/v1/execute"


@pytest.mark.asyncio
async def test_the_domain_payload_reaches_the_worker_unchanged(converge_worker: Any) -> None:
    """The SDK transports the payload; it never interprets or supplements it."""
    dispatch, recording = _transport()
    packet = make_gate_dispatch_packet(target_node=WORKER, payload=CONSUMER_PAYLOAD)

    await dispatch.send_gate_authored_packet(
        packet=packet, target_node=WORKER, worker_base_url="http://enrichment-engine:8000"
    )

    assert recording.sent_packet(0)["payload"] == CONSUMER_PAYLOAD


@pytest.mark.asyncio
async def test_operation_identity_survives_the_dispatch(converge_worker: Any) -> None:
    """Tenant, idempotency, correlation, and root lineage all reach the worker."""
    dispatch, recording = _transport()
    packet = make_gate_dispatch_packet(target_node=WORKER, payload=CONSUMER_PAYLOAD)

    response = await dispatch.send_gate_authored_packet(
        packet=packet, target_node=WORKER, worker_base_url="http://enrichment-engine:8000"
    )

    sent = recording.sent_packet(0)
    assert sent["header"]["idempotency_key"] == "odoo:enrichment:run-1"
    assert sent["header"]["correlation_id"] == "corr-1"
    assert response.header.idempotency_key == packet.header.idempotency_key
    assert response.header.correlation_id == packet.header.correlation_id
    assert response.tenant == packet.tenant
    assert response.lineage.root_id == packet.lineage.root_id


@pytest.mark.asyncio
async def test_response_routing_is_canonical(converge_worker: Any) -> None:
    """The worker answers as itself, addressed back to Gate, caused by the dispatch."""
    dispatch, _ = _transport()
    packet = make_gate_dispatch_packet(target_node=WORKER, payload=CONSUMER_PAYLOAD)

    response = await dispatch.send_gate_authored_packet(
        packet=packet, target_node=WORKER, worker_base_url="http://enrichment-engine:8000"
    )

    assert response.address.source_node == WORKER
    assert response.address.destination_node == "gate"
    assert response.header.causation_id == packet.header.packet_id
    validate_transport_packet(response, dev_mode=True)


@pytest.mark.asyncio
async def test_the_dispatched_packet_carries_a_valid_child_hop(converge_worker: Any) -> None:
    """Every hop on the dispatched packet is bound to that packet's own id."""
    dispatch, recording = _transport()
    packet = make_gate_dispatch_packet(target_node=WORKER, payload=CONSUMER_PAYLOAD)

    await dispatch.send_gate_authored_packet(
        packet=packet, target_node=WORKER, worker_base_url="http://enrichment-engine:8000"
    )

    sent = TransportPacket.model_validate(recording.sent_packet(0))
    assert sent.hop_trace
    for hop in sent.hop_trace:
        assert hop.packet_id == sent.header.packet_id


# ---------------------------------------------------------------------------
# One downstream deadline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remaining_budget_drives_socket_and_worker_alike(converge_worker: Any) -> None:
    """
    The whole point of the closure, proven in one test.

    A root operation budgeted 30s has 28s consumed inside Gate. The worker's
    registered cap is 25s. The remaining downstream attempt budget is therefore
    2s, and that single number must appear in three places: the packet header
    the worker reads, the socket deadline Gate waits on, and the handler budget
    the worker's runtime enforces. Any two of them disagreeing is the drift this
    closes.
    """
    root_budget_ms = 30_000
    elapsed_in_gate_ms = 28_000
    worker_cap_ms = 25_000
    remaining_ms = min(root_budget_ms - elapsed_in_gate_ms, worker_cap_ms)
    assert remaining_ms == 2_000

    root = create_transport_packet(
        action="converge",
        payload=CONSUMER_PAYLOAD,
        tenant="tenant-a",
        source_node="odoo",
        destination_node="gate",
        reply_to="odoo",
        timeout_ms=root_budget_ms,
        provenance=RoutingProvenance(
            origin_kind="node",
            requested_action="converge",
            resolved_by_gate=False,
            original_source_node="odoo",
        ),
    )
    dispatch_packet = derive_gate_dispatch(root, target_node=WORKER, timeout_ms=remaining_ms)

    observed_handler_budget: list[float] = []
    real_wait_for = asyncio.wait_for

    async def recording_wait_for(awaitable: Any, timeout: float | None = None) -> Any:
        observed_handler_budget.append(timeout)
        return await real_wait_for(awaitable, timeout)

    recording = RecordingTransport(worker_runtime_responder(WORKER))
    transport = GateDispatchTransport(make_dispatch_config(), transport=recording)

    original = execution.asyncio.wait_for
    execution.asyncio.wait_for = recording_wait_for  # type: ignore[assignment]
    try:
        await transport.send_gate_authored_packet(
            packet=dispatch_packet,
            target_node=WORKER,
            worker_base_url="http://enrichment-engine:8000",
        )
    finally:
        execution.asyncio.wait_for = original  # type: ignore[assignment]

    # 1. the budget the packet advertises downstream
    assert dispatch_packet.header.timeout_ms == 2_000
    assert recording.sent_packet(0)["header"]["timeout_ms"] == 2_000
    # 2. the deadline httpx actually applied
    assert recording.applied_timeout(0)["read"] == pytest.approx(2.0)
    # 3. the budget the worker's runtime enforced on its handler
    assert observed_handler_budget == [pytest.approx(2.0)]


@pytest.mark.asyncio
async def test_the_public_surface_has_no_timeout_parameter() -> None:
    """
    A timeout argument here would recreate two downstream deadlines.

    Gate would pass its remaining budget as a socket timeout while the packet
    still advertised the parent's, which is precisely the state this closure
    removes.
    """
    import inspect

    parameters = set(inspect.signature(GateDispatchTransport.send_gate_authored_packet).parameters)
    assert not (parameters & {"timeout", "timeout_ms", "timeout_seconds", "deadline"})
    assert parameters == {"self", "packet", "target_node", "worker_base_url"}


@pytest.mark.asyncio
async def test_a_short_packet_budget_is_not_widened(converge_worker: Any) -> None:
    """A 500ms dispatch waits 0.5s, whatever any client default might say."""
    dispatch, recording = _transport()
    packet = make_gate_dispatch_packet(target_node=WORKER, payload=CONSUMER_PAYLOAD, timeout_ms=500)

    await dispatch.send_gate_authored_packet(
        packet=packet, target_node=WORKER, worker_base_url="http://enrichment-engine:8000"
    )

    assert recording.applied_timeout(0)["read"] == pytest.approx(0.5)
