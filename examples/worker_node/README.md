# Worker Node Example

This example shows a minimal worker node built with `constellation-node-sdk`.

## Action handled

- `score`

## Run

```bash
export L9_ENVIRONMENT=local
export L9_NODE_NAME=score
export L9_SERVICE_NAME=score-node
export L9_ALLOWED_ACTIONS=score
export L9_ALLOWED_PACKET_TYPES=request
export GATE_URL=http://localhost:9000

uvicorn examples.worker_node.app:app --host 0.0.0.0 --port 8001
What it demonstrates
packet-native runtime

canonical handler registration

response generation through SDK runtime

Gate self-registration via spec.yaml if enabled


```python
