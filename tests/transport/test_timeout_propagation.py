"""
SDK13 / SDK14 — timeout_ms is transported faithfully; the SDK picks no budget.

The transport layer carries whatever timeout the calling application declared.
Choosing 25s, 30s, or any other number is application/release policy, owned by
the node that mints the packet — never by this SDK.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from constellation_node_sdk.transport.codec import (
    decode_transport_packet,
    encode_transport_packet,
)
from constellation_node_sdk.transport.packet import create_transport_packet
from constellation_node_sdk.transport.tenant import TenantContext

APPLICATION_TIMEOUT_MS = 45_000


def test_default_timeout_remains_thirty_seconds(
    tenant: TenantContext,
    domain_payload: dict,
) -> None:
    """Compatibility guard: the documented default must not drift silently."""
    packet = create_transport_packet(
        action="converge",
        payload=domain_payload,
        tenant=tenant,
    )

    assert packet.header.timeout_ms == 30_000


def test_custom_timeout_survives_packet_creation(
    tenant: TenantContext,
    domain_payload: dict,
) -> None:
    packet = create_transport_packet(
        action="converge",
        payload=domain_payload,
        tenant=tenant,
        timeout_ms=APPLICATION_TIMEOUT_MS,
    )

    assert packet.header.timeout_ms == APPLICATION_TIMEOUT_MS


def test_custom_timeout_survives_serialization_boundary(
    tenant: TenantContext,
    domain_payload: dict,
) -> None:
    packet = create_transport_packet(
        action="converge",
        payload=domain_payload,
        tenant=tenant,
        timeout_ms=APPLICATION_TIMEOUT_MS,
    )

    encoded = encode_transport_packet(packet)
    assert encoded["header"]["timeout_ms"] == APPLICATION_TIMEOUT_MS
    assert decode_transport_packet(encoded).header.timeout_ms == APPLICATION_TIMEOUT_MS


def test_derive_inherits_timeout_by_default(
    tenant: TenantContext,
    domain_payload: dict,
) -> None:
    root = create_transport_packet(
        action="converge",
        payload=domain_payload,
        tenant=tenant,
        timeout_ms=APPLICATION_TIMEOUT_MS,
    )

    worker_packet = root.derive(
        source_node="gate",
        destination_node="eie",
        reply_to="gate",
    )

    assert worker_packet.header.timeout_ms == APPLICATION_TIMEOUT_MS


def test_derive_honours_an_explicit_timeout_override(
    tenant: TenantContext,
    domain_payload: dict,
) -> None:
    root = create_transport_packet(
        action="converge",
        payload=domain_payload,
        tenant=tenant,
        timeout_ms=APPLICATION_TIMEOUT_MS,
    )

    child = root.derive(timeout_ms=5_000)

    assert child.header.timeout_ms == 5_000
    assert root.header.timeout_ms == APPLICATION_TIMEOUT_MS


@pytest.mark.parametrize("invalid_timeout", [0, -1, -30_000])
def test_non_positive_timeout_is_rejected(
    tenant: TenantContext,
    domain_payload: dict,
    invalid_timeout: int,
) -> None:
    """Fail closed: an unbounded or negative budget is never coerced to a default."""
    with pytest.raises(ValidationError):
        create_transport_packet(
            action="converge",
            payload=domain_payload,
            tenant=tenant,
            timeout_ms=invalid_timeout,
        )


@pytest.mark.parametrize("invalid_timeout", [0, -1])
def test_derive_rejects_non_positive_timeout_override(
    tenant: TenantContext,
    domain_payload: dict,
    invalid_timeout: int,
) -> None:
    root = create_transport_packet(
        action="converge",
        payload=domain_payload,
        tenant=tenant,
    )

    with pytest.raises(ValidationError):
        root.derive(timeout_ms=invalid_timeout)
