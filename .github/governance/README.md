# CI governance instantiation pack

This directory is the CI-side instantiation pack read by
[`Quantum-L9/l9-ci-core`](https://github.com/Quantum-L9/l9-ci-core)'s
`resolve-governance` action (Phase 3 of the L9 CI control plane). It controls
**when** the governed Semgrep analysis pipeline runs, whether it is required,
which SDK policy it selects, and how its result is rolled out — it does not
inspect findings or compute gates itself. Findings interpretation belongs to
the pinned `l9-ci-sdk`, not to this repo or to Core.

See `.github/workflows/l9-analysis.yml` for the workflow that consumes these
files.

## Files

- `execution-profiles.yaml` — maps a profile name (`pr_fast`, `merge`,
  `nightly`, `release`, `supply_chain`) to an SDK execution profile,
  strictness, default publication mode, provider set, and allowed trigger
  events.
- `provider-requiredness.yaml` — whether a provider must run for a given
  profile. A required provider cannot be disabled.
- `rule-modes.yaml` — default publication mode per profile
  (`blocking` / `advisory` / `shadow` / `disabled`), with optional
  per-provider overrides.
- `quality-thresholds.yaml` — selects an SDK policy file per profile. This
  repo does not currently define a custom policy, so `sdk_policy` is empty
  and the SDK's own defaults apply.
- `promotion-policy.yaml` — the allowed mode transitions (e.g.
  `shadow -> advisory -> blocking`) and the observation requirements before a
  mode may be promoted.
- `waivers.yaml` — scope- and time-bounded governance waivers. Empty by
  default. Every waiver requires a unique id, owner, reason, creation date,
  expiration date, and explicit scope; expired or malformed waivers fail
  validation.

## Wired profiles

All five profiles are wired to profile-scoped workflows, each of which calls
the shared reusable engine `.github/workflows/_l9-analysis.yml` with the
matching profile and a distinct `matrix-id`. Every trigger matches the
profile's `allowed_events` above:

| Profile | Workflow | Trigger |
|---|---|---|
| `pr_fast` | `.github/workflows/l9-analysis.yml` | `pull_request`, `workflow_dispatch` |
| `merge` | `.github/workflows/l9-analysis-merge.yml` | `push` to `main`/`master`, `workflow_dispatch` |
| `nightly` | `.github/workflows/l9-analysis-nightly.yml` | daily `schedule`, `workflow_dispatch` |
| `release` | `.github/workflows/l9-analysis-release.yml` | `push` of a `v*` tag, `workflow_dispatch` |
| `supply_chain` | `.github/workflows/l9-analysis-supply-chain.yml` | weekly `schedule`, `workflow_dispatch` |

The pack itself is validated in CI by `.github/workflows/l9-governance-validate.yml`,
which runs Core's `validate-governance` action against every profile/provider
combination whenever these files change.

## Do not

- Do not have this repo (or a workflow file here) parse Semgrep findings,
  compute pass/fail gates, or reconstruct canonical finding bundles. That is
  exclusively the SDK's job; Core only routes and publishes what the SDK
  produces.
- Do not add a waiver without an owner, reason, and expiration date.
