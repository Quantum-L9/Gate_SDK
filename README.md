# Constellation Node SDK

`constellation-node-sdk` is the canonical Python SDK for Constellation worker and orchestrator nodes.

It defines the packet-native transport contract used by the Constellation system:

- `TransportPacket` is the single canonical wire format
- all node-originated follow-up work goes back through **Gate**
- hop trace is append-only and tamper-evident
- transport integrity, lineage, provenance, and delegation are first-class protocol concepts

## What this SDK provides

- canonical `TransportPacket` models and helpers
- transport hashing, signing, verification, and validation
- Gate-only outbound client
- reusable node runtime with `/v1/execute` and `/v1/health`
- orchestrator helpers for workflow composition through Gate
- tests and examples for worker and orchestrator nodes

## Architectural rules

1. Nodes **must not** know peer node URLs.
2. Nodes **must only** send follow-up work to `GATE_URL`.
3. Gate is the sole routing authority.
4. `TransportPacket` is the only supported transport format.
5. Semantic packet changes create child packets via `derive()`.
6. Observational movement appends hop trace entries without changing `transport_hash`.

## Install

```bash
pip install -e .
For development:

pip install -e ".[dev]"
Quick start
Worker node
from constellation_node_sdk import create_node_app, register_handler

@register_handler("score")
async def handle_score(_tenant: str, payload: dict) -> dict:
    return {
        "status": "completed",
        "score": 91,
        "entity_id": payload["entity_id"],
    }

app = create_node_app(
    service_name="score-node",
    version="1.0.0",
)
Orchestrator node
from constellation_node_sdk import create_node_app, register_handler
from constellation_node_sdk.gate import GateClient, get_gate_client_config_from_env
from constellation_node_sdk.orchestrator.step_executor import StepExecutor

gate_client = GateClient(get_gate_client_config_from_env())
step_executor = StepExecutor(gate_client=gate_client, source_node="orchestrator")

@register_handler("full_pipeline")
async def handle_full_pipeline(_tenant: str, payload: dict, packet):
    enrich = await step_executor.execute_step(
        parent=packet,
        action="enrich",
        payload={"entity_id": payload["entity_id"]},
    )
    score = await step_executor.execute_step(
        parent=packet,
        action="score",
        payload={**payload, **enrich.payload},
    )
    return {
        "status": "completed",
        "entity_id": payload["entity_id"],
        "enrich": enrich.payload,
        "score": score.payload,
    }

app = create_node_app(
    service_name="orchestrator",
    version="1.0.0",
)
Environment
See .env.example for runtime configuration.

Repo structure
src/constellation_node_sdk/transport/ — transport contract and hashing

src/constellation_node_sdk/security/ — signing, verification, validation

src/constellation_node_sdk/gate/ — Gate-only client and registration

src/constellation_node_sdk/runtime/ — reusable node runtime

src/constellation_node_sdk/orchestrator/ — workflow composition helpers

contracts/ — formal contract specs

examples/ — runnable worker and orchestrator examples

tests/ — unit and integration coverage

.github/governance/ — CI governance instantiation pack for the L9 analysis pipeline

CI

`.github/workflows/ci.yml` runs lint, type-check, contract validation, schema-drift check, tests, dependency audit, secret scan, and build on every pull request.

`.github/workflows/l9-analysis.yml` wires this repo into the org-wide L9 CI control plane ([Quantum-L9/l9-ci-core](https://github.com/Quantum-L9/l9-ci-core)): it runs a governed Semgrep scan, normalizes and validates the findings through the pinned `l9-ci-sdk`, and publishes the result as a GitHub check via Core's `publish-analysis.yml` reusable workflow. See `.github/governance/README.md` for how the governance profile is resolved.

Status
This repo is intended to be:

packet-native

Gate-routed

composable

production-oriented

strict about protocol correctness


```markdown
