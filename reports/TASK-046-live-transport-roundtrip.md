# TASK-046 — Live transport round-trip (Gate ↔ CEG ↔ EIE)

**Date:** 2026-08-02
**Verdict:** `LIVE_TRANSPORT_ROUNDTRIP_PASS`
**Evidence digest:** `sha256:819de0dde2e1ad66ef0fa0f00a48350f7d8351c405def83c50aa093cde730235`
**Machine evidence:** [`TASK-046-live-transport-roundtrip.json`](./TASK-046-live-transport-roundtrip.json)

## What passed

PlasticOS live harness against a real local stack (no stub workers on `:9101`/`:9102`):

```text
PLASTICOS_GATE_LIVE_URL=http://127.0.0.1:9000
pytest tests/integration/test_gate_external_authority_e2e.py
→ 3 passed
  - test_live_gate_availability_is_available
  - test_live_match_round_trip
  - test_live_converge_round_trip
```

| Hop | Endpoint | Registered node | Proof |
|---|---|---|---|
| Gate | `http://127.0.0.1:9000` | `gate` | `/v1/health` healthy |
| Match worker | `http://127.0.0.1:8000` | `ceg-real` | Response `source_node=ceg-real` |
| Converge worker | `http://127.0.0.1:8001` | `enrichment-engine` | Response `source_node=enrichment-engine` |

Registry contained **only** those two workers. Stub ports `9101`/`9102` were free.

## SDK defects closed by this PR

1. **UTC-stable `transport_hash`** (`hashing.py`)
   Datetime canonicalize previously used the host local timezone. Packets minted on macOS EDT failed integrity checks inside UTC containers (`transport_hash does not match packet`).

2. **`derive()` hop reset** (`packet.py`)
   Child packets inherited parent `hop_trace` entries bound to the parent's `packet_id`, so client-side `validate_hop_trace` failed after Gate→worker round-trips. Parentage remains in `lineage`.

## Honest non-claims

This is **not** `LIVE_INTEGRATION_PASS` / full semantic CEG+EIE enrichment proof, and **not** `PROMOTION_APPROVED`.

Observed handler outcomes on the proof run (transport still valid):

| Action | Packet type | Payload status | Worker error (honest) |
|---|---|---|---|
| `match` | `failure` | `failed` | `ValidationError`: no candidate entity for `intake_to_buyer` (sparse graph) |
| `converge` | `failure` | `failed` | `ValueError` (EIE handler/payload shape) |

Both responses had `hop_packet_ids_match_header=true` and Odoo mappers accepted the payloads (`mapper_ok=true`).

## Spec alignment

See `contracts/TRANSPORT_PACKET_SPEC.md`:

- datetime hash material always UTC/`Z`
- `derive()` resets `hop_trace`; lineage carries parentage
