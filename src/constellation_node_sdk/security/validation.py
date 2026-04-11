from __future__ import annotations

import ipaddress
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from constellation_node_sdk.security.verification import verify_transport_packet_signature
from constellation_node_sdk.transport.errors import (
    PacketSizeError,
    SchemaVersionError,
    TenantMutationError,
    TransportAuthenticationError,
    TransportAuthorizationError,
    TransportExpiredError,
    TransportIntegrityError,
    TransportNotYetValidError,
    TransportValidationError,
)
from constellation_node_sdk.transport.hop_trace import validate_hop_trace
from constellation_node_sdk.transport.packet import TransportPacket
from constellation_node_sdk.transport.tenant import assert_tenant_immutable


def transport_packet_size_bytes(packet: TransportPacket) -> int:
    return len(
        json.dumps(
            packet.model_dump_json_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    )


def validate_derived_transport_packet(parent: TransportPacket, child: TransportPacket) -> None:
    try:
        assert_tenant_immutable(parent.tenant, child.tenant)
    except TenantMutationError:
        raise
    except Exception as exc:
        raise TenantMutationError(str(exc)) from exc

    if child.lineage.parent_id != parent.header.packet_id:
        raise TransportValidationError("derived packet parent_id mismatch")
    if child.lineage.root_id != parent.lineage.root_id:
        raise TransportValidationError("derived packet root_id mismatch")
    if child.lineage.generation != parent.lineage.generation + 1:
        raise TransportValidationError("derived packet generation must increment by 1")


def validate_transport_packet(
    packet: TransportPacket,
    *,
    key_resolver: Callable[[str | None], str | bytes | None]
    | Mapping[str, str | bytes]
    | str
    | bytes
    | None = None,
    require_signature: bool = False,
    max_packet_bytes: int = 262_144,
    max_hop_depth: int = 64,
    max_delegation_depth: int = 8,
    max_attachments: int = 32,
    max_attachment_size_bytes: int = 10_485_760,
    allowed_attachment_schemes: tuple[str, ...] = (),
    allow_private_attachment_hosts: bool = False,
    allowed_clock_skew_seconds: int = 30,
    local_node: str | None = None,
    allowed_actions: tuple[str, ...] | None = None,
    allowed_packet_types: tuple[str, ...] | None = None,
    required_idempotency_actions: tuple[str, ...] | None = None,
    replay_enabled: bool = True,
    now: datetime | None = None,
    dev_mode: bool = False,
    verify_hop_signatures: bool = False,
    hop_key_resolver: Callable[[str | None], str | bytes | None]
    | Mapping[str, str | bytes]
    | str
    | bytes
    | None = None,
    require_monotonic_hop_timestamps: bool = True,
) -> None:
    current_time = (now or datetime.now(UTC)).astimezone(UTC)

    if packet.header.schema_version != "1.0":
        raise SchemaVersionError(f"unsupported schema_version: {packet.header.schema_version}")

    if allowed_packet_types is not None and packet.header.packet_type not in set(allowed_packet_types):
        raise TransportAuthorizationError(f"packet_type not allowed: {packet.header.packet_type}")

    if allowed_actions is not None and packet.header.action not in set(allowed_actions):
        raise TransportAuthorizationError(f"action not allowed: {packet.header.action}")

    if local_node is not None and packet.address.destination_node != local_node.strip().lower():
        raise TransportAuthorizationError("packet destination does not match this node")

    if packet.header.created_at > current_time + timedelta(seconds=allowed_clock_skew_seconds):
        raise TransportValidationError("packet created_at exceeds allowed clock skew")

    if transport_packet_size_bytes(packet) > max_packet_bytes:
        raise PacketSizeError("packet exceeds maximum allowed size")

    if len(packet.hop_trace) > max_hop_depth:
        raise TransportValidationError("hop trace exceeds maximum depth")

    if len(packet.delegation_chain) > max_delegation_depth:
        raise TransportValidationError("delegation chain exceeds maximum depth")

    if len(packet.attachments) > max_attachments:
        raise TransportValidationError("attachment count exceeds maximum")

    for attachment in packet.attachments:
        if attachment.size_bytes > max_attachment_size_bytes:
            raise TransportValidationError("attachment exceeds maximum allowed size")
        _validate_attachment_uri(
            attachment.uri,
            allowed_schemes=allowed_attachment_schemes,
            allow_private_hosts=allow_private_attachment_hosts,
        )

    if not packet.security.payload_hash or not packet.security.transport_hash:
        raise TransportIntegrityError("packet hashes are missing")

    if packet.header.not_before is not None and current_time < packet.header.not_before:
        raise TransportNotYetValidError("packet not valid yet")

    if packet.header.expires_at is not None and current_time > packet.header.expires_at:
        raise TransportExpiredError("packet TTL exceeded")

    if packet.header.packet_type == "replay_request":
        if not packet.header.replay_mode:
            raise TransportValidationError("replay_request packets must set replay_mode=true")
        if not replay_enabled:
            raise TransportAuthorizationError("replay is disabled on this service")

    if packet.header.replay_mode and packet.header.packet_type != "replay_request":
        raise TransportValidationError("replay_mode is only permitted for replay_request packets")

    if required_idempotency_actions and packet.header.action in set(required_idempotency_actions):
        if not packet.header.idempotency_key:
            raise TransportValidationError("idempotency_key required for this action")

    if "GDPR" in packet.governance.compliance_tags and not packet.governance.data_subject_id:
        raise TransportValidationError("GDPR packets require data_subject_id")

    if packet.security.classification == "restricted" and packet.governance.audit_required is not True:
        raise TransportValidationError("restricted packets must set audit_required=true")

    _validate_delegation_chain(packet, now=current_time)

    if packet.security.signature is None:
        if (require_signature or packet.security.classification == "restricted") and not dev_mode:
            raise TransportAuthenticationError("signature required but not present")
    else:
        if not (dev_mode and key_resolver is None):
            if not verify_transport_packet_signature(packet, key_resolver=key_resolver):
                raise TransportAuthenticationError("invalid transport signature")

    validate_hop_trace(
        packet,
        verify_hop_signatures=verify_hop_signatures,
        hop_key_resolver=hop_key_resolver,
        require_monotonic_timestamps=require_monotonic_hop_timestamps,
    )


def _validate_delegation_chain(packet: TransportPacket, *, now: datetime) -> None:
    previous_scope: set[str] | None = None

    for index, link in enumerate(packet.delegation_chain):
        current_scope = set(link.scope)

        if previous_scope is not None and not current_scope.issubset(previous_scope):
            raise TransportAuthorizationError("delegation scope escalation detected")

        if link.expires_at is not None and now > link.expires_at.astimezone(UTC):
            raise TransportAuthorizationError("delegation link expired")

        if index == len(packet.delegation_chain) - 1 and packet.header.packet_type == "delegation":
            if packet.header.action not in current_scope:
                raise TransportAuthorizationError("delegated packet action not permitted by last delegation scope")
            if packet.address.destination_node != link.delegatee:
                raise TransportAuthorizationError("delegated packet destination does not match last delegation target")

        previous_scope = current_scope


def _validate_attachment_uri(uri: str, *, allowed_schemes: tuple[str, ...], allow_private_hosts: bool) -> None:
    parsed = urlparse(uri)
    scheme = parsed.scheme.lower()

    if not scheme:
        raise TransportValidationError("attachment uri must include a scheme")
    if allowed_schemes and scheme not in set(allowed_schemes):
        raise TransportValidationError(f"attachment scheme not allowed: {scheme}")
    if scheme in {"file", "ftp"}:
        raise TransportValidationError(f"attachment scheme not allowed: {scheme}")

    hostname = parsed.hostname
    if hostname is None:
        if scheme in {"https", "http"}:
            raise TransportValidationError("http(s) attachment uri must include a host")
        return

    normalized_host = hostname.lower()
    if normalized_host in {"localhost", "metadata.google.internal"}:
        raise TransportValidationError("attachment host not allowed")

    try:
        host_ip = ipaddress.ip_address(normalized_host)
    except ValueError:
        return

    if not allow_private_hosts and (host_ip.is_private or host_ip.is_loopback or host_ip.is_link_local):
        raise TransportValidationError("private attachment hosts are not allowed")
