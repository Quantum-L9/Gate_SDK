"""
Gate-authority transport — for Constellation.Gate only.

This namespace exists so the privileged surface cannot be mistaken for the
node-facing one. Nothing here is exported from ``constellation_node_sdk.gate``
or from the package root, and nothing here is part of a normal application
integration:

* an **application** calls ``GateClient.execute()`` — it reaches Gate, and Gate
  alone, and cannot name a destination;
* a **packet-native caller** calls ``GateClient.send_to_gate()`` — same
  destination law, one level lower;
* **Gate** calls ``GateDispatchTransport.send_gate_authored_packet()`` — the
  only surface in the SDK that addresses a worker, and it accepts only packets
  that carry Gate's own routing authority.

Importing this module grants nothing. The authority check is on the packet, not
on the caller: a node cannot mint a packet that passes it.
"""

from __future__ import annotations

from constellation_node_sdk.gate_authority.config import GateDispatchTransportConfig
from constellation_node_sdk.gate_authority.dispatch import GateDispatchTransport
from constellation_node_sdk.gate_authority.errors import (
    GateDispatchAuthorityError,
    GateDispatchConfigurationError,
    GateDispatchError,
    GateDispatchSecurityError,
    WorkerConnectionError,
    WorkerHTTPError,
    WorkerResponseError,
    WorkerTimeoutError,
)

__all__ = [
    "GateDispatchAuthorityError",
    "GateDispatchConfigurationError",
    "GateDispatchError",
    "GateDispatchSecurityError",
    "GateDispatchTransport",
    "GateDispatchTransportConfig",
    "WorkerConnectionError",
    "WorkerHTTPError",
    "WorkerResponseError",
    "WorkerTimeoutError",
]
