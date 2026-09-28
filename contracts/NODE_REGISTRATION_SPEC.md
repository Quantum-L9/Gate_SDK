# Node Registration Specification

## Purpose

Nodes register with Gate so Gate can resolve actions to healthy runtime instances.

## Registration endpoint

```text
POST /v1/admin/register
```

Payload shape is a top-level JSON object keyed by node name:

```json
{
  "score": {
    "internal_url": "http://score:8000",
    "supported_actions": ["score"],
    "priority_class": "P1",
    "max_concurrent": 25,
    "health_endpoint": "/v1/health",
    "timeout_ms": 15000,
    "metadata": {
      "version": "1.2.3",
      "type": "worker",
      "generated_by": "constellation-node-sdk"
    }
  }
}
```

## Required Fields

- `internal_url`
- `supported_actions`

Optional fields:

- `priority_class`
- `max_concurrent`
- `health_endpoint`
- `timeout_ms`
- `metadata`

### Metadata

`metadata` is a flat mapping of string keys to string values. Gate's registration
schema forbids unknown top-level keys, so anything a node needs to declare
beyond the fields above travels here.

The SDK derives four keys and reserves them: `owner`, `version`, `type`, and
`generated_by`. They are set through `NodeRegistration` fields, not through the
metadata mapping, so two sources can never disagree about the same
registration.

`metadata.owner` — Gate resolves the semantic owner of a canonical action from
`metadata.owner` first, falling back to a recognizable node name. A node
claiming a canonical action whose name Gate cannot map to an owner is
rejected. Set owner explicitly whenever the node name is not itself the owner
name.

`metadata` is control-plane metadata. It is not a domain payload surface, and the
SDK enforces that: non-string values are rejected before the request is built.

### SDK entry points

- `NodeRegistration` — the typed registration, rendered by `to_payload()`
- `register_node(...)` — register from in-process configuration; no spec.yaml needed
- `build_node_registration(spec)` / `register_with_gate(...)` — the spec.yaml path
- `register_from_env(registration=None)` — env-configured; an in-process
  `NodeRegistration` replaces the spec file when given
- `create_node_app(registration=None)` — the default: the runtime owns the
  node's participation (below), so a node needs no registration code at all

Both paths render the identical body.

## Invariants

Registration rules:

- node names are normalized to lowercase
- supported actions must be non-empty, lowercase, and free of duplicates
- internal URL must be absolute
- health endpoint must begin with `/`
- priority class must be one of `P0`, `P1`, `P2`, `P3`
- registration may be rejected if overwrite is false and the node exists
- Gate is authoritative for activation and health state

Retry policy: registration is control-plane reconciliation, not application
execution, so it retries with bounded exponential backoff. A Gate rejection
(400, 401, 403, 409, 422) is a decision and is never retried. Failure is
non-fatal: registration returns a boolean and never raises into node startup.

## Participation lifecycle (L9-PARTICIPATION-01)

`create_node_app()` owns the node's Gate participation (`NodeParticipation`),
so a node never writes its own registration loop or registration-aware
readiness:

| State | Meaning | `/v1/ready` |
|---|---|---|
| `disabled` | `auto_register_with_gate=False`, `GATE_REGISTRATION_ENABLED=false`, or no Gate URL | 200 |
| `not_attempted` | enabled, no attempt completed yet | 503 |
| `registering` | first attempt in flight | 503 |
| `active` | Gate accepted the most recent attempt | 200 |
| `degraded` | the most recent attempt failed (rejected, unreachable, or the registration could not be built) | 503 |

- Startup makes one bounded attempt (the retry policy above), then the node
  re-registers every `GATE_REREGISTRATION_INTERVAL_SECONDS` (default 300; 0
  disables the loop). Gate's registry is in memory; this is what brings a node
  back after a Gate restart without node code.
- A re-registration of an `active` node keeps it `active` until the attempt
  resolves, so readiness does not flap on every interval.
- `GET /v1/health` is liveness: always 200, with the participation status under
  `gate_participation`. Gate's health monitor probes it by status code, so a
  node Gate has not accepted yet is still reachable for the probe.
- `GET /v1/ready` is readiness: 200 only in `active` or `disabled`.
- Shutdown cancels the loop. Gate has no deregistration call.

The SDK asks; Gate decides. A node is `active` only after Gate accepted it.
