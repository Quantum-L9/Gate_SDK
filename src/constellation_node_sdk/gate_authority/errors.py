from __future__ import annotations

from typing import Any

from constellation_node_sdk._packet_http import truncate_body


class GateDispatchError(Exception):
    """
    Base exception for Gate-authorized worker dispatch failures.

    Deliberately a separate hierarchy from ``GateClientError``. That one means
    "the call to Gate failed"; these mean "the call Gate made to a worker
    failed". Collapsing them would give Gate a single ``except`` that cannot
    tell which leg of the rail broke, and would make ``GateConnectionError``
    read as "could not reach Gate" while actually meaning a worker was down.

    Constellation.Gate classifies dispatch outcomes by catching these types.
    It must never need ``httpx`` or a message substring to do it.
    """


class GateDispatchAuthorityError(GateDispatchError, ValueError):
    """
    Raised when a packet is not a genuine Gate-authored dispatch.

    This is the guard that stops the worker transport from becoming a
    node-to-peer side door: supplying a worker URL is never sufficient, because
    the packet itself must carry Gate's routing authority. Raised before any
    network call.
    """


class GateDispatchConfigurationError(GateDispatchError, ValueError):
    """
    Raised when the dispatch transport is misconfigured.

    Not a worker failure: the dispatch cannot be attempted. Retrying is
    pointless until configuration changes.
    """


class GateDispatchSecurityError(GateDispatchError):
    """
    Raised when signing, signature verification, or packet integrity fails.

    ``direction`` is ``"outbound"`` for the Gate-authored packet and
    ``"inbound"`` for the worker's response. An inbound security failure means
    the worker's answer could not be trusted and must never be softened into a
    parsing error.
    """

    def __init__(self, message: str, *, direction: str) -> None:
        super().__init__(message)
        self.direction = direction


class WorkerConnectionError(GateDispatchError):
    """
    Raised when the resolved worker could not be reached at all.

    The worker performed no execution. Gate decides what this means for that
    node's health — the SDK does not mark, evict, or fail over.
    """


class WorkerTimeoutError(GateDispatchError, TimeoutError):
    """
    Raised when the worker did not answer within the packet's budget.

    ``timeout_seconds`` is the deadline actually applied, derived from
    ``packet.header.timeout_ms``. The worker may or may not have executed, so a
    replay is Gate's decision under a stable idempotency key, never the SDK's.
    """

    def __init__(self, message: str, *, timeout_seconds: float | None = None) -> None:
        super().__init__(message)
        self.timeout_seconds = timeout_seconds


class WorkerHTTPError(GateDispatchError):
    """
    Raised when the worker answered with a non-success HTTP status.

    ``status_code`` and the truncated ``response_text`` let Gate distinguish a
    4xx rejection from a 5xx outage without re-reading the wire.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        response_text: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_text = truncate_body(response_text)

    @property
    def is_client_error(self) -> bool:
        return 400 <= self.status_code < 500

    @property
    def is_server_error(self) -> bool:
        return 500 <= self.status_code < 600


class WorkerResponseError(GateDispatchError, ValueError):
    """
    Raised when the worker answered, but not with a canonical response packet.

    Covers undecodable bodies, non-object JSON, bodies that do not validate as a
    ``TransportPacket``, and packets whose routing does not answer this dispatch.
    Distinct from :class:`GateDispatchSecurityError`, which means the response
    was well-formed but untrusted.
    """

    def __init__(self, message: str, *, body: Any = None) -> None:
        super().__init__(message)
        self.body = body


__all__ = [
    "GateDispatchAuthorityError",
    "GateDispatchConfigurationError",
    "GateDispatchError",
    "GateDispatchSecurityError",
    "WorkerConnectionError",
    "WorkerHTTPError",
    "WorkerResponseError",
    "WorkerTimeoutError",
]
