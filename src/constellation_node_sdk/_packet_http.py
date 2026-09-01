"""
Canonical-packet HTTP machinery, shared by both sides of Gate.

There is exactly one implementation of "POST a canonical packet and decode a
canonical packet back". ``GateClient`` uses it for application → Gate, and the
Gate-authority dispatch transport uses it for Gate → resolved worker.

A second copy would be the real risk here: two implementations drift, and the
one that drifts is whichever is exercised less. The two callers differ only in
which exception types they raise, so the error classes are injected rather than
the code being duplicated.

This module is private. Nothing here is part of the public SDK surface.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

_MAX_BODY_CHARS = 2048


@dataclass(frozen=True)
class PacketTransportErrors:
    """
    The exception types one caller raises for each transport failure category.

    ``timeout`` and ``http`` are constructed with keyword arguments
    (``timeout_seconds``, ``status_code`` / ``response_text``); ``connection``
    and ``response`` take a message alone.
    """

    # Exception *factories*, not plain classes: the timeout and http types take
    # keyword arguments, which `type[Exception]` cannot express.
    timeout: Callable[..., Exception]
    connection: Callable[..., Exception]
    http: Callable[..., Exception]
    response: Callable[..., Exception]


def describe_exception(exc: BaseException) -> str:
    """
    Render an exception with its type, never relying on ``str(exc)`` alone.

    httpx timeout exceptions frequently stringify to ``""``. A consumer that
    logged only the message recorded a blank failure reason in production, so
    the type name is always carried.
    """
    detail = str(exc)
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


async def post_packet_json(
    *,
    url: str,
    json_body: dict[str, Any],
    timeout_seconds: float,
    errors: PacketTransportErrors,
    what: str,
    client: httpx.AsyncClient | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    headers: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
) -> httpx.Response:
    """
    Perform exactly one POST and translate every httpx failure into a typed error.

    There is no retry here, on either side of Gate. A failed request is returned
    to the caller as a typed failure: replaying it is a decision that needs
    semantic authority and a stable idempotency key, which transport does not
    have.

    When ``client`` is supplied the request reuses that client's connection
    pool, and ``timeout_seconds`` is applied per request so a pooled client's
    own default can never widen this call's deadline.
    """
    request_headers = {"Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)

    try:
        if client is not None:
            return await client.post(
                url,
                json=json_body,
                headers=request_headers,
                params=params,
                timeout=timeout_seconds,
            )
        async with httpx.AsyncClient(timeout=timeout_seconds, transport=transport) as owned:
            return await owned.post(
                url,
                json=json_body,
                headers=request_headers,
                params=params,
            )
    except httpx.TimeoutException as exc:
        # Ordered before TransportError: httpx.TimeoutException subclasses it,
        # so reversing these collapses "deadline elapsed" into "unreachable".
        raise errors.timeout(
            f"{what} did not respond within {timeout_seconds}s ({describe_exception(exc)})",
            timeout_seconds=timeout_seconds,
        ) from exc
    except httpx.HTTPError as exc:
        raise errors.connection(
            f"could not reach {what} at {url} ({describe_exception(exc)})"
        ) from exc


def raise_for_status(
    response: httpx.Response,
    *,
    context: str,
    errors: PacketTransportErrors,
) -> None:
    """Translate a non-success status into a typed error carrying the status."""
    if response.is_success:
        return
    raise errors.http(
        f"{context} returned HTTP {response.status_code}",
        status_code=response.status_code,
        response_text=response.text,
    )


def decode_packet_body(
    response: httpx.Response,
    *,
    context: str,
    errors: PacketTransportErrors,
) -> dict[str, Any]:
    """Decode a JSON object body, or raise the caller's response error type."""
    try:
        body = response.json()
    except ValueError as exc:
        raise errors.response(
            f"{context} was not decodable JSON ({describe_exception(exc)})"
        ) from exc
    if not isinstance(body, dict):
        raise errors.response(f"{context} must be a JSON object, got {type(body).__name__}")
    return body


def truncate_body(text: str | None) -> str | None:
    """Bound a response body carried on an exception."""
    return text[:_MAX_BODY_CHARS] if text is not None else None
