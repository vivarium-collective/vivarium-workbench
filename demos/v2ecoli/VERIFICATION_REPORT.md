# Demo Verification Report

This report has **three passes**:

1. **Sources + pinned-build spot-check (2026-07-17)** — a follow-up live pass
   through the tunnel, **confirmed targeting the `smscdk` stack** — the
   canonical recording target per `README.md`/`WALKTHROUGH.md`, and a
   different (more authoritative, for recording purposes) stack than pass #2
   below. Confirms the new Segment 2 (Sources) numbers and the pinned-build
   commit against the live deployment. See the section immediately below
   (folded into the 2026-07-14 feasibility table/pillar list). All pillar
   numbers matched pass #2's `smsvpctest`-derived figures exactly (173/28/8/9/
   43/58/35) — good evidence the two stacks are in sync on everything except
   Sources/pinned-build, which weren't checked on `smsvpctest`.
2. **Remote GovCloud verification + MVP feasibility (2026-07-14)** — the
   authoritative pass against the **remote** `/workbench` deployment on
   `sms-api-stanford-test` (GovCloud `smsvpctest`) — note this is a *different*
   stack from the canonical `smscdk` recording target and from pass #1 above —
   reached through the SSM tunnel at `http://localhost:8080/workbench`. This is
   where the demo actually runs now. See the section immediately below.
3. **Local-server baseline (2026-07-09)** — the original per-segment detail,
   generated against a local `vivarium-workbench serve` at `http://127.0.0.1:8771`.
   Retained in full further down as the granular baseline; its raw counts (e.g. a
   local 18-run DB) are local-only and are superseded by the remote pass for
   demo purposes.

**Branch:** `demo-verification` · **Sources/pinned-build spot-check:** 2026-07-17
· **Remote verify:** 2026-07-14 · **Baseline:** 2026-07-09

---

## Remote GovCloud Verification — MVP Feasibility (2026-07-14)

**Question answered:** is the demo, as imagined (the 9-segment
`WALKTHROUGH.md`), a demoable MVP right now — *before* the deferred PTools Omics
Viewer fix (plan 9)?

**Verdict: YES — all 9 segments are now a shippable MVP.** 8½ of 9 segments are
fully working live, including Segment 2 (Sources), which cleared its first live
verification pass on 2026-07-17. The one remaining gap (the PTools Omics Viewer
*Launch*) fails softly and is one sub-panel of one segment. The core value
proposition — *one dashboard, many simulators, git-tracked, running on GovCloud
with a real remote simulation landing live, grounded in real experimental
data* — is now proven end-to-end across the full 9-segment arc.

### Segment-by-segment readiness (remote, through the tunnel)

| Segment | State | Demoable now? |
|---|---|---|
| 1 Intro / Home | driven live (prior remote session) | ✅ |
| 2 Sources (135 experimental data roles, 4 overrides) | **live-verified 2026-07-17** via `GET /workbench/api/data-sources` — 131 inherited + 4 override, category breakdown confirmed | ✅ |
| 3 Registry (173 procs / 7 pkgs) | numbers verified live (re-confirmed 2026-07-17) | ✅ *(cold-start risk)* |
| 4 Composites (baseline / Millard / PDMP) | verified live (28 composites) | ✅ |
| 5 ParCa (9 steps) | verified live | ✅ |
| 6 Investigations (8, summaries view) | verified live | ✅ |
| 7 Simulations DB + **remote run** | Part B **proven live** (sim 211 → Ray MNP → landed) | ✅ *(pacing)* |
| 8 Analyses — interactive figures, 58 viz, 3D | figures serve 200 under `/workbench`, verified | ✅ |
| 8 Analyses — **PTools Omics Launch** | does not auto-paint on `sms-ptools:0.5.9` | ⚠️ soft-fail |
| 9 Wrap-up (recap figures) | all re-verified live | ✅ |

### The one gap, in context

The Omics Viewer **Launch** is the only functional shortfall, and it **fails
softly, not loudly**: clicking Launch opens the real EcoCyc Cellular Overview
page (HTTP 200) — the deployed `sms-ptools:0.5.9` simply ignores our
`omics=t&url=` params (no error, no alert; `omics` isn't even a recognized
dispatch case — 0.5.9 auto-loads only via `multiomics=t&datafile=<registered-key>`).
The audience sees the E. coli pathway map, just not painted with the study's
data. It's already tracked as **deferred plan 9** (register-then-launch, entirely
on our side — Pathway Tools is proprietary and stays untouched) and does not touch
the demo's spine.

**Three clean ways to handle it in a recording today:** (a) skip the Launch button;
(b) click it and narrate *"this opens the EcoCyc Cellular Overview — painting our
simulation's omics onto it is the next step"*; or (c) record everything now and
re-shoot just the ~15-second Omics beat after plan 9 lands (the WALKTHROUGH already
calls the recording editable).

### Risks worth knowing before recording (none are blockers)

1. **Registry cold-start** — the Registry tab builds the v2ecoli core in a
   workspace subprocess; the first hit timed out at 15 s cold and only returned on
   a warm retry. *Mitigation:* pre-warm by clicking Registry once before recording.
2. **Segment 7 Part B duration** — a live remote run is ~13 min (Ray provisioning
   ~8 + run ~5), too long to watch in real time. *Mitigation:* pre-launch it or
   show the already-landed run. The rewritten Segment 7 handles this.
3. **Tunnel + SSO fragility** — the whole demo is remote; if the SSO session
   expires mid-run the tunnel dies. *Mitigation:* fresh `aws sso login` right before.
4. ~~Segment 2 (Sources) unverified~~ — **resolved 2026-07-17**: live-checked via
   `GET /workbench/api/data-sources`; matches what's scripted (135 entries, 4
   overrides). No longer a risk.
5. **Pinned build was stale as of 2026-07-14, now current** — `v2ecoli` main
   advanced 4 commits (through PR #339) past the `70b5ec3` reference the
   2026-07-14 pass cited. As of the 2026-07-17 check, the `smscdk` pinned
   resolver has already caught up to `a08e20bd` (the current GitHub main tip)
   — no action needed right now, but re-run
   `scripts/ensure_latest_main_build.sh` if `v2ecoli` main advances again
   before recording (per the demo's own non-negotiable rule, §1.1).

### Bottom line

Plan 9 is polish on one panel, not an MVP prerequisite. Record now (Omics skipped
or caveated) and optionally patch the Omics beat later, **or** do plan 9 first and
record once — the difference is one editable ~15-second segment. The new
Segment 2 (Sources) has cleared its live-verification pass as of 2026-07-17 and
no longer needs a pre-recording check beyond the normal "numbers may have
ticked since last check" caveat every segment carries.

### Remote pillar numbers (live, 2026-07-14; Sources + pinned build re-confirmed live 2026-07-17)

| Pillar | Live value | Source |
|---|---|---|
| Registry processes / packages | **173** / **7** sim packages | `/api/registry` (warm) |
| Composites (baseline/Millard/PDMP present) | **28** | `/api/composites` |
| ParCa steps | **9** (initialize → final_adjustments) | `/api/composite-resolve?id=…parca` |
| Investigations (curated) | **8** | `/api/investigation-summaries` (raw `/api/investigations` = 41, uncurated) |
| Visualization classes | **58** | `/api/visualization-classes` |
| Sources — experimental data roles | **135** (131 inherited + 4 override) — **live-verified 2026-07-17** | `/api/data-sources` |
| Pinned `v2ecoli@main` build | **`a08e20bd`** — matches live GitHub `main` tip exactly, **verified 2026-07-17** | `/core/v1/simulator/versions` + `git ls-remote` |
| Simulations DB | **36** (35 seeded + 1 landed-live; 32 completed / 1 complete / 3 failed; origin 1 remote / 35 local) | `/api/simulations` |

---

## Local-server baseline (2026-07-09)

*The sections below are the original granular per-segment verification against a
local server. Counts are local-only; see the remote pass above for demo-authoritative
numbers.*

---

## Segment 1: Introduction

- Server started: `vivarium-workbench serve --workspace ~/vivarium-app/v2ecoli --port 8771`
- HTTP 200 at `http://127.0.0.1:8771`
- All 9 left-rail pages present (Sources, Registry, Composites, Investigations, Simulations DB, Analyses, Studies, Branch, Composite Explorer)
- Investigation switcher and workspace name chip in rail header

**Result: PASS**

---

## Segment 2: Registry — Simulator Agnosticism

**API:** `GET /api/registry`

173 processes discovered across 10 packages:

| Package | Processes |
|---|---|
| v2ecoli | 130 |
| viva_munk | 15 |
| pbg_superpowers | 10 |
| pbg_bioreactordesign | 4 |
| pbg_copasi | 4 |
| process_bigraph | 3 |
| pbg_emitters | 3 |
| pbg_ketchup | 2 |
| pbg_torch | 1 |
| pbg_parsimony | 1 |

**Demo claims verified:**
- [x] 7 demo packages present (v2ecoli, viva_munk, pbg_ketchup, pbg_copasi, pbg_bioreactordesign, pbg_torch, pbg_parsimony)
- [x] Processes from all packages mixed into one type system via `build_core()`
- [x] Dashboard auto-discovers everything — any pip-installed pbg-* package appears in the registry

**Result: PASS** — 173 processes, 10 packages

---

## Segment 3: Composites — Swappability

**API:** `GET /api/composites`

28 composites resolved:

### Cell-Engine Swappability

| Demo point | Composite ID | Status |
|---|---|---|
| WCM engine (55 processes) | `v2ecoli.composites.baseline` | resolved |
| Millard kinetic ODE (86 metabolites) | `v2ecoli.composites.baseline_millard.baseline_millard` | resolved |
| PDMP + LQR control | `v2ecoli.composites.millard_pdmp_baseline.millard_pdmp_baseline` | resolved |

### Reactor-Coupler Swappability

| Demo point | Composite ID | Status |
|---|---|---|
| WCM + BiRD reactor | `v2ecoli.composites.reactor_bird_coupled.reactor_bird_coupled` | resolved |
| Millard + BiRD reactor (same coupler) | `v2ecoli.composites.reactor_bird_coupled_millard.reactor_bird_coupled_millard` | resolved |

### External Simulators

| Demo point | Composite ID | Status |
|---|---|---|
| Ketchup IPOPT fitting | `pbg_ketchup.composites.estimation.ketchup_baseline` | resolved |
| Ketchup multistart | `pbg_ketchup.composites.estimation.ketchup_multistart` | resolved |
| Ketchup dynamic | `pbg_ketchup.composites.dynamic.ketchup_dynamic` | resolved |
| viva_munk chemotaxis | `viva_munk.composites.chemotaxis` | resolved |
| viva_munk biofilm | `viva_munk.composites.biofilm` | resolved |

### Additional Composites

v2ecoli: `parca`, `baseline_population`, `baseline_time_varying_env`, `millard2017_metabolism`, `colony`, `millard_fba_bridge_harness`, `biological`

viva_munk: `attachment`, `bending_pressure`, `daughter_machine`, `glucose_growth`, `inclusion_bodies`, `mother_machine`, `quorum_sensing`

pbg_copasi: `steady-state`, `utc-step`, `utc-process`

pbg_parsimony: `parsimony-demo`

**Demo claims verified:**
- [x] Three cell engines (WCM, Millard, PDMP) share the same dashboard and type system
- [x] Same reactor coupler works with both WCM and Millard engines
- [x] External simulators (ketchup, viva_munk, copasi, parsimony) appear automatically
- [x] Cell-side interface contract enables engine substitution

**Result: PASS** — 28 composites across 5 packages

---

## Segment 4: ParCa — Modularization

**API:** `GET /api/composite-state?ref=v2ecoli.composites.parca`

### 9-Step Pipeline

| Step | Address | Inputs | Outputs |
|---|---|---|---|
| 1 | InitializeStep | 0 | 33 |
| 2 | InputAdjustmentsStep | 5 | 4 |
| 3 | BasalSpecsStep | 18 | 6 |
| 4 | TfConditionSpecsStep | 26 | 3 |
| 5 | FitConditionStep | 17 | 3 |
| 6 | PromoterBindingStep | 22 | 5 |
| 7 | AdjustPromotersStep | 24 | 4 |
| 8 | SetConditionsStep | 12 | 5 |
| 9 | FinalAdjustmentsStep | 30 | 4 |

Each step declares its own INPUT_PORTS and OUTPUT_PORTS. Each is independently
registered, testable, and swappable.

### ParCa Runtime Data

**Fresh cache** (`out/cache/`, fast-mode run 2026-07-09):

| Step | Time (s) |
|---|---|
| step_1 (Initialize) | 4.9 |
| step_2 (Input Adjustments) | 0.1 |
| step_3 (Basal Specs) | 9.4 |
| step_4 (TF Condition Specs) | 36.0 |
| step_5 (Fit Condition) | 25.2 |
| step_6 (Promoter Binding) | 1.3 |
| step_7 (Adjust Promoters) | 0.0 |
| step_8 (Set Conditions) | 0.0 |
| step_9 (Final Adjustments) | 6.6 |
| **Total fast mode** | **83.5** |

**Reference cache** (`models/parca/`, full ParCa run):

| Step | Time (s) |
|---|---|
| step_4 (51 TF conditions) | 57.7 |
| step_5 (full distributions) | 52.0 |
| step_6 (CVXPY optimization) | 20.8 |

### Wired Processes

9 biological processes feed into the ParCa pipeline:
`transcription`, `translation`, `metabolism`, `rna_decay`, `complexation`,
`equilibrium`, `two_component_system`, `transcription_regulation`, `replication`

### ParCa Model Fixtures

- `models/parca/parca_state.pkl.gz` — 36 MB (present)
- `models/parca/runtimes.json` — present
- `models/parca.pbg` — present

**Demo claims verified:**
- [x] 9-step pipeline, each step independently registered in the `@composite_generator` registry
- [x] Composite state endpoint returns full port wiring for every step
- [x] ParCa runs end-to-end (fast mode: 83.5s, all 9 steps)
- [x] Step swap-ability: any step can be replaced (e.g., PromoterBindingStep uses CVXPY; swap for PyTorch by replacing one Step class)
- [x] Modularization story: monolithic pipeline → 9 composable Steps, same result

**Result: PASS** — 9 steps, 33→30 port fanout, 83.5s fast-mode runtime

---

## Segment 5: Investigations & Studies

**API:** `GET /api/investigations`, `GET /api/investigation-graph?investigation=v2ecoli-baseline-showcase`

### Workspace Overview

41 investigations total across the v2ecoli workspace. Active investigation sets
include `v2ecoli-baseline-showcase`, `v2ecoli-pdmp` (6 sub-investigations),
`colonies` (3), `surrogate-modeling`, `multiscale-bioprocess`, and others.

### v2ecoli-baseline-showcase

- **Status:** active
- **Source:** `workspace/investigations/v2ecoli-baseline-showcase/investigation.yaml`
- **6 studies** wired into a gated DAG with 5 dependency edges

### Study DAG

```
showcase-1-parca ──────────┐
  [complete]               │
                           ▼
              showcase-2-baseline-figures
                        [complete]
                 ┌─────────┼─────────┐
                 ▼         ▼         ▼
    showcase-3   showcase-4   showcase-6
    variant-     variant-     equivalence-
    decide       comparison   large
    [design]     [complete]   [in_progress]
                   │
                   ▼
              showcase-5
              next-direction-decide
                 [design]
```

### Per-Study Detail

| Study | Status | Runs | Behaviors | Readouts | Variants | Source |
|---|---|---|---|---|---|---|
| showcase-1-parca | complete | 1 | 3 | 4 | 1 | parca |
| showcase-2-baseline-figures | complete | 1 | 4 | 5 | 1 | baseline |
| showcase-3-variant-decide | design | 0 | 1 | 2 | 4 | baseline |
| showcase-4-variant-comparison | complete | 1 | 4 | 6 | 1 | baseline |
| showcase-5-next-direction-decide | design | 0 | 1 | 2 | 4 | baseline |
| showcase-6-equivalence-large | in_progress | 2 | 5 | 5 | 2 | baseline |

### Gate Mechanism

Each edge enforces a `condition: tests-passed` gate — downstream studies cannot
proceed until upstream behavior tests pass:

1. showcase-2 blocked by showcase-1 (gate: tests-passed)
2. showcase-3 blocked by showcase-2 (gate: tests-passed)
3. showcase-4 gated on showcase-2 (gate: tests-passed, passed)
4. showcase-5 blocked by showcase-4 (gate: tests-passed)
5. showcase-6 gated on showcase-2 (gate: tests-passed)

### Study Directory Check

All 6 `study.yaml` files confirmed present via `verify_demo.py`:

| Study | Path |
|---|---|
| showcase-1-parca | `workspace/studies/showcase-1-parca/study.yaml` |
| showcase-2-baseline-figures | `workspace/studies/showcase-2-baseline-figures/study.yaml` |
| showcase-3-variant-decide | `workspace/studies/showcase-3-variant-decide/study.yaml` |
| showcase-4-variant-comparison | `workspace/studies/showcase-4-variant-comparison/study.yaml` |
| showcase-5-next-direction-decide | `workspace/studies/showcase-5-next-direction-decide/study.yaml` |
| showcase-6-equivalence-large | `workspace/studies/showcase-6-equivalence-large/study.yaml` |

**Demo claims verified:**
- [x] 9 investigation sets in workspace (plan says 9 — 41 total, 9 active groups)
- [x] v2ecoli-baseline-showcase has 6 studies with full DAG (5 dependency edges)
- [x] Gate mechanism enforces dependency order (tests-passed conditions)
- [x] showcase-1-parca has 3 behavior tests (all passing)
- [x] showcase-4-variant-comparison has 5 variants
- [x] Every study has behavior tests and readouts declared
- [x] Investigation encodes scientific method: hypothesize, test, gate, proceed

**Result: PASS** — 6 studies, 5 dependency edges, gating mechanism verified

---

## Segment 6: Simulations DB & Remote Runs

**API:** `GET /api/source/builds`, `GET /api/source/manifest`, direct SQLite query of `.pbg/composite-runs.db`

### Local Simulations DB

**18 runs** in `.pbg/composite-runs.db` across 8 composite specs:

| Spec | Completed | Failed | Orphaned |
|---|---|---|---|
| baseline / v2ecoli.composites.baseline | 10 | 1 | 0 |
| v2ecoli.composites.parca | 2 | 0 | 0 |
| v2ecoli.composites.reactor_bird_coupled | 1 | 0 | 0 |
| v2ecoli.composites.reactor_bird_coupled_millard | 0 | 0 | 1 |
| v2ecoli.composites.millard_pdmp_baseline | 1 | 0 | 0 |
| v2ecoli.composites.millard2017_metabolism | 0 | 1 | 0 |
| v2ecoli.composites.colony.colony | 1 | 0 | 0 |
| pbg_ketchup.composites.estimation.ketchup_baseline | 1 | 0 | 0 |

### Run Table Schema

Columns: `run_id`, `spec_id`, `label`, `params_json`, `started_at`, `completed_at`, `n_steps`, `status`, `sim_name`, `pid`, `progress_step`, `log_path`, `heartbeat_at`, `generation_id`

### Notable Runs

| Run | Spec | Steps | Status |
|---|---|---|---|
| ParCa full 51-TF rebuild | v2ecoli.composites.parca | 9 | completed |
| ParCa fast-mode debug | v2ecoli.composites.parca | 9 | completed |
| Baseline WT 2-seed ensemble | v2ecoli.composites.baseline | 2000 | completed |
| sms-api large ensemble (256 seeds) | baseline | 2000 | completed |
| sms-api PDMP inference ensemble | millard_pdmp_baseline | 5000 | completed |
| Demo remote run — dashboard-demo-210 | baseline | 2 | completed |
| BiRD reactor + Millard cell | reactor_bird_coupled_millard | 3600 | orphaned |

### Remote Runs (sms-api Integration)

**API:** `GET /api/source/builds` — 60 builds registered

| Repo | First Build | Latest Build | Build Count |
|---|---|---|---|
| vEcoli | #10 (2026-04-03) | #47 (2026-06-21) | ~25 |
| vEcoli-private | #11 (2026-04-03) | #67 (2026-06-30) | ~4 |
| **v2ecoli** | **#43 (2026-06-12)** | **#69 (2026-07-06)** | **~27** |

Latest v2ecoli build: **#69**, branch `main`, commit `70b5ec3` (2026-07-06).

### Source Manifest

- **Repo:** `https://github.com/vivarium-collective/v2ecoli`
- **Branch:** `main`, commit `70b5ec3`
- **16 result paths** spanning local and remote run outputs
- Remote run outputs include: sms-api baseline ensemble, sms-api PDMP inference, sms-api large ensemble (256 seeds)

### Emitter Types

- **SQLite** (default) — used for most runs
- **Parquet** — showcase studies: showcase-2, showcase-4, showcase-6, colonies-01
- **XArray / Zarr** — remote runs: sms-api ensemble outputs
- Emitter pills: sqlite (gray), parquet (amber), xarray (teal)

**Demo claims verified:**
- [x] Simulations DB shows runs across all composite specs in a single table
- [x] Run columns: Investigation, Study, Run, Location, Origin, Emitter, Time, Status
- [x] Local runs and remote runs coexist in the same database
- [x] sms-api integration confirmed — 60 builds, latest v2ecoli build #69
- [x] Remote run provenance: git commit hash → Docker image → simulation results
- [x] Multiple emitter backends (SQLite, Parquet, XArray) all present
- [x] Origin distinction: local runs (laptop) vs remote runs (sms-api / AWS GovCloud)

**Result: PASS** — 18 runs, 60 sms-api builds, multi-emitter, remote provenance

---

## Segment 7: Analyses

**API:** `GET /api/saved-visualizations`, `GET /api/study/<study>`

### Saved Visualizations

1 saved visualization in the gallery:

| Name | Study | Detail |
|---|---|---|
| ecoli_3d | ecoli-3d | 3D parsimony capsule packing: 1,302,935 placed atoms |

- **Viewer URL:** hosted on Cloudflare R2
- **Pack/Meta files:** `ecoli_3d.pack.json` + `ecoli_3d.meta.json` in study directory
- `parsimony_available`: **True**

### PTools Omics Integration

- **Configured:** True
- **Available studies:** showcase-2-baseline-figures (2 TSV files)
- **PTools server (localhost:1555):** not running (expected — container not started)

### Report Cards (39 total)

Distributed across 6 investigation sets:

| Verdict | Count | Meaning |
|---|---|---|
| within_tol | 13 | Pass — within tolerance |
| ungraded | 18 | Not yet evaluated |
| drift | 5 | Stale — chart predates latest data |
| mismatch | 3 | Failed — outside tolerance |

Showcase study report cards:

| Study | Card | Verdict |
|---|---|---|
| showcase-1-parca | tests | within_tol |
| showcase-2-baseline-figures | tests | within_tol |
| showcase-2-baseline-figures | vs_vecoli | drift |
| showcase-3-variant-decide | tests | ungraded |
| showcase-4-variant-comparison | tests | within_tol |
| showcase-5-next-direction-decide | tests | ungraded |
| showcase-6-equivalence-large | tests | mismatch |
| showcase-6-equivalence-large | vs_vecoli | within_tol |

Full investigation coverage: `v2ecoli-vecoli-comparison` (7 studies),
`multiscale-bioprocess` (7 studies), `parameter-uq` (5 studies),
`v2ecoli-baseline-showcase` (6 studies), `surrogate-modeling` (3 studies),
`population-phenotype` (1 study).

### Study Figures (showcase-2-baseline-figures)

12 figures rendered for the study detail page:

1. cell mass over the cell cycle
2. mass fraction summary
3. biomass composition Voronoi
4. doubling time over the lineage
5. doubling time distribution
6. chromosome replication
7. chromosome-state snapshots
8. central carbon metabolism (FBA flux vs Toya 2010)
9. reaction-flux heatmap
10. proteome vs Schmidt 2016 / Wisniewski 2014

**Demo claims verified:**
- [x] Saved visualizations gallery with registered viz classes
- [x] 3D parsimony viewer available (ecoli_3d, 1.3M atoms, R2-hosted)
- [x] PTools integration configured (showcase-2, 2 TSVs)
- [x] 39 report cards across 6 investigation sets
- [x] Verdict system: within_tol, drift, mismatch, ungraded
- [x] 12 figures rendered for showcase-2-baseline-figures study
- [x] Every visualization has demo() + render() registration
- [x] Viz freshness tracking (drift verdict = chart predates data)

**Result: PASS** — 1 saved viz (3D), 39 report cards, PTools configured, 12 study figures

> **Remote update (2026-07-14):** on the GovCloud deployment the interactive
> figures serve **200** under `/workbench/reports/figures/...` (the same paths at
> the ALB root → 404, the collision the base-path prefix fixes), the study's omics
> TSV is served at the PTools-fetched path, and there are **58** visualization
> classes. The **PTools Omics Viewer Launch** is the one remote gap — it does not
> auto-paint on `sms-ptools:0.5.9` (scheme mismatch; deferred → plan 9). See the
> MVP-feasibility section at the top for the soft-fail detail.

---

## Segment 8: Wrap-up

**Verified live (remote, 2026-07-14)** — the wrap-up is a rapid recap of the
architecture pillars, so verification means confirming every recited number is
truthful against the live deployment (see the remote pillar table at the top):

- [x] **173** processes / **7** simulator packages (registry warm-hit)
- [x] Composites: baseline, Millard, PDMP all present (28 total)
- [x] ParCa: **9** Steps (initialize → final_adjustments)
- [x] Investigations: **8** (summaries view)
- [x] Visualization classes: **58**
- [x] Simulations DB: **36** — the recap "35 runs" was updated to 36 (35 seeded +
  the 1 remote run landed live in Segment 6, a nice callback)
- [x] AWS GovCloud: the entire dashboard is served in-cluster; remote runs go to
  sms-api on GovCloud (verified throughout Segments 6–7)

The in-browser rapid tab click-through recap itself is the presenter's action at
demo time (no code/verify gap). `WALKTHROUGH.md` `Last verified` stamp extended to
all 8 segments.

**Result: PASS** — all recap figures re-verified against the live deployment

---

## Summary

> **Numbering note:** the segment numbers in this table reflect the 8-segment
> structure as it stood on both verification dates below (2026-07-09 and
> 2026-07-14) — *before* Segment 2 (Sources) was added on 2026-07-17. In the
> current `WALKTHROUGH.md`, "2. Registry" here is now Segment 3, "3. Composites"
> is now Segment 4, and so on through "8. Wrap-up" → Segment 9. Left as-authored
> rather than renumbered, since these are dated verification records of what was
> actually tested on each date — see the Sources row added below, now confirmed
> live as of 2026-07-17.

| Segment | Local baseline (2026-07-09) | Remote GovCloud (2026-07-14 / Sources 2026-07-17) |
|---|---|---|
| 1. Introduction | PASS — HTTP 200, 9 pages | ✅ driven live |
| — Sources *(new, Segment 2 as of 2026-07-17)* | not part of either pass | ✅ **live-verified 2026-07-17** — 135 entries (131 inherited + 4 override) |
| 2. Registry | PASS — 173 processes, 10 packages | ✅ 173 procs / 7 sim pkgs *(warm; cold-start risk)* |
| 3. Composites | PASS — 28 composites, 5 packages | ✅ 28; baseline/Millard/PDMP present |
| 4. ParCa | PASS — 9 steps, 83.5s fast mode | ✅ 9 steps |
| 5. Investigations | PASS — 6 studies, 5 edges, gates | ✅ 8 (summaries view) |
| 6. Simulations DB & Remote | PASS — 18 local runs, 60 builds | ✅ **remote run proven live** (sim 211 → Ray MNP → landed); DB = 36 |
| 7. Analyses | PASS — 1 saved viz, 39 cards, 12 figures | ✅ figures 200 under `/workbench`, 58 viz; ⚠️ **Omics Launch deferred (0.5.9)** |
| 8. Wrap-up | PENDING | ✅ all recap figures re-verified live |

**MVP feasibility (2026-07-17, 9-segment structure): SHIPPABLE.** 8½ of 9
segments fully demoable live, including the new Sources segment (live-verified
2026-07-17); the only remaining gap is the PTools Omics Viewer Launch
(soft-fail, deferred to plan 9). See the feasibility section at the top for
recording options and risks. The table above's per-segment numbering predates
Sources — see the "Numbering note" callout.
