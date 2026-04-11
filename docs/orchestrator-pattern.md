
Orchestrator Pattern

Overview

Orchestrator is a node that composes other nodes via Gate.

It:
	•	receives request
	•	breaks into steps
	•	sends steps via Gate
	•	aggregates results

⸻

Flow

client
  ↓
Gate
  ↓
orchestrator node
  ↓
Gate → worker A
  ↓
Gate → worker B
  ↓
aggregate
  ↓
response


⸻

Key Rule

Orchestrator NEVER calls nodes directly.

Always:

orchestrator → gate → worker


⸻

Benefits

1. Centralized Routing

Gate maintains:
	•	observability
	•	control
	•	policy

⸻

2. Traceability

Each step recorded in hop_trace.

⸻

3. Composability

Workflows can evolve without node coupling.

⸻

Implementation

result_a = await gate_client.execute(...)
result_b = await gate_client.execute(...)


⸻

Anti-Patterns

❌ Direct Worker Calls

Breaks:
	•	lineage
	•	control plane

⸻

❌ Hidden Workflow State

All steps must be observable via packets

⸻

Design Insight

Orchestrator is:

a workflow engine built on top of TransportPacket + Gate

⸻

