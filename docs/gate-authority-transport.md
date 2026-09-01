# Gate-Authority Worker Transport

**For Constellation.Gate only.** This is the third and last transport surface in
the SDK, and the only one that addresses a worker.

| Surface | Caller | Reaches |
|---|---|---|
| `GateClient.execute()` | a normal application | Gate, and only Gate |
| `GateClient.send_to_gate()` | a packet-native caller | Gate, and only Gate |
| `GateDispatchTransport.send_gate_authored_packet()` | **Gate** | the worker Gate resolved |

---

## Why it is safe to exist

Adding a worker-addressing surface is exactly where an SDK grows a general
node-to-peer client by accident. It does not here, because **authority lives on
the packet, not on the caller**.

Importing `constellation_node_sdk.gate_authority` grants nothing. Every dispatch
is refused before any network call unless the packet is:

- sourced from the configured Gate node,
- addressed to the named target node,
- replied to the configured Gate node,
- marked `provenance.resolved_by_gate`,
- carrying `provenance.route_kind == "external_ingress"`.

An application cannot mint such a packet — `GateClient` will not build one, and
its own outbound policy rejects a peer-targeted packet before it leaves. So
holding a worker URL is never sufficient.

That check is not a second copy of the rule. It calls
`validate_execute_ingress_packet` — the same function every SDK-based worker
applies on `/v1/execute` — so Gate cannot send a packet its worker would reject,
and there is no second dialect to drift.

---

## Usage

```python
from constellation_node_sdk.gate_authority import (
    GateDispatchTransport,
    GateDispatchTransportConfig,
)

transport = GateDispatchTransport(
    GateDispatchTransportConfig(local_gate_node="gate"),
    client=pooled_httpx_client,      # optional; reused across dispatches
)

response = await transport.send_gate_authored_packet(
    packet=dispatch_packet,                        # Gate derived and hopped it
    target_node=target.node_name,                  # Gate resolved it
    worker_base_url=target.internal_url,           # Gate's registry knows it
)
```

The SDK owns: authority validation, signing, transport validation of the signed
artifact, the `/v1/execute` endpoint, the single network attempt, the deadline
actually applied, response decoding, integrity and signature validation, and
proving the response answers this dispatch.

Gate keeps: target selection, deadline arithmetic, per-node concurrency, health
marking, and replay policy.

---

## One downstream deadline

There is **no timeout parameter**, deliberately.

The deadline is derived from `packet.header.timeout_ms`, which is the same value
the worker's runtime uses to bound its handler. So a single number governs three
things:

```
original caller budget
      ↓ Gate subtracts what it has already spent
remaining budget, bounded by the registered worker cap
      ↓ Gate writes it into the derived child
child.header.timeout_ms  ──┬──►  the deadline Gate waits on the socket
                           └──►  the budget the worker bounds its handler with
```

### Required consumer change

Gate must write the remaining budget into the derived child:

```python
dispatch_base = ingress_observed.derive(
    ...,
    timeout_ms=remaining_worker_budget,   # ← previously omitted
)
```

Without it the child inherits the parent's full budget, and Gate ends up waiting
2s on a socket while the packet tells the worker it has 30s. A `timeout_seconds`
parameter on this API would have preserved exactly that split, which is why
there is not one.

---

## Exactly one attempt

`send_gate_authored_packet()` performs one POST. No retry on connection failure,
timeout, 429, or any 5xx.

Retrying a worker execution is a decision that needs the operation's idempotency
key and its remaining deadline — both of which are Gate's, not the transport's.
A hidden retry here would double-execute a domain operation.

---

## Typed failures

A separate hierarchy from `GateClientError`, on purpose: that one means "the call
*to* Gate failed", and `GateConnectionError` would read as "could not reach
Gate" while actually meaning a worker was down.

| Type | Meaning | Gate's usual reading |
|---|---|---|
| `GateDispatchAuthorityError` | not a Gate-authored dispatch | a bug in Gate; never reached the network |
| `GateDispatchConfigurationError` | transport or URL cannot express this dispatch | a bug in Gate or its registry |
| `GateDispatchSecurityError` | signing / signature / integrity failed (`.direction`) | untrusted; never retry into it |
| `WorkerConnectionError` | worker unreachable; nothing ran | node health |
| `WorkerTimeoutError` | budget elapsed (`.timeout_seconds`); may have run | replay only under idempotency |
| `WorkerHTTPError` | non-2xx (`.status_code`, `.is_server_error`) | 5xx is health, 4xx is a contract problem |
| `WorkerResponseError` | not a canonical answer to this dispatch | protocol mismatch |

All descend from `GateDispatchError`, and every cause is chained. Gate no longer
needs `except httpx.TransportError`, and no longer needs to flatten into
`RuntimeError`.

**The SDK reports; Gate decides.** It never marks a node unhealthy, evicts it
from the registry, adjusts routing weight, or fails over to another worker.

---

## Response validation

The return value is a validated `TransportPacket`, never a dict or a raw
response. Before returning, the SDK proves the answer belongs to *this*
dispatch: worker identity, destination, action, root lineage, causation, tenant,
correlation, and idempotency. A canonical, correctly signed packet from the
wrong worker is still the wrong answer.

---

## Connection lifecycle

Gate dispatches continuously, so a client per packet would mean a TCP handshake
per packet. Supply a pooled `httpx.AsyncClient` and it is reused across
dispatches; its lifetime stays yours, and the transport never closes a client it
did not create. Used as an async context manager, the transport creates and
closes one of its own instead.

The per-dispatch deadline is applied per request, so a pooled client's own
default timeout can never widen a dispatch budget.

---

## See also

- [`gate-client.md`](gate-client.md) — the application and packet-native surfaces
- [`node-runtime.md`](node-runtime.md) — the worker that receives these packets
- [`../contracts/ROUTING_POLICY_SPEC.md`](../contracts/ROUTING_POLICY_SPEC.md)
