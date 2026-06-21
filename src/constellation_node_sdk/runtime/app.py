from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from constellation_node_sdk.gate.registration import register_from_env
from constellation_node_sdk.transport.packet import TransportPacket

from .config import NodeRuntimeConfig, get_runtime_config
from .errors import raise_http_exception
from .execution import create_error_transport_packet, execute_transport_packet
from .inbound_policy import validate_execute_ingress_packet, validate_relay_ingress_packet
from .lifecycle import LifecycleHook, NoOpLifecycle
from .observability import configure_logging, metrics_response, record_request, set_readiness
from .preflight import run_preflight


def _key_material_from_config(config: NodeRuntimeConfig) -> tuple[bytes | str | None, str | None]:
    if config.signing_algorithm == "hmac-sha256":
        return config.signing_key, config.signing_algorithm
    if config.signing_algorithm == "ed25519":
        return config.signing_private_key, config.signing_algorithm
    return None, None


async def _parse_transport_packet(request: Request) -> TransportPacket:
    body = await request.json()
    if not isinstance(body, dict):
        raise ValueError("request body must be a JSON object")
    return TransportPacket.model_validate(body)


def _resolve_route_validation(
    *,
    route_mode: Literal["execute", "relay"],
    resolved_config: NodeRuntimeConfig,
) -> dict[str, Any]:
    if route_mode == "relay":
        return {
            "require_signature": resolved_config.relay_require_signature
            if resolved_config.relay_require_signature is not None
            else resolved_config.require_signature,
            "verify_hop_signatures": resolved_config.relay_verify_hop_signatures
            if resolved_config.relay_verify_hop_signatures is not None
            else resolved_config.verify_hop_signatures,
            "allowed_actions": resolved_config.relay_allowed_actions or None,
            "allowed_packet_types": resolved_config.relay_allowed_packet_types or None,
        }
    return {
        "require_signature": resolved_config.execute_require_signature
        if resolved_config.execute_require_signature is not None
        else resolved_config.require_signature,
        "verify_hop_signatures": resolved_config.execute_verify_hop_signatures
        if resolved_config.execute_verify_hop_signatures is not None
        else resolved_config.verify_hop_signatures,
        "allowed_actions": (
            resolved_config.execute_allowed_actions
            or resolved_config.allowed_actions
            or None
        ),
        "allowed_packet_types": (
            resolved_config.execute_allowed_packet_types
            or resolved_config.allowed_packet_types
            or None
        ),
    }


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
    async def lifespan(app: FastAPI):
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
    async def metrics():
        return metrics_response()

    @app.post("/v1/execute")
    async def execute(request: Request):
        packet: TransportPacket | None = None
        try:
            packet = await _parse_transport_packet(request)
            if resolved_config.enforce_gate_only_ingress:
                validate_execute_ingress_packet(
                    packet,
                    local_node=resolved_config.node_name,
                    gate_node_name=resolved_config.gate_node_name,
                    require_route_kind=resolved_config.require_gate_mediation_provenance,
                )

            route_validation = _resolve_route_validation(
                route_mode="execute", resolved_config=resolved_config,
            )

            response_signing_key, response_signing_algorithm = (
                _key_material_from_config(resolved_config)
            )

            response_packet = await execute_transport_packet(
                packet,
                execution_mode="execute",
                node_name=resolved_config.node_name,
                signing_key=(
                    response_signing_key if response_signing_algorithm == "hmac-sha256" else None
                ),
                signing_private_key=(
                    response_signing_key if response_signing_algorithm == "ed25519" else None
                ),
                signing_key_id=resolved_config.signing_key_id,
                signing_algorithm=response_signing_algorithm,
                verifying_keys=resolved_config.verifying_keys,
                require_signature=route_validation["require_signature"],
                allowed_actions=route_validation["allowed_actions"],
                allowed_packet_types=route_validation["allowed_packet_types"],
                required_idempotency_actions=(
                    resolved_config.require_idempotency_for_actions or None
                ),
                replay_enabled=resolved_config.replay_enabled,
                dev_mode=resolved_config.dev_mode,
                verify_hop_signatures=route_validation["verify_hop_signatures"],
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
                response_signing_key, response_signing_algorithm = (
                    _key_material_from_config(resolved_config)
                )
                failure_packet = create_error_transport_packet(
                    packet,
                    exc,
                    node_name=resolved_config.node_name,
                    signing_key=(
                        response_signing_key
                        if response_signing_algorithm == "hmac-sha256"
                        else None
                    ),
                    signing_private_key=(
                        response_signing_key
                        if response_signing_algorithm == "ed25519"
                        else None
                    ),
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

    if resolved_config.enable_relay_route:
        @app.post("/v1/relay")
        async def relay(request: Request):
            packet: TransportPacket | None = None
            try:
                packet = await _parse_transport_packet(request)
                if resolved_config.enforce_gate_only_ingress:
                    validate_relay_ingress_packet(
                        packet,
                        local_node=resolved_config.node_name,
                        gate_node_name=resolved_config.gate_node_name,
                        require_route_kind=resolved_config.require_gate_mediation_provenance,
                        allowed_actions=resolved_config.relay_allowed_actions or None,
                        allowed_packet_types=resolved_config.relay_allowed_packet_types or None,
                    )

                route_validation = _resolve_route_validation(
                    route_mode="relay", resolved_config=resolved_config,
                )
                response_signing_key, response_signing_algorithm = (
                    _key_material_from_config(resolved_config)
                )

                response_packet = await execute_transport_packet(
                    packet,
                    execution_mode="relay",
                    node_name=resolved_config.node_name,
                    signing_key=(
                        response_signing_key
                        if response_signing_algorithm == "hmac-sha256"
                        else None
                    ),
                    signing_private_key=(
                        response_signing_key
                        if response_signing_algorithm == "ed25519"
                        else None
                    ),
                    signing_key_id=resolved_config.signing_key_id,
                    signing_algorithm=response_signing_algorithm,
                    verifying_keys=resolved_config.verifying_keys,
                    require_signature=route_validation["require_signature"],
                    allowed_actions=route_validation["allowed_actions"],
                    allowed_packet_types=route_validation["allowed_packet_types"],
                    required_idempotency_actions=(
                        resolved_config.require_idempotency_for_actions or None
                    ),
                    replay_enabled=resolved_config.replay_enabled,
                    dev_mode=resolved_config.dev_mode,
                    verify_hop_signatures=route_validation["verify_hop_signatures"],
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
                    response_signing_key, response_signing_algorithm = (
                        _key_material_from_config(resolved_config)
                    )
                    failure_packet = create_error_transport_packet(
                        packet,
                        exc,
                        node_name=resolved_config.node_name,
                        signing_key=(
                            response_signing_key
                            if response_signing_algorithm == "hmac-sha256"
                            else None
                        ),
                        signing_private_key=(
                            response_signing_key
                            if response_signing_algorithm == "ed25519"
                            else None
                        ),
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
