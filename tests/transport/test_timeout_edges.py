"""
Track G — the SDK transports and enforces a timeout, it does not choose one.

PR #37 pinned the 30s default, inheritance across ``derive()``, and rejection
of non-positive budgets. This file covers the edges around those: the
smallest accepted budget, budgets far larger than any application policy
would allow, and exact integer preservation across the wire.

The distinction being protected is a design boundary. "Enrichment must
finish within 30 seconds" is an application policy and belongs to Odoo. The
SDK's job is to carry whatever integer the caller set and to enforce that
integer at the worker — never to substitute a business maximum of its own.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from constellation_node_sdk.transport.codec import (
    decode_transport_packet,
    encode_transport_packet,
)
from constellation_node_sdk.transport.packet import TransportPacket, create_transport_packet
from constellation_node_sdk.transport.tenant import TenantContext

ONE_HOUR_MS = 3_600_000


def _packet(tenant: TenantContext, timeout_ms: int) -> TransportPacket:
    return create_transport_packet(
        action="converge",
        payload={"entity": {"id": "res.partner:55"}},
        tenant=tenant,
        source_node="odoo",
        destination_node="gate",
        reply_to="odoo",
        timeout_ms=timeout_ms,
    )


@pytest.mark.parametrize("timeout_ms", [1, 2, 999, 30_000, 120_000, ONE_HOUR_MS])
def test_any_positive_budget_is_accepted_and_carried_exactly(
    tenant: TenantContext, timeout_ms: int
) -> None:
    """
    The SDK imposes no ceiling of its own.

    An hour is well past anything the enrichment rail would ask for, and it
    is still transported: a value that large is an application-policy
    question, and the SDK is not where that policy lives.
    """
    packet = _packet(tenant, timeout_ms)
    assert packet.header.timeout_ms == timeout_ms


@pytest.mark.parametrize("timeout_ms", [0, -1, -30_000])
def test_a_non_positive_budget_is_rejected_rather_than_coerced(
    tenant: TenantContext, timeout_ms: int
) -> None:
    """
    A meaningless budget fails loudly.

    Coercing it to the default would be the SDK choosing a timeout, and the
    caller would never learn its own value had been discarded.
    """
    with pytest.raises(ValidationError):
        _packet(tenant, timeout_ms)


@pytest.mark.parametrize("timeout_ms", [1, 30_000, ONE_HOUR_MS])
def test_the_budget_crosses_the_wire_as_an_exact_integer(
    tenant: TenantContext, timeout_ms: int
) -> None:
    """
    No float, no string, no rounding.

    Gate bounds execution on this number; a value that arrives as ``30000.0``
    or ``"30000"`` would either fail validation downstream or silently change
    meaning.
    """
    packet = _packet(tenant, timeout_ms)
    wire = json.loads(json.dumps(encode_transport_packet(packet)))

    assert wire["header"]["timeout_ms"] == timeout_ms
    assert isinstance(wire["header"]["timeout_ms"], int)
    assert not isinstance(wire["header"]["timeout_ms"], bool)

    assert decode_transport_packet(wire).header.timeout_ms == timeout_ms


def test_the_budget_survives_a_multi_generation_chain(tenant: TenantContext) -> None:
    """Inheritance holds across generations, not just across one derive."""
    root = _packet(tenant, 45_000)
    second = root.derive(source_node="gate", destination_node="worker", reply_to="gate")
    third = second.derive(packet_type="response", source_node="worker", destination_node="gate")

    assert [p.header.timeout_ms for p in (root, second, third)] == [45_000, 45_000, 45_000]


def test_an_explicit_override_replaces_only_that_generations_budget(
    tenant: TenantContext,
) -> None:
    """
    An override is a decision that generation made, and it then inherits.

    This is Gate narrowing a worker's budget: the child gets the smaller
    number and the grandchild inherits the child's, not the root's.
    """
    root = _packet(tenant, 45_000)
    narrowed = root.derive(
        source_node="gate", destination_node="worker", reply_to="gate", timeout_ms=5_000
    )
    grandchild = narrowed.derive(packet_type="response", source_node="worker")

    assert root.header.timeout_ms == 45_000, "the parent's own budget was rewritten"
    assert narrowed.header.timeout_ms == 5_000
    assert grandchild.header.timeout_ms == 5_000
