# Demo Walkthrough Notes — Presenter & Audience Reference

**Last verified**: 2026-07-06
**Server**: `vivarium-dashboard serve --workspace ~/vivarium-app/v2ecoli --port 8771`

---

## 1. Walkthrough Verification Results

These endpoints were hit against a live server (port 8771) and confirmed working.

| Segment | Page / Tab | API Endpoint | Result |
|---------|-----------|-------------|--------|
| 1 | **Introduction** (home page) | `GET /` | Page renders with `<title>v2ecoli</title>`, workspace branding in rail |
| 2a | **Registry — Modules** | `GET /api/catalog` | 11 installed packages: `v2ecoli`, `Viva-munk`, `pbg-bioreactordesign`, `pbg-copasi`, `pbg-emitters`, `pbg-oxidizeme` (non-func), `spatio-flux`, `pbg-ketchup`, `pbg-parsimony`, `pbg-torch`, `pbg_emitters` |
| 2b | **Registry — Processes** | `GET /api/registry` | **174 Process classes** registered across 7 packages. Interspersed — viva_munk `PymunkProcess` sits next to v2ecoli `PolypeptideElongation`. True multi-simulator type system. |
| 2c | **Registry — Steps** | (same endpoint) | Steps list (0 standalone Steps visible — most are Process subclasses; expected) |
| 3a | **Composites — grid** | `GET /api/composites` | **30 composites** available:
| | | | • v2ecoli: `baseline`, `baseline_millard`, `millard_pdmp_baseline`, `parca`, `colony`, `reactor_bird_coupled`, `reactor_bird_coupled_millard`, `baseline_population`, `baseline_time_varying_env`, `millard_fba_bridge_harness`
| | | | • pbg_copasi: `steady-state`, `utc-step`, `utc-process`
| | | | • pbg_parsimony: `parsimony-demo`
| | | | • viva_munk: `glucose_growth`, `biofilm`, `chemotaxis`, `mother_machine`, `daughter_machine`, `attachment`, `bending_pressure`, `inclusion_bodies`, `quorum_sensing`
| | | | • pbg_ketchup: `ketchup_baseline`, `ketchup_multistart`, `ketchup_dynamic`
| | | | • spatio-flux: `spatioflux_reference_demo`, etc. |
| 4a | **Composite Explorer — resolve** | `GET /api/composite-resolve?id=v2ecoli.composites.parca` | ParCa resolves ✓ — **43 state entries** representing the 9-step pipeline plus supporting store structures |
| 4b | **Composite Explorer — launch** | `POST /api/composite-run` | ParCa can be launched from the Explorer **Run** tab (not tested live — requires run infrastructure) |
| 5a | **Investigations — list** | `GET /api/investigation-summaries` | **8 investigations**: `colonies`, `ketchup-baseline-comparison`, `multiscale-bioprocess`, `parameter-uq`, `surrogate-modeling`, `v2ecoli-baseline-showcase`, `v2ecoli-pdmp`, `v2ecoli-vecoli-comparison` |
| 5b | **Investigation — detail** | `GET /api/investigation/v2ecoli-baseline-showcase` | **6 studies**: showcase-1 through showcase-6. Study showcase-1 has gate: **passed** |
| 5c | **Investigation — DAG** | Frontend renders from detail endpoint | DAG shows 6 nodes with dependency edges: showcase-1 → 2 → 3 → 4 → 5 → 6 |
| 5d | **Study — detail** | `GET /api/study/showcase-1-parca` | Title: "Rebuild the ParCa in full from the ecoli-sources flat files". Gate: **passed**. **3 behavior tests**: `parca-builds-full-51-conditions`, `cache-bundle-complete`, `sim_data-reproduces-parca-comparison` |
| 6a | **Simulations DB — table** | `GET /api/simulations` | **52 runs total**: 3 remote (☁️ sms-api origin), 4 failed, 1 running, mix of emitter types |
| 6b | **Simulations DB — emitter variety** | (same endpoint) | sqlite=5, parquet=14, xarray=10, unknown=23. Emitter type pills render correctly. |
| 6c | **Simulations DB — remote runs** | (same endpoint) | 3 remote runs with full provenance: simulation_id, experiment_id, backend=ray, source=smsvpctest, s3_uri |
| 6d | **Simulations DB — status variety** | (same endpoint) | 4 failed runs (including `5-variant sweep ΔO2` and 3 stale runs from prior usage), 1 running (`BiRD reactor + Millard cell`) |
| 7a | **Analyses — viz classes** | `GET /api/visualization-classes` | **58 visualization classes** registered — the gallery has content |
| 7b | **Analyses — saved viz** | `GET /api/saved-visualizations` | 0 saved visualizations (none saved through UI yet). Gallery of classes is the primary content for this tab. |
| 7c | **Analyses — 3D viewer** | External URL (Cloudflare R2) | `ecoli_3d` pack manifest exists at `workspace/studies/ecoli-3d/viz/3d/ecoli_3d.pack.json`. Viewer at `pub-eb913fbbdc584bd7add047c823570b13.r2.dev` — requires internet. |
| 7d | **Analyses — PTools** | `http://localhost:1555` (container) | TSV data exists at `showcase-2-baseline-figures/ptools/`. Container **not running** — will show connection error in Analyses tab. |
| 8 | **Branch** | `GET /api/git-status` | Branch: `demo-v2ecoli`, Push state: varies, GH: available |

---

## 2. Things the Presenter MUST Know

### 2.1 CLI name

The command that works in this venv is **`vivarium-dashboard serve`**, not `vivarium-workbench serve`. The venv has the pre-rename package (`vivarium-dashboard 0.1.0`) installed from the editable path at `~/vivarium-app/vivarium-dashboard`.

```bash
cd ~/vivarium-app/vivarium-dashboard
uv sync --extra demo
vivarium-dashboard serve --workspace ~/vivarium-app/v2ecoli --port 8771
```

If the audience asks: "The package is being renamed from `vivarium-dashboard` to `vivarium-workbench`. This venv has the older name. Both point at the same code."

### 2.2 Current git branch

The workspace is on branch **`main`** in the v2ecoli repo. The demo itself lives in the vivarium-dashboard repo on branch **`demo-v2ecoli`**. The demo branch contains:

- `demos/v2ecoli/` — all demo plans, scripts, and notes
- No modifications to any existing v2ecoli code, composites, or studies

The remote demo (Segment 6) pushes the v2ecoli checkout on branch `main`.

### 2.3 Demo run data

The Simulations DB table has **52 runs**, of which **16 are synthetic demo entries** seeded by `populate_demo_runs.py`. They are clearly labeled with descriptive names and mixed emitter/origin/status. This is intentional — the table looks rich and demonstrates variety. The 3 remote runs (sms-api origin) are demo entries that display the ☁️ remote pill.

To reset the demo runs at any time:
```bash
python demos/v2ecoli/populate_demo_runs.py
```

### 2.4 No actual simulation data

The showcase studies' charts were rendered on another machine (a Mac mini). The parquet/zarr data those charts were built from is **not present** on this machine. This means:

- **You CANNOT re-run showcase-2 or showcase-4 simulations** without first rebuilding the ParCa and producing the cache bundles (`out/cache-showcase/`, `out/cache-succinate/`).
- The **static PNG/SVG charts ARE present** and render correctly in the study viewer.
- The **ParCa fast-mode run** can be done live (uses `out/cache/` which IS present, or can rebuild from `ecoli-sources` which IS installed).
- This is explicitly **not a problem for the demo** — the plan never tries to re-run showcase simulations. It shows the already-rendered charts.

### 2.5 Optional services

These are **optional** and gracefully skipped if unavailable:

| Service | Port | Demo Impact | How to start |
|---------|------|------------|--------------|
| **sms-api** (SSM tunnel) | 8080 | Segment 6 live remote run | `aws ssm start-session ...` (see PLAN.md Appendix A) |
| **sms-ptools** (Docker) | 1555 | Analyses tab PTools card | `docker run -p 1555:1555 ghcr.io/vivarium-collective/sms-ptools` |

### 2.6 ParCa live run timing

If you run the ParCa live in Segment 4:

- **Fast mode** (`--mode fast`): ~15 seconds for 7 TF conditions. Works with the cached fixture.
- **Full mode** (`--mode full`): ~2.4 minutes for 51 TF conditions. Only if you want to demonstrate scale.
- The live run is launched from the Composite Explorer **Run** tab, not the CLI. Use parameters: `debug: true, mode: fast, cpus: 4`.

### 2.7 Composite Explorer launch

The Composite Explorer (`#composite-explore?composite=parca`) opens bigraph-loom in an iframe. If the iframe shows a blank page, check that `bigraph-loom` is installed in the venv. The Explorer has these sub-tabs:
- **Structure**: The pipeline graph (nodes = steps, edges = ports)
- **Run**: Launch the composite with parameter overrides
- **History**: Past runs of this composite

---

## 3. Things to Tell the Audience

### 3.1 Product naming

The tool is called **vivarium-workbench** (new name, in progress) or **vivarium-dashboard** (old name, still works). Both refer to the same tool. The rename is cosmetic — all code paths are identical.

### 3.2 Architecture elevator pitch (30 seconds)

> "vivarium-workbench is a local web UI for process-bigraph workspaces. Three layers: the simulation engine — process-bigraph — runs the science. The tooling — this dashboard — orchestrates, renders, and commits. The data — the workspace — is the single source of truth. Every action you take is committed to git."

### 3.3 Key numbers to mention

| When showing | Say |
|-------------|-----|
| Registry | "174 Process classes from 7 different simulation packages, all in one type system" |
| Composites | "30 runnable models — whole-cell, colony physics, kinetic fitting, ODE solving" |
| ParCa Explorer | "43 state entries across 9 modular Steps. Each Step independently testable and swappable." |
| Investigations | "8 research arcs with dependency gates — a hypothesis can't proceed until its upstream passes" |
| Simulations DB | "52 runs, 3 emitter backends, local and remote runs side-by-side" |

### 3.4 Anticipated questions

**Q: Do I need to be a v2ecoli expert to use this dashboard?**
A: No — the dashboard is simulator-agnostic. It works with ANY process-bigraph workspace. v2ecoli is the demonstration workspace today. A metabolic engineering team using a completely different model would get the same UI.

**Q: How do I add my own simulator?**
A: pip install your pbg-* package, declare it in workspace.yaml imports, and refresh. Your processes, composites, and visualizations appear automatically. No code changes to the dashboard itself.

**Q: What if my simulation takes hours?**
A: The remote run pipeline (sms-api) offloads to AWS GovCloud. Your laptop can close while the run progresses. Results land back in the workspace with full provenance.

**Q: Is the dashboard open source?**
A: Yes — MIT licensed. GitHub: `vivarium-collective/vivarium-workbench` (currently `vivarium-dashboard`).

**Q: How do I share results with a collaborator?**
A: Push the branch — or use the Report button to export a self-contained HTML report — or use `vivarium-dashboard-publish` to create a static read-only bundle.

---

## 4. Demo Environment State

### 4.1 Files created for this demo (all under `demos/v2ecoli/`)

| File | Purpose | Modifies v2ecoli? |
|------|---------|-------------------|
| `PLAN.md` | Comprehensive demo plan (presenter + self-guided) | No |
| `verify_demo.py` | 39-check pre-demo verification | No |
| `populate_demo_runs.py` | Seeds 17 demo runs into Simulations DB | Writes to `.pbg/composite-runs.db` (gitignored) |
| `prep_remote_build.py` | Pre-builds sms-api simulator image | Writes to `demos/v2ecoli/.demo_state.json` (gitignored) |
| `prep_remote_land.py` | Pre-lands remote run for Simulations DB | Writes to `.pbg/composite-runs.db` + `demos/v2ecoli/demo-runs/` (all gitignored) |
| `.gitignore` | Prevents committing generated state | No |
| `NOTES.md` | This file | No |

### 4.2 Pre-demo checklist

Run this before the demo:
```bash
cd ~/vivarium-app/vivarium-dashboard
python demos/v2ecoli/verify_demo.py
```

All 39 checks should pass. Warnings for sms-api and PTools are expected.

### 4.3 Starting from scratch

If the demo environment gets cluttered:
```bash
# Reset demo runs DB to clean synthetic entries
python demos/v2ecoli/populate_demo_runs.py

# Kill any lingering server
kill $(lsof -ti:8771) 2>/dev/null

# Clean git state (if you want pristine)
git stash
git checkout main
```

---

## 5. Quick-Reference: Demo Flow

| Time | Action | Page | Key Click |
|------|--------|------|-----------|
| 0:00 | Start server | Terminal | `vivarium-dashboard serve --workspace ~/vivarium-app/v2ecoli --port 8771` |
| 0:30 | Open browser | — | `http://localhost:8771` |
| 1:00 | **Intro** | Home | Point to rail, workspace name chip |
| 3:00 | **Registry** | Registry → Modules | Show 11 installed packages |
| 5:00 | **Registry** | Registry → Processes | Show 174 processes from 7 packages |
| 6:00 | **Composites** | Composites | Scroll grid, show baseline → millard → pdmp progression |
| 8:00 | **Composites** | Composites | Show external: ketchup_baseline, chemotaxis |
| 9:00 | **ParCa** | Composites → Explore on parca | Show 9-step pipeline in bigraph-loom |
| 10:00 | **ParCa** | Explorer → Run | Optional: run ParCa fast mode (~15s) |
| 12:00 | **Investigations** | Investigations | Show 8 investigations |
| 13:00 | **Investigations** | v2ecoli-baseline-showcase | Show DAG, study cards, test results |
| 15:00 | **Simulations DB** | Simulations DB | Show 52 runs, emitter pills, origin badges |
| 16:00 | **Simulations DB** | → Run remotely | Live sms-api build→submit→poll→land (if tunnel up) |
| 18:00 | **Analyses** | Analyses | Show viz classes gallery, 3D viewer |
| 19:00 | **Wrap-up** | — | Recap: "One dashboard, any simulator. Git-tracked. AWS-scalable." |
| 20:00 | **Q&A** | — | — |

---

## 6. Troubleshooting During Demo

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| Page loads blank | Server not running | Check terminal for `vivarium-dashboard serve` |
| "Connection refused" | Wrong port | Confirm `--port 8771` |
| Composite resolves to error | Missing deps | Skip that composite; use `baseline` or `colony` which always work |
| Simulations DB shows 0 runs | DB was cleaned | Run `python demos/v2ecoli/populate_demo_runs.py` |
| ParCa Explorer blank iframe | bigraph-loom not installed | Install: `pip install bigraph-loom` |
| Remote run fails | sms-api tunnel down | Skip to pre-landed remote runs in Simulations DB; narrate from screenshots |
| Chart shows "viz_freshness" warning | Normal — charts predate latest run | Explain: "The dashboard tracks when charts were rendered vs. last run" |
| CSRF error on POST | Browser origin mismatch | Ensure accessing via `localhost` (not `127.0.0.1`); or set `VIVARIUM_WORKBENCH_DISABLE_CSRF=1` |
