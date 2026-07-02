"""
Canonical worker node template for constellation-node-sdk.

This file is a complete, executable reference implementation. Copy, rename, and extend it
to build any new worker node. Every routing law, tenant law, and contract rule is
enforced and annotated inline.

Acceptance gates:
  ruff check examples/worker_node/worker_node_template.py
  mypy examples/worker_node/worker_node_template.py
  python -c "import examples.worker_node.worker_node_template"
  grep -n 'destination_node' examples/worker_node/worker_node_template.py | grep gate
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI

from constellation_node_sdk.gate.client import GateClient
from constellation_node_sdk.gate.config import GateClientConfig, get_gate_client_config_from_env
from constellation_node_sdk.runtime.app import create_node_app
from constellation_node_sdk.runtime.config import NodeRuntimeConfig, get_runtime_config
from constellation_node_sdk.runtime.handlers import register_handler
from constellation_node_sdk.runtime.lifecycle import LifecycleHook
from constellation_node_sdk.security.signing import sign_transport_packet
from constellation_node_sdk.transport.hop_trace import make_execution_hop
from constellation_node_sdk.transport.packet import TransportPacket

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 1. Handler registration
# ---------------------------------------------------------------------------
# action name MUST match regex ^[a-z0-9][a-z0-9-]{0,63}$ — models.py §9
# 3-param signature: (org_id: str, payload: dict, packet: TransportPacket)
# Handler MUST return dict or TransportPacket — runtime/execution.py §7


@register_handler("my-worker-action")
async def handle_my_worker_action(
    org_id: str,
    payload: dict[str, Any],
    packet: TransportPacket,
) -> dict[str, Any]:
    """
    Process inbound transport packet and return result payload.

    Rules enforced here:
    - MUST NOT mutate packet fields (frozen=True on all models — §19)
    - MUST NOT change tenant context (assert_tenant_immutable enforced by derive() — §10)
    - MUST route follow-up traffic through Gate only — routing law §12
    - MUST return dict or TransportPacket — runtime/execution.py
    """
    logger.info("handling action", extra={"action": packet.header.action, "org_id": org_id})

    result = _do_work(payload)

    if result.get("needs_followup"):
        await _send_followup_to_gate(packet, followup_payload=result)

    return {"status": "completed", "result": result}


def _do_work(payload: dict[str, Any]) -> dict[str, Any]:
    """Pure, stateless work function. MUST NOT mutate packet or tenant."""
    return {"processed": True, "input_keys": list(payload.keys())}


async def _send_followup_to_gate(
    inbound_packet: TransportPacket,
    followup_payload: dict[str, Any],
) -> TransportPacket:
    """
    Derive a child packet and send it to Gate.

    Routing law §12: ALL node-originated follow-up traffic MUST target Gate.
    MUST NOT target peer nodes directly.
    destination_node MUST always be "gate" (or allowed_gate_destination from config).
    """
    config: NodeRuntimeConfig = get_runtime_config()
    gate_config: GateClientConfig = get_gate_client_config_from_env()
    client = GateClient(gate_config)

    # derive() preserves TenantContext immutably — tenant law §10
    # destination_node="gate" — routing law §12: MUST NOT target peer nodes
    child = inbound_packet.derive(
        action="followup-action",
        source_node=config.node_name,
        destination_node="gate",  # routing law: §12 — MUST equal "gate" or allowed_gate_destination
        payload=followup_payload,
    )

    # Append execution hop before dispatching (audit trail — §9)
    exec_hop = make_execution_hop(
        packet=child,
        node=config.node_name,
        action=child.header.action,
        status="processing",
    )
    child_with_hop = child.with_hop(exec_hop)

    # Sign if signing key is configured — security §10
    if config.signing_key and config.signing_key_id:
        child_with_hop = sign_transport_packet(
            child_with_hop,
            key=config.signing_key,
            key_id=config.signing_key_id,
            algorithm=config.signing_algorithm,
        )

    return await client.send_to_gate(child_with_hop)


# ---------------------------------------------------------------------------
# 2. Lifecycle hook
# ---------------------------------------------------------------------------
# LifecycleHook ABC requires both startup() and shutdown() — runtime/lifecycle.py §8
# Inject via create_node_app(lifecycle_hook=...) — §8


class WorkerNodeLifecycle(LifecycleHook):
    """
    Startup and shutdown hooks for this worker node.

    startup(): initialize external connections, caches, warm-up logic.
    shutdown(): graceful drain, close connections, flush state.
    """

    async def startup(self) -> None:
        logger.info("worker node starting up")

    async def shutdown(self) -> None:
        logger.info("worker node shutting down")


# ---------------------------------------------------------------------------
# 3. App factory
# ---------------------------------------------------------------------------
# create_node_app returns FastAPI with /v1/execute, /v1/health, /metrics — §7
# auto_register_with_gate=True will POST to Gate /v1/admin/register on startup
# using GATE_NODE_SPEC_PATH spec.yaml — §17


def build_app(
    config: NodeRuntimeConfig | None = None,
    auto_register: bool = True,
) -> FastAPI:
    """
    Build the FastAPI application for this worker node.

    config: Override with explicit NodeRuntimeConfig for testing.
            Defaults to get_runtime_config() (reads from env — §17).
    auto_register: Set False in tests to skip Gate registration on startup.
    """
    resolved_config = config or get_runtime_config()

    return create_node_app(
        lifecycle_hook=WorkerNodeLifecycle(),
        config=resolved_config,
        auto_register_with_gate=auto_register,
    )


# ---------------------------------------------------------------------------
# 4. ASGI app entry point
# ---------------------------------------------------------------------------
# Used by: uvicorn examples.worker_node.worker_node_template:app
# Environment variables required — see .env.example and §17:
#   L9_NODE_NAME=my-worker-node
#   L9_ENVIRONMENT=local
#   GATE_URL=http://gate:8000
#   GATE_NODE_SPEC_PATH=examples/worker_node/spec.yaml

app: FastAPI = build_app()
