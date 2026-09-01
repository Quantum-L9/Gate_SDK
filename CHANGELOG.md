# Changelog

All notable changes to `constellation-node-sdk` are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

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
