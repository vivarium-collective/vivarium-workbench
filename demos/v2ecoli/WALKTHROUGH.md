# vivarium-workbench Dashboard Demo — Unified Walkthrough

**Last verified**: 2026-07-07
**Branch**: `demo-v2ecoli` in `vivarium-collective/vivarium-dashboard`

---

## 0. Prerequisites

### 0.1 Repository & Environment

```bash
git clone git@github.com:vivarium-collective/vivarium-dashboard.git ~/vivarium-app/vivarium-dashboard
cd ~/vivarium-app/vivarium-dashboard
git checkout demo-v2ecoli

# Also clone v2ecoli (required dependency for the demo)
git clone git@github.com:vivarium-collective/v2ecoli.git ~/vivarium-app/v2ecoli
```

Install v2ecoli as a local editable dependency:
```bash
uv sync --extra demo
```

Verify:
```bash
python -c "import v2ecoli; import viva_munk; import pbg_ketchup; import pbg_copasi; print('OK')"
```

The v2ecoli venv must have these packages (already true for a working workspace):

```
process-bigraph, bigraph-schema, bigraph-viz, vivarium-workbench
pbg_superpowers, pbg_ketchup, pbg_copasi, pbg_parsimony
pbg_bioreactordesign, pbg_torch, viva_munk, pbg_emitters
```

Verify:
```bash
source .venv/bin/activate
python -c "import v2ecoli; import viva_munk; import pbg_ketchup; import pbg_copasi; print('OK')"
```

Minimum hardware: macOS/Linux, 16 GB RAM, 5 GB free disk.

### 0.2 AWS GovCloud Authentication

This demo's Segment 6 (remote runs) requires authenticated access to the `smsvpctest` GovCloud deployment stack. The canonical auth flow uses the shell function `stanford` defined in `~/.zshrc`:

```bash
# The 'stanford' function (in ~/.zshrc) does:
#   1. Sets AWS_PROFILE=stanford-sso
#   2. Sets AWS_DEFAULT_REGION=us-gov-west-1
#   3. Runs aws sso login --profile stanford-sso
#
# The `stanford test` alias resolves the stanford function with the test profile.

stanford test
```

After successful SSO login, your credentials provide read access to the `smsvpctest` CloudFormation stacks (`smsvpctest-batch`, `smsvpctest-internal-alb`, `smsvpctest-sms`) and the ability to establish SSM sessions to the batch submit node.

Verify auth:
```bash
AWS_PROFILE=stanford-sso AWS_DEFAULT_REGION=us-gov-west-1 aws sts get-caller-identity
```

### 0.3 SMS API Tunnel

The dashboard's "Run remotely" pipeline targets the sms-api service behind an internal ALB. The canonical tunnel script resolves the batch submit node ID and ALB DNS from the `smsvpctest` CloudFormation stacks, then establishes an SSM port-forwarding session that proxies `localhost:8080` → submit node → internal ALB:

```bash
# Run in a dedicated terminal (stays alive until Ctrl+C)
AWS_PROFILE=stanford-sso AWS_DEFAULT_REGION=us-gov-west-1 \
  ~/sms/sms-cdk/scripts/ptools-proxy.sh -s smsvpctest
```

The script auto-checks: AWS CLI, Session Manager plugin, valid credential, port availability. Expected output:

```
SMS Application Proxy
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Application URL:  http://localhost:8080/
  Available endpoints:
    • http://localhost:8080/              (PTools UI)
    • http://localhost:8080/sms/sms.html  (PTools SMS Simulation UI)
    • http://localhost:8080/docs          (SMS API UI)
```

Leave this terminal open for the duration of the demo. The tunnel proxies ALL SMS services (simulator build/run/land APIs, PTools, docs).

> **Note**: The `ptools-proxy.sh` uses `AWS-StartPortForwardingSessionToRemoteHost` to proxy through the submit node to the ALB — not the simple `AWS-StartPortForwardingSession` that tunnels to a single port on the instance. This provides full ALB routing (build API, run API, PTools, docs) through a single local port.

Verify tunnel health:
```bash
curl -s http://localhost:8080/docs | head -5
```

### 0.4 PTools Omics Viewer (optional — Segment 7)

```bash
docker run -p 1555:1555 ghcr.io/vivarium-collective/sms-ptools
```

Not required — gracefully skipped if unavailable.

### 0.5 Pre-Demo Remote Setup (one-time, before first demo)

Once the tunnel is up, pre-build a simulator image on sms-api and pre-land a remote run so the Simulations DB has live ☁️ entries:

```bash
source .venv/bin/activate
python demos/v2ecoli/prep_remote_build.py   # pushes branch, registers build, polls (~4 min)
python demos/v2ecoli/prep_remote_land.py    # submits, polls, downloads, lands (~3 min)
```

These are one-time operations. Subsequent demos reuse the cached `.demo_state.json`.

---

## 1. Pre-Flight (every demo session)

Execute in order:

```bash
# Terminal 1 — SMS API tunnel
AWS_PROFILE=stanford-sso AWS_DEFAULT_REGION=us-gov-west-1 \
  ~/sms/sms-cdk/scripts/ptools-proxy.sh -s smsvpctest

# Terminal 2 — Verify + start dashboard (from vivarium-dashboard repo)
cd ~/vivarium-app/vivarium-dashboard
python demos/v2ecoli/verify_demo.py          # expect 39/39 pass
vivarium-dashboard serve --workspace ~/vivarium-app/v2ecoli --port 8771
open http://localhost:8771
```

> **CLI name**: Use `vivarium-dashboard serve` or `vivarium-workbench serve` — both point at the same code.

---

## 2. Segment 1: Introduction (2 min)

### Actions
1. Open `http://localhost:8771` — confirm page renders with `<title>v2ecoli</title>` and workspace branding
2. Point out the **left rail** — 9 pages: Sources, Registry, Composites, Investigations, Simulations DB, Analyses, Studies, Branch, Composite Explorer
3. Point out the **investigation switcher** at the top of the rail
4. Point out the **workspace name chip** in the rail header (source switching for remote builds in Segment 6)

### API
`GET /` → Page renders with workspace branding

### Narration
> "vivarium-workbench is a local web UI for process-bigraph workspaces. Three layers: the simulation engine — process-bigraph — runs the science. The tooling — this dashboard — orchestrates, renders, and commits. The data — the v2ecoli workspace — is the single source of truth. Every action you take in the dashboard is committed to git."

### Talking Points
- Generic tool — works with ANY process-bigraph workspace, not just v2ecoli
- Git-tracked: add a dataset, create a study, run a simulation → all committed
- Research notebook that leaves an audit trail

---

## 3. Segment 2: Registry — Simulator Agnosticism (3 min)

### Actions
1. Click **Registry** → **Modules** sub-tab
2. Point to the 11 installed packages:
   - `v2ecoli` — E. coli whole-cell model (55 processes)
   - `viva_munk` — colony physics (PymunkProcess, chemotaxis, biofilm)
   - `pbg_ketchup` — kinetic parameter estimators (IPOPT)
   - `pbg_copasi` — ODE steady-state solver
   - `pbg_bioreactordesign` — bioreactor transport (BiRD)
   - `pbg_torch` — neural surrogate models
   - `pbg_parsimony` — capsule cell geometry
3. Click **Discovered registry** → **Processes** sub-tab
4. Scroll: **173 Process classes** from 7 packages, interspersed — `viva_munk`'s `PymunkProcess` sits next to v2ecoli's `PolypeptideElongation`

### API
`GET /api/catalog` → 11 packages
`GET /api/registry` → 173 Process classes

### Narration
> "Seven different simulation packages. One dashboard. One type system. Any process from any package can be composed into any composite. To onboard a new simulator, you pip install it and declare it in `workspace.yaml` — it just appears."

### Key Number
> **173** Process classes from **7** different simulation packages, all in one type system.

---

## 4. Segment 3: Composites — Swappability (3 min)

### Actions
1. Click **Composites** — **28 composites** across all packages

#### Cell-Engine Swappability
2. Click `baseline` — "v2ecoli whole-cell model, 55 processes, tFBA metabolism"
3. Click `baseline_millard` — "Same architecture, Millard 2017 kinetic ODE, 86 metabolites"
4. Click `millard_pdmp_baseline` — "PDMP reformulation: Millard + LQR control + Poisson jump processes"

#### Reactor-Coupler Swappability
5. Click `reactor_bird_coupled` — "WCM cells coupled to BiRD reactor"
6. Click `reactor_bird_coupled_millard` — "SAME reactor coupler, DIFFERENT cell engine (Millard). The cell-side interface contract makes this possible."

#### External Simulators
7. Click `ketchup_baseline` (pbg_ketchup) — "Kinetic parameter fitting with IPOPT. Completely different domain. Same dashboard."
8. Click `chemotaxis` (viva_munk) — "Bacterial chemotaxis in a 2D ligand gradient. Same dashboard."

### API
`GET /api/composites` → 28 composites: 12 v2ecoli, 3 pbg_copasi, 1 pbg_parsimony, 9 viva_munk, 3 pbg_ketchup

### Narration
> "Three different cell engines, all sharing the same reactor coupler, all managed by the same dashboard. Swappability means ONE workflow — Composite → Run → View results — for ANY simulator."

### Key Number
> **28** runnable models — whole-cell, colony physics, kinetic fitting, ODE solving.

---

## 5. Segment 4: ParCa — Modularization (2 min)

### Actions
1. From Composites, click **Explore** on the `parca` composite (opens Composite Explorer)
2. Point to the **9-step pipeline** in bigraph-loom:
   - Step 1: Initialize (scatter flat files → sim_data)
   - Step 2: Input Adjustments (compute/merge, pure)
   - Step 3: Basal Specs (fit minimal-medium condition)
   - Step 4: TF Condition Specs (51 transcription-factor conditions)
   - Step 5: Fit Condition (bulk distributions + translation supply)
   - Step 6: Promoter Binding (CVXPY optimization)
   - Step 7: Adjust Promoters (couple to genome position)
   - Step 8: Set Conditions (extract→compute→merge, pure)
   - Step 9: Final Adjustments (kinetic constants for the online model)
3. Click through steps to show port wiring
4. **Optional live run**: Click **Run** tab → `mode: fast, cpus: 4, debug: true` → ~15s

### API
`GET /api/composite-resolve?id=v2ecoli.composites.parca` → 43 state entries

### Narration
> "ParCa used to be a monolithic script. Now it's 9 modular Steps, each independently registered, testable, and swappable. Step 4 (TF condition fitting) could be swapped for a different algorithm — you'd only touch one file. Step 6 uses CVXPY. Swap it for a PyTorch optimizer? Replace one Step class, wire the same ports."

### Key Number
> **43** state entries across **9** modular Steps. Each independently testable and swappable.

### Explorer Sub-tabs
- **Structure** — pipeline graph (nodes = steps, edges = ports)
- **Run** — launch with parameter overrides
- **History** — past runs

---

## 6. Segment 5: Investigations & Studies (3 min)

### Actions
1. Click **Investigations** — **8 investigations** (Active / Closed)
2. Open **`v2ecoli-baseline-showcase`**
3. Show the detail panel: Status pill, Report button, Notebook download, "About this investigation" disclosure
4. Show the **DAG**: 6 study nodes with dependency edges — not a straight line, it fans out:
   `showcase-1-parca` → `showcase-2-baseline-figures`, which then branches to three parallel children — `showcase-3-variant-decide`, `showcase-4-variant-comparison`, and `showcase-6-equivalence-large` — with `showcase-5-next-direction-decide` depending on `showcase-4`
5. Gate mechanism: "showcase-2 can't proceed until showcase-1 passes its gate"
6. Click **showcase-1-parca** — study detail in iframe:
   - **3 behavior tests** (all passing): `parca-builds-full-51-conditions`, `cache-bundle-complete`, `sim_data-reproduces-parca-comparison`
   - Rendered figures: source manifest, simdata summary, cache bundle
7. Click **showcase-4-variant-comparison** — "5-variant perturbation sweep" with overlaid charts

### API
`GET /api/investigation-summaries` → 8 investigations
`GET /api/investigation/v2ecoli-baseline-showcase` → 6 studies, showcase-1 gate: **passed**
`GET /api/study/showcase-1-parca` → 3 behavior tests, all passed

### Narration
> "Investigations are research arcs — DAGs of studies grouped under a shared question. Each study is an experiment with pass/fail criteria. The DAG enforces dependency order — a downstream study literally cannot proceed until its upstream passes."

### Key Number
> **8** research arcs with dependency gates — a hypothesis can't proceed until its upstream passes.

---

## 7. Segment 6: Simulations DB & Remote Runs (3 min)

This segment requires the SMS API tunnel (Section 0.3) to be running on `localhost:8080`.

### Actions — Part A: Simulations DB
1. Click **Simulations DB**
2. Show the **52-run** table: columns — Investigation, Study, Run, Location, Origin, Emitter, Time, Status
3. Point out **Emitter type pills**: sqlite (gray), parquet (amber), xarray (teal)
4. Point out **Origin badges**: local vs. remote (☁️ blue pill)
5. Point out the **4 remote runs** with full provenance (simulation_id, experiment_id, backend=ray, s3_uri from sms-api)
6. Show **status variety**: 4 failed (including `5-variant sweep ΔO2`), 1 orphaned (`BiRD reactor + Millard cell` — a stale in-progress run whose backing process died; shows as `orphaned`, not `running`)

### API
`GET /api/simulations` → 52 runs: 4 remote ☁️, 4 failed, 1 orphaned
Emitter distribution: sqlite=12, parquet=13, xarray=11, unknown=16

### Actions — Part B: Live Remote Run
7. From any study page, click **"Run remotely"**
8. The browser-driven thin-client pipeline executes:
   - **Phase 1 (building)**: Pushes current branch to sms-api, registers a Docker build, polls for image readiness (~1–2 min for cached build)
   - **Phase 2 (running)**: Submits the simulation run to sms-api, polls for completion (~2–4 min for short ensemble)
   - **Phase 3 (landing)**: Downloads results from S3, records them in the study's runs.db with git provenance
9. The newly landed run appears in Simulations DB (local origin, since it was landed from remote)

### Narration
> "The Simulations DB shows every run, whether it happened on your laptop or on AWS GovCloud. The remote pipeline is stateless — it's driven entirely by the browser through the SSM tunnel. No server-side queue. Every run is traceable: git commit hash → exact Docker image → exact simulation results."

### Talking Points
- "Any simulator, any emitter backend (SQLite, Parquet, XArray), any scale — laptop → AWS GovCloud. Same table, side-by-side."
- "The remote pipeline uses the `smsvpctest` deployment stack. The `ptools-proxy.sh` script tunnels through the batch submit node to the internal ALB, providing the full SMS API surface through `localhost:8080`."
- "Extensibility: push a branch, click 'Run remotely', and the sms-api builds the Docker image from your exact code. Full reproducibility."

### Key Number
> **52** runs, **3** emitter backends, local and remote side-by-side.

### Fallback (tunnel down)
Skip Part B. Show the 4 pre-landed remote ☁️ runs in Simulations DB and narrate the pipeline architecture. Mention the `ptools-proxy.sh -s smsvpctest` command to establish the tunnel when ready.

---

## 8. Segment 7: Analyses (2 min)

### Actions
1. Click **Analyses** — visualization class gallery
3. **PTools omics viewer**: If sms-ptools is running on `localhost:1555`, launch Pathway Tools Cellular Overview with study omics data overlaid on E. coli metabolic map
4. **Visualization preview**: Show a `demo()` method rendering instantly against synthetic data

### API
`GET /api/visualization-classes` → 58 visualization classes
3D viewer manifest: `workspace/studies/ecoli-3d/viz/3d/ecoli_3d.pack.json`

### Narration
> "Every visualization is a registered class with `demo()` + `render()` methods — preview before you run. PTools bridges the dashboard to external analysis tools through a URL template. 3D viewers, network graphs, time-series — all share the same registration system."

### Key Number
> **58** visualization classes — 3D viewers, network graphs, time-series, omics overlays.

---

## 9. Segment 8: Wrap-up (2 min)

### Actions
1. Rapid click-through of all tabs as recap
2. Highlight architecture pillars:
   - **One dashboard, many simulators** — Registry: 173 processes from 7 packages
   - **Swappable cell engines** — Composites: baseline, Millard, PDMP, all sharing reactor coupler
   - **Modular pipelines** — ParCa: 9 Steps, each independently swappable
   - **Reproducible, git-tracked runs** — Simulations DB: 52 runs with full provenance
   - **AWS GovCloud at scale, local for development** — Segment 6: browser-driven remote pipeline through `smsvpctest`

### Narration
> "vivarium-workbench is a simulator-agnostic research notebook. Today we saw v2ecoli — but the same dashboard serves viva_munk colony physics, ketchup kinetic fitting, copasi ODE models, and BiRD reactor transport. All in one UI, all git-tracked. Questions?"

---

## 10. After the Demo

```bash
# Terminal 1: Ctrl+C to stop the SMS API tunnel

# Terminal 2: Stop dashboard + reset
kill $(lsof -ti:8771) 2>/dev/null
python demos/v2ecoli/populate_demo_runs.py    # reset demo runs if needed
```

---

## Appendix A: Architecture Elevator Pitch (30 seconds)

> "vivarium-workbench is a local web UI for process-bigraph workspaces. Three layers: the simulation engine — process-bigraph — runs the science. The tooling — this dashboard — orchestrates, renders, and commits. The data — the workspace — is the single source of truth. Every action you take is committed to git."

---

## Appendix B: Anticipated Q&A

**Q: Do I need to be a v2ecoli expert to use this dashboard?**
A: No — the dashboard is simulator-agnostic. v2ecoli is the demonstration workspace. A totally different model gets the same UI.

**Q: How do I add my own simulator?**
A: `pip install` your pbg-* package, declare it in `workspace.yaml` imports, refresh. Processes, composites, and visualizations appear automatically. No dashboard code changes.

**Q: What if my simulation takes hours?**
A: The remote run pipeline (sms-api) offloads to AWS GovCloud. Your laptop can close while the run progresses. Results land back with full provenance.

**Q: Is the dashboard open source?**
A: Yes — MIT licensed. GitHub: `vivarium-collective/vivarium-workbench` (currently `vivarium-dashboard`).

**Q: How do I share results with a collaborator?**
A: Push the branch, or export a self-contained HTML report, or use `vivarium-dashboard-publish` for a static read-only bundle.

**Q: What's the SMS API tunnel doing under the hood?**
A: `ptools-proxy.sh -s smsvpctest` resolves the batch submit node ID and ALB DNS from the `smsvpctest-*` CloudFormation stacks, then runs an SSM `StartPortForwardingSessionToRemoteHost` session. The submit node proxies `localhost:8080` to the internal ALB, which routes to the sms-api service (build/run/land APIs), PTools, and docs — all through a single port.

**Q: Do I need the tunnel for the rest of the demo?**
A: No — only Segment 6 (remote runs) requires it. Segments 1–5 and 7–8 work entirely offline against the local workspace.

---

## Appendix C: Quick-Reference Timing

| Time | Segment | Page | Key Click |
|------|---------|------|-----------|
| 0:00 | Tunnel | Terminal | `ptools-proxy.sh -s smsvpctest` |
| 0:15 | Start server | Terminal | `vivarium-dashboard serve --workspace . --port 8771` |
| 0:30 | Open browser | — | `http://localhost:8771` |
| 1:00 | **1. Intro** | Home | Rail, workspace chip |
| 3:00 | **2. Registry** | Registry → Modules | 11 packages |
| 4:00 | **2. Registry** | Registry → Processes | **173** processes from 7 packages |
| 6:00 | **3. Composites** | Composites | baseline → millard → pdmp |
| 8:00 | **3. Composites** | Composites | External: ketchup, chemotaxis |
| 9:00 | **4. ParCa** | Composites → Explore on parca | **9-step** pipeline |
| 10:00 | **4. ParCa** | Explorer → Run | Optional: fast mode (~15s) |
| 12:00 | **5. Investigations** | Investigations | **8** investigations |
| 13:00 | **5. Investigations** | v2ecoli-baseline-showcase | DAG, tests, charts |
| 15:00 | **6. Simulations DB** | Simulations DB | **52** runs, emitter pills, ☁️ badges |
| 16:00 | **6. Simulations DB** | → Run remotely | Live sms-api pipeline |
| 18:00 | **7. Analyses** | Analyses | **58** viz classes, 3D, PTools |
| 19:00 | **8. Wrap-up** | — | Recap + Q&A |
| 20:00 | **Q&A** | — | — |

---

## Appendix D: Demo Environment State

All demo artifacts are under `demos/v2ecoli/`. Zero modifications to existing v2ecoli code, composites, studies, or investigations.

| File | Purpose | Side Effects |
|------|---------|-------------|
| `WALKTHROUGH.md` | This file | None |
| `PLAN.md` | Presenter + self-guided plan | None |
| `NOTES.md` | Presenter quick reference | None |
| `verify_demo.py` | 39-check pre-demo verification | None |
| `populate_demo_runs.py` | Seeds 16 synthetic demo runs | Writes `.pbg/composite-runs.db` (gitignored) |
| `prep_remote_build.py` | Pre-builds sms-api Docker image | Writes `.demo_state.json` (gitignored) |
| `prep_remote_land.py` | Pre-lands a remote run | Writes `.pbg/composite-runs.db` (gitignored) |
| `.gitignore` | Prevents committing generated state | None |

---

## Appendix E: Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| Page loads blank | Server not running | Check terminal for `vivarium-dashboard serve` |
| "Connection refused" | Wrong port | Confirm `--port 8771` |
| Composite resolves to error | Missing deps | Skip; use `baseline` or `colony` |
| Simulations DB shows 0 runs | DB cleaned | `python demos/v2ecoli/populate_demo_runs.py` |
| ParCa Explorer blank iframe | bigraph-loom missing | `pip install bigraph-loom` |
| "Run remotely" fails | Tunnel down | Check `ptools-proxy.sh` terminal; verify `curl localhost:8080/docs` |
| Tunnel fails: "credentials not valid" | SSO session expired | Re-run `stanford test` |
| Tunnel fails: "Could not find submit node" | Wrong stack or region | Confirm `AWS_PROFILE=stanford-sso` and `AWS_DEFAULT_REGION=us-gov-west-1` |
| Chart shows "viz_freshness" warning | Normal — charts predate latest run | Explain freshness tracking |
| CSRF error on POST | Browser origin mismatch | Access via `localhost` (not `127.0.0.1`) |
| PTools card shows error | Container not running | Mention as integration example, skip |
| Port 8771 already in use | Prior server still running | `kill $(lsof -ti:8771)` |

---

## Appendix F: Presenter Must-Know

1. **CLI name**: `vivarium-dashboard serve` (venv has pre-rename package — both names point at same code)
2. **Branch**: `demo-v2ecoli` in `vivarium-collective/vivarium-dashboard` — no v2ecoli files modified
3. **Auth**: `stanford test` in `~/.zshrc` sets `AWS_PROFILE=stanford-sso`, `AWS_DEFAULT_REGION=us-gov-west-1`, runs `aws sso login`
4. **Tunnel**: `~/sms/sms-cdk/scripts/ptools-proxy.sh -s smsvpctest` proxies `localhost:8080` → SMS API via batch submit node + internal ALB
5. **Simulations DB**: 52 runs, 16 synthetic + 4 remote ☁️ + real runs; seeded by `populate_demo_runs.py`
6. **No raw simulation data**: Showcase chart PNGs are present and render correctly. You CANNOT re-run showcase sims without rebuilding ParCa caches — which is not needed for this demo.
7. **ParCa live run**: Fast mode ~15s (7 TF conditions). Full mode ~2.4 min (51 conditions).
8. **Reset**: `python demos/v2ecoli/populate_demo_runs.py` restores synthetic run entries.
