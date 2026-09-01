from __future__ import annotations

from collections.abc import Callable, Mapping
from types import TracebackType
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from constellation_node_sdk._packet_http import (
    PacketTransportErrors,
    decode_packet_body,
    post_packet_json,
    raise_for_status,
)
from constellation_node_sdk.runtime.inbound_policy import validate_execute_ingress_packet
from constellation_node_sdk.security.signing import sign_transport_packet
from constellation_node_sdk.security.validation import validate_transport_packet
from constellation_node_sdk.transport.errors import TransportError
from constellation_node_sdk.transport.packet import TransportPacket

from .config import GateDispatchTransportConfig
from .errors import (
    GateDispatchAuthorityError,
    GateDispatchConfigurationError,
    GateDispatchSecurityError,
    WorkerConnectionError,
    WorkerHTTPError,
    WorkerResponseError,
    WorkerTimeoutError,
)

# The Gate->worker half of the shared canonical-packet HTTP machinery. Same code
# as the application->Gate half, different names on the failures so a caller can
# tell which leg of the rail broke.
_WORKER_TRANSPORT_ERRORS = PacketTransportErrors(
    timeout=WorkerTimeoutError,
    connection=WorkerConnectionError,
    http=WorkerHTTPError,
    response=WorkerResponseError,
)

# Workers expose canonical execution at exactly one path. The caller supplies a
# base URL because Gate owns the registry; it does not get to choose the path.
_WORKER_EXECUTE_PATH = "/v1/execute"

_SUPPORTED_SCHEMES = frozenset({"http", "https"})


class GateDispatchTransport:
    """
    Gate-authorized transport for an already-routed canonical packet.

    This is **not** a general node-to-peer client, and it is not reachable
    through ``GateClient``. It transports a packet that Gate has already
    derived, addressed, and hopped, to the worker Gate has already resolved.

    The authority boundary is mechanical, not documentary. Supplying a worker
    URL is never sufficient: the packet itself must carry Gate's routing
    authority (sourced from Gate, replied to Gate, addressed to the named
    target, ``resolved_by_gate`` true, ``route_kind`` ``external_ingress``), and
    a packet that does not is rejected before any network call. A node or
    application cannot obtain peer transport by importing this class, because it
    cannot mint a packet that passes the check.

    Division of authority:

    * **Gate** decides *where* — target selection, deadline arithmetic,
      concurrency, health, and replay policy all stay in Gate.
    * **Gate_SDK** decides *how* — validation, signing, serialization, the
      single network attempt, the deadline actually applied, response decoding,
      and canonical response validation.
    * **The worker** decides *what* — the payload is transported untouched.
    """

    def __init__(
        self,
        config: GateDispatchTransportConfig,
        *,
        client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """
        Args:
            config: Gate identity and signing/verification policy.
            client: Optional pre-built ``httpx.AsyncClient`` whose connection
                pool is reused across dispatches. Gate dispatches continuously,
                so a client per packet would mean a TCP handshake per packet;
                supply one and it is reused. Its lifecycle stays with the
                caller — this class never closes a client it did not create.
                The per-request deadline is still applied per dispatch, so a
                pooled client's own default timeout can never widen a call.
            transport: Optional httpx transport, an SDK-internal test seam for
                exercising real behavior without a network. It carries no URL
                and cannot redirect a dispatch: the destination is still built
                from the caller's resolved base URL under the authority checks
                above.
        """
        self._config = config
        self._client = client
        self._transport = transport
        self._owns_client = False

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def __aenter__(self) -> GateDispatchTransport:
        """
        Enter a managed client lifecycle.

        Using the transport as an async context manager creates a pooled client
        it owns and closes. A client passed to ``__init__`` is left alone: it
        belongs to the caller.
        """
        if self._client is None:
            self._client = httpx.AsyncClient(transport=self._transport)
            self._owns_client = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close only a client this transport created, never the caller's."""
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None
            self._owns_client = False

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    async def send_gate_authored_packet(
        self,
        *,
        packet: TransportPacket,
        target_node: str,
        worker_base_url: str,
    ) -> TransportPacket:
        """
        Send a Gate-authored dispatch packet to the worker Gate resolved.

        The SDK does not route. ``target_node`` and ``worker_base_url`` come
        from Gate's registry and resolver; the SDK verifies that the packet
        agrees with them, and never discovers, chooses, load-balances, or fails
        over between workers.

        There is no timeout parameter, deliberately. The deadline is derived
        from ``packet.header.timeout_ms`` — the same value the worker's runtime
        uses to bound its handler — so the budget Gate waits on and the budget
        the worker is told about are one number. A separate parameter here would
        recreate exactly the drift this closure removes.

        Exactly one HTTP request is performed. Transport failure is returned as
        a typed error; whether to replay a worker execution is Gate's decision,
        under Gate's idempotency, not the SDK's.

        Args:
            packet: The packet Gate derived, addressed, and hopped.
            target_node: The node name Gate resolved.
            worker_base_url: That node's base URL from Gate's registry. The
                canonical ``/v1/execute`` endpoint is appended by the SDK.

        Returns:
            The validated canonical ``TransportPacket`` the worker returned.

        Raises:
            GateDispatchAuthorityError: The packet is not a Gate-authored
                dispatch, or does not agree with the named target.
            GateDispatchConfigurationError: The transport or the supplied URL
                cannot express this dispatch.
            GateDispatchSecurityError: Signing, signature, or integrity failed.
            WorkerConnectionError: The worker could not be reached.
            WorkerTimeoutError: The packet's budget elapsed.
            WorkerHTTPError: The worker answered with a non-success status.
            WorkerResponseError: The worker's answer was not a canonical
                response to this dispatch.
        """
        normalized_target = target_node.strip().lower()
        if not normalized_target:
            raise GateDispatchConfigurationError("target_node must not be blank")

        execute_url = self._build_execute_url(worker_base_url)

        # Authority first, before any crypto or I/O: a packet that must not
        # leave is refused without spending a signature on it.
        self._assert_gate_authored(packet, target_node=normalized_target)

        signed_packet = self._maybe_sign(packet)
        self._validate_outbound_transport(signed_packet)
        timeout_seconds = self._network_timeout_seconds(signed_packet)

        response = await post_packet_json(
            url=execute_url,
            json_body=signed_packet.model_dump_json_dict(),
            timeout_seconds=timeout_seconds,
            errors=_WORKER_TRANSPORT_ERRORS,
            what=f"worker {normalized_target!r}",
            client=self._client,
            transport=self._transport if self._client is None else None,
        )
        raise_for_status(
            response,
            context=f"worker {normalized_target!r} execute",
            errors=_WORKER_TRANSPORT_ERRORS,
        )
        body = decode_packet_body(
            response,
            context=f"worker {normalized_target!r} execute response",
            errors=_WORKER_TRANSPORT_ERRORS,
        )

        response_packet = self._decode_response_packet(body, target_node=normalized_target)
        self._validate_inbound_response(response_packet)
        self._assert_response_answers_dispatch(
            response_packet,
            request=signed_packet,
            target_node=normalized_target,
        )
        return response_packet

    # ------------------------------------------------------------------
    # Endpoint authority
    # ------------------------------------------------------------------

    @staticmethod
    def _build_execute_url(worker_base_url: str) -> str:
        """
        Build the canonical worker execution endpoint from a base URL.

        The caller supplies a base only. A path, query, or fragment would mean
        the caller choosing where execution happens, which is the SDK's to fix
        and not the registry's to vary.

        The scheme is not forced to https: worker base URLs are cluster-internal
        service addresses, and Gate's own registration schema accepts both.
        Network trust is a deployment concern, separate from transport syntax.
        """
        candidate = worker_base_url.strip()
        if not candidate:
            raise GateDispatchConfigurationError("worker_base_url must not be blank")

        parts = urlsplit(candidate)
        if parts.scheme.lower() not in _SUPPORTED_SCHEMES:
            raise GateDispatchConfigurationError(
                f"worker_base_url must use http or https, got {parts.scheme or '<none>'!r}"
            )
        if not parts.netloc:
            raise GateDispatchConfigurationError(
                f"worker_base_url must include a host, got {worker_base_url!r}"
            )
        if parts.query or parts.fragment:
            raise GateDispatchConfigurationError(
                "worker_base_url must not carry a query string or fragment; "
                "the SDK owns the execution endpoint"
            )
        if parts.path.strip("/"):
            raise GateDispatchConfigurationError(
                f"worker_base_url must be a base URL without a path, got {parts.path!r}; "
                "the SDK appends the canonical execution endpoint"
            )
        return f"{candidate.rstrip('/')}{_WORKER_EXECUTE_PATH}"

    # ------------------------------------------------------------------
    # Gate authority validation
    # ------------------------------------------------------------------

    def _assert_gate_authored(self, packet: TransportPacket, *, target_node: str) -> None:
        """
        Prove the packet is a genuine Gate dispatch for this exact target.

        The bulk of this is the worker's own ingress law, reused rather than
        restated: ``validate_execute_ingress_packet`` is what every SDK-based
        worker applies on ``/v1/execute``. Checking the same function here means
        Gate cannot send a packet its worker would reject, and there is no
        second dialect of the rule to drift.

        ``reply_to`` is the one addition: the worker does not care where the
        answer goes, but a dispatch that does not return to Gate is not a Gate
        dispatch.
        """
        gate_node = self._config.local_gate_node
        try:
            validate_execute_ingress_packet(
                packet,
                local_node=target_node,
                gate_node_name=gate_node,
                require_route_kind=True,
            )
        except ValueError as exc:
            raise GateDispatchAuthorityError(
                f"packet is not a Gate-authored dispatch for {target_node!r}: {exc}"
            ) from exc

        reply_to = packet.address.reply_to.strip().lower()
        if reply_to != gate_node:
            raise GateDispatchAuthorityError(
                f"Gate-authored dispatch must reply to {gate_node!r}, got {reply_to!r}"
            )

    # ------------------------------------------------------------------
    # Signing and validation
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
            raise GateDispatchConfigurationError(
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
            raise GateDispatchSecurityError(
                f"could not sign Gate dispatch packet: {type(exc).__name__}: {exc}",
                direction="outbound",
            ) from exc

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
            raise GateDispatchSecurityError(
                f"Gate dispatch packet failed transport validation: {type(exc).__name__}: {exc}",
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
            raise GateDispatchSecurityError(
                f"worker response failed transport validation: {type(exc).__name__}: {exc}",
                direction="inbound",
            ) from exc

    # ------------------------------------------------------------------
    # Deadline
    # ------------------------------------------------------------------

    @staticmethod
    def _network_timeout_seconds(packet: TransportPacket) -> float:
        """
        Derive the network deadline from the packet's own advertised budget.

        The worker's runtime bounds its handler with the same
        ``header.timeout_ms``, so this is deliberately the identical number
        rather than a related one: one downstream budget, honoured on both ends.
        """
        budget_ms = packet.header.timeout_ms
        if budget_ms <= 0:
            raise GateDispatchConfigurationError(
                f"dispatch packet advertises a non-positive budget: {budget_ms}ms"
            )
        return budget_ms / 1000.0

    # ------------------------------------------------------------------
    # Response validation
    # ------------------------------------------------------------------

    @staticmethod
    def _decode_response_packet(body: dict[str, Any], *, target_node: str) -> TransportPacket:
        try:
            return TransportPacket.model_validate(body)
        except TransportError as exc:
            # Integrity failures reach here directly rather than as a pydantic
            # error, so a tampered response is a security failure, not a
            # malformed-body failure Gate might reasonably retry into.
            raise GateDispatchSecurityError(
                f"worker {target_node!r} response failed packet integrity: "
                f"{type(exc).__name__}: {exc}",
                direction="inbound",
            ) from exc
        except ValidationError as exc:
            raise WorkerResponseError(
                f"worker {target_node!r} response is not a canonical TransportPacket: {exc}",
                body=body,
            ) from exc

    def _assert_response_answers_dispatch(
        self,
        response: TransportPacket,
        *,
        request: TransportPacket,
        target_node: str,
    ) -> None:
        """
        Prove the response actually answers this dispatch.

        A canonical, correctly signed packet from the wrong worker, the wrong
        operation, or the wrong tenant is still the wrong answer. The SDK worker
        runtime derives its response from the request, so every relationship
        checked here is one it genuinely establishes.
        """
        problems: list[str] = []

        if response.address.source_node != target_node:
            problems.append(
                f"source_node {response.address.source_node!r} is not the dispatched "
                f"worker {target_node!r}"
            )
        if response.address.destination_node != self._config.local_gate_node:
            problems.append(
                f"destination_node {response.address.destination_node!r} is not "
                f"{self._config.local_gate_node!r}"
            )
        if response.header.action != request.header.action:
            problems.append(
                f"action {response.header.action!r} does not answer {request.header.action!r}"
            )
        if response.lineage.root_id != request.lineage.root_id:
            problems.append("root lineage does not match the dispatched operation")
        if response.header.causation_id != request.header.packet_id:
            problems.append("causation_id does not point at the dispatched packet")
        if response.tenant != request.tenant:
            problems.append("tenant context was mutated")
        if response.header.correlation_id != request.header.correlation_id:
            problems.append("correlation_id does not match the dispatched operation")
        if response.header.idempotency_key != request.header.idempotency_key:
            problems.append("idempotency_key does not match the dispatched operation")

        if problems:
            raise WorkerResponseError(
                f"worker {target_node!r} response does not answer this dispatch: "
                + "; ".join(problems)
            )
