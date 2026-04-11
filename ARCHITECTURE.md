# Constellation Node SDK Architecture

## Purpose

The SDK exists to ensure every Constellation node speaks the same protocol and obeys the same routing law.

The SDK is deliberately opinionated:

- one canonical transport type: `TransportPacket`
- one canonical node egress path: `GateClient.send_to_gate()`
- one canonical node runtime: `create_node_app()`

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
