from __future__ import annotations

from typing import Any


class GateClientError(Exception):
    """
    Base exception for every failure that leaves the Gate client surface.

    Callers classify Gate transport failures by catching these types. They must
    never need to import ``httpx`` or parse exception strings to decide whether a
    failure is retryable, permanent, or a local configuration mistake.
    """


class GateConfigurationError(GateClientError, ValueError):
    """
    Raised when the local Gate client is misconfigured.

    This is never a Gate failure: the operation cannot be attempted because the
    SDK was handed an incoherent configuration (for example a signing key with
    no key id). Retrying is pointless until configuration changes.

    Also a ``ValueError`` so that callers written against the pre-taxonomy SDK,
    which raised bare ``ValueError`` from this path, keep working.
    """


class GatePolicyError(GateClientError, ValueError):
    """
    Raised when an outbound packet violates Gate-only routing policy.

    The operation was rejected locally and never reached the network. Retrying
    the same packet cannot succeed.

    Also a ``ValueError`` for backward compatibility with callers written
    against the pre-taxonomy SDK.
    """


class GateSecurityError(GateClientError):
    """
    Raised when signing, signature verification, or packet integrity fails.

    ``direction`` is ``"outbound"`` when the local packet could not be signed or
    validated before sending, and ``"inbound"`` when a Gate response failed
    signature or integrity validation. An inbound security failure means the
    response could not be trusted; it must never be treated as a soft error.
    """

    def __init__(self, message: str, *, direction: str) -> None:
        super().__init__(message)
        self.direction = direction


class GateConnectionError(GateClientError):
    """
    Raised when Gate could not be reached at all.

    No application execution was performed by Gate. This is the canonical
    retryable transport failure.
    """


class GateTimeoutError(GateClientError, TimeoutError):
    """
    Raised when the transport deadline elapsed before Gate answered.

    ``timeout_seconds`` is the network deadline the SDK actually applied, which
    is derived from the caller's operation budget. Gate may or may not have
    executed the operation, so a retry is only safe under a stable idempotency
    key.

    Also a ``TimeoutError`` so ordinary timeout handling keeps working.
    """

    def __init__(self, message: str, *, timeout_seconds: float | None = None) -> None:
        super().__init__(message)
        self.timeout_seconds = timeout_seconds


class GateHTTPError(GateClientError):
    """
    Raised when Gate answered with a non-success HTTP status.

    ``status_code`` is the status Gate returned and ``response_text`` is the
    (truncated) body, so callers can distinguish a 4xx rejection from a 5xx
    outage without re-reading the wire.
    """

    _MAX_BODY_CHARS = 2048

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        response_text: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_text = (
            response_text[: self._MAX_BODY_CHARS] if response_text is not None else None
        )

    @property
    def is_client_error(self) -> bool:
        return 400 <= self.status_code < 500

    @property
    def is_server_error(self) -> bool:
        return 500 <= self.status_code < 600


class GateResponseError(GateClientError, ValueError):
    """
    Raised when Gate answered, but not with a canonical response.

    Covers undecodable bodies, non-object JSON, and bodies that do not validate
    as a ``TransportPacket``. Distinct from :class:`GateSecurityError`, which
    means the response was well-formed but could not be trusted.

    Also a ``ValueError`` for backward compatibility with callers written
    against the pre-taxonomy SDK.
    """

    def __init__(self, message: str, *, body: Any = None) -> None:
        super().__init__(message)
        self.body = body


class GateRegistrationError(GateClientError):
    """Raised when Gate registration cannot be completed safely."""


__all__ = [
    "GateClientError",
    "GateConfigurationError",
    "GateConnectionError",
    "GateHTTPError",
    "GatePolicyError",
    "GateRegistrationError",
    "GateResponseError",
    "GateSecurityError",
    "GateTimeoutError",
]
