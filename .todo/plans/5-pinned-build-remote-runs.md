# Plan 5 — Pinned-build remote runs (make Segment 6 Part B work e2e, production-grade)

### Name

Feat: "Run against pinned build" — submit remote sims against the latest **built**
v2ecoli `main` simulator (no per-run push/build/login), unblocking the GovCloud
demo's Segment 6 Part B.

Linked: unblocks WS-E of [[4-remote-govcloud-demo-e2e]] (Segment 6 Part B was the
acceptance gate). Spans `vivarium-dashboard@demo-v2ecoli` (code) + `sms-api`
overlay (env). No `v2ecoli` changes.

### Status: 🔄 EXECUTING (approved 2026-07-13; user said "proceed")

## Why (root cause, evidence-backed on the live pod)

The stock "Run on remote" card drives **build → poll → submit → poll → land**,
where **only Phase 1 (build)** pushes git + needs GitHub login. On the GovCloud
pod that phase is triply blocked:

- **A** `VIVARIUM_WORKBENCH_GH_CLIENT_ID` unset → `auth/github/start` = `no_client_id` → device-flow login disabled.
- **B** `/workspace/.git` owned by uid 17163 ≠ app uid 0 → git "dubious ownership" → `has_origin_remote`=false, `github-repo`=null.
- **C** workspace is on protected `main` of `vivarium-collective/v2ecoli` → `git push -u origin main` would be rejected + violates the no-push-to-main policy.

## Decision (user)

- Demo pins to the **latest tip of `main` at demo time** — resolved dynamically, never a hardcoded SHA.
- **Direction 1**: skip Phase 1 entirely; submit sims against the already-built pinned simulator.
- Login gate = **most production-grade + reproducible** → since Phase 2/3 do **no GitHub write**, requiring a human token is neither. Use a **config-gated pinned mode** (declarative env; trust boundary = network + in-cluster dashboard↔sms-api). Default build-first flow keeps its login gate.

Consequence: **Blockers B and C vanish** (no push, no local git). Blocker A is
replaced by the config gate.

## Live facts that shape the design

- sms-api `/core/v1/simulator/versions` already has **built** v2ecoli `main`
  simulators; latest = **`database_id 69`, commit `70b5ec3`** (2026-07-06),
  `simulator_status(69)` = **completed**. Matches the pod's checked-out commit.
- **Gotcha**: builds are registered under `github.com/vivarium-collective/v2ecoli`
  (**no `.git`**); `latest_simulator(".../v2ecoli.git")` returns an *unbuilt*
  git-tip (`a08e20b`, no `database_id`). ⇒ pinned-resolve must **normalize the
  `.git` suffix** and pick the newest matching entry from `versions`, NOT trust
  `latest_simulator`.
- ⇒ Phase 1 becomes a **single in-cluster GET** (resolve latest built main),
  no push/build/poll/login/git. Everything runs through in-cluster sms-api — no
  github.com egress from the dashboard pod.

## Workstreams

- **P1 — lib `remote_pinned.py` (new, additive)**: `pinned_config()` (reads
  `VIVARIUM_WORKBENCH_REMOTE_PINNED` / `_REMOTE_REPO_URL` / `_REMOTE_BRANCH`=main);
  `_normalize_repo`; `resolve_pinned_build(client, repo_url, branch)` → newest
  `versions` entry matching normalized repo+branch → `{simulator_id, commit, branch, repo_url}`.
- **P2 — `remote_run_views.py`**: add `remote_run_pinned_build_start(ws, body)`
  (returns `{simulator_id, phase:"built", commit, branch, pinned:true}`, 202);
  relax the `current_session()` gate in `remote_run_submit`/`remote_run_land` to
  allow when `pinned_config()` is enabled (default flow unchanged).
- **P3 — `api/app.py`**: `POST /api/remote-run-pinned-build`; `GET
  /api/remote-run-config` (→ `{pinned, repo_url, branch, commit, simulator_id}` so
  the client can relabel the card).
- **P4 — frontend `study-detail.js` + `templates/study-detail.html`**: on load
  fetch `/api/remote-run-config`; when pinned, relabel the card ("Run against
  pinned build — main @ <short-sha>"), drop the "Requires GitHub login" line, and
  route `_submitRemoteRun` → `/api/remote-run-pinned-build` → skip build-poll →
  `_submitRun`.
- **P5 — tests** `tests/test_remote_run_pinned.py`: resolve picks latest built
  main + normalizes `.git`; pinned-build-start returns phase built w/o push/login;
  submit works with no session when pinned enabled.
- **P6 — deploy** `sms-api` overlay `kustomize/base/workbench/workbench.yaml`: add
  `VIVARIUM_WORKBENCH_REMOTE_PINNED=1`,
  `VIVARIUM_WORKBENCH_REMOTE_REPO_URL=https://github.com/vivarium-collective/v2ecoli`,
  `VIVARIUM_WORKBENCH_REMOTE_BRANCH=main`. Build image → deploy → drive Part B live.
- **P7 — WALKTHROUGH**: rewrite Segment 6 (Part A drift: 0 remote-origin rows;
  emitter pills sqlite 3/parquet 6/xarray 3/unrecorded 23; status 31 completed +1
  complete +3 failed) + Part B = pinned-build run. Stamp `Last verified` after live pass.

## Progress

- **P1–P5 CODE-COMPLETE + tested locally** (`demo-v2ecoli`): new `lib/remote_pinned.py`;
  `remote_run_views.py` (pinned-build-start + `remote_run_config` + relaxed
  submit/land gate via `_run_auth_ok`); `api/app.py` two routes
  (`/api/remote-run-pinned-build`, `/api/remote-run-config`); `study-detail.js`
  (`_initRemoteRunPinned` + pinned branch in `_submitRemoteRun` + pinned-aware
  reset); `study-detail.html` init hook; `tests/test_remote_run_pinned.py`
  (14 pass). mypy clean on the two typed lib modules; app builds + both routes
  register. (Pre-existing unrelated failure: `test_view_run_button_routes_to_visualizations_not_dead_route`.)
- **P6 CONFIG-COMPLETE** (`sms-api@patch/db-filter`): `workbench.yaml` +3 env vars
  (`REMOTE_PINNED=1`, `REMOTE_REPO_URL=…/v2ecoli`, `REMOTE_BRANCH=main`).
- **P6 DEPLOYED + Part B PROVEN LIVE** (2026-07-13): image `72e00b8` built +
  deployed (overlay `newTag` 2c56cb8→72e00b8, rolled out). Headless verified
  `/api/remote-run-config` → `{pinned:true, commit 70b5ec3, simulator_id 69}`.
  **Full e2e drive succeeded**: study card relabeled "Run against pinned build
  (main @ 70b5ec3)"; clicked → **NO login prompt** → resolve build 69 → submit
  (sim 211) → ParCa → **3-node transient Ray MNP cluster** (queued≈8 min = Ray
  provisioning; running≈5 min) → completed → **landed** as
  `baseline__1783986815__08c5be` in showcase-2-baseline-figures. Simulations DB
  now 36 runs.
- **KEY FINDING**: landed-from-remote runs DO carry `remote_origin`
  (`{deployment:smsvpctest, simulation_id:211, backend:ray, experiment_id:…}`),
  NOT local origin as the old doc claimed. `s3_uri` came back None (minor land-payload gap).
- **P7 DONE** (2026-07-13): `demos/v2ecoli/WALKTHROUGH.md` Segment 6 rewritten as
  the pinned-build flow (Part A drift numbers corrected; Part B = pinned build →
  ParCa → Ray MNP → land; "landed = local origin" corrected to ray `remote_origin`);
  header `Last verified: 2026-07-13 (remote — Seg 1–6 incl. live Part B)`; timing
  table + offline numbers (52→35) fixed; troubleshooting rows added for pinned mode.
- **REMAINING**: finish the full 8-segment WS-E drive (Segments 7–8, needs
  browser) and WS-F PRs (no auto-merge). Awaiting user's word to proceed.
