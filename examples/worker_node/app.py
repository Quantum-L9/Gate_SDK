from __future__ import annotations

from constellation_node_sdk import create_node_app

from . import handlers  # noqa: F401

app = create_node_app(
    service_name="score-node",
    version="1.0.0",
)
