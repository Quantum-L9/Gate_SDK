"""Packet-neutral utilities for W3C Trace Context version 00 headers."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import NoReturn

__all__ = [
    "TraceContext",
    "TraceContextError",
    "inject_trace_context",
    "parse_trace_context",
]

_ERROR_MESSAGE = "invalid W3C trace context"
_TRACEPARENT_RE = re.compile(
    r"00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}\Z",
    re.ASCII,
)
_TRACESTATE_KEY_RE = re.compile(
    r"(?:[a-z][a-z0-9_*/-]{0,255}|[a-z0-9][a-z0-9_*/-]{0,240}@[a-z][a-z0-9_*/-]{0,13})\Z",
    re.ASCII,
)
_CONTEXT_HEADER_NAMES = frozenset({"traceparent", "tracestate", "baggage"})
_MAX_SCANNED_HEADER_FIELDS = 64
_MAX_TRACEPARENT_VALUES = 1
_MAX_TRACESTATE_FIELD_VALUES = 32
_MAX_TRACESTATE_LENGTH = 512
_MAX_TRACESTATE_MEMBERS = 32
_MAX_TRACESTATE_VALUE_LENGTH = 256


class TraceContextError(ValueError):
    """Raised when W3C trace-context input is malformed or unsafe."""


def _raise_invalid() -> NoReturn:
    raise TraceContextError(_ERROR_MESSAGE) from None


@dataclass(frozen=True, slots=True, repr=False)
class TraceContext:
    """Validated W3C version 00 trace headers without packet coupling."""

    traceparent: str
    tracestate: str | None = None

    def __post_init__(self) -> None:
        try:
            traceparent = _validate_traceparent(self.traceparent)
            tracestate = (
                None if self.tracestate is None else _normalize_tracestate((self.tracestate,))
            )
        except Exception:
            pass
        else:
            object.__setattr__(self, "traceparent", traceparent)
            object.__setattr__(self, "tracestate", tracestate)
            return
        _raise_invalid()

    def __repr__(self) -> str:
        tracestate = "None" if self.tracestate is None else "<redacted>"
        return f"TraceContext(traceparent=<redacted>, tracestate={tracestate})"


def parse_trace_context(
    headers: Mapping[str, str | Sequence[str]],
) -> TraceContext | None:
    """Parse strict W3C v00 context from case-insensitive HTTP-style headers.

    Baggage is intentionally ignored and never becomes part of ``TraceContext``.
    A malformed ``tracestate`` is discarded when its ``traceparent`` is valid, as
    required for receiver interoperability.
    """
    try:
        if not isinstance(headers, Mapping):
            _raise_invalid()

        traceparent_values: tuple[str, ...] = ()
        raw_tracestate_fields: list[object] = []
        traceparent_fields = 0
        tracestate_fields = 0

        for header_name, header_value in _bounded_mapping_items(headers):
            if not isinstance(header_name, str):
                _raise_invalid()
            normalized_name = header_name.lower()
            if normalized_name == "traceparent":
                traceparent_fields += 1
                if traceparent_fields > 1:
                    _raise_invalid()
                traceparent_values = _header_values(
                    header_value,
                    maximum=_MAX_TRACEPARENT_VALUES,
                )
            elif normalized_name == "tracestate":
                tracestate_fields += 1
                raw_tracestate_fields.append(header_value)

        if traceparent_fields == 0:
            if tracestate_fields:
                _raise_invalid()
            return None
        if len(traceparent_values) != _MAX_TRACEPARENT_VALUES:
            _raise_invalid()

        traceparent = _validate_traceparent(traceparent_values[0])
        tracestate: str | None = None
        if tracestate_fields:
            try:
                tracestate_values = _tracestate_header_values(raw_tracestate_fields)
                tracestate = _normalize_tracestate(
                    tracestate_values,
                    allow_empty_members=True,
                )
            except Exception:
                tracestate = None

        return TraceContext(traceparent=traceparent, tracestate=tracestate)
    except Exception:
        pass
    _raise_invalid()


def inject_trace_context(headers: Mapping[str, object], context: TraceContext) -> dict[str, object]:
    """Copy headers, replace trace context, and deliberately drop baggage."""
    try:
        if not isinstance(headers, Mapping) or not isinstance(context, TraceContext):
            _raise_invalid()
        validated_context = TraceContext(
            traceparent=context.traceparent,
            tracestate=context.tracestate,
        )
        injected: dict[str, object] = {}
        for header_name, header_value in _bounded_mapping_items(headers):
            if not isinstance(header_name, str):
                _raise_invalid()
            if header_name.lower() not in _CONTEXT_HEADER_NAMES:
                injected[header_name] = header_value
        injected["traceparent"] = validated_context.traceparent
        if validated_context.tracestate is not None:
            injected["tracestate"] = validated_context.tracestate
        return injected
    except Exception:
        pass
    _raise_invalid()


def _bounded_mapping_items[HeaderName, HeaderValue](
    headers: Mapping[HeaderName, HeaderValue],
) -> Iterator[tuple[HeaderName, HeaderValue]]:
    """Yield at most a fixed number of mapping fields without using ``items()``."""
    header_names = iter(headers)
    for _ in range(_MAX_SCANNED_HEADER_FIELDS):
        try:
            header_name = next(header_names)
        except StopIteration:
            return
        yield header_name, headers[header_name]
    _raise_invalid()


def _header_values(value: object, *, maximum: int) -> tuple[str, ...]:
    """Read a bounded HTTP header value sequence using indexed access only."""
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (bytes, bytearray)) or not isinstance(value, Sequence):
        _raise_invalid()

    value_count = len(value)
    if value_count > maximum:
        _raise_invalid()

    values: list[str] = []
    for index in range(value_count):
        item = value[index]
        if not isinstance(item, str):
            _raise_invalid()
        values.append(item)
    return tuple(values)


def _tracestate_header_values(raw_values: Sequence[object]) -> tuple[str, ...]:
    """Collect bounded tracestate fields without splitting an oversized value."""
    values: list[str] = []
    for raw_value in raw_values:
        remaining = _MAX_TRACESTATE_FIELD_VALUES - len(values)
        if remaining <= 0:
            _raise_invalid()
        for value in _header_values(raw_value, maximum=remaining):
            if len(value) > _MAX_TRACESTATE_LENGTH:
                _raise_invalid()
            values.append(value)
    return tuple(values)


def _validate_traceparent(value: object) -> str:
    if not isinstance(value, str) or not _TRACEPARENT_RE.fullmatch(value):
        _raise_invalid()
    trace_id = value[3:35]
    parent_id = value[36:52]
    if trace_id == "0" * 32 or parent_id == "0" * 16:
        _raise_invalid()
    return value


def _normalize_tracestate(
    values: Sequence[str],
    *,
    allow_empty_members: bool = False,
) -> str | None:
    if any(not isinstance(value, str) for value in values):
        _raise_invalid()

    raw_length = max(len(values) - 1, 0)
    for value in values:
        if len(value) > _MAX_TRACESTATE_LENGTH:
            _raise_invalid()
        raw_length += len(value)
        if raw_length > _MAX_TRACESTATE_LENGTH:
            _raise_invalid()

    members: list[str] = []
    seen_keys: set[str] = set()
    for raw_value in values:
        for raw_member in raw_value.split(","):
            member = raw_member.strip(" \t")
            if not member:
                if allow_empty_members:
                    continue
                _raise_invalid()
            if member.count("=") != 1:
                _raise_invalid()

            key, value = member.split("=", 1)
            if not key or not value or key[-1] in " \t" or value[0] in " \t":
                _raise_invalid()
            if not _TRACESTATE_KEY_RE.fullmatch(key) or key in seen_keys:
                _raise_invalid()
            if len(value) > _MAX_TRACESTATE_VALUE_LENGTH:
                _raise_invalid()
            if any(character in ",=" or not 0x20 <= ord(character) <= 0x7E for character in value):
                _raise_invalid()

            seen_keys.add(key)
            members.append(f"{key}={value}")
            if len(members) > _MAX_TRACESTATE_MEMBERS:
                _raise_invalid()

    if not members:
        if allow_empty_members:
            return None
        _raise_invalid()

    normalized = ",".join(members)
    if len(normalized) > _MAX_TRACESTATE_LENGTH:
        _raise_invalid()
    return normalized
