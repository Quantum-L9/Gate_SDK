from __future__ import annotations

import pytest
from pydantic import ValidationError

from constellation_node_sdk.gate.config import (
    GateClientConfig,
    GateRegistrationConfig,
    get_gate_client_config_from_env,
    get_gate_registration_config_from_env,
)


def _minimal_client_config(**overrides: object) -> GateClientConfig:
    defaults: dict[str, object] = {
        "gate_url": "http://gate:8000",
        "local_node": "orchestrator",
    }
    defaults.update(overrides)
    return GateClientConfig(**defaults)  # type: ignore[arg-type]


def test_gate_client_config_normalizes_gate_url_and_node_fields() -> None:
    config = _minimal_client_config(
        gate_url="  http://gate:8000/  ",
        local_node="  Orchestrator  ",
        allowed_gate_destination="  Gate  ",
    )

    assert config.gate_url == "http://gate:8000"
    assert config.local_node == "orchestrator"
    assert config.allowed_gate_destination == "gate"


@pytest.mark.parametrize("bad_url", ["", "   ", "ftp://gate:8000", "gate:8000"])
def test_gate_client_config_rejects_invalid_gate_url(bad_url: str) -> None:
    with pytest.raises(ValidationError):
        _minimal_client_config(gate_url=bad_url)


@pytest.mark.parametrize("bad_node", ["", "   "])
def test_gate_client_config_rejects_blank_node_fields(bad_node: str) -> None:
    with pytest.raises(ValidationError):
        _minimal_client_config(local_node=bad_node)


@pytest.mark.parametrize("field", ["signing_key_id", "signing_algorithm"])
def test_gate_client_config_rejects_blank_optional_strings(field: str) -> None:
    with pytest.raises(ValidationError):
        _minimal_client_config(**{field: "   "})


def test_gate_client_config_allows_none_optional_strings() -> None:
    config = _minimal_client_config(signing_key_id=None, signing_algorithm=None)

    assert config.signing_key_id is None
    assert config.signing_algorithm is None


def test_gate_client_config_normalizes_verifying_keys() -> None:
    config = _minimal_client_config(verifying_keys={"  key-1  ": "  secret-1  "})

    assert config.verifying_keys == {"key-1": "secret-1"}


def test_gate_client_config_rejects_blank_verifying_key_entries() -> None:
    with pytest.raises(ValidationError):
        _minimal_client_config(verifying_keys={"key-1": "   "})


def test_resolve_verifying_key_prefers_explicit_verifying_keys() -> None:
    config = _minimal_client_config(
        verifying_keys={"key-1": "secret-1"},
        signing_key="own-secret",
        signing_key_id="key-1",
    )

    assert config.resolve_verifying_key("key-1") == "secret-1"


def test_resolve_verifying_key_falls_back_to_own_signing_key() -> None:
    config = _minimal_client_config(signing_key="own-secret", signing_key_id="self-key")

    assert config.resolve_verifying_key("self-key") == "own-secret"


def test_resolve_verifying_key_returns_none_for_unknown_key_id() -> None:
    config = _minimal_client_config()

    assert config.resolve_verifying_key("unknown") is None


def test_resolve_verifying_key_returns_none_for_none_key_id() -> None:
    config = _minimal_client_config()

    assert config.resolve_verifying_key(None) is None


def test_gate_registration_config_normalizes_gate_url() -> None:
    config = GateRegistrationConfig(gate_url="  http://gate:8000/  ")

    assert config.gate_url == "http://gate:8000"
    assert config.spec_path == "engine/spec.yaml"
    assert config.registration_enabled is True
    assert config.retries == 3
    assert config.overwrite is True


@pytest.mark.parametrize("bad_url", ["", "   ", "gate:8000"])
def test_gate_registration_config_rejects_invalid_gate_url(bad_url: str) -> None:
    with pytest.raises(ValidationError):
        GateRegistrationConfig(gate_url=bad_url)


def test_gate_registration_config_rejects_blank_admin_token() -> None:
    with pytest.raises(ValidationError):
        GateRegistrationConfig(gate_url="http://gate:8000", admin_token="   ")


def test_gate_registration_config_allows_none_admin_token() -> None:
    config = GateRegistrationConfig(gate_url="http://gate:8000", admin_token=None)

    assert config.admin_token is None


def test_get_gate_client_config_from_env_requires_gate_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GATE_URL", raising=False)

    with pytest.raises(ValueError, match="GATE_URL is required"):
        get_gate_client_config_from_env()


def test_get_gate_client_config_from_env_builds_config_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GATE_URL", "http://gate:9000")
    monkeypatch.setenv("L9_NODE_NAME", "Worker-A")
    monkeypatch.setenv("L9_SIGNING_KEY", "super-secret")
    monkeypatch.setenv("L9_SIGNING_KEY_ID", "hmac-key-1")
    monkeypatch.setenv("L9_SIGNING_ALGORITHM", "hmac-sha256")
    monkeypatch.setenv("L9_REQUIRE_SIGNATURE", "true")
    monkeypatch.setenv("GATE_CLIENT_TIMEOUT_SECONDS", "12.5")
    monkeypatch.setenv("L9_VERIFY_HOP_SIGNATURES", "yes")
    monkeypatch.setenv("GATE_ALLOWED_DESTINATION", "gate-prod")

    config = get_gate_client_config_from_env()

    assert config.gate_url == "http://gate:9000"
    assert config.local_node == "worker-a"
    assert config.signing_key == "super-secret"
    assert config.signing_key_id == "hmac-key-1"
    assert config.signing_algorithm == "hmac-sha256"
    assert config.require_signature is True
    assert config.verify_response_signatures is True
    assert config.timeout_seconds == 12.5
    assert config.verify_hop_signatures is True
    assert config.allowed_gate_destination == "gate-prod"


def test_get_gate_client_config_from_env_uses_defaults_when_optional_vars_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GATE_URL", "http://gate:9000")
    for var in (
        "L9_NODE_NAME",
        "L9_SIGNING_KEY",
        "L9_SIGNING_SECRET",
        "L9_SIGNING_KEY_ID",
        "L9_SIGNING_ALGORITHM",
        "L9_REQUIRE_SIGNATURE",
        "GATE_CLIENT_TIMEOUT_SECONDS",
        "L9_VERIFY_HOP_SIGNATURES",
        "GATE_ALLOWED_DESTINATION",
    ):
        monkeypatch.delenv(var, raising=False)

    config = get_gate_client_config_from_env()

    assert config.local_node == "unknown-node"
    assert config.signing_key is None
    assert config.require_signature is False
    assert config.timeout_seconds == 30.0
    assert config.verify_hop_signatures is False
    assert config.allowed_gate_destination == "gate"


def test_get_gate_registration_config_from_env_requires_gate_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GATE_URL", raising=False)

    with pytest.raises(ValueError, match="GATE_URL is required"):
        get_gate_registration_config_from_env()


def test_get_gate_registration_config_from_env_builds_config_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GATE_URL", "http://gate:9000")
    monkeypatch.setenv("GATE_ADMIN_TOKEN", "admin-token-1")
    monkeypatch.setenv("GATE_NODE_SPEC_PATH", "custom/spec.yaml")
    monkeypatch.setenv("GATE_REGISTRATION_ENABLED", "false")
    monkeypatch.setenv("GATE_REGISTER_RETRIES", "5")
    monkeypatch.setenv("GATE_REGISTER_OVERWRITE", "false")

    config = get_gate_registration_config_from_env()

    assert config.gate_url == "http://gate:9000"
    assert config.admin_token == "admin-token-1"
    assert config.spec_path == "custom/spec.yaml"
    assert config.registration_enabled is False
    assert config.retries == 5
    assert config.overwrite is False
