# Dashboard Demo — README

A comprehensive demonstration of **vivarium-workbench** (a.k.a. vivarium-dashboard) — the local web UI for [process-bigraph](https://github.com/vivarium-collective/process-bigraph) workspaces — using the **v2ecoli** whole-cell model workspace.

**Branch**: `demo-v2ecoli` (in the vivarium-dashboard repo)
**Duration**: ~20 min (live) / ~45 min (self-guided)
**Prerequisites**: checkout of `vivarium-collective/v2ecoli` at `~/vivarium-app/v2ecoli`, plus `uv`

---

## Quick Start

```bash
cd ~/vivarium-app/vivarium-dashboard
uv sync --extra demo

# Verify everything is ready (39 checks)
python demos/v2ecoli/verify_demo.py

# Seed demo runs for the Simulations DB tab
python demos/v2ecoli/populate_demo_runs.py

# Start the dashboard (pointing at the v2ecoli workspace)
vivarium-dashboard serve --workspace ~/vivarium-app/v2ecoli --port 8771
# Open http://localhost:8771
```

---

## What This Demo Covers

| Tab / Feature | What you'll see | Key talking point |
|---|---|---|
| **Registry** | 174 Process classes from 7 simulator packages co-existing in one type system | "The dashboard is simulator-agnostic. Install a new package and its types appear automatically." |
| **Composites — swappability** | 3 cell engines (WCM, Millard ODE, PDMP) sharing the same BiRD reactor coupler | "The cell-side interface contract makes engines drop-in replaceable. Same workflow, any engine." |
| **Composites — multi-simulator** | v2ecoli + ketchup kinetic fitting + viva_munk colony physics + copasi ODE — all launchable | "One dashboard, any simulator. Composite → Run → View results." |
| **Composite Explorer — ParCa** | 9-step modular pipeline rendered as a connected graph in bigraph-loom | "ParCa used to be monolithic. Now 9 independent Steps, each testable and swappable." |
| **Investigations** | 6-study showcase DAG with pass/fail gates between studies | "Investigations encode the scientific method: hypothesize, test, gate, proceed." |
| **Simulations DB** | 52 runs — local + remote, 3 emitter types (SQLite, Parquet, XArray), status variety | "Every run is git-traceable. Local laptop and AWS GovCloud runs appear side-by-side." |
| **Simulations DB — remote** | 3 sms-api runs with ☁️ origin pills, full provenance (simulation_id, S3 URI, backend) | "Extensibility: any simulator, any backend, any scale. Unified by git provenance." |
| **Analyses** | 58 registered visualization classes + 3D parsimony viewer + PTools omics integration (optional) | "Every visualization is a registered class with demo() + render() — preview before you run." |
| **Branch** | Git status with push state, branch tracking, PR integration | "Every dashboard action is committed to git. Full audit trail." |

---

## File Layout

```
demos/v2ecoli/
├── README.md               ← This file
├── PLAN.md                 ← Full demo plan (presenter script + self-guided guide, 596 lines)
├── NOTES.md                ← Presenter quick reference (walkthrough table, key numbers, Q&A, troubleshooting)
├── verify_demo.py          ← Pre-demo verification (39 checks, read-only)
├── populate_demo_runs.py   ← Seeds 16 synthetic runs into Simulations DB (idempotent)
├── prep_remote_build.py    ← Pre-builds v2ecoli simulator image on sms-api
├── prep_remote_land.py     ← Pre-lands an sms-api remote run for Simulations DB
└── .gitignore              ← Keeps `.demo_state.json`, `demo-runs/`, `downloads/` out of git
```

---

## Demo Flow (8 Segments)

| # | Segment | Page / Tab | Duration |
|---|---------|-----------|----------|
| 1 | Introduction | Home page, rail overview | 2 min |
| 2 | Simulator agnosticism | **Registry** → Modules + Processes | 3 min |
| 3 | Engine swappability | **Composites** grid | 3 min |
| 4 | ParCa modularization | **Composite Explorer** → parca | 2 min |
| 5 | Investigation DAG | **Investigations** → v2ecoli-baseline-showcase | 3 min |
| 6 | Simulations DB + remote | **Simulations DB** + live sms-api run | 3 min |
| 7 | Visualizations | **Analyses** | 2 min |
| 8 | Wrap-up & Q&A | — | 2 min |

For the detailed presenter script with narration, actions, and talking points, see [PLAN.md](PLAN.md) Section 4.
For a quick-reference table with expected API results and key numbers, see [NOTES.md](NOTES.md) Section 1.

---

## Decoupling Principle

**The demo assets never modify existing v2ecoli files.** All new artifacts live under `demos/v2ecoli/`. Existing composites, studies, investigations, and the workspace configuration are consumed read-only.

| What the demo creates | Where | Git status |
|---|---|---|
| Plans, scripts, notes | `demos/v2ecoli/` | Tracked (committed to `demo-v2ecoli` branch) |
| Synthetic run entries | `.pbg/composite-runs.db` | Gitignored (already in `.gitignore`) |
| Remote build state | `demos/v2ecoli/.demo_state.json` | Gitignored (demo `.gitignore`) |
| Downloaded remote results | `demos/v2ecoli/demo-runs/` | Gitignored (demo `.gitignore`) |

---

## Scripts Reference

### `verify_demo.py`
Pre-demo verification. **Always run this before a demo.**
```bash
python demos/v2ecoli/verify_demo.py
```
39 checks across: package imports, composite resolution, study directories, ParCa cache, git state, dashboard CLI, Simulations DB demo data, cell-side contract. Passes = ready. Warnings for sms-api and PTools are expected unless services are running.

### `populate_demo_runs.py`
Seeds the Simulations DB with 16 synthetic run entries. Idempotent — deletes and recreates.
```bash
python demos/v2ecoli/populate_demo_runs.py
```
Creates entries with: 3 emitter types (SQLite/Parquet/XArray), 3 remote ☁️ runs, 1 running, 1 failed, varied timestamps. Labeled clearly as demo data.

### `prep_remote_build.py` and `prep_remote_land.py`
One-time setup for the live sms-api remote demo. Requires an SSM tunnel to AWS GovCloud (`localhost:8080`). See [PLAN.md](PLAN.md) Appendices A–B for tunnel setup and fallback plan.

---

## Environment Notes

- **CLI entry point**: `vivarium-dashboard serve` (or `vivarium-workbench serve` — same code)
- **Current branch**: `demo-v2ecoli` in the vivarium-dashboard repo. This is NOT a branch of v2ecoli.
- **v2ecoli dependency**: The demo requires v2ecoli installed as a local editable package. `uv sync --extra demo` resolves it from `../v2ecoli`.
- **Demo runs**: Synthetic (clearly labeled). Real simulation output lives on a different machine; static charts are committed and render fine
- **sms-api**: Optional. Simulations DB already has pre-seeded remote entries showing ☁️ pills. Live remote demo needs SSM tunnel
- **PTools**: Optional. TSV data exists at `workspace/studies/showcase-2-baseline-figures/ptools/`. Needs sms-ptools Docker container on `:1555`

---

## Customization

To adapt this demo for a different workspace:
1. Update `PACKAGES` and `SELECTED_COMPOSITES` in `verify_demo.py` to match the target workspace's imports
2. Update the study slugs and composite IDs in `populate_demo_runs.py` to reference the target workspace's studies
3. Replace the 8-segment narrative in `PLAN.md` Section 4 with the target workspace's story
4. Update the key numbers and Q&A in `NOTES.md`

The dashboard itself is workspace-agnostic — no dashboard code changes needed.
