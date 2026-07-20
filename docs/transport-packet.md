
TransportPacket — Canonical System Contract

Overview

TransportPacket is the only allowed communication primitive across the system.

It defines:
	•	routing intent
	•	execution semantics
	•	lineage
	•	replay/idempotency boundaries
	•	security envelope

No alternative envelope, DTO, or ad-hoc structure is permitted.

⸻

Core Guarantees

1. Single Transport Surface

All interactions must use TransportPacket.

client → gate → node → gate → node → ...

There is no:
	•	direct node-to-node call
	•	alternate transport format
	•	implicit execution context

⸻

2. Immutable Execution Record

Each packet represents a complete, immutable execution intent.

Mutation is only allowed through:
	•	derive(...)
	•	with_hop(...)

⸻

3. Lineage Preservation

Every hop is recorded.

hop_trace = append-only

This guarantees:
	•	full execution reconstruction
	•	debugging without external tracing systems
	•	auditability

⸻

Structure

Header

Defines identity and execution semantics.

header = {
  "packet_id": UUID,
  "action": str,
  "packet_type": "request" | "response",
  "created_at": timestamp,
  "idempotency_key": Optional[str]
}

Address

Defines routing envelope.

address = {
  "source_node": str,
  "destination_node": str,
  "reply_to": str
}

Payload

Opaque business data.

payload: dict[str, Any]


⸻

Provenance

Defines who owns routing authority.

provenance = {
  "origin_kind": "client" | "node" | "gate",
  "resolved_by_gate": bool,
  "original_source_node": str
}

Critical invariant:

If resolved_by_gate == False, packet MUST go through Gate before dispatch.

⸻

Hop Trace

Append-only execution log.

Each hop contains:
	•	node
	•	action
	•	timestamp
	•	status
	•	metadata

⸻

Execution Semantics

Request Flow

request packet
  ↓
Gate validates
  ↓
Gate resolves action → node
  ↓
Gate dispatches (derive)
  ↓
Node executes
  ↓
Node returns response packet
  ↓
Gate returns upstream


⸻

Response Semantics

Response packets:
	•	preserve original packet_id
	•	reverse address direction
	•	carry execution result in payload

⸻

Idempotency

Key Rules
	•	defined at header level
	•	enforced at Gate
	•	cache stores full response packet

same idempotency_key → same response packet


⸻

Replay Protection

Identity

Replay is based on:

packet.header.packet_id

Scope
	•	protects transport-level duplication
	•	independent of idempotency

⸻

Security Model

TransportPacket enables:
	•	signature validation
	•	hop-level verification
	•	provenance enforcement

⸻

Anti-Patterns

❌ Direct Node Calls

node → node (bypass gate)

Breaks:
	•	lineage
	•	routing authority
	•	observability

⸻

❌ Mutating Packet In Place

Always use:

packet.derive(...)


⸻

❌ Multiple Transport Schemas

Leads to:
	•	inconsistent execution semantics
	•	broken replay/idempotency

⸻

Design Insight

TransportPacket is not just a DTO.

It is a distributed execution log + routing contract + security boundary.

⸻
