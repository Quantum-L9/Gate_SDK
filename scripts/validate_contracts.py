#!/usr/bin/env python3
"""Contract validation script for Gate_SDK.

Checks:
  1. contracts/transport-packet.schema.json is valid JSON.
  2. All 11 required top-level fields are present.
  3. $id matches the canonical contract URL.
  4. additionalProperties=false is set at the top level.
  5. header.action has the canonical pattern constraint.
  6. header.schema_version has the '1.0' enum constraint.
  7. security.signature_algorithm has enum constraint (hmac-sha256, ed25519).
  8. hop_trace.items.status has enum constraint.
  9. hop_trace.items.hop_signature_algorithm has enum constraint.
 10. provenance.origin_kind has enum constraint.

Exit codes:
  0 -- all checks pass
  1 -- one or more checks failed
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
SCHEMA_PATH = SCRIPT_DIR.parent / "contracts" / "transport-packet.schema.json"
CANONICAL_ID = "https://constellation.local/contracts/transport-packet.schema.json"
ACTION_PATTERN = "^[a-z0-9][a-z0-9-]{0,63}$"

failures: list[str] = []


def fail(msg: str) -> None:
    failures.append(msg)
    print(f"  FAIL: {msg}")


def ok(msg: str) -> None:
    print(f"  OK:   {msg}")


print("[validate_contracts] checking transport-packet.schema.json...")

if not SCHEMA_PATH.exists():
    fail(f"schema not found: {SCHEMA_PATH}")
    sys.exit(1)

try:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
except json.JSONDecodeError as exc:
    fail(f"schema is not valid JSON: {exc}")
    sys.exit(1)

ok("schema is valid JSON")

if schema.get("$id") == CANONICAL_ID:
    ok("$id matches canonical")
else:
    fail(f"$id mismatch: got {schema.get('$id')!r}")

if schema.get("additionalProperties") is False:
    ok("top-level additionalProperties=false")
else:
    fail("top-level additionalProperties must be false")

required = set(schema.get("required", []))
expected = {
    "header", "address", "tenant", "payload", "security", "governance",
    "provenance", "delegation_chain", "hop_trace", "lineage", "attachments",
}
missing = expected - required
extra = required - expected
if not missing:
    ok(f"all {len(expected)} required top-level fields present")
else:
    fail(f"missing required fields: {sorted(missing)}")
if extra:
    fail(f"unexpected required fields: {sorted(extra)}")

props = schema.get("properties", {})

action_def = props.get("header", {}).get("properties", {}).get("action", {})
if action_def.get("pattern") == ACTION_PATTERN:
    ok("header.action has correct pattern constraint")
else:
    fail(f"header.action pattern mismatch: got {action_def.get('pattern')!r}")

schema_version_def = props.get("header", {}).get("properties", {}).get("schema_version", {})
if schema_version_def.get("enum") == ["1.0"]:
    ok("header.schema_version enum = ['1.0']")
else:
    fail(f"header.schema_version must have enum=['1.0'], got: {schema_version_def!r}")

sig_alg_def = props.get("security", {}).get("properties", {}).get("signature_algorithm", {})
has_algo = "enum" in sig_alg_def or any("enum" in b for b in sig_alg_def.get("anyOf", []))
if has_algo:
    ok("security.signature_algorithm has enum constraint")
else:
    fail("security.signature_algorithm must have enum constraint [hmac-sha256, ed25519]")

status_def = (
    props.get("hop_trace", {}).get("items", {}).get("properties", {}).get("status", {})
)
has_status = "enum" in status_def or any("enum" in b for b in status_def.get("anyOf", []))
if has_status:
    ok("hop_trace.items.status has enum constraint")
else:
    fail("hop_trace.items.status must have enum constraint")

hop_sig_alg_def = (
    props.get("hop_trace", {}).get("items", {}).get("properties", {}).get("hop_signature_algorithm", {})
)
has_hop_sig_alg = "enum" in hop_sig_alg_def or any("enum" in b for b in hop_sig_alg_def.get("anyOf", []))
if has_hop_sig_alg:
    ok("hop_trace.items.hop_signature_algorithm has enum constraint")
else:
    fail("hop_trace.items.hop_signature_algorithm must have enum constraint")

origin_def = props.get("provenance", {}).get("properties", {}).get("origin_kind", {})
if origin_def.get("enum"):
    ok("provenance.origin_kind has enum constraint")
else:
    fail("provenance.origin_kind must have enum constraint")

print()
if failures:
    print(f"[validate_contracts] FAILED -- {len(failures)} check(s) failed.")
    sys.exit(1)
else:
    print("[validate_contracts] all checks passed.")
