# GATE_SDK PR FINDINGS BRIEF

```yaml
PR:
  number/url: 40 — https://github.com/Quantum-L9/Gate_SDK/pull/40
  branch: claude/gate-sdk-transport-closure-u2klcf
  exact_pr_head_sha: 6225f75
  base: main

VERDICT:
  local: GO — 612 passed, ruff clean, mypy clean, contracts pass, schema byte-identical
  contract: GO — wire contract unchanged; transport/ untouched by this branch
  runtime: GO — full rail proven root → Gate ingress → hop → derive → worker → response
  installability: GO — wheel built, installed into an empty dir with the checkout
    stripped from sys.path; 17 packaging tests pass, closure surface included
  cross_repo: GO — Odoo and EIE shadow transport provably removable; Gate shows no
    regression attributable to this branch
  merge: APPROVE (one base-branch CI check red — see BLOCKERS)

IMPLEMENTED:
  - GateClient.execute() — one application call taking business inputs only; no
    destination parameter, no TransportPacket parameter
  - One-deadline closure — network deadline derived from packet.header.timeout_ms;
    max_timeout_ms ceiling and transport_margin_ms, both explicit and off by default
  - Typed failure taxonomy — configuration / policy / security / connection /
    timeout / HTTP / response, all under GateClientError, causes chained
  - Registration closure — NodeRegistration with owner and constrained string
    metadata; register_node() removes the spec.yaml-on-disk requirement
  - Fixed: send_to_gate signed after validating, so validation judged a different
    artifact than the one sent and require_signature=True could never send
  - Reordered: routing policy → sign → transport validation of the signed packet
  - Fixed: two new env vars (and pre-existing L9_SIGNING_SECRET) were undocumented;
    added a test that parses the env readers and asserts .env.example covers them
  - Consumer drift guard, application example, and 126 new tests

PROVEN:
  - 612 passed on py3.12.3, py3.13.12, and at the declared cryptography lower
    bound (43.0.0); security/transport subset 223 passed at the lower bound
  - Deadline proven by reading request.extensions["timeout"] — the deadline httpx
    actually applied, not the one the SDK claims
  - No hidden retry proven by request count (1) across execute, send_to_gate,
    every retryable-looking status, dead socket, read timeout, and the full rail
  - Gate's own suite against this candidate: 180 passed, 1 failed — the identical
    single failure occurs with Gate_SDK main installed
  - Gate's real Dispatcher driven with this candidate: correct lineage, valid
    packet, accepted by the worker runtime
  - Odoo proof (8 assertions) and EIE registration proof (11 assertions), both
    against the installed SDK, both all-true
  - contracts/transport-packet.schema.json regenerates byte-identical

BLOCKERS:
  - gate-5-dep-audit is red — and is red on main (d09fe58) too. 7 advisories in
    cryptography 44.0.3; every fix version (46.0.5 … 50.0.0) is above the
    deliberate <45 ceiling set yesterday in #39 to keep IB-Odoo_19's 43.0.3 pin
    installable and stop pip floating past pyOpenSSL 24.3.0. No fix is portable
    without reversing that decision, so none was ported. This branch does not
    touch dependency manifests. Stood down with one PR comment naming the check
    and the reasoning. Not this PR's to resolve.

NON_BLOCKING:
  - SonarCloud S5332 on the internal_url http:// fallback — pre-existing on main
    (registration.py:55), only relocated by this refactor. Not changed to https:
    it is a cluster-internal address, Gate's own schema accepts both schemes, and
    every real node would break. Suppressed with stated justification matching
    the repo's existing NOSONAR precedent. SonarCloud is green on 389299e.
  - A leftover repo-root build/ shadows the build frontend in tests/packaging
    (local-only; make clean resolves).
  - Registration sends metadata.generated_by where EIE's hand-written payload
    omitted it. Accepted by Gate, ignored by ownership resolution.
  - Legacy gate tests still monkeypatch httpx.AsyncClient globally rather than
    using the new transport= seam.

CROSS_REPO_EFFECT:
  Odoo: send_action's packet construction, the timeout_ms arithmetic, the
    MAX_GATE_TIMEOUT_SECONDS clamp, classify_transport_failure() with its two
    token lists, and the blank-reason workaround all become deletable. Odoo keeps
    its domain: payload, durable run id, response mapping.
  EIE: app/services/gate_registration.py — module, httpx client, retry, logging —
    becomes deletable in favor of register_node(). Both gaps its docstring names
    (metadata.owner, health_endpoint) are closed. Execution runtime unaffected.
  Constellation.Gate: no regression from this branch. One stale assertion
    (tests/architecture/test_lineage_reentry.py, len(hop_trace) == 2) fails
    identically against Gate_SDK main because Gate pins a770e853, predating SDK
    commit 1d52369. Gate's runtime is unaffected — verified directly. Fix is
    Gate-side: bump the pin and update the assertion.

SDK CONTRACT:
  high_level_execute: yes — GateClient.execute()
  caller_manual_packet_required: no
  one_deadline: yes — packet header and socket derived from one budget
  hidden_execution_retries: none — proven by request count
  typed_errors: yes — full taxonomy, no httpx or string matching needed
  registration_complete: yes — owner metadata and spec-free registration

TEST EVIDENCE:
  - command: pytest -q (py3.12.3)
    result: 612 passed
  - command: pytest -q (py3.13.12)
    result: 612 passed
  - command: pytest -q (cryptography==43.0.0)
    result: 612 passed; tests/security + tests/transport 223 passed
  - command: pytest -q tests/packaging (built + installed wheel)
    result: 17 passed
  - command: ruff check src tests scripts examples
    result: All checks passed
  - command: ruff format --check src tests scripts examples
    result: 120 files already formatted
  - command: mypy src
    result: Success, 42 source files
  - command: python scripts/validate_contracts.py
    result: all checks passed
  - command: python scripts/generate_schema.py
    result: byte-identical — no wire drift
  - command: python -m build --wheel
    result: constellation_node_sdk-1.0.1-py3-none-any.whl
  - command: pytest -q (Constellation.Gate suite, candidate SDK force-installed)
    result: 180 passed, 1 failed (pre-existing)
  - command: pytest -q (Constellation.Gate suite, Gate_SDK main installed)
    result: 180 passed, 1 failed (identical — proves pre-existing)
  - command: Odoo consumability proof, installed SDK
    result: 8/8 assertions true
  - command: EIE registration proof, installed SDK
    result: 11/11 assertions true
  - command: gh check-runs on 389299e
    result: all success except gate-5-dep-audit (red on main too)

SCOPE DRIFT:
  none — two judgment calls named in FINAL_FINDINGS.md and defended:
    max_timeout_ms / transport_margin_ms (consumer-driven, both default off, and
    neither is a second deadline), and the keyword-only transport= test seam
    (required to prove the applied deadline and attempt count rather than assert
    them; carries no URL and is guarded against becoming a peer-dispatch path).
  Explicitly not done: no domain translation, no peer routing, no direct worker
    client, no Odoo/EIE-specific SDK code, no security default weakened, no
    dependency relaxed, no protocol change, no peer repository edited.

NEXT STRAIGHT_LINE MOVE:
  Enrichment.Inference.Engine — an ADR pack locking domain ownership and
  durable-before-completed semantics, with its contract consuming these Gate_SDK
  capabilities (register_node() for control-plane registration, the typed
  taxonomy for transport failures) rather than inventing another boundary.
```
