"""
Node participation is owned by the SDK (L9-PARTICIPATION-01).

A node must become, and stay, an active Gate participant without writing its
own registration loop or registration-aware readiness. These tests pin the
lifecycle that EIE and CEG each used to implement for themselves.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from constellation_node_sdk.gate import registration as registration_module
from constellation_node_sdk.gate.registration import NodeRegistration, register_from_env
from constellation_node_sdk.runtime import participation as participation_module
from constellation_node_sdk.runtime.app import create_node_app
from constellation_node_sdk.runtime.config import NodeRuntimeConfig
from constellation_node_sdk.runtime.participation import (
    NodeParticipation,
    ParticipationState,
    ParticipationStatus,
)

GATE = "http://gate:9000"


def _registration(name: str = "score") -> NodeRegistration:
    return NodeRegistration(
        node_name=name,
        internal_url=f"http://{name}:8000",
        supported_actions=("score",),
    )


class _ScriptedRegister:
    """Stands in for register_node: returns (or raises) a scripted outcome per call."""

    def __init__(self, *outcomes: bool | BaseException) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> bool:
        self.calls.append(kwargs)
        outcome = self._outcomes.pop(0) if len(self._outcomes) > 1 else self._outcomes[0]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


@pytest.fixture
def scripted(monkeypatch: pytest.MonkeyPatch) -> Any:
    def _install(*outcomes: bool | BaseException) -> _ScriptedRegister:
        fake = _ScriptedRegister(*outcomes)
        monkeypatch.setattr(participation_module, "register_node", fake)
        return fake

    return _install


async def test_no_gate_url_is_disabled_and_ready_without_any_attempt(scripted: Any) -> None:
    fake = scripted(True)
    participation = NodeParticipation(gate_url=None, registration=_registration())

    status = await participation.start()

    assert status.state is ParticipationState.DISABLED
    assert participation.ready is True
    assert fake.calls == []
    assert participation.running is False


async def test_accepted_registration_is_active_and_keeps_a_recovery_loop(scripted: Any) -> None:
    fake = scripted(True)
    participation = NodeParticipation(
        gate_url=GATE, registration=_registration(), reregistration_interval_seconds=60
    )

    status = await participation.start()

    assert status.state is ParticipationState.ACTIVE
    assert status.ready is True
    assert status.attempts == 1 and status.consecutive_failures == 0
    assert status.node_name == "score"
    assert fake.calls[0]["gate_url"] == GATE
    assert fake.calls[0]["registration"].node_name == "score"
    assert participation.running is True
    await participation.stop()
    assert participation.running is False


async def test_rejected_registration_is_degraded_and_not_ready(scripted: Any) -> None:
    scripted(False)
    participation = NodeParticipation(
        gate_url=GATE, registration=_registration(), reregistration_interval_seconds=0
    )

    status = await participation.start()

    assert status.state is ParticipationState.DEGRADED
    assert status.ready is False
    assert status.consecutive_failures == 1
    assert status.last_error is not None and "rejected" in status.last_error
    assert participation.running is False  # interval 0: no loop


async def test_a_raising_attempt_is_recorded_not_propagated(scripted: Any) -> None:
    scripted(RuntimeError("boom"))
    participation = NodeParticipation(
        gate_url=GATE, registration=_registration(), reregistration_interval_seconds=0
    )

    status = await participation.start()

    assert status.state is ParticipationState.DEGRADED
    assert status.last_error == "registration attempt failed: RuntimeError: boom"


async def test_unbuildable_spec_is_degraded(tmp_path: Any, scripted: Any) -> None:
    fake = scripted(True)
    participation = NodeParticipation(
        gate_url=GATE,
        spec_path=str(tmp_path / "missing.yaml"),
        reregistration_interval_seconds=0,
    )

    status = await participation.start()

    assert status.state is ParticipationState.DEGRADED
    assert status.last_error is not None
    assert status.last_error.startswith("registration could not be built")
    assert fake.calls == []


async def test_recovery_after_gate_forgets_the_node(scripted: Any) -> None:
    """Gate restart: accepted, then rejected/unreachable, then accepted again."""
    scripted(True, False, True)
    seen: list[ParticipationState] = []
    participation = NodeParticipation(
        gate_url=GATE,
        registration=_registration(),
        reregistration_interval_seconds=0,
        on_change=lambda s: seen.append(s.state),
    )

    await participation.start()
    await participation.register_once()
    degraded = participation.status
    await participation.register_once()

    assert degraded.state is ParticipationState.DEGRADED
    assert participation.status.state is ParticipationState.ACTIVE
    assert participation.status.consecutive_failures == 0
    assert participation.status.attempts == 3
    assert seen[0] is ParticipationState.REGISTERING
    assert ParticipationState.DEGRADED in seen
    assert seen[-1] is ParticipationState.ACTIVE


async def test_reregistering_an_active_node_does_not_flap_readiness(scripted: Any) -> None:
    scripted(True)
    seen: list[ParticipationStatus] = []
    participation = NodeParticipation(
        gate_url=GATE,
        registration=_registration(),
        reregistration_interval_seconds=0,
        on_change=seen.append,
    )
    await participation.start()
    seen.clear()

    await participation.register_once()

    assert all(s.ready for s in seen)
    assert ParticipationState.REGISTERING not in {s.state for s in seen}


async def test_background_loop_reregisters_and_survives_a_failing_cycle(scripted: Any) -> None:
    fake = scripted(True, RuntimeError("gate down"), True)
    participation = NodeParticipation(
        gate_url=GATE, registration=_registration(), reregistration_interval_seconds=0.01
    )

    await participation.start()
    for _ in range(200):
        if len(fake.calls) >= 4:
            break
        await asyncio.sleep(0.01)
    await participation.stop()

    assert len(fake.calls) >= 4
    assert participation.status.state is ParticipationState.ACTIVE


def test_invalid_arguments_are_refused() -> None:
    with pytest.raises(ValueError):
        NodeParticipation(gate_url=GATE, registration=_registration(), retries=0)
    with pytest.raises(ValueError):
        NodeParticipation(
            gate_url=GATE, registration=_registration(), reregistration_interval_seconds=-1
        )
    with pytest.raises(ValueError):
        NodeParticipation(gate_url=GATE)


def test_from_env_disabled_needs_no_gate_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GATE_URL", raising=False)
    monkeypatch.setenv("GATE_REGISTRATION_ENABLED", "false")

    participation = NodeParticipation.from_env()

    assert participation.status.state is ParticipationState.DISABLED


def test_from_env_reads_the_registration_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GATE_URL", GATE)
    monkeypatch.setenv("GATE_REREGISTRATION_INTERVAL_SECONDS", "15")
    monkeypatch.setenv("GATE_NODE_SPEC_PATH", "node/spec.yaml")

    participation = NodeParticipation.from_env()

    assert participation.status.state is ParticipationState.NOT_ATTEMPTED
    assert participation.status.reregistration_interval_seconds == 15.0


async def test_register_from_env_disabled_does_not_require_gate_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GATE_URL", raising=False)
    monkeypatch.setenv("GATE_REGISTRATION_ENABLED", "false")

    assert await register_from_env() is False


async def test_register_from_env_accepts_an_in_process_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GATE_URL", GATE)
    monkeypatch.setenv("GATE_REGISTRATION_ENABLED", "true")
    fake = _ScriptedRegister(True)
    monkeypatch.setattr(registration_module, "register_node", fake)

    assert await register_from_env(_registration("eie")) is True
    assert fake.calls[0]["registration"].node_name == "eie"


# ── create_node_app binds readiness to participation ─────────────────────────


def _config(gate_url: str) -> NodeRuntimeConfig:
    return NodeRuntimeConfig(
        environment="test",
        node_name="score",
        service_name="score-node",
        service_version="1.0.0",
        dev_mode=True,
        allowed_actions=("score",),
        gate_url=gate_url,
    )


def _app(
    monkeypatch: pytest.MonkeyPatch, *outcomes: bool, gate_url: str = GATE, **kwargs: Any
) -> tuple[Any, _ScriptedRegister]:
    monkeypatch.setenv("GATE_REREGISTRATION_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("GATE_REGISTRATION_ENABLED", "true")
    fake = _ScriptedRegister(*outcomes)
    monkeypatch.setattr(participation_module, "register_node", fake)
    app = create_node_app(config=_config(gate_url), registration=_registration(), **kwargs)
    return app, fake


def test_node_app_is_ready_only_after_gate_accepts_it(monkeypatch: pytest.MonkeyPatch) -> None:
    app, fake = _app(monkeypatch, True)

    with TestClient(app) as client:
        ready = client.get("/v1/ready")
        health = client.get("/v1/health")

    assert ready.status_code == 200
    assert ready.json()["gate_participation"]["state"] == "active"
    assert health.json()["ready"] is True
    assert fake.calls[0]["registration"].node_name == "score"


def test_rejected_node_is_live_but_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    app, _ = _app(monkeypatch, False)

    with TestClient(app) as client:
        ready = client.get("/v1/ready")
        health = client.get("/v1/health")

    assert ready.status_code == 503
    assert ready.json()["gate_participation"]["state"] == "degraded"
    assert health.status_code == 200  # liveness: Gate's health probe must still succeed
    assert health.json()["ready"] is False
    assert health.json()["status"] == "starting"


def test_readiness_recovers_when_participation_recovers(monkeypatch: pytest.MonkeyPatch) -> None:
    app, _ = _app(monkeypatch, False, True)

    with TestClient(app) as client:
        assert client.get("/v1/ready").status_code == 503
        client.portal.call(app.state.participation.register_once)  # type: ignore[union-attr]
        assert client.get("/v1/ready").status_code == 200


def test_auto_registration_off_reports_disabled_and_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, fake = _app(monkeypatch, False, auto_register_with_gate=False)

    with TestClient(app) as client:
        ready = client.get("/v1/ready")

    assert ready.status_code == 200
    assert ready.json()["gate_participation"]["state"] == "disabled"
    assert fake.calls == []


def test_node_app_stops_the_loop_on_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GATE_REREGISTRATION_INTERVAL_SECONDS", "60")
    monkeypatch.setenv("GATE_REGISTRATION_ENABLED", "true")
    monkeypatch.setattr(participation_module, "register_node", _ScriptedRegister(True))
    app = create_node_app(config=_config(GATE), registration=_registration())

    with TestClient(app):
        assert app.state.participation.running is True

    assert app.state.participation.running is False


def test_minimal_example_node_needs_no_gate_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """examples/minimal_node: handlers + spec + create_node_app() becomes an active node."""
    import importlib
    import sys
    from pathlib import Path

    from constellation_node_sdk.runtime.config import get_runtime_config
    from constellation_node_sdk.runtime.handlers import clear_handlers

    repo = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(repo))
    monkeypatch.setenv("GATE_URL", GATE)
    monkeypatch.setenv("GATE_NODE_SPEC_PATH", str(repo / "examples/minimal_node/spec.yaml"))
    monkeypatch.setenv("GATE_REREGISTRATION_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("L9_NODE_NAME", "sdk-minimal-node")
    monkeypatch.setenv("L9_ENVIRONMENT", "test")
    monkeypatch.setenv("L9_DEV_MODE", "true")
    fake = _ScriptedRegister(True)
    monkeypatch.setattr(participation_module, "register_node", fake)
    get_runtime_config.cache_clear()
    for name in [m for m in sys.modules if m.startswith("examples.minimal_node")]:
        monkeypatch.delitem(sys.modules, name)
    clear_handlers()
    try:
        app = importlib.import_module("examples.minimal_node.app").app
        with TestClient(app) as client:
            ready = client.get("/v1/ready")
    finally:
        get_runtime_config.cache_clear()
        clear_handlers()

    assert ready.status_code == 200
    registration = fake.calls[0]["registration"]
    assert registration.node_name == "sdk-minimal-node"
    assert registration.supported_actions == ("sdk-echo",)


def test_registration_under_another_name_is_degraded_not_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate would route the registered name to a node whose runtime rejects it."""
    monkeypatch.setenv("GATE_REREGISTRATION_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("GATE_REGISTRATION_ENABLED", "true")
    fake = _ScriptedRegister(True)
    monkeypatch.setattr(participation_module, "register_node", fake)
    app = create_node_app(config=_config(GATE), registration=_registration("not-score"))

    with TestClient(app) as client:
        ready = client.get("/v1/ready")

    assert ready.status_code == 503
    participation = ready.json()["gate_participation"]
    assert participation["state"] == "degraded"
    assert "does not match the runtime node_name" in participation["last_error"]
    assert fake.calls == []
