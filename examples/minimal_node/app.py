"""
The L9-PARTICIPATION-01 acid test: a node with zero Gate integration code.

Handlers + a spec file + ``create_node_app()``. Registration, recovery after a
Gate restart, and readiness (``/v1/ready``) come from the SDK. If this node
ever needs more than this to become an active Gate participant, the gap
belongs in Gate_SDK, not here.
"""

from __future__ import annotations

from constellation_node_sdk import create_node_app

from . import handlers  # noqa: F401

app = create_node_app()
