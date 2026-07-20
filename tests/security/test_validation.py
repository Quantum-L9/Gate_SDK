from __future__ import annotations

import pytest

from constellation_node_sdk.security.signing import sign_transport_packet
from constellation_node_sdk.security.validation import validate_transport_packet
from constellation_node_sdk.transport.packet import create_transport_packet


def test_validate_transport_packet_accepts_valid_signed_packet() -> None:
    packet = create_transport_packet(
        action="score",
        payload={"entity_id": "42"},
        tenant="tenant-a",
        destination_node="gate",
        source_node="client",
        reply_to="client",
    )
    signed = sign_transport_packet(
        packet,
        key="super-secret",
        key_id="hmac-key-1",
        algorithm="hmac-sha256",
    )

    validate_transport_packet(
        signed,
        key_resolver={"hmac-key-1": "super-secret"},
        require_signature=True,
        dev_mode=False,
    )


def test_validate_transport_packet_rejects_wrong_destination_for_local_node() -> None:
    packet = create_transport_packet(
        action="score",
        payload={"entity_id": "42"},
        tenant="tenant-a",
        destination_node="gate",
        source_node="client",
        reply_to="client",
    )

    with pytest.raises((ValueError, Exception)):
        validate_transport_packet(
            packet,
            local_node="worker-a",
            dev_mode=True,
        )


def test_validate_transport_packet_rejects_missing_signature_when_required() -> None:
    packet = create_transport_packet(
        action="score",
        payload={"entity_id": "42"},
        tenant="tenant-a",
        destination_node="gate",
        source_node="client",
        reply_to="client",
    )

    with pytest.raises((ValueError, Exception)):
        validate_transport_packet(
            packet,
            require_signature=True,
            dev_mode=False,
        )


def test_validate_transport_packet_enforces_idempotency_for_selected_actions() -> None:
    packet = create_transport_packet(
        action="score",
        payload={"entity_id": "42"},
        tenant="tenant-a",
        destination_node="gate",
        source_node="client",
        reply_to="client",
    )

    with pytest.raises((ValueError, Exception)):
        validate_transport_packet(
            packet,
            required_idempotency_actions=("score",),
            dev_mode=True,
        )
