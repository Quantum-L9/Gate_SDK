# Application client example

The complete outbound Gate integration for a business application.

```bash
python examples/application_client/app_client.py
```

## What the application owns

| Concern | Owner |
|---|---|
| Domain payload meaning | the application |
| What counts as one logical operation (`idempotency_key`) | the application |
| The operation deadline | the application |
| Everything else below | Gate_SDK |

## What the application never does

`app_client.py` contains no `TransportPacket`, no `create_transport_packet`, no
destination, no signing call, no `httpx` import, and no failure-string matching.
That is the point: each of those, written in an application, is a shadow SDK in
its first week.

If your integration needs one of them, that is a Gate_SDK capability gap
(ADR-SDK-013) — raise it against the SDK rather than solving it locally.

## Failure handling

Every failure is a `GateClientError` subclass, so classification is a type
check:

| Type | Meaning | Retry |
|---|---|---|
| `GateConnectionError` | Gate never reached; nothing ran | yes |
| `GateTimeoutError` | deadline elapsed; Gate may have run it | only under a stable idempotency key |
| `GateHTTPError` | Gate answered non-2xx (`.status_code`) | `.is_server_error` only |
| `GateResponseError` | answer was not a canonical packet | no |
| `GateSecurityError` | signature or integrity failure | no |
| `GatePolicyError` | outbound packet violated Gate-only routing | no |
| `GateConfigurationError` | local misconfiguration | no |

## Packet-native use

Gate, the SDK runtime, orchestrators, and protocol tests work at the packet
level with `create_transport_packet` and `GateClient.send_to_gate`. See
`examples/worker_node/` and `examples/orchestrator_node/`. Those are protocol
primitives, not the normal application integration.
