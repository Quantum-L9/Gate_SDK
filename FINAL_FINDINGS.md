# FINAL_FINDINGS — Gate_SDK PR #40

# Executive Verdict

**GO for merge.** Gate_SDK now owns canonical transport on **both sides of
Gate**. An application reaches Gate through one call and cannot reach anything
else; Gate reaches its resolved worker through one call and needs no HTTP code
of its own. Neither capability weakens the other: the surface that addresses a
worker validates Gate's authority on the *packet*, so an application that
imports it still cannot use it.

The wire contract is untouched. `src/constellation_node_sdk/transport/` has no
diff against `main`, and `contracts/transport-packet.schema.json` regenerates
byte-identical.

The external blocker this PR previously carried is **closed**: Constellation.Gate
fixed its stale hop-count assertion on its own branch, and that branch's full
suite passes against this candidate. One pre-existing dependency-audit failure
remains, red on `main` too, and is not this PR's to resolve.

# Repository / Branch / Candidate HEAD

| Field | Value |
|---|---|
| Repository | `Quantum-L9/Gate_SDK` |
| Pull request | [#40](https://github.com/Quantum-L9/Gate_SDK/pull/40) |
| Branch | `claude/gate-sdk-transport-closure-u2klcf` |
| Candidate HEAD | `08413cd17ff9b52bbf4fc3088a63a760372345c9` |
| Base (`origin/main`) | `d09fe58a6cd68ef8aa883896c68badc95f96e090` |
| Package version | `1.0.1` (unchanged) |
| `requires-python` | `>=3.12` (unchanged) |
| cryptography constraint | `>=43.0.0,<45` (unchanged — deliberately not relaxed) |

The delta contract named `6225f75` as the previously-audited candidate. That was
stale by two commits; the branch was at `d7f85d7` when this pass began, verified
against the PR API rather than assumed.

Peer repositories, read-only:

| Repository | Ref | HEAD |
|---|---|---|
| `Quantum-L9/Constellation.Gate` | `main` | `545eda4259121dbce85c084385f68d00632981d7` |
| `Quantum-L9/Constellation.Gate` | `claude/gate-routing-sdk-transport-4btf4g` | `4778fee795c19732b41cf05da070b90dee89ecef` |
| `Quantum-L9/Enrichment.Inference.Engine` | `main` | `cfda45043477bfe4a0f2a8c249ff9be30d1705aa` |
| `cryptoxdog/IB-Odoo_19` | working branch | `ce8ff00a51fffc73119160df99a9bca9af39b6c6` |

No peer repository was edited.

# Existing PR #40 Closure State

Every capability from the first pass survives, re-asserted by the suite:

| Property | State | Guarded by |
|---|---|---|
| `GateClient.execute()` present | yes | `test_one_call_carries_business_inputs_only` |
| Caller must build a packet | no | `test_execute_accepts_no_packet_argument` |
| Application can name a peer | no | `test_gate_client_still_cannot_reach_a_worker` |
| One budget → packet + socket | yes | `test_caller_budget_reaches_both_the_packet_and_the_socket` |
| Hidden application retry | none | `test_execute_performs_exactly_one_request` |
| Typed transport taxonomy | yes | `test_every_gate_error_descends_from_one_base` |
| Registration `metadata.owner` | yes | `test_owner_reaches_gate_as_metadata_owner` |
| Sign before transport validation | yes | `test_outbound_transport_validation_judges_the_signed_packet` |
| Wire contract changed | no | schema regenerates identical |

`GateClient` gained nothing. Its architectural statement — *it never accepts an
arbitrary peer URL* — is unchanged and re-asserted structurally.

# Gate-Authorized Worker Transport API

```python
from constellation_node_sdk.gate_authority import (
    GateDispatchTransport,
    GateDispatchTransportConfig,
)

transport = GateDispatchTransport(
    GateDispatchTransportConfig(local_gate_node="gate"),
    client=pooled_httpx_client,          # optional; reused across dispatches
)

response = await transport.send_gate_authored_packet(
    packet=dispatch_packet,              # Gate derived and hopped it
    target_node=target.node_name,        # Gate resolved it
    worker_base_url=target.internal_url, # Gate's registry knows it
)
```

Signature parameters are exactly `{packet, target_node, worker_base_url}` —
asserted, so a routing or timeout parameter cannot appear unnoticed.

**Two deliberate departures from Gate's requested shape**
(`GATE_SDK_REQUIRED_DELTA.md` asked for `target_url` and `timeout_seconds`):

1. `worker_base_url`, not `target_url`. The SDK appends `/v1/execute` itself. A
   caller-supplied endpoint would make the execution path a registry value, and
   a registry value is what a misconfiguration or an attacker gets to change.
   Paths, queries, and fragments are rejected.
2. **No `timeout_seconds`.** See *Deadline Semantics* — accepting it would have
   preserved the exact two-deadline split this closure removes.

Both departures are narrower than what was asked for, not broader.

# Gate Authority Validation

Refused **before any network call** unless the packet is sourced from the
configured Gate node, addressed to the named `target_node`, replied to Gate,
`provenance.resolved_by_gate`, and `provenance.route_kind == "external_ingress"`.

The bulk of that is not restated. It calls
`runtime.inbound_policy.validate_execute_ingress_packet` — the same function
every SDK-based worker applies on `/v1/execute` — so Gate cannot send a packet
its worker would reject, and there is no second dialect to drift. `reply_to` is
the single addition: a worker does not care where the answer goes, but a
dispatch that does not return to Gate is not a Gate dispatch.

Also validated: non-blank target, and a base URL with a supported scheme, a
host, no path, no query, no fragment.

# Application Peer-Escape Analysis

**Closed.** The guarantee is mechanical rather than documentary: *authority is on
the packet, not the caller*. Importing `gate_authority` grants nothing, because
an application cannot mint a packet that passes.

| Attempt | Result |
|---|---|
| Node-authored packet + worker URL | `GateDispatchAuthorityError`, zero requests |
| Client-authored packet + worker URL | `GateDispatchAuthorityError`, zero requests |
| `resolved_by_gate=False` | rejected |
| `route_kind` missing | rejected |
| `route_kind="gate_relay"` | rejected |
| `source_node != gate` | rejected |
| `reply_to != gate` | rejected |
| destination ≠ named target | rejected |
| Application imports the module and tries anyway | rejected |

Structurally guarded as well: the surface is exported from neither the package
root nor `gate` (asserted, including `hasattr`), no public API anywhere is named
`send_to_url` / `send_to_peer` / `post_packet` / `execute_peer` / `send_to_node`
/ `dispatch_to_url`, `GateClient` has no method taking `url` / `peer_url` /
`worker_url` / `destination_node` / `endpoint`, and the dispatch module's AST
contains no routing construct and no `constellation_gate` import.

# Gate→Worker Deadline Semantics

One number, three places:

```
root budget        30,000 ms
elapsed in Gate    28,000 ms
worker cap         25,000 ms
                   ↓ min(remaining, cap)
child.header.timeout_ms   2,000 ms   ← what the worker is told
actual socket timeout       2.0 s    ← what Gate waits
worker handler budget       2.0 s    ← what the worker enforces
```

Proven by `test_remaining_budget_drives_socket_and_worker_alike`, which reads
the timeout httpx actually applied and instruments the worker runtime's own
`asyncio.wait_for`, rather than asserting the SDK's intent.

### Required Constellation.Gate consumer change

Gate's dispatcher computes the remaining budget correctly
(`_attempt_timeout_seconds`, ADR-GATE-008) but **never writes it into the
packet**. Its `derive(...)` call omits `timeout_ms`, so the child inherits the
parent's full budget: today Gate waits 2s on a socket while telling the worker
it has 30s.

```python
dispatch_base = ingress_observed.derive(
    ...,
    timeout_ms=self._attempt_timeout_seconds(...) * 1000,   # ← currently absent
)
```

This is the single required consumer change. Had the SDK accepted
`timeout_seconds`, that split would have been preserved permanently, which is
why the parameter does not exist.

# Signing / Validation State

Order is **routing policy → sign → transport validation of the signed artifact →
network**. Policy runs first because it is cheap and unconditional: there is no
reason to spend a signature on a packet about to be refused. Validation runs
after signing so it judges the exact bytes sent.

If signing material is configured but incomplete, the dispatch fails with
`GateDispatchConfigurationError` **before** the network. No second crypto
implementation exists: signing and validation call the same
`security.signing` / `security.validation` primitives as the rest of the SDK.

# Worker Response Validation

Returns a validated `TransportPacket`, never a dict or a raw response. Before
returning, the SDK proves the answer belongs to *this* dispatch: worker
identity, destination back to Gate, action, root lineage, causation, tenant,
correlation, and idempotency. A canonical, correctly signed packet from the
wrong worker is still the wrong answer, and is rejected
(`test_a_response_from_the_wrong_worker_is_rejected`).

Integrity failures raise `GateDispatchSecurityError`, not a response error —
collapsing them would invite Gate to treat tampering as a dialect problem and
retry into it. Unknown or missing required signing keys fail closed.

Every response fixture in these tests is produced by the **real**
`execute_transport_packet`, not hand-written. A fixture that agreed only with
the client under test would prove the client consistent with itself and nothing
about whether a real worker accepts what Gate sends.

# Retry State

`worker_network_attempts: 1`.

Proven by request count, not by absence of retry code: no retry on 400, 401,
403, 404, 409, 422, 429, 500, 502, 503, 504, on a dead socket, or on a read
timeout. `GateDispatchTransportConfig` exposes no retry or attempt field.

Gate owns whole-operation replay, because only Gate holds the operation's
idempotency key and its remaining deadline. A hidden retry here would
double-execute a domain operation.

# Typed Worker Failure Surface

```
GateDispatchError
├── GateDispatchAuthorityError      (also ValueError)   not a Gate-authored dispatch
├── GateDispatchConfigurationError  (also ValueError)   transport/URL cannot express it
├── GateDispatchSecurityError       (.direction)        signing / signature / integrity
├── WorkerConnectionError                               worker unreachable; nothing ran
├── WorkerTimeoutError              (also TimeoutError) budget elapsed (.timeout_seconds)
├── WorkerHTTPError                 (.status_code, .is_server_error, .response_text)
└── WorkerResponseError             (also ValueError)   not a canonical answer to this dispatch
```

Deliberately a **separate hierarchy** from `GateClientError`: that one means "the
call *to* Gate failed", and `GateConnectionError` would read as "could not reach
Gate" while actually meaning a worker was down.

`raw_httpx_required_by_gate: false` — proven by a classifier built only from SDK
types, correct across blank-string `ConnectTimeout`, `ConnectError`, 503, 422,
and a non-canonical body. No failure message is empty, including for causes that
stringify to `""`. Every cause is chained.

Gate can stop flattening transport failures into `RuntimeError`, which currently
loses the cause entirely.

# Connection Lifecycle

`reusable_transport_supported: true`.

Gate dispatches continuously, so a client per packet is a TCP handshake per
packet. A pooled `httpx.AsyncClient` can be supplied and is reused across
dispatches; its lifetime stays the caller's, and the transport never closes a
client it did not create. As an async context manager it creates and closes one
of its own. The per-dispatch deadline is applied per request, so a pooled
client's own default timeout cannot widen a dispatch budget
(`test_a_pooled_client_default_cannot_widen_the_packet_deadline`).

This is not a new pooling framework — it is httpx used correctly.

Gate's own finding that its production dispatcher never actually receives its
pooled client is a Gate-side wiring issue; the SDK now makes the correct wiring
expressible and cheap.

# Constellation.Gate Consumability

Verdict: **PASS.** Gate was not edited.

Gate's exact dispatch inputs were reproduced against the installed SDK. Ten
proofs, all true: `_post_dispatch_packet` replaced by one call; no httpx
serialization; no `response.json()` / `raise_for_status()` /
`TransportPacket.model_validate` needed; endpoint built by the SDK; Gate's
pooled client reused and left open; one attempt per dispatch; the 30000→2000ms
budget derivation; socket equals header; worker handler equals both; and
classification without httpx.

### Exact deletion / substitution plan

| Gate code today | After adoption |
|---|---|
| `_post_dispatch_packet()` (both branches, ~30 lines) | deleted |
| `import httpx` in `routing/dispatch.py` | deleted |
| `response.raise_for_status()` / `response.json()` / JSON-object check | deleted |
| `TransportPacket.model_validate(response)` | deleted (SDK returns a packet) |
| `except httpx.TransportError` → `RuntimeError` | `except GateDispatchError` subclasses |
| `f"{target.internal_url}/v1/execute"` | pass `target.internal_url` as `worker_base_url` |
| `timeout_seconds=self._attempt_timeout_seconds(...)` passed to the POST | **moved into `derive(timeout_ms=...)`** |
| `WORKER_TRANSPORT_ADAPTER` guard scope | shrinks to empty |

What Gate keeps, correctly: target resolution, deadline arithmetic, per-node
concurrency permits, registry active counts, and health marking. The SDK reports
typed outcomes; Gate decides what they mean.

### Gate suites run against this candidate

| Gate ref | Result |
|---|---|
| `main` (`545eda4`) | 180 passed, **1 failed** — the pre-existing stale hop-count assertion |
| `claude/gate-routing-sdk-transport-4btf4g` (`4778fee`) | **339 passed, 0 failed** |

Before each run the imported `constellation_node_sdk` path and the presence of
`send_gate_authored_packet` were printed and asserted, because Gate's own
`pip install -e ".[dev]"` silently reinstalls its pinned SDK — a trap that
invalidated an earlier run in the first pass.

**The previously deferred external blocker is closed.** Gate's delta branch
changes `assert len(posted_packet.hop_trace) == 2` to `== 1` with a comment
matching the original diagnosis. It was closed by Gate, not by this PR.

# Real SDK Worker Round Trip

The full rail runs against the genuine worker runtime:

```
root packet → Gate ingress validation → ingress observation hop
→ Gate derive (timeout_ms = remaining budget) → dispatch hop
→ send_gate_authored_packet() → real /v1/execute ingress policy
→ real execute_transport_packet() → registered handler
→ canonical worker response → SDK response validation → Gate
```

Asserted: payload preserved byte-for-byte, tenant preserved, idempotency
preserved, correlation preserved, root lineage preserved, new child packet id,
correct causation, every child hop bound to the child's own packet id, worker
response hop valid, response source is the dispatched worker, destination is
Gate, one network request, one deadline.

# Signed Round Trip

Gate signs the dispatch, the worker requires a signature, the worker signs its
response, and Gate verifies it — end to end through the real runtime.

Negative cases fail closed: tampered response → `GateDispatchSecurityError`
(`direction="inbound"`); unsigned response when signatures are required →
security error; response signed by an unknown key → security error; incomplete
Gate signing material → configuration error with **zero** requests.

# Installed-Package Evidence

Wheel built with the project's own `pyproject.toml`, installed into an empty
directory, with the repo `src/` and any editable finder stripped from the child
interpreter, which asserts in-child that the package resolves under the install
directory.

From the wheel: the `gate_authority` namespace ships with all ten exports and
`send_gate_authored_packet`; it is absent from both application namespaces
(`__all__` and `hasattr`); the full Gate→worker rail runs through the real
worker runtime with `packet_timeout_ms == 2000`, socket `2.0`, handler `[2.0]`,
one attempt, correct response routing, idempotency preserved; and a
node-authored packet with a worker URL is refused with zero requests.

# Wire Contract Diff

```
schema_changed:           false
transport_hash_changed:   false
derive_semantics_changed: false
```

`git diff main..HEAD -- src/constellation_node_sdk/transport/` is empty.
`python scripts/generate_schema.py` produces no diff. This was an abstraction
closure over existing primitives; no wire revision was smuggled in.

# Security State

No security default was weakened. Signing, verification, hop verification, and
validation defaults are unchanged on both surfaces, and the new surface fails
closed on every unresolvable key.

The new authority check is itself a security control: it is what distinguishes a
Gate transport from a peer client, and it is asserted to exist and to be wired
into the send path, so a future edit that removes it fails the suite rather than
silently opening node-to-node routing.

Domain payloads remain opaque. The existing domain-neutrality guard `rglob`s the
whole source tree, so all four new modules are scanned automatically — verified,
not assumed.

# Dependency Audit State

```
pr_owned_security_findings:      none
preexisting_security_findings:   gate-5-dep-audit (cryptography advisories)
external_dependency_blockers:    the pyOpenSSL / Odoo.sh coordination
```

This delta adds no dependency and introduces no new finding. `git diff
main..HEAD -- pyproject.toml requirements*.txt` is empty.

The standing failure: 7 advisories in `cryptography 44.0.3`, minimum fix
`46.0.5`, against a deliberate `<45` ceiling. Verified against
`IB-Odoo_19/requirements.txt` rather than a commit message — `<45` is exactly
pyOpenSSL 24.3.0's declared range (cryptography 41.0.5–44.x), and exceeding it
crashes the entire Odoo.sh registry on restart. Unblocking order is fixed:
pyOpenSSL must be forward-pinned in IB-Odoo_19 first. Not relaxed, not
suppressed, not touched.

# Remaining Blocking Defects

None in Gate_SDK.

`gate-5-dep-audit` is red on the PR and on `main`; see *Dependency Audit State*
and *External Blockers*. Stood down with one comment on PR #40.

# Remaining Non-Blocking Defects

1. **Leftover repo-root `build/` shadows the `build` frontend** in
   `tests/packaging`, producing a confusing "wheel build failed" locally. CI
   runs on a clean checkout; `make clean` resolves it. The fix belongs in the
   packaging harness (build from a temp cwd).
2. **`metadata.generated_by`** is sent where EIE's hand-written payload omitted
   it. Accepted by Gate, ignored by ownership resolution.
3. **Legacy gate tests monkeypatch `httpx.AsyncClient` globally** rather than
   using the `transport=` seam. New suites use the seam.
4. **SonarCloud S5332** on the `internal_url` `http://` fallback — pre-existing
   on `main`, relocated by the earlier refactor, suppressed with stated
   justification. SonarCloud is green.

# External Blockers

| Blocker | State |
|---|---|
| Gate worker transport missing from the SDK | **CLOSED by this delta** |
| Gate's stale `len(hop_trace) == 2` assertion | **CLOSED by Gate** on `claude/gate-routing-sdk-transport-4btf4g` |
| `cryptography` advisories vs the pyOpenSSL ceiling | **OPEN** — owned by the pin's author (#39) plus IB-Odoo_19; unblocking requires a pyOpenSSL forward-pin first |

# Scope Drift Audit

No drift. Judgment calls, each defended above: the `worker_base_url` /
`target_url` departure, the absent `timeout_seconds`, the separate error
hierarchy, the private shared HTTP module, and the keyword-only `client` /
`transport` seams.

Explicitly not done, per scope: no arbitrary peer client for applications, no
domain translation, no worker selection, no hidden retries, no worker failover,
no health mutation, no concurrency or backpressure policy, no Odoo- or
EIE-specific code, no dependency change, no schema change, and no edits to any
peer repository.

# Merge Recommendation

**APPROVE.**

Both sides of Gate are closed, the peer-escape guarantee is stronger than before
(now structurally guarded), the wire contract is untouched, the full suite is
green, the built wheel proves the capability, and Gate's own delta branch passes
completely against this candidate.

A reviewer should look deliberately at two things: the authority check in
`gate_authority/dispatch.py::_assert_gate_authored` — it is the entire safety
argument for the surface existing — and the absence of a timeout parameter,
which is a deliberate refusal of what Gate asked for.

# Release-Set Recommendation

1. **Merge Gate_SDK #40.** Additive to the wire; consumers adopt at their own pace.
2. **Constellation.Gate** — pin to this SDK candidate, add `timeout_ms=` to the
   `derive` call, replace `_post_dispatch_packet` with
   `send_gate_authored_packet`, consume the typed errors, and wire its pooled
   client through. Its delta branch already passes against this SDK.
3. **EIE** — replace `app/services/gate_registration.py` with `register_node()`.
4. **Odoo** — adopt `execute()` and delete its shadow transport.

# Next Straight-Line Move

Pin Constellation.Gate to this exact SDK candidate, derive each worker child
with the remaining downstream `timeout_ms`, delete `_post_dispatch_packet`, and
execute the real Gate→EIE runtime proof.

# Machine-Readable Summary

```yaml
repository: Quantum-L9/Gate_SDK
pr: 40
branch: claude/gate-sdk-transport-closure-u2klcf
candidate_head: "08413cd17ff9b52bbf4fc3088a63a760372345c9"
remote_pr_head: "PENDING_PUSH"
application_transport:
  high_level_execute: true
  caller_manual_packet_required: false
  arbitrary_peer_routing: false
gate_worker_transport:
  present: true
  api: "send_gate_authored_packet"
  selects_worker: false
  accepts_gate_resolved_worker: true
  gate_authored_packet_required: true
  fixed_execute_endpoint: true
  caller_manual_http: false
authority:
  source_gate_required: true
  destination_matches_target: true
  reply_to_gate_required: true
  resolved_by_gate_required: true
  route_kind_external_ingress_required: true
deadline:
  separate_socket_budget_parameter: false
  packet_header_drives_network_timeout: true
  remaining_budget_worker_proof: PASS
  one_downstream_deadline: true
retry:
  hidden_application_retry: false
  hidden_worker_dispatch_retry: false
  worker_network_attempts: 1
security:
  outbound_gate_dispatch_signing: PASS
  signed_artifact_validation: PASS
  worker_response_integrity: PASS
  worker_response_signature: PASS
  application_peer_escape: BLOCKED
errors:
  raw_httpx_required_by_gate: false
  typed_worker_transport_failures: true
connection:
  reusable_transport_supported: true
wire_contract:
  schema_changed: false
  transport_hash_changed: false
  derive_semantics_changed: false
compatibility:
  installed_package: PASS
  constellation_gate: PASS
  sdk_worker_runtime: PASS
  signed_round_trip: PASS
validation:
  tests: PASS
  lint: PASS
  format: PASS
  mypy: PASS
  contracts: PASS
  make_pr: PENDING
blocking_defects: []
non_blocking_defects:
  - "leftover repo-root build/ shadows the build frontend in tests/packaging (local-only)"
  - "registration sends metadata.generated_by where EIE's hand-written payload omitted it"
  - "legacy gate tests monkeypatch httpx.AsyncClient globally instead of using the transport= seam"
  - "SonarCloud S5332 on the internal_url http fallback, pre-existing on main, suppressed with justification"
external_blockers:
  - id: gate-sdk-dep-audit-cryptography
    repository: Quantum-L9/Gate_SDK + cryptoxdog/IB-Odoo_19
    caused_by_this_pr: false
    fails_identically_on_main: true
    cause: "7 advisories in cryptography 44.0.3; minimum fix 46.0.5 exceeds the <45 ceiling, which is exactly pyOpenSSL 24.3.0's declared range"
    removal_trigger: "pyOpenSSL forward-pinned in IB-Odoo_19, then the Gate_SDK ceiling raised"
closed_blockers:
  - id: gate-worker-transport-missing
    closed_by: "this delta"
  - id: gate-lineage-reentry-hopcount
    closed_by: "Constellation.Gate branch claude/gate-routing-sdk-transport-4btf4g (4778fee)"
verdict:
  local: GO
  transport_contract: GO
  gate_worker_closure: GO
  merge: APPROVE
  release_set: GO
next_move: >
  Pin Constellation.Gate to this exact SDK candidate, derive each worker child
  with the remaining downstream timeout_ms, replace its manual worker HTTP with
  send_gate_authored_packet, then execute the real Gate→EIE runtime proof.
```
