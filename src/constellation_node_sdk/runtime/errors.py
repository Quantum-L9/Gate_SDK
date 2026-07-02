from __future__ import annotations

from dataclasses import dataclass
from typing import NoReturn

from fastapi import HTTPException

from constellation_node_sdk.transport.errors import (
    PacketSizeError,
    SchemaVersionError,
    TenantMutationError,
    TransportAuthenticationError,
    TransportAuthorizationError,
    TransportExpiredError,
    TransportIntegrityError,
    TransportNotYetValidError,
    TransportValidationError,
)


class RuntimeErrorBase(Exception):
    """Base exception for runtime-layer failures."""


class RuntimeConfigurationError(RuntimeErrorBase):
    """Raised when runtime configuration is invalid or incomplete."""


class RuntimeValidationError(RuntimeErrorBase):
    """Raised when an inbound request fails runtime validation."""


class RuntimeExecutionError(RuntimeErrorBase):
    """Raised when handler execution fails within the runtime shell."""


@dataclass(frozen=True)
class RuntimeErrorDetail:
    code: str
    message: str
    status_code: int


def classify_exception(exc: Exception) -> RuntimeErrorDetail:
    """
    Map runtime and transport exceptions to safe HTTP-facing error details.
    """
    if isinstance(
        exc,
        (
            ValueError,
            TransportValidationError,
            TransportIntegrityError,
            PacketSizeError,
            SchemaVersionError,
            TenantMutationError,
        ),
    ):
        return RuntimeErrorDetail(
            code="invalid_request",
            message=str(exc),
            status_code=400,
        )

    if isinstance(exc, (TransportAuthenticationError,)):
        return RuntimeErrorDetail(
            code="authentication_failed",
            message="transport authentication failed",
            status_code=401,
        )

    if isinstance(exc, (TransportAuthorizationError,)):
        return RuntimeErrorDetail(
            code="authorization_failed",
            message="transport authorization failed",
            status_code=403,
        )

    if isinstance(exc, (TransportExpiredError, TransportNotYetValidError)):
        return RuntimeErrorDetail(
            code="temporal_validity_failed",
            message=str(exc),
            status_code=409,
        )

    if isinstance(exc, TimeoutError):
        return RuntimeErrorDetail(
            code="execution_timeout",
            message="handler execution timed out",
            status_code=504,
        )

    return RuntimeErrorDetail(
        code="internal_error",
        message="internal server error",
        status_code=500,
    )


def raise_http_exception(exc: Exception) -> NoReturn:
    """
    Raise a FastAPI HTTPException from a classified runtime exception.
    """
    detail = classify_exception(exc)
    raise HTTPException(
        status_code=detail.status_code,
        detail={
            "code": detail.code,
            "message": detail.message,
        },
    ) from exc
