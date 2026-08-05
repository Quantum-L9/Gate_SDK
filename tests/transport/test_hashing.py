from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from constellation_node_sdk.transport.hashing import (
    _canonicalize,
    canonical_json,
    compute_payload_hash,
    compute_transport_hash,
)
from constellation_node_sdk.transport.packet import create_transport_packet


def test_canonical_json_is_stable_for_equivalent_dict_orderings() -> None:
    left = {"b": 2, "a": 1, "nested": {"z": 9, "x": 3}}
    right = {"nested": {"x": 3, "z": 9}, "a": 1, "b": 2}

    assert canonical_json(left) == canonical_json(right)


def test_datetime_canonicalize_is_utc_stable_across_offsets() -> None:
    """transport_hash must not depend on the host local timezone."""
    instant = datetime(2026, 8, 2, 16, 34, 29, 280366, tzinfo=UTC)
    edt = instant.astimezone(timezone(timedelta(hours=-4)))
    naive = instant.replace(tzinfo=None)

    assert _canonicalize(instant) == "2026-08-02T16:34:29.280366Z"
    assert _canonicalize(edt) == "2026-08-02T16:34:29.280366Z"
    assert _canonicalize(naive) == "2026-08-02T16:34:29.280366Z"
    assert canonical_json({"t": instant}) == canonical_json({"t": edt})
    assert canonical_json({"t": instant}) == canonical_json({"t": naive})


def test_compute_payload_hash_is_stable_for_equivalent_payloads() -> None:
    left = {"b": 2, "a": 1}
    right = {"a": 1, "b": 2}

    assert compute_payload_hash(left) == compute_payload_hash(right)


def test_compute_transport_hash_changes_when_semantic_payload_changes() -> None:
    packet = create_transport_packet(
        action="score",
        payload={"entity_id": "42"},
        tenant="tenant-a",
        destination_node="gate",
        source_node="client",
        reply_to="client",
    )
    mutated = packet.derive(
        action="score",
        source_node="client",
        destination_node="gate",
        reply_to="client",
        payload={"entity_id": "43"},
    )

    assert compute_transport_hash(packet) != compute_transport_hash(mutated)


def test_create_packet_integrity_passes_under_utc_canonicalize() -> None:
    packet = create_transport_packet(
        action="match",
        payload={"query": {"polymer": "HDPE"}},
        tenant="plasticos",
        destination_node="gate",
        source_node="odoo",
        reply_to="odoo",
    )
    assert compute_transport_hash(packet) == packet.security.transport_hash
    round_trip = type(packet).model_validate(packet.model_dump(mode="json"))
    assert round_trip.security.transport_hash == packet.security.transport_hash
