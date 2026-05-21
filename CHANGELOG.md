# Changelog

All notable changes to `constellation-node-sdk` are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
