1. **(.todo/plans/1-fix-study-detail-interactivity.md)**:

### Name

Fix: Study-detail page unstyled/non-interactive under /workbench subpath

### Status: ✅ CONFIRMED FIXED (browser-verified)

PR OPEN (REVIEW_REQUIRED) — https://github.com/vivarium-collective/vivarium-workbench/pull/465; merged into demo-v2ecoli (861aefa). k8s image rebuilt+deployed to sms-api-stanford-test (e74b644). The 2026-07-13 browser walkthrough confirmed the study-detail page renders + is interactive under `/workbench`. Only remaining item is PR merge to main (hygiene, non-blocking).

---

## 2. **(.todo/plans/2-fix-csrf-origin-guard-reverse-proxy.md)**:

### Name

Fix: CSRF/origin guard 403s all POST/DELETE behind ALB reverse-proxy subpath deployment

Linked tasks: independent of #1 and #3 (different subsystem, no shared files, no ordering dependency); shares the broader demo-v2ecoli e2e-walkthrough context with both.

### Status: ✅ ROOT CAUSE CONFIRMED + FIX CODED/PUSHED (deploy pending in WS-D)

**2026-07-13 update:** Live probe CONFIRMED the ALB rewrites `Host` (403 even with a matching client `Host: localhost:8080`) and omits `X-Forwarded-Host`, so `--trust-proxy` is a dead end. Production-grade allowed-origins allowlist implemented + 33 tests pass, pushed as part of demo-v2ecoli `2c56cb8`; sms-api env pushed as `15c3391`. Folded into umbrella #4 (WS-B). See [[project_alb_rewrites_host_csrf]]. Prior status below (historical):

### (historical) ❌ DEPLOYED but STILL BROKEN — root cause of why the fix isn't taking effect NOT yet found

Fix landed in `481b3f2` (`demo-v2ecoli`, pushed; part of PR #465), deployed to sms-api-stanford-test with `--trust-proxy` in the pod args. Opt-in `trust_forwarded`/`forwarded_host` on `is_request_allowed()` + `--trust-proxy`/`VIVARIUM_WORKBENCH_TRUST_PROXY=1`; targeted suites pass. **But the 2026-07-13 browser walkthrough still hit the 403** (`POST /workbench/api/study-run-baseline → 403` reproduced server-side in pod logs). The fix is correct in isolation but isn't taking effect through the ALB→SSM-tunnel→k8s chain — likely `X-Forwarded-Host` never arrives, or the env check reads the wrong var, or uvicorn strips the header. Remaining: diagnose live headers (`curl -v` through tunnel or debug-log redeploy), then fix. See plan #2 "Post-deploy diagnosis" + `SAVE_SLOT.md`.

---

## 3. **(.todo/plans/3-fix-composite-resolve-unhandled-errors.md)**:

### Name

Fix: composite-resolve swallows real exceptions; colony (pymunk) composite 500s unobservably

Linked tasks: independent of #1 and #2 (different subsystem, no shared files, no ordering dependency); shares the broader demo-v2ecoli e2e-walkthrough context with both.

### Status: ❌ DEPLOYED, still 500s — but Tier 1 logging surfaced the real traceback; Tier 2a now unblocked

Tier 1 + Tier 2 landed in `481b3f2` (`demo-v2ecoli`, pushed; part of PR #465), deployed. Catch-all handler now `logger.exception(...)`s; both unguarded seams degrade via shared `_degraded_result()`. Targeted suites pass. **Tier 1 worked**: deployed logs captured `ModuleNotFoundError: No module named 'bigraph_loom'` on the loom-asset route (not the guarded resolve seams) — the leading candidate for the real Bug 3, since the always-visible loom panel fires a loom-asset request for any composite. This IS the Tier 2a dependency fix that was gated on this evidence: the image builds from v2ecoli's lock (`Dockerfile:43-45`) which likely omits `bigraph-loom`, and the build sanity check (`Dockerfile:70`) doesn't import it. Remaining: confirm v2ecoli's lock omits it (local grep) + correlate a live colony click, then add a Dockerfile overlay install + sanity import. See plan #3 "Tier 2a — now unblocked". **Now absorbed into umbrella item #4** (v2ecoli lock confirmed to omit `bigraph-loom`).

---

## 4. **(.todo/plans/4-remote-govcloud-demo-e2e.md)**:

### Name

Close all gaps for a reproducible remote GovCloud dashboard demo (fix bugs #2 + #3; rewrite WALKTHROUGH remote-first; verify e2e; merge + release)

Linked tasks: **umbrella item that absorbs the remaining open work in #2 and #3** (the two blocking bugs) and depends on #1 (✅). Spans `demo-v2ecoli` (./) + `patch/db-filter` (`~/sms/sms-api`); `v2ecoli` main unchanged.

### Status: 🔄 EXECUTING — WS-A/B/C landed+pushed; build running; WS-D deploy + WS-E/F remaining

**2026-07-13 execution update:** WS-A/B/C/D all DONE + live-verified. Image `2c56cb8` built, deployed to `sms-api-stanford-test`, rolled out. Bug 2 (CSRF probe→405, was 403) + Bug 3 (`/workbench/bigraph-loom/`→200; `parca`+`colony` resolves→200) verified on the live pod. Pushed: dashboard `2c56cb8`, sms-api `15c3391`+`10159223`. Numbers reconciled to live (simulations 52→35; orphaned removed); all named elements present; Part B backbone confirmed (pod→sms-api /docs→200). REMAINING: WS-E full 8-segment browser drive (incl. live Run-remotely) + WS-F PRs. See `SAVE_SLOT.md` (ground truth).

### (historical) Status: PLANNED — approved plan, no code written yet

The demo is redefined to run against the REMOTE `/workbench` k8s deployment via `sms-proxy.sh -s smsvpctest` → `localhost:8080/workbench`. Bug 3 root cause CONFIRMED (v2ecoli `uv.lock` has zero `bigraph-loom`; combined image builds from that lock → `bigraph_loom` never installed → `ModuleNotFoundError` on the always-visible loom panel). Bug 2 NARROWED (AWS ALB omits `X-Forwarded-Host`, so `--trust-proxy` is a no-op; one live header capture pending) → production-grade allowed-origins allowlist chosen. Six workstreams: WS-A Bug 3 Dockerfile install + broadened sanity import; WS-B Bug 2 diagnose→allowlist (code on `demo-v2ecoli`, env on `patch/db-filter`); WS-C rewrite `WALKTHROUGH.md` remote-first (local flow → Appendix G); WS-D iterative build(gh-action)→deploy→verify on the two feature branches ONLY; WS-E full e2e walkthrough as the acceptance gate; WS-F merge + release, gated on WS-E reproducibility. Full plan: `.todo/plans/4-remote-govcloud-demo-e2e.md` (mirror at `~/.claude/plans/giggly-hatching-globe.md`).

---

## 5. **(.todo/plans/5-pinned-build-remote-runs.md)**:

### Name

Feat: pinned-build remote runs — submit sims against the latest **built** v2ecoli `main` simulator (no per-run push/build/login), unblocking the GovCloud demo's Segment 6 Part B.

Linked tasks: unblocks WS-E of #4 (Segment 6 Part B was the acceptance gate). Spans `vivarium-dashboard@demo-v2ecoli` (code) + `sms-api` overlay (env). No `v2ecoli` changes.

### Status: 🔄 EXECUTING — feature DEPLOYED + Part B PROVEN LIVE + P7 doc DONE; Segments 7–8 drive + WS-F PRs remain

**2026-07-13:** Part B was root-caused to 3 pod deployment gaps (A: no GH client_id → login disabled; B: `/workspace/.git` uid 17163 ≠ app uid 0 → dubious ownership; C: protected-main push). Fixed via the **pinned-build** model (Direction 1): resolve the latest built simulator for the configured repo@branch from in-cluster sms-api and skip Phase 1 (push/build/login) entirely; login gate relaxed only under declarative pinned config. Shipped dashboard `demo-v2ecoli 72e00b84` (img `72e00b8`, deployed) + sms-api `patch/db-filter 2ef52c0a`. **Proven live e2e**: sim 211 → ParCa → 3-node transient Ray MNP cluster → completed → landed (Simulations DB now 36). P7 WALKTHROUGH Segment 6 rewrite DONE. Segment 7 now coded/committed (see #6). Ground truth `SAVE_SLOT.md`.

---

## 6. **(.todo/plans/6-segment7-ptools-omics-deploy-verify.md)**:

### Name

Feat/verify: land Segment 7 (PTools Omics Viewer + interactive figures) across the coupled pair, live-verify Segments 7–8, record the demo, then open the post-completion release PRs.

Linked tasks: continues #5. The two coupled branches — dashboard `demo-v2ecoli` ↔ sms-api `patch/db-filter` — jointly deliver the whole demo (memory `[[project_demo_branch_coupling]]`); post-completion = PR merge + version-bump release into each `main`. No `v2ecoli` changes.

### Status: 🔄 EXECUTING — Seg 7 (figures PASS, Omics deferred→plan 9) + Seg 8 (recap figures verified live) DONE; NEXT = plan 9 → record

**2026-07-14:** Segment 7 deployed (pod 1/1 on `7a9620c`, seed env stamped) and live-verified headlessly. **Interactive figures PASS** (5/5 → 200 under `/workbench/reports/...`; root → 404). **TSV HTTP delivery PASS** (dashboard serves omics TSV 200/~355 KB at the PTools-fetched path). **Omics Viewer auto-load FAIL on `sms-ptools:0.5.9`** — root-caused: 0.5.9 auto-loads via `multiomics=t&datafile=<registered-key>` (fetches `/get-registered-multiomics-data`), NOT the launcher's `omics=t&url=<tsv>` (0.8.2 scheme); the `/ptools-data` fallback also fails since both feed the ignored `url=`. **DECISION (2026-07-14):** keep Omics Launch in the demo, DEFER the fix to plan 9 — order is **Segment 8 (WS-3) → plan 9 → record (WS-4)**. REMAINING: Segment 8 → plan 9 → stamp all 8 → record → WS-F release PRs. Ground truth `SAVE_SLOT.md` + memory `[[project_ptools_segment7_routing]]`.

---

## 9. **(.todo/plans/9-omics-viewer-0.5.9-register-launch.md)**:

### Name

Feat: make the PTools Omics Viewer Launch paint on the deployed `sms-ptools:0.5.9` via a **frictionless semi-manual upload**, closing the ❌ half of #6 WS-2.

Linked tasks: closes #6 WS-2b (interactive-figures half already PASSES live). Adjacent to #8 (which consumes this launch mechanism). Spans THREE repos: `pbg-ptools` (`workbench_viewers` — new third coupled repo) + dashboard frontend + likely-no-change sms-api. No v2ecoli changes.

### Status: 📋 PLANNED + REFINED (via /plan, approved) — DEFERRED slot: AFTER Segment 8 ✅, BEFORE recording; awaits "proceed"

**⛔ CONSTRAINT: Pathway Tools in `sms-ptools` is PROPRIETARY — never edit/patch it.** All new code is OURS; we only *use* PTools' existing Omics upload dialog. **Refinement (2026-07-14):** live investigation INVALIDATED the original "register-then-launch" idea — 0.5.9 has NO register-and-return-key endpoint (only `/overview-multi-omics-process`, which paints an already-open overview from a direct upload, + `/save-omics-prefs`); the `datafile=<key>` path only reads pre-registered data nothing in the client can create. **Chosen approach = frictionless semi-manual:** Launch opens the clean overview AND the dashboard hands the presenter the study TSV (one-click download + "upload this in the Omics dialog" prompt); one upload click paints it via PTools' own UI. Implementation: `ui.ptools_scheme` switch (default `manual` for 0.5.9, `url` opt-in for 0.8.x) in `pbg_ptools.workbench_viewers` + a `_launchViewer` helper panel in `static/walkthrough.js` reusing the `available`/`tsv_url` the launcher already returns. Needs tunnel (WS-1/WS-4) + a local `pbg-ptools` clone. See plan for WS-1…WS-4 + `~/.claude/plans/validated-roaming-catmull.md` + memory `[[project_ptools_segment7_routing]]`.

---

## 7. **(.todo/plans/7-pinned-run-progress-ux.md)**:

### Name

Feat: sleek, production-grade progress feedback (progress bar + spinner) for long-running UI-triggered processes, first case = the "Run against pinned build" card in the Simulations tab.

Linked tasks: builds on #5 (the pinned-build run whose submit fans out to ParCa → Ray MNP → land is the process with thin progress signal today). Dashboard-only, on `feat/improved-visual-feedback` (cut from `main`, which now carries the merged+released demo work, `0.2.0`). Source: `.todo/_backlog.md` item (b).

### Status: ✅ DONE + RELEASED (v0.3.0, 2026-07-15) — PR #467 merged (`1c51df2`), tag + GitHub Release `v0.3.0` published

**Re-target 2026-07-14** (`~/.claude/plans/purrfect-wandering-narwhal.md`, approved): `demo-v2ecoli` merged into `main` + released (`0.2.0`); the `vivarium_workbench` rename moved the frontend under the package. Branch `demo-v2ecoli` → **`feat/improved-visual-feedback`**; all frontend paths → `vivarium_workbench/static/…` + `vivarium_workbench/templates/…` (tests stay repo-root). sms-api confirmed on `main` (PR #163) — **no sms-api change iter 1**; future substage work → new branch off sms-api `main`. Design unchanged; all code anchors re-verified present (line #s hold). Feasibility verified by a backend + frontend sweep: a true continuous 0–100% bar is NOT backed by data (the poller `GET /api/remote-run-poll` forwards only a categorical `phase`/`raw_status` — no fraction, no SSE for remote runs). Two honest signals ARE available → **HYBRID model** (user-chosen): a determinate segmented **milestone bar** (Resolve → Submit → Queued → Running → Done → Landed) + an honest **time-based soft-fill** within the two long waits (Queued ≈ 8 min, Running ≈ 5 min; capped <100%, snaps on the real transition) + a **spinner** on the active stage. Reuse scope = **pinned card only** this iteration, but a **dual-shape component API** (`stages` + `measured`) with a documented adoption note for the genuinely-determinate local composite-run path (`progress_step`/`n_steps` via `/api/composite-run/{id}/status`). Wraps the existing `_renderRemoteRunProgress` as an adapter (unchanged call sites). New: `vivarium_workbench/static/progress-track.{js,css}` + `tests/js/test_progress_track.js`. See plan for WS-1…WS-4.

---

## 8. **(.todo/plans/8-autoparam-ptools-from-exports-tsv.md)**:

### Name

Feat: auto-parameterize the embedded Pathway Tools Omics Viewer from a study's Exports `.tsv` on the remote smsvpctest deployment.

Linked tasks: generalizes #6 (Segment 7 Omics wiring) from a single seeded study to any study whose Exports carry a compatible `.tsv`. **Gated on #6 WS-2** (HTTP `url=` vs filesystem `/ptools-data` delivery must inherit whichever mechanism #6 proves live). Spans `demo-v2ecoli` + possibly the sms-api overlay. Source: `.todo/_backlog.md` Prompt Queue.

### Status: 📋 PLANNED — promoted from backlog Prompt Queue (2026-07-14); awaits "proceed" + #6 outcome

Detect PTools-compatible Exports `.tsv` → build the `celOv.shtml?…&url=` (or filesystem) target → surface as an auto-parameterized Launch, no-op when absent. See plan for WS-1…WS-4.

---

## 11. **(.todo/plans/11-demo-recording-prep-local-remote-compute.md)**:

### Name

Docs/tooling: log and land the demo-prep artifacts authored ahead of the v2ecoli GovCloud demo recording — a new "local dashboard, remote compute" companion walkthrough, a generalized register→build→run script, a full word-for-word narration script, and a supporting image asset.

Linked tasks: prep work for the recording session in `NEXT_STEPS.md`; companion to `demos/v2ecoli/WALKTHROUGH.md` and Plans 5/6/7. Dashboard-repo-only, no v2ecoli changes.

### Status: ✅ Artifacts authored (were untracked, no owning plan) — WS-1/WS-2 (log + commit) executing now; WS-3 (actual recording) deferred to next session per `NEXT_STEPS.md`.

Discovered via `/orientation` (2026-07-17): 5 untracked files
(`demos/v2ecoli/WALKTHROUGH-local-remote-compute.md`,
`demos/v2ecoli/scripts/remote_commit_run.py`,
`demos/v2ecoli/speaker/NARRATION.md`,
`demos/v2ecoli/speaker/three_layers.png`) — read in full, confirmed
coherent finished prep work, not orphaned WIP. See plan for full inventory
and WS-1…WS-3.

---

## 10. **(.todo/plans/10-release-improved-visual-feedback-smscdk.md)**:

### Name

Ops: close remaining doc/PR gaps on `feat/improved-visual-feedback` (PR #467), container-verify it against `sms-api-stanford` (smscdk) without a durable change to the shared namespace, merge, then cut a real release and deploy it there for good.

Linked tasks: closes out #7 (Plan 7's code is done; this is the "deploy-verified → merged → released" tail). Spans THREE repos: `vivarium-workbench` (gap fixes, tag, release), `sms-api` (kustomize overlay pin only, no code changes), and the `sms-api-stanford` k8s namespace itself. No v2ecoli changes.

### Status: ✅ WS-1…WS-8(step 1) DONE — PR #467 MERGED (`1c51df2`), `v0.3.0` tagged + GitHub Release published, image built. 🔄 WS-8 steps 2–3 IN FLIGHT in `sms-api` — overlay pin `0.2.0`→`0.3.0` cut, **PR #176 still OPEN** (not merged).

Plan approved via `/plan` (`~/.claude/plans/quirky-snuggling-crystal.md`, 2026-07-15). Two judgment calls resolved with the user: WS-4's smscdk verification deploy is **smoke-and-revert** (no durable sms-api commit), and WS-8's durable sms-api overlay pin goes through a **small PR** (not a direct commit, despite sms-api's own precedent for the latter). See plan for WS-1…WS-8.

**Update (2026-07-20, verified from both repos):** the workbench side is fully done — PR #467 merged (`1c51df2`), `pyproject.toml` on `main` reads `0.3.0`, tag `v0.3.0` + GitHub Release published, `:0.3.0` image built. **WS-8 steps 2–3 (durable sms-api overlay pin) are NOT yet merged:** in `~/sms/sms-api`, branch `deploy/workbench-0.3.0` (2 ahead of `origin/main`) carries the overlay bump `vivarium-workbench 0.2.0→0.3.0` on both `sms-api-stanford` + `sms-api-stanford-test` (commit `e9862a10`) and integration release `0.9.22` (`2de193fe`), surfaced as **PR #176 (OPEN)**. Stanford-prod parity landed separately via merged PR #175. **Remaining tail: merge sms-api PR #176 + final smscdk cutover.** Not demo-blocking — the live smscdk stack already verified running `0.3.0` this cycle.

---

## 12. **(.todo/plans/12-durable-remote-run-persistence.md)**:

### Name

Fix/Feat: remotely dispatched sms-api runs never appear in the Simulations DB tab and do not survive a session — give them the same durable-record-first `runs_meta` lifecycle that local composite runs already have, plus a remote reconciler that auto-lands completed sims on boot.

Linked tasks: surfaced while executing the fully-remote variant of the demo protocol in `demos/v2ecoli/WALKTHROUGH-local-remote-compute.md` (#11's artifact). Builds on the pinned-build remote-run model from #5 and the remote-run progress UX from #7 — both drive the same `remote_run_views` submit→poll→land flow this plan makes durable. Dashboard-repo only; **no sms-api and no v2ecoli changes** (sms-api already exposes `simulation_status` + `download_data`).

### Status: 📋 PLANNED (via `/plan`, 2026-07-20) — awaits explicit "proceed"

Root cause identified and confirmed by direct inspection: the local run path is **durable-record-first** (a `runs_meta` row with `status='running'` is written *before* work starts, a pid is recorded, and `reconcile_stale_runs` repairs crash-orphans on boot at `lib/startup.py:84`), but the remote path **inverts** that contract — its row is created only after a *manual* "Land results" click and is written already-terminal (`lib/remote_run_landing.py:81-90`). Between dispatch and landing there is **no server-side record of any kind**: `remote_run_submit` (`lib/remote_run_views.py:213`) writes nothing, and the `simulator_id`/`simulation_id` live solely in the browser's `_remoteRunState` (`static/study-detail.js:1702`) with no `localStorage`. So an in-flight remote run is invisible to the index's five on-disk sources *and* unrecoverable after a reload.

Three design forks resolved with the user: (1) **record home** — the user rejected the either/or framing ("any simulation run associated with a study IS in fact a composite run; thus it should be in sync"), resolved as one row / one home (`studies/<slug>/runs.db`) on the *identical* `runs_meta` contract, with sync satisfied structurally by `_discover_dbs` (which already unions the central + per-study DBs) rather than by duplicating rows; (2) **auto-land = yes** — reconcile flips status *and* downloads/lands, so a prior-session run materializes with zero clicks; (3) **scope** = persistence + the three index bugs that also hide already-completed work (7 unenumerated `.pbg/parquet-runs` hives; `study.yaml` runs keyed on `simulation_id` silently dropped; `_append_remote_simulations` gated on `.viv-build.json`). Lane A (`RemoteRunManager`) deletion and Lane C are explicitly out of scope. Five workstreams WS-1…WS-5. Full plan: `.todo/plans/12-durable-remote-run-persistence.md` (mirror at `~/.claude/plans/ancient-nibbling-kahan.md`).

---


