# Checkpoint: vivarium-workbench `/workbench` subpath deployment — ALB routing FIXED, study-detail page FIXED (PR open)

**Updated:** 2026-07-13 — Two issues chased down this session, both resolved at the
code level. First (ALB target-group misroute) is live and verified. Second
(study-detail page unstyled/non-interactive) has a PR open and is merged into
`demo-v2ecoli`, but **not yet on the live k8s deployment** (image rebuild pending).

## Session Goal

Get the vivarium-workbench dashboard fully functional when served from the
`sms-api-stanford-test` k8s namespace behind the internal ALB at the `/workbench`
subpath (`sms-proxy.sh -s smsvpctest` → `http://localhost:8080/workbench`), per the
bug list in `demos/v2ecoli/NOTES.md:228-234` ("Post e2e remote walkthrough").

## Progress Table

| Issue | Status | Detail |
|---|---|---|
| ALB target group pointed at wrong-VPC target group | ✅ Done | Root cause: a prior session ran `DEPLOY_ENV=stanford` (wrong env → `smscdk-internal-alb` stack, wrong VPC) instead of `DEPLOY_ENV=stanford-vpc-test` (→ correct, pre-existing `smsvpctest-internal-alb` stack). Fixed by reverting the `sms-api` TGB ARN edit + recreating the k8s `TargetGroupBinding` + cleaning up the stray AWS resources. E2E-verified via curl through the live tunnel. |
| Study-detail page (`/studies/<slug>`) unstyled + non-interactive under `/workbench` | ✅ Code fixed, 🔄 not yet deployed | Root cause: the route never applied base-path rewriting (`_apply_live_base_path`) that the main shell page gets — hardcoded root-absolute asset refs (`/style.css` etc.) 404'd under the subpath. Fixed, tested, PR open, merged into `demo-v2ecoli`. **Live k8s cluster still runs the old `0.1.1` image** — this fix hasn't reached the browser yet. |
| Investigations tab sub-tabs clickable (browser confirmation) | ❌ PENDING | Blocked on the k8s image rebuild/redeploy below. Once redeployed: Investigations → click "statistical" study → confirm single, styled, collapsed pillar nav, sub-tabs clickable. |

## Key Files Touched

### AWS/k8s (outside this repo — `~/sms/sms-cdk`, `~/sms/sms-api`)
- `~/sms/sms-api/kustomize/overlays/sms-api-stanford-test/target-group-binding.yaml` —
  reverted an erroneous ARN edit (was never committed) back to the correct,
  pre-existing `smsvpctestinternalalb-workbench` target group.
- k8s `TargetGroupBinding/workbench-binding` (`sms-api-stanford-test` ns) — deleted
  + recreated with the correct ARN (ARN is immutable on this resource).
- CloudFormation stack `smscdk-internal-alb` — reverted (destroyed 3 stray
  resources: `WorkbenchTargetGroup`, `WorkbenchRouteRule`, `BigraphLoomRouteRule`)
  via a clean, previewed `cdk diff`/`cdk deploy` from `main` branch, then restored
  `workbench-alb-cotenant` branch. `smscdk-internal-alb`'s own unrelated `api`/`ptools`
  target groups were never touched.

### This repo — `vivarium_workbench/lib/report.py`
- Relocated `_normalize_asset_urls` here (from `publish.py`) alongside
  `_apply_live_base_path`, so both the static-bundle (`publish.py`) and live-server
  (`lib/study_page.py`) code paths share one canonical implementation.

### `vivarium_workbench/lib/study_page.py`
- `render_study_detail_html(ws_root, name, spec, *, base_path: str = "")` — new
  keyword-only param, passed into the Jinja context and used to run the rendered
  HTML through `_normalize_asset_urls()` then `_apply_live_base_path()` before
  returning (mirrors what `publish.py` already did for the static bundle).
- `build_study_detail_page(ws_root, slug, *, base_path: str = "")` — threads
  `base_path` through.

### `vivarium_workbench/api/app.py`
- `study_detail_page()` (`GET /studies/{slug}`) now takes a `request: Request` param,
  computes `base_path = request.scope.get("root_path") or ""` (identical pattern to
  the existing `index_shell` route), passes it through.

### `vivarium_workbench/publish.py`
- Removed the local `_normalize_asset_urls` def; imports it from `lib.report` instead.
  Its two call sites (home page + per-study shell) are unchanged.

### `vivarium_workbench/templates/study-detail.html`
- The `/api/study-analysis-zip` download-all anchor is now `{{ base_path }}`-prefixed
  (the only hardcoded absolute ref that needed explicit Jinja templating — everything
  else is covered by the shared post-render rewrite).

### `vivarium_workbench/static/walkthrough.js`
- The "seeded study" finding link now uses the existing `_studyHref()` helper
  instead of building a raw, base-path-unaware `/studies/...` href — same bug class,
  one-line fix.

### `tests/test_study_page_lib.py`
- 5 new tests (`TestRenderStudyDetailHtmlBasePath` class + one new
  `TestBuildStudyDetailPage` case) covering: default (`base_path=""`) still
  produces resolvable `/assets/...` refs; `base_path="/workbench"` prefixes every
  asset/API ref; `__DASH_CONFIG__`/runtime shim injected; `build_study_detail_page`
  threads `base_path` through.
- Fixed a pre-existing monkeypatch test (`test_builder_delegates_to_render_via_monkeypatch`)
  whose `fake_render` stub needed a `base_path` kwarg to match the new signature.

### Checkpoint docs (this repo, uncommitted, not part of the PR)
- `AGENTS.md` — added `kubectl`/`stanford` zshrc-function KUBECONFIG mapping notes.
- `NEXT_STEPS.md`, `SAVE_SLOT.md` (this file), `demos/v2ecoli/NOTES.md` — session
  checkpoint trail.
- `.todo/plans/1-fix-study-detail-interactivity.md` + `.todo/MANIFEST.md` — the
  user's own plan-tracking system (new this session — see "Key Design Decisions").
  **Not gitignored** (only the flat `todo.md` is); currently untracked, not staged.

## Key Design Decisions

1. **Root cause was operational, not code.** The ALB issue was a `DEPLOY_ENV`
   mix-up (deployed to the wrong CDK stack/VPC), not a workbench bug — the
   `--base-path` code (`_BasePathStripMiddleware`, `_apply_live_base_path`) was
   already correct and already deployed to the right stack by a colleague on
   2026-07-10. The fix was reverting a bad, uncommitted `sms-api` YAML edit that
   had "fixed" a mismatch that didn't actually exist.
2. **The study-detail bug was a real, narrow code gap**: the live `/studies/{slug}`
   route was the *only* HTML-serving route missing the base-path treatment that
   `index_shell` already had — confirmed by two independent Explore-agent traces
   converging on the same file/line, then verified by reading the source directly.
3. **Reused existing patterns rather than inventing new ones**: `_normalize_asset_urls`
   already existed in `publish.py` for the exact same problem in the static-bundle
   path; relocating it into `lib/report.py` let the live-server path reuse it
   verbatim instead of duplicating logic.
4. **Branch strategy**: `fix/study-detail-base-path` was cut from `demo-v2ecoli`
   HEAD, PR'd to `main` for review, **and** merged directly into `demo-v2ecoli`
   in parallel (clean fast-forward, pushed) so the demo branch didn't have to wait
   on PR review latency. See memory `project_demo_v2ecoli_fix_branch_strategy` —
   this is now the default pattern for future demo-v2ecoli-branched fixes.
5. **`.todo/plans/` + `.todo/MANIFEST.md`** is a new, user-introduced planning
   convention (distinct from the flat `todo.md` used for cross-repo infra
   checkpoints) — a numbered plan file per task with a `## Progress` checklist,
   indexed by `MANIFEST.md`. Kept updated live during implementation, not just
   before it. See memory `feedback_todo_protocol` (updated this session).

## Verification

- **ALB fix**: `curl` through the live `sms-proxy.sh -s smsvpctest` tunnel —
  `/workbench` (200), `/workbench/assets/style.css` (200),
  `/workbench/api/workspace` + `/workbench/api/investigation-summaries` (200, real
  data), `__DASH_CONFIG__.basePath` correctly `/workbench`. Target health
  `healthy` and stable.
- **Study-detail fix**:
  - `uv run --no-sync pytest tests/test_study_page_lib.py tests/test_study_detail_page.py tests/test_study_detail_template.py tests/test_publish.py -q`
    → **13 failed, 61 passed, 3 skipped**. All 13 failures confirmed pre-existing
    and unrelated (identical failure set with this branch's changes `git stash`ed
    out — template/test drift, not base-path related). All 5 new tests pass.
  - Manual: `uvicorn vivarium_workbench.api.app:app --root-path /workbench` →
    `GET /studies/baseline` returns every asset/API ref correctly prefixed
    (`/workbench/assets/style.css`, `/workbench/assets/data-source.js`,
    `/workbench/assets/configure-run.js`, `/workbench/assets/study-detail.js`,
    `/workbench/api/study-analysis-zip?study=baseline`) and
    `__DASH_CONFIG__ = { mode: "local-server", basePath: "/workbench" }`.
  - Manual: same server with no `--root-path` (root hosting) still resolves
    correctly (`/assets/style.css` etc.) — zero regression.

## Next Steps

1. **Get PR #465 reviewed and merged to `main`**:
   https://github.com/vivarium-collective/vivarium-workbench/pull/465
   (already merged into `demo-v2ecoli` locally+pushed, so `main`'s merge-back will
   be a no-op for this change — not urgent for the demo, just for hygiene).
2. **Rebuild + push a new `vivarium-workbench` image** (currently pinned `0.1.1`
   in the k8s Deployment) that includes commit `861aefa`, and update the k8s
   Deployment to use it. This is the actual blocker for the live browser check —
   not started this session.
3. **Once redeployed**, re-run the browser check: `sms-proxy.sh -s smsvpctest` →
   `http://localhost:8080/workbench` → Investigations → click "statistical" study
   → confirm a single, styled, collapsed pillar nav with only the active pillar's
   sub-tabs visible and clickable (was the original bug from the screenshot).
4. Optional / lower priority: the 13 pre-existing test failures found during
   verification (`test_study_detail_page.py`, `test_study_detail_template.py`,
   `test_publish.py` — tab-scaffold/panel-id/skeptic-toggle assertions, one
   snapshot-popout assertion) look like real template/test drift, unrelated to
   this session's work. Not investigated further — flagging as a separate
   cleanup candidate if wanted.
5. The uncommitted checkpoint-doc changes in this repo (`AGENTS.md`,
   `NEXT_STEPS.md`, this file, `demos/v2ecoli/NOTES.md`, `.gitignore`) and the new
   `.todo/` directory are still sitting locally on `demo-v2ecoli`, uncommitted —
   not part of PR #465. Commit separately when ready (per the do-not-commit list,
   `AGENTS.md` specifically should stay out of any commit).

## Quick Reference

```bash
# kubectl (sms-api-stanford-test namespace)
export KUBECONFIG="/Users/alexanderpatrie/.kube/kube_stanford_test.yml"

# Tunnel to the live cluster
AWS_PROFILE=stanford-sso AWS_DEFAULT_REGION=us-gov-west-1 \
  ~/sms/sms-cdk/scripts/sms-proxy.sh -s smsvpctest
# → http://localhost:8080/workbench

# Local subpath test (no k8s needed) — reproduces exactly what was broken
cd ~/vivarium-app/vivarium-dashboard
VIVARIUM_WORKBENCH_WORKSPACE="$(pwd)/tests/_fixtures/ws_increase_demo" \
VIVARIUM_WORKBENCH_DISABLE_CSRF=1 \
  uv run --no-sync uvicorn vivarium_workbench.api.app:app --root-path /workbench --port 8799
curl -s http://localhost:8799/studies/baseline | grep -o 'href="[^"]*style.css"'

# Test suite for the study-detail fix
uv run --no-sync pytest tests/test_study_page_lib.py tests/test_study_detail_page.py \
  tests/test_study_detail_template.py tests/test_publish.py -q

# PR
gh pr view 465 --web
```

## Related Files

- **PR**: https://github.com/vivarium-collective/vivarium-workbench/pull/465
- **Plan**: `.todo/plans/1-fix-study-detail-interactivity.md` (full root-cause writeup + progress checklist), indexed by `.todo/MANIFEST.md`
- **ALB fix history**: `todo.md`, `NEXT_STEPS.md` (this repo root) — the k8s/ALB routing chase from earlier this session
- **Original bug report**: `demos/v2ecoli/NOTES.md:228-234` ("Post e2e remote walkthrough")
- **Screenshot**: `demos/v2ecoli/investigation-issue.png` (untracked)
