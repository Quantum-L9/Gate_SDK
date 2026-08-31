"""
One deadline (ADR-SDK-004).

``TransportPacket.header.timeout_ms`` and the real network deadline used to be
independent knobs that every consumer had to keep in step by hand. These tests
read the timeout httpx actually applied to the request, not the one the SDK
claims, because a deadline that only agrees in the docstring is drift.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from gate_client_helpers import (
    RecordingTransport,
    gate_echo_responder,
    make_client_config,
    make_root_packet,
)

from constellation_node_sdk.gate.client import GateClient
from constellation_node_sdk.gate.config import GateClientConfig
from constellation_node_sdk.gate.errors import GateConfigurationError, GateTimeoutError


@pytest.mark.asyncio
async def test_caller_budget_reaches_both_the_packet_and_the_socket(
    domain_payload: dict[str, Any],
) -> None:
    """One caller budget, one packet header value, one real network deadline."""
    transport = RecordingTransport(gate_echo_responder({"state": "completed"}))
    client = GateClient(make_client_config(timeout_seconds=30.0), transport=transport)

    await client.execute(
        action="converge", payload=domain_payload, tenant="tenant-a", timeout_ms=7_000
    )

    assert transport.sent_packet(0)["header"]["timeout_ms"] == 7_000
    applied = transport.applied_timeout(0)
    assert applied["read"] == pytest.approx(7.0)
    assert applied["connect"] == pytest.approx(7.0)


@pytest.mark.asyncio
async def test_default_budget_comes_from_configuration(
    domain_payload: dict[str, Any],
) -> None:
    """With no explicit budget the configured default is used for both."""
    transport = RecordingTransport(gate_echo_responder({"state": "completed"}))
    client = GateClient(make_client_config(timeout_seconds=12.0), transport=transport)

    await client.execute(action="converge", payload=domain_payload, tenant="tenant-a")

    assert transport.sent_packet(0)["header"]["timeout_ms"] == 12_000
    assert transport.applied_timeout(0)["read"] == pytest.approx(12.0)


@pytest.mark.asyncio
async def test_network_deadline_never_outlives_the_advertised_budget(
    domain_payload: dict[str, Any],
) -> None:
    """
    A short packet budget is not silently widened by a long client configuration.

    This is the failure the closure exists to prevent: the header promising 5s
    downstream while the caller actually blocks for 30s.
    """
    transport = RecordingTransport(gate_echo_responder({"state": "completed"}))
    client = GateClient(make_client_config(timeout_seconds=30.0), transport=transport)

    await client.execute(
        action="converge", payload=domain_payload, tenant="tenant-a", timeout_ms=5_000
    )

    applied = transport.applied_timeout(0)
    assert applied["read"] == pytest.approx(5.0)
    assert applied["read"] <= transport.sent_packet(0)["header"]["timeout_ms"] / 1000.0


@pytest.mark.asyncio
async def test_send_to_gate_takes_its_deadline_from_the_packet() -> None:
    """
    The packet-native primitive honours the budget the packet advertises.

    A packet-native caller has already declared its budget in the header; the
    client does not get to wait longer than that.
    """
    transport = RecordingTransport(gate_echo_responder({"state": "completed"}))
    client = GateClient(make_client_config(timeout_seconds=30.0), transport=transport)

    await client.send_to_gate(make_root_packet(timeout_ms=3_000))

    assert transport.applied_timeout(0)["read"] == pytest.approx(3.0)


@pytest.mark.asyncio
async def test_configured_ceiling_clamps_an_oversized_budget(
    domain_payload: dict[str, Any],
) -> None:
    """
    A deployment whose synchronous caller cannot outlive a fixed window can say so.

    The clamp applies to both the advertised header and the socket, so a
    downstream node is never told about a budget the caller will not honour.
    """
    transport = RecordingTransport(gate_echo_responder({"state": "completed"}))
    client = GateClient(
        make_client_config(timeout_seconds=30.0, max_timeout_ms=10_000), transport=transport
    )

    await client.execute(
        action="converge", payload=domain_payload, tenant="tenant-a", timeout_ms=120_000
    )

    assert transport.sent_packet(0)["header"]["timeout_ms"] == 10_000
    assert transport.applied_timeout(0)["read"] == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_no_ceiling_is_configured_by_default(
    domain_payload: dict[str, Any],
) -> None:
    """The ceiling is opt-in; the SDK does not impose a hidden maximum."""
    assert GateClientConfig.model_fields["max_timeout_ms"].default is None

    transport = RecordingTransport(gate_echo_responder({"state": "completed"}))
    client = GateClient(make_client_config(), transport=transport)

    await client.execute(
        action="converge", payload=domain_payload, tenant="tenant-a", timeout_ms=90_000
    )

    assert transport.applied_timeout(0)["read"] == pytest.approx(90.0)


@pytest.mark.asyncio
async def test_transport_margin_is_explicit_and_reserved_from_the_budget(
    domain_payload: dict[str, Any],
) -> None:
    """
    A margin lets the SDK raise a typed timeout before the caller's own deadline.

    It is opt-in and subtractive: the network deadline shortens, the advertised
    budget does not change, and nothing is reserved unless configured.
    """
    transport = RecordingTransport(gate_echo_responder({"state": "completed"}))
    client = GateClient(
        make_client_config(timeout_seconds=30.0, transport_margin_ms=500), transport=transport
    )

    await client.execute(
        action="converge", payload=domain_payload, tenant="tenant-a", timeout_ms=10_000
    )

    assert transport.sent_packet(0)["header"]["timeout_ms"] == 10_000
    assert transport.applied_timeout(0)["read"] == pytest.approx(9.5)


def test_no_margin_is_reserved_by_default() -> None:
    """Default 0: the network deadline equals the budget, with no hidden reservation."""
    assert GateClientConfig.model_fields["transport_margin_ms"].default == 0


@pytest.mark.asyncio
async def test_a_margin_that_consumes_the_budget_is_a_configuration_error(
    domain_payload: dict[str, Any],
) -> None:
    """A margin wider than the budget leaves no deadline; that is not silently ignored."""
    client = GateClient(
        make_client_config(transport_margin_ms=5_000),
        transport=RecordingTransport(gate_echo_responder({})),
    )

    with pytest.raises(GateConfigurationError):
        await client.execute(
            action="converge", payload=domain_payload, tenant="tenant-a", timeout_ms=1_000
        )


@pytest.mark.asyncio
async def test_a_non_positive_budget_is_a_configuration_error(
    domain_payload: dict[str, Any],
) -> None:
    client = GateClient(make_client_config(), transport=RecordingTransport(gate_echo_responder({})))

    with pytest.raises(GateConfigurationError):
        await client.execute(
            action="converge", payload=domain_payload, tenant="tenant-a", timeout_ms=0
        )


@pytest.mark.asyncio
async def test_an_elapsed_deadline_is_a_typed_timeout_carrying_the_budget(
    domain_payload: dict[str, Any],
) -> None:
    """
    A timeout is classifiable without reading the exception's string.

    httpx timeout exceptions frequently stringify to nothing, which is precisely
    why a consumer that classified on ``str(exc)`` recorded a blank reason.
    """

    def stall(_request: httpx.Request, _attempt: int) -> httpx.Response:
        raise httpx.ConnectTimeout("")

    transport = RecordingTransport(stall)
    client = GateClient(make_client_config(), transport=transport)

    with pytest.raises(GateTimeoutError) as caught:
        await client.execute(
            action="converge", payload=domain_payload, tenant="tenant-a", timeout_ms=4_000
        )

    assert caught.value.timeout_seconds == pytest.approx(4.0)
    assert "ConnectTimeout" in str(caught.value)
    assert len(transport.requests) == 1
