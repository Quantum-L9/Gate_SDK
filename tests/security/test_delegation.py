from __future__ import annotations

from datetime import UTC, datetime, timedelta

from constellation_node_sdk.security.delegation import compute_delegation_proof
from constellation_node_sdk.transport.packet import create_transport_packet


def test_compute_delegation_proof_is_deterministic() -> None:
    packet = create_transport_packet(
        action="enrich",
        payload={"entity_id": "42"},
        tenant="tenant-a",
        destination_node="gate",
        source_node="client",
        reply_to="client",
    )

    granted_at = datetime(2026, 1, 1, tzinfo=UTC)
    expires_at = granted_at + timedelta(hours=1)

    proof_a = compute_delegation_proof(
        packet=packet,
        delegator="gate",
        delegatee="enrich",
        scope=("enrich",),
        granted_at=granted_at,
        expires_at=expires_at,
        constraints={"tenant": "tenant-a"},
        key="secret",
    )

    proof_b = compute_delegation_proof(
        packet=packet,
        delegator="gate",
        delegatee="enrich",
        scope=("enrich",),
        granted_at=granted_at,
        expires_at=expires_at,
        constraints={"tenant": "tenant-a"},
        key="secret",
    )

    assert proof_a == proof_b


def test_compute_delegation_proof_changes_when_inputs_change() -> None:
    packet = create_transport_packet(
        action="enrich",
        payload={"entity_id": "42"},
        tenant="tenant-a",
        destination_node="gate",
        source_node="client",
        reply_to="client",
    )

    granted_at = datetime(2026, 1, 1, tzinfo=UTC)

    proof_a = compute_delegation_proof(
        packet=packet,
        delegator="gate",
        delegatee="enrich",
        scope=("enrich",),
        granted_at=granted_at,
        key="secret",
    )

    proof_b = compute_delegation_proof(
        packet=packet,
        delegator="gate",
        delegatee="enrich",
        scope=("score",),
        granted_at=granted_at,
        key="secret",
    )

    assert proof_a != proof_b
