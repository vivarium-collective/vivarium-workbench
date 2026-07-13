# vivarium-workbench Dashboard Demo — Plan

**Status**: In verification — 2026-07-09
**Last updated**: 2026-07-09

---

## 1. Overview

### 1.1 What this demo demonstrates

`vivarium-workbench` is a local web UI for [process-bigraph](https://github.com/vivarium-collective/process-bigraph) workspaces. This demo walks through every major feature of the dashboard using the `v2ecoli` workspace as the demonstration substrate. The demo is designed to serve two audiences:

| Mode | Audience | Duration | Format |
|------|----------|----------|--------|
| **Presenter script** | Live audience (demo day, stakeholder meeting, conference) | ~20 minutes | Screen-shared narrated walkthrough with talking points |
| **Self-guided guide** | Individual user (new team member, curious engineer) | ~45 minutes | Detailed step-by-step with expected outputs and troubleshooting |

### 1.2 Decoupling principle

**The demo assets never modify existing v2ecoli content.** All new artifacts (prep scripts, demo-specific studies, verification tools) live under `demos/v2ecoli/` and reference existing composites, studies, and investigations as read-only data. This means:

- No edits to existing `study.yaml`, `workspace.yaml`, composite generators, or investigations
- No commits to the v2ecoli workspace git history on behalf of the demo
- The demo can be torn down without affecting the workspace

### 1.3 What we cover

| Feature | Tab / Page | Key demo point |
|---|---|---|
| Simulator agnosticism | **Registry** | 7 installed pbg-* packages co-existing in one type system |
| Swappability | **Composites** | 3 cell engines (WCM, Millard, PDMP) share the same reactor coupler |
| ParCa modularization | **Composite Explorer** | 9-step pipeline, each step independently registered |
| Investigation DAG & gating | **Investigations** | 6-study showcase with pass/fail gates and dependency edges |
| Study management & charts | **Studies** | Variant sweeps, behavior tests, self-contained reports |
| Simulations DB & remote runs | **Simulations DB** | Local + remote runs side-by-side; live sms-api build→submit→poll→land |
| Visualizations | **Analyses** | Saved viz gallery, 3D viewers, PTools omics integration |
| Audit trail | **Branch** | Git commits, PR workflow |

---

## 2. Prerequisites

### 2.1 Hardware

- macOS or Linux with ≥16 GB RAM
- ≥5 GB free disk (for ParCa cache, run outputs, sms-api workspace tarballs)
- Network access to GitHub and (for remote demo) AWS GovCloud via SSM

### 2.2 Software

| Component | Minimum version | How to verify |
|---|---|---|
| Python | 3.12 | `python3 --version` |
| uv | 0.4+ | `uv --version` |
| Git | 2.40+ | `git --version` |
| AWS CLI (remote demo) | 2.15+ | `aws --version` |
| Session Manager plugin (remote demo) | latest | `session-manager-plugin --version` |
| A modern browser | Chrome/Firefox/Safari current | — |

### 2.3 Repositories

| Repo | Purpose | Expected location |
|---|---|---|
| `vivarium-collective/vivarium-workbench` | The dashboard itself | `~/vivarium-app/vivarium-dashboard/` |
| `vivarium-collective/v2ecoli` | The demo workspace (read-only for this demo) | `~/vivarium-app/v2ecoli/` |
| `vivarium-collective/sms-api` | Remote run backend | Not cloned locally; reached via SSM tunnel |

### 2.4 v2ecoli dependency

The demo runs from the **vivarium-dashboard** repo and requires v2ecoli as a local editable dependency.
Both repositories must be checked out as siblings:

```
~/vivarium-app/
├── vivarium-dashboard/   # this repo (on branch demo-v2ecoli)
└── v2ecoli/              # v2ecoli model on branch main
```

Install everything with:
```bash
cd ~/vivarium-app/vivarium-dashboard
uv sync --extra demo
```

This installs vivarium-workbench plus v2ecoli (from `../v2ecoli` in editable mode) and all transitive
dependencies (process-bigraph, bigraph-schema, pbg_ketchup, pbg_copasi, pbg_parsimony,
pbg_bioreactordesign, pbg_torch, viva_munk, pbg_emitters, etc.).

Verify:
```bash
python -c "import v2ecoli; import viva_munk; import pbg_ketchup; import pbg_copasi; print('OK')"
```

### 2.5 ParCa cache (optional but recommended)

The existing `models/parca/parca_state.pkl.gz` fixture ships with the repo. If it's missing or stale, re-run the ParCa in fast mode once:

```bash
cd ~/vivarium-app/v2ecoli
source .venv/bin/activate
v2ecoli-parca --mode fast --cpus 4 --output out/cache-demo
```

This is read from `models/parca/` and `out/cache/` by the composites — the demo does not modify these.

### 2.6 sms-api tunnel (for remote demo segments)

```bash
# Replace <INSTANCE_ID> with the actual GovCloud EC2 instance ID
aws ssm start-session \
  --region us-gov-west-1 \
  --target <INSTANCE_ID> \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["8080"],"localPortNumber":["8080"]}'
```

Verify:
```bash
curl -s http://localhost:8080/core/v1/simulator/versions | python -m json.tool | head -5
```

The demo plan includes a fallback for when the tunnel is unavailable (see Appendix B).

---

## 3. Demo Preparation (one-time setup)

All preparation scripts live in `demos/v2ecoli/` and never touch existing v2ecoli files.

### 3.1 Quick verification

Run the prep script to check everything is in order:

```bash
cd ~/vivarium-app/vivarium-dashboard
python demos/v2ecoli/verify_demo.py
```

This script (to be created alongside this plan) checks:
- All required imports resolve
- All showcase composites resolve via `build_composite()` (read-only, no side effects)
- Expected study directories exist
- ParCa cache is present
- Dashboard server can start (starts and immediately stops)

### 3.1b Iterative Segment Verification Protocol

After the quick verification passes, each presenter segment (1–8) is verified
programmatically against the live dashboard server. Results are recorded in
`demos/v2ecoli/VERIFICATION_REPORT.md`, creating a static, tangible, provable
record of functionality. The protocol for each segment is:

1. **Start the server**: `vivarium-workbench serve --workspace ~/vivarium-app/v2ecoli --port 8771`
2. **Hit the relevant API endpoint(s)** to confirm the data backing that segment's claims
3. **Record results** in `VERIFICATION_REPORT.md` under that segment's heading
4. **Update `SAVE_SLOT.md`** and **`NEXT_STEPS.md`** to reflect current progress
5. **Proceed** to the next segment only after the current one passes

The `VERIFICATION_REPORT.md` serves as the final deliverable: a reviewer can open
it and see that every claim in the presenter script was verified against live,
running code.

### 3.2 Pre-build sms-api simulator image (remote demo)

The remote-run demo needs at least one pre-built image on sms-api so the demo doesn't wait for a build:

```bash
# From the vivarium-dashboard repo
cd ~/vivarium-app/vivarium-dashboard

# This registers the current HEAD on sms-api (async build, ~4 min for first build)
python demos/v2ecoli/prep_remote_build.py
```

The script pushes the current branch, registers it on sms-api, and polls until the image is ready. The resulting `simulator_id` is saved to `demos/v2ecoli/.demo_state.json` so subsequent demos reuse it without re-building.

### 3.3 Pre-land a remote run (Simulations DB tab)

To populate the Simulations DB with remote runs:

```bash
cd ~/vivarium-app/vivarium-dashboard
python demos/v2ecoli/prep_remote_land.py
```

This submits one baseline run to the pre-built simulator, waits for completion, lands the results into a demo study under `demos/v2ecoli/demo-runs/`, and records `runs_meta` entries that show up in the Simulations DB with `Origin: remote` pills. The run uses a short 2-generation ensemble so it completes in ~3 minutes.

### 3.4 Pre-computation summary

| Step | Script | Time | Required for |
|---|---|---|---|
| Verify workspace | `verify_demo.py` | ~10s | All segments |
| Pre-build sms-api image | `prep_remote_build.py` | ~4 min | Segment 6 (one-time) |
| Pre-land remote run | `prep_remote_land.py` | ~3 min | Segment 6 (one-time) |

No other pre-computation is needed — the showcase studies already have their runs and charts committed in the repo.

### 3.5 Start the dashboard

```bash
cd ~/vivarium-app/vivarium-dashboard
vivarium-dashboard serve --workspace ~/vivarium-app/v2ecoli --port 8771
```

> **Note**: The CLI entry point is `vivarium-dashboard serve` (or the newer `vivarium-workbench serve`).
> Both point at the same code. The `--workspace` flag points at the v2ecoli checkout.

Open `http://localhost:8771` in a browser. For the presenter demo, use a clean browser profile with the window sized to 1440x900 or larger. Hide bookmarks bars and other chrome.

---

## 4. PART A: Presenter Script

Each segment includes:
- **Narration** — what the presenter says (in quotes for suggested phrasing)
- **Actions** — concrete clicks and interactions
- **Talking points** — the "why this matters" payoff
- **Duration** — target time

Total: ~20 minutes

---

### Segment 1: Introduction (2 minutes)

**Narration:**
> "vivarium-workbench is a local web UI for process-bigraph workspaces. It turns a directory of YAML and simulation output into an interactive, git-backed research notebook. The mental model is three layers: the simulation engine — process-bigraph — runs the science. The tooling — this dashboard — orchestrates, renders, and commits. The data — the v2ecoli workspace — is the single source of truth. Every action you take in the dashboard is committed to git. Let's look at it."

**Actions:**
1. Open `http://localhost:8771` in a browser
2. Point out the left rail: 9 pages (Sources, Registry, Composites, Investigations, Simulations DB, Analyses, Studies, Branch, Composite Explorer)
3. Point out the investigation switcher at the top of the rail
4. Point out the workspace name chip in the rail header (for source switching later)

**Talking points:**
- The dashboard is a generic tool — it works with ANY process-bigraph workspace, not just v2ecoli
- All actions are git-tracked: add a dataset, create a study, run a simulation → all committed
- This is a research notebook that leaves an audit trail

---

### Segment 2: Registry — Simulator Agnosticism (3 minutes)

**Narration:**
> "The Registry tab shows every type and package the workspace knows about. This is where you see that the dashboard is not v2ecoli-specific — it's a truly multi-simulator platform. Let me show you."

**Actions:**
1. Click **Registry** in the left rail
2. Click the **Modules** sub-tab
3. Point to the installed packages:
   - `v2ecoli` — the E. coli whole-cell model (55 processes)
   - `viva_munk` — colony physics (PymunkProcess, chemotaxis, biofilm)
   - `pbg_ketchup` — kinetic parameter estimators (IPOPT)
   - `pbg_copasi` — ODE steady-state solver
   - `pbg_bioreactordesign` — bioreactor transport (BiRD)
   - `pbg_torch` — neural surrogate models
   - `pbg_parsimony` — capsule cell geometry
4. Click **Discovered registry** → **Processes** sub-tab
5. Scroll through: v2ecoli processes, viva_munk processes, ketchup estimator → all mixed together

**Talking points:**
- "Seven different simulation packages. One dashboard. One type system."
- "Any process from any package can be composed into any composite"
- "The dashboard auto-discovers everything via build_core(). To onboard a new simulator, you pip install it and declare it in workspace.yaml — it just appears."
- "This is the foundation of swappability: if everything shares a type system, everything is composable."

**Fallback:** If some packages aren't installed, show the **Available** filter in Modules and explain the install flow.

---

### Segment 3: Composites — Swappability (3 minutes)

**Narration:**
> "The Composites tab shows every runnable model the workspace can build. This is where swappability becomes concrete — we have three different cell engines, all sharing the same reactor coupler, all managed by the same dashboard."

**Actions:**
1. Click **Composites** in the left rail
2. Scroll through the grid — point out the available composites across all packages
3. **Cell-engine swappability — hand 1:** Click `baseline`
   - "This is the v2ecoli whole-cell model. 55 processes. tFBA metabolism. ~2.5 million ODEs."
4. **Cell-engine swappability — hand 2:** Click `baseline_millard`
   - "Same overall architecture, but metabolism is swapped out — instead of tFBA, we use the Millard 2017 kinetic ODE with 86 metabolites."
5. **Cell-engine swappability — hand 3:** Click `millard_pdmp_baseline`
   - "The PDMP reformulation. Millard metabolism plus LQR control, Poisson jump processes for transcription."
6. **Reactor-coupler swappability:** Click `reactor_bird_coupled`
   - "WCM cells coupled to the BiRD reactor. The coupler reads population biomass and exchange fluxes."
7. Click `reactor_bird_coupled_millard`
   - "SAME reactor coupler. SAME transport equations. DIFFERENT cell engine — Millard instead of WCM. The cell-side interface contract makes this possible."
8. **External simulators:** Click `ketchup_baseline`
   - "Kinetic parameter fitting with IPOPT. Completely different domain. Same dashboard."
9. Click `chemotaxis` (viva_munk)
   - "Bacterial chemotaxis in a 2D ligand gradient. viva_munk. Same dashboard."

**Talking points:**
- "Swappability means ONE workflow — Composite → Run → View results — for ANY simulator."
- "The cell-side interface contract is what makes engine substitution possible. It defines the inputs and outputs a cell engine must provide to work with the reactor coupler."
- "Add a new simulator? Install it, declare it in workspace.yaml, and its composites appear here automatically."
- "The dashboard is agnostic — it doesn't know or care what your simulator does, only that it conforms to the process-bigraph protocol."

**Fallback:** If a composite fails to resolve, skip it and use one that does. The baseline, baseline_millard, and colony composites are the most reliable.

---

### Segment 4: ParCa — Modularization (2 minutes)

**Narration:**
> "ParCa is the Parameter Calculator — the pipeline that fits experimental E. coli data into a self-consistent parameter set. It used to be a monolithic script. Now it's 9 modular Steps, each independently registered, testable, and swappable. Let's see it."

**Actions:**
1. From the Composites tab, click the **Explore** button on the `parca` composite
   - This opens the Composite Explorer
2. Point to the 9-step pipeline rendered in bigraph-loom:
   - Step 1: Initialize (scatter flat files into sim_data)
   - Step 2: Input Adjustments (compute/merge, pure)
   - Step 3: Basal Specs (fit minimal-medium condition)
   - Step 4: TF Condition Specs (51 transcription-factor conditions)
   - Step 5: Fit Condition (bulk distributions + translation supply)
   - Step 6: Promoter Binding (CVXPY optimization)
   - Step 7: Adjust Promoters (couple to genome position)
   - Step 8: Set Conditions (extract→compute→merge, pure)
   - Step 9: Final Adjustments (kinetic constants for the online model)
3. Click through a few steps to show their port wiring
4. **Optional — live run:** Click **Run** with `mode: fast`, `cpus: 4`, `debug: true`
   - This runs ~15 seconds for 7 TF conditions
   - Show the progress bar as steps execute

**Talking points:**
- "Each step declares its own INPUT_PORTS and OUTPUT_PORTS. Each is independently testable."
- "Step 4 (TF condition fitting) could be swapped out for a different algorithm — you'd only touch one file."
- "Step 6 (promoter binding) uses CVXPY. Swap it for a PyTorch optimizer? Replace one Step class, wire the same ports."
- "This is the modularization story. Monolithic pipeline → 9 composable Steps. Same result, vastly more maintainable."
- "The `@composite_generator` decorator is what makes ParCa visible here. Before this, the pipeline existed but was invisible to the dashboard."

**Fallback:** If the live run fails (timeout, dependency issue), show the pre-computed result from `models/parca/parca_state.pkl.gz` instead and explain the step timing from `models/parca/runtimes.json`.

---

### Segment 5: Investigations & Studies (3 minutes)

**Narration:**
> "Now let's look at how the dashboard organizes research. Investigations are research arcs — DAGs of studies grouped under a shared question. Each study is an experiment with pass/fail criteria."

**Actions:**
1. Click **Investigations** in the left rail
2. Point to the 9 investigation sets (Active / Closed)
3. **Open `v2ecoli-baseline-showcase`** — "This is a DEMONSTRATION investigation. Six studies that walk through the full v2ecoli pipeline."
4. Show the investigation detail:
   - Status pill, Report button, Notebook download
   - "About this investigation" disclosure with abstract and narrative
   - **Investigation DAG**: 6 study nodes with dependency edges
     - showcase-1-parca → showcase-2-baseline-figures → showcase-3-variant-decide → showcase-4-variant-comparison → showcase-5-next-direction-decide → showcase-6-equivalence-large
   - Point out the gate mechanism: "showcase-2 can't proceed until showcase-1 passes its gate."
5. Click on **showcase-1-parca** study node — the study detail opens in an iframe
   - Show the 3 behavior tests (all passing): condition count, cache bundle completeness, sim-data reproduction
   - Show the rendered figures (source manifest, simdata summary, cache bundle)
6. Click on **showcase-4-variant-comparison** — "5-variant perturbation sweep"
   - Show the overlaid charts for 5 variants

**Talking points:**
- "Investigations encode the scientific method: hypothesize, test, gate, proceed."
- "The DAG enforces dependency order. A downstream study literally cannot proceed until its upstream passes."
- "Every study has behavior tests — small declarative checks (in_range, monotonic) that auto-evaluate against run results."
- "The Report button generates a self-contained HTML report you can send to a reviewer."

**Fallback:** If study charts fail to render (stale viz), explain the viz-freshness system and show the raw test results instead.

---

### Segment 6: Simulations DB & Remote Runs (3 minutes)

**This is the anchor segment. Requires the sms-api tunnel to be up.**

**Narration:**
> "The Simulations DB tab shows every simulation run across the entire workspace, whether it ran on your laptop or on AWS GovCloud. This is where we demonstrate the sms-api integration — running the v2ecoli model at scale on AWS, with full traceability back to a git commit."

**Actions:**
1. Click **Simulations DB** in the left rail
2. Show the table with local runs:
   - Columns: Investigation, Study, Run, Location, Origin, Emitter, Time, Status
   - Emitter type pills: sqlite (gray), parquet (amber), xarray (teal)
   - Origin: local (gray)
3. **Switch to remote source:**
   - Click the workspace name chip at the top of the left rail
   - The source-switcher dropdown shows "Local" and "Builds — sms-api"
   - If a pre-built image exists, select it
   - The page reloads against the remote build workspace
4. Back in **Simulations DB**:
   - Remote runs now appear in the table
   - Origin column shows **remote** (blue pill) with deployment info
   - Runs side-by-side: local laptop runs and AWS GovCloud runs in the same table
5. **Live remote run demonstration:**
   - Switch back to the local workspace
   - Navigate to a study (use the demo study prepped in Section 3, or showcase-2-baseline-figures)
   - Click the **"Run remotely"** button
   - Watch the thin-client pipeline:
     - **Phase 1 (building)**: Pushes branch, registers build on sms-api, polls for image readiness (~1-2 min for cached build)
     - **Phase 2 (running)**: Submits the simulation run, polls for completion (~2-4 min for short ensemble)
     - **Phase 3 (landing)**: Downloads results and records them in the study's runs.db
   - The newly landed run appears in the Simulations DB with a local origin

**Talking points:**
- "Every run is traceable: git commit hash → exact Docker image → exact simulation results. Full reproducibility."
- "Extensibility: any simulator, any emitter backend (SQLite, Parquet, XArray), any scale (laptop → AWS GovCloud)."
- "The dashboard doesn't care WHERE the run happened. Local and remote runs appear side-by-side in the same table."
- "The remote pipeline is stateless — it's driven by the browser. No server-side queue. No infrastructure dependency beyond the sms-api."
- "This is the 'Simulations DB connected to sms-api' story: your laptop for development, AWS for production-scale runs, unified by git provenance."

**Action if tunnel is down:** Skip the live remote run. Show the pre-landed remote runs in the Simulations DB and explain the pipeline from the UI screenshots (see Appendix B).

---

### Segment 7: Analyses (2 minutes)

**Narration:**
> "The Analyses tab is a gallery of interactive visualizations. Some are saved views from studies, some are standalone analysis tools. Let's look at a few."

**Actions:**
1. Click **Analyses** in the left rail
2. Point to any saved visualizations in the gallery
3. **3D viewer**: If the `ecoli_3d` pack is available, show the whole-cell 3D model
   - Link opens the viewer with birth and pre-division states
   - "This is the parsimony capsule packing visualization, served from Cloudflare R2."
4. **PTools omics viewer**: If sms-ptools is running (`http://localhost:1555`):
   - Click the PTools card
   - "Pathway Tools Cellular Overview with study omics data overlaid on the E. coli metabolic map."
5. **Visualization preview**: Show the preview modal
   - "Visualization classes have a `demo()` method — they render instantly against synthetic data before any real run completes."
   - "Once real runs finish, the same visualization renders against actual simulation output."

**Talking points:**
- "Every visualization is a registered class with demo() + render() methods — preview before you run."
- "The PTools integration bridges the dashboard to external analysis tools through a simple URL template."
- "3D viewers, network graphs, time-series plots — all share the same registration and preview system."

**Fallback:** If PTools is unavailable, mention it as an integration example and skip to the visualization preview.

---

### Segment 8: Wrap-up (2 minutes)

**Narration:**
> "Let me summarize what we've seen. vivarium-workbench is a simulator-agnostic research notebook. It works with ANY process-bigraph workspace. Today we saw v2ecoli — but the same dashboard serves viva_munk colony physics, ketchup kinetic fitting, copasi ODE models, and BiRD reactor transport. All in one UI, all git-tracked."

**Actions:**
1. Click through each tab one more time as a rapid recap
2. Highlight the key architecture points:
   - "One dashboard, many simulators — the Registry proves it."
   - "Swappable cell engines — the Composites tab demonstrates it."
   - "Modular pipelines — ParCa's 9 Steps show it."
   - "Reproducible, git-tracked runs — the Simulations DB delivers it."
   - "AWS GovCloud at scale, local laptop for development — unified view."

**Talking points:**
- "The dashboard is a pip dependency of the workspace. It runs inside the workspace's venv on purpose — so it can import your model code and build composites."
- "Everything is committed to git. Add a dataset? Commit. Create a study? Commit. Run a simulation? The result lands in the workspace with provenance."
- "Questions?"

---

## 5. PART B: Self-Guided Guide

### 5.1 Setup

Complete the prerequisites in Section 2, then:

```bash
# 1. Activate the v2ecoli workspace venv
cd ~/vivarium-app/v2ecoli
source .venv/bin/activate

# 2. Verify the workspace
python demos/v2ecoli/verify_demo.py

# 3. Start the dashboard
vivarium-dashboard serve --workspace . --port 8771
# (or `vivarium-workbench serve` if your venv has the post-rename package)

# 4. Open in browser
open http://localhost:8771
```

### 5.2 Walkthrough

Follow the same 8 segments as the Presenter Script (Section 4), taking time to explore each tab. Expected outputs and key observations are noted in each segment's "Talking points" section. If something doesn't match expectations, see the Troubleshooting section.

### 5.3 Remote demo (optional)

If you have access to the sms-api SSM tunnel:

```bash
# 1. Start the tunnel (separate terminal)
aws ssm start-session \
  --region us-gov-west-1 \
  --target <INSTANCE_ID> \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["8080"],"localPortNumber":["8080"]}'

# 2. Pre-build an image
python demos/v2ecoli/prep_remote_build.py

# 3. Pre-land a remote run
python demos/v2ecoli/prep_remote_land.py

# 4. Now follow Segment 6 in the walkthrough
```

### 5.4 What success looks like

After completing the walkthrough, you should have:

- [ ] Viewed the Registry tab and identified at least 5 installed pbg-* packages
- [ ] Opened the Composites tab and located the baseline, baseline_millard, and millard_pdmp_baseline composites
- [ ] Opened the ParCa composite in the Explorer and identified all 9 steps
- [ ] Opened the v2ecoli-baseline-showcase investigation and viewed its DAG
- [ ] Viewed the Simulations DB tab with at least a few entries
- [ ] Browsed the Analyses tab and viewed at least one saved visualization
- [ ] (If remote demo) Completed a live sms-api build → submit → poll → land cycle

---

## 6. Assets Inventory

### 6.1 Existing v2ecoli assets used (read-only)

| Asset | Path | Used in |
|---|---|---|
| Workspace config | `workspace.yaml` | Entire demo |
| ParCa composite | `v2ecoli/composites/parca.py` | Segment 4 |
| Baseline composite | `v2ecoli/composites/baseline.py` | Segment 3 |
| Baseline Millard composite | `v2ecoli/composites/baseline_millard.py` | Segment 3 |
| PDMP baseline composite | `v2ecoli/composites/millard_pdmp_baseline.py` | Segment 3 |
| Reactor coupled composites | `v2ecoli/composites/reactor_bird_coupled*.py` | Segment 3 |
| Colony composites | `v2ecoli/composites/colony*.py` | Segment 3 |
| 6-study showcase investigation | `workspace/investigations/v2ecoli-baseline-showcase/` | Segment 5 |
| Showcase studies (1-6) | `workspace/studies/showcase-*/` | Segment 5 |
| mbr investigation | `workspace/investigations/multiscale-bioprocess/` | Segment 5 (backup) |
| ParCa model fixture | `models/parca/parca_state.pkl.gz` | Segment 4 (fallback) |
| ParCa runtime data | `models/parca/runtimes.json` | Segment 4 (fallback) |
| Cell-side interface contract | `workspace/references/expert/cell_side_interface_contract.md` | Segment 3 (reference) |
| ketchup composites | `pbg_ketchup` package (venv) | Segment 3 |
| viva_munk composites | `viva_munk` package (venv) | Segment 3 |

### 6.2 New demo assets (in `demos/v2ecoli/`, decoupled)

| Asset | Purpose | Status |
|---|---|---|
| `PLAN.md` | This file | In review |
| `verify_demo.py` | Pre-demo verification script | Completed (350 lines, 39 checks) |
| `prep_remote_build.py` | Pre-build sms-api simulator image | Completed (161 lines) |
| `prep_remote_land.py` | Pre-land a remote run into the workspace | Completed (266 lines) |
| `populate_demo_runs.py` | Seed Simulations DB with synthetic demo runs | Completed (176 lines) |
| `.demo_state.json` | Cached simulator_id and remote run state | Auto-generated |
| `demo-runs/` | Directory for pre-landed remote runs | Auto-generated |

None of these scripts modify any file outside `demos/v2ecoli/` or `demos/v2ecoli/demo-runs/`.

---

## 7. Appendix

### A. sms-api tunnel reference

```bash
# Establish the SSM tunnel (GovCloud)
aws ssm start-session \
  --region us-gov-west-1 \
  --target i-0123456789abcdef0 \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["8080"],"localPortNumber":["8080"]}'

# Verify
curl -s http://localhost:8080/core/v1/simulator/versions | python -m json.tool | head -10

# The tunnel stays alive as long as the terminal is open.
# Run it in a dedicated terminal window or use tmux/screen.

# Alternative: background with nohup
nohup aws ssm start-session \
  --region us-gov-west-1 \
  --target <INSTANCE_ID> \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["8080"],"localPortNumber":["8080"]}' \
  > /tmp/sms-tunnel.log 2>&1 &
```

### B. Fallback plan

| Scenario | Fallback |
|---|---|
| sms-api tunnel down | Skip Segment 6 live run. Show pre-landed remote runs in Simulations DB with "Origin: remote" pills. Narrate the pipeline from screenshots or the thin-client UI (which shows the build/submit buttons even if unreachable). |
| Composite fails to resolve | Skip that composite. Use `baseline` and `colony` which are the most reliable. Dismiss any error toasts quickly and move on. |
| Browser tab crashes | Reload the page. The dashboard renders from the workspace on each load — no state is lost (except in-progress runs). |
| ParCa live run times out | Use the pre-computed fixture. Show `runtimes.json` to explain per-step timing. |
| viz_freshness warning on charts | Acknowledge it: "The dashboard tracks when charts were rendered vs. when the last run completed. This warning means the chart predates the latest data." |
| CSRF error on mutation | Ensure the browser is accessing via `localhost` (not `127.0.0.1` or a file:// URL). |
| Package import fails | Verify the v2ecoli venv is active and all dependencies are installed. Re-run `verify_demo.py`. |

### C. Demo recording notes

For producing a video archive of the demo:

- Record at 1080p or 1440p, 30 fps
- Use a clean browser profile (no bookmarks, no extensions visible)
- Hide the OS dock/taskbar
- Use a neutral desktop background
- Keep the browser at a fixed window size (1440x900 recommended)
- For the remote demo, record the SSM tunnel startup in the terminal alongside the browser
- Total recorded length: ~25 minutes (20 min demo + 5 min buffer)

### D. Timing budget

| Segment | Target | Min | Max | Notes |
|---|---|---|---|---|
| 1. Introduction | 2:00 | 1:30 | 3:00 | Can compress by skipping architecture if audience knows it |
| 2. Registry | 3:00 | 2:00 | 4:00 | Can compress by showing fewer packages |
| 3. Composites & Swappability | 3:00 | 2:30 | 5:00 | Most important segment — allow extra time for questions |
| 4. ParCa | 2:00 | 1:30 | 3:00 | Add 15-20s if running live |
| 5. Investigations | 3:00 | 2:00 | 4:00 | Can skip showcase-5 and showcase-6 to save time |
| 6. Simulations DB & Remote | 3:00 | 2:00 | 5:00 | Skippable if tunnel is down; anchor if tunnel is up |
| 7. Analyses | 2:00 | 1:00 | 3:00 | Can skip PTools entirely |
| 8. Wrap-up | 2:00 | 1:00 | 2:00 | — |
| **Total** | **20:00** | **13:30** | **29:00** | Buffer: cut from Analyses, compress Registry |
