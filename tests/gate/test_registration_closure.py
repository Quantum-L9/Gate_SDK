"""
Registration closure (ADR-SDK-008).

A conforming node must not need bespoke HTTP to express a valid Gate
registration. Two concrete gaps drove a real consumer to write its own client:
the SDK could not emit ``metadata.owner``, and registration was reachable only
through a ``spec.yaml`` on disk. Both are closed here.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from gate_client_helpers import RecordingTransport

from constellation_node_sdk.gate.registration import (
    NodeRegistration,
    build_node_registration,
    build_registration_payload,
    register_node,
)

# ---------------------------------------------------------------------------
# Owner metadata — the field Gate requires for canonical actions
# ---------------------------------------------------------------------------


def test_owner_reaches_gate_as_metadata_owner() -> None:
    """
    Gate resolves a canonical action's semantic owner from ``metadata.owner``.

    Without it, Gate rejects the registration of any canonical action whose node
    name it cannot map to an owner. The SDK previously had no way to send it.
    """
    registration = NodeRegistration(
        node_name="enrichment-engine",
        internal_url="http://enrichment-engine:8000",
        supported_actions=("converge", "graph-inference-result"),
        health_endpoint="/api/v1/health",
        version="2.3.0",
        node_type="enrichment",
        owner="eie",
    )

    body = registration.to_payload()["enrichment-engine"]
    assert body["metadata"]["owner"] == "eie"
    assert body["metadata"]["version"] == "2.3.0"
    assert body["metadata"]["type"] == "enrichment"
    assert body["health_endpoint"] == "/api/v1/health"
    assert body["supported_actions"] == ["converge", "graph-inference-result"]


def test_owner_is_omitted_when_not_claimed() -> None:
    """A node that claims no owner sends none, rather than an empty string."""
    registration = NodeRegistration(
        node_name="worker",
        internal_url="http://worker:8000",
        supported_actions=("do-thing",),
    )
    assert "owner" not in registration.to_payload()["worker"]["metadata"]


def test_owner_arrives_from_spec_yaml_too() -> None:
    spec = {
        "node": {
            "id": "enrichment-engine",
            "actions": ["converge"],
            "owner": "EIE",
            "health_endpoint": "/api/v1/health",
        }
    }
    payload = build_registration_payload(spec)["enrichment-engine"]
    assert payload["metadata"]["owner"] == "eie"
    assert payload["health_endpoint"] == "/api/v1/health"


# ---------------------------------------------------------------------------
# Metadata stays control-plane metadata
# ---------------------------------------------------------------------------


def test_extra_metadata_is_carried() -> None:
    registration = NodeRegistration(
        node_name="worker",
        internal_url="http://worker:8000",
        supported_actions=("do-thing",),
        metadata={"region": "eu-central", "deployment": "blue"},
    )
    metadata = registration.to_payload()["worker"]["metadata"]
    assert metadata["region"] == "eu-central"
    assert metadata["deployment"] == "blue"


@pytest.mark.parametrize("reserved", ["owner", "version", "type", "generated_by"])
def test_sdk_derived_metadata_keys_cannot_be_overridden(reserved: str) -> None:
    """
    ``metadata`` is control-plane metadata, not a generic escape hatch.

    A caller who wants a different owner sets the field; smuggling it through
    ``metadata`` would let two sources disagree about the same registration.
    """
    with pytest.raises(ValueError, match="derived by the SDK"):
        NodeRegistration(
            node_name="worker",
            internal_url="http://worker:8000",
            supported_actions=("do-thing",),
            metadata={reserved: "smuggled"},
        )


def test_metadata_values_must_be_strings() -> None:
    """Gate's registration schema accepts ``dict[str, str]``; fail here, not on the wire."""
    with pytest.raises(ValueError, match="must be a string"):
        NodeRegistration(
            node_name="worker",
            internal_url="http://worker:8000",
            supported_actions=("do-thing",),
            metadata={"max_batch": 10},
        )


def test_registration_is_not_a_domain_payload_surface() -> None:
    """No free-form object field through which a domain payload could ride along."""
    assert set(NodeRegistration.model_fields) == {
        "node_name",
        "internal_url",
        "supported_actions",
        "priority_class",
        "max_concurrent",
        "health_endpoint",
        "timeout_ms",
        "version",
        "node_type",
        "owner",
        "metadata",
    }


# ---------------------------------------------------------------------------
# Gate schema conformance
# ---------------------------------------------------------------------------


def test_payload_keys_match_the_gate_registration_schema() -> None:
    """
    Gate's ``NodeRegistrationInput`` forbids extra keys.

    An unknown key is a 422, so the rendered body must contain exactly the keys
    Gate accepts — no ``execute_path``, no ``health_path``, no top-level owner.
    """
    body = NodeRegistration(
        node_name="worker",
        internal_url="http://worker:8000",
        supported_actions=("do-thing",),
        owner="eie",
    ).to_payload()["worker"]

    assert set(body) == {
        "internal_url",
        "supported_actions",
        "priority_class",
        "max_concurrent",
        "health_endpoint",
        "timeout_ms",
        "metadata",
    }


@pytest.mark.parametrize("bad_priority", ["P9", "urgent", ""])
def test_priority_class_is_validated_locally(bad_priority: str) -> None:
    with pytest.raises(ValueError):
        NodeRegistration(
            node_name="worker",
            internal_url="http://worker:8000",
            supported_actions=("do-thing",),
            priority_class=bad_priority,
        )


def test_health_endpoint_must_be_a_path() -> None:
    with pytest.raises(ValueError, match="must start with /"):
        NodeRegistration(
            node_name="worker",
            internal_url="http://worker:8000",
            supported_actions=("do-thing",),
            health_endpoint="v1/health",
        )


def test_duplicate_actions_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicates"):
        NodeRegistration(
            node_name="worker",
            internal_url="http://worker:8000",
            supported_actions=("do-thing", "do-thing"),
        )


def test_internal_url_must_be_absolute() -> None:
    with pytest.raises(ValueError, match="http"):
        NodeRegistration(
            node_name="worker",
            internal_url="worker:8000",
            supported_actions=("do-thing",),
        )


def test_spec_yaml_and_typed_registration_render_the_same_body() -> None:
    """The two entry points are one contract, not two."""
    spec = {
        "node": {
            "id": "worker",
            "actions": ["do-thing"],
            "internal_url": "http://worker:8000",
            "owner": "eie",
        }
    }
    assert build_registration_payload(spec) == build_node_registration(spec).to_payload()


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


def _patch_registration_transport(monkeypatch: Any, transport: RecordingTransport) -> None:
    original = httpx.AsyncClient

    class PatchedAsyncClient(httpx.AsyncClient):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = transport
            original.__init__(self, *args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", PatchedAsyncClient)


@pytest.mark.asyncio
async def test_register_node_needs_no_spec_file_on_disk(monkeypatch: Any) -> None:
    """
    A node whose identity comes from application settings registers directly.

    This is the second half of the closure: without it, a settings-driven node
    has to write its own admin HTTP client even when the payload is expressible.
    """
    transport = RecordingTransport(lambda _r, _a: httpx.Response(200, json={"registered": []}))
    _patch_registration_transport(monkeypatch, transport)

    ok = await register_node(
        gate_url="http://gate:8000",
        registration=NodeRegistration(
            node_name="enrichment-engine",
            internal_url="http://enrichment-engine:8000",
            supported_actions=("converge", "graph-inference-result"),
            health_endpoint="/api/v1/health",
            version="2.3.0",
            node_type="enrichment",
            owner="eie",
        ),
        admin_token="admin-secret",
    )

    assert ok is True
    request = transport.requests[0]
    assert str(request.url) == "http://gate:8000/v1/admin/register?overwrite=true"
    assert request.headers["X-Admin-Token"] == "admin-secret"
    body = json.loads(request.content.decode("utf-8"))
    assert body["enrichment-engine"]["metadata"]["owner"] == "eie"


@pytest.mark.asyncio
async def test_admin_token_header_is_absent_when_unset(monkeypatch: Any) -> None:
    transport = RecordingTransport(lambda _r, _a: httpx.Response(200, json={}))
    _patch_registration_transport(monkeypatch, transport)

    await register_node(
        gate_url="http://gate:8000",
        registration=NodeRegistration(
            node_name="worker",
            internal_url="http://worker:8000",
            supported_actions=("do-thing",),
        ),
    )
    assert "x-admin-token" not in transport.requests[0].headers


@pytest.mark.parametrize("status_code", [400, 401, 403, 409, 422])
@pytest.mark.asyncio
async def test_gate_rejection_is_not_retried(monkeypatch: Any, status_code: int) -> None:
    """A rejection is a decision, not a transient failure. Retrying it is noise."""
    transport = RecordingTransport(lambda _r, _a: httpx.Response(status_code, json={}))
    _patch_registration_transport(monkeypatch, transport)

    ok = await register_node(
        gate_url="http://gate:8000",
        registration=NodeRegistration(
            node_name="worker",
            internal_url="http://worker:8000",
            supported_actions=("do-thing",),
        ),
        retries=3,
    )

    assert ok is False
    assert len(transport.requests) == 1


@pytest.mark.asyncio
async def test_registration_retry_is_bounded_and_visible(monkeypatch: Any) -> None:
    """
    Registration may retry — it is control-plane reconciliation, not execution.

    The exemption is bounded and does not extend to ``GateClient.execute``.
    """
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("constellation_node_sdk.gate.registration.asyncio.sleep", fake_sleep)

    def explode(_request: httpx.Request, _attempt: int) -> httpx.Response:
        raise httpx.ConnectError("gate not up yet")

    transport = RecordingTransport(explode)
    _patch_registration_transport(monkeypatch, transport)

    ok = await register_node(
        gate_url="http://gate:8000",
        registration=NodeRegistration(
            node_name="worker",
            internal_url="http://worker:8000",
            supported_actions=("do-thing",),
        ),
        retries=3,
    )

    assert ok is False
    assert len(transport.requests) == 3
    assert slept == [1.0, 2.0]


@pytest.mark.asyncio
async def test_registration_never_raises_into_node_startup(monkeypatch: Any) -> None:
    """A missing Gate must not crash a node; registration failure is non-fatal."""
    transport = RecordingTransport(
        lambda _r, _a: (_ for _ in ()).throw(httpx.ConnectError("no gate"))
    )
    _patch_registration_transport(monkeypatch, transport)
    monkeypatch.setattr(
        "constellation_node_sdk.gate.registration.asyncio.sleep",
        lambda _s: __import__("asyncio").sleep(0),
    )

    assert (
        await register_node(
            gate_url="http://gate:8000",
            registration=NodeRegistration(
                node_name="worker",
                internal_url="http://worker:8000",
                supported_actions=("do-thing",),
            ),
            retries=1,
        )
        is False
    )
