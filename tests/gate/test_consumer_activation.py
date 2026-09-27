"""
Consumer participation is owned by the SDK (L9-PARTICIPATION-01).

A correctly configured consumer confirms its admission and its granted actions
with one SDK call, and classifies Gate's refusals from typed errors — no
consumer-side failure classifier, no ``os.environ`` writes to build a config.
Gate decides; ``activate()`` only asks.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from constellation_node_sdk import (
    ADMISSION_ACTION,
    ConsumerAccessReceipt,
    GateAuthorizationError,
    GateClient,
    GateConfigurationError,
    GateConnectionError,
    GateHTTPError,
    GateResponseError,
    GateSecurityError,
    GateTimeoutError,
    activate_consumer,
    get_gate_client_config_from_env,
    sign_transport_packet,
)
from constellation_node_sdk.gate.errors import GateClientError, gate_http_error
from constellation_node_sdk.transport.packet import TransportPacket
from tests.gate_client_helpers import RecordingTransport, gate_response_for, make_client_config

ODOO_KEY = "odoo-secret-0123456789"
GATE_KEY = "gate-secret-0123456789"


def _signed_config(**overrides: Any) -> Any:
    values: dict[str, Any] = {
        "signing_key": ODOO_KEY,
        "signing_key_id": "odoo-k1",
        "signing_algorithm": "hmac-sha256",
        "require_signature": True,
        "verify_response_signatures": True,
        "verifying_keys": {"gate-k1": GATE_KEY},
    }
    values.update(overrides)
    return make_client_config(**values)


def _receipt_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": "l9.gate.admission.v1",
        "gate_node": "gate",
        "key_id": "odoo-k1",
        "source_node": "odoo",
        "scope": "restricted",
        "scoped_actions": ["converge", "match"],
        "granted_actions": ["converge", "match"],
        "routable_actions": ["converge", "match", "sync"],
    }
    payload.update(overrides)
    return payload


def _gate(payload: dict[str, Any], *, key: str = GATE_KEY, key_id: str = "gate-k1") -> Any:
    def respond(request: httpx.Request, _attempt: int) -> httpx.Response:
        sent = TransportPacket.model_validate(json.loads(request.content.decode("utf-8")))
        response = sign_transport_packet(
            gate_response_for(sent, payload), key=key, key_id=key_id, algorithm="hmac-sha256"
        )
        return httpx.Response(200, json=response.model_dump_json_dict())

    return respond


def _status(code: int, body: Any) -> Any:
    def respond(_request: httpx.Request, _attempt: int) -> httpx.Response:
        return httpx.Response(code, json=body)

    return respond


# ── activate() ──────────────────────────────────────────────────────────────


async def test_activate_returns_gates_answer_as_a_typed_receipt() -> None:
    transport = RecordingTransport(_gate(_receipt_payload()))
    client = GateClient(_signed_config(), transport=transport)

    receipt = await client.activate(required_actions=("converge", "MATCH"))

    assert isinstance(receipt, ConsumerAccessReceipt)
    assert receipt.admitted is True
    assert receipt.scope == "restricted"
    assert receipt.granted_actions == ("converge", "match")
    assert receipt.required_actions == ("converge", "match")
    assert receipt.permits("converge") and not receipt.permits("sync")
    assert len(transport.requests) == 1
    assert transport.requests[0].url.path == "/v1/admission"
    probe = transport.sent_packet()
    assert probe["header"]["action"] == ADMISSION_ACTION
    assert probe["security"]["signing_key_id"] == "odoo-k1"
    assert probe["address"]["destination_node"] == "gate"
    assert probe["payload"] == {}


async def test_missing_required_action_is_a_typed_refusal() -> None:
    transport = RecordingTransport(_gate(_receipt_payload()))

    with pytest.raises(GateAuthorizationError) as caught:
        await activate_consumer(
            _signed_config(), required_actions=["converge", "sync"], transport=transport
        )

    assert caught.value.code == "action_not_permitted"
    assert caught.value.status_code == 403
    assert "sync" in str(caught.value)
    assert caught.value.retryable is False
    assert len(transport.requests) == 1


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"signing_key": None, "signing_key_id": None}, "no signing key"),
        ({"signing_key_id": None}, "without a key id"),
        ({"verifying_keys": {}}, "no verifying keys"),
    ],
)
async def test_unprovable_identity_is_a_configuration_error_before_any_request(
    overrides: dict[str, Any], expected: str
) -> None:
    transport = RecordingTransport(_gate(_receipt_payload()))
    client = GateClient(_signed_config(**overrides), transport=transport)

    with pytest.raises(GateConfigurationError, match=expected):
        await client.activate()

    assert transport.requests == []


async def test_receipt_not_signed_by_a_known_gate_key_is_refused() -> None:
    transport = RecordingTransport(
        _gate(_receipt_payload(), key="impostor-secret-0123456", key_id="impostor")
    )

    with pytest.raises(GateSecurityError):
        await GateClient(_signed_config(), transport=transport).activate()


async def test_receipt_with_an_unknown_schema_is_refused() -> None:
    transport = RecordingTransport(_gate(_receipt_payload(schema="something-else")))

    with pytest.raises(GateResponseError):
        await GateClient(_signed_config(), transport=transport).activate()


async def test_unknown_key_rejection_is_an_http_error_not_authorization() -> None:
    body = {"detail": {"code": "invalid_transport_packet", "message": "unknown key id"}}
    transport = RecordingTransport(_status(400, body))

    with pytest.raises(GateHTTPError) as caught:
        await GateClient(_signed_config(), transport=transport).activate()

    assert not isinstance(caught.value, GateAuthorizationError)
    assert caught.value.code == "invalid_transport_packet"


# ── typed Gate refusals on execute ──────────────────────────────────────────


async def test_execute_refused_by_key_scope_raises_a_typed_authorization_error() -> None:
    body = {"detail": {"code": "action_not_permitted", "message": "key 'odoo-k1' ..."}}
    transport = RecordingTransport(_status(403, body))
    client = GateClient(_signed_config(), transport=transport)

    with pytest.raises(GateAuthorizationError) as caught:
        await client.execute(action="sync", payload={}, tenant="plasticos")

    assert caught.value.code == "action_not_permitted"
    assert caught.value.retryable is False


@pytest.mark.parametrize(
    ("status", "authorization", "retryable"),
    [
        (400, False, False),
        (401, True, False),
        (403, True, False),
        (404, False, False),
        (408, False, True),
        (409, False, False),
        (429, False, True),
        (500, False, True),
        (501, False, False),
        (502, False, True),
        (503, False, True),
        (504, False, True),
        (505, False, False),
    ],
)
def test_http_status_classification(status: int, authorization: bool, retryable: bool) -> None:
    error = gate_http_error("x", status_code=status, response_text="not json")

    assert isinstance(error, GateAuthorizationError) is authorization
    assert error.retryable is retryable
    assert error.code is None


def test_retryability_of_non_http_failures() -> None:
    assert GateConnectionError("down").retryable is True
    assert GateTimeoutError("slow", timeout_seconds=1.0).retryable is True
    assert GateConfigurationError("bad").retryable is False
    assert GateResponseError("garbled").retryable is False
    assert GateSecurityError("forged", direction="inbound").retryable is False
    assert GateClientError("base").retryable is False


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ('{"detail": {"code": "action_not_permitted"}}', "action_not_permitted"),
        ('{"code": "no_healthy_node"}', "no_healthy_node"),
        ('{"detail": "plain string"}', None),
        ("[1, 2]", None),
        ("", None),
    ],
)
def test_gate_error_code_parsing(text: str, code: str | None) -> None:
    assert gate_http_error("x", status_code=503, response_text=text).code == code


# ── configuration without os.environ writes ─────────────────────────────────


def test_client_config_overrides_win_and_need_no_gate_url_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GATE_URL", raising=False)
    monkeypatch.setenv("L9_NODE_NAME", "enrichment-engine")

    config = get_gate_client_config_from_env(gate_url="http://gate:9000", timeout_seconds=5.0)

    assert config.gate_url == "http://gate:9000"
    assert config.timeout_seconds == 5.0
    assert config.local_node == "enrichment-engine"


def test_client_config_rejects_unknown_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GATE_URL", "http://gate:9000")

    with pytest.raises(ValueError, match="unknown GateClientConfig fields"):
        get_gate_client_config_from_env(gate_uri="http://typo")


def test_client_config_still_requires_a_gate_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GATE_URL", raising=False)

    with pytest.raises(ValueError, match="GATE_URL is required"):
        get_gate_client_config_from_env()
