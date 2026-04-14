from __future__ import annotations

import logging
import sys

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

REGISTRY = CollectorRegistry(auto_describe=True)

# ---------------------------------------------------------------------------
# Existing metrics (unchanged)
# ---------------------------------------------------------------------------

REQUESTS_TOTAL = Counter(
    "constellation_node_requests_total",
    "Total execute requests",
    ["service", "action", "status"],
    registry=REGISTRY,
)
READY_GAUGE = Gauge(
    "constellation_node_ready",
    "Service readiness",
    ["service"],
    registry=REGISTRY,
)

# ---------------------------------------------------------------------------
# New metrics
# ---------------------------------------------------------------------------

REQUEST_DURATION_MS = Histogram(
    "constellation_node_request_duration_ms",
    "End-to-end handler execution time in milliseconds",
    ["service", "action"],
    buckets=[5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 30000],
    registry=REGISTRY,
)

TRANSPORT_ERRORS_TOTAL = Counter(
    "constellation_node_transport_errors_total",
    "Transport-layer errors broken down by error class",
    ["service", "action", "error_class"],
    registry=REGISTRY,
)

PACKET_SIZE_BYTES = Histogram(
    "constellation_node_packet_size_bytes",
    "Serialized inbound packet size in bytes",
    ["service", "action"],
    buckets=[512, 1024, 4096, 16384, 65536, 131072, 262144],
    registry=REGISTRY,
)

HOP_DEPTH = Histogram(
    "constellation_node_hop_depth",
    "Number of hops on completed packets",
    ["service", "action"],
    buckets=[1, 2, 3, 4, 6, 8, 16, 32, 64],
    registry=REGISTRY,
)

PACKET_GENERATION = Histogram(
    "constellation_node_packet_generation",
    "Lineage generation depth of inbound packets",
    ["service", "action"],
    buckets=[0, 1, 2, 3, 5, 8, 13, 21],
    registry=REGISTRY,
)

RETRY_ATTEMPTS_TOTAL = Counter(
    "constellation_node_retry_attempts_total",
    "Total step-executor retry attempts by action",
    ["service", "action"],
    registry=REGISTRY,
)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def configure_logging(config: NodeRuntimeConfig) -> None:
    """Configure root logging for the node runtime."""
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        jsonlogger.JsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root.addHandler(handler)


# ---------------------------------------------------------------------------
# Existing record helpers (unchanged)
# ---------------------------------------------------------------------------

def set_readiness(*, config: NodeRuntimeConfig, ready: bool) -> None:
    """Update readiness gauge for the current service."""
    READY_GAUGE.labels(service=config.service_name).set(1 if ready else 0)


def record_request(*, config: NodeRuntimeConfig, action: str, status: str) -> None:
    """Increment request counter for an executed action."""
    REQUESTS_TOTAL.labels(
        service=config.service_name,
        action=action.strip().lower(),
        status=status.strip().lower(),
    ).inc()


# ---------------------------------------------------------------------------
# New record helpers
# ---------------------------------------------------------------------------

def record_duration(
    *,
    config: NodeRuntimeConfig,
    action: str,
    duration_ms: float,
) -> None:
    """Record end-to-end handler execution time in milliseconds."""
    REQUEST_DURATION_MS.labels(
        service=config.service_name,
        action=action.strip().lower(),
    ).observe(duration_ms)


def record_error(
    *,
    config: NodeRuntimeConfig,
    action: str,
    error_class: str,
) -> None:
    """Increment transport error counter with the classified error code."""
    TRANSPORT_ERRORS_TOTAL.labels(
        service=config.service_name,
        action=action.strip().lower(),
        error_class=error_class.strip().lower(),
    ).inc()


def record_packet_size(
    *,
    config: NodeRuntimeConfig,
    action: str,
    size_bytes: int,
) -> None:
    """Record serialized inbound packet size in bytes."""
    PACKET_SIZE_BYTES.labels(
        service=config.service_name,
        action=action.strip().lower(),
    ).observe(size_bytes)


def record_hop_depth(
    *,
    config: NodeRuntimeConfig,
    action: str,
    depth: int,
) -> None:
    """Record the number of hops on a completed packet's hop_trace."""
    HOP_DEPTH.labels(
        service=config.service_name,
        action=action.strip().lower(),
    ).observe(depth)


def record_packet_generation(
    *,
    config: NodeRuntimeConfig,
    action: str,
    generation: int,
) -> None:
    """Record the lineage.generation depth of an inbound packet."""
    PACKET_GENERATION.labels(
        service=config.service_name,
        action=action.strip().lower(),
    ).observe(generation)


def record_retry(
    *,
    config: NodeRuntimeConfig,
    action: str,
) -> None:
    """Increment the retry attempt counter for a step executor action."""
    RETRY_ATTEMPTS_TOTAL.labels(
        service=config.service_name,
        action=action.strip().lower(),
    ).inc()


def metrics_response() -> Response:
    """Return the Prometheus metrics payload."""
    return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)
