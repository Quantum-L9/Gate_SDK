
Migration from PacketEnvelope → TransportPacket

Overview

PacketEnvelope is deprecated.

TransportPacket replaces it with:
	•	stricter semantics
	•	full lineage
	•	routing authority enforcement

⸻

Key Differences

Feature	PacketEnvelope	TransportPacket
Routing authority	implicit	explicit (provenance)
Lineage	partial	full hop_trace
Idempotency	inconsistent	first-class
Replay protection	weak	enforced
Mutation model	loose	derive-only


⸻

Migration Strategy

Step 1 — Replace Envelope Creation

# OLD
PacketEnvelope(...)

# NEW
create_transport_packet(...)


⸻

Step 2 — Replace Routing Logic

Remove:

node → node calls

Replace with:

node → gate → node


⸻

Step 3 — Add Provenance

Ensure:

resolved_by_gate = False (initial)


⸻

Step 4 — Update Handlers

Handlers must:
	•	accept TransportPacket
	•	return TransportPacket

⸻

Step 5 — Enforce Idempotency

Add:

idempotency_key

for critical flows.

⸻

Common Pitfalls

❌ Partial Migration

Mixing Envelope and TransportPacket

❌ Missing Provenance

Breaks routing enforcement

❌ Direct Dispatch

Bypassing Gate

⸻

Validation Checklist
	•	All requests use TransportPacket
	•	No direct node-to-node calls
	•	hop_trace present and growing
	•	idempotency enforced where needed
	•	replay protection active

⸻

Design Insight

This migration is not a refactor.

It is a shift to a control-plane-driven distributed system.

⸻
