# Node Registration Specification

## Purpose

Nodes register with Gate so Gate can resolve actions to healthy runtime instances.

## Registration endpoint

```text
POST /v1/admin/register
Payload shape
Top-level JSON object keyed by node name:

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
Required fields
internal_url

supported_actions

Optional fields
priority_class

max_concurrent

health_endpoint

timeout_ms

metadata

Metadata
metadata is a flat mapping of string keys to string values. Gate's registration
schema forbids unknown top-level keys, so anything a node needs to declare
beyond the fields above travels here.

The SDK derives four keys and reserves them: owner, version, type, and
generated_by. They are set through NodeRegistration fields, not through the
metadata mapping, so two sources can never disagree about the same
registration.

metadata.owner
Gate resolves the semantic owner of a canonical action from metadata.owner
first, falling back to a recognizable node name. A node claiming a canonical
action whose name Gate cannot map to an owner is rejected. Set owner explicitly
whenever the node name is not itself the owner name.

metadata is control-plane metadata. It is not a domain payload surface, and the
SDK enforces that: non-string values are rejected before the request is built.

SDK entry points
NodeRegistration — the typed registration, rendered by to_payload()

register_node(...) — register from in-process configuration; no spec.yaml needed

build_node_registration(spec) / register_with_gate(...) — the spec.yaml path

Both paths render the identical body.

Registration rules
node names are normalized to lowercase

supported actions must be non-empty, lowercase, and free of duplicates

internal URL must be absolute

health endpoint must begin with /

priority class must be one of P0, P1, P2, P3

registration may be rejected if overwrite is false and node exists

Gate is authoritative for activation and health state

Retry policy
Registration is control-plane reconciliation, not application execution, so it
retries with bounded exponential backoff. A Gate rejection (400, 401, 403, 409,
422) is a decision and is never retried. Failure is non-fatal: registration
returns a boolean and never raises into node startup.


```markdown
