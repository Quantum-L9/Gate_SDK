# Constellation Node SDK Architecture

## Purpose

The SDK exists to ensure every Constellation node speaks the same protocol and obeys the same routing law.

The SDK is deliberately opinionated:

- one canonical transport type: `TransportPacket`
- one canonical node egress path: `GateClient` — `execute()` for applications, `send_to_gate()` for packet-native callers
- one canonical node runtime: `create_node_app()`

The two client surfaces are one egress path, not two. `execute()` is a closure
over the packet-native primitive: it builds the canonical root packet from
business inputs and hands it to `send_to_gate()`. There is no second transport
implementation, and no way for an application to reach a peer node through
either.

```text
application            action, payload, tenant, operation id, one deadline
      │
      ▼
GateClient.execute()   root packet · Gate destination · deadline · idempotency
      │
      ▼
GateClient.send_to_gate()   validate · sign · HTTP · decode · validate · typed errors
      │
      ▼
Gate
```

### The Gate-authority surface

One SDK serves both sides of Gate. After Gate makes the routing decision, the
mechanics of reaching the resolved worker are SDK-owned too:

```text
Gate                   resolve target · remaining budget · derive · dispatch hop
      │
      ▼
GateDispatchTransport.send_gate_authored_packet()
      │                authority check · sign · validate · /v1/execute · one POST
      │                decode · integrity · "does this answer my dispatch?"
      ▼
resolved worker
```

This is the only surface in the SDK that addresses a worker, and it lives in its
own `gate_authority` namespace, exported from neither the package root nor the
node-facing `gate` package. It is not a peer client: authority is checked on the
**packet**, not the caller, so importing it grants nothing. A dispatch is refused
before any network call unless the packet is sourced from Gate, replied to Gate,
addressed to the named target, `resolved_by_gate`, and `route_kind =
external_ingress` — which an application cannot produce.

The check reuses `validate_execute_ingress_packet`, the worker's own ingress
law, so Gate cannot send what its worker would reject.

Authority stays split: **Gate decides where, Gate_SDK decides how, the worker
decides what.** The SDK never resolves an action, queries a registry, chooses or
load-balances workers, fails over, marks a node unhealthy, or reads a payload.

See [`docs/gate-authority-transport.md`](docs/gate-authority-transport.md).

## System model

```text
Worker / Orchestrator Node
    │
    │   TransportPacket
    ▼
GateClient (GATE_URL only)
    │
    ▼
Gate
    │
    ├── validates ingress
    ├── resolves destination by action
    ├── appends hop trace
    └── dispatches to worker
Transport layers
1. Semantic transport core
The stable transport core includes:

header

address

tenant

payload

governance

provenance

delegation chain

lineage

attachments

This core is hashed into transport_hash.

2. Operational routing journal
hop_trace is append-only and excluded from transport_hash.

This enables:

stable transport signatures

mutable routing history

Gate ingress/dispatch recording without breaking packet integrity

Hop trace is protected separately by:

previous_hop_hash

hop_hash

optional hop_signature

Packet semantics
Root packet
Created with create_transport_packet(...)

Properties:

new packet_id

root_id = packet_id

parent_id = None

generation = 0

Child packet
Created with packet.derive(...)

Properties:

new packet_id

parent_id = parent.packet_id

same root_id

generation += 1

Use child packets for semantic changes:

payload mutation

action change

provenance change

destination change

workflow step execution

Hop append
Created with packet.with_hop(...)

Use hop append for observational changes:

ingress

dispatch

execution

response

Routing law
The SDK enforces the following:

Node-origin packets
address.source_node != client

provenance.origin_kind == "node"

address.destination_node == "gate"

Gate-authored dispatch
provenance.origin_kind == "gate"

provenance.resolved_by_gate == True

destination may be a worker node

Forbidden
direct node-to-node peer dispatch

peer URL awareness in node runtime

alternate transport formats

Runtime model
The node runtime exposes:

POST /v1/execute

GET /v1/health

GET /metrics

Execution flow:

decode canonical TransportPacket

validate packet

resolve registered handler

append execution hop

execute handler

derive response/failure packet

append response hop

optionally sign response

Orchestrator model
Orchestrators are internal clients of Gate.

They:

receive workflow packets

maintain local workflow state

derive step packets

send each step to Gate

accumulate results

return final response

They do not:

know peer node URLs

resolve actions directly

bypass Gate

SDK boundary
The SDK owns:

protocol contract

security

runtime

Gate client

orchestration helpers

The Gate repo owns:

ingress enforcement

action resolution

registry

dispatch

workflow kernel


```dotenv
