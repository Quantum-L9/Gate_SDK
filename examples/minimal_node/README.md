# Minimal node

The whole integration surface of an L9 node (L9-PARTICIPATION-01):

| File | Purpose |
|---|---|
| `handlers.py` | domain logic, `@register_handler` |
| `spec.yaml` | registration identity (`GATE_NODE_SPEC_PATH`) |
| `app.py` | `create_node_app()` — nothing else |

Run it with the node's security environment (`L9_NODE_NAME`,
`L9_SIGNING_KEY`/`L9_SIGNING_KEY_ID`, `L9_VERIFYING_KEYS_JSON`,
`L9_REQUIRE_SIGNATURE=true`), `GATE_URL`, `GATE_ADMIN_TOKEN` and
`GATE_NODE_SPEC_PATH=examples/minimal_node/spec.yaml`:

```bash
uvicorn examples.minimal_node.app:app --host 0.0.0.0 --port 8000
```

It registers with Gate, re-registers every `GATE_REREGISTRATION_INTERVAL_SECONDS`
(so it comes back after a Gate restart), and answers `GET /v1/ready` with 200
only while Gate has accepted it. None of that is code in this directory.
