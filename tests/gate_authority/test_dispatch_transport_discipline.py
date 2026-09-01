"""
Transport discipline for Gate→worker: one attempt, typed failures, signed rails.

Gate owns whether a worker execution is replayed, because only Gate has the
operation's idempotency and its deadline. The SDK's job is to make exactly one
attempt and report what happened in types Gate can act on without importing
``httpx``.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from gate_client_helpers import (
    RecordingTransport,
    make_dispatch_config,
    make_gate_dispatch_packet,
    worker_runtime_responder,
)

from constellation_node_sdk.gate_authority import (
    GateDispatchConfigurationError,
    GateDispatchError,
    GateDispatchSecurityError,
    GateDispatchTransport,
    WorkerConnectionError,
    WorkerHTTPError,
    WorkerResponseError,
    WorkerTimeoutError,
)
from constellation_node_sdk.gate_authority import errors as dispatch_errors
from constellation_node_sdk.runtime.execution import execute_transport_packet
from constellation_node_sdk.runtime.handlers import clear_handlers, register_handler
from constellation_node_sdk.transport.packet import TransportPacket

WORKER = "enrichment-engine"
WORKER_URL = "http://enrichment-engine:8000"

SIGNING = {
    "signing_key": "gate-secret",
    "signing_key_id": "gate-key-1",
    "signing_algorithm": "hmac-sha256",
}


@pytest.fixture()
def converge_worker() -> Any:
    clear_handlers()

    @register_handler("converge")
    async def handle(_org_id: str, _payload: dict[str, Any]) -> dict[str, Any]:
        return {"state": "completed"}

    yield
    clear_handlers()


def _send(responder: Any, **config_overrides: Any) -> tuple[Any, RecordingTransport]:
    recording = RecordingTransport(responder)
    transport = GateDispatchTransport(make_dispatch_config(**config_overrides), transport=recording)
    return transport, recording


async def _dispatch(responder: Any, **config_overrides: Any) -> tuple[Any, RecordingTransport]:
    transport, recording = _send(responder, **config_overrides)
    result = await transport.send_gate_authored_packet(
        packet=make_gate_dispatch_packet(target_node=WORKER, timeout_ms=5_000),
        target_node=WORKER,
        worker_base_url=WORKER_URL,
    )
    return result, recording


# ---------------------------------------------------------------------------
# Exactly one attempt
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 409, 422, 429, 500, 502, 503, 504])
@pytest.mark.asyncio
async def test_no_status_is_retried(status_code: int) -> None:
    """
    Even the statuses a retry layer would act on get one attempt.

    ``429`` and ``503`` carry retry semantics by convention; if a hidden retry
    existed anywhere, it would be here. A retried dispatch would double-execute
    a domain operation.
    """
    transport, recording = _send(lambda _r, _a: httpx.Response(status_code, json={}))

    with pytest.raises(WorkerHTTPError) as caught:
        await transport.send_gate_authored_packet(
            packet=make_gate_dispatch_packet(target_node=WORKER),
            target_node=WORKER,
            worker_base_url=WORKER_URL,
        )

    assert caught.value.status_code == status_code
    assert len(recording.requests) == 1


@pytest.mark.asyncio
async def test_a_dead_socket_is_not_retried() -> None:
    def explode(_request: httpx.Request, _attempt: int) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    transport, recording = _send(explode)
    with pytest.raises(WorkerConnectionError):
        await transport.send_gate_authored_packet(
            packet=make_gate_dispatch_packet(target_node=WORKER),
            target_node=WORKER,
            worker_base_url=WORKER_URL,
        )
    assert len(recording.requests) == 1


@pytest.mark.asyncio
async def test_a_timeout_is_not_retried() -> None:
    def stall(_request: httpx.Request, _attempt: int) -> httpx.Response:
        raise httpx.ReadTimeout("")

    transport, recording = _send(stall)
    with pytest.raises(WorkerTimeoutError):
        await transport.send_gate_authored_packet(
            packet=make_gate_dispatch_packet(target_node=WORKER),
            target_node=WORKER,
            worker_base_url=WORKER_URL,
        )
    assert len(recording.requests) == 1


@pytest.mark.asyncio
async def test_a_successful_dispatch_makes_one_request(converge_worker: Any) -> None:
    _, recording = await _dispatch(worker_runtime_responder(WORKER))
    assert len(recording.requests) == 1


def test_the_config_exposes_no_retry_surface() -> None:
    """There is no field through which a caller could ask for a retry."""
    from constellation_node_sdk.gate_authority import GateDispatchTransportConfig

    fields = set(GateDispatchTransportConfig.model_fields)
    assert not {f for f in fields if "retr" in f.lower() or "attempt" in f.lower()}


# ---------------------------------------------------------------------------
# Typed failures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_timeout_carries_the_applied_budget() -> None:
    """
    Classifiable without reading the message.

    httpx timeout exceptions frequently stringify to ``""``, so the type and the
    applied deadline are the only usable evidence.
    """

    def stall(_request: httpx.Request, _attempt: int) -> httpx.Response:
        raise httpx.ConnectTimeout("")

    transport, _ = _send(stall)
    with pytest.raises(WorkerTimeoutError) as caught:
        await transport.send_gate_authored_packet(
            packet=make_gate_dispatch_packet(target_node=WORKER, timeout_ms=3_000),
            target_node=WORKER,
            worker_base_url=WORKER_URL,
        )

    assert caught.value.timeout_seconds == pytest.approx(3.0)
    assert not isinstance(caught.value, WorkerConnectionError)
    assert str(caught.value).strip()


@pytest.mark.parametrize(
    ("status_code", "expect_server"),
    [(429, False), (500, True), (502, True), (503, True), (403, False), (404, False)],
)
@pytest.mark.asyncio
async def test_http_failures_expose_the_status_structurally(
    status_code: int, expect_server: bool
) -> None:
    transport, _ = _send(lambda _r, _a: httpx.Response(status_code, json={"detail": "no"}))
    with pytest.raises(WorkerHTTPError) as caught:
        await transport.send_gate_authored_packet(
            packet=make_gate_dispatch_packet(target_node=WORKER),
            target_node=WORKER,
            worker_base_url=WORKER_URL,
        )
    assert caught.value.is_server_error is expect_server
    assert caught.value.is_client_error is (400 <= status_code < 500)
    assert caught.value.response_text is not None


@pytest.mark.parametrize(
    "responder",
    [
        lambda _r, _a: httpx.Response(200, json={"state": "completed"}),
        lambda _r, _a: httpx.Response(200, json=[1, 2, 3]),
        lambda _r, _a: httpx.Response(
            200, content=b"<html/>", headers={"Content-Type": "text/html"}
        ),
    ],
    ids=["bare domain dict", "non-object json", "html error page"],
)
@pytest.mark.asyncio
async def test_a_noncanonical_response_is_typed(responder: Any) -> None:
    transport, _ = _send(responder)
    with pytest.raises(WorkerResponseError):
        await transport.send_gate_authored_packet(
            packet=make_gate_dispatch_packet(target_node=WORKER),
            target_node=WORKER,
            worker_base_url=WORKER_URL,
        )


@pytest.mark.asyncio
async def test_a_tampered_response_is_a_security_failure(converge_worker: Any) -> None:
    """
    A broken hash means untrusted, not merely malformed.

    Collapsing this into a response error would invite Gate to treat tampering
    as a dialect problem and retry into it.
    """
    import json

    async def tampering_responder(request: httpx.Request, attempt: int) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        dispatched = TransportPacket.model_validate(body)
        response = await execute_transport_packet(dispatched, node_name=WORKER, dev_mode=True)
        payload = response.model_dump_json_dict()
        payload["payload"]["state"] = "mutated"
        return httpx.Response(200, json=payload)

    transport, _ = _send(tampering_responder)
    with pytest.raises(GateDispatchSecurityError) as caught:
        await transport.send_gate_authored_packet(
            packet=make_gate_dispatch_packet(target_node=WORKER),
            target_node=WORKER,
            worker_base_url=WORKER_URL,
        )

    assert caught.value.direction == "inbound"
    assert not isinstance(caught.value, WorkerResponseError)


@pytest.mark.asyncio
async def test_a_response_from_the_wrong_worker_is_rejected(converge_worker: Any) -> None:
    """
    A canonical packet from somewhere else is still the wrong answer.

    Integrity alone does not prove a response answers *this* dispatch.
    """

    async def impostor(request: httpx.Request, _attempt: int) -> httpx.Response:
        import json

        body = json.loads(request.content.decode("utf-8"))
        dispatched = TransportPacket.model_validate(body)
        response = await execute_transport_packet(dispatched, node_name=WORKER, dev_mode=True)
        # Re-derive the response as though a different node had answered.
        impostor_response = response.derive(source_node="some-other-worker")
        return httpx.Response(200, json=impostor_response.model_dump_json_dict())

    transport, _ = _send(impostor)
    with pytest.raises(WorkerResponseError) as caught:
        await transport.send_gate_authored_packet(
            packet=make_gate_dispatch_packet(target_node=WORKER),
            target_node=WORKER,
            worker_base_url=WORKER_URL,
        )
    assert "source_node" in str(caught.value)


@pytest.mark.parametrize(
    "error_name",
    [
        "GateDispatchAuthorityError",
        "GateDispatchConfigurationError",
        "GateDispatchSecurityError",
        "WorkerConnectionError",
        "WorkerHTTPError",
        "WorkerResponseError",
        "WorkerTimeoutError",
    ],
)
def test_every_dispatch_failure_descends_from_one_base(error_name: str) -> None:
    """``except GateDispatchError`` is a complete catch for worker dispatch."""
    assert issubclass(getattr(dispatch_errors, error_name), GateDispatchError)


@pytest.mark.asyncio
async def test_gate_can_classify_without_importing_httpx() -> None:
    """
    The reason this taxonomy exists.

    Gate currently flattens transport failures into ``RuntimeError`` and loses
    the cause; this is the classifier that replaces that.
    """

    def classify(exc: GateDispatchError) -> str:
        if isinstance(exc, WorkerTimeoutError | WorkerConnectionError):
            return "worker-unavailable"
        if isinstance(exc, WorkerHTTPError):
            return "worker-unavailable" if exc.is_server_error else "worker-rejected"
        if isinstance(exc, GateDispatchSecurityError):
            return "untrusted"
        return "not-dispatchable"

    cases: list[tuple[Any, str]] = [
        (lambda _r, _a: (_ for _ in ()).throw(httpx.ConnectTimeout("")), "worker-unavailable"),
        (lambda _r, _a: (_ for _ in ()).throw(httpx.ConnectError("")), "worker-unavailable"),
        (lambda _r, _a: httpx.Response(503, json={}), "worker-unavailable"),
        (lambda _r, _a: httpx.Response(422, json={}), "worker-rejected"),
        (lambda _r, _a: httpx.Response(200, json={"x": 1}), "not-dispatchable"),
    ]

    for responder, expected in cases:
        transport, _ = _send(responder)
        with pytest.raises(GateDispatchError) as caught:
            await transport.send_gate_authored_packet(
                packet=make_gate_dispatch_packet(target_node=WORKER),
                target_node=WORKER,
                worker_base_url=WORKER_URL,
            )
        assert classify(caught.value) == expected
        assert str(caught.value).strip()


@pytest.mark.asyncio
async def test_failures_chain_the_underlying_cause() -> None:
    def explode(_request: httpx.Request, _attempt: int) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    transport, _ = _send(explode)
    with pytest.raises(WorkerConnectionError) as caught:
        await transport.send_gate_authored_packet(
            packet=make_gate_dispatch_packet(target_node=WORKER),
            target_node=WORKER,
            worker_base_url=WORKER_URL,
        )
    assert isinstance(caught.value.__cause__, httpx.ConnectError)


# ---------------------------------------------------------------------------
# Signed rail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_signed_dispatch_round_trips(converge_worker: Any) -> None:
    """
    Gate signs the dispatch, the worker requires it, and both directions verify.

    Signing runs after the authority check and before transport validation, so
    the artifact validated is the artifact sent.
    """
    import json

    async def signing_worker(request: httpx.Request, _attempt: int) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        dispatched = TransportPacket.model_validate(body)
        response = await execute_transport_packet(
            dispatched,
            node_name=WORKER,
            signing_key="worker-secret",
            signing_key_id="worker-key-1",
            signing_algorithm="hmac-sha256",
            verifying_keys={"gate-key-1": "gate-secret"},
            require_signature=True,
        )
        return httpx.Response(200, json=response.model_dump_json_dict())

    transport, recording = _send(
        signing_worker,
        require_signature=True,
        verify_response_signatures=True,
        verifying_keys={"gate-key-1": "gate-secret", "worker-key-1": "worker-secret"},
        **SIGNING,
    )

    response = await transport.send_gate_authored_packet(
        packet=make_gate_dispatch_packet(target_node=WORKER),
        target_node=WORKER,
        worker_base_url=WORKER_URL,
    )

    sent = recording.sent_packet(0)
    assert sent["security"]["signature"] is not None
    assert sent["security"]["signing_key_id"] == "gate-key-1"
    assert response.security.signature is not None
    assert response.payload["state"] == "completed"


@pytest.mark.asyncio
async def test_an_unsigned_response_is_rejected_when_signatures_are_required(
    converge_worker: Any,
) -> None:
    """A worker that does not sign cannot satisfy a Gate that requires it."""
    transport, _ = _send(
        worker_runtime_responder(WORKER),
        verify_response_signatures=True,
        verifying_keys={"gate-key-1": "gate-secret"},
        **SIGNING,
    )

    with pytest.raises(GateDispatchSecurityError) as caught:
        await transport.send_gate_authored_packet(
            packet=make_gate_dispatch_packet(target_node=WORKER),
            target_node=WORKER,
            worker_base_url=WORKER_URL,
        )
    assert caught.value.direction == "inbound"


@pytest.mark.asyncio
async def test_incomplete_signing_material_fails_before_the_network() -> None:
    """A local misconfiguration is not a worker failure and never reaches the wire."""
    transport, recording = _send(
        worker_runtime_responder(WORKER),
        signing_key="gate-secret",
        signing_key_id=None,
        signing_algorithm=None,
    )

    with pytest.raises(GateDispatchConfigurationError):
        await transport.send_gate_authored_packet(
            packet=make_gate_dispatch_packet(target_node=WORKER),
            target_node=WORKER,
            worker_base_url=WORKER_URL,
        )
    assert recording.requests == []


@pytest.mark.asyncio
async def test_a_response_signed_by_an_unknown_key_fails_closed(converge_worker: Any) -> None:
    """An unresolvable signing key is a failure, not a skipped check."""
    import json

    async def stranger(request: httpx.Request, _attempt: int) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        dispatched = TransportPacket.model_validate(body)
        response = await execute_transport_packet(
            dispatched,
            node_name=WORKER,
            signing_key="unknown-secret",
            signing_key_id="unknown-key",
            signing_algorithm="hmac-sha256",
        )
        return httpx.Response(200, json=response.model_dump_json_dict())

    transport, _ = _send(
        stranger,
        verify_response_signatures=True,
        verifying_keys={"worker-key-1": "worker-secret"},
    )

    with pytest.raises(GateDispatchSecurityError):
        await transport.send_gate_authored_packet(
            packet=make_gate_dispatch_packet(target_node=WORKER),
            target_node=WORKER,
            worker_base_url=WORKER_URL,
        )


# ---------------------------------------------------------------------------
# Connection lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_supplied_client_is_reused_across_dispatches(converge_worker: Any) -> None:
    """
    Gate dispatches continuously; a client per packet is a handshake per packet.

    The same pooled client must serve every dispatch.
    """
    recording = RecordingTransport(worker_runtime_responder(WORKER))
    async with httpx.AsyncClient(transport=recording) as pooled:
        transport = GateDispatchTransport(make_dispatch_config(), client=pooled)
        for _ in range(3):
            await transport.send_gate_authored_packet(
                packet=make_gate_dispatch_packet(target_node=WORKER),
                target_node=WORKER,
                worker_base_url=WORKER_URL,
            )
        assert len(recording.requests) == 3
        assert not pooled.is_closed


@pytest.mark.asyncio
async def test_a_supplied_client_is_never_closed_by_the_transport(
    converge_worker: Any,
) -> None:
    """The caller's client belongs to the caller, including its lifetime."""
    recording = RecordingTransport(worker_runtime_responder(WORKER))
    async with httpx.AsyncClient(transport=recording) as pooled:
        transport = GateDispatchTransport(make_dispatch_config(), client=pooled)
        await transport.aclose()
        assert not pooled.is_closed
        await transport.send_gate_authored_packet(
            packet=make_gate_dispatch_packet(target_node=WORKER),
            target_node=WORKER,
            worker_base_url=WORKER_URL,
        )


@pytest.mark.asyncio
async def test_a_managed_client_is_created_and_closed(converge_worker: Any) -> None:
    """Used as a context manager, the transport owns and closes its own client."""
    recording = RecordingTransport(worker_runtime_responder(WORKER))
    async with GateDispatchTransport(make_dispatch_config(), transport=recording) as transport:
        await transport.send_gate_authored_packet(
            packet=make_gate_dispatch_packet(target_node=WORKER),
            target_node=WORKER,
            worker_base_url=WORKER_URL,
        )
        owned = transport._client
        assert owned is not None
        assert not owned.is_closed
    assert owned.is_closed


@pytest.mark.asyncio
async def test_a_pooled_client_default_cannot_widen_the_packet_deadline(
    converge_worker: Any,
) -> None:
    """
    The per-dispatch deadline still wins over the pooled client's own default.

    A pooled client configured with a long timeout must not silently extend a
    short dispatch budget.
    """
    recording = RecordingTransport(worker_runtime_responder(WORKER))
    async with httpx.AsyncClient(transport=recording, timeout=300.0) as pooled:
        transport = GateDispatchTransport(make_dispatch_config(), client=pooled)
        await transport.send_gate_authored_packet(
            packet=make_gate_dispatch_packet(target_node=WORKER, timeout_ms=1_500),
            target_node=WORKER,
            worker_base_url=WORKER_URL,
        )
    assert recording.applied_timeout(0)["read"] == pytest.approx(1.5)


def test_a_non_positive_packet_budget_is_a_configuration_error() -> None:
    """
    A packet advertising no budget cannot produce a network deadline.

    ``timeout_ms`` is ``ge=1`` on the model, so this guard is unreachable
    through ordinary construction. It is kept because the deadline is derived
    rather than defaulted: a future path that produces a zero budget must fail
    loudly rather than hand httpx a meaningless deadline.
    """
    transport = GateDispatchTransport(make_dispatch_config())
    packet = make_gate_dispatch_packet(target_node=WORKER)
    zero_budget = packet.model_construct(
        **{
            **dict(packet),
            "header": packet.header.model_construct(**{**dict(packet.header), "timeout_ms": 0}),
        }
    )

    with pytest.raises(GateDispatchConfigurationError):
        transport._network_timeout_seconds(zero_budget)
