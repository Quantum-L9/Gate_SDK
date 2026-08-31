"""
SDK14 / SDK15 / SDK16 / SDK17 — worker runtime is domain-transparent.

The runtime resolves a handler by ``header.action``, hands it the payload
untouched, bounds it with the packet's own timeout, and returns whatever dict
the handler produced. It does not inspect payload shape to choose a handler,
does not rewrite the application's response, and adds no retry layer of its own.
"""

from __future__ import annotations

import asyncio
import copy

import pytest

from constellation_node_sdk.runtime.execution import execute_transport_packet
from constellation_node_sdk.runtime.handlers import register_handler
from constellation_node_sdk.transport.packet import TransportPacket, create_transport_packet
from constellation_node_sdk.transport.tenant import TenantContext

FORBIDDEN_MANUFACTURED_KEYS = (
    "entity_snapshot",
    "entity_id",
    "final_fields",
    "writeback",
)


def _worker_packet(
    tenant: TenantContext,
    payload: dict,
    *,
    action: str = "converge",
    timeout_ms: int = 30_000,
    idempotency_key: str | None = None,
) -> TransportPacket:
    """A packet shaped the way Gate delivers work to a worker node."""
    return create_transport_packet(
        action=action,
        payload=payload,
        tenant=tenant,
        source_node="gate",
        destination_node="eie",
        reply_to="gate",
        timeout_ms=timeout_ms,
        idempotency_key=idempotency_key,
    )


async def _execute(packet: TransportPacket, *, action: str = "converge") -> TransportPacket:
    return await execute_transport_packet(
        packet,
        node_name="eie",
        allowed_actions=(action,),
        allowed_packet_types=("request",),
        max_attachments=0,
        max_attachment_size_bytes=0,
        allowed_attachment_schemes=(),
        dev_mode=True,
    )


@pytest.mark.asyncio
async def test_handler_receives_the_domain_payload_unchanged(
    tenant: TenantContext,
    domain_payload: dict,
) -> None:
    original = copy.deepcopy(domain_payload)
    seen: dict = {}

    @register_handler("converge")
    async def handle(_org: str, payload: dict) -> dict:
        seen.update({"payload": copy.deepcopy(payload)})
        return {"state": "completed"}

    await _execute(_worker_packet(tenant, domain_payload))

    assert seen["payload"] == original


@pytest.mark.asyncio
async def test_response_payload_is_returned_exactly_as_the_handler_produced_it(
    tenant: TenantContext,
    domain_payload: dict,
    domain_response_payload: dict,
) -> None:
    """
    ``state`` stays ``state``; ``fields`` stays ``fields``.

    Translating either into ``status`` / ``final_fields`` is a domain-contract
    decision owned by the application repositories, never by transport.
    """
    expected = copy.deepcopy(domain_response_payload)

    @register_handler("converge")
    async def handle(_org: str, _payload: dict) -> dict:
        return copy.deepcopy(domain_response_payload)

    response = await _execute(_worker_packet(tenant, domain_payload))

    assert response.payload == expected
    assert [key for key in FORBIDDEN_MANUFACTURED_KEYS if key in response.payload] == []


@pytest.mark.asyncio
async def test_response_payload_is_neutral_for_an_unrelated_shape(
    tenant: TenantContext,
) -> None:
    """A second, structurally unrelated response proves neutrality, not luck."""
    arbitrary = {"rows": [1, 2, 3], "cursor": None, "meta": {"page": 1}}

    @register_handler("converge")
    async def handle(_org: str, _payload: dict) -> dict:
        return copy.deepcopy(arbitrary)

    response = await _execute(_worker_packet(tenant, {"query": "x"}))

    assert response.payload == arbitrary


@pytest.mark.asyncio
async def test_hop_status_heuristic_never_writes_into_the_payload(
    tenant: TenantContext,
    domain_payload: dict,
) -> None:
    """
    The runtime reads ``payload['status']`` only to label the observational hop.

    When the application does not use that key, the hop falls back to
    ``completed`` and the payload must remain exactly what the handler returned.
    """

    @register_handler("converge")
    async def handle(_org: str, _payload: dict) -> dict:
        return {"state": "completed"}

    response = await _execute(_worker_packet(tenant, domain_payload))

    assert response.payload == {"state": "completed"}
    assert "status" not in response.payload
    assert response.hop_trace[-1].direction == "response"
    assert response.hop_trace[-1].status == "completed"


@pytest.mark.asyncio
async def test_response_stays_correlated_to_the_request(
    tenant: TenantContext,
    domain_payload: dict,
) -> None:
    """SDK17: the response is traceable back to the logical request."""

    @register_handler("converge")
    async def handle(_org: str, _payload: dict) -> dict:
        return {"state": "completed"}

    request = _worker_packet(tenant, domain_payload, idempotency_key="odoo:enrichment:123")
    response = await _execute(request)

    assert response.header.packet_type == "response"
    assert response.header.correlation_id == request.header.correlation_id
    assert response.header.causation_id == request.header.packet_id
    assert response.lineage.parent_id == request.header.packet_id
    assert response.lineage.root_id == request.lineage.root_id
    assert response.header.idempotency_key == request.header.idempotency_key
    assert response.header.timeout_ms == request.header.timeout_ms
    assert response.address.source_node == "eie"
    assert response.address.destination_node == request.address.reply_to
    assert response.address.reply_to == "eie"


@pytest.mark.asyncio
async def test_runtime_bounds_handler_execution_with_the_packet_timeout(
    tenant: TenantContext,
    domain_payload: dict,
) -> None:
    """SDK15: the packet's own budget is the one enforced, not an SDK constant."""

    @register_handler("converge")
    async def handle(_org: str, _payload: dict) -> dict:
        await asyncio.sleep(30)
        return {"state": "completed"}

    packet = _worker_packet(tenant, domain_payload, timeout_ms=150)

    loop = asyncio.get_running_loop()
    started = loop.time()
    with pytest.raises(TimeoutError, match="handler timeout after 150ms"):
        await _execute(packet)

    assert loop.time() - started < 5.0


@pytest.mark.asyncio
async def test_runtime_does_not_retry_a_failing_handler(
    tenant: TenantContext,
    domain_payload: dict,
) -> None:
    """SDK16: no hidden whole-operation retry wraps an application handler."""
    calls: list[int] = []

    @register_handler("converge")
    async def handle(_org: str, _payload: dict) -> dict:
        calls.append(1)
        raise RuntimeError("provider unavailable")

    with pytest.raises(RuntimeError, match="provider unavailable"):
        await _execute(_worker_packet(tenant, domain_payload))

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_runtime_does_not_retry_a_timing_out_handler(
    tenant: TenantContext,
    domain_payload: dict,
) -> None:
    calls: list[int] = []

    @register_handler("converge")
    async def handle(_org: str, _payload: dict) -> dict:
        calls.append(1)
        await asyncio.sleep(30)
        return {"state": "completed"}

    with pytest.raises(TimeoutError):
        await _execute(_worker_packet(tenant, domain_payload, timeout_ms=100))

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_dispatch_is_by_header_action_not_payload_shape(
    tenant: TenantContext,
    domain_payload: dict,
) -> None:
    """
    Two identical payloads dispatch to different handlers purely by action.

    The runtime must never sniff the payload to choose business behaviour.
    """

    @register_handler("converge")
    async def handle_converge(_org: str, _payload: dict) -> dict:
        return {"handled_by": "converge"}

    @register_handler("score")
    async def handle_score(_org: str, _payload: dict) -> dict:
        return {"handled_by": "score"}

    converge = await _execute(_worker_packet(tenant, domain_payload, action="converge"))
    scored = await _execute(
        _worker_packet(tenant, copy.deepcopy(domain_payload), action="score"),
        action="score",
    )

    assert converge.payload == {"handled_by": "converge"}
    assert scored.payload == {"handled_by": "score"}
    assert converge.header.action == "converge"
    assert scored.header.action == "score"
