# AGENTS.md — Constellation Node SDK

This file defines the operating rules for coding agents working in this repository.

The repo is protocol-sensitive. Small mistakes can create transport drift, routing-law violations, or silent incompatibility with `constellation-gate`.

Read this file before making changes.

---

## 1. Mission of this repo

`constellation-node-sdk` is the canonical SDK for worker and orchestrator nodes in the Constellation system.

This repo owns:

- the `TransportPacket` contract
- transport hashing and validation
- transport signing and verification
- hop-chain integrity
- Gate-only outbound client
- reusable node runtime
- orchestrator composition helpers
- contract schema and examples

This repo does **not** own:

- Gate action resolution
- Gate registry authority
- worker dispatch authority
- legacy compatibility shims

---

## 2. Non-negotiable architectural laws

### 2.1 Canonical transport only
There is exactly one wire format:

- `TransportPacket`

Do not introduce:
- alternate packet types
- “legacy request” adapters inside core runtime
- dict-first fallback paths inside runtime or Gate client

Any compatibility belongs outside the core SDK.

### 2.2 Gate-only node egress
Nodes must only send follow-up work to `GATE_URL`.

Forbidden:
- peer node URLs in node runtime
- direct node-to-node dispatch helpers
- APIs that accept arbitrary worker endpoints

Allowed:
- `GateClient.execute(...)` — the application surface
- `GateClient.send_to_gate(packet)` — the packet-native primitive

### 2.6 One application surface
A normal application-to-Gate operation must be expressible through one
`GateClient.execute()` call, using business inputs only.

`create_transport_packet()`, `TransportPacket.derive()`, `with_hop()`, and
`send_to_gate()` remain supported protocol primitives for Gate, the SDK
runtime, orchestrators, and protocol tests. They are **not** the recommended
application integration, and examples must not teach them as one.

If an application needs substantial code for packet construction, deadline
synchronization, transport idempotency, Gate HTTP, response validation,
signing, failure classification, or registration HTTP, the default diagnosis is
a Gate_SDK capability gap — not an application-specific requirement.

### 2.7 One deadline
The caller supplies one operation budget. The SDK writes it into
`packet.header.timeout_ms` and derives the real network deadline from it.

The network deadline must never silently exceed the budget the packet
advertises downstream. `config.timeout_seconds` is the *default* budget, not a
second deadline. Do not add a second deadline abstraction.

### 2.8 No hidden execution retry
`execute()` and `send_to_gate()` perform exactly one HTTP request. A failed
execution is returned as a typed failure, never replayed: retrying belongs to
the layer with semantic authority and a stable idempotency key.

Gate registration is the one exemption — control-plane reconciliation with
bounded, visible backoff. Do not extend it to execution.

### 2.9 Typed failure closure
Every failure leaving the Gate client is a `GateClientError` subclass, with the
underlying exception chained and structured context (status code, applied
deadline, direction) preserved.

Do not let `httpx` exceptions, pydantic `ValidationError`, or bare `ValueError`
escape the client. A consumer must never have to import `httpx` or match
substrings against a message to classify a transport failure. Note that httpx
timeout exceptions frequently stringify to `""`, so messages must carry the
exception type rather than relying on `str(exc)`.

### 2.11 Gate-authority worker transport
`constellation_node_sdk.gate_authority` is the only surface that addresses a
worker, and it exists for Constellation.Gate alone.

Its safety is mechanical, not documentary: authority is validated on the
**packet**, so importing the module grants nothing. Never relax that check, and
never re-export the surface from the package root or the `gate` package — an
application that can reach it by accident can be made to route peer-to-peer.

The SDK does not route. It may verify the target it is given; it must never
resolve an action, query a registry, choose or load-balance workers, fail over,
mark a node unhealthy, or apply backpressure. Those are Gate's.

Do not add a general `send_to_url` / `send_to_peer` / `post_packet(url)` surface
under any name. The authority check is what distinguishes this from a peer
client; without it there is no difference.

### 2.12 One transport implementation
Both sides of Gate share one canonical-packet HTTP implementation
(`_packet_http.py`), differing only in the error types they inject. Do not add a
second copy: two implementations drift, and the one that drifts is whichever is
exercised less.

### 2.10 Domain payloads stay opaque
The SDK transports domain payloads. It never interprets, renames, supplements,
or translates them, and no domain schema (`EnrichRequest`, Odoo fields, CRM
identities, provider payloads) belongs in SDK production code. Test fixtures
may be copied from consumers; they stay opaque dictionaries.

### 2.3 Gate is the routing authority
Nodes express intent by `action`.
Gate resolves destination by policy and registry.

Never add code in this repo that:
- resolves `action -> worker URL`
- bypasses Gate routing
- caches peer routing outside Gate

### 2.4 Distinguish semantic change from observational change
Use:
- `derive()` for semantic child packets
- `with_hop()` for observational hop additions

Rule of thumb:
- payload / provenance / action / destination changed → derive child packet
- ingress / dispatch / execution / response observed → append hop

### 2.5 Stable transport hash
`transport_hash` must remain stable across hop additions.

`hop_trace` is excluded from `transport_hash`.

Do not change that unless the entire protocol spec is deliberately revised.

---

## 3. Source-of-truth files

When in doubt, these files define the contract:

- `src/constellation_node_sdk/transport/models.py`
- `src/constellation_node_sdk/transport/packet.py`
- `src/constellation_node_sdk/transport/hashing.py`
- `src/constellation_node_sdk/transport/hop_trace.py`
- `src/constellation_node_sdk/security/validation.py`
- `contracts/transport-packet.schema.json`
- `contracts/TRANSPORT_PACKET_SPEC.md`
- `contracts/ROUTING_POLICY_SPEC.md`

Agents must keep code, tests, and schema aligned.

---

## 4. Change policy

### 4.1 Allowed changes
Safe categories:
- implementation bug fixes
- test additions
- docs improvements
- CI hardening
- performance improvements without contract drift
- clearer error handling
- stronger type safety

### 4.2 High-risk changes
Require extreme care:
- changes to `TransportPacket` fields
- changes to hashing input
- changes to `derive()` semantics
- changes to hop-chain semantics
- changes to validation defaults
- changes to routing provenance rules
- changes to Gate-client policy

If making a high-risk change:
1. update schema
2. update spec docs
3. update tests
4. explain the protocol impact in the PR

### 4.3 Forbidden changes
Do not:
- add peer URL dispatch APIs
- add alternate packet contracts
- weaken validation by default
- hide signature failures silently
- mutate existing hop entries
- make tenant context mutable across derived packets

---

## 5. Code standards

### 5.1 Style
- Python 3.12+
- type annotations required
- prefer explicit, readable code
- no clever one-liners in protocol code
- keep functions single-purpose

### 5.2 Error handling
- fail closed, not open
- transport violations should raise explicit validation/auth/authz errors
- never silently coerce invalid protocol state
- error messages should be useful but not misleading

### 5.3 Immutability preference
Transport structures are treated as immutable protocol data.

Prefer:
- `model_copy(update=...)`
- returning new packet objects

Avoid:
- in-place mutation of packet internals
- hidden mutation inside helpers

---

## 6. Testing rules

Any meaningful change to transport/security/runtime/orchestrator code must update or add tests.

### Required test scope by area

#### Transport changes
Add/update:
- transport packet tests
- hashing tests
- codec tests
- lineage tests
- hop trace tests

#### Security changes
Add/update:
- signing tests
- verification tests
- validation tests
- delegation tests

#### Gate client changes
Add/update:
- Gate policy tests
- Gate client tests
- registration tests if affected
- `tests/gate/test_application_execute.py` — the application surface
- `tests/gate/test_deadline_closure.py` — asserts the timeout httpx *actually* applied
- `tests/gate/test_error_taxonomy.py` — every failure category, reachable and distinct
- `tests/gate/test_consumer_architecture_guard.py` — consumer-facing drift guard
- `tests/contracts/test_release_set_compatibility.py` — the full producer/Gate/worker rail
- `tests/packaging/test_installed_package.py` — the same behavior from the built wheel

#### Gate-authority transport changes
Add/update:
- `tests/gate_authority/test_dispatch_authority.py` — the peer-escape negatives
- `tests/gate_authority/test_dispatch_transport_discipline.py` — one attempt, typed failures, signed rails, connection lifecycle
- `tests/gate_authority/test_worker_dispatch_rail.py` — the real SDK worker round trip and the one-deadline proof
- `tests/gate/test_consumer_architecture_guard.py` — the structural guards

#### Runtime changes
Add/update:
- handler tests
- execution tests
- app tests
- preflight tests

#### Orchestrator changes
Add/update:
- packet builder tests
- retry tests
- merge tests
- step executor tests

If a change crosses boundaries, add integration tests.

---

## 7. Validation workflow before merge

Agents should assume the following must pass:

```bash
python -m pip install -e ".[dev]"
python scripts/validate_contracts.py
python scripts/generate_schema.py
ruff check src tests scripts
mypy src
pytest -q
If schema normalization changes the schema file, commit the updated normalized file.

8. Contract synchronization rules
Schema, code, and docs must not drift.

If changing protocol structure:

update Python models

update schema

update contract docs

update examples

update tests

Never update only one of these.

9. Example and docs rules
Examples must demonstrate the actual intended architecture.

That means:

worker examples target Gate, not peers

orchestrator examples compose via Gate step execution

packet examples are illustrative and clearly marked as such

docs should reinforce Gate-only routing law

Do not ship examples that violate architecture for convenience.

10. Guidance for AI coding agents
10.1 Preferred workflow
inspect contract-sensitive files

make minimal coherent change

update tests in the same pass

run validation commands

only then widen scope

10.2 Avoid drift
Do not invent fields or rename protocol properties casually.
If a name seems inconsistent, verify against:

schema

tests

packet model

docs

10.3 Stabilization over expansion
When the repo is already broad, prefer:

repair passes

test hardening

alignment fixes

over:

adding new abstractions

broad refactors

speculative features

10.4 No hidden shortcuts
Do not:

bypass validation in tests unless the test is explicitly about invalid state

stub protocol behavior in production code

add “temporary” dual-path logic to core transport

11. PR checklist
Before considering a change complete, confirm:

 no violation of Gate-only egress

 no alternate transport path introduced

 schema still matches models

 tests cover changed behavior

 docs/examples remain accurate

 no transport-hash drift from hop-only changes

 no tenant mutation across derived packets

 no direct node-to-node semantics introduced

12. Final principle
This repo is not just a helper library.

It is the protocol boundary that keeps the Constellation architecture coherent.
