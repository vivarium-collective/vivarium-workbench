# vivarium-workbench — v2ecoli GovCloud Demo: Full Narration

**What this is**: a fully scripted, word-for-word narration for the remote
GovCloud demo. `WALKTHROUGH.md` is the technical run-sheet — what to click,
which API each click hits, the last-verified numbers. This file is the
companion *script* — what to say, beat for beat, so you never have to
improvise a sentence or guess at a save if something misbehaves. Read
WALKTHROUGH.md once beforehand for the "why"; use this file live for the
"what to say."

**Target runtime**: ~20 min + Q&A (~25 min total).
**Demo target**: remote `/workbench` on the `smscdk` GovCloud stack, reached at
`http://localhost:8080/workbench` via `sms-proxy.sh -s smscdk`.
**Numbers used below** (173 processes / 7 packages, 28 composites, 8
investigations, 35 seeded runs → 36 after Segment 6 Part B, 58 visualization
classes) are WALKTHROUGH's/VERIFICATION_REPORT's last-verified (2026-07-14)
figures. **Re-confirm each against the live deployment before recording** —
if any has drifted, swap the number in the relevant line below; the sentences
around it don't need to change.

---

## 0. Pre-Session Checklist (silent — no narration)

Do this before the audience arrives. Nothing below is spoken.

- [ ] `stanford` (sets `AWS_PROFILE=stanford-sso`, `AWS_DEFAULT_REGION=us-gov-west-1`, runs `aws sso login`)
- [ ] Terminal 1: `AWS_PROFILE=stanford-sso AWS_DEFAULT_REGION=us-gov-west-1 ~/sms/sms-cdk/scripts/sms-proxy.sh -s smscdk` — confirm the banner lists `/workbench`, `/`, `/sms/sms.html`, `/docs` on port 8080
- [ ] Terminal 2: `./demos/v2ecoli/scripts/ensure_latest_main_build.sh` — must print `MATCH ✓` or `BUILT ✓` (allow ~13 min lead time if it has to build)
- [ ] Browser: `open http://localhost:8080/workbench` — confirm `<title>v2ecoli</title>` renders
- [ ] **Pre-warm the Registry tab**: click Registry once now and let it fully load. The first hit builds the v2ecoli core in a workspace subprocess and can take up to ~15s cold; a warm retry is instant. Doing this now means Segment 2 is snappy live.
- [ ] Optional: pre-launch the Segment 6 Part B remote run now (see Segment 6 notes) if you'd rather show a completed run than watch the full ~13 min live
- [ ] Close extra tabs, hide the bookmarks bar, bump font size for projection

---

## 1. Segment 1 — Introduction (2 min)

> **Show**: `http://localhost:8080/workbench` home page. Point to the left rail and the workspace name chip.

**Narration**:

### Opener

> Alright, we're going to show you the sms-api using our v1 and v2 simulators (otherwise known as vEcoli and v2ecoli respectively), 
    along with what we have called the vivarium "workbench"

> "vivarium-workbench is a web UI for process-bigraph workspaces, which for the purposes of this demo is the sms-ecoli repo.
> when it comes to what were presenting, There are several fundamental layers 
>
> compute: process-bigraph: the compositional framework that provides a domain-agnostic simulation engine runtime
> tooling: workbench: end-user interface that provides a generalized scientific workflow and orchestration
> data: sms-ecoli workspace: defines the model itself as well as simulation design document artifacts that can be consumed by process-bigraph.
> 
> Here's the key property: every action you take in the dashboard is committed to git. Add a dataset? Committed. Create a study? Committed. Run a simulation? Committed. It's a research notebook that leaves an audit trail.
>
> And one more thing before we dive in: what you're looking at right now is not running on my laptop. It's served from a Kubernetes pod on AWS GovCloud. I'm reaching it through an authenticated SSM tunnel. The same dashboard that runs locally for development runs in-cluster for production use — nothing about the UI changes."

> **Point to the left rail** (9 pages: Sources, Registry, Composites, Investigations, Simulations DB, Analyses, Studies, Branch, Composite Explorer) **and the investigation switcher at the top of the rail.**

**Talking Points** (full sentences, use if there's time or it comes up in Q&A):

> "This is not v2ecoli-specific — it's a generic tool. It works with any process-bigraph workspace: colony physics, kinetic parameter fitting, ODE solving, bioreactor transport. I'll show you examples of all of those today, all living side-by-side in this one workspace."
>
> "One dashboard, one type system, one git-tracked source of truth, deployed anywhere from a laptop to a GovCloud cluster."

**Transition**: "Let me show you what's under the hood — starting with the Registry."

---

## 2. Segment 2 — Registry: Simulator Agnosticism (3 min)

> **Show**: click **Registry** → **Modules** sub-tab.

**Narration**:

> "This is the Registry — the catalog of everything installed in this workspace. Look at these packages: v2ecoli is the workspace itself, the E. coli whole-cell model. But there are six others sitting right alongside it — viva_munk for colony physics and chemotaxis, pbg_ketchup for kinetic parameter estimation using IPOPT, pbg_copasi for ODE steady-state solving, pbg_bioreactordesign for bioreactor transport, pbg_torch for neural surrogate models, and pbg_parsimony for capsule cell geometry.
>
> Seven different simulation packages. One dashboard. One type system."

> **Fallback line**, only if the tab is slow on first load: "This tab builds the workspace's core the first time it's hit in a session — give it a second, it's warming up in the background."

> **Show**: click **Discovered registry** → **Processes** sub-tab. Scroll slowly.

**Narration**:

> "Now here's the discovered registry — every Process class across all seven packages, interspersed. Look — viva_munk's PymunkProcess, which handles collision physics for bacterial chemotaxis, sits right next to v2ecoli's PolypeptideElongation, which handles ribosome translation kinetics. Completely different domains. Same list, same type system, same wiring rules.
>
> Any process from any package can be composed into any composite. To onboard a brand-new simulator, you pip install it, declare it in workspace.yaml, and refresh — it just appears. No dashboard code changes required."

**Key Number**: "**173** Process classes from **7** different simulation packages, all in one type system."

**Transition**: "So they're all registered — what can you actually build with them? Let's look at Composites."

---

## 3. Segment 3 — Composites: Swappability (3 min)

> **Show**: click **Composites**.

**Narration** (opening):

> "This page lists every runnable model across every package in the workspace. Let me show you why swappability matters, starting with the cell engine itself."

### Cell-engine swappability

> **Click `baseline`.**

> "This is the baseline whole-cell model — 55 processes, tFBA-based metabolism. This is the reference architecture."

> **Click `baseline_millard`.**

> "Same architecture — but swap the metabolism engine. Instead of flux-balance analysis, this one uses Millard 2017 kinetic ODEs, tracking 86 metabolites with explicit enzyme kinetics. One cell engine swapped for another, same surrounding composite."

> **Click `millard_pdmp_baseline`.**

> "And a third engine here — a piecewise-deterministic Markov process reformulation. Millard kinetics, plus LQR control, plus Poisson jump processes for stochastic events. Three genuinely different mathematical approaches to modeling the same cell, all wired into the same composite framework."

### Reactor-coupler swappability

> **Click `reactor_bird_coupled`.**

> "This is a reactor coupler — whole-cell models coupled to a BiRD bioreactor simulation. Here's the baseline cell engine living inside that reactor."

> **Click `reactor_bird_coupled_millard`.**

> "And here — same reactor coupler, different cell engine underneath, the Millard one this time. The cell-side interface contract is what makes this possible. You define the contract once, and any cell engine that satisfies it plugs straight in — no changes to the reactor side at all."

### External simulators

> **Click `ketchup_baseline`.**

> "This one's a completely different domain: kinetic parameter fitting using IPOPT, from the pbg_ketchup package. This isn't simulating a cell forward in time — it's an optimization problem. Same dashboard, same Composite → Run → View results workflow."

> **Click `chemotaxis`.**

> "And this is bacterial chemotaxis in a two-dimensional ligand gradient, from viva_munk — spatial physics, not biochemistry. Same dashboard again."

**Key Number**: "**28** runnable models — whole-cell engines, colony physics, kinetic fitting, ODE solving, all in the same catalog."

**Fallback line**, if a composite fails to resolve: "That one needs a dependency this session doesn't have loaded — let me switch to `baseline`, which we know is solid," then click `baseline` or `parca`.

**Transition**: "Three different cell engines, all sharing the same reactor coupler, all managed by the same dashboard. But before any of these can run, the cell needs its parameters calculated. Let me show you how that got modularized."

---

## 4. Segment 4 — ParCa: Modularization (2 min)

> **Show**: from Composites, click **Explore** on the `parca` composite — opens the Composite Explorer with the embedded bigraph-loom panel.

**Narration**:

> "ParCa is the Parameter Calculator — it used to be a single monolithic script, thousands of lines, impossible to swap any one piece out. Now it's nine modular Steps."

> **Point to each step in the pipeline graph as you name it:**

> "Step one, Initialize — scatters flat input files into sim_data. Step two, Input Adjustments — a pure compute-and-merge step. Step three, Basal Specs — fits the minimal-medium growth condition. Step four, TF Condition Specs — fits fifty-one separate transcription-factor conditions. Step five, Fit Condition — bulk molecule distributions and translation supply. Step six, Promoter Binding — a CVXPY convex optimization. Step seven, Adjust Promoters — couples binding results to genome position. Step eight, Set Conditions — another pure extract-compute-merge step. Step nine, Final Adjustments — computes the kinetic constants the online simulation actually uses.
>
> Every one of these Steps is independently registered, independently testable, and independently swappable. Step six uses CVXPY today. Want to try a PyTorch-based optimizer instead? You replace that one Step class and wire it to the same ports — the other eight Steps don't change at all."

> **Optional live run**: click the **Run** tab, set `mode: fast, cpus: 4, debug: true`, click run — completes in about 15 seconds. While it runs: "This is fast mode — seven of the fifty-one TF conditions, just to prove the pipeline executes end-to-end live. Full mode runs all fifty-one and takes about two and a half minutes."

**Key Number**: "**43** state entries flowing across **9** modular Steps — each one independently testable and swappable."

**Fallback line**, if the loom/Explorer panel errors or is blank: "This panel needs a rendering library baked into the deployed image — if it's not showing, I'll narrate the nine steps from here instead," then continue verbally from the step list above without the visual.

**Explorer sub-tabs**, mention in passing: "Structure shows the pipeline graph, Run lets you launch with parameter overrides, History shows past runs — the same three sub-tabs on every composite in this workspace."

**Transition**: "Now let me show you how we organize actual research on top of all this — Investigations and Studies."

---

## 5. Segment 5 — Investigations & Studies (3 min)

> **Show**: click **Investigations**.

**Narration**:

> "Eight investigations right now. Each one is a research arc — a collection of studies grouped under a shared scientific question. 
> Let me open the baseline showcase."

> **Click `v2ecoli-baseline-showcase`.**

> "Here's the detail panel — status, a report button, a notebook download, and an 'about this investigation' disclosure if you want the full write-up.
>
> Now look at the DAG below it: six studies, connected by dependency edges. It's not a straight line — it fans out. `showcase-1-parca` feeds into `showcase-2-baseline-figures`, and from there it branches into three parallel children: `showcase-3-variant-decide`, `showcase-4-variant-comparison`, and `showcase-6-equivalence-large`. And `showcase-5-next-direction-decide` depends specifically on `showcase-4`.
>
> Here's the mechanism that matters: `showcase-2` literally cannot proceed until `showcase-1` passes its gate. The DAG isn't just a picture of the research plan — it enforces the order."

> **Click `showcase-1-parca`.**

> "Every study carries pass/fail behavior tests. This one has three, all passing: `parca-builds-full-51-conditions`, `cache-bundle-complete`, and `sim_data-reproduces-parca-comparison`. Below that, the rendered figures — the source manifest, the simdata summary, the cache bundle contents."

> **Click `showcase-4-variant-comparison`.**

> "And here's a five-variant perturbation sweep, with overlaid charts comparing them. This study literally could not have been created until its three upstream studies all passed their own gates."

**Key Number**: "**8** research arcs, each with dependency gates — a hypothesis genuinely cannot proceed until its upstream passes."

**Fallback line**, if a study-detail iframe loads blank or unstyled: "That's a base-path rendering issue on an older deployed build — let me open a different study," then click `showcase-1-parca` or `showcase-4-variant-comparison` instead.

**Transition**: "Studies produce simulation runs. Let's go see where every one of those lives."

---

## 6. Segment 6 — Simulations DB & Remote Runs (3 min)

This segment has two parts. Part A tours the run ledger — this always works,
no live compute involved. Part B runs a brand-new simulation on GovCloud
against a pinned, already-built simulator image. Decide before the session
whether you'll run Part B live or show a pre-landed run — see the note below.

### Part A — Simulations DB (tour)

> **Show**: click **Simulations DB**.

**Narration**:

> "Every simulation run in this workspace — local or remote — lives in this one table. Right now there are 35 seeded runs; that count is about to tick up by one when we land a live run in a minute.
>
> Look at the columns: Investigation, Study, Run, Location, Origin, Emitter, Time, Status. The Emitter column tells you the storage backend for that run's results — this seed has six Parquet runs, three SQLite runs, three XArray runs, and twenty-three with no recorded emitter. Any emitter backend, same table, side by side.
>
> Now look at Origin. Every one of these 35 seeded runs is local. You'll see a cloud icon appear in that column the moment we land a live remote run in Part B — there are none pre-staged. That's deliberate: the cloud badge is earned live, not faked.
>
> And status varies too — thirty-one completed, one still marked 'complete' with a slightly different label, and three failed. All visible, all traceable, nothing hidden."

**Key Number**: "**35** seeded runs, **3** emitter backends, about to become **36** with a live cloud-origin run."

### Part B — Live Remote Run (pinned build)

> **Decision point**: a full live run is ~13 minutes (Ray provisioning ~8 min +
> run ~5 min) — too long to sit through in real time in front of an audience.
> Either (a) pre-launch this run during the pre-session checklist and simply
> show its progress/completed state here, or (b) kick it off live now and
> narrate the architecture while it runs in the background, checking back
> at the end of the demo to land it. Script below assumes you're narrating a
> run that's either just-launched or already progressing.

> **Show**: open a study (e.g. `showcase-2-baseline-figures`), scroll to the run card.

**Narration**:

> "With pinned mode enabled on this deployment, the run card reads 'Run against pinned build — main at commit 70b5ec3' — and right underneath it: 'No push or GitHub login required.' That's new; the old flow used to stop here and ask you to authenticate.
>
> I'll leave Generations and Seeds at 1 and 1, keep Run ParCa checked, and click Run on remote, pinned."

> **Click ▶ Run on remote (pinned).**

> "Notice it goes straight to 'Using pinned build… submitting run…' — no login prompt. The old blocker is gone entirely.
>
> Now watch the phases. The dashboard is polling sms-api; sms-api owns all of the actual async compute.
>
> Build resolves instantly — checkmark — because the pinned, already-built simulator image is reused. Nothing rebuilds for this run.
>
> Run moves to queued. That means AWS Batch is provisioning a transient Ray cluster behind the scenes — that's the multi-node-parallel provisioning state — while a ParCa dependency job runs as a gate. This step is the slow one; expect a few minutes here.
>
> Once the Ray head comes up, it executes the actual E. coli ensemble, and the state moves to running, then done."

> **Fallback line**, if the run stays queued for an unusually long time or AWS Batch can't provision capacity: "This is provisioning a compute cluster on demand, so it's occasionally slower than usual — while we wait, let me show you a run that landed in an earlier session," then scroll to a previously-landed ☁️ run in the Simulations DB table and narrate from there using the Segment 6 architecture language above.

> Once done — **click ⬇ Land results locally.**

> "This downloads the result store from S3 and records it in this study's runs database with full git provenance."

> **Show**: back in Simulations DB, point to the new row.

> "And there it is — the landed run now carries a remote cloud-origin badge, with full provenance: deployment `smscdk`, a simulation ID, and `backend: ray`. That's the cloud pill from Part A, now earned live."

**Narration** (architecture summary, use if Part B is skipped or still running at wrap-up):

> "One pinned, reproducible build — an exact git commit resolved to an exact Docker image, already built on GovCloud. From the dashboard we can submit any number of simulation configs against that single build, each one spinning up its own transient Ray cluster. No push, no rebuild, no login — the entire thing is driven from the browser, which talks to sms-api in-cluster. Every landed run traces back to that exact commit."

**Talking Points** (full sentences):

> "Any simulator, any emitter backend — SQLite, Parquet, XArray — any scale from a laptop to AWS GovCloud, all in the same table, side by side."
>
> "It's one deployment: dashboard, sms-api, and PTools all sit behind a single internal load balancer, reached through one SSM tunnel on localhost 8080."
>
> "Reproducibility is the whole point here: the run targets a pinned commit's prebuilt image, sms-api provisions a transient Ray cluster for the ParCa-plus-ensemble pipeline, and results land with full git provenance — no per-run build, no credentials needed."

**Key Number**: "**35** seeded runs, **3** emitter backends, plus the **one** cloud-origin run we just landed live on a Ray cluster."

**Transition**: "Simulations produce data. Let's see how we visualize it."

---

## 7. Segment 7 — Analyses (2 min)

> **Show**: click **Analyses**.

**Narration**:

> "This is the visualization class gallery. Every visualization here is a registered class with a demo method and a render method — meaning you can preview it before you ever run a simulation, against synthetic data, for instant feedback."

### PTools Omics Viewer

> **Click Launch** on the `showcase-2-baseline-figures` row of the "Pathway Tools — Omics Viewer" card. A new tab opens.

**Narration**:

> "This opens the real, live EcoCyc Cellular Overview — the full E. coli metabolic map, served by Pathway Tools running remotely in the same cluster. Painting our study's omics data directly onto this map is the next step in this integration — right now the Launch button gets you to the live map itself, which is already the harder half of the problem: reaching a proprietary external tool from inside our dashboard, live, over the same tunnel."

> Use this exact framing regardless of whether the map visibly paints with data — it is accurate either way, and it means you never have to react live to whether the overlay renders.

### Interactive figures

> **Show**: on a study's Visualizations tab, an embedded Plotly figure — e.g. showcase-2's dry-mass composition chart.

**Narration**:

> "These figures are fully interactive — you can zoom, hover, pan — and they're served directly from the dashboard under its own report path, not from the co-tenant Pathway Tools service at the root of this ALB. So they load instantly and independently of anything PTools-related."

### Visualization preview

> **Show**: a `demo()` method rendering against synthetic data.

**Narration**:

> "And this is what I mean by preview-before-you-run — this chart is rendering against synthetic demo data right now, with no simulation behind it at all. Every one of the 3D viewers, network graphs, and time-series classes in this gallery works the same way."

**Key Number**: "**58** visualization classes — 3D viewers, network graphs, time-series, omics overlays, all registered the same way."

**Transition**: "Let me bring it all together."

---

## 8. Segment 8 — Wrap-up (2 min)

> **Show**: rapid click-through of all left-rail tabs as a visual recap.

**Narration**:

> "Let me tie this back to five architecture pillars.
>
> One dashboard, many simulators — the Registry showed us 173 processes from 7 packages, all in one type system.
>
> Swappable cell engines — Composites showed baseline, Millard, and PDMP metabolism, all sharing the exact same reactor coupler.
>
> Modular pipelines — ParCa is 9 independently swappable Steps, not a monolithic script anymore.
>
> Reproducible, git-tracked runs — Simulations DB now shows 36 runs with full provenance: 35 seeded, plus the one remote run we just landed live, in front of you, during this demo. That number ticking up in real time is the whole point.
>
> And AWS GovCloud at scale — this entire dashboard is served in-cluster, and remote runs go straight to sms-api on GovCloud, with no local build or push required.
>
> vivarium-workbench is a simulator-agnostic research notebook, and it runs anywhere — from a laptop to a GovCloud Kubernetes cluster. Today we saw v2ecoli, but the exact same dashboard serves viva_munk colony physics, ketchup kinetic fitting, copasi ODE models, and BiRD reactor transport. All in one UI, all git-tracked.
>
> Questions?"

---

## 9. Q&A

Full spoken answers — not just the table from WALKTHROUGH Appendix B.

**Q: Do I need to be a v2ecoli expert to use this dashboard?**
> "No — the dashboard itself is completely simulator-agnostic. v2ecoli is just today's demonstration workspace. Point it at a totally different process-bigraph workspace and you get the identical UI."

**Q: How do I add my own simulator?**
> "You pip install your pbg-* package, declare it in the workspace's workspace.yaml imports, and refresh. Processes, composites, and visualizations from that package appear automatically — there's no dashboard code to write or change."

**Q: What if my simulation takes hours?**
> "That's exactly what the remote run pipeline is for. sms-api offloads the compute to AWS GovCloud, your browser can close entirely while it runs, and the results land back later with full git provenance intact."

**Q: Is the dashboard open source?**
> "Yes — MIT licensed. It's on GitHub as vivarium-collective slash vivarium-workbench, though you'll still see it referred to as vivarium-dashboard in some places during the rename."

**Q: How do I share results with a collaborator?**
> "A few ways — push the branch so they can pull it themselves, export a self-contained HTML report, or use vivarium-workbench-publish to generate a static, read-only bundle they can open with no server at all."

**Q: What is the tunnel actually doing under the hood?**
> "sms-proxy dash s h smscdk resolves the batch submit node and the internal load balancer's DNS from the smscdk CloudFormation stacks, then opens an SSM port-forwarding session. The submit node forwards localhost 8080 to that internal ALB, which path-routes slash workbench to the dashboard, slash docs to the SMS API, and the root plus slash sms slash sms dot html to PTools — all through that one local port."

**Q: Why is the dashboard at slash workbench instead of the root?**
> "Because that same ALB is path-routing multiple services on one host. The dashboard is served with a base path of slash workbench, and every link and asset it generates is aware of that base path."

**Q: Do I need the tunnel for the whole demo?**
> "Yes, for this flow — the dashboard itself is remote from the first click, so the tunnel has to stay up for every segment. That's different from the old local-serve flow. The PTools Omics Viewer in Segment 7 is remote too, for the same reason."

**Cold-open elevator pitch** (use if someone walks in late, or asks "what is this" before you've set context):
> "vivarium-workbench is a web UI for process-bigraph workspaces. Three layers: the simulation engine — process-bigraph — runs the science. The tooling — this dashboard — orchestrates, renders, and commits. The data — the workspace — is the single source of truth. Every action is committed to git. And it runs anywhere: a laptop for development, a GovCloud Kubernetes cluster for the real thing."

---

## 10. After the Demo

No narration — Ctrl+C the SSM tunnel in Terminal 1. There's no local server to stop; the dashboard runs entirely in-cluster.

---

## 11. Quick-Reference Timing Card

| Time | Segment | Key Click | Narration Hook |
|------|---------|-----------|----------------|
| 0:00 | Tunnel (pre-session) | `sms-proxy.sh -s smscdk` | (silent) |
| 0:20 | Open browser | `http://localhost:8080/workbench` | (silent) |
| 1:00 | **1. Intro** | Home / rail | "Three layers…" |
| 3:00 | **2. Registry** | Registry → Modules | "Seven packages…" |
| 4:00 | **2. Registry** | Registry → Processes | "173 processes from 7 packages…" |
| 6:00 | **3. Composites** | baseline → millard → pdmp | "Three cell engines…" |
| 8:00 | **3. Composites** | reactor coupler → ketchup → chemotaxis | "Same reactor, different cell…" |
| 9:00 | **4. ParCa** | Composites → Explore on parca | "Nine modular Steps…" |
| 10:00 | **4. ParCa** | Explorer → Run (optional) | "Fast mode, ~15 seconds…" |
| 12:00 | **5. Investigations** | Investigations list | "Eight research arcs…" |
| 13:00 | **5. Investigations** | v2ecoli-baseline-showcase | "DAG with dependency gates…" |
| 15:00 | **6. Simulations DB** | Simulations DB (Part A) | "35 seeded runs…" |
| 16:00 | **6. Simulations DB** | Run on remote, pinned (Part B) | "Pinned build, no login…" |
| 18:00 | **7. Analyses** | Analyses gallery + PTools Launch | "58 visualization classes…" |
| 19:00 | **8. Wrap-up** | Rapid tab recap | "Five architecture pillars…" |
| 20:00 | **9. Q&A** | — | "Questions?" |
| 25:00 | — | Ctrl+C tunnel | (post-demo) |

Add a few seconds of slack per tab for SSM tunnel latency (~8s observed on first hit per tab).

---

## 12. Presenter Must-Know

Carried forward from WALKTHROUGH.md Appendix F — reference facts, not narration:

1. **Everything is remote**: the dashboard is a Kubernetes pod on GovCloud, reached at `http://localhost:8080/workbench` through the SSM tunnel. There is no local server in this flow.
2. **Tunnel**: `~/sms/sms-cdk/scripts/sms-proxy.sh -s smscdk` → `localhost:8080` → submit node → internal ALB → `/workbench` (dashboard), `/docs` (SMS API), `/` + `/sms/sms.html` (PTools).
3. **Auth**: `stanford` (no arg) in `~/.zshrc` sets `AWS_PROFILE=stanford-sso`, `AWS_DEFAULT_REGION=us-gov-west-1`, runs `aws sso login` for the smscdk stack (`stanford test` selects smsvpctest instead — do NOT use that form for this demo).
4. **CSRF**: the pod carries `VIVARIUM_WORKBENCH_ALLOWED_ORIGINS=http://localhost:8080` — the ALB rewrites `Host`, so this allowlist is what makes POSTs (Run, save) work at all.
5. **Latency**: SSM-tunnel GETs can take several seconds; the page stays interactive throughout — don't panic-refresh.
6. **CLI name**: `vivarium-workbench` (the `vivarium-dashboard` name still works as a deprecated alias, in case it comes up).
7. **ParCa live run timing**: fast mode ~15s (7 TF conditions). Full mode ~2.4 min (51 conditions).
8. **Numbers** (173 processes, 28 composites, 8 investigations, 35 seeded runs, 58 viz classes) reflect the seeded workspace as of the last verification pass — confirm against the live deployment before quoting exact figures on recording day. The Simulations DB run count grows by one for every landed remote run.

---

## Appendix — Known Risks Referenced Above

Sourced from `WALKTHROUGH.md` Appendix E and `demos/v2ecoli/bugs/` screenshots. Each risk already has its scripted fallback line inline in the relevant segment above; this table is just the map back to WALKTHROUGH's fuller troubleshooting/fix instructions if something needs an actual infra fix rather than a live save.

| Segment | Risk | Where the fallback line lives above |
|---|---|---|
| 2. Registry | Cold-start timeout on first hit | Segment 2, inline fallback line (mitigated by pre-warming in the checklist) |
| 3. Composites | A composite fails to resolve (missing deps) | Segment 3, inline fallback line |
| 4. ParCa | Loom/Explorer panel 500s on an older image | Segment 4, inline fallback line |
| 5. Investigations | Study-detail iframe loads blank/unstyled | Segment 5, inline fallback line |
| 6. Simulations DB | AWS Batch can't provision / run stuck queued | Segment 6 Part B, inline fallback line + pre-launch decision point |
| 7. Analyses | PTools Omics Launch doesn't paint the overlay | Segment 7, scripted as the primary framing (not a fallback — always say this) |

For infra-level fixes to any of the above (rebuilding the image, checking `kubectl` logs, resetting the tunnel), see `WALKTHROUGH.md` Appendix E in full.
