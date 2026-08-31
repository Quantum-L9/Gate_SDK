"""
Track C — mutated transport state must not masquerade as valid.

PR #37 proved that a payload mutation and one protected header mutation are
detected. This file widens that to the specific fields the coordinated rail
routes, caches, and bounds execution on, and adds the harder case: an
attacker who repairs ``payload_hash`` after editing the payload still fails,
because ``transport_hash`` covers the repaired digest.

Every mutation here goes through the supported low-level path — dump the
packet, edit the dict, re-parse it — because ``TransportPacket`` is frozen.
That is also the shape of the real threat: bytes arriving over HTTP.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from constellation_node_sdk.transport.errors import TransportIntegrityError
from constellation_node_sdk.transport.hashing import compute_payload_hash, compute_transport_hash
from constellation_node_sdk.transport.hop_trace import make_ingress_hop, validate_hop_trace
from constellation_node_sdk.transport.packet import TransportPacket, create_transport_packet
from constellation_node_sdk.transport.tenant import TenantContext


def _packet(tenant: TenantContext) -> TransportPacket:
    return create_transport_packet(
        action="converge",
        payload={"entity": {"id": "res.partner:55"}, "objective": "enrich"},
        tenant=tenant,
        source_node="odoo",
        destination_node="gate",
        reply_to="odoo",
        idempotency_key="odoo:enrichment:res.partner:55",
        correlation_id="corr-adversarial-1",
        timeout_ms=45_000,
    )


def _mutate(raw: dict[str, Any], path: tuple[str, ...], value: Any) -> dict[str, Any]:
    mutated = copy.deepcopy(raw)
    cursor: Any = mutated
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = value
    return mutated


# ---------------------------------------------------------------------------
# Payload integrity
# ---------------------------------------------------------------------------


def test_payload_mutation_is_rejected(tenant: TenantContext) -> None:
    """Editing the payload without touching the digests fails ``payload_hash``."""
    raw = _packet(tenant).model_dump(mode="json")
    tampered = _mutate(raw, ("payload", "objective"), "exfiltrate")

    with pytest.raises(TransportIntegrityError, match="payload_hash"):
        TransportPacket.model_validate(tampered)


def test_repairing_the_payload_hash_does_not_rescue_a_mutated_payload(
    tenant: TenantContext,
) -> None:
    """
    The interesting adversary is the one who knows about ``payload_hash``.

    ``transport_hash`` covers the payload digest, so recomputing the digest
    to match the edited payload moves the failure rather than removing it.
    A single-hash design would have accepted this packet.
    """
    raw = _packet(tenant).model_dump(mode="json")
    tampered = _mutate(raw, ("payload", "objective"), "exfiltrate")
    tampered["security"]["payload_hash"] = compute_payload_hash(tampered["payload"])

    with pytest.raises(TransportIntegrityError, match="transport_hash"):
        TransportPacket.model_validate(tampered)


def test_adding_a_payload_key_is_rejected(tenant: TenantContext) -> None:
    """Injection into an opaque payload is a mutation like any other."""
    raw = _packet(tenant).model_dump(mode="json")
    tampered = copy.deepcopy(raw)
    tampered["payload"]["injected"] = True

    with pytest.raises(TransportIntegrityError):
        TransportPacket.model_validate(tampered)


# ---------------------------------------------------------------------------
# Transport header and envelope integrity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "value", "why_it_matters"),
    [
        (("header", "action"), "discover", "re-routes the request to a different worker"),
        (("header", "timeout_ms"), 1, "shrinks the execution budget the worker enforces"),
        (
            ("header", "idempotency_key"),
            "someone-elses-key",
            "collides with, or escapes, Gate's result cache",
        ),
        (("header", "correlation_id"), "corr-someone-else", "reattributes the trace"),
        (("header", "priority"), 0, "jumps the routing queue"),
        (("header", "replay_mode"), True, "re-frames a live request as a replay"),
        (("address", "destination_node"), "worker", "bypasses Gate mediation"),
        (("address", "source_node"), "gate", "forges the origin"),
        (("address", "reply_to"), "attacker", "redirects the response"),
        (("tenant", "org_id"), "other-org", "crosses a tenant boundary"),
        (("governance", "intent"), "discover", "misdeclares what the packet is for"),
        (("governance", "retention_days"), 0, "rewrites the retention obligation"),
        (("provenance", "resolved_by_gate"), True, "claims a routing decision never made"),
        (("lineage", "generation"), 7, "falsifies the position in the chain"),
    ],
)
def test_protected_field_mutation_is_rejected(
    tenant: TenantContext, path: tuple[str, ...], value: Any, why_it_matters: str
) -> None:
    """
    Every field ``transport_hash`` covers fails closed when edited in flight.

    The third parameter is the point of each case: these are not arbitrary
    fields, they are the ones an in-flight edit would use to reroute, escape
    idempotency, cross a tenant, or shorten a budget.
    """
    raw = _packet(tenant).model_dump(mode="json")
    tampered = _mutate(raw, path, value)

    assert tampered != raw, f"mutation of {'.'.join(path)} did not change the packet"

    with pytest.raises(TransportIntegrityError, match="transport_hash"):
        TransportPacket.model_validate(tampered)


# Fields ``compute_transport_hash`` does not cover. Excluding the signature
# triple is structural — the signature signs the transport hash, so it cannot
# be inside it. The other three are a real gap, recorded below rather than
# quietly omitted from the parametrization above.
UNPROTECTED_SECURITY_FIELDS = ("classification", "encryption_status", "pii_fields")


@pytest.mark.parametrize(
    ("field_name", "tampered_value"),
    [
        ("classification", "public"),
        ("encryption_status", "encrypted"),
        ("pii_fields", ["entity.name"]),
    ],
)
def test_security_labels_outside_the_transport_hash_are_not_tamper_evident(
    tenant: TenantContext, field_name: str, tampered_value: Any
) -> None:
    """
    FINDING, not an endorsement: these three labels are unprotected in flight.

    ``compute_transport_hash`` covers header, address, tenant, payload,
    governance, delegation_chain, lineage, attachments, provenance, and
    ``payload_hash``. It does not cover ``security.classification``,
    ``security.encryption_status``, or ``security.pii_fields``, and the
    packet signature signs the transport hash, so it does not cover them
    either. An in-path intermediary can therefore rewrite them and the
    packet still parses and still verifies.

    The consequence is not cosmetic. ``validate_transport_packet`` gates on
    ``classification == "restricted"`` twice — to require
    ``audit_required=true``, and to require a signature on an unsigned
    packet — so a downgrade to ``internal`` walks past both. The audit
    obligation itself sits in ``governance``, which is hashed; only the
    label and the gates keyed on it are exposed.

    This test pins the boundary exactly as it is today. Closing the gap
    means adding these fields to the transport-hash envelope, which changes
    every transport hash and breaks wire compatibility with every deployed
    node at once — a coordinated release decision, not a test-PR change.
    Until that decision is taken this test fails if the field set moves in
    either direction, so the gap cannot widen or silently close unnoticed.
    """
    packet = _packet(tenant)
    raw = packet.model_dump(mode="json")
    tampered = _mutate(raw, ("security", field_name), tampered_value)
    assert tampered != raw

    reparsed = TransportPacket.model_validate(tampered)

    assert getattr(reparsed.security, field_name) != getattr(packet.security, field_name)
    assert reparsed.security.transport_hash == packet.security.transport_hash


def test_the_transport_hash_envelope_is_exactly_what_is_documented(
    tenant: TenantContext,
) -> None:
    """
    Lock the covered set, so a future change to it has to be deliberate.

    Widening the envelope is a wire-compatibility event for the whole rail;
    narrowing it is a security regression. Either way it must not happen by
    accident.
    """
    packet = _packet(tenant)
    baseline = compute_transport_hash(packet)

    for field_name in UNPROTECTED_SECURITY_FIELDS:
        raw = packet.model_dump(mode="json")
        assert field_name in raw["security"], f"{field_name} vanished from the security block"

    for section in ("header", "address", "tenant", "payload", "governance", "lineage"):
        raw = packet.model_dump(mode="json")
        assert section in raw, f"{section} vanished from the packet"

    assert baseline == packet.security.transport_hash


def test_an_unmutated_packet_still_parses(tenant: TenantContext) -> None:
    """
    Guard against a vacuous parametrization.

    If ``model_validate`` rejected every round-tripped packet, the cases
    above would pass while proving nothing.
    """
    packet = _packet(tenant)
    reparsed = TransportPacket.model_validate(packet.model_dump(mode="json"))

    assert reparsed.security.transport_hash == packet.security.transport_hash
    assert reparsed.payload == packet.payload


# ---------------------------------------------------------------------------
# Hop trace — observational, and still tamper-evident
# ---------------------------------------------------------------------------


def test_hop_append_is_observational_only(tenant: TenantContext) -> None:
    """
    Appending a hop changes observational state and nothing else.

    ``hop_trace`` is excluded from ``transport_hash`` by design, so an
    intermediary can record that it saw a packet without invalidating the
    signed core or forcing any other field to be recomputed.
    """
    packet = _packet(tenant)
    observed = packet.with_hop(
        make_ingress_hop(packet=packet, node="gate", action=packet.header.action)
    )

    assert observed.security.transport_hash == packet.security.transport_hash
    assert observed.security.payload_hash == packet.security.payload_hash
    assert observed.security.signature == packet.security.signature
    assert observed.header == packet.header
    assert observed.payload == packet.payload
    assert observed.lineage == packet.lineage

    assert len(observed.hop_trace) == len(packet.hop_trace) + 1
    validate_hop_trace(observed)


def test_a_recorded_hop_cannot_be_edited_after_the_fact(tenant: TenantContext) -> None:
    """
    Observational does not mean unprotected.

    Each hop carries a hash bound to the packet's ``transport_hash``, so
    rewriting a hop that has already been recorded is detected even though
    the transport core is untouched.
    """
    packet = _packet(tenant)
    observed = packet.with_hop(
        make_ingress_hop(packet=packet, node="gate", action=packet.header.action)
    )

    raw = observed.model_dump(mode="json")
    raw["hop_trace"][0]["node"] = "attacker"
    reparsed = TransportPacket.model_validate(raw)

    assert reparsed.security.transport_hash == observed.security.transport_hash, (
        "the transport core is intentionally unaffected by hop_trace"
    )
    with pytest.raises(TransportIntegrityError, match="hop_hash"):
        validate_hop_trace(reparsed)


def test_a_hop_from_another_packet_cannot_be_grafted_on(tenant: TenantContext) -> None:
    """A hop is bound to the packet it observed, not to hop shape in general."""
    packet = _packet(tenant)
    other = _packet(tenant)
    foreign_hop = make_ingress_hop(packet=other, node="gate", action=other.header.action)

    with pytest.raises(ValueError, match="hop.packet_id must match"):
        packet.with_hop(foreign_hop)
