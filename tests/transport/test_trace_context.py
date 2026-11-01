from __future__ import annotations

import ast
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from constellation_node_sdk.trace_context import (
    TraceContext,
    TraceContextError,
    create_root_trace_context,
    inject_trace_context,
    parse_trace_context,
)
from constellation_node_sdk.transport.codec import encode_transport_packet
from constellation_node_sdk.transport.packet import create_transport_packet

TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"


def _assert_generic_error(operation: Callable[[], object], secret: str = "") -> None:
    with pytest.raises(TraceContextError) as captured:
        operation()
    error = captured.value
    assert str(error) == "invalid W3C trace context"
    assert error.__cause__ is None
    assert error.__context__ is None
    assert error.__suppress_context__ is True
    if secret:
        assert secret not in str(error)
        assert secret not in repr(error)


class _SecretErrorMapping(Mapping[str, object]):
    def __init__(self, secret: str, error_type: type[Exception] = TraceContextError) -> None:
        self._secret = secret
        self._error_type = error_type

    def __getitem__(self, key: str) -> object:
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        raise self._error_type(self._secret)

    def __len__(self) -> int:
        return 1


class _SecretErrorSequence(Sequence[str]):
    def __init__(self, secret: str, error_type: type[Exception] = TraceContextError) -> None:
        self._secret = secret
        self._error_type = error_type

    def __getitem__(self, index: int) -> str:
        raise self._error_type(self._secret)

    def __len__(self) -> int:
        return 1


class _SecretErrorText(str):
    def __new__(
        cls,
        secret: str,
        error_type: type[Exception] = TraceContextError,
    ) -> _SecretErrorText:
        value = super().__new__(cls, "vendor=value")
        value._secret = secret
        value._error_type = error_type
        return value

    def __init__(
        self,
        secret: str,
        error_type: type[Exception] = TraceContextError,
    ) -> None:
        del secret, error_type

    def split(self, separator: str | None = None, maxsplit: int = -1) -> list[str]:
        del separator, maxsplit
        raise self._error_type(self._secret)


class _OversizedSequence(Sequence[str]):
    def __init__(self, value_count: int) -> None:
        self._value_count = value_count

    def __getitem__(self, index: int) -> str:
        raise AssertionError(f"oversized sequence was indexed at {index}")

    def __len__(self) -> int:
        return self._value_count


class _BoundedScanMapping(Mapping[str, object]):
    def __getitem__(self, key: str) -> object:
        return "value"

    def __iter__(self) -> Iterator[str]:
        for index in range(10_000):
            if index >= 64:
                raise AssertionError("mapping scan exceeded the configured bound")
            yield f"x-{index}"

    def __len__(self) -> int:
        return 10_000


def test_root_context_is_valid_fresh_and_unsampled() -> None:
    first = create_root_trace_context()
    second = create_root_trace_context()

    assert parse_trace_context({"traceparent": first.traceparent}) == first
    assert first.tracestate is None
    assert first.traceparent.endswith("-00")
    assert first.traceparent != second.traceparent


def test_parse_and_inject_normalize_context_without_baggage() -> None:
    context = parse_trace_context(
        {
            "TraceParent": TRACEPARENT,
            "TrAcEsTaTe": ["acme=one", "vendor=two"],
            "Baggage": "customer-id=private",
        }
    )
    assert context == TraceContext(TRACEPARENT, "acme=one,vendor=two")
    assert context is not None

    original_headers: dict[str, object] = {
        "X-Request-ID": "request-7",
        "TraceParent": "00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-00",
        "TRACESTATE": "prior=value",
        "Baggage": "customer-id=private",
        "bAgGaGe": "session=private",
    }
    original_snapshot = dict(original_headers)

    injected = inject_trace_context(original_headers, context)

    assert original_headers == original_snapshot
    assert injected == {
        "X-Request-ID": "request-7",
        "traceparent": TRACEPARENT,
        "tracestate": "acme=one,vendor=two",
    }
    assert injected is not original_headers


def test_parse_returns_none_only_when_both_context_headers_are_absent() -> None:
    assert parse_trace_context({}) is None
    assert parse_trace_context({"baggage": "customer-id=private"}) is None
    _assert_generic_error(
        lambda: parse_trace_context({"tracestate": "vendor=private-marker"}),
        "private-marker",
    )


@pytest.mark.parametrize(
    "headers",
    [
        {"traceparent": None},
        {"traceparent": "not-a-traceparent-secret-marker"},
        {"traceparent": [TRACEPARENT, TRACEPARENT]},
        {"traceparent": TRACEPARENT, "TraceParent": TRACEPARENT},
    ],
)
def test_parse_rejects_invalid_or_repeated_traceparent_generically(
    headers: dict[str, object],
) -> None:
    _assert_generic_error(lambda: parse_trace_context(headers))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "traceparent",
    [
        TRACEPARENT.replace("00-", "01-", 1),
        TRACEPARENT.replace("4bf9", "4BF9", 1),
        f"00-{'0' * 32}-00f067aa0ba902b7-01",
        f"00-4bf92f3577b34da6a3ce929d0e0e4736-{'0' * 16}-01",
        TRACEPARENT.replace("-01", "-0A", 1),
        f" {TRACEPARENT}",
        f"{TRACEPARENT} ",
        f"{TRACEPARENT}\r\nprivate-marker",
    ],
)
def test_parse_rejects_non_v00_or_malformed_traceparent(traceparent: str) -> None:
    _assert_generic_error(
        lambda: parse_trace_context({"traceparent": traceparent}), "private-marker"
    )


@pytest.mark.parametrize(
    ("tracestate", "expected"),
    [
        ("", None),
        (" \t ", None),
        (
            ", \t, 1@vendor=one \t, \t acme=two \t, ",
            "1@vendor=one,acme=two",
        ),
        ([" \t", " 1@vendor=one ", " \t, acme=two"], "1@vendor=one,acme=two"),
    ],
)
def test_parse_accepts_empty_ows_and_multi_tenant_tracestate_members(
    tracestate: str | list[str],
    expected: str | None,
) -> None:
    context = parse_trace_context({"traceparent": TRACEPARENT, "tracestate": tracestate})

    assert context is not None
    assert context.traceparent == TRACEPARENT
    assert context.tracestate == expected


@pytest.mark.parametrize(
    "tracestate",
    [
        "Vendor=value",
        "vendor=",
        "=value",
        "vendor =value",
        "vendor= value",
        "vendor=has=equals",
        "vendor=value\r\nprivate-marker",
        "vendor@1=value",
        ["acme=one", "acme=two"],
        [f"key{index}=value" for index in range(33)],
        f"a={'x' * 257}",
        None,
    ],
)
def test_parse_drops_malformed_or_oversized_tracestate_with_valid_parent(
    tracestate: object,
) -> None:
    context = parse_trace_context(
        {"traceparent": TRACEPARENT, "tracestate": tracestate}  # type: ignore[dict-item]
    )

    assert context == TraceContext(TRACEPARENT)


def test_parse_observes_combined_tracestate_limit_with_individual_value_limit() -> None:
    at_limit = f"a={'x' * 256},b={'y' * 251}"
    over_limit = f"a={'x' * 256},b={'y' * 252}"
    assert len(at_limit) == 512
    assert len(over_limit) == 513

    accepted = parse_trace_context({"traceparent": TRACEPARENT, "tracestate": at_limit})
    dropped = parse_trace_context({"traceparent": TRACEPARENT, "tracestate": over_limit})

    assert accepted == TraceContext(TRACEPARENT, at_limit)
    assert dropped == TraceContext(TRACEPARENT)


@pytest.mark.parametrize(
    "tracestate",
    [
        "",
        " \t ",
        "Vendor=value",
        "vendor=",
        "vendor =value",
        "vendor= value",
        "vendor@1=value",
        "vendor=one,,acme=two",
        f"a={'x' * 257}",
        ("vendor=value",),
    ],
)
def test_trace_context_direct_construction_rejects_malformed_tracestate(
    tracestate: object,
) -> None:
    _assert_generic_error(
        lambda: TraceContext(TRACEPARENT, tracestate),  # type: ignore[arg-type]
    )


def test_trace_context_is_frozen_redacted_and_validates_direct_construction() -> None:
    context = TraceContext(TRACEPARENT, "1@vendor=opaque-secret")
    assert context.__dataclass_fields__["traceparent"]
    assert hasattr(context, "__slots__")
    assert TRACEPARENT not in repr(context)
    assert "opaque-secret" not in repr(context)

    with pytest.raises(FrozenInstanceError):
        context.traceparent = TRACEPARENT  # type: ignore[misc]

    _assert_generic_error(lambda: TraceContext("invalid-secret-marker"), "invalid-secret-marker")


def test_inject_rejects_bad_inputs_generically() -> None:
    context = TraceContext(TRACEPARENT)
    _assert_generic_error(lambda: inject_trace_context([], context))  # type: ignore[arg-type]
    _assert_generic_error(lambda: inject_trace_context({"x": "y"}, object()))  # type: ignore[arg-type]
    _assert_generic_error(lambda: inject_trace_context({1: "unsafe"}, context))  # type: ignore[dict-item]


def test_public_boundaries_sanitize_malicious_mapping_and_sequence_errors() -> None:
    secret = "attacker-controlled-secret"
    context = TraceContext(TRACEPARENT)

    for error_type in (TraceContextError, RuntimeError):
        _assert_generic_error(
            lambda error_type=error_type: parse_trace_context(  # type: ignore[arg-type]
                _SecretErrorMapping(secret, error_type)
            ),
            secret,
        )
        _assert_generic_error(
            lambda error_type=error_type: parse_trace_context(
                {"traceparent": _SecretErrorSequence(secret, error_type)}
            ),
            secret,
        )
        _assert_generic_error(
            lambda error_type=error_type: inject_trace_context(
                _SecretErrorMapping(secret, error_type), context
            ),
            secret,
        )
        _assert_generic_error(
            lambda error_type=error_type: TraceContext(
                TRACEPARENT, _SecretErrorText(secret, error_type)
            ),
            secret,
        )


def test_parse_bounds_header_scanning_and_sequence_access_before_materialization() -> None:
    _assert_generic_error(lambda: parse_trace_context(_BoundedScanMapping()))  # type: ignore[arg-type]
    _assert_generic_error(lambda: parse_trace_context({"traceparent": _OversizedSequence(2)}))

    context = parse_trace_context(
        {"traceparent": TRACEPARENT, "tracestate": _OversizedSequence(33)}
    )

    assert context == TraceContext(TRACEPARENT)


def test_trace_context_does_not_change_serialized_packet_body_or_hash() -> None:
    packet = create_transport_packet(
        action="enrich",
        payload={"entity_id": "42"},
        tenant="tenant-a",
        destination_node="gate",
        source_node="client",
        reply_to="client",
    )
    serialized_before = encode_transport_packet(packet)
    transport_hash_before = packet.security.transport_hash

    context = parse_trace_context({"traceparent": TRACEPARENT, "tracestate": "acme=one"})
    assert context is not None

    headers = inject_trace_context({"X-Request-ID": "request-7"}, context)

    assert headers["traceparent"] == TRACEPARENT
    assert encode_transport_packet(packet) == serialized_before
    assert packet.security.transport_hash == transport_hash_before


def test_trace_context_module_has_no_packet_or_runtime_imports() -> None:
    source_path = Path(__file__).parents[2] / "src" / "constellation_node_sdk" / "trace_context.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_modules.add(node.module)

    forbidden_parts = {"packet", "gate", "runtime", "logging", "prometheus", "opentelemetry"}
    assert all(not (set(module.split(".")) & forbidden_parts) for module in imported_modules)
