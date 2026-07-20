from __future__ import annotations

import pytest
from fastapi import HTTPException

from constellation_node_sdk.runtime.errors import classify_exception, raise_http_exception
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


@pytest.mark.parametrize(
    "exc",
    [
        ValueError("bad value"),
        TransportValidationError("invalid"),
        TransportIntegrityError("tampered"),
        PacketSizeError("too big"),
        SchemaVersionError("unsupported schema"),
        TenantMutationError("tenant changed"),
    ],
)
def test_classify_exception_maps_validation_family_to_400(exc: Exception) -> None:
    detail = classify_exception(exc)

    assert detail.code == "invalid_request"
    assert detail.status_code == 400
    assert detail.message == str(exc)


def test_classify_exception_maps_authentication_error_to_401_generic_message() -> None:
    detail = classify_exception(TransportAuthenticationError("bad signature"))

    assert detail.code == "authentication_failed"
    assert detail.status_code == 401
    assert detail.message == "transport authentication failed"


def test_classify_exception_maps_authorization_error_to_403_generic_message() -> None:
    detail = classify_exception(TransportAuthorizationError("not allowed"))

    assert detail.code == "authorization_failed"
    assert detail.status_code == 403
    assert detail.message == "transport authorization failed"


@pytest.mark.parametrize(
    "exc",
    [
        TransportExpiredError("expired"),
        TransportNotYetValidError("not yet valid"),
    ],
)
def test_classify_exception_maps_temporal_errors_to_409(exc: Exception) -> None:
    detail = classify_exception(exc)

    assert detail.code == "temporal_validity_failed"
    assert detail.status_code == 409
    assert detail.message == str(exc)


def test_classify_exception_maps_timeout_error_to_504() -> None:
    detail = classify_exception(TimeoutError("handler timed out"))

    assert detail.code == "execution_timeout"
    assert detail.status_code == 504
    assert detail.message == "handler execution timed out"


def test_classify_exception_maps_unknown_exception_to_500_generic_message() -> None:
    detail = classify_exception(RuntimeError("something exploded"))

    assert detail.code == "internal_error"
    assert detail.status_code == 500
    assert detail.message == "internal server error"


def test_raise_http_exception_raises_http_exception_with_classified_detail() -> None:
    original = TransportAuthorizationError("denied")
    with pytest.raises(HTTPException) as exc_info:
        raise_http_exception(original)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == {
        "code": "authorization_failed",
        "message": "transport authorization failed",
    }


def test_raise_http_exception_preserves_original_exception_as_cause() -> None:
    original = ValueError("bad input")

    with pytest.raises(HTTPException) as exc_info:
        raise_http_exception(original)

    assert exc_info.value.__cause__ is original
