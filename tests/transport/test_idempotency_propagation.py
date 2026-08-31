"""
SDK11 / SDK12 — idempotency_key is a first-class transport header field.

The SDK does not generate, interpret, or namespace idempotency keys. It carries
whatever the calling application supplied, unchanged, across every canonical
transport transition: creation, serialization, parsing, and derivation.

Constellation.Gate depends on this: ``DelegationFactory`` documents that
``TransportPacket.derive`` inherits the parent's idempotency key, and
``ExecuteService`` caches results under ``header.idempotency_key``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from constellation_node_sdk.transport.codec import (
    decode_transport_packet,
    encode_transport_packet,
)
from constellation_node_sdk.transport.errors import TransportIntegrityError
from constellation_node_sdk.transport.packet import create_transport_packet
from constellation_node_sdk.transport.tenant import TenantContext

# An application-chosen key. Its format is the application's business, not the
# SDK's — this value is deliberately opaque and must never be reformatted.
APPLICATION_IDEMPOTENCY_KEY = "odoo:enrichment:123"


def test_create_transport_packet_stores_idempotency_key_verbatim(
    tenant: TenantContext,
    domain_payload: dict,
) -> None:
    packet = create_transport_packet(
        action="converge",
        payload=domain_payload,
        tenant=tenant,
        idempotency_key=APPLICATION_IDEMPOTENCY_KEY,
    )

    assert packet.header.idempotency_key == APPLICATION_IDEMPOTENCY_KEY


def test_idempotency_key_survives_serialization_boundary(
    tenant: TenantContext,
    domain_payload: dict,
) -> None:
    packet = create_transport_packet(
        action="converge",
        payload=domain_payload,
        tenant=tenant,
        idempotency_key=APPLICATION_IDEMPOTENCY_KEY,
    )

    encoded = encode_transport_packet(packet)
    assert encoded["header"]["idempotency_key"] == APPLICATION_IDEMPOTENCY_KEY

    decoded = decode_transport_packet(encoded)
    assert decoded.header.idempotency_key == APPLICATION_IDEMPOTENCY_KEY


def test_idempotency_key_survives_full_gate_style_rail(
    tenant: TenantContext,
    domain_payload: dict,
) -> None:
    """
    root packet -> serialize -> parse -> Gate-style derive -> worker packet.

    This is the exact chain a node-originated request travels before a worker
    runtime sees it. The key must be identical at the far end.
    """
    root = create_transport_packet(
        action="converge",
        payload=domain_payload,
        tenant=tenant,
        source_node="odoo",
        destination_node="gate",
        reply_to="odoo",
        idempotency_key=APPLICATION_IDEMPOTENCY_KEY,
    )

    at_gate = decode_transport_packet(encode_transport_packet(root))

    worker_packet = at_gate.derive(
        source_node="gate",
        destination_node="eie",
        reply_to="gate",
    )
    at_worker = decode_transport_packet(encode_transport_packet(worker_packet))

    assert at_worker.header.idempotency_key == APPLICATION_IDEMPOTENCY_KEY


def test_derive_preserves_idempotency_key_across_generations(
    tenant: TenantContext,
    domain_payload: dict,
) -> None:
    root = create_transport_packet(
        action="converge",
        payload=domain_payload,
        tenant=tenant,
        idempotency_key=APPLICATION_IDEMPOTENCY_KEY,
    )

    packet = root
    for _ in range(3):
        packet = packet.derive()
        assert packet.header.idempotency_key == APPLICATION_IDEMPOTENCY_KEY


def test_derive_to_response_preserves_idempotency_key(
    tenant: TenantContext,
    domain_payload: dict,
    domain_response_payload: dict,
) -> None:
    """A response packet stays keyed to the logical request that produced it."""
    request = create_transport_packet(
        action="converge",
        payload=domain_payload,
        tenant=tenant,
        source_node="gate",
        destination_node="eie",
        reply_to="gate",
        idempotency_key=APPLICATION_IDEMPOTENCY_KEY,
    )

    response = request.derive(
        packet_type="response",
        source_node="eie",
        destination_node="gate",
        reply_to="eie",
        payload=domain_response_payload,
    )

    assert response.header.idempotency_key == APPLICATION_IDEMPOTENCY_KEY


def test_absent_idempotency_key_stays_absent(
    tenant: TenantContext,
    domain_payload: dict,
) -> None:
    """The SDK never manufactures a key the application did not supply."""
    root = create_transport_packet(
        action="converge",
        payload=domain_payload,
        tenant=tenant,
    )

    assert root.header.idempotency_key is None

    encoded = encode_transport_packet(root)
    assert "idempotency_key" not in encoded["header"]

    child = decode_transport_packet(encoded).derive()
    assert child.header.idempotency_key is None


def test_blank_idempotency_key_is_rejected(tenant: TenantContext) -> None:
    """A blank-but-present key is a caller bug, not a transport default."""
    with pytest.raises(ValidationError):
        create_transport_packet(
            action="converge",
            payload={"entity": {}},
            tenant=tenant,
            idempotency_key="   ",
        )


def test_idempotency_key_is_covered_by_transport_hash(
    tenant: TenantContext,
    domain_payload: dict,
) -> None:
    """
    Swapping the key must change the transport hash.

    SDK20: automatic replay of side-effect-capable operations depends on the
    key being part of the integrity-protected transport core, not free metadata.
    """
    keyed = create_transport_packet(
        action="converge",
        payload=domain_payload,
        tenant=tenant,
        idempotency_key=APPLICATION_IDEMPOTENCY_KEY,
    )
    other = keyed.derive(
        payload=dict(domain_payload),
    )

    tampered = encode_transport_packet(keyed)
    tampered["header"]["idempotency_key"] = "odoo:enrichment:999"

    with pytest.raises(TransportIntegrityError, match="transport_hash"):
        decode_transport_packet(tampered)

    # Sanity: the untampered derived packet still validates.
    assert other.header.idempotency_key == APPLICATION_IDEMPOTENCY_KEY
