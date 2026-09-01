from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import httpx
from pydantic import ValidationError

from constellation_node_sdk._packet_http import (
    PacketTransportErrors,
    decode_packet_body,
    describe_exception,
    post_packet_json,
    raise_for_status,
)
from constellation_node_sdk.security.signing import sign_transport_packet
from constellation_node_sdk.security.validation import validate_transport_packet
from constellation_node_sdk.transport.errors import TransportError
from constellation_node_sdk.transport.packet import TransportPacket, create_transport_packet
from constellation_node_sdk.transport.provenance import RoutingProvenance
from constellation_node_sdk.transport.tenant import TenantContext

from .config import GateClientConfig
from .errors import (
    GateConfigurationError,
    GateConnectionError,
    GateHTTPError,
    GateResponseError,
    GateSecurityError,
    GateTimeoutError,
)
from .policy import validate_outbound_gate_packet

# The application->Gate half of the shared canonical-packet HTTP machinery.
# The Gate->worker half injects its own worker-named types into the same code.
_GATE_TRANSPORT_ERRORS = PacketTransportErrors(
    timeout=GateTimeoutError,
    connection=GateConnectionError,
    http=GateHTTPError,
    response=GateResponseError,
)


class GateClient:
    """
    Canonical Gate-only transport client for nodes.

    This client is the only allowed outbound inter-node transport surface.
    It never accepts an arbitrary peer URL.

    Two public surfaces, in order of preference:

    ``execute()``
        The application surface. Takes business inputs (action, payload,
        tenant, logical operation identity, one deadline) and owns the whole
        transport lifecycle: root packet construction, Gate destination,
        deadline translation, signing, HTTP, and response validation.
        Normal application code should need nothing else.

    ``send_to_gate()``
        The packet-native protocol primitive, for Gate, the SDK runtime,
        orchestrators, and protocol tests. It takes an already-canonical
        ``TransportPacket``.

    Every failure leaving either surface is a
    :class:`~constellation_node_sdk.gate.errors.GateClientError` subclass.
    Callers never need ``httpx`` to classify a transport failure.
    """

    def __init__(
        self,
        config: GateClientConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """
        Args:
            config: Gate client configuration.
            transport: Optional httpx transport. This is an SDK-internal test
                seam for exercising real client behavior (actual applied
                timeouts, request counts) without a network. It carries no URL
                and cannot be used to reach a peer node: the destination is
                still resolved from ``config.gate_url`` under Gate-only policy.
        """
        self._config = config
        self._transport = transport

    @property
    def gate_url(self) -> str:
        return self._config.gate_url

    # ------------------------------------------------------------------
    # Application surface
    # ------------------------------------------------------------------

    async def execute(
        self,
        *,
        action: str,
        payload: Mapping[str, Any],
        tenant: str | Mapping[str, Any] | TenantContext,
        idempotency_key: str | None = None,
        timeout_ms: int | None = None,
        correlation_id: str | None = None,
        trace_id: str | None = None,
        classification: str = "internal",
        compliance_tags: tuple[str, ...] = (),
        retention_days: int = 90,
        priority: int = 2,
    ) -> TransportPacket:
        """
        Execute one logical operation through Gate and return Gate's response packet.

        The caller supplies business inputs only. The SDK owns every transport
        mechanic: root packet construction, Gate destination, source and
        reply-to identity, deadline translation, transport idempotency
        representation, signing, HTTP, and response validation.

        Args:
            action: The intent. Gate resolves which node owns it; the caller
                never names a destination.
            payload: The domain payload. Opaque to the SDK — it is transported,
                never interpreted or translated.
            tenant: Tenant context, as a tenant id, a mapping, or a
                ``TenantContext``.
            idempotency_key: The caller's logical business operation identity.
                The application decides what constitutes one operation; the SDK
                only owns placing that value into the transport header. No
                payload hash is ever substituted for it.
            timeout_ms: The single operation budget. It becomes both the packet's
                advertised ``timeout_ms`` and the basis for the actual network
                deadline, so the two can never drift. Defaults to the client's
                configured default budget.
            correlation_id: Correlation id to join this call to an existing
                trace. Defaults to the new packet id.
            trace_id: Trace id. Defaults to the new packet id.
            classification: Transport security classification.
            compliance_tags: Governance compliance tags.
            retention_days: Governance retention window.
            priority: Transport priority class.

        Returns:
            The validated canonical ``TransportPacket`` Gate returned.

        Raises:
            GateConfigurationError: The client configuration cannot express this call.
            GatePolicyError: The resulting packet violates Gate-only routing policy.
            GateSecurityError: Signing, signature, or integrity validation failed.
            GateConnectionError: Gate could not be reached.
            GateTimeoutError: The transport deadline elapsed.
            GateHTTPError: Gate answered with a non-success status.
            GateResponseError: Gate's answer was not a canonical packet.
        """
        budget_ms = self._resolve_operation_budget_ms(timeout_ms)
        packet = self._build_root_packet(
            action=action,
            payload=payload,
            tenant=tenant,
            idempotency_key=idempotency_key,
            budget_ms=budget_ms,
            correlation_id=correlation_id,
            trace_id=trace_id,
            classification=classification,
            compliance_tags=compliance_tags,
            retention_days=retention_days,
            priority=priority,
        )
        return await self.send_to_gate(packet)

    def _resolve_operation_budget_ms(self, timeout_ms: int | None) -> int:
        """
        Resolve the single operation budget for one execution.

        Explicit caller budget wins; otherwise the client's configured default
        budget applies. An optional configured ceiling clamps both.
        """
        if timeout_ms is None:
            budget_ms = int(self._config.timeout_seconds * 1000)
        else:
            budget_ms = int(timeout_ms)

        if budget_ms <= 0:
            raise GateConfigurationError(
                f"operation timeout budget must be positive, got {budget_ms}ms"
            )

        ceiling = self._config.max_timeout_ms
        if ceiling is not None and budget_ms > ceiling:
            budget_ms = ceiling
        return budget_ms

    def _build_root_packet(
        self,
        *,
        action: str,
        payload: Mapping[str, Any],
        tenant: str | Mapping[str, Any] | TenantContext,
        idempotency_key: str | None,
        budget_ms: int,
        correlation_id: str | None,
        trace_id: str | None,
        classification: str,
        compliance_tags: tuple[str, ...],
        retention_days: int,
        priority: int,
    ) -> TransportPacket:
        """
        Build the canonical node-originated root packet for an application call.

        Destination is always Gate. Source and reply-to are always the
        configured local node identity. Neither is caller-controllable: the
        application expresses intent by ``action`` and Gate resolves ownership.
        """
        local_node = self._config.local_node
        normalized_action = action.strip().lower()
        if not normalized_action:
            raise GateConfigurationError("action must not be blank")

        normalized_tenant: str | dict[str, Any] | TenantContext
        if isinstance(tenant, TenantContext | str):
            normalized_tenant = tenant
        else:
            normalized_tenant = dict(tenant)

        try:
            return create_transport_packet(
                action=normalized_action,
                payload=dict(payload),
                tenant=normalized_tenant,
                source_node=local_node,
                destination_node=self._config.allowed_gate_destination,
                reply_to=local_node,
                priority=priority,
                timeout_ms=budget_ms,
                classification=classification,
                compliance_tags=compliance_tags,
                retention_days=retention_days,
                idempotency_key=idempotency_key,
                trace_id=trace_id,
                correlation_id=correlation_id,
                provenance=RoutingProvenance(
                    origin_kind="node",
                    requested_action=normalized_action,
                    resolved_by_gate=False,
                    original_source_node=local_node,
                ),
            )
        except TransportError as exc:
            raise GateSecurityError(
                f"could not build a canonical root packet: {type(exc).__name__}: {exc}",
                direction="outbound",
            ) from exc
        except (ValidationError, ValueError) as exc:
            raise GateConfigurationError(
                f"could not build a canonical root packet: {type(exc).__name__}: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Deadline closure
    # ------------------------------------------------------------------

    def _network_timeout_seconds(self, packet: TransportPacket) -> float:
        """
        Translate the packet's advertised budget into the actual network deadline.

        There is exactly one deadline. The packet header carries the operation
        budget; the network deadline is derived from it, minus an explicitly
        configured transport margin. The network deadline can therefore never
        silently outlive the budget the packet advertises downstream.
        """
        budget_ms = packet.header.timeout_ms
        ceiling = self._config.max_timeout_ms
        if ceiling is not None and budget_ms > ceiling:
            budget_ms = ceiling

        effective_ms = budget_ms - self._config.transport_margin_ms
        if effective_ms <= 0:
            raise GateConfigurationError(
                f"transport_margin_ms={self._config.transport_margin_ms} leaves no network "
                f"deadline for a {budget_ms}ms operation budget"
            )
        return effective_ms / 1000.0

    # ------------------------------------------------------------------
    # Packet transport primitive
    # ------------------------------------------------------------------

    def _transport_key_resolver(
        self,
    ) -> Callable[[str | None], str | bytes | None] | Mapping[str, str | bytes] | None:
        if not self._config.verifying_keys and self._config.signing_key is None:
            return None
        return self._config.resolve_verifying_key

    def _maybe_sign(self, packet: TransportPacket) -> TransportPacket:
        if self._config.signing_key is None:
            return packet
        if self._config.signing_key_id is None or self._config.signing_algorithm is None:
            raise GateConfigurationError(
                "signing_key_id and signing_algorithm are required when signing_key is configured"
            )
        try:
            return sign_transport_packet(
                packet,
                key=self._config.signing_key,
                key_id=self._config.signing_key_id,
                algorithm=self._config.signing_algorithm,
            )
        except TransportError as exc:
            raise GateSecurityError(
                f"could not sign outbound packet: {type(exc).__name__}: {exc}",
                direction="outbound",
            ) from exc

    def _validate_routing_policy(self, packet: TransportPacket) -> None:
        """
        Reject a packet that must not leave this node, before any crypto runs.

        Routing policy is cheap and unconditional, so it goes first: there is no
        reason to sign a packet we are about to refuse to send.
        """
        validate_outbound_gate_packet(
            packet,
            local_node=self._config.local_node,
            gate_node_name=self._config.allowed_gate_destination,
        )

    def _validate_outbound_transport(self, packet: TransportPacket) -> None:
        """Validate the signed packet — the exact artifact that goes on the wire."""
        try:
            validate_transport_packet(
                packet,
                key_resolver=self._transport_key_resolver(),
                require_signature=self._config.require_signature,
                dev_mode=not self._config.require_signature,
                verify_hop_signatures=self._config.verify_hop_signatures,
            )
        except TransportError as exc:
            raise GateSecurityError(
                f"outbound packet failed transport validation: {type(exc).__name__}: {exc}",
                direction="outbound",
            ) from exc

    def _validate_inbound_response(self, packet: TransportPacket) -> None:
        try:
            validate_transport_packet(
                packet,
                key_resolver=self._transport_key_resolver(),
                require_signature=self._config.verify_response_signatures,
                dev_mode=not self._config.verify_response_signatures,
                verify_hop_signatures=self._config.verify_hop_signatures,
            )
        except TransportError as exc:
            raise GateSecurityError(
                f"Gate response failed transport validation: {type(exc).__name__}: {exc}",
                direction="inbound",
            ) from exc

    async def _post_json(
        self,
        *,
        url: str,
        json_body: dict[str, Any],
        timeout_seconds: float,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
    ) -> httpx.Response:
        """
        Perform exactly one POST and translate every httpx failure into a typed error.

        There is no retry here. A failed execution is returned to the caller as a
        typed transport failure; replaying an application execution requires
        semantic authority the SDK does not have.
        """
        return await post_packet_json(
            url=url,
            json_body=json_body,
            timeout_seconds=timeout_seconds,
            errors=_GATE_TRANSPORT_ERRORS,
            what="Gate",
            transport=self._transport,
            headers=headers,
            params=params,
        )

    @staticmethod
    def _decode_packet_body(response: httpx.Response, *, context: str) -> dict[str, Any]:
        return decode_packet_body(response, context=context, errors=_GATE_TRANSPORT_ERRORS)

    @staticmethod
    def _raise_for_status(response: httpx.Response, *, context: str) -> None:
        raise_for_status(response, context=context, errors=_GATE_TRANSPORT_ERRORS)

    async def send_to_gate(self, packet: TransportPacket) -> TransportPacket:
        """
        Send a canonical TransportPacket to Gate and decode a canonical TransportPacket response.

        This is the packet-native protocol primitive. Application code should
        normally use :meth:`execute` instead, which builds the packet for it.

        The network deadline is derived from ``packet.header.timeout_ms`` — the
        budget the packet itself advertises — so a caller cannot end up waiting
        on the wire past the deadline the packet promises downstream.

        Exactly one HTTP request is performed. There is no hidden retry.
        """
        # Policy first (cheap, no crypto), then sign, then validate the signed
        # packet. Transport validation must judge the exact artifact that goes
        # on the wire: validating the unsigned packet both checks the wrong
        # thing and makes `require_signature=True` unusable for a node that
        # signs its own traffic — it would reject every packet for a missing
        # signature it was about to add.
        self._validate_routing_policy(packet)
        signed_packet = self._maybe_sign(packet)
        self._validate_outbound_transport(signed_packet)
        timeout_seconds = self._network_timeout_seconds(signed_packet)

        response = await self._post_json(
            url=f"{self._config.gate_url}/v1/execute",
            json_body=signed_packet.model_dump_json_dict(),
            timeout_seconds=timeout_seconds,
        )
        self._raise_for_status(response, context="Gate execute")
        body = self._decode_packet_body(response, context="Gate execute response")

        try:
            response_packet = TransportPacket.model_validate(body)
        except TransportError as exc:
            raise GateSecurityError(
                f"Gate response failed packet integrity: {type(exc).__name__}: {exc}",
                direction="inbound",
            ) from exc
        except ValidationError as exc:
            raise GateResponseError(
                f"Gate response is not a canonical TransportPacket: {exc}",
                body=body,
            ) from exc

        self._validate_inbound_response(response_packet)
        return response_packet

    async def health(self) -> dict[str, Any]:
        """
        Query Gate health endpoint.

        Uses the client's configured default budget, since a health probe has no
        packet of its own to carry a deadline.
        """
        timeout_seconds = self._config.timeout_seconds
        url = f"{self._config.gate_url}/v1/health"
        try:
            async with httpx.AsyncClient(
                timeout=timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.get(url)
        except httpx.TimeoutException as exc:
            raise GateTimeoutError(
                f"Gate health did not respond within {timeout_seconds}s "
                f"({describe_exception(exc)})",
                timeout_seconds=timeout_seconds,
            ) from exc
        except httpx.HTTPError as exc:
            raise GateConnectionError(
                f"could not reach Gate health at {url} ({describe_exception(exc)})"
            ) from exc

        self._raise_for_status(response, context="Gate health")
        return self._decode_packet_body(response, context="Gate health response")
