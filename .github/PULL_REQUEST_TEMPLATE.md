## PR Summary

<!-- One sentence: what does this change do? -->

---

## Merge-Rejection Checklist

Complete all sections before requesting review. Every item maps to a named rejection
condition in `AGENT_LOAD_PACK.md §23`. A PR MUST NOT be merged if any item is unchecked.

### Transport / Contract Integrity

- [ ] Gate-only egress preserved — no code paths allow node-to-peer dispatch or accept arbitrary worker URLs (rejection #1)
- [ ] No alternate transport format introduced — `TransportPacket` remains the only wire contract in runtime and GateClient paths (rejection #2)
- [ ] Schema is not drifted — `contracts/transport-packet.schema.json` matches `transport/models.py` after running `python scripts/generate_schema.py` (rejection #3)
- [ ] Contract consistency preserved — if any of `transport/models.py`, `contracts/transport-packet.schema.json`, contract spec docs, examples, or tests were updated, ALL five were updated together (rejection #4)
- [ ] Existing `TransportHop` entries are NOT mutated after appending (rejection #5)
- [ ] `transport_hash` is stable under hop-only changes — no hash change when only `hop_trace` changes (rejection #6)

### Tenant / Security Invariants

- [ ] Tenant immutability preserved — no derived packet has a different `TenantContext` from its parent (rejection #7)
- [ ] Validation defaults NOT weakened — no default reduced security posture (e.g., `require_signature` default remains `False`) (rejection #8)
- [ ] `TransportAuthenticationError` is NOT caught and suppressed anywhere (rejection #9)
- [ ] `dev_mode=True` is NOT present in staging/prod configs (rejection #10)

### CI Gates

- [ ] `ruff check src tests scripts` passes — zero lint errors (rejection #11)
- [ ] `mypy src` passes in strict mode — zero type errors (rejection #12)
- [ ] `pytest -q` passes — all tests pass (rejection #13)
- [ ] `python scripts/validate_contracts.py` passes (rejection #14)

### Test Coverage

- [ ] Meaningful transport/security/runtime/orchestrator changes include corresponding test additions in the correct `tests/` subdirectory (rejection #15)
- [ ] Examples (if changed) do NOT demonstrate node-to-node dispatch or non-Gate egress (rejection #16)

### Protocol Rules

- [ ] `schema_version` is `"1.0"` — no new schema version values introduced without full protocol revision (rejection #17)
- [ ] `max_attachment_size_bytes` does NOT exceed `max_packet_bytes` (rejection #18)
- [ ] `max_attachments > 0` is NOT configured without `attachment_allowed_schemes` (rejection #19)
- [ ] `require_idempotency_for_actions` contains ONLY actions that are also in `allowed_actions` (rejection #20)

### General

- [ ] No merge conflict markers present in any changed file (rejection #21)

---

## Change Classification

- [ ] Transport / Contract change (HIGH RISK — triggers schema regeneration + full contract update)
- [ ] Security / Validation change (HIGH RISK — must not weaken defaults)
- [ ] Routing / Delegation change (HIGH RISK — verify gate-only egress)
- [ ] Runtime / Handler change (MEDIUM)
- [ ] Orchestrator change (MEDIUM)
- [ ] CI / Test / Tooling change (LOW)
- [ ] Documentation / Example change (LOW)

## Pre-Merge Commands Run

```
python -m pip install -e ".[dev]"
python scripts/validate_contracts.py
python scripts/generate_schema.py
ruff check src tests scripts
mypy src
pytest -q
```

<!-- Paste abbreviated output or CI link confirming all passed. -->
