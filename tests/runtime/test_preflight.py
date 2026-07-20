from __future__ import annotations

import pytest

from constellation_node_sdk.runtime.config import NodeRuntimeConfig
from constellation_node_sdk.runtime.preflight import PreflightFailure, run_preflight


def _base_config() -> NodeRuntimeConfig:
    return NodeRuntimeConfig(
        environment="test",
        node_name="score",
        service_name="score-node",
        service_version="1.0.0",
        dev_mode=True,
        require_signature=False,
        expose_internal_errors=False,
        return_transport_errors=True,
        signing_algorithm="hmac-sha256",
        signing_key=None,
        signing_private_key=None,
        signing_key_id=None,
        verifying_keys={},
        allowed_actions=("score",),
        allowed_packet_types=("request",),
        require_idempotency_for_actions=(),
        allowed_clock_skew_seconds=30,
        max_packet_bytes=262_144,
        max_hop_depth=64,
        max_delegation_depth=8,
        max_attachments=0,
        max_attachment_size_bytes=0,
        attachment_allowed_schemes=(),
        allow_private_attachment_hosts=False,
        replay_enabled=True,
        verify_hop_signatures=False,
        gate_url="http://gate:8000",
        host="127.0.0.1",
        port=8001,
    )


def test_run_preflight_accepts_valid_config() -> None:
    config = _base_config()
    run_preflight(config)


def test_node_runtime_config_rejects_invalid_idempotency_action() -> None:
    with pytest.raises(ValueError):
        NodeRuntimeConfig(
            environment="test",
            node_name="score",
            service_name="score-node",
            service_version="1.0.0",
            dev_mode=True,
            allowed_actions=("score",),
            allowed_packet_types=("request",),
            require_idempotency_for_actions=("unknown",),
            max_attachments=0,
            max_attachment_size_bytes=0,
            attachment_allowed_schemes=(),
        )


def test_run_preflight_rejects_missing_attachment_schemes_when_attachments_enabled() -> None:
    config = _base_config().model_copy(
        update={
            "max_attachments": 1,
            "max_attachment_size_bytes": 1024,
            "attachment_allowed_schemes": (),
        }
    )

    with pytest.raises(PreflightFailure):
        run_preflight(config)


def test_run_preflight_rejects_invalid_gate_url() -> None:
    config = _base_config().model_copy(update={"gate_url": "not-a-url"})

    with pytest.raises(PreflightFailure):
        run_preflight(config)


def test_run_preflight_rejects_unsigned_gate_only_ingress_outside_dev_mode() -> None:
    config = _base_config().model_copy(
        update={
            "dev_mode": False,
            "enforce_gate_only_ingress": True,
            "require_signature": False,
            "enable_relay_route": False,
        }
    )

    with pytest.raises(PreflightFailure, match="requires a verified signature on /v1/execute"):
        run_preflight(config)


def test_run_preflight_rejects_unsigned_relay_route_outside_dev_mode() -> None:
    config = _base_config().model_copy(
        update={
            "dev_mode": False,
            "enforce_gate_only_ingress": True,
            "require_signature": True,
            "signing_key": "secret",
            "enable_relay_route": True,
            "relay_require_signature": False,
        }
    )

    with pytest.raises(PreflightFailure, match="requires a verified signature on /v1/relay"):
        run_preflight(config)


def test_run_preflight_accepts_signed_gate_only_ingress_outside_dev_mode() -> None:
    config = _base_config().model_copy(
        update={
            "dev_mode": False,
            "enforce_gate_only_ingress": True,
            "require_signature": True,
            "signing_key": "secret",
            "enable_relay_route": True,
        }
    )

    run_preflight(config)


def test_node_runtime_config_accepts_idempotency_action_covered_only_by_execute_allowlist() -> None:
    """L9_EXECUTE_ALLOWED_ACTIONS=pay + L9_REQUIRE_IDEMPOTENCY_FOR_ACTIONS=pay must be
    usable without a legacy L9_ALLOWED_ACTIONS entry."""
    config = NodeRuntimeConfig(
        environment="test",
        node_name="score",
        service_name="score-node",
        service_version="1.0.0",
        dev_mode=True,
        allowed_actions=(),
        execute_allowed_actions=("pay",),
        allowed_packet_types=("request",),
        require_idempotency_for_actions=("pay",),
        max_attachments=0,
        max_attachment_size_bytes=0,
        attachment_allowed_schemes=(),
    )
    assert config.require_idempotency_for_actions == ("pay",)


def test_node_runtime_config_rejects_idempotency_action_absent_from_execute_allowlist() -> None:
    with pytest.raises(ValueError, match="unknown actions"):
        NodeRuntimeConfig(
            environment="test",
            node_name="score",
            service_name="score-node",
            service_version="1.0.0",
            dev_mode=True,
            allowed_actions=(),
            execute_allowed_actions=("refund",),
            allowed_packet_types=("request",),
            require_idempotency_for_actions=("pay",),
            max_attachments=0,
            max_attachment_size_bytes=0,
            attachment_allowed_schemes=(),
        )


def test_run_preflight_accepts_idempotency_action_covered_only_by_execute_allowlist() -> None:
    config = _base_config().model_copy(
        update={
            "allowed_actions": (),
            "execute_allowed_actions": ("pay",),
            "require_idempotency_for_actions": ("pay",),
        }
    )
    run_preflight(config)


def test_run_preflight_skips_ingress_authentication_check_when_gate_only_ingress_disabled() -> None:
    config = _base_config().model_copy(
        update={
            "dev_mode": False,
            "enforce_gate_only_ingress": False,
            "require_signature": False,
        }
    )

    run_preflight(config)
