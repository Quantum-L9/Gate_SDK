from __future__ import annotations


class TransportError(Exception):
    """Base exception for transport contract failures."""


class TransportValidationError(TransportError):
    """Raised when a transport packet violates structural or semantic rules."""


class TransportIntegrityError(TransportValidationError):
    """Raised when payload or transport hashes do not match."""


class TransportAuthenticationError(TransportValidationError):
    """Raised when transport authentication or signature state is invalid."""


class TransportAuthorizationError(TransportValidationError):
    """Raised when transport routing or delegation violates policy."""


class TransportExpiredError(TransportValidationError):
    """Raised when a packet has exceeded its expiry window."""


class TransportNotYetValidError(TransportValidationError):
    """Raised when a packet is not valid yet due to not_before constraints."""


class TenantMutationError(TransportValidationError):
    """Raised when a derived packet mutates immutable tenant context."""


class SchemaVersionError(TransportValidationError):
    """Raised when an unsupported schema version is encountered."""


class PacketSizeError(TransportValidationError):
    """Raised when a packet exceeds maximum allowed size."""
