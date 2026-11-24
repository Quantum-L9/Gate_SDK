# Changelog

All notable changes to `constellation-node-sdk` are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.3.4] — 2026-11-24

### Fixed
- **A peer's verifying key is bound by the key, never by the node's own
  algorithm (D-1 regression in 1.3.3).** 1.3.3 bound every verifying key to the
  verifying node's own `signing_algorithm`, so a node signing hmac-sha256 refused
  Gate's ed25519 signatures (GATE-KEY-01). Each key id is still bound to exactly
  one algorithm, now taken from a per-key declaration (`verifying_key_algorithms`
  on `NodeRuntimeConfig`, `GateClientConfig` and `GateDispatchTransportConfig`;
  env `L9_VERIFYING_KEY_ALGORITHMS_JSON`) or, where unambiguous, from the
  material: a PEM Ed25519 public key is ed25519, raw material that is not 32
  bytes is an HMAC secret. An undeclared 32-byte raw key is ambiguous and is
  refused when a signature is required. The node's own algorithm binds only its
  own key id. A declaration for an unknown key id, an unsupported algorithm, or
  one contradicting the key material is a configuration error.
  `resolve_verifying_key` still returns raw material; verification uses the new
  `bound_key_resolver()`.

### Changed
- **Verifying-key resolution lives in one place.** The per-key declaration and
  material-inference logic added in the D-1 regression fix is a single shared
  mixin (`transport/key_binding.py`) used by the node runtime, `GateClient` and the
  Gate dispatch transport, instead of three copies. No behaviour change; the
  configuration surface and its validation are identical.
## [1.3.3] — 2026-11-24

### Security
- **A signing key id is bound to exactly one signature algorithm (D-1).**
  `verify_transport_packet_signature` used to take its algorithm from the
  packet's own `security.signature_algorithm`, so a packet declaring
  `hmac-sha256` under an ed25519 key id was verified with that key's *public*
  bytes as the HMAC secret: anyone could forge any ed25519 participant.
  Verification now accepts a binding — `key_algorithm=` for raw material, or a
  `VerifyingKey(algorithm, material)` returned by the resolver — and refuses a
  declared algorithm that differs from it before any cryptographic check. The
  same binding covers hop signatures (`hop_key_algorithm=`). `GateClient`, the
  Gate dispatch transport and the worker runtime bind every key they resolve to
  their configured `signing_algorithm`. A resolver returning unbound raw
  material with no `key_algorithm` keeps its previous behaviour; callers that
  verify ed25519 participants must pass a binding.

### Fixed
- **An ed25519 `GateClient` can send (D-2).** `GateClientConfig` and
  `GateDispatchTransportConfig` resolved their own key id to `signing_key`,
  which under ed25519 is the private key, so outbound validation of the packet
  just signed always failed with `invalid ed25519 transport signature`. The own
  key id now resolves to the public key derived from the private key; HMAC
  still resolves to the shared secret.
- **One ed25519 env layout for client and runtime (D-3).** With
  `L9_SIGNING_ALGORITHM=ed25519`, `get_gate_client_config_from_env` reads the
  private key from `L9_SIGNING_PRIVATE_KEY`, the variable the worker runtime
  reads, and refuses `L9_SIGNING_KEY`/`L9_SIGNING_SECRET` as the runtime does.
  HMAC and unset-algorithm configurations read `L9_SIGNING_KEY` as before.

### Changed
- **make-v2 repository execution.** `.l9/repo-workflow.json` declares the
  `make-v2` facade (phases `setup validate check test-fast test`) and
  `Makefile` is regenerated from l9-ci-core. `Repo.mk` adds `repo-test-fast`,
  a bounded literal pytest selection of every suite that exercises `src/`
  (all package unit tests plus the contract, lock and release-identity
  checks); `tests/packaging` and `tests/integration` stay out of it.
  `make test` remains the whole suite.
- **Pull-request CI runs `make test-fast`.** `ci.yml` gate 4 runs the bounded
  selection. `coverage.yml` keeps an 80% floor on pull requests, applied to the
  changed lines with `diff-cover` against the PR base, and the bounded run
  itself must reach 90% (measured 90.9% on adoption). The whole suite with the
  whole-repository `--cov-fail-under=80` now runs nightly and on
  `workflow_dispatch` only. `diff-cover` gets its own hash lock,
  `requirements/coverage.lock`, compiled against `dev.lock`.

### Changed
- **CI installs are hash-locked** (issue #53, SonarCloud `githubactions:S8541`
  / `S8544`, 62 findings on main). Every workflow installs only from
  `requirements/*.lock` with `--require-hashes --only-binary :all:`: `build`
  (setuptools/wheel/build), `runtime`, `dev` (runtime + `[dev]`), and one lock
  per tool (`audit`, `precommit`, `twine`, `sbom`) compiled against the lock it
  shares an environment with, so shared packages carry one version. The
  package installs offline (`--no-index --no-deps --no-build-isolation -e .`)
  and every install step ends with `pip check`. `pip install --upgrade pip` is
  gone; jobs use the interpreter's bundled pip.
- **Builds use the locked backend.** CI and release builds run
  `python -m build --no-isolation`, so published artifacts are no longer built
  by an unhashed setuptools fetched at release time. The wheel and sdist are
  content-identical to the isolated build.
- **The release SBOM describes the built wheel.** Gate 5 installs the runtime
  lock plus the wheel gate 4 produced, instead of an editable install, which
  would have required the build toolchain and listed it in the SBOM.
- **A prepared release is validated before its tag exists.** The release-identity
  validator treated the absence of the exact tag on a full clone as a disagreement,
  which no release-preparation pull request can satisfy: the tag cannot exist before
  the merge that creates it. The ledger now states `github_release_status`
  (`pending` while prepared, `created` once published); a `pending` ledger defers the
  tag-object and channel checks to the release gate, which still requires the tag,
  HEAD == that object, and an ancestor channel.
- `scripts/lock_requirements.py` regenerates every lock (scoped refresh;
  `LOCK_UPGRADE_ALL=1` for a full one). `scripts/validate_locks.py` (gate 2 and
  release gate 2) fails on a stale or disagreeing lock and on any unlocked
  `pip install` line in a workflow. `pre-commit`'s version now lives only in
  `requirements/precommit.in`.

## [1.3.2] — 2026-11-01

### Added
- **W3C trace-context utility** — `TraceContext`, strict parsing/injection,
  and `create_root_trace_context()` are public SDK APIs. They preserve
  canonical `TransportPacket` bytes, hashes, lineage, routing, and deadlines;
  telemetry is never identity or routing authority.
- **Gate-authority worker propagation** —
  `GateDispatchTransport.send_gate_authored_packet(..., trace_context=...)`
  accepts only a validated typed context and sends only `traceparent` plus an
  optional `tracestate` on the existing Gate-owned worker rail. It excludes
  baggage and arbitrary headers, adds no retry or deadline surface, and keeps
  the worker transport outside application namespaces.

## [1.2.0] — 2026-09-30

Participation closure (L9-PARTICIPATION-01): a node or consumer joins L9 with
the SDK and its security configuration alone. Harvested from the machinery
EIE, CEG and IB-Odoo_19 each wrote for themselves, and proven on CEG's
cross-repo Docker rail. Requires Constellation.Gate with `POST /v1/admission`
for `activate()`.

### Added
- **runtime/participation.py** — `NodeParticipation`, `ParticipationState`, `ParticipationStatus`. Registers at startup, re-registers every `GATE_REREGISTRATION_INTERVAL_SECONDS` (default 300, 0 disables) so a node recovers after Gate restarts and forgets it, and reports `disabled`/`not_attempted`/`registering`/`active`/`degraded`. Never raises into the node.
- **`GET /v1/ready`** on `create_node_app()` — 200 only while Gate has accepted the node (or participation is disabled), 503 otherwise.
- **`create_node_app(registration=...)`** and **`register_from_env(registration)`** — an in-process `NodeRegistration` instead of a spec file.
- **`GateClient.activate(required_actions=...)`** / **`activate_consumer(config, ...)`** — signed admission probe (`gate.admission`) to `POST /v1/admission`; returns Gate's signed answer as a typed `ConsumerAccessReceipt` (key id, scope, granted actions). `GateClientConfig.admission_problems()` reports an unprovable identity or an unverifiable receipt as configuration before any request; the receipt is always verified against a Gate key, never the consumer's own.
- **`GateAuthorizationError`** (401/403), **`GateHTTPError.code`** (Gate's error code, e.g. `action_not_permitted`), and **`GateClientError.retryable`** on every client error.
- **`get_gate_client_config_from_env(**overrides)`** — explicit values win over the environment.
- **examples/minimal_node/** — a node with zero Gate integration code (the L9-PARTICIPATION-01 acid test).

### Changed
- With `auto_register_with_gate=True` (the default) and a Gate URL, readiness now follows registration: the node is not ready until Gate accepts it, and becomes not ready again if a re-registration fails. `GET /v1/health` stays 200 (liveness) and adds `gate_participation`. `auto_register_with_gate=False` is unchanged apart from reporting `disabled`.
- `register_from_env()` honours `GATE_REGISTRATION_ENABLED=false` before requiring `GATE_URL`.
- A registration whose `node_name` differs from the runtime's `node_name` is refused locally and reported `degraded` (Gate would route it to a node that rejects the packets).
- `get_gate_client_config_from_env(**overrides)` never parses an environment variable whose field is overridden.

---

## [1.1.0] — 2026-09-02

Release identity for the coordinated IB-Odoo_19 → Gate_SDK → Constellation.Gate
→ Enrichment.Inference.Engine set. The public surface below shipped on `main`
across PRs #40, #42 and #43 without a version bump, so every consumer pinned a
different pull-request commit and no pin agreed with the release ledger. This
release names one immutable commit for all three consumers
(`contracts/RELEASE_IDENTITY_LEDGER.json`).

### Added
- **gate_authority/** — `GateDispatchTransport.send_gate_authored_packet()`, the Gate→worker transport. Closes the last transport shadow: Constellation.Gate hand-rolled the POST, status check, JSON decode, and `TransportPacket.model_validate`, and flattened every failure into `RuntimeError`. It lives in its own namespace, exported from neither the package root nor `gate`, and is safe to exist because authority is validated on the **packet** — sourced from Gate, replied to Gate, addressed to the named target, `resolved_by_gate`, `route_kind=external_ingress` — not on the caller. Importing it grants nothing; an application cannot mint a packet that passes. The check calls the worker's own `validate_execute_ingress_packet` rather than restating it. The SDK does not route: target and base URL come from Gate, and it never resolves actions, queries a registry, load-balances, fails over, or marks health.
- **gate_authority/errors.py** — `GateDispatchError` and its subclasses (`GateDispatchAuthorityError`, `GateDispatchConfigurationError`, `GateDispatchSecurityError`, `WorkerConnectionError`, `WorkerTimeoutError`, `WorkerHTTPError`, `WorkerResponseError`). A separate hierarchy from `GateClientError` on purpose: `GateConnectionError` would read as "could not reach Gate" while meaning a worker was down.
- **_packet_http.py** — the single canonical-packet HTTP implementation now shared by both sides of Gate (one POST, timeout/connection translation, status mapping, JSON decode), parameterized by the error types each caller injects. A guard asserts it is defined exactly once.

### Added (PR #40)
- **gate/client.py** — `GateClient.execute()`, the application execution surface. Takes business inputs only (`action`, `payload`, `tenant`, `idempotency_key`, `timeout_ms`, `correlation_id`, `trace_id`, plus security/governance metadata) and owns the whole transport lifecycle. It exposes no destination parameter: intent is the action, Gate resolves ownership. It is a closure over `send_to_gate()`, not a second transport implementation.
- **gate/errors.py** — Full transport failure taxonomy: `GateConfigurationError`, `GateSecurityError` (with `.direction`), `GateConnectionError`, `GateTimeoutError` (with `.timeout_seconds`), `GateHTTPError` (with `.status_code`, `.response_text`, `.is_client_error`, `.is_server_error`), alongside the existing `GatePolicyError`, `GateResponseError`, and `GateRegistrationError`. Every failure leaving the client is a `GateClientError` subclass with the cause chained, so consumers classify by type rather than by matching substrings against `httpx` exception text.
- **gate/config.py** — `max_timeout_ms` (opt-in hard ceiling on any operation budget, default `None`) and `transport_margin_ms` (explicit slice of the budget reserved so the SDK raises a typed timeout before the caller's own deadline, default `0` — nothing is reserved unless configured). Env: `GATE_CLIENT_MAX_TIMEOUT_MS`, `GATE_CLIENT_TRANSPORT_MARGIN_MS`.
- **gate/registration.py** — `NodeRegistration` model carrying `owner` and constrained `Mapping[str, str]` metadata, plus `register_node()` for registering from in-process configuration. `metadata.owner` is what Gate reads to resolve a canonical action's semantic owner and the SDK previously could not emit it at all; `register_node()` removes the `spec.yaml`-on-disk requirement. `build_node_registration()` exposes the typed form of the spec path; both paths render an identical body. The SDK-derived metadata keys (`owner`, `version`, `type`, `generated_by`) are reserved and rejected if supplied through `metadata`.
- **gate/client.py** — Keyword-only, defaulted `transport` argument on `GateClient.__init__`, an SDK-internal seam for exercising real client behavior (applied deadlines, attempt counts) without a network. It carries no URL and cannot reach a peer node.
- **tests** — `tests/contracts/test_release_set_compatibility.py` exercises the full rail (root packet → Gate ingress → observation hop → Gate derive → worker runtime → response → originating client) against a Gate stand-in that mirrors Constellation.Gate's own dispatch sequence. Also adds application-surface, deadline, taxonomy, registration, and consumer-architecture-drift suites, and extends the installed-wheel proof to cover the closure surface.
- **examples/application_client/** — The complete application-side integration, with no packet construction, no destination, no `httpx` import, and no failure-string matching.

### Changed
- **gate/client.py** — The network deadline is now derived from `packet.header.timeout_ms` rather than `config.timeout_seconds`, so the wire wait can never silently outlive the budget the packet advertises downstream. `timeout_seconds` becomes the *default operation budget* for `execute()`. For a caller whose packet budget and client timeout already agreed — including every consumer that computed one from the other — behavior is unchanged; for one where they disagreed, the packet's advertised budget now wins.
- **gate/policy.py** — Outbound routing rejections raise `GatePolicyError` instead of bare `ValueError`. `GatePolicyError`, `GateResponseError`, and `GateConfigurationError` also subclass `ValueError`, and `GateTimeoutError` also subclasses `TimeoutError`, so callers written against the previous behavior keep working.

### Fixed
- **gate/client.py** — `send_to_gate()` signed the packet *after* validating it, so outbound validation judged a different artifact than the one sent, and `require_signature=True` rejected every packet for a missing signature it was about to add — making self-signed traffic impossible. Signing now precedes validation.
- **gate/config.py** — `get_gate_client_config_from_env()` now reads `L9_VERIFYING_KEYS_JSON` into `GateClientConfig.verifying_keys` (same env contract as the worker runtime). It previously hard-coded an empty map while still setting `verify_response_signatures` from `L9_REQUIRE_SIGNATURE`, so a node that required signatures could only resolve its *own* key id — and Gate signs the responses it authors with Gate's key id. Every env-configured node therefore rejected every signed Gate response with `GateSecurityError: no verifying key available for transport signature verification`. Found by the EIE ↔ Gate ↔ CEG real-process seam E2E (2026-09-02); malformed JSON now fails fast instead of silently disabling verification.

### Unchanged
- The wire contract. `contracts/transport-packet.schema.json` regenerates byte-identical; `TransportPacket`, hashing, `derive()`, and hop semantics are untouched.

---

## [1.0.1] — 2026-05-20

### Fixed
- **runtime/errors.py** — `raise_http_exception` annotated `-> NoReturn`; mypy now correctly understands the call site never returns, eliminating the need for dead return sentinels in callers.
- **runtime/app.py** — Removed dead `return JSONResponse(content={})` sentinel after `raise_http_exception()` call in `/v1/execute` handler. Unreachable code eliminated.
- **runtime/app.py** — `_key_material_from_config` return type narrowed from `tuple[bytes | str | None, str | None]` to `tuple[str | None, str | None]` (bytes were never actually returned).
- **runtime/app.py** — Added `AsyncGenerator[None, None]` return type to `lifespan` context manager.
- **runtime/app.py** — Added explicit return types to `metrics()` (`-> Response`) and `execute()` (`-> JSONResponse`) route handlers.
- **runtime/config.py** — Default `host` changed from `0.0.0.0` to `127.0.0.1`. Containers must explicitly set `HOST=0.0.0.0` via environment variable. Prevents accidental public interface binding in bare-metal and dev environments.
- **runtime/config.py** — `validate_security_profile` return type annotation updated from `"NodeRuntimeConfig"` (string forward ref) to `NodeRuntimeConfig` (direct, `from __future__ import annotations` handles forward resolution).
- **observability.py** — Updated `pythonjsonlogger` import path from `pythonjsonlogger.jsonlogger.JsonFormatter` to `pythonjsonlogger.json.JsonFormatter` (compatibility with python-json-logger >=3.0).
- **security/signing.py** — `TransportAuthenticationError` import moved from `security.verification` to canonical source `transport.errors`.
- **orchestrator/state.py** — String forward references in return types replaced with direct type references.
- **transport/models.py** — String forward references in `model_validator` return types replaced with direct type references.
- **transport/packet.py** — String forward references in return types replaced with direct type references.
- **tests** — All `action=` fixtures updated to comply with `^[a-z0-9][a-z0-9-]{0,63}$` regex (`workflow.execute` → `workflow-execute`, `full_pipeline` → `full-pipeline`).
- **tests/runtime/test_preflight.py** — `model_copy(update={...})` replaced with direct `NodeRuntimeConfig(**base)` construction to correctly trigger Pydantic v2 `model_validator`.
- **tests/security/test_validation.py** — Bare `pytest.raises(Exception)` replaced with precise transport error types (`TransportAuthorizationError`, `TransportAuthenticationError`, `TransportValidationError`).
- **tests/transport/test_hop_trace.py** — `pytest.raises(Exception)` replaced with `pytest.raises(TransportIntegrityError)`.
- **tests/transport/test_lineage.py** — `pytest.raises(Exception)` replaced with `pytest.raises(TransportValidationError)`.

### Added
- **pyproject.toml** — `types-PyYAML>=6.0.12` added to `[dev]` dependencies for mypy YAML stub support.

### Security
- **runtime/config.py** — `host` default changed to `127.0.0.1` (SEC-CONFIG-HOST-SDK). All consumer nodes inherit the safe-by-default bind address. Containers override via `HOST=0.0.0.0` env var.

---

## [1.0.0] — 2026-05-01

### Added
- Initial release of `constellation-node-sdk`.
- `TransportPacket` — immutable, hash-verified, lineage-tracked inter-node protocol primitive.
- `Gate` client with `TransportPacket` signing and verification.
- `create_node_app()` — ASGI factory for L9 constellation nodes.
- `NodeRuntimeConfig` — Pydantic v2 frozen config model with full env-var binding.
- `security/` — HMAC-SHA256 and Ed25519 signing, verification, and delegation chain validation.
- `orchestrator/` — Multi-step workflow orchestration with retry policy and state tracking.
- `transport/` — Hop trace, lineage, hashing, provenance, tenant isolation, and codec primitives.
- `contracts/` — JSON Schema contract for `TransportPacket` with `validate_contracts.py` script.
