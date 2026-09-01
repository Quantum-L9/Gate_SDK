# Gate Client — SDK Entry Point

The Gate client is the only supported way to send work into the system.

It has two surfaces, and choosing correctly between them is the whole point of
this page.

| Surface | For | Takes |
|---|---|---|
| `GateClient.execute()` | **normal applications** | business inputs |
| `GateClient.send_to_gate()` | Gate, the SDK runtime, orchestrators, protocol tests | a canonical `TransportPacket` |

---

## Application surface — `execute()`

```python
from constellation_node_sdk import GateClient, GateClientConfig

client = GateClient(
    GateClientConfig(
        gate_url="https://gate.internal:8000",
        local_node="erp",
        timeout_seconds=30.0,
    )
)

response = await client.execute(
    action="converge",
    payload={"entity": {"id": "org-4711"}},
    tenant="tenant-a",
    idempotency_key="erp:enrichment:run-4711",
    timeout_ms=25_000,
    correlation_id="run-4711",
)
```

That is the entire outbound integration. The SDK owns everything below it:

- root `TransportPacket` construction
- Gate destination, source, and reply-to identity
- deadline translation
- transport idempotency representation
- correlation, causation, and lineage
- signing, HTTP, and response validation
- typed failure classification

### Parameters

| Parameter | Meaning |
|---|---|
| `action` | The intent. Gate resolves which node owns it. |
| `payload` | The domain payload. Opaque to the SDK. |
| `tenant` | A tenant id, mapping, or `TenantContext`. |
| `idempotency_key` | Your logical business operation identity (see below). |
| `timeout_ms` | The single operation budget. Defaults to `config.timeout_seconds`. |
| `correlation_id`, `trace_id` | Join this call to an existing trace. Default to the new packet id. |
| `classification`, `compliance_tags`, `retention_days`, `priority` | Security and governance metadata. |

There is deliberately **no destination parameter**. Intent is the action; Gate
resolves ownership. See [`ROUTING_POLICY_SPEC.md`](../contracts/ROUTING_POLICY_SPEC.md).

---

## One deadline

`timeout_ms` is a single operation budget. The SDK writes it into the packet
header **and** derives the real network deadline from it, so the deadline a
packet advertises downstream and the one the caller actually waits on cannot
drift apart.

Two optional configuration knobs shape it, both explicit and off by default:

| Field | Default | Effect |
|---|---|---|
| `max_timeout_ms` | `None` | Hard ceiling on any budget, for a deployment whose synchronous caller cannot outlive a fixed window. |
| `transport_margin_ms` | `0` | Slice of the budget reserved so the SDK raises a typed timeout just before the caller's own deadline. |

`timeout_seconds` is the **default operation budget**, not a second deadline.
`send_to_gate()` takes its deadline from `packet.header.timeout_ms`, so a
packet-native caller does not get a longer wire wait than the packet promises.

---

## Idempotency

The application decides what constitutes one logical business operation; the
SDK owns only its transport representation.

```python
idempotency_key = f"erp:enrichment:{durable_run_id}"
```

No payload hash is ever substituted for it. Two structurally identical payloads
may be two different runs, and one retried run is still the same operation —
only the application knows which.

---

## No hidden retries

`execute()` and `send_to_gate()` perform **exactly one** HTTP request. A failed
execution comes back as a typed failure.

Retrying belongs to the layer with semantic authority and a stable idempotency
key. A hidden retry in the transport would double-execute a domain operation.

Gate *registration* is the one exception: it is control-plane reconciliation,
not application execution, and retries with bounded, visible backoff.

---

## Error model

Every failure leaving the client is a `GateClientError` subclass. Callers never
need `httpx`, and never need to match substrings against an exception message.

| Type | Meaning | Retry |
|---|---|---|
| `GateConfigurationError` | Local misconfiguration; never reached the wire | no |
| `GatePolicyError` | Outbound packet violated Gate-only routing | no |
| `GateSecurityError` | Signing, signature, or integrity failure (`.direction`) | no |
| `GateConnectionError` | Gate never reached; nothing ran | yes |
| `GateTimeoutError` | Deadline elapsed (`.timeout_seconds`); Gate may have run it | only under a stable idempotency key |
| `GateHTTPError` | Non-2xx (`.status_code`, `.is_server_error`) | server errors only |
| `GateResponseError` | Answer was not a canonical packet | no |

```python
try:
    response = await client.execute(...)
except GateTimeoutError as exc:
    ...  # exc.timeout_seconds
except GateHTTPError as exc:
    ...  # exc.status_code, exc.is_server_error
except GateClientError as exc:
    ...  # one base class catches the rest
```

Underlying exceptions are chained (`__cause__`), so nothing is lost. Messages
always name the failure type, because httpx timeout exceptions frequently
stringify to an empty string.

`GatePolicyError`, `GateResponseError`, and `GateConfigurationError` also
subclass `ValueError`, and `GateTimeoutError` also subclasses `TimeoutError`,
so callers written before the taxonomy keep working.

---

## Packet-native surface — `send_to_gate()`

For Gate, the SDK runtime, orchestrators, and protocol tests:

```python
from constellation_node_sdk import create_transport_packet

packet = create_transport_packet(action=..., payload=..., tenant=..., ...)
response = await client.send_to_gate(packet)
```

These are protocol primitives, not the normal application integration. If an
application needs them, treat it as a Gate_SDK capability gap and raise it here
rather than solving it locally — that is how shadow SDKs start.

---

## Anti-patterns

```python
requests.post(f"{gate_url}/v1/execute", json=raw_dict)   # ❌
```

Breaks schema guarantees, validation, signing, and forward compatibility.

```python
if "timeout" in str(exc).lower():                        # ❌
    retry()
```

Classify with the taxonomy above. An httpx `ConnectTimeout` stringifies to `""`,
so a substring classifier records a correct verdict with a blank reason.

```python
packet = create_transport_packet(                        # ❌ in an application
    action=..., destination_node="gate",
    timeout_ms=int(config.timeout_seconds * 1000),
)
await client.send_to_gate(packet)
```

Use `execute()`. Every line above is transport mechanics the SDK owns.

---

## See also

- [`examples/application_client/`](../examples/application_client/) — the complete application integration
- [`node-runtime.md`](node-runtime.md) — the receiving side
- [`orchestrator-pattern.md`](orchestrator-pattern.md) — packet-native composition
