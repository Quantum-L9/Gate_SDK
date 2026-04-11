from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from .errors import TransportIntegrityError
from .hashing import compute_payload_hash, compute_transport_hash
from .models import (
    DelegationLink,
    TransportAddress,
    TransportAttachment,
    TransportGovernance,
    TransportHeader,
    TransportHop,
    TransportLineage,
    TransportSecurity,
    utc_now,
)
from .provenance import RoutingProvenance
from .tenant import TenantContext, assert_tenant_immutable, ensure_tenant_context


class TransportPacket(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    header: TransportHeader
    address: TransportAddress
    tenant: TenantContext
    payload: dict[str, Any]
    security: TransportSecurity
    governance: TransportGovernance
    provenance: RoutingProvenance
    delegation_chain: tuple[DelegationLink, ...] = ()
    hop_trace: tuple[TransportHop, ...] = ()
    lineage: TransportLineage
    attachments: tuple[TransportAttachment, ...] = ()

    @field_validator("payload")
    @classmethod
    def validate_payload(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise TypeError("payload must be a dict")
        return value

    @model_validator(mode="after")
    def validate_integrity(self) -> "TransportPacket":
        expected_payload_hash = compute_payload_hash(self.payload)
        if self.security.payload_hash != expected_payload_hash:
            raise TransportIntegrityError("payload_hash does not match payload")

        expected_transport_hash = compute_transport_hash(self)
        if self.security.transport_hash != expected_transport_hash:
            raise TransportIntegrityError("transport_hash does not match packet")

        return self

    def model_dump_json_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict representation."""
        return self.model_dump(mode="json", exclude_none=True)

    def with_hop(self, hop: TransportHop) -> "TransportPacket":
        """
        Append a hop without mutating the signed transport core.

        hop_trace is intentionally excluded from transport_hash, so a hop append
        must preserve payload_hash, transport_hash, and any existing transport
        signature.
        """
        if hop.packet_id != self.header.packet_id:
            raise ValueError("hop.packet_id must match packet.header.packet_id")

        appended = self.model_copy(update={"hop_trace": self.hop_trace + (hop,)})
        return TransportPacket(
            header=appended.header,
            address=appended.address,
            tenant=appended.tenant,
            payload=appended.payload,
            security=appended.security,
            governance=appended.governance,
            provenance=appended.provenance,
            delegation_chain=tuple(appended.delegation_chain),
            hop_trace=tuple(appended.hop_trace),
            lineage=appended.lineage,
            attachments=tuple(appended.attachments),
        )

    def derive(
        self,
        *,
        packet_type: str | None = None,
        action: str | None = None,
        source_node: str | None = None,
        destination_node: str | None = None,
        reply_to: str | None = None,
        payload: dict[str, Any] | None = None,
        provenance: RoutingProvenance | None = None,
        hop: TransportHop | None = None,
        delegation_link: DelegationLink | None = None,
        governance: TransportGovernance | None = None,
        priority: int | None = None,
        expires_at: datetime | None = None,
        timeout_ms: int | None = None,
        not_before: datetime | None = None,
        replay_mode: bool | None = None,
    ) -> "TransportPacket":
        """Create an immutable child packet with correct lineage and refreshed hashes."""
        new_header = TransportHeader(
            packet_id=uuid4(),
            packet_type=packet_type or self.header.packet_type,
            action=action or self.header.action,
            priority=self.header.priority if priority is None else priority,
            created_at=utc_now(),
            expires_at=self.header.expires_at if expires_at is None else expires_at,
            timeout_ms=self.header.timeout_ms if timeout_ms is None else timeout_ms,
            schema_version=self.header.schema_version,
            idempotency_key=self.header.idempotency_key,
            trace_id=self.header.trace_id,
            correlation_id=self.header.correlation_id,
            causation_id=self.header.packet_id,
            retry_count=0,
            replay_mode=self.header.replay_mode if replay_mode is None else replay_mode,
            not_before=self.header.not_before if not_before is None else not_before,
        )

        new_address = TransportAddress(
            source_node=(source_node or self.address.source_node).strip().lower(),
            destination_node=(destination_node or self.address.destination_node).strip().lower(),
            reply_to=(reply_to or self.address.reply_to).strip().lower(),
        )

        new_payload = dict(self.payload if payload is None else payload)
        new_governance = governance or self.governance
        new_provenance = provenance or self.provenance
        new_delegation_chain = self.delegation_chain + ((delegation_link,) if delegation_link is not None else ())
        new_hop_trace = self.hop_trace + ((hop,) if hop is not None else ())
        new_lineage = TransportLineage(
            parent_id=self.header.packet_id,
            root_id=self.lineage.root_id,
            generation=self.lineage.generation + 1,
        )

        provisional = TransportPacket.model_construct(
            header=new_header,
            address=new_address,
            tenant=self.tenant,
            payload=new_payload,
            security=TransportSecurity(
                payload_hash="0" * 64,
                transport_hash="0" * 64,
                signature=None,
                signature_algorithm=None,
                signing_key_id=None,
                classification=self.security.classification,
                encryption_status=self.security.encryption_status,
                pii_fields=self.security.pii_fields,
            ),
            governance=new_governance,
            provenance=new_provenance,
            delegation_chain=new_delegation_chain,
            hop_trace=new_hop_trace,
            lineage=new_lineage,
            attachments=self.attachments,
        )
        child = _finalize_transport_packet(provisional, preserve_signature=False)
        assert_tenant_immutable(self.tenant, child.tenant)
        return child


def _default_provenance(*, source_node: str, action: str) -> RoutingProvenance:
    source = source_node.strip().lower()
    origin_kind = "client" if source == "client" else "gate" if source == "gate" else "node"
    return RoutingProvenance(
        origin_kind=origin_kind,
        requested_action=action.strip().lower(),
        resolved_by_gate=False,
        original_source_node=None if origin_kind == "client" else source,
    )


def _finalize_transport_packet(packet: TransportPacket, *, preserve_signature: bool) -> TransportPacket:
    payload_hash = compute_payload_hash(packet.payload)
    provisional = packet.model_copy(
        update={
            "security": packet.security.model_copy(
                update={
                    "payload_hash": payload_hash,
                    "transport_hash": "0" * 64,
                    "signature": packet.security.signature if preserve_signature else None,
                    "signature_algorithm": packet.security.signature_algorithm if preserve_signature else None,
                    "signing_key_id": packet.security.signing_key_id if preserve_signature else None,
                }
            )
        }
    )
    transport_hash = compute_transport_hash(provisional)
    signature_still_valid = (
        preserve_signature
        and packet.security.payload_hash == payload_hash
        and packet.security.transport_hash == transport_hash
    )

    finalized = provisional.model_copy(
        update={
            "security": provisional.security.model_copy(
                update={
                    "transport_hash": transport_hash,
                    "signature": packet.security.signature if signature_still_valid else None,
                    "signature_algorithm": packet.security.signature_algorithm if signature_still_valid else None,
                    "signing_key_id": packet.security.signing_key_id if signature_still_valid else None,
                }
            )
        }
    )
    return TransportPacket(
        header=finalized.header,
        address=finalized.address,
        tenant=finalized.tenant,
        payload=finalized.payload,
        security=finalized.security,
        governance=finalized.governance,
        provenance=finalized.provenance,
        delegation_chain=tuple(finalized.delegation_chain),
        hop_trace=tuple(finalized.hop_trace),
        lineage=finalized.lineage,
        attachments=tuple(finalized.attachments),
    )


def create_transport_packet(
    *,
    action: str,
    payload: dict[str, Any],
    tenant: str | dict[str, Any] | TenantContext,
    destination_node: str = "gate",
    source_node: str = "client",
    reply_to: str = "client",
    priority: int = 2,
    timeout_ms: int = 30_000,
    classification: str = "internal",
    compliance_tags: tuple[str, ...] = (),
    retention_days: int = 90,
    expires_at: datetime | None = None,
    idempotency_key: str | None = None,
    trace_id: str | None = None,
    correlation_id: str | None = None,
    provenance: RoutingProvenance | None = None,
) -> TransportPacket:
    """Build a canonical root transport packet with initialized lineage and hashes."""
    packet_id = uuid4()
    normalized_tenant = ensure_tenant_context(tenant)
    normalized_action = action.strip().lower()
    normalized_source = source_node.strip().lower()
    normalized_destination = destination_node.strip().lower()
    normalized_reply_to = reply_to.strip().lower()

    header = TransportHeader(
        packet_id=packet_id,
        packet_type="request",
        action=normalized_action,
        priority=priority,
        created_at=utc_now(),
        expires_at=expires_at,
        timeout_ms=timeout_ms,
        schema_version="1.0",
        idempotency_key=idempotency_key,
        trace_id=trace_id or str(packet_id),
        correlation_id=correlation_id or str(packet_id),
        causation_id=None,
        retry_count=0,
        replay_mode=False,
        not_before=None,
    )

    address = TransportAddress(
        source_node=normalized_source,
        destination_node=normalized_destination,
        reply_to=normalized_reply_to,
    )

    governance = TransportGovernance(
        intent=normalized_action,
        compliance_tags=compliance_tags,
        retention_days=retention_days,
        redaction_applied=False,
        audit_required=False,
        data_subject_id=None,
    )

    packet = TransportPacket.model_construct(
        header=header,
        address=address,
        tenant=normalized_tenant,
        payload=dict(payload),
        security=TransportSecurity(
            payload_hash="0" * 64,
            transport_hash="0" * 64,
            signature=None,
            signature_algorithm=None,
            signing_key_id=None,
            classification=classification,
            encryption_status="plaintext",
            pii_fields=(),
        ),
        governance=governance,
        provenance=provenance or _default_provenance(source_node=normalized_source, action=normalized_action),
        delegation_chain=(),
        hop_trace=(),
        lineage=TransportLineage(
            parent_id=None,
            root_id=packet_id,
            generation=0,
        ),
        attachments=(),
    )
    return _finalize_transport_packet(packet, preserve_signature=False)
