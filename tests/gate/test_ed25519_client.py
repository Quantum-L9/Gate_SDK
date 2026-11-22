"""
D-2 and D-3: an ed25519 GateClient can sign, self-validate and be configured from env.

D-2: ``GateClientConfig.resolve_verifying_key`` resolved the client's own key id
to ``signing_key``. Under ed25519 that is the private key, so the outbound
validation of the packet the client had just signed always failed with
``invalid ed25519 transport signature``. It now resolves to the public key
derived from the private key. HMAC still resolves to the shared secret.

D-3: ``get_gate_client_config_from_env`` read ``signing_key`` only from
``L9_SIGNING_KEY``/``L9_SIGNING_SECRET`` while the worker runtime refuses
``L9_SIGNING_KEY`` under ed25519 and reads ``L9_SIGNING_PRIVATE_KEY``. A node
that was both could not be configured. Under ed25519 both now read
``L9_SIGNING_PRIVATE_KEY``.
"""

from __future__ import annotations

import base64

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from constellation_node_sdk.gate.client import GateClient
from constellation_node_sdk.gate.config import GateClientConfig, get_gate_client_config_from_env
from constellation_node_sdk.gate_authority.config import GateDispatchTransportConfig
from constellation_node_sdk.runtime.config import get_runtime_config
from constellation_node_sdk.security.signing import sign_transport_packet
from constellation_node_sdk.security.validation import validate_transport_packet
from constellation_node_sdk.transport.packet import create_transport_packet
from constellation_node_sdk.transport.provenance import RoutingProvenance

SIGNING_ENV = (
    "L9_SIGNING_ALGORITHM",
    "L9_SIGNING_KEY",
    "L9_SIGNING_SECRET",
    "L9_SIGNING_PRIVATE_KEY",
    "L9_SIGNING_KEY_ID",
)


def _identity() -> tuple[str, str]:
    private_key = Ed25519PrivateKey.generate()
    return (
        base64.b64encode(private_key.private_bytes_raw()).decode(),
        base64.b64encode(private_key.public_key().public_bytes_raw()).decode(),
    )


def _outbound_packet():
    return create_transport_packet(
        action="enrich",
        payload={"entity_id": "42"},
        tenant="tenant-a",
        destination_node="gate",
        source_node="eie",
        reply_to="eie",
        provenance=RoutingProvenance(
            origin_kind="node",
            requested_action="enrich",
            resolved_by_gate=False,
            original_source_node="eie",
        ),
    )


class _SignedGateResponse(httpx.AsyncBaseTransport):
    """Answers like Gate: an ed25519-signed TransportPacket addressed to the caller."""

    def __init__(self, gate_private: str) -> None:
        self._gate_private = gate_private
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        response = sign_transport_packet(
            create_transport_packet(
                action="enrich",
                payload={"status": "completed"},
                tenant="tenant-a",
                destination_node="eie",
                source_node="gate",
                reply_to="gate",
            ),
            key=self._gate_private,
            key_id="gate-gl",
            algorithm="ed25519",
        )
        return httpx.Response(200, json=response.model_dump_json_dict(), request=request)


# --- D-2 ---------------------------------------------------------------------


def test_ed25519_client_resolves_its_own_key_id_to_its_public_key() -> None:
    private_b64, public_b64 = _identity()
    config = GateClientConfig(
        gate_url="http://gate:8000",
        local_node="eie",
        signing_key=private_b64,
        signing_key_id="eie-gl",
        signing_algorithm="ed25519",
    )

    resolved = config.resolve_verifying_key("eie-gl")

    assert resolved == base64.b64decode(public_b64)
    assert resolved != base64.b64decode(private_b64)


def test_ed25519_client_derives_public_key_from_pem_private_key() -> None:
    from cryptography.hazmat.primitives import serialization

    private_key = Ed25519PrivateKey.generate()
    pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    config = GateClientConfig(
        gate_url="http://gate:8000",
        local_node="eie",
        signing_key=pem,
        signing_key_id="eie-gl",
        signing_algorithm="ed25519",
    )

    assert config.resolve_verifying_key("eie-gl") == private_key.public_key().public_bytes_raw()


def test_hmac_client_still_resolves_its_own_key_id_to_the_shared_secret() -> None:
    config = GateClientConfig(
        gate_url="http://gate:8000",
        local_node="eie",
        signing_key="shared-secret",
        signing_key_id="eie-hmac",
        signing_algorithm="hmac-sha256",
    )

    assert config.resolve_verifying_key("eie-hmac") == "shared-secret"


def test_gate_dispatch_config_resolves_its_own_ed25519_key_id_to_its_public_key() -> None:
    private_b64, public_b64 = _identity()
    config = GateDispatchTransportConfig(
        signing_key=private_b64, signing_key_id="gate-gl", signing_algorithm="ed25519"
    )

    assert config.resolve_verifying_key("gate-gl") == base64.b64decode(public_b64)


def test_own_ed25519_packet_validates_against_resolved_key() -> None:
    private_b64, _ = _identity()
    config = GateClientConfig(
        gate_url="http://gate:8000",
        local_node="eie",
        require_signature=True,
        signing_key=private_b64,
        signing_key_id="eie-gl",
        signing_algorithm="ed25519",
    )
    signed = sign_transport_packet(
        _outbound_packet(), key=private_b64, key_id="eie-gl", algorithm="ed25519"
    )

    validate_transport_packet(
        signed,
        key_resolver=config.resolve_verifying_key,
        key_algorithm="ed25519",
        require_signature=True,
    )


@pytest.mark.asyncio
async def test_ed25519_client_sends_signed_packet_and_verifies_gate_response() -> None:
    client_private, _ = _identity()
    gate_private, gate_public = _identity()
    transport = _SignedGateResponse(gate_private)
    config = GateClientConfig(
        gate_url="http://gate:8000",
        local_node="eie",
        require_signature=True,
        signing_key=client_private,
        signing_key_id="eie-gl",
        signing_algorithm="ed25519",
        verify_response_signatures=True,
        verifying_keys={"gate-gl": gate_public},
    )

    response = await GateClient(config, transport=transport).send_to_gate(_outbound_packet())

    assert response.payload["status"] == "completed"
    assert len(transport.requests) == 1


# --- D-3 ---------------------------------------------------------------------


@pytest.fixture
def clean_signing_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in SIGNING_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GATE_URL", "http://gate:8000")
    get_runtime_config.cache_clear()
    yield monkeypatch
    get_runtime_config.cache_clear()


def test_ed25519_client_and_runtime_read_the_same_private_key_variable(
    clean_signing_env: pytest.MonkeyPatch,
) -> None:
    private_b64, public_b64 = _identity()
    clean_signing_env.setenv("L9_SIGNING_ALGORITHM", "ed25519")
    clean_signing_env.setenv("L9_SIGNING_PRIVATE_KEY", private_b64)
    clean_signing_env.setenv("L9_SIGNING_KEY_ID", "eie-gl")

    client_config = get_gate_client_config_from_env()
    runtime_config = get_runtime_config()

    assert client_config.signing_key == private_b64
    assert runtime_config.signing_private_key == private_b64
    assert client_config.resolve_verifying_key("eie-gl") == base64.b64decode(public_b64)
    assert client_config.admission_problems() == [
        "no verifying keys (L9_VERIFYING_KEYS_JSON) to authenticate Gate's admission receipt"
    ]


def test_ed25519_client_env_refuses_l9_signing_key(clean_signing_env: pytest.MonkeyPatch) -> None:
    private_b64, _ = _identity()
    clean_signing_env.setenv("L9_SIGNING_ALGORITHM", "ed25519")
    clean_signing_env.setenv("L9_SIGNING_KEY", private_b64)

    with pytest.raises(ValueError, match="must not be used with ed25519"):
        get_gate_client_config_from_env()


def test_signing_algorithm_override_selects_the_private_key_variable(
    clean_signing_env: pytest.MonkeyPatch,
) -> None:
    private_b64, _ = _identity()
    clean_signing_env.setenv("L9_SIGNING_PRIVATE_KEY", private_b64)

    config = get_gate_client_config_from_env(signing_algorithm="ed25519")

    assert config.signing_key == private_b64


def test_hmac_client_env_is_unchanged(clean_signing_env: pytest.MonkeyPatch) -> None:
    clean_signing_env.setenv("L9_SIGNING_ALGORITHM", "hmac-sha256")
    clean_signing_env.setenv("L9_SIGNING_KEY", "shared-secret")
    clean_signing_env.setenv("L9_SIGNING_PRIVATE_KEY", "ignored-under-hmac")

    assert get_gate_client_config_from_env().signing_key == "shared-secret"


def test_unset_algorithm_client_env_is_unchanged(clean_signing_env: pytest.MonkeyPatch) -> None:
    clean_signing_env.setenv("L9_SIGNING_SECRET", "legacy-secret")

    assert get_gate_client_config_from_env().signing_key == "legacy-secret"
