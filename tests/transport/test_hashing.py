from __future__ import annotations

from constellation_node_sdk.transport.hashing import canonical_json, compute_payload_hash, compute_transport_hash
from constellation_node_sdk.transport.packet import create_transport_packet


def test_canonical_json_is_stable_for_equivalent_dict_orderings() -> None:
    left = {"b": 2, "a": 1, "nested": {"z": 9, "x": 3}}
    right = {"nested": {"x": 3, "z": 9}, "a": 1, "b": 2}

    assert canonical_json(left) == canonical_json(right)


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
