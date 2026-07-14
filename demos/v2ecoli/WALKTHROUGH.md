# vivarium-workbench Dashboard Demo — Unified Walkthrough (Remote GovCloud)

**Last verified**: 2026-07-14 *(remote — Segments 1–6 driven live incl. a live pinned-build remote run in Segment 6 Part B. Segment 7: interactive figures + omics-TSV HTTP delivery verified live; the PTools Omics Viewer **Launch** does NOT auto-paint on the deployed `sms-ptools:0.5.9` (scheme mismatch — deferred, see plan 9), so demo it with that caveat or skip the Launch. Segment 8: all recap figures re-verified against the live deployment — 173 processes / 7 packages, 9 ParCa Steps, 8 investigations (summaries view), 58 viz classes, Simulations DB now **36** (35 seeded + 1 landed live).)*
**Branch**: `demo-v2ecoli` in `vivarium-collective/vivarium-dashboard`
**Demo target**: the **REMOTE** `/workbench` deployment on the `sms-api-stanford-test`
Kubernetes namespace (GovCloud `smsvpctest` stack), reached in the browser at
**`http://localhost:8080/workbench`** through the `sms-proxy.sh` SSM tunnel.

> This is the **canonical remote demo**. You do NOT clone, install, or `serve`
> anything locally for this flow — the dashboard, the v2ecoli workspace, and the
> sms-api service all run in-cluster; you only open an authenticated tunnel and a
> browser. The old local-serve flow is preserved verbatim in **Appendix G — Local
> Dev (offline)** as a fallback.

---

## 0. Prerequisites (one-time setup)

### 0.1 AWS GovCloud Authentication

The remote deployment lives in the `smsvpctest` GovCloud stack. Authenticate with
the `stanford` shell function (defined in `~/.zshrc`):

```bash
# The 'stanford' function (in ~/.zshrc) does:
#   1. export AWS_PROFILE=stanford-sso
#   2. export AWS_DEFAULT_REGION=us-gov-west-1
#   3. aws sso login --profile stanford-sso
#
# 'stanford test' resolves the function against the test profile.
stanford test
```

After SSO login, your credentials provide read access to the `smsvpctest`
CloudFormation stacks and the ability to establish SSM sessions to the batch
submit node.

Verify auth:
```bash
AWS_PROFILE=stanford-sso AWS_DEFAULT_REGION=us-gov-west-1 aws sts get-caller-identity
```

### 0.2 The sms-cdk clone (holds the tunnel script)

The tunnel script ships in the `sms-cdk` repo:
```bash
git clone git@github.com:vivarium-collective/sms-cdk.git ~/sms/sms-cdk   # if not already present
```

### 0.3 PTools Omics Viewer — Segment 7

No local setup. Pathway Tools runs as the `ptools` Deployment in the
`sms-api-stanford-test` namespace, served at the internal-ALB **root** (`/`) —
the same ALB the dashboard co-tenants under `/workbench/`. Through the
`sms-proxy -s smsvpctest` tunnel it's reachable at `http://localhost:8080`, so
the Omics Viewer's **Launch** button (Segment 7) opens the live remote Cellular
Overview in a new tab. The `seed-workspace` initContainer stamps
`ui.ptools_server_url` (the browser target) and `ui.dashboard_public_base_url`
(the in-cluster URL the ptools pod fetches study TSVs from) into the served
`workspace.yaml`; nothing to run locally.

### 0.4 Remote workspace state

The remote pod serves the v2ecoli workspace mounted from its private EBS PVC at
`/workspace` — it comes **pre-seeded** (registry, composites, investigations,
studies, and the Simulations DB). There is no local seeding step for the remote
demo. (The local seeding scripts — `populate_demo_runs.py`, `prep_remote_*.py` —
belong to the offline flow; see Appendix G.)

---

## 1. Pre-Flight (every demo session)

Two steps. No local server.

```bash
# Terminal 1 — open the SSM tunnel (stays alive until Ctrl+C).
#   sms-proxy.sh resolves the batch submit node + internal ALB DNS from the
#   smsvpctest-* CloudFormation stacks and starts an SSM
#   StartPortForwardingSessionToRemoteHost session: localhost:8080 → ALB:80.
AWS_PROFILE=stanford-sso AWS_DEFAULT_REGION=us-gov-west-1 \
  ~/sms/sms-cdk/scripts/sms-proxy.sh -s smsvpctest
```

Confirm the proxy banner lists the endpoints (all on port 8080):

```
SMS Application Proxy
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Application URL:  http://localhost:8080/
  Available endpoints:
    • http://localhost:8080/workbench     (vivarium-workbench dashboard)
    • http://localhost:8080/              (PTools UI)
    • http://localhost:8080/sms/sms.html  (PTools SMS Simulation UI)
    • http://localhost:8080/docs          (SMS API UI)
```

```bash
# Terminal 2 — verify the tunnel is routing the dashboard, then open it.
curl -s -o /dev/null -w "workbench HTTP %{http_code}\n" http://localhost:8080/workbench/
open http://localhost:8080/workbench
```

> **Tunnel latency**: API GETs traverse SSM → ALB → pod and can take several
> seconds (~8s observed) on first hit; the page is fully interactive, just not
> LAN-fast. Give each tab a moment to populate.

> **The ALB rewrites `Host` and omits `X-Forwarded-Host`.** The pod is deployed
> with `VIVARIUM_WORKBENCH_ALLOWED_ORIGINS=http://localhost:8080`, so POST/DELETE
> (Run, Run-remotely, saves) are admitted by the CSRF allowlist. If you see
> `cross-origin request forbidden`, that env is missing on the workbench
> Deployment — see Appendix E.

> **`sms-proxy.sh` vs `ptools-proxy.sh`**: `ptools-proxy.sh` is a symlink to
> `sms-proxy.sh` — either name works; `sms-proxy.sh` is canonical.

---

## 2. Segment 1: Introduction (2 min)

### Actions
1. Open `http://localhost:8080/workbench` — confirm the page renders with
   `<title>v2ecoli</title>` and workspace branding
2. Point out the **left rail** — 9 pages: Sources, Registry, Composites, Investigations, Simulations DB, Analyses, Studies, Branch, Composite Explorer
3. Point out the **investigation switcher** at the top of the rail
4. Point out the **workspace name chip** in the rail header (source switching for remote builds in Segment 6)

### API
`GET /workbench/` → Page renders with workspace branding

### Narration
> "vivarium-workbench is a web UI for process-bigraph workspaces. Three layers: the simulation engine — process-bigraph — runs the science. The tooling — this dashboard — orchestrates, renders, and commits. The data — the v2ecoli workspace — is the single source of truth. Every action you take in the dashboard is committed to git. And what you're looking at is running on AWS GovCloud — served from a Kubernetes pod, reached through an SSM tunnel."

### Talking Points
- Generic tool — works with ANY process-bigraph workspace, not just v2ecoli
- Git-tracked: add a dataset, create a study, run a simulation → all committed
- Research notebook that leaves an audit trail
- Deployed to GovCloud: the same dashboard that runs on a laptop runs in-cluster

---

## 3. Segment 2: Registry — Simulator Agnosticism (3 min)

### Actions
1. Click **Registry** → **Modules** sub-tab
2. Point to the installed packages:
   - `v2ecoli` — E. coli whole-cell model (55 processes)
   - `viva_munk` — colony physics (PymunkProcess, chemotaxis, biofilm)
   - `pbg_ketchup` — kinetic parameter estimators (IPOPT)
   - `pbg_copasi` — ODE steady-state solver
   - `pbg_bioreactordesign` — bioreactor transport (BiRD)
   - `pbg_torch` — neural surrogate models
   - `pbg_parsimony` — capsule cell geometry
3. Click **Discovered registry** → **Processes** sub-tab
4. Scroll: **Process classes** from 7 packages, interspersed — `viva_munk`'s `PymunkProcess` sits next to v2ecoli's `PolypeptideElongation`

### API
`GET /workbench/api/catalog` → installed packages
`GET /workbench/api/registry` → Process classes

### Narration
> "Seven different simulation packages. One dashboard. One type system. Any process from any package can be composed into any composite. To onboard a new simulator, you pip install it and declare it in `workspace.yaml` — it just appears."

### Key Number
> **173** Process classes from **7** different simulation packages, all in one type system.

---

## 4. Segment 3: Composites — Swappability (3 min)

### Actions
1. Click **Composites** — composites across all packages

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
`GET /workbench/api/composites` → 28 composites: 12 v2ecoli, 3 pbg_copasi, 1 pbg_parsimony, 9 viva_munk, 3 pbg_ketchup

### Narration
> "Three different cell engines, all sharing the same reactor coupler, all managed by the same dashboard. Swappability means ONE workflow — Composite → Run → View results — for ANY simulator."

### Key Number
> **28** runnable models — whole-cell, colony physics, kinetic fitting, ODE solving.

---

## 5. Segment 4: ParCa — Modularization (2 min)

### Actions
1. From Composites, click **Explore** on the `parca` composite (opens Composite Explorer)
2. Point to the **9-step pipeline** in the embedded bigraph-loom explorer:
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

> **Composite Explorer + loom panel** render for any composite (`parca`, `colony`,
> `baseline`). `bigraph-loom` is baked into the combined image — an earlier build
> that omitted it 500'd this panel. If the loom panel errors, the deployed image
> predates the fix; see Appendix E.

### API
`GET /workbench/api/composite-resolve?id=v2ecoli.composites.parca` → 43 state entries

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

> **Study-detail iframe under `/workbench`**: the detail page is base-path-aware
> and renders styled + interactive through the subpath (a prior bug served it
> unstyled; fixed). If a study detail loads blank/unstyled, see Appendix E.

### API
`GET /workbench/api/investigation-summaries` → 8 investigations
`GET /workbench/api/investigation/v2ecoli-baseline-showcase` → 6 studies, showcase-1 gate: **passed**
`GET /workbench/api/study/showcase-1-parca` → 3 behavior tests, all passed

### Narration
> "Investigations are research arcs — DAGs of studies grouped under a shared question. Each study is an experiment with pass/fail criteria. The DAG enforces dependency order — a downstream study literally cannot proceed until its upstream passes."

### Key Number
> **8** research arcs with dependency gates — a hypothesis can't proceed until its upstream passes.

---

## 7. Segment 6: Simulations DB & Remote Runs (3 min)

The dashboard is already remote. Part A tours the run ledger; Part B runs a NEW
simulation on GovCloud **against a pinned, already-built simulator** — no git push,
no Docker build, no GitHub login. Your browser only needs the tunnel to reach
`/workbench`. (Pinned mode is enabled by the deployment: `VIVARIUM_WORKBENCH_REMOTE_PINNED=1`
+ `_REMOTE_REPO_URL`/`_REMOTE_BRANCH`.)

### Actions — Part A: Simulations DB
1. Click **Simulations DB**
2. Show the run table: columns — Investigation, Study, Run, Location, Origin, Emitter, Time, Status. The seeded baseline is **35 runs**; the count grows by one each time you land a live remote run in Part B.
3. Point out **Emitter type pills**: **xarray, parquet, sqlite** — the seed has parquet ×6, sqlite ×3, xarray ×3, and 23 runs with no recorded emitter. "Any emitter backend, same table."
4. Point out **Origin**: the seeded runs are all **local**. A remote (**☁️**) origin appears **after** you land a live Run-on-remote in Part B — there are none pre-seeded. (Honest by design: the ☁️ pill is earned live, not staged.)
5. Point out **status variety** across the 35 seeded runs: **31 completed, 1 "complete", 3 failed**.

### API
`GET /workbench/api/simulations` → 35 seeded runs (31 `completed` + 1 `complete` + 3 `failed`); emitter pills xarray/parquet/sqlite + unrecorded. The count increments by one per landed remote run.

### Actions — Part B: Live Remote Run (pinned build)
6. Open a study (e.g. **showcase-2-baseline-figures**) and scroll to the run card. With pinned mode on, it reads **"Run against pinned build (main @ 70b5ec3)"** with *"No push or GitHub login required."*
7. Leave Generations / Seeds at 1 / 1, keep **Run ParCa** checked, click **▶ Run on remote (pinned)**. It goes straight to *"Using pinned build… Submitting run…"* — **no login prompt** (the old blocker is gone).
8. Watch the phases (the dashboard polls sms-api; sms-api owns the async compute):
   - **build → ✓ instantly** — the pinned, already-built simulator (`simulator_id 69`) is reused; nothing rebuilds.
   - **run → queued** — sms-api submits a **ParCa** job + an **N-node simulation ensemble** to **AWS Batch as a transient Ray (MNP) cluster**. "Queued" = Batch provisioning the Ray cluster (`RUNNABLE`) + the ParCa dependency gate (`PENDING`); expect a few minutes.
   - **run → running → done** — the Ray head executes the E. coli ensemble, then completes.
9. Click **⬇ Land results locally** — downloads the result store from S3 and records it in the study's `runs.db` with git provenance.
10. The landed run appears in Simulations DB carrying a **remote ☁️ origin** with full provenance — `deployment: smsvpctest`, `simulation_id`, `backend: ray` (the ☁️ pill from step A4, now earned live).

### Narration
> "One pinned, reproducible build — an exact commit resolved to an exact Docker image, already built on GovCloud. From the dashboard we submit any number of simulation configs against that single build, each on a transient Ray cluster spun up per run. No push, no rebuild, no login: the whole thing is driven by the browser through the dashboard, which calls sms-api in-cluster. Every landed run is traceable back to that commit."

### Talking Points
- "Any simulator, any emitter backend (SQLite, Parquet, XArray), any scale — laptop → AWS GovCloud. Same table, side-by-side."
- "One deployment: dashboard, sms-api, PTools behind one internal ALB, one SSM tunnel on `localhost:8080`."
- "Reproducibility: the run targets a pinned commit's prebuilt image; sms-api provisions a transient Ray MNP cluster (ParCa → N-node ensemble) and lands results with full git provenance — no per-run build or credentials."

### Key Number
> **35** seeded runs, **3** emitter backends, **+1** remote ☁️ run you land live on a 3-node Ray cluster.

### Fallback (remote run unavailable)
If AWS Batch can't provision the Ray cluster (capacity) or sms-api is unreachable,
skip the live land: narrate the pinned-build architecture (pinned commit → prebuilt
image → transient Ray MNP cluster) from Part A, and show a previously-landed ☁️ run
if the session has one.

---

## 8. Segment 7: Analyses (2 min)

### Actions
1. Click **Analyses** — visualization class gallery
2. **PTools Omics Viewer** (remote, no local container): on the "Pathway Tools — Omics Viewer" card, click **Launch** on the `showcase-2-baseline-figures` row. A new tab opens the live remote EcoCyc **Cellular Overview** with the study's exported omics TSVs painted onto the E. coli metabolic map. (Served by the `ptools` Deployment in `sms-api-stanford-test` at the ALB root; the in-cluster ptools pod fetches the TSV from the workbench Service.)
3. **Interactive figures**: on a study's **Visualizations** tab, the embedded Plotly figures (e.g. showcase-2's dry-mass composition) render inline — served under `/workbench/reports/figures/...` so they resolve to the dashboard, not the co-tenant PTools at the ALB root.
4. **Visualization preview**: Show a `demo()` method rendering instantly against synthetic data

### API
`GET /workbench/api/visualization-classes` → 58 visualization classes
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
   - **Reproducible, git-tracked runs** — Simulations DB: **36 runs** with full provenance (35 seeded + the 1 remote run we just landed live in Segment 6 — a nice callback: the number ticked up in front of you)
   - **AWS GovCloud at scale** — the entire dashboard is served in-cluster; remote runs go to sms-api on GovCloud

### Narration
> "vivarium-workbench is a simulator-agnostic research notebook — and it runs anywhere, from a laptop to a GovCloud Kubernetes cluster. Today we saw v2ecoli, but the same dashboard serves viva_munk colony physics, ketchup kinetic fitting, copasi ODE models, and BiRD reactor transport. All in one UI, all git-tracked. Questions?"

---

## 10. After the Demo

```bash
# Terminal 1: Ctrl+C to stop the SSM tunnel.
# (No local server to stop — the dashboard runs in-cluster.)
```

---

## Appendix A: Architecture Elevator Pitch (30 seconds)

> "vivarium-workbench is a web UI for process-bigraph workspaces. Three layers: the simulation engine — process-bigraph — runs the science. The tooling — this dashboard — orchestrates, renders, and commits. The data — the workspace — is the single source of truth. Every action is committed to git. And it runs anywhere: laptop for development, GovCloud Kubernetes for the real thing."

---

## Appendix B: Anticipated Q&A

**Q: Do I need to be a v2ecoli expert to use this dashboard?**
A: No — the dashboard is simulator-agnostic. v2ecoli is the demonstration workspace. A totally different model gets the same UI.

**Q: How do I add my own simulator?**
A: `pip install` your pbg-* package, declare it in `workspace.yaml` imports, refresh. Processes, composites, and visualizations appear automatically. No dashboard code changes.

**Q: What if my simulation takes hours?**
A: The remote run pipeline (sms-api) offloads to AWS GovCloud. Your browser can close while the run progresses. Results land back with full provenance.

**Q: Is the dashboard open source?**
A: Yes — MIT licensed. GitHub: `vivarium-collective/vivarium-workbench` (currently `vivarium-dashboard`).

**Q: How do I share results with a collaborator?**
A: Push the branch, or export a self-contained HTML report, or use `vivarium-workbench-publish` for a static read-only bundle.

**Q: What is the tunnel doing under the hood?**
A: `sms-proxy.sh -s smsvpctest` resolves the batch submit node ID and internal ALB DNS from the `smsvpctest-*` CloudFormation stacks, then runs an SSM `StartPortForwardingSessionToRemoteHost` session. The submit node forwards `localhost:8080` to the internal ALB, which path-routes `/workbench` to the dashboard, `/docs` to the SMS API, and `/`, `/sms/sms.html` to PTools — all through one local port.

**Q: Why is the dashboard reachable at `/workbench` and not the root?**
A: The ALB path-routes multiple services on one host. The dashboard is served under the `/workbench` base path (`--base-path /workbench`); all its links and assets are base-path-aware.

**Q: Do I need the tunnel for the whole demo?**
A: Yes — the dashboard itself is remote, so the tunnel is required for every segment (unlike the old local flow). The PTools Omics Viewer (Segment 7) is remote too — it's the `ptools` Deployment in `sms-api-stanford-test` at the ALB root, reached over the same tunnel; no local container.

---

## Appendix C: Quick-Reference Timing

| Time | Segment | Page | Key Click |
|------|---------|------|-----------|
| 0:00 | Tunnel | Terminal | `sms-proxy.sh -s smsvpctest` |
| 0:20 | Open browser | — | `http://localhost:8080/workbench` |
| 1:00 | **1. Intro** | Home | Rail, workspace chip |
| 3:00 | **2. Registry** | Registry → Modules | packages |
| 4:00 | **2. Registry** | Registry → Processes | **173** processes from 7 packages |
| 6:00 | **3. Composites** | Composites | baseline → millard → pdmp |
| 8:00 | **3. Composites** | Composites | External: ketchup, chemotaxis |
| 9:00 | **4. ParCa** | Composites → Explore on parca | **9-step** pipeline |
| 10:00 | **4. ParCa** | Explorer → Run | Optional: fast mode (~15s) |
| 12:00 | **5. Investigations** | Investigations | **8** investigations |
| 13:00 | **5. Investigations** | v2ecoli-baseline-showcase | DAG, tests, charts |
| 15:00 | **6. Simulations DB** | Simulations DB | **35** seeded runs, emitter pills, ☁️ earned live |
| 16:00 | **6. Simulations DB** | → Run on remote (pinned) | Pinned build → Ray MNP → land |
| 18:00 | **7. Analyses** | Analyses | **58** viz classes, 3D, PTools |
| 19:00 | **8. Wrap-up** | — | Recap + Q&A |
| 20:00 | **Q&A** | — | — |

> Add a few seconds of slack per tab for tunnel latency.

---

## Appendix D: Demo Environment State

The remote pod serves a pre-seeded workspace from its EBS PVC. Local demo
artifacts under `demos/v2ecoli/` apply to the **offline** flow (Appendix G) only.

| File | Purpose | Side Effects |
|------|---------|-------------|
| `WALKTHROUGH.md` | This file | None |
| `PLAN.md` | Presenter + self-guided plan | None |
| `NOTES.md` | Presenter quick reference | None |
| `verify_demo.py` | Pre-demo verification (offline flow) | None |
| `populate_demo_runs.py` | Seeds synthetic demo runs (offline flow) | Writes `.pbg/composite-runs.db` (gitignored) |
| `prep_remote_build.py` | Pre-builds sms-api Docker image (offline flow) | Writes `.demo_state.json` (gitignored) |
| `prep_remote_land.py` | Pre-lands a remote run (offline flow) | Writes `.pbg/composite-runs.db` (gitignored) |
| `.gitignore` | Prevents committing generated state | None |

---

## Appendix E: Troubleshooting (remote flow)

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `http://localhost:8080/workbench` refuses / times out | Tunnel down or SSO expired | Check the `sms-proxy.sh` terminal; re-run `stanford test`, then restart the tunnel |
| Tunnel fails: "credentials not valid" | SSO session expired | Re-run `stanford test` |
| Tunnel fails: "Could not find submit node" | Wrong stack or region | Confirm `AWS_PROFILE=stanford-sso` and `AWS_DEFAULT_REGION=us-gov-west-1` |
| Tab slow to populate | SSM tunnel latency (~8s/GET) | Normal — wait a beat; the page is interactive |
| `cross-origin request forbidden` on any POST (Run, save) | Workbench pod missing `VIVARIUM_WORKBENCH_ALLOWED_ORIGINS` | Set `VIVARIUM_WORKBENCH_ALLOWED_ORIGINS=http://localhost:8080` on the workbench Deployment env and roll out (the ALB rewrites `Host` and omits `X-Forwarded-Host`, so same-origin/`--trust-proxy` cannot admit the origin) |
| Composite Explorer / loom panel 500s | Deployed image predates the `bigraph-loom` overlay | Rebuild from `demo-v2ecoli` (`gh workflow run build-and-push.yml --ref demo-v2ecoli`), bump the overlay `newTag`, roll out |
| Study detail loads blank/unstyled | Deployed image predates the base-path fix | Rebuild + redeploy as above |
| Composite resolves to error | Missing deps for that composite | Skip; use `baseline`, `parca`, or `colony` |
| Simulations DB shows 0 runs | Workspace PVC not mounted / empty | Confirm the workbench pod has `/workspace` mounted; check `kubectl -n sms-api-stanford-test describe pod` |
| Run card still says "Run on remote (smsvpctest) / Requires GitHub login" | Pinned mode not enabled on the pod | Set `VIVARIUM_WORKBENCH_REMOTE_PINNED=1` + `_REMOTE_REPO_URL`/`_REMOTE_BRANCH`; verify `GET /workbench/api/remote-run-config` → `pinned:true` |
| "Could not resolve pinned build" | No built simulator for the pinned repo@branch | Confirm a completed build exists (`GET /core/v1/simulator/versions`); register/build one if absent |
| Run stuck in `queued` for long | AWS Batch provisioning the Ray MNP cluster (or ParCa dependency running) | Normal for a cold compute env; check the `smsvpctest-ray-mnp` queue + `describe-jobs` state (`RUNNABLE`=provisioning, `PENDING`=ParCa gate) |
| Run submit/land fails with 401 | Pinned mode off AND no GitHub session | Enable pinned mode (above), or log in via the GitHub button |
| "Run on remote" fails | sms-api unreachable in-cluster | Check `SMS_API_BASE` on the workbench Deployment; check sms-api pod health |
| PTools card shows error | Container not running | Mention as an integration example, skip |

Cluster access for the fixes above:
```bash
export AWS_PROFILE=stanford-sso AWS_DEFAULT_REGION=us-gov-west-1 \
  KUBECONFIG=/Users/alexanderpatrie/.kube/kube_stanford_test.yml
kubectl -n sms-api-stanford-test logs -f deploy/workbench
kubectl -n sms-api-stanford-test rollout status deploy/workbench
```
(`scripts/set-govcloud-env.sh smsvpctest` sets these env vars for you.)

---

## Appendix F: Presenter Must-Know

1. **Everything is remote**: the dashboard is a Kubernetes pod on GovCloud, reached at `http://localhost:8080/workbench` through the SSM tunnel. There is no local server in this flow.
2. **Tunnel**: `~/sms/sms-cdk/scripts/sms-proxy.sh -s smsvpctest` → `localhost:8080` → submit node → internal ALB → `/workbench` (dashboard), `/docs` (SMS API), `/` + `/sms/sms.html` (PTools).
3. **Auth**: `stanford test` in `~/.zshrc` sets `AWS_PROFILE=stanford-sso`, `AWS_DEFAULT_REGION=us-gov-west-1`, runs `aws sso login`.
4. **CSRF**: the pod carries `VIVARIUM_WORKBENCH_ALLOWED_ORIGINS=http://localhost:8080` — the ALB rewrites `Host`, so this allowlist is what makes POSTs work.
5. **Latency**: SSM-tunnel GETs can take several seconds; the page is interactive throughout.
6. **CLI name**: `vivarium-workbench` (the `vivarium-dashboard` name still works as a deprecated alias).
7. **ParCa live run**: Fast mode ~15s (7 TF conditions). Full mode ~2.4 min (51 conditions).
8. **Numbers** (173 processes, 28 composites, 8 investigations, **35** seeded runs, 58 viz) reflect the seeded workspace; confirm against the live remote before quoting exact figures. The run count grows by one per landed remote run.

---

## Appendix G: Local Dev (offline)

The original local-serve flow, preserved as a fallback for offline development
(no tunnel, no GovCloud). The dashboard serves a local v2ecoli workspace on
`localhost:8771`.

### G.1 Repository & Environment
```bash
git clone git@github.com:vivarium-collective/vivarium-dashboard.git ~/vivarium-app/vivarium-dashboard
cd ~/vivarium-app/vivarium-dashboard
git checkout demo-v2ecoli
git clone git@github.com:vivarium-collective/v2ecoli.git ~/vivarium-app/v2ecoli
uv sync --extra demo
```
Verify:
```bash
source .venv/bin/activate
python -c "import v2ecoli, viva_munk, pbg_ketchup, pbg_copasi; print('OK')"
```
Minimum hardware: macOS/Linux, 16 GB RAM, 5 GB free disk.

### G.2 Pre-Demo Seeding (one-time)
```bash
source .venv/bin/activate
python demos/v2ecoli/populate_demo_runs.py    # seeds synthetic demo runs
# (optional, needs the tunnel) pre-build + pre-land remote runs:
python demos/v2ecoli/prep_remote_build.py
python demos/v2ecoli/prep_remote_land.py
```

### G.3 Pre-Flight (every offline session)
```bash
cd ~/vivarium-app/vivarium-dashboard
python demos/v2ecoli/verify_demo.py                       # expect all checks pass
vivarium-workbench serve --workspace ~/vivarium-app/v2ecoli --port 8771
open http://localhost:8771
```
> The CLI accepts both `vivarium-workbench serve` and the deprecated
> `vivarium-dashboard serve` — same code.

For the offline flow, use `http://localhost:8771/...` in place of every
`http://localhost:8080/workbench/...` URL in Segments 1–8. Segment 6 remote runs
still need the tunnel (Section 1) since they call sms-api on GovCloud.

### G.4 After the Demo (offline)
```bash
kill $(lsof -ti:8771) 2>/dev/null
python demos/v2ecoli/populate_demo_runs.py    # reset demo runs if needed
```

### G.5 Offline Troubleshooting
| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| Page loads blank | Server not running | Check the `vivarium-workbench serve` terminal |
| "Connection refused" | Wrong port | Confirm `--port 8771` |
| ParCa Explorer blank iframe | bigraph-loom missing in the venv | `pip install bigraph-loom` (the git-main dep) |
| CSRF error on POST | Browser origin mismatch | Access via `localhost` (not `127.0.0.1`) |
| Port 8771 already in use | Prior server still running | `kill $(lsof -ti:8771)` |
