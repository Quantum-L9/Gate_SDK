"""
Track C — a malformed Gate response must never look like a successful call.

``GateClient.send_to_gate`` is Odoo's receive path. Whatever comes back on
that socket, exactly one thing may be handed to the application: a canonical
``TransportPacket`` that parsed and passed integrity. Everything else raises.

The failure that matters is the quiet one — a bare application dict that
looks like a result being returned as though it were a verified packet.
Every case below also asserts a single HTTP attempt, so a rejection cannot
be masked by a silent retry.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from constellation_node_sdk.gate.client import GateClient
from constellation_node_sdk.gate.config import GateClientConfig
from constellation_node_sdk.transport.codec import encode_transport_packet
from constellation_node_sdk.transport.errors import TransportIntegrityError
from constellation_node_sdk.transport.hashing import compute_payload_hash
from constellation_node_sdk.transport.packet import TransportPacket, create_transport_packet
from constellation_node_sdk.transport.provenance import RoutingProvenance
from constellation_node_sdk.transport.tenant import TenantContext


def _client() -> GateClient:
    return GateClient(
        GateClientConfig(gate_url="http://gate:8000", local_node="odoo", timeout_seconds=5.0)
    )


def _request(tenant: TenantContext) -> TransportPacket:
    return create_transport_packet(
        action="converge",
        payload={"entity": {"id": "res.partner:55"}},
        tenant=tenant,
        source_node="odoo",
        destination_node="gate",
        reply_to="odoo",
        provenance=RoutingProvenance(
            origin_kind="node",
            requested_action="converge",
            resolved_by_gate=False,
            original_source_node="odoo",
        ),
    )


def _canonical_response(request: TransportPacket) -> dict[str, Any]:
    response = request.derive(
        packet_type="response",
        source_node="gate",
        destination_node="odoo",
        reply_to="gate",
        payload={"state": "completed", "fields": {"website": "https://example.com"}},
    )
    return encode_transport_packet(response)


async def _expect_rejection(
    route_gate_http: Any,
    tenant: TenantContext,
    responder: Any,
    expected: type[Exception],
) -> Exception:
    """Send one packet, require a rejection, and require exactly one attempt."""
    request = _request(tenant)
    with route_gate_http(responder) as transport:
        with pytest.raises(expected) as caught:
            await _client().send_to_gate(request)
    assert len(transport.requests) == 1, "a rejected response was silently retried"
    return caught.value


# ---------------------------------------------------------------------------
# The case the rail actually risks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bare_domain_dict_is_not_accepted_as_a_response(
    route_gate_http: Any, tenant: TenantContext
) -> None:
    """
    A response body that is only the application's result is rejected.

    This is the expensive silent failure: it is well-formed JSON, it is a
    dict, and it carries exactly the keys the caller was hoping for. Nothing
    but the canonical envelope distinguishes it from a verified packet.
    """
    body = {"state": "completed", "fields": {"website": "https://example.com"}}

    await _expect_rejection(
        route_gate_http,
        tenant,
        lambda _request, _attempt: httpx.Response(200, json=body),
        ValidationError,
    )


@pytest.mark.asyncio
async def test_a_canonical_response_is_accepted_so_the_rejections_mean_something(
    route_gate_http: Any, tenant: TenantContext
) -> None:
    """Guard against a vacuous suite: the well-formed case must pass."""
    request = _request(tenant)
    with route_gate_http(
        lambda _r, _a: httpx.Response(200, json=_canonical_response(request))
    ) as transport:
        response = await _client().send_to_gate(request)

    assert isinstance(response, TransportPacket)
    assert response.payload == {"state": "completed", "fields": {"website": "https://example.com"}}
    assert len(transport.requests) == 1


# ---------------------------------------------------------------------------
# Structural malformation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "description"),
    [
        ({}, "empty object"),
        ({"header": {"action": "converge"}}, "envelope fragment"),
        ({"packet": {"header": {}}, "ok": True}, "packet nested under a wrapper key"),
    ],
)
async def test_structurally_invalid_packet_bodies_are_rejected(
    route_gate_http: Any, tenant: TenantContext, body: dict[str, Any], description: str
) -> None:
    """A dict that is not a packet is not a packet, however plausible it looks."""
    await _expect_rejection(
        route_gate_http,
        tenant,
        lambda _request, _attempt: httpx.Response(200, json=body),
        ValidationError,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [["not", "an", "object"], "ok", 42, True], ids=str)
async def test_non_object_response_bodies_are_rejected(
    route_gate_http: Any, tenant: TenantContext, body: Any
) -> None:
    """JSON that is valid but is not an object never reaches packet parsing."""
    error = await _expect_rejection(
        route_gate_http,
        tenant,
        lambda _request, _attempt: httpx.Response(200, json=body),
        ValueError,
    )
    assert "JSON object" in str(error)


@pytest.mark.asyncio
async def test_a_non_json_response_body_is_rejected(
    route_gate_http: Any, tenant: TenantContext
) -> None:
    """An HTML error page from a proxy is not a transport response."""
    await _expect_rejection(
        route_gate_http,
        tenant,
        lambda _request, _attempt: httpx.Response(
            200, content=b"<html>gateway</html>", headers={"Content-Type": "text/html"}
        ),
        json.JSONDecodeError,
    )


@pytest.mark.asyncio
async def test_an_unknown_top_level_key_is_rejected(
    route_gate_http: Any, tenant: TenantContext
) -> None:
    """``extra="forbid"`` means an augmented envelope is a rejected envelope."""
    request = _request(tenant)
    body = copy.deepcopy(_canonical_response(request))
    body["injected_section"] = {"anything": True}

    await _expect_rejection(
        route_gate_http,
        tenant,
        lambda _request, _attempt: httpx.Response(200, json=body),
        ValidationError,
    )


# ---------------------------------------------------------------------------
# Failed integrity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_response_with_a_mutated_payload_is_rejected(
    route_gate_http: Any, tenant: TenantContext
) -> None:
    """A packet-shaped body whose payload was edited in flight fails closed."""
    request = _request(tenant)
    body = copy.deepcopy(_canonical_response(request))
    body["payload"]["fields"]["website"] = "https://attacker.example"

    error = await _expect_rejection(
        route_gate_http,
        tenant,
        lambda _request, _attempt: httpx.Response(200, json=body),
        TransportIntegrityError,
    )
    assert "payload_hash" in str(error)


@pytest.mark.asyncio
async def test_a_response_with_a_repaired_payload_hash_is_still_rejected(
    route_gate_http: Any, tenant: TenantContext
) -> None:
    """Recomputing the payload digest moves the failure to ``transport_hash``."""
    request = _request(tenant)
    body = copy.deepcopy(_canonical_response(request))
    body["payload"]["fields"]["website"] = "https://attacker.example"
    body["security"]["payload_hash"] = compute_payload_hash(body["payload"])

    error = await _expect_rejection(
        route_gate_http,
        tenant,
        lambda _request, _attempt: httpx.Response(200, json=body),
        TransportIntegrityError,
    )
    assert "transport_hash" in str(error)


# ---------------------------------------------------------------------------
# HTTP-level failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [400, 404, 429, 500, 502, 503, 504])
async def test_an_http_error_never_yields_a_packet(
    route_gate_http: Any, tenant: TenantContext, status_code: int
) -> None:
    """
    A non-2xx response raises rather than returning whatever the body held.

    The body deliberately carries a canonical, valid packet: if the client
    ever parsed before checking status, this would return a success.
    """
    request = _request(tenant)
    body = _canonical_response(request)

    await _expect_rejection(
        route_gate_http,
        tenant,
        lambda _request, _attempt: httpx.Response(status_code, json=body),
        httpx.HTTPStatusError,
    )


@pytest.mark.asyncio
async def test_a_transport_failure_never_yields_a_packet(
    route_gate_http: Any, tenant: TenantContext
) -> None:
    """A dead socket surfaces as a transport error, not as an empty result."""

    def explode(_request: httpx.Request, _attempt: int) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    await _expect_rejection(route_gate_http, tenant, explode, httpx.ConnectError)
