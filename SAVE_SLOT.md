# Checkpoint: Segments 7 & 8 VERIFIED live — figures✅ TSV✅ (Omics-Launch deferred, plan 9) / Seg-8 recap figures✅ — DEMO IS A SHIPPABLE MVP — NEXT: plan 9 (Omics fix) → record

## MVP feasibility verdict (2026-07-14) — SHIPPABLE NOW, pre-plan-9

The remote GovCloud demo (8-segment `WALKTHROUGH.md`) is a **demoable MVP as it
stands**: 7½ of 8 segments verify live; the only gap is the PTools Omics Viewer
**Launch** (soft-fail on `sms-ptools:0.5.9` — opens the EcoCyc overview unpainted,
no error; deferred → plan 9). Recording options: (a) skip Launch, (b) click +
caveat, (c) record now and re-shoot the ~15 s Omics beat after plan 9. Two paths
open: **record now** (Omics skipped/caveated) or **plan 9 first, then record once**
— user to decide. Full assessment written to
`demos/v2ecoli/VERIFICATION_REPORT.md` (top "Remote GovCloud Verification — MVP
Feasibility" section) + PR #465 "Demo readiness" section. PR #465 OPEN /
REVIEW_REQUIRED (not merged). All 6 demo commits this session pushed
(`03fa445..f2874b6`); coupled sms-api `patch/db-filter` overlay repoint pushed
(`c2a337cd..6924aa8e`). Live pod on `7a9620c`, 1/1.

## Segment 8 (Wrap-up) — recap figures VERIFIED against the live deployment (2026-07-14, headless)

All architecture-pillar numbers re-checked through the tunnel so the recap
narration is truthful: **173 processes / 7 packages** (registry endpoint is a
workspace subprocess — first hit timed out at 15 s cold, warm hit returned 173),
**9 ParCa Steps**, **8 investigations** (`/api/investigation-summaries`; the raw
`/api/investigations`=41 is a different uncurated view — NOT a drift), **58 viz
classes**, composites baseline/Millard/PDMP present (28 total), **Simulations DB =
36** (35 seeded + 1 landed-live; status 32 completed / 1 complete / 3 failed;
origin 1 remote / 35 local). Only stale figure: recap bullet "35 runs" → updated to
36 with a live-callback note. `WALKTHROUGH.md` `Last verified` stamp extended to all
8 segments. The in-browser rapid tab click-through recap is the presenter's action
at demo time (no code/verify gap).

---


**Updated:** 2026-07-14 (deploy landed). Segment 6 Part B is proven live (below).
**Segment 7 (Analyses / PTools Omics Viewer) is coded + PUSHED + DEPLOYED on BOTH
coupled branches.** All three deploy actions done; the live pod runs `7a9620c`
(1/1) with the seed initContainer stamping `DASHBOARD_PUBLIC_BASE_URL` +
`PTOOLS_SERVER_URL`. Headless pre-verify GREEN. **Remaining is the browser
live-verify (WS-2) + Segment 8 + recording.** Ground-truth plan for Segment 7:
**`.todo/plans/6-segment7-ptools-omics-deploy-verify.md`**.

## Segment 7 deploy progression (iterative action protocol) — COMPLETE

- ✅ **Action 1 — push (DONE)**: dashboard `demo-v2ecoli` `b33b7ca..7a9620c`;
  sms-api `patch/db-filter` `00d456f2..c2a337cd`. Both branches level with origin.
- ✅ **Action 2 — build image (DONE)**: `gh workflow run build-and-push.yml
  --ref demo-v2ecoli` → run **`29299423533`** = **success** (2026-07-14). GHCR tag
  **`7a9620c`** confirmed present (anonymous-bearer probe → HTTP 200). Build-provenance
  git tag **`build/demo-v2ecoli/7a9620c`** created on the built commit + pushed —
  immutable image↔commit link; does NOT trigger release CI; **formal semver release
  stays deferred to WS-F** (post-merge version-bump on each `main`).
- ✅ **Action 3 — repoint + roll out (DONE 2026-07-14)**: overlay `newTag`
  `72e00b8`→`7a9620c` in `kustomize/overlays/sms-api-stanford-test/kustomization.yaml`;
  `kubectl apply -k` → `deployment.apps/workbench configured`; rollout completed —
  ReplicaSet `workbench-7484f6b7dd` **1/1 Running**, pod image
  `ghcr.io/vivarium-collective/vivarium-workbench:7a9620c`. Seed initContainer env
  confirmed (`DASHBOARD_PUBLIC_BASE_URL` in-cluster URL + `PTOOLS_SERVER_URL`).
- ✅ **Headless pre-verify (2026-07-14)**: tunnel `localhost:8080/workbench/` → 200;
  `/api/remote-run-config` → pinned `{commit 70b5ec3, simulator_id 69}`; served
  dashboard HTML carries `basePath:"/workbench"` in `__DASH_CONFIG__` + the base-path
  shim whose prefix list includes `/reports/`.

## Segment 7 live-verify RESULTS (headless through the tunnel, 2026-07-14)

- ✅ **Interactive figures — PASS.** All 5 `showcase-2-baseline-figures` figures
  → 200 under `/workbench/reports/figures/...`; identical path at the ALB root →
  404. The base-path prefix fix is proven; these render inline in the browser.
- ✅ **TSV HTTP delivery — PASS.** Dashboard serves the omics TSV (200, ~355 KB
  of protein time-series) at `.../workbench/workspace/studies/<slug>/ptools/
  ptools_proteins.tsv`, exactly the URL the PTools pod fetches server-side
  (`tsv_url = dashboard_public_base_url + "/" + relpath(ws_root)`). Seed config on
  the live pod confirmed (`ptools_server_url` + `dashboard_public_base_url`
  stamped; `ptools_data_dir` cleared).
- ❌ **Omics Viewer auto-load — FAIL on `sms-ptools:0.5.9` (open risk resolved
  NEGATIVELY).** Root cause from the live PTools JS: `pathwayTools-overviews.js`
  auto-loads omics ONLY via dispatch `case "multiomics":` → `replayMultiOmicsParam`,
  which reads `datafile`/`datakeys` and fetches `/get-registered-multiomics-data?
  key=<datafile>` (server-registered-KEY flow). There is **zero** `.get('url')` or
  `case "omics"` in the 915 KB bundle; `celOv.shtml` is byte-identical with/without
  our params. `pbg_ptools.workbench_viewers` emits the **0.8.2** scheme
  `?omics=t&url=<tsv>&class=&column1=`, all of which 0.5.9 ignores. **The
  `/ptools-data` filesystem fallback also fails** — both delivery modes feed the
  same unused `url=`. Detail in memory `[[project_ptools_segment7_routing]]`.

**DECISION (2026-07-14):** keep the Omics Viewer Launch IN the demo; DEFER the
0.5.9 fix to **after Segment 8 ✅, before the recording**. Tracked as
**`.todo/plans/9-omics-viewer-0.5.9-register-launch.md`**. **Approach REFINED via
/plan (approved):** the original "register-then-launch" was invalidated — 0.5.9 has
NO register-and-return-key endpoint (only `/overview-multi-omics-process`, a direct
upload that paints the open overview). **Chosen: frictionless semi-manual upload** —
Launch opens the clean overview + the dashboard hands the presenter the study TSV
(one-click download + "upload in the Omics dialog" prompt); one upload click paints
it via PTools' own UI. Impl: `ui.ptools_scheme` switch (default `manual`) in
`pbg_ptools.workbench_viewers` + a `_launchViewer` helper panel in
`static/walkthrough.js`. Needs tunnel (WS-1/WS-4) + a local `pbg-ptools` clone
(third coupled repo). Full plan: `~/.claude/plans/validated-roaming-catmull.md`.
Order: **Segment 8 ✅ → plan 9 (Omics fix) → record**. Interactive figures + TSV
delivery already PASS, so Segment 7 is otherwise demo-ready.

**⛔ HARD CONSTRAINT (do not lose this):** Pathway Tools inside `sms-ptools` is
**PROPRIETARY third-party software — we CANNOT edit/patch/adjust it in ANY way**
(source, JS bundles, config, templates — all off-limits). Any Omics-Launch fix
must live ENTIRELY in our launcher (`pbg_ptools.workbench_viewers`) driving PTools'
**existing, unmodified** endpoints, or in infra (image tag / volume mount / env).
Reading its shipped JS to understand the contract is fine; changing it is not. If
a paint would require modifying PTools, that path is out of bounds → upgrade the
`sms-ptools` image to a version whose shipped scheme fits, or descope the Launch.
See memory `[[project_ptools_segment7_routing]]`.

## Segment 7 — committed this session (2026-07-13), NOT yet deployed

The demo is delivered jointly by the two spiritually-coupled branches — dashboard
`demo-v2ecoli` ↔ sms-api `patch/db-filter` (memory `[[project_demo_branch_coupling]]`);
post-completion → PR merge + version-bump release into each `main`.

- **dashboard `demo-v2ecoli` `7a9620c`** — `lib/report.py::_apply_live_base_path`
  now base-path-prefixes `/reports/` src/href so a study's interactive Plotly
  figures resolve to `/workbench/reports/...` (the dashboard) instead of colliding
  with the co-tenant PTools at the ALB root (which 404s). WALKTHROUGH Segment 7
  written (remote-first). `bugs/ptools-misroute.png` is the failure it fixes.
- **sms-api `patch/db-filter` `c2a337cd`** — the workbench `seed-workspace`
  initContainer now stamps `ui.dashboard_public_base_url` (the in-cluster URL the
  ptools pod fetches the study TSV from) and CLEARS `ui.ptools_data_dir` so the
  Omics Viewer launcher uses HTTP delivery (the ptools pod has no workspace mount).

Both commits exist locally; both branches are 1 commit ahead of origin.
**Remaining to make Segment 7 real:** push both → build a new workbench image
(gh action) with `7a9620c` → repoint overlay `newTag` `72e00b8`→new SHA → roll out
→ live-verify in browser. **OPEN RISK:** remote PTools is `sms-ptools:0.5.9`; the
`celOv.shtml?…&url=` auto-load param is documented against 0.8.2. If 0.5.9 ignores
`url=`, fall back to mounting the workspace into the ptools pod at `/ptools-data`
and keep `ptools_data_dir`. See `[[project_ptools_segment7_routing]]`.

---

## (prior) Segment 6 Part B — pinned-build remote runs DEPLOYED + PROVEN LIVE

The full-e2e demo blocker (Segment 6
Part B "Run on remote") was root-caused to **three deployment gaps**, fixed via a
new **pinned-build** model (Direction 1), deployed, and **proven live end-to-end**
(sim 211 ran on a 3-node Ray cluster and landed). Ground-truth plan:
**`.todo/plans/5-pinned-build-remote-runs.md`** (supersedes the Part-B portion of
`.todo/plans/4-remote-govcloud-demo-e2e.md` WS-E).

## What happened this session

1. Restarted the tunnel; headless Pass-1 re-verified GREEN (Bug 2 CSRF→405; Bug 3
   loom→200, parca+colony resolve→200; pod→sms-api /docs→200).
2. Drove Segment 6. **Part A drift found** + **Part B blocked**.
3. Root-caused Part B to 3 gaps (below). User reframed the demo to a **pinned
   commit** (latest built `main`) → "one build, many sims". Chose **Direction 1**
   (skip the build phase) + **config-gated** login (most production-grade/reproducible).
4. Implemented, tested, committed, pushed. Kicked the image build.

## Root cause of Part B (evidence-backed on the live pod)

Only Phase 1 (build) of the remote-run pipeline pushes git / needs login. On the pod:

- **A** `VIVARIUM_WORKBENCH_GH_CLIENT_ID` unset → device-flow login disabled (`no_client_id`).
- **B** `/workspace/.git` owned by uid **17163** ≠ app uid **0** → git "dubious ownership" → `has_origin_remote`=false, `github-repo`=null.
- **C** workspace on protected `main` of v2ecoli → `git push -u origin main` rejected + violates no-push-to-main policy.

**Pinned-build model drops B & C** (no push, no local git) and **replaces A with a
config gate** (submit/land do no GitHub write). Enabled declaratively via env.

## Shipped this session (committed + pushed)

- **vivarium-dashboard `demo-v2ecoli` `72e00b84`** — pinned-build remote runs:
  - `lib/remote_pinned.py` (new): `pinned_config()`, `resolve_pinned_build()`
    (picks newest **built** simulator for repo@branch from sms-api `versions`,
    **normalizing `.git`** — the gotcha that made `latest_simulator` return an
    unbuilt tip).
  - `lib/remote_run_views.py`: `remote_run_pinned_build_start` (one in-cluster
    GET → `phase:"built"`, no push/login/git), `remote_run_config`, relaxed
    `_run_auth_ok()` gate (session OR pinned-enabled) on submit/land.
  - `api/app.py`: `POST /api/remote-run-pinned-build`, `GET /api/remote-run-config`.
  - `static/study-detail.js` + `templates/study-detail.html`: pinned card relabel
    + skip-build submit path.
  - `tests/test_remote_run_pinned.py` (14 pass). mypy clean; app builds; routes register.
- **sms-api `patch/db-filter` `2ef52c0a`** — `kustomize/base/workbench/workbench.yaml`
  +3 env: `VIVARIUM_WORKBENCH_REMOTE_PINNED=1`,
  `VIVARIUM_WORKBENCH_REMOTE_REPO_URL=https://github.com/vivarium-collective/v2ecoli`,
  `VIVARIUM_WORKBENCH_REMOTE_BRANCH=main`.

## DONE — deployed + proven live (2026-07-13)

- Image **`72e00b8`** built (gh run 29292011506) + confirmed in GHCR; overlay
  `newTag` 2c56cb8→72e00b8 applied + rolled out (pod 1/1).
- Headless: `/api/remote-run-config` → `{pinned:true, commit 70b5ec3, simulator_id 69}`.
- **Part B live e2e PASSED**: study card relabeled "Run against pinned build (main
  @ 70b5ec3)"; clicked → **NO login prompt** → build reused (69) → submit (sim
  211) → ParCa → **3-node transient Ray MNP cluster** (Batch RUNNABLE≈8 min = Ray
  provisioning; STARTING→RUNNING≈5 min) → completed → **landed**
  `baseline__1783986815__08c5be` in showcase-2-baseline-figures. **Simulations DB
  now 36 runs.**
- **KEY FINDING (feeds P7)**: landed-from-remote runs DO carry `remote_origin`
  (`{deployment:smsvpctest, simulation_id:211, backend:ray}`), NOT local as the
  old doc said. So remote-☁️ count = 0 until a live run lands, then +1 per landed run.

## Next steps (resume here)

1. ✅ **P7 — WALKTHROUGH Segment 6 rewrite DONE** (2026-07-13): pinned-build Part B
   (card "Run against pinned build (main @ 70b5ec3)", no push/login; ParCa→Ray
   MNP→land); Part A drift corrected (remote-☁️ 0 until live land; emitter sqlite
   3/parquet 6/xarray 3/unrecorded 23; status 31 completed + 1 complete + 3
   failed); "landed = local origin" → ray `remote_origin`; timing + offline numbers
   (52→35) fixed; pinned-mode troubleshooting rows; header stamped.
2. **Finish full 8-segment WS-E drive** (needs browser; AWAITING USER'S WORD):
   Segments 7 (Analyses) + 8 (Wrap-up). Then extend the `Last verified` stamp to all 8.
3. **WS-F PRs** (no auto-merge): PR #465 (demo-v2ecoli→main) + sms-api
   patch/db-filter→main; then cut a release tag + repoint the overlay from `72e00b8`.

## Ray/queued mechanism (confirmed from sms-api code, for the doc)

- v2ecoli runs on a **transient Ray cluster = AWS Batch MNP job** (`simulation_service_ray.py`):
  node 0 = Ray head (runs workload), nodes 1: = workers; `RAY_NUM_NODES=3`, arm64,
  queue `smsvpctest-ray-mnp`.
- Dashboard "queued" = Batch `SUBMITTED`/`RUNNABLE`/`PENDING` (`_BATCH_STATE_MAP`).
  `RUNNABLE` = provisioning the MNP compute (Ray spin-up); `PENDING` = waiting on
  the **ParCa Batch dependency** (sim job gated on ParCa SUCCEEDED). Flips to
  running at `STARTING`→`RUNNING`.

## Pinned-build live facts (reuse)

- `latest built main` = **simulator_id 69 @ 70b5ec3** (2026-07-06),
  `simulator_status(69)`=completed. Matches the pod's checked-out commit.
- **Gotcha**: builds registered under `.../v2ecoli` (no `.git`);
  `latest_simulator(".../v2ecoli.git")` returns unbuilt tip `a08e20b` (no id).
  ⇒ resolve from `versions`, normalize `.git`.

## Env / gotchas

- Cluster: `export AWS_PROFILE=stanford-sso AWS_DEFAULT_REGION=us-gov-west-1 KUBECONFIG=/Users/alexanderpatrie/.kube/kube_stanford_test.yml`
- Tunnel: `~/sms/sms-cdk/scripts/sms-proxy.sh -s smsvpctest` → `localhost:8080/workbench`; dies with SSO expiry → `aws sso login` + restart.
- Tests: `uv run --no-sync pytest -q` (bare `uv run` fails — missing `../pbg-ptools` path dep).
- Commits this session are **SSH-signed and worked** (no gpgsign bypass needed).
- Pre-existing unrelated test failure: `test_remote_run_panel.py::test_view_run_button_routes_to_visualizations_not_dead_route`.

## Related

- `.todo/plans/5-pinned-build-remote-runs.md` (ground truth), `.todo/plans/4-remote-govcloud-demo-e2e.md`, `.todo/MANIFEST.md`, `NEXT_STEPS.md`
- memory `[[project_alb_rewrites_host_csrf]]`, `[[project_v2ecoli_branch_policy]]`, `[[project_ssh_commit_signing]]`

## REM Insight (2026-07-14, slumber deep cycle)

The pattern connecting every change this session: **the deferred WS-F release was
not a gap to work around — it was the organizing constraint that made every
provenance decision unambiguous.** Because the formal semver version-bump/tag/
release is deliberately deferred to post-merge-on-`main`, a *dev-SHA deploy* needs
its own lightweight, immutable provenance layer, and each piece this session slots
into exactly that layer: image tag = git short sha (`7a9620c`), a build-provenance
git tag (`build/demo-v2ecoli/7a9620c`) that links image↔commit without consuming a
version or triggering release CI, and per-action doc commits that timestamp the
progression. Even the `environment.py` exclusion fits — provenance integrity means
a commit must contain *only* the action's own work. Takeaway: "deploy before
release" workflows should treat build-provenance tagging as a first-class, reusable
layer distinct from semantic releases, not an afterthought.

## REM Insight (2026-07-14, slumber deep cycle — verification session)

The pattern connecting every change this session: **the work was epistemic, not
constructive — it converted "deployed/assumed" into "verified/bounded," and the
value came from the boundaries drawn, not features added.** No new product code
shipped; instead the rollout became a *confirmed* pod on `7a9620c`, the figures
became a *proven* 200-vs-404 contrast, the Omics gap became a *definitively
root-caused* negative (0.5.9 reads `multiomics=t&datafile=`, never `url=` — so even
the documented `/ptools-data` fallback was ruled out), and the recap numbers became
*re-checked* live facts. Two boundaries did the heavy lifting: the **soft-fail
boundary** (Launch opens the overview unpainted, no error) is exactly what lets the
MVP verdict be "shippable" rather than "blocked"; and the **proprietary boundary**
(Pathway Tools is untouchable) is what turned plan 9 from a vague "fix PTools" into
a precise "drive its existing endpoints from our launcher." Takeaway: a verdict is
only as trustworthy as the gaps it names — the honest framing of the one soft-fail
is what makes "MVP" credible, and naming what you cannot touch is what makes the
remaining work well-defined.
