# Routing Policy Specification

## Purpose

All node-originated follow-up traffic must return to Gate so Gate remains the
only hop that may address workers.

## Rules

### Core rule

**All node-originated follow-up traffic must return to Gate.**

### Allowed patterns

```text
client -> gate
node -> gate
gate -> worker
```

Forbidden pattern:

```text
node-a -> node-b
```

No worker or orchestrator may directly target another worker node.

### Required packet semantics

Node-origin packet:

- `provenance.origin_kind == "node"`
- `address.source_node ==` local node
- `address.destination_node == "gate"`
- `provenance.original_source_node ==` local node

Gate dispatch packet:

- `provenance.origin_kind == "gate"`
- `provenance.resolved_by_gate == true`
- `address.source_node == "gate"`
- `address.destination_node ==` resolved worker node

## Invariants

Policy enforcement points:

- SDK Gate client
- Gate ingress validator
- Gate routing policy validator
- architecture tests
