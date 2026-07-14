# Checkpoint: Segment 6 Part B — pinned-build remote runs DEPLOYED + PROVEN LIVE; P7 doc + segments 7–8 + PRs remain

**Updated:** 2026-07-13 (execution session). The full-e2e demo blocker (Segment 6
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
