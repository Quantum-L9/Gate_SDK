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

Native gates:

`.github/workflows/ci.yml` runs lint, type-check, contract validation, schema-drift check, tests, dependency audit, secret scan, and build on every pull request. `coverage.yml`, `integration.yml`, `nightly.yml`, `pre-commit-ci.yml`, `release.yml`, and `release-publish.yml` cover coverage, integration, nightly regression, pre-commit enforcement, and release/publish.

L9 CI control plane ([Quantum-L9/l9-ci-core](https://github.com/Quantum-L9/l9-ci-core)):

This repo fully instantiates the l9-ci-core consumer surface. A shared reusable engine, `.github/workflows/_l9-analysis.yml`, runs a governed Semgrep scan, normalizes/validates the findings through the pinned `l9-ci-sdk` using Core's SHA-pinned composite actions, and publishes the result as a GitHub check via Core's `publish-analysis.yml` reusable workflow. Five thin wrappers drive it, one per governance profile:

- `l9-analysis.yml` — `pr_fast`, on pull requests
- `l9-analysis-merge.yml` — `merge`, on push to `main`/`master`
- `l9-analysis-nightly.yml` — `nightly`, daily schedule
- `l9-analysis-release.yml` — `release`, on `v*` tags
- `l9-analysis-supply-chain.yml` — `supply_chain`, weekly schedule

Two more workflows exercise the rest of Core's consumer primitives:

- `l9-governance-validate.yml` — validates `.github/governance/` via Core's `validate-governance` action
- `l9-sdk-contract-check.yml` — provisions and verifies the pinned `l9-ci-sdk` CLI contract via Core's `provision-sdk` action

`l9-lint-test.yml` adopts Core's generic Python lint/test consumer template (overlaps the native `ci.yml`; `ci.yml` remains the merge-blocking source of truth). See `.github/governance/README.md` for how a governance profile is resolved and which events each profile allows.

Status
This repo is intended to be:

packet-native

Gate-routed

composable

production-oriented

strict about protocol correctness


```markdown
