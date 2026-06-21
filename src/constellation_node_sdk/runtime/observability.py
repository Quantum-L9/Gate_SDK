from __future__ import annotations

import logging
import sys

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    generate_latest,
)
from pythonjsonlogger import jsonlogger
from starlette.responses import Response

from .config import NodeRuntimeConfig

REGISTRY = CollectorRegistry(auto_describe=True)
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


def configure_logging(config: NodeRuntimeConfig) -> None:
    """
    Configure root logging for the node runtime.
    """
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        jsonlogger.JsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root.addHandler(handler)


def set_readiness(*, config: NodeRuntimeConfig, ready: bool) -> None:
    """
    Update readiness gauge for the current service.
    """
    READY_GAUGE.labels(service=config.service_name).set(1 if ready else 0)


def record_request(*, config: NodeRuntimeConfig, action: str, status: str) -> None:
    """
    Increment request counter for an executed action.
    """
    REQUESTS_TOTAL.labels(
        service=config.service_name,
        action=action.strip().lower(),
        status=status.strip().lower(),
    ).inc()


def metrics_response() -> Response:
    """
    Return the Prometheus metrics payload.
    """
    return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)
