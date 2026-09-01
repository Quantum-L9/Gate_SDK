"""
Gate authority — the guard that stops this becoming a node-to-peer side door.

The property under test is that **holding a worker URL is never enough**. The
authority lives in the packet, not in the caller, so importing the dispatch
transport grants an application nothing: it cannot mint a packet that passes.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from gate_client_helpers import (
    RecordingTransport,
    gate_echo_responder,
    make_client_config,
    make_dispatch_config,
    make_gate_dispatch_packet,
    worker_runtime_responder,
)

from constellation_node_sdk.gate.client import GateClient
from constellation_node_sdk.gate.errors import GatePolicyError
from constellation_node_sdk.gate_authority import (
    GateDispatchAuthorityError,
    GateDispatchConfigurationError,
    GateDispatchTransport,
)
from constellation_node_sdk.runtime.handlers import clear_handlers, register_handler
from constellation_node_sdk.transport.packet import TransportPacket, create_transport_packet
from constellation_node_sdk.transport.provenance import RoutingProvenance

WORKER = "enrichment-engine"
WORKER_URL = "http://enrichment-engine:8000"


def _dispatch(**config_overrides: Any) -> tuple[GateDispatchTransport, RecordingTransport]:
    recording = RecordingTransport(worker_runtime_responder(WORKER))
    return (
        GateDispatchTransport(make_dispatch_config(**config_overrides), transport=recording),
        recording,
    )


def _packet(**overrides: Any) -> TransportPacket:
    return create_transport_packet(
        action="converge",
        payload={"entity_id": "42"},
        tenant="tenant-a",
        timeout_ms=5_000,
        **overrides,
    )


# ---------------------------------------------------------------------------
# A worker URL alone is never enough
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_node_authored_packet_cannot_reach_a_worker() -> None:
    """
    The exact escape this guard exists to prevent.

    A node holds a worker URL and a perfectly valid packet of its own. Without
    Gate's authority on the packet, it goes nowhere.
    """
    dispatch, recording = _dispatch()
    node_packet = _packet(
        source_node="odoo",
        destination_node=WORKER,
        reply_to="odoo",
        provenance=RoutingProvenance(
            origin_kind="node",
            requested_action="converge",
            resolved_by_gate=False,
            original_source_node="odoo",
        ),
    )

    with pytest.raises(GateDispatchAuthorityError):
        await dispatch.send_gate_authored_packet(
            packet=node_packet, target_node=WORKER, worker_base_url=WORKER_URL
        )

    assert recording.requests == []


@pytest.mark.asyncio
async def test_a_client_authored_packet_cannot_reach_a_worker() -> None:
    dispatch, recording = _dispatch()
    client_packet = _packet(
        source_node="client",
        destination_node=WORKER,
        reply_to="client",
    )

    with pytest.raises(GateDispatchAuthorityError):
        await dispatch.send_gate_authored_packet(
            packet=client_packet, target_node=WORKER, worker_base_url=WORKER_URL
        )

    assert recording.requests == []


@pytest.mark.parametrize(
    ("mutation", "why"),
    [
        ({"resolved_by_gate": False}, "resolved_by_gate false"),
        ({"route_kind": None}, "route_kind missing"),
        ({"route_kind": "gate_relay"}, "route_kind is not external_ingress"),
    ],
)
@pytest.mark.asyncio
async def test_provenance_must_carry_gate_routing_authority(
    mutation: dict[str, Any], why: str
) -> None:
    """Gate-shaped addressing without Gate-shaped provenance is still rejected."""
    dispatch, recording = _dispatch()
    packet = _packet(
        source_node="gate",
        destination_node=WORKER,
        reply_to="gate",
        provenance=RoutingProvenance(
            **{
                "origin_kind": "gate",
                "requested_action": "converge",
                "resolved_by_gate": True,
                "route_kind": "external_ingress",
                "original_source_node": "odoo",
                **mutation,
            }
        ),
    )

    with pytest.raises(GateDispatchAuthorityError):
        await dispatch.send_gate_authored_packet(
            packet=packet, target_node=WORKER, worker_base_url=WORKER_URL
        )

    assert recording.requests == [], why


@pytest.mark.asyncio
async def test_source_must_be_the_configured_gate_node() -> None:
    """A packet not sourced from Gate is not a Gate dispatch, however it is addressed."""
    dispatch, recording = _dispatch()
    packet = make_gate_dispatch_packet(target_node=WORKER, gate_node="edge-gate")

    with pytest.raises(GateDispatchAuthorityError):
        await dispatch.send_gate_authored_packet(
            packet=packet, target_node=WORKER, worker_base_url=WORKER_URL
        )

    assert recording.requests == []


@pytest.mark.asyncio
async def test_destination_must_match_the_named_target() -> None:
    """
    The packet and the resolved target must agree.

    A mismatch means Gate's registry and Gate's packet disagree about where this
    work goes — which is not a routing decision the SDK gets to break the tie on.
    """
    dispatch, recording = _dispatch()
    packet = make_gate_dispatch_packet(target_node="some-other-worker")

    with pytest.raises(GateDispatchAuthorityError):
        await dispatch.send_gate_authored_packet(
            packet=packet, target_node=WORKER, worker_base_url=WORKER_URL
        )

    assert recording.requests == []


@pytest.mark.asyncio
async def test_reply_to_must_return_to_gate() -> None:
    """A dispatch whose answer would go elsewhere is not a Gate dispatch."""
    dispatch, recording = _dispatch()
    packet = _packet(
        source_node="gate",
        destination_node=WORKER,
        reply_to="odoo",
        provenance=RoutingProvenance(
            origin_kind="gate",
            requested_action="converge",
            resolved_by_gate=True,
            route_kind="external_ingress",
            original_source_node="odoo",
        ),
    )

    with pytest.raises(GateDispatchAuthorityError) as caught:
        await dispatch.send_gate_authored_packet(
            packet=packet, target_node=WORKER, worker_base_url=WORKER_URL
        )

    assert "reply" in str(caught.value).lower()
    assert recording.requests == []


@pytest.mark.asyncio
async def test_a_blank_target_is_rejected() -> None:
    dispatch, recording = _dispatch()
    with pytest.raises(GateDispatchConfigurationError):
        await dispatch.send_gate_authored_packet(
            packet=make_gate_dispatch_packet(target_node=WORKER),
            target_node="   ",
            worker_base_url=WORKER_URL,
        )
    assert recording.requests == []


# ---------------------------------------------------------------------------
# The SDK owns the execution endpoint
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_url",
    [
        "",
        "   ",
        "enrichment-engine:8000",
        "ftp://enrichment-engine:8000",
        "http://",
        "http://enrichment-engine:8000/v1/execute",
        "http://enrichment-engine:8000/admin",
        "http://enrichment-engine:8000?admin=1",
        "http://enrichment-engine:8000#frag",
    ],
)
@pytest.mark.asyncio
async def test_the_caller_supplies_a_base_url_not_an_endpoint(bad_url: str) -> None:
    """
    Gate names the host; the SDK names the path.

    Letting a caller pass a full endpoint would make the execution path a
    registry value, and a registry value is exactly what an attacker or a
    misconfiguration gets to change.
    """
    dispatch, recording = _dispatch()
    with pytest.raises(GateDispatchConfigurationError):
        await dispatch.send_gate_authored_packet(
            packet=make_gate_dispatch_packet(target_node=WORKER),
            target_node=WORKER,
            worker_base_url=bad_url,
        )
    assert recording.requests == []


@pytest.fixture()
def converge_worker() -> Any:
    """These cases reach the real worker runtime, which needs a handler."""
    clear_handlers()

    @register_handler("converge")
    async def handle(_org_id: str, _payload: dict[str, Any]) -> dict[str, Any]:
        return {"state": "completed"}

    yield
    clear_handlers()


@pytest.mark.parametrize(
    "base_url",
    ["http://enrichment-engine:8000", "http://enrichment-engine:8000/", "https://eie.internal"],
)
@pytest.mark.asyncio
async def test_the_canonical_endpoint_is_appended(base_url: str, converge_worker: Any) -> None:
    """A trailing slash, and either scheme, resolve to the same canonical endpoint."""
    dispatch, recording = _dispatch()
    await dispatch.send_gate_authored_packet(
        packet=make_gate_dispatch_packet(target_node=WORKER),
        target_node=WORKER,
        worker_base_url=base_url,
    )
    assert str(recording.requests[0].url) == f"{base_url.rstrip('/')}/v1/execute"


# ---------------------------------------------------------------------------
# The application surface stays sealed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_client_still_cannot_reach_a_worker() -> None:
    """
    PR #40's guarantee is unchanged by adding a Gate-only surface.

    An application still reaches Gate and only Gate: its client takes no
    destination, and its packet-native primitive refuses a peer target.
    """
    transport = RecordingTransport(gate_echo_responder({"state": "completed"}))
    client = GateClient(make_client_config(local_node="odoo"), transport=transport)

    execute_params = set(inspect.signature(GateClient.execute).parameters)
    assert not (
        execute_params
        & {"destination_node", "destination", "peer_url", "worker_url", "url", "target_node"}
    )

    peer_targeted = _packet(
        source_node="odoo",
        destination_node=WORKER,
        reply_to="odoo",
        provenance=RoutingProvenance(
            origin_kind="node",
            requested_action="converge",
            resolved_by_gate=False,
            original_source_node="odoo",
        ),
    )
    with pytest.raises(GatePolicyError):
        await client.send_to_gate(peer_targeted)
    assert transport.requests == []


@pytest.mark.asyncio
async def test_importing_the_dispatch_transport_grants_nothing() -> None:
    """
    Authority is on the packet, not the import.

    An application that imports the Gate-only module and hands it a worker URL
    still cannot dispatch, because it cannot produce a Gate-authored packet.
    """
    dispatch, recording = _dispatch()
    application_packet = _packet(
        source_node="odoo",
        destination_node=WORKER,
        reply_to="odoo",
        provenance=RoutingProvenance(
            origin_kind="node",
            requested_action="converge",
            resolved_by_gate=False,
            original_source_node="odoo",
        ),
    )

    with pytest.raises(GateDispatchAuthorityError):
        await dispatch.send_gate_authored_packet(
            packet=application_packet, target_node=WORKER, worker_base_url=WORKER_URL
        )
    assert recording.requests == []
