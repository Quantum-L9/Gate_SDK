from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from constellation_node_sdk.security.signing import recompute_transport_core, sign_transport_packet
from constellation_node_sdk.security.validation import (
    validate_derived_transport_packet,
    validate_transport_packet,
)
from constellation_node_sdk.transport.errors import (
    PacketSizeError,
    SchemaVersionError,
    TenantMutationError,
    TransportAuthorizationError,
    TransportExpiredError,
    TransportIntegrityError,
    TransportNotYetValidError,
    TransportValidationError,
)
from constellation_node_sdk.transport.models import DelegationLink, TransportAttachment
from constellation_node_sdk.transport.packet import create_transport_packet


def _packet(**overrides):
    defaults = dict(
        action="score",
        payload={"entity_id": "42"},
        tenant="tenant-a",
        destination_node="gate",
        source_node="client",
        reply_to="client",
    )
    defaults.update(overrides)
    return create_transport_packet(**defaults)


def _with_attachments(packet, attachments):
    updated = packet.model_copy(update={"attachments": tuple(attachments)})
    return recompute_transport_core(updated)


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


def _with_header(packet, **overrides):
    new_header = packet.header.model_copy(update=overrides)
    return packet.model_copy(update={"header": new_header})


def _attachment(uri: str, size_bytes: int = 100) -> TransportAttachment:
    return TransportAttachment(
        media_type="application/octet-stream",
        uri=uri,
        content_hash="a" * 64,
        size_bytes=size_bytes,
    )


def test_validate_transport_packet_rejects_unsupported_schema_version() -> None:
    packet = _with_header(_packet(), schema_version="2.0")

    with pytest.raises(SchemaVersionError, match="unsupported schema_version"):
        validate_transport_packet(packet, dev_mode=True)


def test_validate_transport_packet_rejects_disallowed_packet_type() -> None:
    packet = _packet()

    with pytest.raises(TransportAuthorizationError, match="packet_type not allowed"):
        validate_transport_packet(packet, dev_mode=True, allowed_packet_types=("response",))


def test_validate_transport_packet_rejects_disallowed_action() -> None:
    packet = _packet(action="score")

    with pytest.raises(TransportAuthorizationError, match="action not allowed"):
        validate_transport_packet(packet, dev_mode=True, allowed_actions=("enrich",))


def test_validate_transport_packet_rejects_clock_skew_beyond_allowance() -> None:
    packet = _with_header(_packet(), created_at=datetime.now(UTC) + timedelta(minutes=5))

    with pytest.raises(TransportValidationError, match="clock skew"):
        validate_transport_packet(packet, dev_mode=True, allowed_clock_skew_seconds=30)


def test_validate_transport_packet_rejects_oversized_packet() -> None:
    packet = _packet()

    with pytest.raises(PacketSizeError, match="exceeds maximum allowed size"):
        validate_transport_packet(packet, dev_mode=True, max_packet_bytes=10)


def test_validate_transport_packet_rejects_hop_trace_exceeding_max_depth() -> None:
    from constellation_node_sdk.transport.hop_trace import make_dispatch_hop, make_ingress_hop

    packet = _packet()
    packet = packet.with_hop(make_ingress_hop(packet=packet, node="gate", action="score"))
    packet = packet.with_hop(
        make_dispatch_hop(packet=packet, node="gate", action="score", target_node="worker")
    )

    with pytest.raises(TransportValidationError, match="hop trace exceeds maximum depth"):
        validate_transport_packet(packet, dev_mode=True, max_hop_depth=1)


def test_validate_transport_packet_rejects_delegation_chain_exceeding_max_depth() -> None:
    packet = _packet(action="enrich")
    now = datetime.now(UTC)
    link1 = DelegationLink(delegator="gate", delegatee="mid", scope=("enrich",), granted_at=now)
    child = packet.derive(delegation_link=link1, destination_node="mid")
    link2 = DelegationLink(delegator="mid", delegatee="worker", scope=("enrich",), granted_at=now)
    child2 = child.derive(delegation_link=link2, destination_node="worker")

    with pytest.raises(TransportValidationError, match="delegation chain exceeds maximum depth"):
        validate_transport_packet(child2, dev_mode=True, max_delegation_depth=1)


def test_validate_transport_packet_rejects_too_many_attachments() -> None:
    packet = _with_attachments(
        _packet(),
        [_attachment("https://cdn.example.com/f1"), _attachment("https://cdn.example.com/f2")],
    )

    with pytest.raises(TransportValidationError, match="attachment count exceeds maximum"):
        validate_transport_packet(packet, dev_mode=True, max_attachments=1)


def test_validate_transport_packet_rejects_oversized_attachment() -> None:
    packet = _with_attachments(
        _packet(), [_attachment("https://cdn.example.com/f1", size_bytes=1000)]
    )

    with pytest.raises(TransportValidationError, match="attachment exceeds maximum allowed size"):
        validate_transport_packet(packet, dev_mode=True, max_attachment_size_bytes=10)


def test_validate_transport_packet_rejects_attachment_uri_without_scheme() -> None:
    packet = _with_attachments(_packet(), [_attachment("no-scheme-uri")])

    with pytest.raises(TransportValidationError, match="must include a scheme"):
        validate_transport_packet(packet, dev_mode=True)


def test_validate_transport_packet_rejects_disallowed_attachment_scheme() -> None:
    packet = _with_attachments(_packet(), [_attachment("s3://bucket/key")])

    with pytest.raises(TransportValidationError, match="attachment scheme not allowed"):
        validate_transport_packet(packet, dev_mode=True, allowed_attachment_schemes=("https",))


@pytest.mark.parametrize("scheme", ["file", "ftp"])
def test_validate_transport_packet_rejects_file_and_ftp_attachment_schemes(scheme: str) -> None:
    packet = _with_attachments(_packet(), [_attachment(f"{scheme}:///etc/passwd")])

    with pytest.raises(TransportValidationError, match="attachment scheme not allowed"):
        validate_transport_packet(packet, dev_mode=True)


def test_validate_transport_packet_rejects_http_attachment_without_host() -> None:
    packet = _with_attachments(_packet(), [_attachment("https:///path-only")])

    with pytest.raises(TransportValidationError, match="must include a host"):
        validate_transport_packet(packet, dev_mode=True)


@pytest.mark.parametrize("host", ["localhost", "metadata.google.internal"])
def test_validate_transport_packet_rejects_blocked_attachment_hosts(host: str) -> None:
    packet = _with_attachments(_packet(), [_attachment(f"https://{host}/path")])

    with pytest.raises(TransportValidationError, match="attachment host not allowed"):
        validate_transport_packet(packet, dev_mode=True)


def test_validate_transport_packet_rejects_private_ip_attachment_host_by_default() -> None:
    packet = _with_attachments(_packet(), [_attachment("https://10.0.0.5/path")])

    with pytest.raises(TransportValidationError, match="private attachment hosts are not allowed"):
        validate_transport_packet(packet, dev_mode=True)


def test_validate_transport_packet_allows_private_ip_attachment_host_when_enabled() -> None:
    packet = _with_attachments(_packet(), [_attachment("https://10.0.0.5/path")])

    validate_transport_packet(packet, dev_mode=True, allow_private_attachment_hosts=True)


def test_validate_transport_packet_allows_public_ip_attachment_host() -> None:
    packet = _with_attachments(_packet(), [_attachment("https://8.8.8.8/path")])

    validate_transport_packet(packet, dev_mode=True)


def test_validate_transport_packet_allows_valid_public_hostname_attachment() -> None:
    packet = _with_attachments(_packet(), [_attachment("https://cdn.example.com/path")])

    validate_transport_packet(packet, dev_mode=True)


def test_validate_transport_packet_rejects_missing_hashes() -> None:
    packet = _packet()
    tampered_security = packet.security.model_copy(update={"payload_hash": ""})
    tampered = packet.model_copy(update={"security": tampered_security})

    with pytest.raises(TransportIntegrityError, match="packet hashes are missing"):
        validate_transport_packet(tampered, dev_mode=True)


def test_validate_transport_packet_rejects_not_yet_valid_packet() -> None:
    now = datetime.now(UTC)
    packet = _with_header(_packet(), not_before=now + timedelta(hours=1))

    with pytest.raises(TransportNotYetValidError, match="not valid yet"):
        validate_transport_packet(packet, dev_mode=True, now=now)


def test_validate_transport_packet_rejects_expired_packet() -> None:
    now = datetime.now(UTC)
    packet = _with_header(_packet(), expires_at=now - timedelta(hours=1))

    with pytest.raises(TransportExpiredError, match="TTL exceeded"):
        validate_transport_packet(packet, dev_mode=True, now=now)


def test_validate_transport_packet_replay_request_requires_replay_mode_flag() -> None:
    packet = _with_header(_packet(), packet_type="replay_request", replay_mode=False)

    with pytest.raises(TransportValidationError, match="replay_mode=true"):
        validate_transport_packet(packet, dev_mode=True)


def test_validate_transport_packet_rejects_replay_when_disabled() -> None:
    packet = _with_header(_packet(), packet_type="replay_request", replay_mode=True)

    with pytest.raises(TransportAuthorizationError, match="replay is disabled"):
        validate_transport_packet(packet, dev_mode=True, replay_enabled=False)


def test_validate_transport_packet_rejects_replay_mode_outside_replay_request() -> None:
    packet = _with_header(_packet(), replay_mode=True)

    with pytest.raises(TransportValidationError, match="replay_mode is only permitted"):
        validate_transport_packet(packet, dev_mode=True)


def test_validate_transport_packet_rejects_gdpr_tag_without_data_subject_id() -> None:
    packet = _packet(compliance_tags=("GDPR",))

    with pytest.raises(TransportValidationError, match="GDPR packets require data_subject_id"):
        validate_transport_packet(packet, dev_mode=True)


def test_validate_transport_packet_rejects_restricted_classification_without_audit_flag() -> None:
    packet = _packet(classification="restricted")

    with pytest.raises(TransportValidationError, match="audit_required=true"):
        validate_transport_packet(packet, dev_mode=True)


def test_validate_transport_packet_rejects_delegation_scope_escalation() -> None:
    packet = _packet(action="enrich")
    now = datetime.now(UTC)
    link1 = DelegationLink(
        delegator="gate", delegatee="mid", scope=("enrich", "score"), granted_at=now
    )
    child = packet.derive(delegation_link=link1, destination_node="mid")
    link2 = DelegationLink(delegator="mid", delegatee="worker", scope=("report",), granted_at=now)
    child2 = child.derive(delegation_link=link2, destination_node="worker")

    with pytest.raises(TransportAuthorizationError, match="delegation scope escalation"):
        validate_transport_packet(child2, dev_mode=True)


def test_validate_transport_packet_rejects_expired_delegation_link() -> None:
    packet = _packet(action="enrich")
    now = datetime.now(UTC)
    granted_at = now - timedelta(days=2)
    expires_at = granted_at + timedelta(hours=1)
    link = DelegationLink(
        delegator="gate",
        delegatee="worker",
        scope=("enrich",),
        granted_at=granted_at,
        expires_at=expires_at,
    )
    child = packet.derive(delegation_link=link, destination_node="worker")

    with pytest.raises(TransportAuthorizationError, match="delegation link expired"):
        validate_transport_packet(child, dev_mode=True, now=now)


def test_validate_transport_packet_rejects_delegated_action_outside_last_link_scope() -> None:
    packet = _packet(action="enrich")
    now = datetime.now(UTC)
    link = DelegationLink(delegator="gate", delegatee="worker", scope=("enrich",), granted_at=now)
    child = packet.derive(
        packet_type="delegation",
        action="report",
        destination_node="worker",
        delegation_link=link,
    )

    with pytest.raises(TransportAuthorizationError, match="action not permitted"):
        validate_transport_packet(child, dev_mode=True)


def test_validate_transport_packet_rejects_delegated_destination_mismatch() -> None:
    packet = _packet(action="enrich")
    now = datetime.now(UTC)
    link = DelegationLink(delegator="gate", delegatee="worker", scope=("enrich",), granted_at=now)
    child = packet.derive(
        packet_type="delegation",
        action="enrich",
        destination_node="other-node",
        delegation_link=link,
    )

    with pytest.raises(TransportAuthorizationError, match="destination does not match"):
        validate_transport_packet(child, dev_mode=True)


def test_validate_derived_transport_packet_accepts_valid_child() -> None:
    parent = _packet()
    child = parent.derive(action="enrich")

    validate_derived_transport_packet(parent, child)


def test_validate_derived_transport_packet_rejects_tenant_mutation() -> None:
    parent = _packet()
    child = parent.derive(action="enrich")
    mutated_tenant = child.tenant.model_copy(update={"org_id": "different-org"})
    mutated_child = child.model_copy(update={"tenant": mutated_tenant})

    with pytest.raises(TenantMutationError):
        validate_derived_transport_packet(parent, mutated_child)


def test_validate_derived_transport_packet_rejects_parent_id_mismatch() -> None:
    parent = _packet()
    other_parent = _packet()
    child = parent.derive(action="enrich")
    mutated_lineage = child.lineage.model_copy(update={"parent_id": other_parent.header.packet_id})
    mutated_child = child.model_copy(update={"lineage": mutated_lineage})

    with pytest.raises(TransportValidationError, match="parent_id mismatch"):
        validate_derived_transport_packet(parent, mutated_child)


def test_validate_derived_transport_packet_rejects_root_id_mismatch() -> None:
    parent = _packet()
    other_root = _packet()
    child = parent.derive(action="enrich")
    mutated_lineage = child.lineage.model_copy(update={"root_id": other_root.lineage.root_id})
    mutated_child = child.model_copy(update={"lineage": mutated_lineage})

    with pytest.raises(TransportValidationError, match="root_id mismatch"):
        validate_derived_transport_packet(parent, mutated_child)


def test_validate_derived_transport_packet_rejects_non_incrementing_generation() -> None:
    parent = _packet()
    child = parent.derive(action="enrich")
    mutated_lineage = child.lineage.model_copy(update={"generation": child.lineage.generation + 5})
    mutated_child = child.model_copy(update={"lineage": mutated_lineage})

    with pytest.raises(TransportValidationError, match="generation must increment by 1"):
        validate_derived_transport_packet(parent, mutated_child)
