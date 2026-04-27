# LOAD_PACK__REGISTRY.md

**Version:** 1.0.0  
**Authority:** This file is the sole activation index for all AI agent load packs across the
L9 system. It governs pack selection, load order, conflict resolution, and combined-session
composition. Agents MUST consult this file before loading any pack.

**Authority order for conflicts:** AGENT_LOAD_PACK > User_Preferences > domain packs  
**Load order within a session:** resolve registry first → load base packs → load domain packs

---

## Pack Index

| pack_id | Title | File | Activation Trigger | Authority Level | Depends On |
|---|---|---|---|---|---|
| `gate-sdk` | Gate_SDK Architecture Kernel | `AGENT_LOAD_PACK.md` | Any Gate_SDK repo work: code change, review, test authoring, PR, CI debugging | **HIGHEST** — repo contracts > CI > tests > code | None |
| `user-prefs` | Igor Beylin Operating Profile | `LOAD_PACK__User_Preference_Consolidation.md` | Any session involving Igor Beylin as principal; personalization context | HIGH — overrides defaults, not domain rules | None |
| `boss-eval` | BOSS Deal Evaluator | `LOAD_PACK__BOSS_Deal_Evaluator.md` | Guyana commodity deal evaluation, ROI modeling, trade finance, counterparty analysis | MEDIUM — domain-specific | `user-prefs` |
| `enforcement` | Cursor Enforcement Verification | `LOAD_PACK__Enforcement_Verification.md` | Cursor AI governance diagnostics, enforcement gap analysis, rule compliance review | MEDIUM — diagnostic only | `user-prefs` |
| `policy-arch` | Presidential-Grade Policy Architect | `LOAD_PACK__Presidential_Grade_Policy_Architect.md` | Guyana policy asset generation, regulatory framework, institutional briefs | MEDIUM — domain-specific | `user-prefs` |
| `saas-ynp` | YNP v3 SaaS Productization | `LOAD_PACK__SaaS_Productization_Digital_Platform.md` | YNP digital platform, Scrap Management SaaS, chained prompt generation | MEDIUM — domain-specific | `user-prefs` |

---

## Activation Rules

### Single-pack sessions
Load exactly one pack matching the activation trigger. If multiple packs match, resolve by
authority level (highest wins). Do NOT load multiple domain packs unless the session
explicitly requires combined activation (see Combined-Activation Matrix below).

### Multi-pack sessions
1. MUST load `user-prefs` first if Igor Beylin is the principal
2. Load domain-specific packs second in dependency order
3. Conflicts between packs resolve by authority level (see table above)
4. Do NOT re-read any pack that is already loaded in the session context

### Pack-selection decision tree
```
What is the task?
│
├─ Modifying / reviewing / testing Gate_SDK code or CI?
│   → Load: gate-sdk
│   → Optionally add: user-prefs (for personalization)
│
├─ Guyana commodity deal analysis or trade finance ROI?
│   → Load: user-prefs + boss-eval
│
├─ Cursor AI governance or enforcement gap review?
│   → Load: user-prefs + enforcement
│
├─ Guyana regulatory policy or institutional brief?
│   → Load: user-prefs + policy-arch
│
├─ YNP v3 digital platform or Scrap Management SaaS?
│   → Load: user-prefs + saas-ynp
│
└─ General session without specific domain?
    → Load: user-prefs only
```

---

## Combined-Activation Matrix

| Session Type | Load Order | Conflict Resolution |
|---|---|---|
| Gate_SDK development (standard) | `gate-sdk` → `user-prefs` | `gate-sdk` rules override `user-prefs` for any repo-specific directive |
| Gate_SDK development + YNP integration | `gate-sdk` → `user-prefs` → `saas-ynp` | `gate-sdk` wins on all transport/contract rules; `saas-ynp` wins on productization decisions |
| Guyana commodity + policy brief | `user-prefs` → `boss-eval` → `policy-arch` | `boss-eval` wins on financial modeling; `policy-arch` wins on regulatory framing |
| Cursor governance review for Gate_SDK | `gate-sdk` → `user-prefs` → `enforcement` | `gate-sdk` is source of truth for rules; `enforcement` audits against it |
| YNP platform + deal modeling | `user-prefs` → `saas-ynp` → `boss-eval` | `saas-ynp` wins on platform architecture; `boss-eval` wins on deal structure |
| Full L9 architecture session | `gate-sdk` → `user-prefs` → applicable domain pack | `gate-sdk` wins on all SDK/protocol decisions |

---

## Pack Summaries (reference only — do not restate pack rules here)

| pack_id | One-line description |
|---|---|
| `gate-sdk` | Full architecture kernel: transport contracts, routing laws, CI gates, validation, merge-rejection conditions, and agent checklists for Gate_SDK |
| `user-prefs` | Igor Beylin's operating profile: tech preferences, expertise map, tool affinities, response style, active project context |
| `boss-eval` | Structured evaluation engine for Guyana commodity operations: deal scoring, ROI, counterparty risk, trade finance workflow |
| `enforcement` | Cursor AI governance diagnostic: identifies enforcement gaps, rule violations, and compliance blind spots in Cursor-managed repos |
| `policy-arch` | Policy asset generator for Guyana regulatory context: institutional briefs, framework documents, regulatory instruments |
| `saas-ynp` | YNP v3 SaaS platform productization: chained-prompt generator for Scrap Management digital transformation, go-to-market scaffolding |

---

## Validation Method

```bash
# Confirm all 6 pack_ids are listed
grep -c 'pack_id' LOAD_PACK__REGISTRY.md
# Expected: >= 6

# Confirm no pack content is duplicated (registry references only)
# Each pack_id row MUST point to its source file — not restate its rules

# Confirm authority order is explicit
grep 'Authority order' LOAD_PACK__REGISTRY.md
```

---

## Known Unknowns

| Item | Status |
|---|---|
| JARVIS Phase 2/3 completion status | **Unknown** — referenced in `user-prefs` but not confirmed in any repo or upload |
| Cross-pack dependency on Gate_SDK version | **Unknown** — `saas-ynp` and `boss-eval` may reference L9 transport but no explicit version pin documented |
| Additional packs not yet generated | **Unknown** — this registry covers only packs generated through 2026-04-26 |
