"""
Shared Gate-client test helpers.

These build clients and canonical Gate-shaped responses without asking the test
to hand-assemble transport internals, which is exactly the property under test.
"""

from __future__ import annotations

import inspect
import json
from typing import Any

import httpx

from constellation_node_sdk.gate.config import GateClientConfig
from constellation_node_sdk.transport.packet import TransportPacket, create_transport_packet


def make_client_config(
    *,
    local_node: str = "odoo",
    gate_url: str = "http://gate:8000",
    timeout_seconds: float = 30.0,
    max_timeout_ms: int | None = None,
    transport_margin_ms: int = 0,
    allowed_gate_destination: str = "gate",
    **overrides: Any,
) -> GateClientConfig:
    """Build a Gate client config with test-friendly defaults."""
    return GateClientConfig(
        gate_url=gate_url,
        local_node=local_node,
        timeout_seconds=timeout_seconds,
        max_timeout_ms=max_timeout_ms,
        transport_margin_ms=transport_margin_ms,
        allowed_gate_destination=allowed_gate_destination,
        **overrides,
    )


class RecordingTransport(httpx.AsyncBaseTransport):
    """
    An httpx transport that records every attempt and the deadline applied to it.

    Counting attempts is how a hidden retry layer is caught. Reading
    ``request.extensions["timeout"]`` is how the *actual* network deadline is
    caught, as opposed to the one the SDK claims to use.
    """

    def __init__(self, responder: Any) -> None:
        self._responder = responder
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.requests.append(request)
        result = self._responder(request, len(self.requests))
        if inspect.isawaitable(result):
            return await result
        return result

    def sent_packet(self, index: int = 0) -> dict[str, Any]:
        """Decode the packet body of a recorded request."""
        body = json.loads(self.requests[index].content.decode("utf-8"))
        assert isinstance(body, dict)
        return body

    def applied_timeout(self, index: int = 0) -> dict[str, float | None]:
        """The real per-request httpx timeout, as the transport layer saw it."""
        timeout = self.requests[index].extensions.get("timeout")
        assert isinstance(timeout, dict), "httpx did not attach a timeout to the request"
        return timeout


def gate_response_for(request_packet: TransportPacket, payload: dict[str, Any]) -> TransportPacket:
    """
    Build the canonical response packet Gate would return for a request.

    Gate derives a semantic child for the response rather than replaying the
    request packet, so the round trip exercises real derive semantics.
    """
    return request_packet.derive(
        packet_type="response",
        source_node="gate",
        destination_node=request_packet.address.reply_to,
        reply_to="gate",
        payload=payload,
    )


def gate_echo_responder(payload: dict[str, Any]) -> Any:
    """
    A responder that answers any request with a canonical Gate response packet.

    The response is derived from the packet actually sent, so correlation,
    lineage, and tenant come from the real request rather than a fixture.
    """

    def respond(request: httpx.Request, _attempt: int) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        sent = TransportPacket.model_validate(body)
        return httpx.Response(200, json=gate_response_for(sent, payload).model_dump_json_dict())

    return respond


def make_root_packet(
    *,
    local_node: str = "odoo",
    action: str = "converge",
    payload: dict[str, Any] | None = None,
    tenant: str = "tenant-a",
    timeout_ms: int = 30_000,
) -> TransportPacket:
    """A node-originated root packet, for exercising the packet-native primitive."""
    from constellation_node_sdk.transport.provenance import RoutingProvenance

    return create_transport_packet(
        action=action,
        payload=payload if payload is not None else {"entity_id": "42"},
        tenant=tenant,
        source_node=local_node,
        destination_node="gate",
        reply_to=local_node,
        timeout_ms=timeout_ms,
        provenance=RoutingProvenance(
            origin_kind="node",
            requested_action=action,
            resolved_by_gate=False,
            original_source_node=local_node,
        ),
    )
