# Orchestrator Node Example

This example shows a minimal orchestrator node using the SDK step executor.

## Action handled

- `full_pipeline`

## Behavior

1. receive `full_pipeline`
2. send `enrich` step to Gate
3. send `score` step to Gate
4. return combined result

## Run

```bash
export L9_ENVIRONMENT=local
export L9_NODE_NAME=orchestrator
export L9_SERVICE_NAME=orchestrator-node
export L9_ALLOWED_ACTIONS=full_pipeline
export L9_ALLOWED_PACKET_TYPES=request
export GATE_URL=http://localhost:9000

uvicorn examples.orchestrator_node.app:app --host 0.0.0.0 --port 8002

```python
