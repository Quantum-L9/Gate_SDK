"""Bounded observability: closed labels only; packet behavior unchanged."""

from __future__ import annotations

from fastapi.testclient import TestClient

from constellation_node_sdk.runtime.app import create_node_app
from constellation_node_sdk.runtime.config import NodeRuntimeConfig
from constellation_node_sdk.runtime.handlers import clear_handlers, register_handler
from constellation_node_sdk.runtime.lifecycle import NoOpLifecycle
from constellation_node_sdk.runtime.observability import (
    BOUNDED_RESULTS,
    REGISTRY,
    REQUESTS_TOTAL,
    RESULT_ACCEPTED,
    RESULT_FAILED,
    RESULT_REJECTED,
    RESULT_RETURNED_ERROR_PACKET,
    bound_result,
    bounded_label_snapshot,
    record_execution,
    record_request,
)
from constellation_node_sdk.transport.packet import create_transport_packet
from constellation_node_sdk.transport.provenance import RoutingProvenance


def _config() -> NodeRuntimeConfig:
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
        allowed_actions=("score", "explode"),
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


def test_bound_result_maps_closed_enum() -> None:
    assert bound_result("completed") == RESULT_ACCEPTED
    assert bound_result("rejected") == RESULT_REJECTED
    assert bound_result("failed") == RESULT_FAILED
    assert bound_result("returned-error-packet") == RESULT_RETURNED_ERROR_PACKET
    assert bound_result("totally-unknown-status") == RESULT_FAILED
    assert bound_result("totally-unknown-status") in BOUNDED_RESULTS


def test_record_request_ignores_action_label() -> None:
    """action must never become a Prometheus label (cardinality bound)."""
    cfg = _config()
    before = REQUESTS_TOTAL.labels(service=cfg.service_name, result=RESULT_ACCEPTED)._value.get()
    record_request(config=cfg, action="score-with-very-unique-name", status="completed")
    after = REQUESTS_TOTAL.labels(service=cfg.service_name, result=RESULT_ACCEPTED)._value.get()
    assert after == before + 1

    for metric in REGISTRY.collect():
        for sample in metric.samples:
            assert "action" not in sample.labels
            if "score-with-very-unique-name" in str(sample):
                raise AssertionError("unbounded action leaked into metrics")


def test_record_execution_histograms_and_bounded_labels() -> None:
    cfg = _config()
    record_execution(
        config=cfg,
        result=RESULT_ACCEPTED,
        duration_seconds=0.012,
        request_bytes=128,
        response_bytes=256,
        hop_count=2,
        retry_count=1,
    )
    assert REQUESTS_TOTAL.labels(service=cfg.service_name, result=RESULT_ACCEPTED)._value.get() >= 1
    snap = bounded_label_snapshot()
    assert set(snap["requests_total.result"]) == set(BOUNDED_RESULTS)
    assert set(snap["payload_bytes.direction"]) == {"request", "response"}

    # Metric families exist with only closed label keys.
    names = {m.name for m in REGISTRY.collect()}
    assert "constellation_node_requests" in names or "constellation_node_requests_total" in names
    for family in REGISTRY.collect():
        for sample in family.samples:
            assert "action" not in sample.labels


def test_execute_path_emits_bounded_metrics_without_action_label() -> None:
    clear_handlers()

    @register_handler("score")
    async def handle_score(_tenant: str, payload: dict) -> dict:
        return {
            "status": "completed",
            "score": 91,
            "entity_id": payload["entity_id"],
        }

    config = _config()
    app = create_node_app(
        service_name="score-node",
        version="1.0.0",
        lifecycle_hook=NoOpLifecycle(),
        config=config,
        auto_register_with_gate=False,
    )

    packet = create_transport_packet(
        action="score",
        payload={"entity_id": "42"},
        tenant="tenant-a",
        destination_node="score",
        source_node="gate",
        reply_to="gate",
        provenance=RoutingProvenance(
            origin_kind="client",
            requested_action="score",
            resolved_by_gate=True,
            route_kind="external_ingress",
        ),
    )

    with TestClient(app) as client:
        response = client.post("/v1/execute", json=packet.model_dump_json_dict())
        assert response.status_code == 200
        body = response.json()
        assert body["payload"]["status"] == "completed"
        assert body["payload"]["score"] == 91

        metrics = client.get("/metrics")
        assert metrics.status_code == 200
        text = metrics.text
        assert "constellation_node_requests_total" in text
        assert 'result="accepted"' in text
        assert "constellation_node_execution_seconds" in text
        assert "constellation_node_payload_bytes" in text
        assert "constellation_node_hop_count" in text
        assert "constellation_node_retry_count" in text
        # Unbounded action must not appear as a label value/key.
        assert 'action="' not in text
        assert 'action="score"' not in text


def test_error_packet_path_uses_returned_error_result() -> None:
    clear_handlers()

    @register_handler("explode")
    async def handle_explode(_tenant: str, _payload: dict) -> dict:
        raise RuntimeError("boom")

    config = _config()
    app = create_node_app(
        service_name="score-node",
        version="1.0.0",
        lifecycle_hook=NoOpLifecycle(),
        config=config,
        auto_register_with_gate=False,
    )

    packet = create_transport_packet(
        action="explode",
        payload={},
        tenant="tenant-a",
        destination_node="score",
        source_node="gate",
        reply_to="gate",
        provenance=RoutingProvenance(
            origin_kind="client",
            requested_action="explode",
            resolved_by_gate=True,
            route_kind="external_ingress",
        ),
    )

    with TestClient(app) as client:
        response = client.post("/v1/execute", json=packet.model_dump_json_dict())
        assert response.status_code == 200
        assert response.json()["header"]["packet_type"] == "failure"
        metrics = client.get("/metrics").text
        assert 'result="returned_error_packet"' in metrics
        assert 'action="' not in metrics
