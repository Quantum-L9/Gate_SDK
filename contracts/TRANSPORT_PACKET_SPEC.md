# TransportPacket Specification

## Purpose

`TransportPacket` is the canonical transport unit for Constellation.

All ingress, inter-node routing, orchestration, and responses use the same packet type.

## Fields

Canonical fields live in `contracts/transport-packet.schema.json`. The sections
below document hashes, signatures, lineage, provenance, and hop trace.

## Schema

Canonical JSON Schema: `contracts/transport-packet.schema.json`.

## Invariants

### Canonical transport
- only `TransportPacket` is supported
- no legacy dict coercion inside runtime or Gate
- packet validation happens before execution

### Semantic child packets
Use `derive()` when:
- action changes
- payload changes
- destination changes
- provenance changes
- workflow steps create new semantic work

`derive()` mints a new `packet_id` and **resets `hop_trace`**. Parentage
is recorded in `lineage` (`parent_id` / `root_id` / `generation`). Do not
carry parent hops onto the child — hop entries are bound to the current
packet's `packet_id` and `transport_hash`.

### Observational hops
Use `with_hop()` when:
- packet enters Gate
- Gate dispatches work
- worker begins execution
- worker returns response

### Routing rules
- node-origin packets must target `gate`
- Gate dispatch packets may target workers
- workers must not know peer URLs

## Hashes

### `payload_hash`
Canonical SHA-256 hash of the `payload`.

### `transport_hash`
Canonical SHA-256 hash of the stable packet core:

- header
- address
- tenant
- payload
- governance
- provenance
- delegation_chain
- lineage
- attachments
- payload_hash

`hop_trace` is intentionally excluded.

Datetime fields in the hash material are always normalized to UTC
(`...Z`). Naive datetimes are treated as UTC. Local-timezone conversion
is forbidden — it made `transport_hash` host-dependent (e.g. macOS EDT
vs container UTC) and broke Gate→worker integrity checks.

## Signatures

### Transport signature
Signs `transport_hash`.

Purpose:
- sender authenticity
- semantic packet integrity

### Hop signature
Optional.
Signs `hop_hash`.

Purpose:
- hop-level authenticity
- tamper-evident routing journal

## Lineage

Fields:
- `root_id`
- `parent_id`
- `generation`

Rules:
- root packet: `parent_id = null`, `generation = 0`
- child packet: `parent_id = parent.packet_id`, same `root_id`, `generation + 1`

## Provenance

Fields:
- `origin_kind`
- `requested_action`
- `resolved_by_gate`
- `route_kind` (optional: `"external_ingress"` or `"gate_relay"`)
- `original_source_node`

Purpose:
- distinguish client, node, and Gate-origin traffic
- preserve source context across routing
- `route_kind` distinguishes external client ingress from internal Gate-mediated relay traffic

## Hop trace

Hop trace is append-only.

Each hop is chained with:
- `previous_hop_hash`
- `hop_hash`

This provides tamper-evident route history without destabilizing the transport signature.
