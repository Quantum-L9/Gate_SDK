
Gate Client — SDK Entry Point

Overview

The Gate client is the only supported way to send work into the system.

It abstracts:
	•	packet creation
	•	serialization
	•	HTTP transport
	•	response validation

⸻

Responsibilities

1. Packet Construction

Creates canonical TransportPacket.

2. Gateway Invocation

Sends packet to:

POST /v1/execute

3. Response Validation

Ensures:
	•	response is valid TransportPacket
	•	matches request semantics

⸻

Usage

client.execute(
    action="score",
    payload={"entity_id": "42"},
    tenant="tenant-a",
)


⸻

Guarantees

Canonical Envelope

Client always produces:
	•	valid packet
	•	correct provenance
	•	correct routing intent

⸻

No Routing Logic

Client does NOT:
	•	choose nodes
	•	resolve actions
	•	perform retries across nodes

All routing is delegated to Gate.

⸻

Error Model

Client surfaces:
	•	transport errors
	•	Gate-level errors (mapped)
	•	validation errors

⸻

Best Practices

Always Use Client

Never construct raw packets manually outside controlled environments.

⸻

Idempotency

Provide idempotency key for:
	•	retries
	•	critical operations

⸻

Anti-Patterns

❌ Direct HTTP Calls

requests.post("/v1/execute", raw_dict)

Breaks:
	•	schema guarantees
	•	validation
	•	future compatibility

⸻

