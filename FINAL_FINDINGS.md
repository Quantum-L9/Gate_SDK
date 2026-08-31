# FINAL_FINDINGS — Gate_SDK transport closure

# Executive Verdict

**GO for merge.** Gate_SDK now exposes a sufficient application transport
closure. A business application reaches Gate through one call carrying business
inputs only; every transport mechanic it previously performed itself is owned by
the SDK.

Nothing in the wire contract changed. `contracts/transport-packet.schema.json`
regenerates byte-identical, and `TransportPacket`, hashing, `derive()`, and hop
semantics are untouched. This was an abstraction closure over existing
primitives, not a protocol revision.

One external blocker exists and is **not** caused by this branch: one
Constellation.Gate test fails identically against Gate_SDK `main`. It is a stale
assertion in Gate, is deferred with an owner, and does not affect Gate's runtime.

# Repository / Branch / Candidate HEAD

| Field | Value |
|---|---|
| Repository | `Quantum-L9/Gate_SDK` |
| Branch | `claude/gate-sdk-transport-closure-u2klcf` |
| Candidate HEAD | `6225f75ae185dd6fff98a12c670898a536069392` |
| Pull request | [#40](https://github.com/Quantum-L9/Gate_SDK/pull/40) (base `main`) — CI green except `gate-5-dep-audit`, which is red on `main` too |
| Base (`main`) at start | `d09fe58a6cd68ef8aa883896c68badc95f96e090` |
| Package version | `1.0.1` (unchanged) |
| `requires-python` | `>=3.12` (unchanged) |
| cryptography constraint | `>=43.0.0,<45` (unchanged — deliberately not relaxed) |
| Open local changes at start | none tracked (`.claude/` untracked, session-managed, not committed) |

Peer repositories, read-only, at these heads:

| Repository | HEAD |
|---|---|
| `Quantum-L9/Constellation.Gate` | `545eda4259121dbce85c084385f68d00632981d7` |
| `Quantum-L9/Enrichment.Inference.Engine` | `cfda45043477bfe4a0f2a8c249ff9be30d1705aa` |
| `cryptoxdog/IB-Odoo_19` | `ce8ff00a51fffc73119160df99a9bca9af39b6c6` |

No peer repository was edited.

# Initial Capability Gaps

Each gap below was read out of a consuming repository, not inferred.

| # | Gap | Evidence |
|---|---|---|
| 1 | No application-level call. Applications assembled `create_transport_packet(...)` + `send_to_gate(...)` themselves. | `plasticos_gate/services/gate_client.py::send_action` builds the packet, names `destination_node="gate"`, and derives tenant/source/reply-to. |
| 2 | Two deadlines the consumer had to keep in step. | Same file: `timeout_ms=int(float(config.timeout_seconds) * 1000)`, with a code comment explaining that the SDK would otherwise advertise 30s while the caller waited for something else. |
| 3 | No typed failure surface. | Same file ships `classify_transport_failure()` — a 40-line substring matcher over `str(exc)` with `_RETRYABLE_TOKENS` / `_PERMANENT_TOKENS` lists. |
| 4 | Timeout failures carried no reason. | Comment recorded in the same file: an exhausted budget raised httpx `ConnectTimeout` with `str(exc) == ""`, so the operator saw `"Gate enrichment failed (retryable): "` and the run stored `validation_issues=[""]`. |
| 5 | Registration could not emit `metadata.owner`. | EIE `app/services/gate_registration.py` docstring: *"the SDK's `build_registration_payload` does not emit `metadata.owner`"*. Gate requires it — `routing/action_ownership.py::assert_registration_ownership` raises `canonical action ... requires metadata.owner` for `converge`, `graph-inference-result`, `match`, `sync`, `outcomes`. |
| 6 | Registration was reachable only through a `spec.yaml` on disk. | EIE builds its registration from `Settings`, so it wrote its own `httpx` admin client. |
| 7 | Docs described an API that did not exist. | `docs/gate-client.md` documented `client.execute(action=..., payload=..., tenant=...)` before this branch. The design was right; the implementation was missing. |

# Final Public API

Application surface — the whole outbound integration:

```python
response = await client.execute(
    action="converge",
    payload=domain_payload,
    tenant="tenant-a",
    idempotency_key="erp:enrichment:run-4711",
    timeout_ms=25_000,
    correlation_id="run-4711",
)
```

Parameters: `action`, `payload`, `tenant`, `idempotency_key`, `timeout_ms`,
`correlation_id`, `trace_id`, `classification`, `compliance_tags`,
`retention_days`, `priority`. All business concerns; no transport mechanics.

There is **no** `destination_node`, `peer_url`, `worker_url`, or `url`
parameter, and no parameter annotated `TransportPacket`. Both are asserted by
`tests/gate/test_consumer_architecture_guard.py`.

Preserved protocol primitives (unchanged): `create_transport_packet()`,
`TransportPacket.derive()`, `TransportPacket.with_hop()`,
`GateClient.send_to_gate()`, `create_node_app()`, orchestrator `StepExecutor`.

New exports: `NodeRegistration`, `register_node`, `build_node_registration`,
`GateConfigurationError`, `GateConnectionError`, `GateHTTPError`,
`GateSecurityError`, `GateTimeoutError` (plus the previously-defined
`GateClientError`, `GatePolicyError`, `GateResponseError`,
`GateRegistrationError`, now reachable from the package root).

New config fields: `max_timeout_ms` (default `None`), `transport_margin_ms`
(default `0`).

New keyword-only, defaulted `transport=` seam on `GateClient.__init__` — an
SDK-internal test seam that carries no URL and cannot reach a peer node.

# Packet Construction State

`caller_manual_root_packet_required: false`.

`execute()` builds the canonical root packet: destination is always
`config.allowed_gate_destination`, source and reply-to are always
`config.local_node`, and provenance is node-origin with
`requested_action == header.action`. An application cannot construct a
peer-targeted packet through this surface even by accident, because it never
supplies a destination.

The packet-native path is unchanged and still fails closed on a peer-targeted
packet — now with `GatePolicyError` rather than a bare `ValueError`, and still
before any network call.

# Deadline State

One budget. `execute(timeout_ms=...)` (or `config.timeout_seconds` as the
default) is written to `packet.header.timeout_ms`, and the real network deadline
is derived from that same header value. `send_to_gate()` derives its deadline
from the packet it was handed.

Two explicit, off-by-default knobs: `max_timeout_ms` (hard ceiling, applied to
both the header and the socket) and `transport_margin_ms` (subtractive
reservation so the SDK raises a typed timeout before the caller's own deadline).
A margin that consumes the budget is a `GateConfigurationError`, not a silent
clamp.

`tests/gate/test_deadline_closure.py` reads `request.extensions["timeout"]` —
the deadline httpx actually applied — rather than the one the SDK claims.

**Behavior change:** the network deadline previously came from
`config.timeout_seconds` regardless of the packet. It now comes from the packet
header. For any caller whose two values already agreed — including every
consumer that computed one from the other — behavior is identical. For one where
they disagreed, the packet's advertised budget now wins, which is strictly the
safer direction.

# Retry State

`hidden_application_retries: false`.

`execute()` and `send_to_gate()` perform exactly one POST. Proven by request
count, not by absence of retry code:
`test_execute_performs_exactly_one_request`, the parametrized
`test_gate_client_does_not_retry_a_retryable_looking_status` (429/500/502/503/504),
dead-socket and read-timeout cases, and
`test_the_rail_performs_exactly_one_execution` across the full rail.
`GateClientConfig` exposes no retry field.

Registration retries — bounded, visible backoff `[1.0, 2.0]`, asserted in
`test_registration_retry_is_bounded_and_visible` — because it is control-plane
reconciliation, not application execution. A Gate rejection (400/401/403/409/422)
is never retried.

# Idempotency State

Caller owns business identity; the SDK owns its transport slot. The value is
placed into `header.idempotency_key` verbatim
(`test_execute_carries_caller_idempotency_verbatim`), and an absent key stays
absent — no payload hash is substituted
(`test_execute_without_idempotency_key_invents_nothing`). It survives Gate's
derivation to the worker (`test_transport_metadata_survives_gate_derivation`).

# Error Taxonomy

```
GateClientError
├── GateConfigurationError  (also ValueError)   local misconfiguration; never reached the wire
├── GatePolicyError         (also ValueError)   outbound Gate-only routing violation
├── GateSecurityError       (.direction)        signing / signature / integrity failure
├── GateConnectionError                         Gate never reached; nothing ran
├── GateTimeoutError        (also TimeoutError) deadline elapsed (.timeout_seconds)
├── GateHTTPError           (.status_code, .response_text, .is_client_error, .is_server_error)
├── GateResponseError       (also ValueError)   answer was not a canonical packet
└── GateRegistrationError                       registration transport failure
```

Every category is reachable, distinct, and chains `__cause__`. Deliberate
separations:

- `GateTimeoutError` is not a `GateConnectionError`, despite
  `httpx.TimeoutException` subclassing `httpx.TransportError` — an
  order-of-except mistake would silently collapse them.
- A tampered response raises `GateSecurityError`, not `GateResponseError`.
  Collapsing them would let a caller treat a broken hash as a dialect problem
  and retry into it.

No failure message is empty, including for causes that stringify to `""`
(`test_no_gate_failure_message_is_empty`). A static guard asserts no
`raise ValueError(` survives in `gate/client.py` or `gate/policy.py`.

The `ValueError` / `TimeoutError` co-inheritance is deliberate backward
compatibility for the three categories that previously surfaced as `ValueError`.

# Registration State

`NodeRegistration` carries `node_name`, `internal_url`, `supported_actions`,
`priority_class`, `max_concurrent`, `health_endpoint`, `timeout_ms`, `version`,
`node_type`, `owner`, and `metadata`. `to_payload()` renders exactly the seven
keys Gate's `extra="forbid"` schema accepts.

`register_node()` registers from in-process configuration — no `spec.yaml`
required. `build_node_registration()` / `register_with_gate()` keep the spec
path, and both render an identical body
(`test_spec_yaml_and_typed_registration_render_the_same_body`).

`metadata` stays control-plane metadata: values must be strings (Gate's schema is
`dict[str, str]`), and the four SDK-derived keys (`owner`, `version`, `type`,
`generated_by`) are reserved and rejected if supplied through `metadata`.

# Derive / Hop State

Unchanged. `git diff main..HEAD -- src/constellation_node_sdk/transport/` is
empty.

Re-audited under the release-set harness rather than by reading:

- a Gate-derived child gets a new `packet_id`, `causation_id` = parent id,
  `lineage.parent_id` = parent id, preserved `root_id`, `generation + 1`;
- observation via `with_hop()` leaves `packet_id`, `transport_hash`, and
  `payload_hash` byte-identical;
- every hop on a child is bound to the child's own `packet_id`, and the child
  validates in the real worker runtime.

The harness's Gate stand-in mirrors Constellation.Gate's own dispatch sequence —
`with_hop` (ingress) → `derive` → `with_hop` (dispatch on the *derived* packet).
My first version built the dispatch hop from the pre-derive packet and failed
hop-trace validation, which is exactly the trap Gate documents in its own
`dispatch.py`.

# Security State

No security default was weakened. `require_signature`,
`verify_response_signatures`, `verify_hop_signatures`, and validation defaults
are unchanged. Signature and integrity failures raise a dedicated
`GateSecurityError` and are never swallowed.

**Defect found and fixed:** `send_to_gate()` validated the outbound packet
*before* signing it. Two consequences — outbound validation judged a different
artifact than the one actually sent, and `require_signature=True` rejected every
packet for a missing signature it was about to add, making self-signed traffic
impossible.

The outbound sequence is now: routing policy (cheap, no crypto) → sign →
transport validation of the signed packet. Policy runs first so a packet that
must not leave is refused before any crypto runs, and transport validation
judges exactly the bytes that go on the wire. Covered by
`test_a_signed_rail_round_trips`,
`test_routing_policy_is_checked_before_signing`, and
`test_outbound_transport_validation_judges_the_signed_packet`.

# Dependency / Installability State

No dependency was added, removed, or relaxed. The `cryptography>=43.0.0,<45`
bound (added in `main` to match the Odoo.sh pyOpenSSL window) is untouched.

| Proof | Result |
|---|---|
| Editable install, Python 3.12.3 | 612 passed |
| Full suite, Python 3.13.12 | 612 passed |
| Full suite, `cryptography==43.0.0` (declared lower bound) | 612 passed; security/transport subset 223 passed |
| `python -m build --wheel` | wheel built |
| Clean-directory wheel install, checkout stripped from `sys.path` | 17 passed (`tests/packaging/`) |

The installed-package harness builds the wheel from an exported copy, installs
it into an empty directory, strips the repo `src/` and any editable finder from
the child interpreter, and asserts in the child that the imported package really
lives under the install directory. It now also proves the closure surface from
the wheel: exports, a Gate-bound packet with the caller's budget on both the
header and the socket, one attempt, the typed taxonomy with no blank reason, and
a registration body carrying `metadata.owner`.

No `--no-deps` proof is presented as the installability evidence.

# Odoo Compatibility

Verdict: **PASS.** Odoo was not edited; the proof reproduces its
responsibilities against the installed SDK.

| Odoo code today | After adoption |
|---|---|
| `send_action` — packet construction, tenant assembly, `destination_node="gate"` | one `client.execute(...)` call |
| `timeout_ms=int(float(config.timeout_seconds) * 1000)` | deleted — SDK owns the deadline |
| `MAX_GATE_TIMEOUT_SECONDS = 30.0` clamp | `GateClientConfig.max_timeout_ms` |
| `classify_transport_failure()` + `_RETRYABLE_TOKENS` + `_PERMANENT_TOKENS` (~40 lines) | `isinstance` against the taxonomy |
| `detail = str(exc) or type(exc).__name__` workaround | deleted — no typed failure has a blank reason |
| `TransportFailureClass` enum | optional; the taxonomy carries the distinction |

Verified against the installed SDK: `execute()` reproduces the outcome;
destination forced to `gate` without a consumer constant; idempotency key
carried verbatim; `timeout_ms` no longer computed by the caller (header 30000,
applied socket read timeout 30.0s); the `MAX_GATE_TIMEOUT_SECONDS` clamp
expressible as configuration; retryable/permanent classification correct across
blank-string `ConnectTimeout`, `ConnectError`, 503, 403, and a non-canonical
body; no failure with a blank reason; no `httpx` import needed.

What Odoo would still own — correctly — is its domain: building the enrichment
payload, deriving the durable run id, and mapping the response. That is the
boundary ADR-SDK-009 draws.

`plasticos_gate/services/gate_client.py` still needs its `_run_async` sync/async
bridge; that is an Odoo-runtime concern, not transport, and is out of scope for
the SDK.

# EIE Compatibility

Verdict: **PASS.** EIE was not edited.

EIE's `build_payload` output is reproduced exactly through `NodeRegistration`:
`metadata.owner == "eie"` (the named gap), `health_endpoint ==
"/api/v1/health"` (the other named gap), exact `supported_actions`, exact
`version` and `type`, no `spec.yaml` on disk, and no key Gate's `extra="forbid"`
schema would reject. Wire behavior matches EIE's hand-written client: same
endpoint and `overwrite=true` param, same `X-Admin-Token` header, same non-fatal
boolean result.

One deliberate difference: the SDK also sends `metadata.generated_by =
"constellation-node-sdk"`, which EIE's hand-written payload omits. Gate's
`metadata` is a free `dict[str, str]`, so it is accepted and ignored by
ownership resolution. Flagged as a difference, not a defect.

`app/services/gate_registration.py` — the module, its `httpx` client, its retry
and logging — becomes deletable in favor of `register_node(...)`. EIE's
execution runtime already uses the SDK (`create_node_app`, `register_handler`,
`execute_transport_packet`) and is unaffected.

# Constellation.Gate Compatibility

Verdict: **PASS with one deferred external blocker.**

Gate's own suite was run against this candidate at Gate HEAD
`545eda4259121dbce85c084385f68d00632981d7`: **180 passed, 1 failed.**

The failure is `tests/architecture/test_lineage_reentry.py::test_lineage_is_preserved_across_gate_reentry_and_dispatch`,
asserting `len(posted_packet.hop_trace) == 2`.

It is **not caused by this branch**. Established, not assumed:

1. The same single failure occurs with Gate_SDK `main` (`d09fe58`) installed —
   verified by installing `main` into the same Gate environment.
2. `git diff main..HEAD -- src/constellation_node_sdk/transport/` is empty.
3. Gate pins Gate_SDK at `a770e853`, which predates SDK commit `1d52369`
   ("derive hop reset"). Gate's assertion encodes the pre-`1d52369` behavior.

A methodological note: the first Gate run showed 181 passed, but Gate's own
`pip install -e ".[dev]"` had silently replaced the candidate with its pinned
git SHA. That run proved nothing about this branch and is not counted. The
reported numbers are from a re-run after force-reinstalling the candidate and
confirming `GateClient.execute` was present.

Gate's **runtime is unaffected** — checked directly, not inferred. Driving
Gate's real `Dispatcher` with the candidate SDK, the dispatched packet has
correct lineage parent, preserved root, generation + 1, `resolved_by_gate=True`,
passes `validate_transport_packet`, and is accepted by the SDK worker runtime.
Only Gate's hop-count assertion is stale.

SDK `main` is correct here: carrying parent hops into a child produces a packet
that fails the SDK's own hop-trace validation, since `hop.packet_id` must equal
the child's `packet_id`. Nothing was changed to accommodate the stale
expectation.

# Contract / Schema Alignment

| Artifact | State |
|---|---|
| `contracts/transport-packet.schema.json` | regenerated, byte-identical — no wire change |
| `scripts/validate_contracts.py` | PASS |
| `contracts/NODE_REGISTRATION_SPEC.md` | updated: metadata, reserved keys, `metadata.owner`, both entry points, retry policy |
| `contracts/TRANSPORT_PACKET_SPEC.md` | unchanged (correctly — the packet contract did not change) |
| `contracts/ROUTING_POLICY_SPEC.md` | unchanged (correctly — routing law did not change) |
| `tests/contracts/release_set_api_matrix.json` | **deliberately unchanged** — it transcribes what consumers call *today*, and its own header forbids aspirational entries. New-surface coverage went into `tests/packaging/` instead. |
| `docs/gate-client.md` | rewritten against the real API |
| `README.md`, `ARCHITECTURE.md`, `AGENTS.md`, `CHANGELOG.md` | updated |
| `examples/application_client/` | added |

# Tests Actually Executed

| Command | Environment | Result |
|---|---|---|
| `pytest -q` (baseline, before changes) | py3.12.3 | 486 passed |
| `pytest -q` | py3.12.3 | **612 passed** |
| `pytest -q` | py3.13.12 | 612 passed |
| `pytest -q` (`cryptography==43.0.0`) | py3.12.3 | 612 passed |
| `pytest -q tests/security tests/transport` (`cryptography==43.0.0`) | py3.12.3 | 223 passed |
| `pytest -q tests/packaging` (built + installed wheel) | py3.12.3 | 17 passed |
| `ruff check src tests scripts examples` | py3.12.3 | All checks passed |
| `mypy src` | py3.12.3 | Success, 42 source files |
| `python scripts/validate_contracts.py` | py3.12.3 | all checks passed |
| `python scripts/generate_schema.py` | py3.12.3 | byte-identical output |
| `python -m build --wheel` | py3.12.3 | wheel built |
| Gate suite vs candidate SDK | py3.12.3 | 180 passed, 1 failed (pre-existing) |
| Gate suite vs SDK `main` | py3.12.3 | 180 passed, 1 failed (identical) |
| Odoo consumability proof (8 assertions) | installed SDK | all true |
| EIE registration proof (11 assertions) | installed SDK | all true |

126 tests added.

# Installed-Package Evidence

Wheel `constellation_node_sdk-1.0.1-py3-none-any.whl` built with the project's
own `pyproject.toml` and installed into an empty directory. In the child
interpreter, with `src/` and editable finders removed, the package resolves under
the install directory (asserted in the child on every call), `py.typed` ships, 67
symbols export, `GateClient.execute` is present, and `execute()` produces a
Gate-bound packet whose header budget (7000ms) and applied socket read timeout
(7.0s) agree, in one attempt, with the typed taxonomy intact.

# Remaining Blocking Defects

None in Gate_SDK.

One CI check is red on the PR and is **not** this branch's: `gate-5-dep-audit`
reports 7 advisories in `cryptography 44.0.3`. It is red on `main` (`d09fe58`)
too, this branch touches no dependency manifest, and every fix version
(46.0.5 … 50.0.0) sits above the deliberate `<45` ceiling set in #39 to keep
IB-Odoo_19's `43.0.3` pin installable and stop pip floating past pyOpenSSL
24.3.0. No fix is portable without reversing that decision, so none was ported
and the security pin was not relaxed. Stood down with one comment on PR #40.

Checked against the primary source rather than the pin's commit message:
`IB-Odoo_19/requirements.txt` documents that `<45` is exactly the top of
pyOpenSSL 24.3.0's declared range (cryptography 41.0.5–44.x), and that
exceeding it crashes the **entire** Odoo.sh registry on restart — every module
imports through `base`, not just `plasticos_*`. So the unblocking order is
fixed: pyOpenSSL must first be forward-pinned in IB-Odoo_19 to a release whose
range covers cryptography ≥ 46.0.5, and only then can the Gate_SDK ceiling
move. That is a coordinated two-repo dependency decision with a
production-severity failure mode, owned by the pin's author (#39) — not
something to reverse inside a transport-abstraction PR.

# Remaining Non-Blocking Defects

1. **Leftover repo-root `build/` shadows the `build` frontend.** A previous
   local `python -m build` leaves `build/`, after which `python -m build` inside
   `tests/packaging/conftest.py` resolves the directory instead of the frontend
   and every packaging test errors with a confusing "wheel build failed". CI
   runs on a clean checkout so this is local-only. `make clean` resolves it.
   Not fixed here: the fix belongs in the packaging harness (build from a temp
   cwd) and is unrelated to this closure.
2. **`metadata.generated_by` is sent where EIE's hand-written payload omitted
   it.** Accepted by Gate, ignored by ownership resolution. Recorded as a
   difference for anyone diffing registrations.
3. **Legacy tests monkeypatch `httpx.AsyncClient` globally.** The
   `route_gate_http` fixture predates the `transport=` seam. New tests use the
   seam. Not migrated: churning working tests was out of scope.
4. **SonarCloud S5332 on the `internal_url` fallback.** `build_node_registration`
   defaults to `http://{node_id}:8000` when `spec.yaml` omits `internal_url`.
   The line is pre-existing on `main` (`registration.py:55`) and was only
   relocated by this refactor, which is why Sonar counts it as new code. It is
   not changed to `https`: the value is a cluster-internal service address on
   the container network, Gate's own `NodeRegistrationInput` accepts both
   schemes for that reason, and every real node (EIE's
   `http://enrichment-engine:8000`) would break. Suppressed with a stated
   justification, matching the repository's existing `NOSONAR` precedent in
   `tests/security/test_validation.py`. Deployments that terminate TLS between
   nodes set `node.internal_url` explicitly.

# Scope Drift Audit

No drift. Nothing was added outside the required set.

Two judgment calls worth naming:

- **`max_timeout_ms` and `transport_margin_ms`** are new configuration, which
  ADR-SDK-004 cautions against ("do not introduce a second deadline abstraction
  if existing types suffice"). Neither is a second deadline: one is a ceiling on
  the single budget, the other a subtractive reservation from it, and both
  default to off. `max_timeout_ms` exists specifically so Odoo can delete its
  `MAX_GATE_TIMEOUT_SECONDS` clamp — consumer-driven, not speculative.
- **The `transport=` seam on `GateClient.__init__`** is new public surface. It
  was required to test the deadline actually applied and the request count,
  which is the difference between proving the closure and asserting it. It is
  keyword-only, defaulted, carries no URL, and is guarded by a test that it
  cannot become a peer-dispatch backdoor.

Explicitly not done, per scope: no domain translation, no peer routing, no
direct worker client, no Odoo- or EIE-specific SDK code, no security default
change, no dependency relaxation, no protocol change, and no edits to any peer
repository.

# Deferred Work

| Item | Owner | Removal trigger |
|---|---|---|
| Gate's stale `len(hop_trace) == 2` assertion | Constellation.Gate | Gate adopts an SDK pin ≥ `1d52369` and updates the assertion |
| Odoo deleting its shadow transport | IB-Odoo_19 | Odoo adopts `execute()` |
| EIE deleting `app/services/gate_registration.py` | EIE | EIE adopts `register_node()` |
| `cryptography` advisories vs the pyOpenSSL ceiling | Gate_SDK + IB-Odoo_19 dependency owner (#39) | pyOpenSSL forward-pin lands in IB-Odoo_19, then Gate_SDK raises the ceiling |
| Packaging harness building from a temp cwd | Gate_SDK | non-blocking hygiene |
| Migrating legacy tests to the `transport=` seam | Gate_SDK | non-blocking hygiene |

Deferred per the original scope, unchanged: durable Gate idempotency, Gate retry
policy redesign, domain contract models, queue/outbox, generalized service
discovery, `TransportSecurity` hash-envelope redesign, package registry
infrastructure.

Recorded in the session debt ledger: `gate-lineage-reentry-hopcount` (deferred,
with owner and removal trigger).

# Merge Recommendation

**APPROVE.**

The wire contract is unchanged, the full suite is green on two Python versions
and at the declared cryptography lower bound, the built wheel behaves like the
checkout, Gate's suite shows no regression attributable to this branch, and both
consumer shadow-transport removals are proven against the installed package.

Two behavior changes a reviewer should look at deliberately: the network
deadline now derives from the packet header, and outbound signing now precedes
outbound validation. Both are argued above; both are strictly safer.

# Release-Set Recommendation

Merge Gate_SDK first — it is additive to the wire and consumers can adopt at
their own pace. Then, in any order:

1. **Constellation.Gate** — bump the SDK pin past `1d52369` and fix the stale
   hop-count assertion. Required before Gate benefits, and the only thing that
   clears the deferred blocker.
2. **EIE** — replace `app/services/gate_registration.py` with `register_node()`.
   Smallest change, largest deletion.
3. **Odoo** — adopt `execute()` in `plasticos_gate/services/gate_client.py` and
   delete the packet construction, the timeout arithmetic, and
   `classify_transport_failure()`.

No consumer is forced to move: existing call sites keep working.

# Next Straight-Line Move

Enrichment.Inference.Engine, per the stated plan: an ADR pack locking domain
ownership and durable-before-completed semantics, with its contract consuming
these Gate_SDK capabilities — `register_node()` for control-plane registration
and the typed taxonomy for transport failures — rather than inventing another
boundary.

# Machine-Readable Summary

```yaml
repository: Quantum-L9/Gate_SDK
branch: claude/gate-sdk-transport-closure-u2klcf
candidate_head: "6225f75ae185dd6fff98a12c670898a536069392"
pr:
  created: true
  number: 40
  url: "https://github.com/Quantum-L9/Gate_SDK/pull/40"
  head_sha: "6225f75ae185dd6fff98a12c670898a536069392"
architecture:
  transport_authority: Gate_SDK
  domain_payload_opaque: true
  gate_only_egress: true
public_api:
  high_level_execute: true
  low_level_send_to_gate_preserved: true
  caller_manual_root_packet_required: false
deadline:
  one_application_budget: true
  packet_timeout_sdk_owned: true
  network_timeout_sdk_owned: true
retry:
  hidden_application_retries: false
idempotency:
  logical_identity_caller_owned: true
  transport_representation_sdk_owned: true
errors:
  typed_failure_closure: true
registration:
  sdk_owned: true
  owner_metadata_supported: true
  bespoke_http_required_by_eie: false
compatibility:
  odoo: PASS
  constellation_gate: PASS
  eie: PASS
  installed_package: PASS
validation:
  contracts: PASS
  lint: PASS
  mypy: PASS
  tests: PASS
blocking_defects: []
non_blocking_defects:
  - "leftover repo-root build/ shadows the build frontend in tests/packaging (local-only; make clean resolves)"
  - "registration sends metadata.generated_by where EIE's hand-written payload omitted it (accepted by Gate, ignored by ownership resolution)"
  - "legacy gate tests still monkeypatch httpx.AsyncClient globally instead of using the transport= seam"
external_blockers:
  - id: gate-lineage-reentry-hopcount
    repository: Quantum-L9/Constellation.Gate
    test: tests/architecture/test_lineage_reentry.py::test_lineage_is_preserved_across_gate_reentry_and_dispatch
    caused_by_this_branch: false
    fails_identically_on_sdk_main: true
    gate_runtime_affected: false
    cause: "Gate pins Gate_SDK a770e853, predating SDK commit 1d52369 (derive hop reset); the assertion encodes pre-1d52369 behavior"
    removal_trigger: "Gate adopts an SDK pin >= 1d52369 and updates the assertion"
verdict:
  local: GO
  transport_contract: GO
  cross_repo: GO
  merge: APPROVE
next_move: "Enrichment.Inference.Engine ADR pack: lock domain ownership and durable-before-completed semantics, consuming register_node() and the typed taxonomy."
```
