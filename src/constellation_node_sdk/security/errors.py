from __future__ import annotations

from constellation_node_sdk.transport.errors import (
    TransportAuthenticationError,
    TransportAuthorizationError,
    TransportExpiredError,
    TransportIntegrityError,
    TransportNotYetValidError,
    TransportValidationError,
)


class SecurityError(Exception):
    """Base exception for the SDK security layer."""


__all__ = [
    "SecurityError",
    "TransportAuthenticationError",
    "TransportAuthorizationError",
    "TransportExpiredError",
    "TransportIntegrityError",
    "TransportNotYetValidError",
    "TransportValidationError",
]
