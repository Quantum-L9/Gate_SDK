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

## Currently wired profile

Only `pr_fast` (triggered on `pull_request` / `workflow_dispatch`) is wired
up in `l9-analysis.yml` today. The other profiles are defined here so they
can be adopted incrementally (e.g. a `merge` run on push to `main`, or a
`nightly`/`release`/`supply_chain` run) without further governance-file
changes — only a new workflow job needs to reference them.

## Do not

- Do not have this repo (or a workflow file here) parse Semgrep findings,
  compute pass/fail gates, or reconstruct canonical finding bundles. That is
  exclusively the SDK's job; Core only routes and publishes what the SDK
  produces.
- Do not add a waiver without an owner, reason, and expiration date.
