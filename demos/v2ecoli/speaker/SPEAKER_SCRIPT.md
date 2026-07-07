# vivarium-workbench Dashboard Demo — Speaker Script

**Tone**: Conversational, confident, technical-but-accessible. Not a sales pitch — a
walkthrough of real tooling solving real research problems.

**Total runtime**: 20 min + Q&A (target ~22 min with buffer)

**Presenter pre-flight** (do this BEFORE the audience arrives):
- [ ] Terminal 1: SMS API tunnel (`ptools-proxy.sh -s smsvpctest`)
- [ ] Terminal 2: `python demos/v2ecoli/verify_demo.py` (expect 39/39)
- [ ] Terminal 2: `vivarium-dashboard serve --workspace ~/vivarium-app/v2ecoli --port 8771`
- [ ] Browser: open `http://localhost:8771`, confirm page renders
- [ ] Close extra browser tabs, hide bookmarks bar, set font size comfortable for projection

---

## SLIDE 0 — Title Slide (Pre-session) :00

> **Show**: Black terminal or branded title card. Nothing interactive yet.

**Speaker**: (don't say anything — let audience settle)

---

## SLIDE 1 — Architecture (2 min) :00–:02

> **Show**: Simple 3-layer diagram or just the dashboard home page.
> If no diagram, show the dashboard itself and use it as the visual.

**Narration**:

"vivarium-workbench is a local web UI for process-bigraph workspaces. I'm going to
show you three layers working together.

First, the simulation engine — process-bigraph — runs the science. That's the
compute layer. Second, the tooling — this dashboard you're looking at —
orchestrates, renders, and commits. Third, the data — the v2ecoli workspace —
is the single source of truth.

Here's the key property: every action you take in the dashboard is committed to
git. Add a dataset? Committed. Create a study? Committed. Run a simulation?
Committed. It's a research notebook that leaves an audit trail."

**Talking points** (if time / Q&A):
- "This is NOT v2ecoli-specific. It works with ANY process-bigraph workspace —
  colony physics, kinetic fitting, ODE solving, bioreactor transport. I'll show
  you all of those today."
- "Generic tool, one UI, one type system, one git-tracked source of truth."

**Transition**: "Let me show you what's under the hood — starting with the Registry."

---

## SLIDE 2 — Registry: Modules (2 min) :02–:04

> **Show**: Dashboard → click **Registry** → **Modules** sub-tab.

**Narration**:

"This is the Registry — the catalog of everything installed. Look at these 11
packages. v2ecoli is the workspace itself — 55 biological processes. But there
are six others: viva_munk for colony physics, pbg_ketchup for kinetic parameter
estimation, pbg_copasi for ODE steady-state solving, BiRD for bioreactor
transport, pbg_torch for neural surrogates, and pbg_parsimony for capsule cell
geometry.

Seven different simulation packages. One dashboard. One type system."

**Stage direction**: Slowly scroll through the list, pointing to each package name.

**Transition**: "Now watch what happens when we look at the discovered registry…"

---

## SLIDE 3 — Registry: Processes (2 min) :04–:06

> **Show**: Click **Discovered registry** → **Processes** sub-tab.

**Narration**:

"174 Process classes from 10 different packages — all in one type system. Look
at the list — viva_munk's PymunkProcess for collision physics sits right next to
v2ecoli's PolypeptideElongation for translation. They're interchangeable
building blocks. Any process from any package can be wired into any composite.

To onboard a new simulator, you pip install it, declare it in workspace.yaml,
and refresh. It just appears. No dashboard code changes."

**Key number**: "174 Process classes from 10 different simulation packages."

**Transition**: "So they're all registered. What can you actually build with them?
Let's look at Composites."

---

## SLIDE 4 — Composites: Cell-Engine Swappability (2 min) :06–:08

> **Show**: Click **Composites** → point to total count.

**Narration**:

"30 runnable models across all packages. Let me show you why swappability matters."

> **Click `baseline`**

"This is the baseline whole-cell model — 55 processes, tFBA metabolism, the
reference architecture."

> **Click `baseline_millard`**

"Same architecture, but swap the metabolism engine. Instead of tFBA, this uses
Millard 2017 kinetic ODEs — 86 metabolites, enzyme kinetics. One cell engine
swapped for another."

> **Click `millard_pdmp_baseline`**

"And here's a third engine — a piecewise-deterministic Markov process
reformulation. Millard kinetics plus LQR control plus Poisson jump processes.
Three different cell engines, same composite framework."

**Key number**: "Three distinct cell engines — FBA, kinetic ODE, PDMP — all in
the same dashboard."

**Transition**: "And it gets better — the interface contract that makes this possible
also lets you swap the environment the cell lives in."

---

## SLIDE 5 — Composites: Reactor Coupler + External (2 min) :08–:10

> **Show**: Click `reactor_bird_coupled`, then `reactor_bird_coupled_millard`.

**Narration**:

"This is a reactor coupler — a whole-cell model coupled to a BiRD bioreactor.
Here's the baseline cell inside the reactor. And here — same reactor coupler,
DIFFERENT cell engine, the Millard one. The cell-side interface contract makes
this possible. You define the contract once, and any cell engine that satisfies
it plugs in."

> **Click `ketchup_baseline`, then `chemotaxis`**

"Completely different domains. ketchup_baseline is kinetic parameter fitting
with IPOPT — optimization, not simulation. chemotaxis is bacterial movement in
a 2D ligand gradient — spatial physics. Same dashboard. Same workflow."

**Key number**: "30 runnable models. One workflow — Composite → Run → View results —
for ANY simulator."

**Transition**: "Before the cell can run, though, it needs parameters. Let me show
you how ParCa got modularized."

---

## SLIDE 6 — ParCa: Modular Pipeline (2 min) :10–:12

> **Show**: Click **Explore** on the `parca` composite → opens Composite Explorer.

**Narration**:

"ParCa is the Parameter Calculator. It used to be a monolithic script —
thousands of lines, impossible to swap pieces out. Now it's 9 modular Steps."

> **Point to each step in the pipeline graph**

"Step 1: Initialize — scatters flat files into sim_data. Step 2: Input adjustments.
Step 3: Basal specs — fits the minimal-medium condition. Step 4: TF condition
specs — 51 transcription-factor conditions. Step 5: Fit condition — bulk
distributions and translation supply. Step 6: Promoter binding — CVXPY
optimization. Step 7: Adjust promoters — couple to genome position. Step 8: Set
conditions — extract, compute, merge. Step 9: Final adjustments — kinetic
constants for the online model."

"Every Step is independently registered, independently testable, and
independently swappable. Step 6 uses CVXPY. Want to swap it for a PyTorch
optimizer? Replace one Step class, wire the same ports. The rest of the
pipeline doesn't change."

**Key number**: "43 state entries across 9 modular Steps."

> **Optional**: Click **Run** tab → `mode: fast, cpus: 4` → ~15-second live run.

**Transition**: "Now let me show you how we organize research on top of this —
Investigations and Studies."

---

## SLIDE 7 — Investigations: Research DAG (2 min) :12–:14

> **Show**: Click **Investigations** → point to 8 investigations.

**Narration**:

"Eight investigations — each is a research arc, a collection of studies grouped
under a shared question. Let me open the baseline showcase."

> **Click `v2ecoli-baseline-showcase`**

"Six studies, connected as a DAG. Look at the edges — showcase-1-parca is
upstream of showcase-2-baseline-figures, which feeds showcase-3-variant-decide,
and so on. The DAG enforces dependency order. A downstream study literally
cannot proceed until its upstream passes its gate."

> **Click showcase-1-parca**

"Each study has pass/fail behavior tests. showcase-1 has three tests — all
passing. It verifies the ParCa builds with all 51 TF conditions, the cache
bundle is complete, and sim_data reproduces the reference."

> **Click showcase-4-variant-comparison**

"Here's a 5-variant perturbation sweep with overlaid charts. This study can't
exist until the three upstream studies all pass their gates."

**Key number**: "8 research arcs with dependency gates — a hypothesis can't
proceed until its upstream passes."

**Transition**: "Studies produce simulation runs. Let's see where those live."

---

## SLIDE 8 — Simulations DB: Run Provenance (2 min) :14–:16

> **Show**: Click **Simulations DB**.

**Narration**:

"Every simulation run — local or remote — lives here. 52 runs in this table."

> **Point to columns**: Investigation, Study, Run, Location, Origin, Emitter, Time, Status.

"Look at the Emitter column — those colored pills tell you the storage backend.
SQLite in gray, Parquet in amber, XArray in teal. Three different emitter
backends, side-by-side in the same table."

> **Point to Origin column — the blue ☁️ pills**

"And these three blue cloud badges — those are remote runs. They ran on AWS
GovCloud, not on my laptop. Full provenance: simulation ID, experiment ID,
Ray backend, S3 URI. Every run is traceable from git commit hash to Docker
image to simulation results."

> **Point to status variety**: failed, running.

"Four failed runs, one currently running — the BiRD reactor with the Millard
cell engine. All visible, all traceable."

**Key number**: "52 runs, 3 emitter backends, local and remote side-by-side."

**Transition**: "Let me show you what a remote run looks like end-to-end."

---

## SLIDE 9 — Remote Run Pipeline (2 min) :16–:18

> **Show**: From any study page, click **"Run remotely"**.

**Narration** (speak while the pipeline runs):

"This is the browser-driven thin-client pipeline. Three phases, all driven
from your browser through an SSM tunnel to AWS GovCloud.

Phase 1 — building. It pushes the current branch to GitHub, registers a Docker
build on sms-api, and polls until the image is ready. Typically 1–2 minutes
for a cached build.

Phase 2 — running. It submits the simulation to sms-api and polls for
completion. The compute happens on AWS — your laptop can close.

Phase 3 — landing. It downloads the results from S3 and records them in the
study's runs.db with full git provenance.

The pipeline is stateless. No server-side queue. The browser drives the whole
thing through the SSM tunnel. Every run is reproducible: git commit hash →
exact Docker image → exact simulation results."

> **If tunnel is down**: Skip live run. Show the 3 pre-landed remote ☁️ runs instead.
> Narrate the architecture while pointing to them.

**Talking point**: "Extensibility — push a branch, click 'Run remotely', and
sms-api builds the Docker image from your exact code. Full reproducibility."

**Transition**: "Simulations produce data. Let's see how we visualize it."

---

## SLIDE 10 — Analyses: Visualization Gallery (2 min) :18–:20

> **Show**: Click **Analyses**.

**Narration**:

"58 visualization classes — everything from 3D viewers to network graphs to
time-series plots. Every visualization is a registered class with demo() and
render() methods. That means you can preview before you run — instant feedback
against synthetic data."

> **Show 3D viewer** if available; otherwise show visualization class list.

"PTools bridges the dashboard to external analysis tools through a URL template.
If you have the Pathway Tools Cellular Overview running, you can overlay study
omics data on the E. coli metabolic map — right from the dashboard."

**Key number**: "58 visualization classes — 3D viewers, network graphs,
time-series, omics overlays."

**Transition**: "Let me bring it all together."

---

## SLIDE 11 — Recap & Architecture Pillars (1 min) :20–:21

> **Show**: Rapid click-through of all tabs as visual recap.

**Narration**:

"Five architecture pillars. One dashboard, many simulators — 174 processes from
10 packages. Swappable cell engines — baseline, Millard, PDMP, all sharing the
same reactor coupler. Modular pipelines — ParCa in 9 independently swappable
Steps. Reproducible, git-tracked runs — 52 runs with full provenance. And AWS
GovCloud at scale, local for development — the browser-driven remote pipeline
through smsvpctest.

vivarium-workbench is a simulator-agnostic research notebook. Today you saw
v2ecoli — but the same dashboard serves colony physics, kinetic fitting, ODE
models, and bioreactor transport. All in one UI, all git-tracked."

**Transition**: "Questions?"

---

## SLIDE 12 — Q&A :21–:25

> **Show**: Return to dashboard home page, ready to click anywhere audience asks about.

**Anticipated Q&A** (from walkthrough Appendix B):

| Q | A |
|---|---|
| Do I need to be a v2ecoli expert? | No. The dashboard is simulator-agnostic. Any pbg workspace gets the same UI. |
| How do I add my own simulator? | `pip install` your pbg-* package, declare it in `workspace.yaml`, refresh. No dashboard code changes. |
| What if my simulation takes hours? | Remote run pipeline offloads to AWS GovCloud. Your laptop can close. Results land back with full provenance. |
| Is the dashboard open source? | Yes — MIT licensed. GitHub: `vivarium-collective/vivarium-workbench`. |
| How do I share results? | Push the branch, export a self-contained HTML report, or use the static publish bundle. |
| What's the SMS API tunnel doing? | SSM port-forwarding through a batch submit node to an internal ALB that routes to sms-api. Single port, full API surface. |
| Do I need the tunnel for everything? | No — only the remote run segment requires it. Everything else works offline. |

---

## POST-DEMO :25

**Actions** (after audience leaves):
```bash
# Terminal 1: Ctrl+C to stop SMS API tunnel
# Terminal 2: Ctrl+C to stop dashboard
kill $(lsof -ti:8771) 2>/dev/null
```

---

## QUICK-REFERENCE CARD

| Time | Slide | What to Show | Narration Hook |
|------|-------|-------------|----------------|
| 0:00 | 0 | Title card | (settle) |
| 0:30 | 1 | Dashboard home | "Three layers…" |
| 2:00 | 2 | Registry → Modules | "11 packages…" |
| 4:00 | 3 | Registry → Processes | "174 process classes…" |
| 6:00 | 4 | Composites → cell engines | "Three cell engines…" |
| 8:00 | 5 | Composites → reactor/external | "Same reactor, different cell…" |
| 10:00 | 6 | ParCa Explorer | "9 modular Steps…" |
| 12:00 | 7 | Investigations → showcase | "DAG with dependency gates…" |
| 14:00 | 8 | Simulations DB | "52 runs, 3 emitters…" |
| 16:00 | 9 | Run remotely | "Browser-driven pipeline…" |
| 18:00 | 10 | Analyses | "58 visualization classes…" |
| 20:00 | 11 | Recap all tabs | "Five architecture pillars…" |
| 21:00 | 12 | Q&A | "Questions?" |
| 25:00 | — | Cleanup | (post-demo) |

## EMERGENCY FALLBACKS

| If… | Then… |
|------|-------|
| Dashboard won't load | Use static `reports/index.html` as visual; it has all tabs in read-only mode |
| SMS API tunnel down | Skip Slide 9 (remote run). Show pre-landed ☁️ runs in Slide 8. Narrate the architecture. |
| PTools not running | Skip the omics viewer mention. Mention it as an integration example. |
| Composite fails to resolve | Switch to a known-good composite (baseline, colony). |
| Browser crashes | `open http://localhost:8771` — stateless; pick up where you left off. |
| Someone asks about a process you don't know | "That's a great question — the Registry page has the full schema for every process. Let's look it up." |
