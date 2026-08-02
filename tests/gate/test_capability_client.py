"""Capability client: Gate fixture parity + topology rejection + cache."""

from __future__ import annotations

import httpx
import pytest
from pydantic import ValidationError

from constellation_node_sdk.gate import capability_client as capability_module
from constellation_node_sdk.gate.capabilities import (
    CapabilityDescriptor,
    CapabilityListResponse,
)
from constellation_node_sdk.gate.capability_client import GateCapabilityClient
from constellation_node_sdk.gate.config import GateClientConfig
from constellation_node_sdk.gate.errors import GateClientError


def _config() -> GateClientConfig:
    return GateClientConfig(gate_url="http://gate:8000", local_node="worker-a")


class _RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(self, responses: list[httpx.Response | Exception]) -> None:
        self._responses = list(responses)
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self._responses:
            raise AssertionError("unexpected request")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _patch_client(monkeypatch: pytest.MonkeyPatch, transport: httpx.AsyncBaseTransport) -> None:
    original = httpx.AsyncClient

    class PatchedAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(capability_module.httpx, "AsyncClient", PatchedAsyncClient)
    monkeypatch.setattr(httpx, "AsyncClient", PatchedAsyncClient)
    _ = original


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "score", "internal_url": "http://x"},
        {"action": "score", "healthy": True},
        {"action": "score", "node_name": "score"},
        {"action": "score", "active_requests": 1},
        {"action": "score", "nested": {"topology": {}}},
    ],
)
def test_capability_descriptor_rejects_topology_fields(payload: dict) -> None:
    with pytest.raises(ValidationError):
        CapabilityDescriptor.model_validate(payload)


def test_released_gate_fixture_parity_shape() -> None:
    """Fixture shape matches Gate TASK-013 capability projection."""
    fixture = {
        "contract_version": "1.0.0",
        "etag": 'W/"abc123"',
        "capabilities": [
            {
                "action": "score",
                "owner": None,
                "contract_version": "1.0.0",
                "advertised": True,
            },
            {
                "action": "match",
                "owner": "ceg",
                "contract_version": "1.0.0",
                "advertised": False,
            },
        ],
    }
    parsed = CapabilityListResponse.model_validate(fixture)
    assert parsed.etag.startswith("W/")
    assert parsed.capabilities[1].owner == "ceg"
    assert all("internal_url" not in c.model_dump() for c in parsed.capabilities)


@pytest.mark.asyncio
async def test_capability_client_caches_etag_and_invalidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = {
        "contract_version": "1.0.0",
        "etag": 'W/"deadbeef"',
        "capabilities": [
            {
                "action": "score",
                "owner": None,
                "contract_version": "1.0.0",
                "advertised": True,
            }
        ],
    }
    transport = _RecordingTransport(
        [
            httpx.Response(200, json=body, headers={"ETag": 'W/"deadbeef"'}),
            httpx.Response(200, json=body, headers={"ETag": 'W/"deadbeef"'}),
        ]
    )
    _patch_client(monkeypatch, transport)
    client = GateCapabilityClient(_config(), cache_ttl_seconds=60.0)

    first = await client.list_capabilities()
    second = await client.list_capabilities()
    assert first.capabilities[0].action == "score"
    assert second.capabilities[0].action == "score"
    # TTL cache hit — only one HTTP call so far
    assert len(transport.requests) == 1

    client.invalidate()
    third = await client.list_capabilities()
    assert third.etag == 'W/"deadbeef"'
    assert len(transport.requests) == 2


@pytest.mark.asyncio
async def test_capability_client_honors_304_revalidation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = {
        "contract_version": "1.0.0",
        "etag": 'W/"cafe"',
        "capabilities": [
            {
                "action": "score",
                "owner": None,
                "contract_version": "1.0.0",
                "advertised": True,
            }
        ],
    }
    transport = _RecordingTransport(
        [
            httpx.Response(200, json=body, headers={"ETag": 'W/"cafe"'}),
            httpx.Response(304, headers={"ETag": 'W/"cafe"'}),
        ]
    )
    _patch_client(monkeypatch, transport)
    client = GateCapabilityClient(_config(), cache_ttl_seconds=0.01)
    first = await client.list_capabilities()
    assert first.etag == 'W/"cafe"'
    # Expire TTL then revalidate via If-None-Match → 304
    import time

    time.sleep(0.02)
    second = await client.list_capabilities()
    assert second.etag == 'W/"cafe"'
    assert len(transport.requests) == 2
    assert transport.requests[1].headers.get("if-none-match") == 'W/"cafe"'


@pytest.mark.asyncio
async def test_capability_client_fails_closed_on_topology_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _RecordingTransport(
        [
            httpx.Response(
                200,
                json={
                    "action": "score",
                    "internal_url": "http://score:8000",
                    "contract_version": "1.0.0",
                    "advertised": True,
                },
            )
        ]
    )
    _patch_client(monkeypatch, transport)
    client = GateCapabilityClient(_config())
    with pytest.raises(GateClientError, match="failed closed"):
        await client.get_capability("score")
