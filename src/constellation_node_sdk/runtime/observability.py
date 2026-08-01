"""Bounded Prometheus instrumentation for the node execute path.

Labels are closed enums only — never raw action names or free-form strings.
"""
from __future__ import annotations

import logging
import sys
import time
from collections.abc import Mapping
from typing import Final

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from pythonjsonlogger import jsonlogger
from starlette.responses import Response

from .config import NodeRuntimeConfig

# Closed result enum (pack: accepted / rejected / failed / retried / returned-error-packet)
RESULT_ACCEPTED: Final = "accepted"
RESULT_REJECTED: Final = "rejected"
RESULT_FAILED: Final = "failed"
RESULT_RETRIED: Final = "retried"
RESULT_RETURNED_ERROR_PACKET: Final = "returned_error_packet"

BOUNDED_RESULTS: Final[frozenset[str]] = frozenset(
    {
        RESULT_ACCEPTED,
        RESULT_REJECTED,
        RESULT_FAILED,
        RESULT_RETRIED,
        RESULT_RETURNED_ERROR_PACKET,
    }
)

_STATUS_TO_RESULT: Final[Mapping[str, str]] = {
    "accepted": RESULT_ACCEPTED,
    "completed": RESULT_ACCEPTED,
    "ok": RESULT_ACCEPTED,
    "success": RESULT_ACCEPTED,
    "rejected": RESULT_REJECTED,
    "invalid_json": RESULT_REJECTED,
    "unauthorized": RESULT_REJECTED,
    "forbidden": RESULT_REJECTED,
    "failed": RESULT_FAILED,
    "error": RESULT_FAILED,
    "retried": RESULT_RETRIED,
    "retry": RESULT_RETRIED,
    "returned_error_packet": RESULT_RETURNED_ERROR_PACKET,
    "returned-error-packet": RESULT_RETURNED_ERROR_PACKET,
}

BOUNDED_DIRECTIONS: Final[frozenset[str]] = frozenset({"request", "response"})

REGISTRY = CollectorRegistry(auto_describe=True)

REQUESTS_TOTAL = Counter(
    "constellation_node_requests_total",
    "Total execute/relay requests by bounded result class",
    ["service", "result"],
    registry=REGISTRY,
)
READY_GAUGE = Gauge(
    "constellation_node_ready",
    "Service readiness",
    ["service"],
    registry=REGISTRY,
)
EXECUTION_SECONDS = Histogram(
    "constellation_node_execution_seconds",
    "Execute/relay handler wall time in seconds",
    ["service"],
    registry=REGISTRY,
)
PAYLOAD_BYTES = Histogram(
    "constellation_node_payload_bytes",
    "Request/response payload size in bytes",
    ["service", "direction"],
    registry=REGISTRY,
)
HOP_COUNT = Histogram(
    "constellation_node_hop_count",
    "Hop-trace length observed on inbound packets",
    ["service"],
    registry=REGISTRY,
    buckets=(0, 1, 2, 4, 8, 16, 32, 64),
)
RETRY_COUNT = Histogram(
    "constellation_node_retry_count",
    "Retry count observed on inbound packets (when present)",
    ["service"],
    registry=REGISTRY,
    buckets=(0, 1, 2, 3, 5, 8, 13),
)


def bound_result(status: str) -> str:
    """Map a free-form status string onto the closed result enum."""
    key = status.strip().lower().replace("-", "_")
    mapped = _STATUS_TO_RESULT.get(key)
    if mapped in BOUNDED_RESULTS:
        return mapped
    return RESULT_FAILED


def configure_logging(config: NodeRuntimeConfig) -> None:
    """Configure root logging for the node runtime."""
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        jsonlogger.JsonFormatter(  # type: ignore[attr-defined]
            "%(asctime)s %(levelname)s %(name)s %(message)s"
        )
    )
    root.addHandler(handler)


def set_readiness(*, config: NodeRuntimeConfig, ready: bool) -> None:
    """Update readiness gauge for the current service."""
    READY_GAUGE.labels(service=config.service_name).set(1 if ready else 0)


def record_request(*, config: NodeRuntimeConfig, action: str, status: str) -> None:
    """Backward-compatible counter increment; ``action`` is intentionally ignored."""
    _ = action  # unbounded — must not become a Prometheus label
    REQUESTS_TOTAL.labels(
        service=config.service_name,
        result=bound_result(status),
    ).inc()


def record_execution(
    *,
    config: NodeRuntimeConfig,
    result: str,
    duration_seconds: float | None = None,
    request_bytes: int | None = None,
    response_bytes: int | None = None,
    hop_count: int | None = None,
    retry_count: int | None = None,
) -> None:
    """Record one execute/relay observation with bounded labels only."""
    bounded = result if result in BOUNDED_RESULTS else bound_result(result)
    service = config.service_name
    REQUESTS_TOTAL.labels(service=service, result=bounded).inc()
    if duration_seconds is not None and duration_seconds >= 0:
        EXECUTION_SECONDS.labels(service=service).observe(duration_seconds)
    if request_bytes is not None and request_bytes >= 0:
        PAYLOAD_BYTES.labels(service=service, direction="request").observe(request_bytes)
    if response_bytes is not None and response_bytes >= 0:
        PAYLOAD_BYTES.labels(service=service, direction="response").observe(response_bytes)
    if hop_count is not None and hop_count >= 0:
        HOP_COUNT.labels(service=service).observe(hop_count)
    if retry_count is not None and retry_count >= 0:
        RETRY_COUNT.labels(service=service).observe(retry_count)


class ExecutionTimer:
    """Simple wall-clock timer for handler instrumentation."""

    __slots__ = ("_start",)

    def __init__(self) -> None:
        self._start = time.perf_counter()

    def seconds(self) -> float:
        return max(0.0, time.perf_counter() - self._start)


def metrics_response() -> Response:
    """Return the Prometheus metrics payload."""
    return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)


def bounded_label_snapshot() -> dict[str, tuple[str, ...]]:
    """Test helper: expose allowed label values for each metric family."""
    return {
        "requests_total.result": tuple(sorted(BOUNDED_RESULTS)),
        "payload_bytes.direction": tuple(sorted(BOUNDED_DIRECTIONS)),
    }
