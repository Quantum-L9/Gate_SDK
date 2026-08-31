"""
Track E — transport carries application vocabulary, it does not prefer one.

The behavioral half of neutrality. ``test_domain_neutrality.py`` guards the
source tree statically; this file proves the property that actually matters
at runtime: whatever shape goes in comes out, key for key, and no shape is
treated better than another.

That last point is the subtle one. Transport must carry legacy vocabulary
too. A ``{"status", "final_fields"}` response is not the contract the
coordinated release settled on, but a node still running it must be carried
faithfully — the SDK's job is not to define or translate either vocabulary.
The proof is comparative: the same transformation, the identity, is applied
to every shape.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from constellation_node_sdk.runtime.execution import execute_transport_packet
from constellation_node_sdk.runtime.handlers import clear_handlers, register_handler
from constellation_node_sdk.transport.codec import (
    decode_transport_packet,
    encode_transport_packet,
)
from constellation_node_sdk.transport.packet import TransportPacket, create_transport_packet
from constellation_node_sdk.transport.tenant import TenantContext

# Three unrelated vocabularies: an arbitrary shape, the vocabulary the
# coordinated release uses, and the legacy one it replaced.
SHAPES: dict[str, dict[str, Any]] = {
    "arbitrary": {"alpha": 1, "nested": {"beta": None}},
    "current": {"state": "completed", "fields": {}},
    "legacy": {"status": "legacy", "final_fields": {}},
}

# Vocabulary from the other shapes. Carrying one must never introduce another.
FOREIGN_KEYS: dict[str, tuple[str, ...]] = {
    "arbitrary": ("state", "fields", "status", "final_fields"),
    "current": ("status", "final_fields", "alpha", "nested"),
    "legacy": ("state", "fields", "alpha", "nested"),
}


def _packet(tenant: TenantContext, payload: dict[str, Any]) -> TransportPacket:
    return create_transport_packet(
        action="converge",
        payload=copy.deepcopy(payload),
        tenant=tenant,
        source_node="gate",
        destination_node="worker",
        reply_to="gate",
    )


def _over_the_wire(packet: TransportPacket) -> TransportPacket:
    return decode_transport_packet(json.loads(json.dumps(encode_transport_packet(packet))))


@pytest.mark.parametrize("shape_name", sorted(SHAPES))
def test_every_shape_crosses_the_wire_as_an_opaque_dict(
    tenant: TenantContext, shape_name: str
) -> None:
    """Serialization treats all three vocabularies identically."""
    payload = SHAPES[shape_name]
    round_tripped = _over_the_wire(_packet(tenant, payload))

    assert round_tripped.payload == payload
    assert set(round_tripped.payload) == set(payload)
    for foreign in FOREIGN_KEYS[shape_name]:
        assert foreign not in round_tripped.payload, (
            f"carrying the {shape_name} shape introduced {foreign!r} from another vocabulary"
        )


@pytest.mark.parametrize("shape_name", sorted(SHAPES))
def test_every_shape_survives_derivation_unchanged(tenant: TenantContext, shape_name: str) -> None:
    """Derivation forwards the payload without inspecting it."""
    payload = SHAPES[shape_name]
    derived = _packet(tenant, payload).derive(source_node="worker", destination_node="gate")

    assert derived.payload == payload
    assert set(derived.payload) == set(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("shape_name", sorted(SHAPES))
async def test_every_shape_reaches_the_handler_and_returns_unchanged(
    tenant: TenantContext, shape_name: str
) -> None:
    """
    Request and response vocabulary are both opaque to the runtime.

    The legacy case is the pointed one: the runtime reads
    ``payload["status"]`` to label its observational hop, so a shape whose
    ``status`` is a domain word rather than a hop status is exactly where a
    write-back would show up.
    """
    payload = SHAPES[shape_name]
    seen: dict[str, Any] = {}

    clear_handlers()

    @register_handler("converge")
    async def handle(_org_id: str, received: dict[str, Any]) -> dict[str, Any]:
        seen["payload"] = copy.deepcopy(received)
        return copy.deepcopy(payload)

    response = await execute_transport_packet(
        _packet(tenant, payload), node_name="worker", dev_mode=True
    )

    assert seen["payload"] == payload
    assert response.payload == payload
    assert set(response.payload) == set(payload)


@pytest.mark.asyncio
async def test_no_vocabulary_is_transformed_more_than_any_other(
    tenant: TenantContext,
) -> None:
    """
    The comparative proof: the transformation applied is the identity, for all three.

    A per-shape assertion could pass while the SDK quietly normalized one
    vocabulary toward another. Measuring the transformation itself cannot.
    """
    transformations: dict[str, bool] = {}

    def echo_handler(shape: dict[str, Any]) -> Any:
        """Two-parameter handler, so the runtime dispatches (org_id, payload)."""

        async def handle(_org_id: str, _received: dict[str, Any]) -> dict[str, Any]:
            return copy.deepcopy(shape)

        return handle

    for shape_name, payload in SHAPES.items():
        clear_handlers()
        register_handler("converge", echo_handler(payload))

        response = await execute_transport_packet(
            _over_the_wire(_packet(tenant, payload)), node_name="worker", dev_mode=True
        )
        transformations[shape_name] = response.payload == payload

    assert set(transformations) == set(SHAPES)
    assert all(transformations.values()), (
        f"the SDK treats some vocabularies differently from others: {transformations}"
    )
