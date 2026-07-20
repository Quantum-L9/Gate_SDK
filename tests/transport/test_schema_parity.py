"""MISALIGNED-001-004: Verify transport-packet.schema.json matches models.py invariants."""

from __future__ import annotations

import json
from pathlib import Path

SCHEMA_PATH = Path(__file__).parents[2] / "contracts" / "transport-packet.schema.json"


def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def test_schema_file_exists() -> None:
    assert SCHEMA_PATH.exists(), f"schema not found at {SCHEMA_PATH}"


def test_header_action_has_pattern_constraint() -> None:
    schema = load_schema()
    action_def = schema["properties"]["header"]["properties"]["action"]
    assert action_def.get("pattern") == "^[a-z0-9][a-z0-9._-]{0,63}$", (
        f"header.action missing pattern; got: {action_def!r}"
    )


def test_header_schema_version_enum() -> None:
    schema = load_schema()
    sv_def = schema["properties"]["header"]["properties"]["schema_version"]
    assert sv_def.get("enum") == ["1.0"], (
        f"header.schema_version must have enum=['1.0']; got: {sv_def!r}"
    )


def test_security_signature_algorithm_enum() -> None:
    schema = load_schema()
    sig_alg = schema["properties"]["security"]["properties"]["signature_algorithm"]
    valid_values = {"hmac-sha256", "ed25519"}
    found: set[str] = set()
    if "enum" in sig_alg:
        found = {v for v in sig_alg["enum"] if v is not None}
    for branch in sig_alg.get("anyOf", []):
        if "enum" in branch:
            found |= {v for v in branch["enum"] if v is not None}
    missing = valid_values - found
    assert not missing, (
        f"security.signature_algorithm missing enum values {missing}; schema: {sig_alg!r}"
    )


def test_hop_status_enum() -> None:
    schema = load_schema()
    status_def = schema["properties"]["hop_trace"]["items"]["properties"]["status"]
    expected = {"received", "validated", "processing", "delegated", "completed", "failed"}
    found: set[str] = set()
    if "enum" in status_def:
        found = set(status_def["enum"])
    for branch in status_def.get("anyOf", []):
        if "enum" in branch:
            found |= set(branch["enum"])
    missing = expected - found
    assert not missing, (
        f"hop_trace.items.status missing enum values {missing}; schema: {status_def!r}"
    )


def test_hop_signature_algorithm_enum() -> None:
    schema = load_schema()
    hop_sig_alg = schema["properties"]["hop_trace"]["items"]["properties"][
        "hop_signature_algorithm"
    ]
    valid_values = {"hmac-sha256", "ed25519"}
    found: set[str] = set()
    if "enum" in hop_sig_alg:
        found = {v for v in hop_sig_alg["enum"] if v is not None}
    for branch in hop_sig_alg.get("anyOf", []):
        if "enum" in branch:
            found |= {v for v in branch["enum"] if v is not None}
    missing = valid_values - found
    assert not missing, (
        f"hop_signature_algorithm missing enum values {missing}; schema: {hop_sig_alg!r}"
    )


def test_required_top_level_fields() -> None:
    schema = load_schema()
    required = set(schema.get("required", []))
    expected = {
        "header",
        "address",
        "tenant",
        "payload",
        "security",
        "governance",
        "provenance",
        "delegation_chain",
        "hop_trace",
        "lineage",
        "attachments",
    }
    assert required == expected


def test_top_level_additional_properties_false() -> None:
    schema = load_schema()
    assert schema.get("additionalProperties") is False
