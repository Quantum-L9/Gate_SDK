
Node Runtime — Execution Contract

Overview

A node is a pure execution unit.

It:
	•	receives TransportPacket
	•	executes action
	•	returns TransportPacket

It does NOT:
	•	route
	•	discover peers
	•	call other nodes directly

⸻

Core Rule

All outbound work MUST go through Gate


⸻

Execution Flow

Gate → Node
Node validates packet
Node executes action handler
Node returns response packet


⸻

Handler Contract

async def handler(packet: TransportPacket) -> TransportPacket:
    ...


⸻

Responsibilities

1. Deterministic Execution

Same input → same output (ideally)

2. No Side Routing

No:

node → node direct calls


⸻

3. Response Integrity

Must return valid TransportPacket.

⸻

Concurrency Model

Node:
	•	is async
	•	should be stateless where possible
	•	must respect concurrency limits

⸻

Delegation Pattern

If node needs another action:

node → gate → node

Never:

node → node


⸻

Anti-Patterns

❌ Embedded Routing Logic

Node deciding where to send work

❌ Shared Mutable State

Breaks reproducibility

⸻

Design Insight

Node = pure function + side effects

Gate = control plane

⸻
