from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.responses import Response

from constellation_node_sdk.gate.registration import register_from_env
from constellation_node_sdk.transport.packet import TransportPacket

from .config import NodeRuntimeConfig, get_runtime_config
from .errors import raise_http_exception
from .execution import create_error_transport_packet, execute_transport_packet
from .lifecycle import LifecycleHook, NoOpLifecycle
from .observability import configure_logging, metrics_response, record_request, set_readiness
from .preflight import run_preflight


def _key_material_from_config(config: NodeRuntimeConfig) -> tuple[bytes | str | None, str | None]:
    if config.signing_algorithm == "hmac-sha256":
        return config.signing_key, config.signing_algorithm
    if config.signing_algorithm == "ed25519":
        return config.signing_private_key, config.signing_algorithm
    return None, None


def create_node_app(
    *,
    service_name: str | None = None,
    version: str | None = None,
    lifecycle_hook: LifecycleHook | None = None,
    config: NodeRuntimeConfig | None = None,
    auto_register_with_gate: bool = True,
) -> FastAPI:
    resolved_config = config or get_runtime_config()
    resolved_service_name = service_name or resolved_config.service_name
    resolved_version = version or resolved_config.service_version
    resolved_lifecycle = lifecycle_hook or NoOpLifecycle()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(resolved_config)
        run_preflight(resolved_config)

        app.state.runtime_ready = False
        set_readiness(config=resolved_config, ready=False)

        await resolved_lifecycle.startup()

        if auto_register_with_gate and resolved_config.gate_url:
            await register_from_env()

        app.state.runtime_ready = True
        set_readiness(config=resolved_config, ready=True)

        yield

        app.state.runtime_ready = False
        set_readiness(config=resolved_config, ready=False)
        await resolved_lifecycle.shutdown()

    app = FastAPI(
        title=resolved_service_name,
        version=resolved_version,
        lifespan=lifespan,
    )
    app.state.runtime_ready = False

    @app.get("/v1/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "healthy" if bool(getattr(app.state, "runtime_ready", False)) else "starting",
            "service_name": resolved_config.service_name,
            "service_version": resolved_config.service_version,
            "node_name": resolved_config.node_name,
            "ready": bool(getattr(app.state, "runtime_ready", False)),
        }

    @app.get("/metrics")
    async def metrics() -> Response:
        return metrics_response()

    @app.post("/v1/execute")
    async def execute(request: Request) -> JSONResponse:
        packet: TransportPacket | None = None
        try:
            body = await request.json()
        except Exception as exc:
            record_request(config=resolved_config, action="unknown", status="invalid_json")
            raise HTTPException(status_code=400, detail=f"invalid JSON body: {exc}") from exc

        try:
            if not isinstance(body, dict):
                raise ValueError("request body must be a JSON object")

            packet = TransportPacket.model_validate(body)

            response_signing_key, response_signing_algorithm = _key_material_from_config(
                resolved_config
            )

            response_packet = await execute_transport_packet(
                packet,
                node_name=resolved_config.node_name,
                signing_key=response_signing_key
                if response_signing_algorithm == "hmac-sha256"
                else None,
                signing_private_key=str(response_signing_key)
                if response_signing_algorithm == "ed25519" and response_signing_key
                else None,
                signing_key_id=resolved_config.signing_key_id,
                signing_algorithm=response_signing_algorithm,
                verifying_keys=resolved_config.verifying_keys,
                require_signature=resolved_config.require_signature,
                allowed_actions=resolved_config.allowed_actions or None,
                allowed_packet_types=resolved_config.allowed_packet_types or None,
                required_idempotency_actions=resolved_config.require_idempotency_for_actions
                or None,
                replay_enabled=resolved_config.replay_enabled,
                dev_mode=resolved_config.dev_mode,
                verify_hop_signatures=resolved_config.verify_hop_signatures,
                allowed_clock_skew_seconds=resolved_config.allowed_clock_skew_seconds,
                max_packet_bytes=resolved_config.max_packet_bytes,
                max_hop_depth=resolved_config.max_hop_depth,
                max_delegation_depth=resolved_config.max_delegation_depth,
                max_attachments=resolved_config.max_attachments,
                max_attachment_size_bytes=resolved_config.max_attachment_size_bytes,
                allowed_attachment_schemes=resolved_config.attachment_allowed_schemes,
                allow_private_attachment_hosts=resolved_config.allow_private_attachment_hosts,
            )

            status = str(response_packet.payload.get("status", "completed")).strip().lower()
            record_request(config=resolved_config, action=packet.header.action, status=status)
            return JSONResponse(content=response_packet.model_dump_json_dict())

        except Exception as exc:
            if packet is not None and resolved_config.return_transport_errors:
                response_signing_key, response_signing_algorithm = _key_material_from_config(
                    resolved_config
                )
                failure_packet = create_error_transport_packet(
                    packet,
                    exc,
                    node_name=resolved_config.node_name,
                    signing_key=response_signing_key
                    if response_signing_algorithm == "hmac-sha256"
                    else None,
                    signing_private_key=str(response_signing_key)
                    if response_signing_algorithm == "ed25519" and response_signing_key
                    else None,
                    signing_key_id=resolved_config.signing_key_id,
                    signing_algorithm=response_signing_algorithm,
                    expose_internal_errors=resolved_config.expose_internal_errors,
                )
                record_request(
                    config=resolved_config,
                    action=packet.header.action,
                    status="failed",
                )
                return JSONResponse(content=failure_packet.model_dump_json_dict())

            record_request(
                config=resolved_config,
                action="unknown" if packet is None else packet.header.action,
                status="error",
            )
            raise_http_exception(exc)

    return app
