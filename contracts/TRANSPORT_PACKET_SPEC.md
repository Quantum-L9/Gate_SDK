# TransportPacket Specification

## Overview

`TransportPacket` is the canonical transport unit for Constellation.

All ingress, inter-node routing, orchestration, and responses use the same packet type.

## Core invariants

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
- `original_source_node`

Purpose:
- distinguish client, node, and Gate-origin traffic
- preserve source context across routing

## Hop trace

Hop trace is append-only.

Each hop is chained with:
- `previous_hop_hash`
- `hop_hash`

This provides tamper-evident route history without destabilizing the transport signature.
