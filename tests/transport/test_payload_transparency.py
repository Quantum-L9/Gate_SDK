"""
SDK4 / SDK5 / SDK6 / SDK18 — the payload is opaque application data.

The transport layer carries a domain payload semantically byte-for-byte. It
never renames, supplements, drops, or reinterprets a key. This file is the
anti-drift regression surface for the coordinated Odoo -> Gate -> EIE rail: if
a future change starts translating domain fields, these tests fail here rather
than surfacing as a mismatched contract in an application repository.
"""

from __future__ import annotations

import copy

import pytest

from constellation_node_sdk.transport.codec import (
    decode_transport_packet,
    encode_transport_packet,
)
from constellation_node_sdk.transport.errors import TransportIntegrityError
from constellation_node_sdk.transport.hop_trace import make_ingress_hop
from constellation_node_sdk.transport.packet import create_transport_packet
from constellation_node_sdk.transport.tenant import TenantContext

# Keys the SDK must never invent. Each is a domain-contract concern owned by an
# application repository, and each has a sibling spelling that a translation
# layer would be tempted to "helpfully" normalize to.
FORBIDDEN_MANUFACTURED_KEYS = (
    "entity_snapshot",
    "entity_id",
    "status",
    "final_fields",
    "writeback",
)


def _assert_no_manufactured_keys(payload: dict) -> None:
    manufactured = [key for key in FORBIDDEN_MANUFACTURED_KEYS if key in payload]
    assert manufactured == [], f"SDK manufactured domain keys: {manufactured}"


def test_round_trip_preserves_an_arbitrary_domain_payload(
    tenant: TenantContext,
    domain_payload: dict,
) -> None:
    original = copy.deepcopy(domain_payload)

    packet = create_transport_packet(
        action="converge",
        payload=domain_payload,
        tenant=tenant,
    )
    decoded = decode_transport_packet(encode_transport_packet(packet))

    assert decoded.payload == original
    assert domain_payload == original, "create_transport_packet mutated the caller's dict"


def test_none_valued_domain_fields_are_not_dropped_by_serialization(
    tenant: TenantContext,
) -> None:
    """
    ``model_dump(exclude_none=True)`` applies to transport model fields only.

    A domain field explicitly set to null is meaningful application data — a
    cleared website is not the same as an unmentioned one — so it must survive
    the wire intact.
    """
    payload = {"entity": {"website": None}, "state": None, "fields": {"vat": None}}

    packet = create_transport_packet(action="converge", payload=payload, tenant=tenant)
    encoded = encode_transport_packet(packet)

    assert encoded["payload"] == payload
    assert decode_transport_packet(encoded).payload == payload


def test_derive_with_unchanged_payload_preserves_domain_semantics(
    tenant: TenantContext,
    domain_payload: dict,
) -> None:
    """Gate derives a worker packet without touching the payload it forwards."""
    original = copy.deepcopy(domain_payload)

    root = create_transport_packet(
        action="converge",
        payload=domain_payload,
        tenant=tenant,
        source_node="odoo",
    )
    worker_packet = root.derive(
        source_node="gate",
        destination_node="eie",
        reply_to="gate",
        payload=dict(root.payload),
    )

    assert worker_packet.payload == original
    assert worker_packet.header.action == "converge"
    _assert_no_manufactured_keys(worker_packet.payload)


def test_sdk_never_manufactures_domain_fields_across_the_rail(
    tenant: TenantContext,
    domain_payload: dict,
) -> None:
    """
    The keys carried at the far end are exactly the keys the caller supplied.

    ``entity`` stays ``entity``; nothing becomes ``entity_snapshot``. No
    top-level ``entity_id``, ``status``, ``final_fields``, or ``writeback``
    appears anywhere along the chain.
    """
    expected_keys = set(domain_payload)

    root = create_transport_packet(
        action="converge",
        payload=domain_payload,
        tenant=tenant,
        source_node="odoo",
    )
    at_gate = decode_transport_packet(encode_transport_packet(root))
    worker_packet = at_gate.derive(
        source_node="gate",
        destination_node="eie",
        reply_to="gate",
    )
    at_worker = decode_transport_packet(encode_transport_packet(worker_packet))

    for stage in (root, at_gate, worker_packet, at_worker):
        assert set(stage.payload) == expected_keys
        _assert_no_manufactured_keys(stage.payload)

    assert at_worker.payload["entity"] == domain_payload["entity"]


def test_unicode_and_nested_structures_survive_canonicalization(
    tenant: TenantContext,
) -> None:
    payload = {
        "entity": {"name": "Ácmé Kunststoffe — GmbH", "aliases": ["ACME", "Ácmé"]},
        "scores": [0.1, 0.25],
        "nested": {"a": {"b": {"c": [1, 2, {"d": None}]}}},
    }

    packet = create_transport_packet(action="converge", payload=payload, tenant=tenant)

    assert decode_transport_packet(encode_transport_packet(packet)).payload == payload


def test_payload_hash_detects_domain_payload_mutation(
    tenant: TenantContext,
    domain_payload: dict,
) -> None:
    """SDK6: a tampered domain field must not pass integrity validation."""
    packet = create_transport_packet(
        action="converge",
        payload=domain_payload,
        tenant=tenant,
    )

    tampered = encode_transport_packet(packet)
    tampered["payload"]["entity"]["name"] = "Not Acme"

    with pytest.raises(TransportIntegrityError, match="payload_hash"):
        decode_transport_packet(tampered)


def test_transport_hash_detects_protected_header_mutation(
    tenant: TenantContext,
    domain_payload: dict,
) -> None:
    packet = create_transport_packet(
        action="converge",
        payload=domain_payload,
        tenant=tenant,
        timeout_ms=30_000,
    )

    tampered = encode_transport_packet(packet)
    tampered["header"]["timeout_ms"] = 1_000

    with pytest.raises(TransportIntegrityError, match="transport_hash"):
        decode_transport_packet(tampered)


def test_hop_append_preserves_the_signed_transport_core(
    tenant: TenantContext,
    domain_payload: dict,
) -> None:
    """SDK8: observing a hop is not a semantic change to the packet."""
    packet = create_transport_packet(
        action="converge",
        payload=domain_payload,
        tenant=tenant,
        source_node="odoo",
    )

    observed = packet.with_hop(
        make_ingress_hop(
            packet=packet,
            node="gate",
            action=packet.header.action,
            status="validated",
        )
    )

    assert observed.security.transport_hash == packet.security.transport_hash
    assert observed.security.payload_hash == packet.security.payload_hash
    assert observed.header.packet_id == packet.header.packet_id
    assert observed.payload == packet.payload
    assert len(observed.hop_trace) == 1
