from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from constellation_node_sdk.gate.config import GateClientConfig
from constellation_node_sdk.runtime.config import NodeRuntimeConfig, get_runtime_config
from constellation_node_sdk.runtime.handlers import clear_handlers
from constellation_node_sdk.security.signing import sign_transport_packet
from constellation_node_sdk.transport.hop_trace import make_execution_hop, make_ingress_hop
from constellation_node_sdk.transport.models import DelegationLink
from constellation_node_sdk.transport.packet import TransportPacket, create_transport_packet
from constellation_node_sdk.transport.provenance import RoutingProvenance
from constellation_node_sdk.transport.tenant import TenantContext


def _ensure_src_on_path() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    src_path = repo_root / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))


_ensure_src_on_path()

# ---------------------------------------------------------------------------
# Env + registry isolation
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_handlers_after_each() -> Any:
    """Autouse: clear the handler registry after every test function."""
    yield
    clear_handlers()


@pytest.fixture(autouse=True, scope="session")
def _clear_runtime_config_cache() -> Any:
    """Autouse (session): clear lru_cache on NodeRuntimeConfig after full session."""
    yield
    get_runtime_config.cache_clear()


# ---------------------------------------------------------------------------
# Canonical tenant fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def tenant() -> TenantContext:
    """Canonical test TenantContext — immutable across derived packets (§10)."""
    return TenantContext(
        actor="test-actor",
        on_behalf_of="test-actor",
        originator="test-actor",
        org_id="test-org",
        user_id="test-user",
    )


# ---------------------------------------------------------------------------
# NodeRuntimeConfig fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def default_runtime_config() -> NodeRuntimeConfig:
    """Default NodeRuntimeConfig — dev_mode, no signing, all actions allowed."""
    return NodeRuntimeConfig(
        environment="local",
        node_name="test-node",
        service_name="test-service",
        service_version="0.0.1",
        dev_mode=True,
        require_signature=False,
        expose_internal_errors=True,
        return_transport_errors=True,
        signing_algorithm="hmac-sha256",
        signing_key=None,
        allowed_actions=(),
        allowed_packet_types=("request", "command", "delegation", "replay_request"),
        max_packet_bytes=262_144,
        max_attachments=0,
        max_attachment_size_bytes=0,
    )


@pytest.fixture()
def signed_runtime_config() -> NodeRuntimeConfig:
    """NodeRuntimeConfig with HMAC signing enabled — key = 'test-hmac-key-32-bytes-padded!!'."""
    return NodeRuntimeConfig(
        environment="local",
        node_name="test-node",
        service_name="test-service",
        service_version="0.0.1",
        dev_mode=False,
        require_signature=True,
        signing_algorithm="hmac-sha256",
        signing_key="test-hmac-key-32-bytes-padded!!",
        signing_key_id="test-key-id",
        verifying_keys={"test-key-id": "test-hmac-key-32-bytes-padded!!"},
        allowed_actions=(),
        allowed_packet_types=("request", "command", "delegation", "replay_request"),
        max_packet_bytes=262_144,
        max_attachments=0,
        max_attachment_size_bytes=0,
    )


# ---------------------------------------------------------------------------
# GateClientConfig fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def gate_client_config() -> GateClientConfig:
    """GateClientConfig pointed at a dummy Gate URL with local_node=test-node."""
    return GateClientConfig(
        gate_url="http://gate.test:8000",
        local_node="test-node",
        timeout_seconds=5.0,
        require_signature=False,
        allowed_gate_destination="gate",
    )


# ---------------------------------------------------------------------------
# Core packet builders
# ---------------------------------------------------------------------------


@pytest.fixture()
def valid_packet(tenant: TenantContext) -> TransportPacket:
    """
    A fully valid root TransportPacket.

    - Source: client → Gate (destination=gate)
    - No signature (unsigned)
    - Generation 0 / root lineage
    - Passes validate_transport_packet() with default config
    """
    return create_transport_packet(
        action="test-action",
        payload={"key": "value"},
        tenant=tenant,
        destination_node="gate",
        source_node="client",
        reply_to="client",
    )


@pytest.fixture()
def node_originated_packet(tenant: TenantContext) -> TransportPacket:
    """
    A valid TransportPacket originated from a node — passes GateClient outbound policy (§12).

    - origin_kind=node, source_node=test-node, destination_node=gate
    - Required for GateClient.send_to_gate() validation
    """
    provenance = RoutingProvenance(
        origin_kind="node",
        requested_action="test-action",
        resolved_by_gate=False,
        original_source_node="test-node",
    )
    return create_transport_packet(
        action="test-action",
        payload={"key": "value"},
        tenant=tenant,
        destination_node="gate",
        source_node="test-node",
        reply_to="test-node",
        provenance=provenance,
    )


@pytest.fixture()
def child_packet(valid_packet: TransportPacket) -> TransportPacket:
    """
    A valid child packet derived from valid_packet.

    - parent_id = valid_packet.header.packet_id
    - root_id = valid_packet.lineage.root_id
    - generation = 1
    - TenantContext preserved (assert_tenant_immutable enforced by derive())
    - destination_node="gate" — routing law: §12
    """
    return valid_packet.derive(
        action="child-action",
        source_node="test-node",
        destination_node="gate",  # routing law: §12
        payload={"child": "payload"},
    )


# ---------------------------------------------------------------------------
# Invalid / failure-mode packet builders
# ---------------------------------------------------------------------------


@pytest.fixture()
def invalid_hash_packet(tenant: TenantContext) -> dict[str, Any]:
    """
    A raw dict that represents a TransportPacket with a tampered payload_hash.

    Use TransportPacket.model_validate(invalid_hash_packet) to trigger TransportIntegrityError.
    Returned as dict because frozen TransportPacket cannot be mutated post-construction.
    """
    good = create_transport_packet(
        action="test-action",
        payload={"key": "value"},
        tenant=tenant,
        destination_node="gate",
        source_node="client",
        reply_to="client",
    )
    raw = good.model_dump(mode="json")
    raw["security"]["payload_hash"] = "a" * 64  # deliberate mismatch
    return raw


@pytest.fixture()
def invalid_transport_hash_packet(tenant: TenantContext) -> dict[str, Any]:
    """
    A raw dict with a correct payload_hash but tampered transport_hash.

    Use TransportPacket.model_validate(...) to trigger TransportIntegrityError.
    """
    good = create_transport_packet(
        action="test-action",
        payload={"key": "value"},
        tenant=tenant,
        destination_node="gate",
        source_node="client",
        reply_to="client",
    )
    raw = good.model_dump(mode="json")
    raw["security"]["transport_hash"] = "b" * 64  # deliberate mismatch
    return raw


@pytest.fixture()
def expired_packet(tenant: TenantContext) -> TransportPacket:
    """
    A TransportPacket whose expires_at is in the past.

    Passes structural validation at construction (created_at < expires_at is set correctly),
    but raises TransportExpiredError when validate_transport_packet() is called with now=utcnow.
    """
    past = datetime.now(UTC) - timedelta(seconds=120)
    return create_transport_packet(
        action="test-action",
        payload={"key": "value"},
        tenant=tenant,
        destination_node="gate",
        source_node="client",
        reply_to="client",
        expires_at=past,
    )


@pytest.fixture()
def not_yet_valid_packet(tenant: TenantContext) -> TransportPacket:
    """
    A TransportPacket with not_before set 10 minutes in the future.

    Raises TransportNotYetValidError when validate_transport_packet() is called with now=utcnow.
    """
    future = datetime.now(UTC) + timedelta(minutes=10)
    pkt = create_transport_packet(
        action="test-action",
        payload={"key": "value"},
        tenant=tenant,
        destination_node="gate",
        source_node="client",
        reply_to="client",
    )
    return pkt.derive(not_before=future)


@pytest.fixture()
def tenant_mutated_dict(valid_packet: TransportPacket) -> dict[str, Any]:
    """
    Raw dict of a packet where the tenant has been mutated post-derive.

    Demonstrates the TenantMutationError path (§10).
    Use assert_tenant_immutable(original_tenant, mutated_tenant) to trigger TenantMutationError.

    NOTE: derive() itself prevents tenant mutation — this fixture patches the raw dict
    to simulate a malformed inbound packet with a different tenant.
    """
    raw = valid_packet.model_dump(mode="json")
    raw["tenant"]["actor"] = "evil-actor"  # mutation
    raw["tenant"]["on_behalf_of"] = "evil-actor"
    raw["tenant"]["originator"] = "evil-actor"
    raw["tenant"]["org_id"] = "evil-org"
    return raw


@pytest.fixture()
def replay_packet(tenant: TenantContext) -> TransportPacket:
    """
    A valid replay_request TransportPacket.

    - packet_type=replay_request, replay_mode=True
    - Passes validate_transport_packet() with replay_enabled=True
    - Raises TransportAuthorizationError when replay_enabled=False
    """
    pkt = create_transport_packet(
        action="test-action",
        payload={"key": "value"},
        tenant=tenant,
        destination_node="gate",
        source_node="client",
        reply_to="client",
    )
    return pkt.derive(packet_type="replay_request", replay_mode=True)


@pytest.fixture()
def idempotency_packet(tenant: TenantContext) -> TransportPacket:
    """
    A TransportPacket with an idempotency_key set.

    Required when validate_transport_packet() is called with required_idempotency_actions
    containing 'test-action'.
    """
    return create_transport_packet(
        action="test-action",
        payload={"key": "value"},
        tenant=tenant,
        destination_node="gate",
        source_node="client",
        reply_to="client",
        idempotency_key=str(uuid4()),
    )


@pytest.fixture()
def delegation_chain_packet(tenant: TenantContext) -> TransportPacket:
    """
    A delegation packet with a single valid DelegationLink.

    - packet_type=delegation
    - delegation_chain has one link: delegator=gate → delegatee=test-node, scope=[test-action]
    - destination_node matches delegatee (validation law: §12)
    """
    link = DelegationLink(
        delegator="gate",
        delegatee="test-node",
        scope=("test-action",),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    pkt = create_transport_packet(
        action="test-action",
        payload={"key": "value"},
        tenant=tenant,
        destination_node="gate",
        source_node="gate",
        reply_to="client",
    )
    return pkt.derive(
        packet_type="delegation",
        destination_node="test-node",
        delegation_link=link,
    )


@pytest.fixture()
def signed_packet(tenant: TenantContext) -> TransportPacket:
    """
    A valid HMAC-SHA256 signed TransportPacket.

    - signature_algorithm=hmac-sha256, signing_key_id=test-key-id
    - Verifiable with key 'test-hmac-key-32-bytes-padded!!'
    - Passes verify_transport_packet_signature() with correct key
    """
    unsigned = create_transport_packet(
        action="test-action",
        payload={"key": "value"},
        tenant=tenant,
        destination_node="gate",
        source_node="client",
        reply_to="client",
    )
    return sign_transport_packet(
        unsigned,
        key="test-hmac-key-32-bytes-padded!!",
        key_id="test-key-id",
        algorithm="hmac-sha256",
    )


@pytest.fixture()
def oversized_packet(tenant: TenantContext) -> TransportPacket:
    """
    A TransportPacket whose payload pushes serialized size > 1024 bytes.

    Use validate_transport_packet(..., max_packet_bytes=1024) to trigger PacketSizeError.
    The fixture itself is valid at default max_packet_bytes=262144.
    """
    large_payload = {"data": "x" * 2048}
    return create_transport_packet(
        action="test-action",
        payload=large_payload,
        tenant=tenant,
        destination_node="gate",
        source_node="client",
        reply_to="client",
    )


@pytest.fixture()
def packet_with_execution_hop(valid_packet: TransportPacket) -> TransportPacket:
    """
    A valid TransportPacket with one execution hop appended.

    - hop direction=execution, status=processing
    - transport_hash is UNCHANGED (hop_trace excluded from transport_hash — §9)
    - previous_hop_hash=None (first hop)
    """
    hop = make_execution_hop(
        packet=valid_packet,
        node="test-node",
        action=valid_packet.header.action,
        status="processing",
    )
    return valid_packet.with_hop(hop)


@pytest.fixture()
def packet_with_hop_chain(valid_packet: TransportPacket) -> TransportPacket:
    """
    A valid TransportPacket with ingress → execution hop chain.

    - Hop chain continuity enforced: hop[1].previous_hop_hash == hop[0].hop_hash
    - transport_hash unchanged across both hop appends (§9)
    """
    ingress_hop = make_ingress_hop(
        packet=valid_packet,
        node="test-node",
        action=valid_packet.header.action,
        status="validated",
    )
    with_ingress = valid_packet.with_hop(ingress_hop)
    execution_hop = make_execution_hop(
        packet=with_ingress,
        node="test-node",
        action=valid_packet.header.action,
        status="processing",
    )
    return with_ingress.with_hop(execution_hop)


@pytest.fixture()
def gdpr_packet(tenant: TenantContext) -> TransportPacket:
    """
    A TransportPacket with GDPR compliance tag and data_subject_id.

    - governance.compliance_tags = ('GDPR',)
    - governance.data_subject_id = 'user-123'
    - Passes GDPR validation gate in validate_transport_packet() (§10)
    """
    return create_transport_packet(
        action="test-action",
        payload={"key": "value"},
        tenant=tenant,
        destination_node="gate",
        source_node="client",
        reply_to="client",
        compliance_tags=("GDPR",),
    )


@pytest.fixture()
def restricted_packet(tenant: TenantContext) -> TransportPacket:
    """
    A TransportPacket with classification=restricted and audit_required=True.

    - security.classification=restricted
    - governance.audit_required=True
    - Passes restricted classification gate in validate_transport_packet() (§10)
    """
    from constellation_node_sdk.transport.hashing import (
        compute_payload_hash,
        compute_transport_hash,
    )

    pkt = create_transport_packet(
        action="test-action",
        payload={"key": "value"},
        tenant=tenant,
        destination_node="gate",
        source_node="client",
        reply_to="client",
        classification="restricted",
    )
    updated_governance = pkt.governance.model_copy(update={"audit_required": True})
    provisional = pkt.model_copy(
        update={
            "governance": updated_governance,
            "security": pkt.security.model_copy(
                update={"payload_hash": "0" * 64, "transport_hash": "0" * 64}
            ),
        }
    )
    payload_hash = compute_payload_hash(provisional.payload)
    with_payload_hash = provisional.model_copy(
        update={"security": provisional.security.model_copy(update={"payload_hash": payload_hash})}
    )
    transport_hash = compute_transport_hash(with_payload_hash)
    finalized = with_payload_hash.model_copy(
        update={
            "security": with_payload_hash.security.model_copy(
                update={"transport_hash": transport_hash}
            )
        }
    )
    return TransportPacket.model_validate(finalized)
