# GATE_SDK PR #40 DELTA FINDINGS BRIEF

```yaml
PR:
  number: 40
  url: https://github.com/Quantum-L9/Gate_SDK/pull/40
  branch: claude/gate-sdk-transport-closure-u2klcf
  base: main
  exact_remote_head_sha: e39d311e — docs-only, on top of the code head below
  code_head_sha: bfe6642062a85a720ad8c25e96446d4df1c299ac
  head_note: >
    Every code claim in this brief was measured at bfe6642. The heads after it
    carry this brief and the findings only; a docs commit cannot change a
    transport verdict, and each one re-ran the full gate before pushing.

MAKE PR:
  result: PASS — pushed d7f85d7..bfe6642, PR #40 already open, gate clean
  failed_phase_if_any: none

VERDICT:
  application_transport: GO — PR #40's closure intact, re-asserted structurally
  gate_worker_transport: GO — send_gate_authored_packet closes the last shadow
  transport_contract: GO — transport/ untouched, schema regenerates identical
  deadline: GO — one number reaches packet header, socket, and worker handler
  retry: GO — one attempt, proven by request count on 11 statuses + socket + timeout
  signing: GO — policy, then sign, then validate the signed artifact
  typed_errors: GO — Gate needs no httpx and no RuntimeError flattening
  installability: GO — full rail runs from the built wheel
  Gate_compatibility: GO — Gate's delta branch 339 passed, 0 failed
  merge: APPROVE
  release_set: GO

IMPLEMENTED:
  - GateDispatchTransport.send_gate_authored_packet(packet, target_node, worker_base_url)
    in its own gate_authority namespace, exported from neither the package root
    nor the gate package
  - Packet-level authority validation reusing the worker's own
    validate_execute_ingress_packet, plus reply_to, refused before any I/O
  - SDK-owned /v1/execute endpoint; caller supplies a base URL only
  - Deadline derived from packet.header.timeout_ms; NO timeout parameter
  - Exactly one POST; no retry surface in the config
  - Separate GateDispatchError hierarchy (authority/configuration/security/
    worker connection/timeout/HTTP/response)
  - Response validated as an answer to THIS dispatch: worker identity,
    destination, action, root lineage, causation, tenant, correlation, idempotency
  - Reusable pooled-client lifecycle; the transport never closes a client it did
    not create; per-dispatch deadline beats a pooled default
  - One shared canonical-packet HTTP implementation for both sides of Gate
  - Architecture guards, docs/gate-authority-transport.md, ARCHITECTURE/AGENTS/README

PROVEN:
  - 705 tests pass; 97 added this delta
  - One downstream deadline: root 30000ms, 28000ms elapsed, 25000ms cap ->
    child header 2000ms, socket 2.0s, worker handler 2.0s
  - Peer escape blocked 9 ways, each with zero network requests
  - No retry across 400/401/403/404/409/422/429/500/502/503/504, dead socket,
    read timeout
  - Signed round trip through the real worker runtime; 4 negative cases fail closed
  - Gate's delta branch (4778fee) 339 passed / 0 failed against this candidate
  - Gate's main (545eda4) 180 passed / 1 failed — the pre-existing stale assertion
  - Installed wheel runs the whole rail, deadline included
  - Wire contract unchanged; no dependency touched

GATE WORKER TRANSPORT:
  public_surface: constellation_node_sdk.gate_authority.GateDispatchTransport
  gate_authority_validation: source=gate, destination=target, reply_to=gate,
    resolved_by_gate, route_kind=external_ingress — validated on the PACKET
  arbitrary_application_peer_routing: BLOCKED (importing the module grants nothing)
  network_attempts: 1
  response_validation: canonical packet + integrity + signature + answers-this-dispatch

DEADLINE:
  root_budget: 30000 ms
  simulated_gate_elapsed: 28000 ms
  derived_child_budget: 2000 ms
  actual_socket_budget: 2.0 s
  worker_runtime_budget: 2.0 s
  one_deadline_proven: true

SECURITY:
  gate_packet_signing: PASS
  worker_response_verification: PASS (tampered, unsigned, unknown-key all fail closed)
  peer_escape_negative_tests: PASS (9 rejection paths, 0 requests each)
  dependency_audit_state: unchanged — no dependency added or relaxed by this delta

CONSTELLATION.GATE EFFECT:
  _post_dispatch_packet_deletable: yes, entirely
  direct_httpx_deletable: yes — no httpx import, no response.json(),
    no raise_for_status(), no TransportPacket.model_validate in dispatch.py
  required_consumer_changes: >
    Add timeout_ms=<remaining budget> to the derive() call. Gate already
    computes it in _attempt_timeout_seconds but never writes it into the packet,
    so today it waits 2s on a socket while telling the worker it has 30s. Then
    call send_gate_authored_packet with target.internal_url as worker_base_url,
    catch GateDispatchError subclasses instead of httpx.TransportError, and pass
    its pooled client through.
  exact_candidate_sdk_sha: bfe6642062a85a720ad8c25e96446d4df1c299ac

WIRE CONTRACT:
  schema_changed: false
  transport_hash_changed: false
  derive_semantics_changed: false

TEST EVIDENCE:
  - command: pytest -q
    result: 705 passed
  - command: ruff check / ruff format --check src tests scripts examples
    result: All checks passed / 128 files already formatted
  - command: mypy src
    result: Success, 47 source files
  - command: python scripts/validate_contracts.py
    result: all checks passed
  - command: python scripts/generate_schema.py
    result: byte-identical, no wire drift
  - command: pytest -q tests/packaging (built + installed wheel)
    result: 20 passed
  - command: Gate delta branch suite vs candidate (SDK path + API asserted first)
    result: 339 passed, 0 failed
  - command: Gate main suite vs candidate
    result: 180 passed, 1 failed (pre-existing stale assertion, fixed on the delta branch)
  - command: Gate consumability proof, installed SDK
    result: 10/10 proofs true
  - command: gh check-runs on the published head
    result: 17 success; gate-5-dep-audit red (and its aggregate), red on main too

BLOCKERS:
  - none owned by this PR

NON_BLOCKING:
  - leftover repo-root build/ shadows the build frontend in tests/packaging (local only)
  - registration sends metadata.generated_by where EIE's payload omitted it
  - legacy gate tests still monkeypatch httpx.AsyncClient globally
  - SonarCloud S5332 on the internal_url http fallback (pre-existing, suppressed
    with justification; SonarCloud green)

EXTERNAL:
  - gate-5-dep-audit: 7 cryptography advisories, minimum fix 46.0.5 vs the
    deliberate <45 ceiling, which is exactly pyOpenSSL 24.3.0's declared range.
    Red on main too; this PR touches no dependency manifest. Unblocking requires
    a pyOpenSSL forward-pin in IB-Odoo_19 first. Stood down with one PR comment.
  - CLOSED this delta: "Gate worker transport missing from the SDK"
  - CLOSED by Gate: the stale len(hop_trace)==2 assertion, fixed on
    claude/gate-routing-sdk-transport-4btf4g and verified passing here

SCOPE DRIFT:
  none — two departures from Gate's requested signature, both narrower than
  asked: worker_base_url instead of target_url (the SDK owns the endpoint), and
  no timeout_seconds (it would have preserved the two-deadline split permanently).

NEXT STRAIGHT_LINE_MOVE: >
  Update Constellation.Gate to this exact SDK SHA, derive worker packets with
  the remaining downstream timeout_ms, delete _post_dispatch_packet, and rerun
  the real EIE round trip.
```
