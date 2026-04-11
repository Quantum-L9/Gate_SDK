from __future__ import annotations


class GateClientError(Exception):
    """Base exception for Gate client failures."""


class GatePolicyError(GateClientError):
    """Raised when an outbound packet violates Gate-only routing policy."""


class GateResponseError(GateClientError):
    """Raised when Gate returns an invalid or non-canonical response."""


class GateRegistrationError(GateClientError):
    """Raised when Gate registration cannot be completed safely."""
