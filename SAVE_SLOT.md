# Checkpoint: vivarium-workbench `/workbench` subpath deployment — code FIXED, image BUILT+DEPLOYED, curl-VERIFIED; awaiting human browser confirmation

**Updated:** 2026-07-13 (same day, later session) — Picked up from the prior checkpoint
(ALB routing fixed + study-detail code fix PR'd). This session closed the loop:
built an off-cycle image containing the fix, deployed it to the live
`sms-api-stanford-test` k8s namespace, and curl-verified the fix is live
server-side. Only the human-eyes browser click-through remains.

## Session Goal

Get the vivarium-workbench dashboard fully functional when served from the
`sms-api-stanford-test` k8s namespace behind the internal ALB at the `/workbench`
subpath (`sms-proxy.sh -s smsvpctest` → `http://localhost:8080/workbench`), per the
bug list in `demos/v2ecoli/NOTES.md:228-234` ("Post e2e remote walkthrough").
Time-pressured: user flagged an "intense deadline."

## Progress Table

| Issue | Status | Detail |
|---|---|---|
| ALB target group pointed at wrong-VPC target group | ✅ Done | Fixed in prior session — see previous checkpoint / `todo.md` for full root cause. E2E-verified via curl through the live tunnel. |
| Study-detail page (`/studies/<slug>`) unstyled + non-interactive under `/workbench` — **code fix** | ✅ Done | PR #465 (`fix/study-detail-base-path` → `main`), merged into `demo-v2ecoli` (commit `861aefa`). |
| Off-cycle image build (`e74b644`, includes `861aefa`) | ✅ Done | `gh workflow run build-and-push.yml --ref demo-v2ecoli` → [run 29262663329](https://github.com/vivarium-collective/vivarium-workbench/actions/runs/29262663329), success. Pushed `ghcr.io/vivarium-collective/vivarium-workbench:e74b644`. |
| k8s deployment update (`sms-api-stanford-test` namespace) | ✅ Done | `sms-api`'s `kustomize/overlays/sms-api-stanford-test/kustomization.yaml` `newTag: 0.1.1 → e74b644`; `kubectl apply -k .` + `kubectl rollout status` succeeded. Pod `workbench-6875799978-swddf` is `1/1 Running` on `e74b644`. **Change is uncommitted in the `sms-api` repo** — see Next Steps. |
| curl-based backend verification | ✅ Done | Through the live `smsvpctest` tunnel: `GET /workbench/studies/showcase-1-parca` → `200`, all asset refs correctly `/workbench/`-prefixed, `__DASH_CONFIG__.basePath == "/workbench"`. `style.css`, `study-detail.js`, `/api/simulations` all `200`. |
| PR #465 description/comment kept in sync | ✅ Done | Body rewritten to include root cause + fix + test plan + full deploy/verification trail; progress comment posted with the build run link. |
| **Human browser click-through** (Investigations → study → sub-tabs clickable, CSS renders, Simulations DB table renders) | ❌ **PENDING — this is the only remaining unknown** | Blocked on nothing technical — just needs eyes. Tunnel is **already running** in the background (PID 57346/57438, started 11:48/11:49 AM) at `http://localhost:8080/workbench`. Curl can confirm asset delivery but not client-side JS tab-collapse behavior — that was the original bug in the user's screenshot. |

## Key Files Touched

### `vivarium-workbench` repo (this repo) — code fix, from prior session, unchanged this session
- `vivarium_workbench/lib/report.py` — relocated `_normalize_asset_urls` here.
- `vivarium_workbench/lib/study_page.py` — `base_path` kwarg threaded through `render_study_detail_html` / `build_study_detail_page`.
- `vivarium_workbench/api/app.py` — `study_detail_page()` reads `request.scope["root_path"]`.
- `vivarium_workbench/publish.py` — imports `_normalize_asset_urls` from `lib.report`.
- `vivarium_workbench/templates/study-detail.html` — `{{ base_path }}`-prefixed the `/api/study-analysis-zip` anchor.
- `vivarium_workbench/static/walkthrough.js` — seeded-study link now uses `_studyHref()`.
- `tests/test_study_page_lib.py` — 5 new tests + fixed a monkeypatch stub.

### `~/sms/sms-api` (sibling repo, **outside** this repo — separate git history)
- `kustomize/overlays/sms-api-stanford-test/kustomization.yaml` — `vivarium-workbench` image `newTag: 0.1.1 → e74b644`, comment updated to explain the off-cycle git-sha pin. **Uncommitted** (working tree edit only, applied live via `kubectl apply -k .`; branch is `patch/db-filter`, clean before this edit). Sibling-repo commits are the user's to make — I stage/show, never commit/push there myself (same convention as this repo).

### This repo, still uncommitted
- `demos/v2ecoli/investigation-issue.png` — untracked screenshot from the original bug report. Not yet triaged (keep as demo artifact vs. delete).

## Key Design Decisions

1. **Off-cycle git-sha image tag, not a semver release.** The `sms-api` kustomization comment says images are "normally published by cutting a GitHub Release" (semver tag). Given the deadline, dispatched `build-and-push.yml` directly against `demo-v2ecoli` via `workflow_dispatch` (no `version` input → defaults to git short-sha), producing `e74b644` instead of waiting for a `0.1.2` release. This is explicitly commented in the kustomization.yaml diff so it's not mistaken for the normal release flow later.
2. **Dispatched against `demo-v2ecoli`, not `main`.** PR #465 isn't merged to `main` yet (open, `REVIEW_REQUIRED`). The build workflow builds whatever ref it's given — dispatching against `main` would have built an image *without* the fix. `demo-v2ecoli` already has `861aefa` fast-forward-merged in, so that's the ref that matters for this deploy.
3. **Deploy happened ahead of PR review**, matching the established `demo-v2ecoli` fix-branch pattern (memory: `project_demo_v2ecoli_fix_branch_strategy`) — PR review and live deployment are decoupled so the demo timeline isn't gated on reviewer latency. PR #465 itself is untouched (not merged, not force-pushed) — only its description/comments were updated to reflect the deploy trail.
4. **kubectl apply read from an uncommitted local edit.** `kustomize`/`kubectl apply -k` builds from whatever is on disk, so the rollout didn't need to wait on a `sms-api` commit+push+PR cycle. The commit itself is still owed (see Next Steps) — the *code* is committed (this repo, PR #465), only the *pin* in the deploy config repo is pending, and that pin isn't scientifically meaningful without an accompanying commit message explaining the off-cycle tag.
5. **curl verification is a distinct claim from browser verification.** curl confirmed the server serves correct, base-path-prefixed HTML/assets (200s, correct hrefs, correct `__DASH_CONFIG__`) — but the original bug was about *client-side* tab-collapse JS behavior once the page loads, which curl cannot observe. Reported both distinctly rather than conflating "backend correct" with "bug fixed" — per this repo's UI-only demo convention, the actual proof is a human in the browser.

## Verification

- **pytest** (re-run this session to confirm no drift):
  ```
  uv run --no-sync pytest tests/test_study_page_lib.py tests/test_study_detail_page.py \
    tests/test_study_detail_template.py tests/test_publish.py -q
  ```
  → **13 failed, 61 passed, 3 skipped** — identical failure set to the prior session (pre-existing template/test drift, unrelated to base-path work; not investigated further).
- **Image build**: `gh run view 29262663329` → `status: completed`, `conclusion: success`.
- **k8s rollout**: `kubectl rollout status deployment/workbench -n sms-api-stanford-test` → `"deployment \"workbench\" successfully rolled out"`. Pod image confirmed via `kubectl get deploy workbench -o jsonpath='{.spec.template.spec.containers[?(@.name=="workbench")].image}'` → `ghcr.io/vivarium-collective/vivarium-workbench:e74b644`.
- **curl through live tunnel** (`http://localhost:8080/workbench`):
  - `GET /studies/showcase-1-parca` → `200`; asset hrefs = `/workbench/assets/style.css`, `/workbench/assets/data-source.js`, `/workbench/assets/configure-run.js`, `/workbench/assets/study-detail.js`; `__DASH_CONFIG__ = { mode: "local-server", basePath: "/workbench" }`.
  - `GET /workbench/assets/style.css` → `200`; `GET /workbench/assets/study-detail.js` → `200`; `GET /workbench/api/simulations` → `200`.
- **NOT YET DONE**: human browser click-through (the actual bug repro/fix confirmation).

## Next Steps

1. **User does the browser check** (only remaining unknown): tunnel is live at
   `http://localhost:8080/workbench`. Investigations → click "statistical" (or
   any) study → confirm a single, styled, collapsed pillar nav with only the
   active pillar's sub-tabs visible and clickable. Also glance at CSS rendering
   generally and the Simulations DB table.
2. **Commit the `sms-api` kustomization.yaml pin** (sibling repo, currently
   uncommitted, branch `patch/db-filter`). Per this session's convention, I
   stage but don't commit/push myself there. Suggested one-liner (run from
   `~/sms/sms-api`, ideally on its own branch rather than `patch/db-filter`
   which has unrelated in-flight work):
   ```
   git checkout -b chore/pin-workbench-e74b644-stanford-test main
   git add kustomize/overlays/sms-api-stanford-test/kustomization.yaml
   git commit -m "chore(stanford-test): pin vivarium-workbench 0.1.1 -> e74b644 (off-cycle, vivarium-workbench#465)"
   ```
   (Note: the edit currently sits on `patch/db-filter`'s working tree, not `main`
   — cherry-pick or re-apply the single-line diff onto a clean branch off `main`
   before committing, to avoid bundling it with `patch/db-filter`'s unrelated
   history.)
3. **Get PR #465 reviewed and merged to `main`** — non-blocking for the demo
   (already live via the off-cycle image), hygiene only:
   https://github.com/vivarium-collective/vivarium-workbench/pull/465
4. **Optional / lower priority**: the same 13 pre-existing test failures
   (`test_study_detail_page.py`, `test_study_detail_template.py`,
   `test_publish.py` — tab-scaffold/panel-id/skeptic-toggle assertions, one
   snapshot-popout assertion) — unrelated template/test drift, still not
   investigated. Flag as a separate cleanup candidate if wanted.
5. **Triage `demos/v2ecoli/investigation-issue.png`** (untracked) — the
   original bug screenshot. Commit as demo documentation or delete once the
   bug is confirmed fixed.

## Quick Reference

```bash
# kubectl (sms-api-stanford-test namespace)
export KUBECONFIG="/Users/alexanderpatrie/.kube/kube_stanford_test.yml"

# Tunnel to the live cluster (ALREADY RUNNING this session — check before starting another)
ps aux | grep sms-proxy
AWS_PROFILE=stanford-sso AWS_DEFAULT_REGION=us-gov-west-1 \
  ~/sms/sms-cdk/scripts/sms-proxy.sh -s smsvpctest
# → http://localhost:8080/workbench

# Re-check rollout / image
kubectl get pods -n sms-api-stanford-test
kubectl get deploy workbench -n sms-api-stanford-test \
  -o jsonpath='{.spec.template.spec.containers[?(@.name=="workbench")].image}'

# Dispatch another off-cycle image build if a new fix lands on demo-v2ecoli
gh workflow run build-and-push.yml --ref demo-v2ecoli

# Local subpath test (no k8s needed) — reproduces exactly what was broken
cd ~/vivarium-app/vivarium-dashboard
VIVARIUM_WORKBENCH_WORKSPACE="$(pwd)/tests/_fixtures/ws_increase_demo" \
VIVARIUM_WORKBENCH_DISABLE_CSRF=1 \
  uv run --no-sync uvicorn vivarium_workbench.api.app:app --root-path /workbench --port 8799

# Test suite for the study-detail fix
uv run --no-sync pytest tests/test_study_page_lib.py tests/test_study_detail_page.py \
  tests/test_study_detail_template.py tests/test_publish.py -q

# PR
gh pr view 465 --web
```

## Related Files

- **PR**: https://github.com/vivarium-collective/vivarium-workbench/pull/465
- **Build run**: https://github.com/vivarium-collective/vivarium-workbench/actions/runs/29262663329
- **Plan**: `.todo/plans/1-fix-study-detail-interactivity.md` (full root-cause writeup + progress checklist), indexed by `.todo/MANIFEST.md` — needs its `## Progress` checklist and `.todo/MANIFEST.md` status line updated to reflect the deploy (still say "PR OPEN ... Remaining: k8s image rebuild/redeploy" as of last edit — now done, only PR merge + browser check remain)
- **Original bug report**: `demos/v2ecoli/NOTES.md:228-234` ("Post e2e remote walkthrough")
- **Screenshot**: `demos/v2ecoli/investigation-issue.png` (untracked)
