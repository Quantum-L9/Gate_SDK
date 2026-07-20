from __future__ import annotations

import asyncio

import httpx
import pytest

import constellation_node_sdk.gate.registration as registration_module


class _RecordingTransport(httpx.AsyncBaseTransport):
    """Replays a scripted sequence of responses/exceptions for successive requests."""

    def __init__(self, responses: list[httpx.Response | Exception]) -> None:
        self._responses = list(responses)
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        next_item = self._responses.pop(0)
        if isinstance(next_item, Exception):
            raise next_item
        return next_item


def _patch_async_client(
    monkeypatch: pytest.MonkeyPatch, transport: httpx.AsyncBaseTransport
) -> None:
    original_async_client = httpx.AsyncClient

    class PatchedAsyncClient(httpx.AsyncClient):
        def __init__(self, *args: object, **kwargs: object) -> None:
            kwargs["transport"] = transport  # type: ignore[assignment]
            super().__init__(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(registration_module.httpx, "AsyncClient", PatchedAsyncClient)
    assert original_async_client is not None  # keep reference alive for clarity


def _write_spec(tmp_path, node_id: str = "score") -> str:
    spec_path = tmp_path / "spec.yaml"
    spec_path.write_text(
        f"node:\n  id: {node_id}\n  actions:\n    - {node_id}\n",
        encoding="utf-8",
    )
    return str(spec_path)


def test_build_registration_payload_from_valid_spec() -> None:
    spec = {
        "node": {
            "id": "score",
            "actions": ["score"],
            "internal_url": "https://score:8000",
            "priority_class": "P1",
            "max_concurrent": 25,
            "health_endpoint": "/v1/health",
            "timeout_ms": 15000,
            "version": "1.2.3",
            "type": "worker",
        }
    }

    payload = registration_module.build_registration_payload(spec)

    assert "score" in payload
    node = payload["score"]

    assert node["internal_url"] == "https://score:8000"
    assert node["supported_actions"] == ["score"]
    assert node["priority_class"] == "P1"
    assert node["max_concurrent"] == 25
    assert node["timeout_ms"] == 15000
    assert node["metadata"]["version"] == "1.2.3"


def test_build_registration_payload_requires_node_id() -> None:
    with pytest.raises(ValueError):
        registration_module.build_registration_payload({"node": {"actions": ["score"]}})


def test_load_node_spec_reads_yaml(tmp_path) -> None:
    spec_path = tmp_path / "spec.yaml"
    spec_path.write_text(
        "node:\n  id: enrich\n  actions:\n    - enrich\n",
        encoding="utf-8",
    )

    spec = registration_module.load_node_spec(str(spec_path))
    assert spec["node"]["id"] == "enrich"


@pytest.mark.asyncio
async def test_register_with_gate_returns_false_when_spec_file_missing(tmp_path) -> None:
    missing_path = str(tmp_path / "does-not-exist.yaml")

    result = await registration_module.register_with_gate(
        gate_url="https://gate:8000", spec_path=missing_path
    )

    assert result is False


@pytest.mark.asyncio
async def test_register_with_gate_returns_false_when_spec_invalid(tmp_path) -> None:
    spec_path = tmp_path / "spec.yaml"
    spec_path.write_text("node:\n  actions:\n    - score\n", encoding="utf-8")

    result = await registration_module.register_with_gate(
        gate_url="https://gate:8000", spec_path=str(spec_path)
    )

    assert result is False


@pytest.mark.asyncio
async def test_register_with_gate_returns_true_on_200(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec_path = _write_spec(tmp_path)
    transport = _RecordingTransport(
        [httpx.Response(status_code=200, json={"status": "registered"})]
    )
    _patch_async_client(monkeypatch, transport)

    result = await registration_module.register_with_gate(
        gate_url="https://gate:8000",
        admin_token="secret-token",
        spec_path=spec_path,
    )

    assert result is True
    assert len(transport.requests) == 1
    assert transport.requests[0].url.path == "/v1/admin/register"
    assert transport.requests[0].headers["x-admin-token"] == "secret-token"


@pytest.mark.parametrize("status_code", [400, 401, 403, 409, 422])
@pytest.mark.asyncio
async def test_register_with_gate_returns_false_on_rejection_status(
    tmp_path, monkeypatch: pytest.MonkeyPatch, status_code: int
) -> None:
    spec_path = _write_spec(tmp_path)
    transport = _RecordingTransport([httpx.Response(status_code=status_code)])
    _patch_async_client(monkeypatch, transport)

    result = await registration_module.register_with_gate(
        gate_url="https://gate:8000", spec_path=spec_path, retries=3
    )

    assert result is False
    # Rejection statuses are terminal — no retry should be attempted.
    assert len(transport.requests) == 1


@pytest.mark.asyncio
async def test_register_with_gate_retries_after_transport_error_then_succeeds(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec_path = _write_spec(tmp_path)
    transport = _RecordingTransport(
        [
            httpx.TransportError("connection reset"),
            httpx.Response(status_code=200, json={"status": "registered"}),
        ]
    )
    _patch_async_client(monkeypatch, transport)

    real_sleep = asyncio.sleep

    async def _fast_sleep(_seconds: float) -> None:
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)

    result = await registration_module.register_with_gate(
        gate_url="https://gate:8000", spec_path=spec_path, retries=2
    )

    assert result is True
    assert len(transport.requests) == 2


@pytest.mark.asyncio
async def test_register_with_gate_returns_false_after_retry_exhaustion(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec_path = _write_spec(tmp_path)
    transport = _RecordingTransport(
        [
            httpx.TransportError("connection reset"),
            httpx.TransportError("connection reset"),
        ]
    )
    _patch_async_client(monkeypatch, transport)

    real_sleep = asyncio.sleep

    async def _fast_sleep(_seconds: float) -> None:
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)

    result = await registration_module.register_with_gate(
        gate_url="https://gate:8000", spec_path=spec_path, retries=2
    )

    assert result is False
    assert len(transport.requests) == 2


@pytest.mark.asyncio
async def test_register_from_env_returns_false_when_registration_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GATE_URL", "https://gate:8000")
    monkeypatch.setenv("GATE_REGISTRATION_ENABLED", "false")

    result = await registration_module.register_from_env()

    assert result is False


@pytest.mark.asyncio
async def test_register_from_env_delegates_to_register_with_gate(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec_path = _write_spec(tmp_path)
    transport = _RecordingTransport(
        [httpx.Response(status_code=200, json={"status": "registered"})]
    )
    _patch_async_client(monkeypatch, transport)

    monkeypatch.setenv("GATE_URL", "https://gate:8000")
    monkeypatch.setenv("GATE_REGISTRATION_ENABLED", "true")
    monkeypatch.setenv("GATE_NODE_SPEC_PATH", spec_path)
    monkeypatch.setenv("GATE_REGISTER_RETRIES", "1")

    result = await registration_module.register_from_env()

    assert result is True
    assert len(transport.requests) == 1
