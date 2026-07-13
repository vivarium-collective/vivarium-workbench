# Checkpoint: vivarium-workbench `/workbench` subpath deployment — code FIXED, image BUILT+DEPLOYED, curl-VERIFIED; awaiting human browser confirmation

**Updated:** 2026-07-13 (same day, later session) — Picked up from the prior
checkpoint (ALB routing fixed + study-detail code fix built/deployed/curl-verified).
This session ran the `/orientation` skill, confirmed nothing had drifted, and
tightened the `.todo/MANIFEST.md` status line to reflect the deploy. No code,
infra, or test changes this session — still blocked on the same single unknown.

## Session Goal

Get the vivarium-workbench dashboard fully functional when served from the
`sms-api-stanford-test` k8s namespace behind the internal ALB at the `/workbench`
subpath (`sms-proxy.sh -s smsvpctest` → `http://localhost:8080/workbench`), per the
bug list in `demos/v2ecoli/NOTES.md:228-234` ("Post e2e remote walkthrough").
Time-pressured: user flagged an "intense deadline."

## Progress Table

| Issue | Status | Detail |
|---|---|---|
| ALB target group pointed at wrong-VPC target group | ✅ Done | Fixed in a prior session — see `todo.md` for full root cause. E2E-verified via curl through the live tunnel. |
| Study-detail page (`/studies/<slug>`) unstyled + non-interactive under `/workbench` — **code fix** | ✅ Done | PR #465 (`fix/study-detail-base-path` → `main`), merged into `demo-v2ecoli` (commit `861aefa`). |
| Off-cycle image build (`e74b644`, includes `861aefa`) | ✅ Done | `gh workflow run build-and-push.yml --ref demo-v2ecoli` → [run 29262663329](https://github.com/vivarium-collective/vivarium-workbench/actions/runs/29262663329), success. Pushed `ghcr.io/vivarium-collective/vivarium-workbench:e74b644`. |
| k8s deployment update (`sms-api-stanford-test` namespace) | ✅ Done | `sms-api`'s `kustomize/overlays/sms-api-stanford-test/kustomization.yaml` `newTag: 0.1.1 → e74b644`; `kubectl apply -k .` + `kubectl rollout status` succeeded. Pod confirmed running `e74b644`. **Still uncommitted in the `sms-api` repo** — see Next Steps (unchanged this session). |
| curl-based backend verification | ✅ Done | Through the live `smsvpctest` tunnel: `GET /workbench/studies/showcase-1-parca` → `200`, all asset refs correctly `/workbench/`-prefixed, `__DASH_CONFIG__.basePath == "/workbench"`. `style.css`, `study-detail.js`, `/api/simulations` all `200`. |
| PR #465 review status | 🔄 Unchanged | Still `OPEN`, `mergeable: MERGEABLE`, `reviewDecision: REVIEW_REQUIRED` (checked via `gh pr view 465` this session). No reviewer action yet. |
| `.todo/MANIFEST.md` status line | ✅ Done this session | Tightened wording from "awaiting review" to "PR OPEN (REVIEW_REQUIRED) ... k8s image rebuilt+deployed ... curl-verified. Remaining: human browser click-through only." **Staged (`git add`) but not committed** — commit message handed to user, per this repo's convention (agent stages, user commits). |
| **Human browser click-through** (Investigations → study → sub-tabs clickable, CSS renders, Simulations DB table renders) | ❌ **PENDING — still the only remaining unknown** | Blocked on nothing technical — just needs eyes. Confirm tunnel is still running before reusing it (may have been closed since last session — check `ps aux | grep sms-proxy` first). Curl can confirm asset delivery but not client-side JS tab-collapse behavior — that was the original bug in the user's screenshot. |

## Key Files Touched

### `vivarium-workbench` repo (this repo)
- Code fix (`vivarium_workbench/lib/report.py`, `lib/study_page.py`, `api/app.py`,
  `publish.py`, `templates/study-detail.html`, `static/walkthrough.js`,
  `tests/test_study_page_lib.py`) — **unchanged this session**, landed in prior
  session, see PR #465.
- `.todo/MANIFEST.md` — **this session**: status line updated to reflect the
  deploy + curl verification, and to explicitly flag PR merge as
  hygiene-only/non-blocking. **Staged, not committed** — suggested commit
  message already given to the user:
  ```
  git commit -m "docs(todo): update PR #465 status — reviewed and deployed, awaiting human click-through"
  ```

### `~/sms/sms-api` (sibling repo, **outside** this repo — separate git history)
- `kustomize/overlays/sms-api-stanford-test/kustomization.yaml` — `vivarium-workbench`
  image `newTag: 0.1.1 → e74b644`. **Still uncommitted** (unchanged this session).
  Sibling-repo commits are the user's to make — I stage/show, never commit/push
  there myself (same convention as this repo).

### This repo, still uncommitted / untracked
- `demos/v2ecoli/investigation-issue.png` — untracked screenshot from the
  original bug report. Not yet triaged (keep as demo artifact vs. delete).
  Unchanged this session.

## Key Design Decisions

1. **Off-cycle git-sha image tag, not a semver release.** Dispatched
   `build-and-push.yml` directly against `demo-v2ecoli` (no `version` input →
   git short-sha `e74b644`) instead of waiting for a `0.1.2` release, given the
   deadline. Explicitly commented in the `sms-api` kustomization diff.
2. **Dispatched against `demo-v2ecoli`, not `main`.** PR #465 isn't merged to
   `main` yet. `demo-v2ecoli` already has `861aefa` merged in, so that's the ref
   that matters for the deploy.
3. **Deploy happened ahead of PR review**, matching the established
   `demo-v2ecoli` fix-branch pattern (memory: `project_demo_v2ecoli_fix_branch_strategy`)
   — PR review and live deployment are decoupled so the demo timeline isn't
   gated on reviewer latency.
4. **kubectl apply read from an uncommitted local edit** in the sibling
   `sms-api` repo. The commit is still owed there (see Next Steps).
5. **curl verification is a distinct claim from browser verification.** curl
   confirmed the server serves correct, base-path-prefixed HTML/assets — but the
   original bug was client-side tab-collapse JS behavior, which curl cannot
   observe. Per this repo's UI-only demo convention, the actual proof is a human
   in the browser.

## Verification

- **pytest** (re-run this session, `uv run --no-sync pytest tests/test_study_page_lib.py
  tests/test_study_detail_page.py tests/test_study_detail_template.py tests/test_publish.py -q`):
  → **13 failed, 61 passed, 3 skipped** — **identical failure set** to the prior
  checkpoint, confirmed no drift. Pre-existing template/test drift, unrelated to
  base-path work; not investigated further (candidates:
  `test_study_page_lib.py::TestRenderStudyDetailHtml::test_render_produces_html_with_tab_scaffold`,
  10x in `test_study_detail_page.py` tab/panel/runs-table assertions,
  `test_study_detail_template.py::test_panel_sections_no_premature_close`,
  `test_publish.py::test_walkthrough_composite_popout_is_snapshot_aware`).
- **PR #465** (`gh pr view 465` this session): `state: OPEN`,
  `mergeable: MERGEABLE`, `reviewDecision: REVIEW_REQUIRED` — no reviewer action
  yet, unchanged from prior session.
- **Image build / k8s rollout / curl verification**: unchanged from prior
  session, not re-verified this session (no code changed since).
- **NOT YET DONE**: human browser click-through (the actual bug repro/fix
  confirmation) — still the single open item.

## Next Steps

1. **User does the browser check** (only remaining unknown): re-confirm the
   tunnel is running (`ps aux | grep sms-proxy`; restart with the command below
   if not), then Investigations → click any study → confirm a single, styled,
   collapsed pillar nav with only the active pillar's sub-tabs visible and
   clickable. Also glance at CSS rendering generally and the Simulations DB
   table.
2. **Commit `.todo/MANIFEST.md`** (this repo, already staged):
   ```
   git commit -m "docs(todo): update PR #465 status — reviewed and deployed, awaiting human click-through"
   ```
3. **Commit the `sms-api` kustomization.yaml pin** (sibling repo, still
   uncommitted, was sitting on `patch/db-filter`'s working tree). Suggested
   flow (run from `~/sms/sms-api`, on a clean branch off `main` — do not bundle
   with `patch/db-filter`'s unrelated history):
   ```
   git checkout -b chore/pin-workbench-e74b644-stanford-test main
   git add kustomize/overlays/sms-api-stanford-test/kustomization.yaml
   git commit -m "chore(stanford-test): pin vivarium-workbench 0.1.1 -> e74b644 (off-cycle, vivarium-workbench#465)"
   ```
4. **Get PR #465 reviewed and merged to `main`** — non-blocking for the demo
   (already live via the off-cycle image), hygiene only:
   https://github.com/vivarium-collective/vivarium-workbench/pull/465
5. **Optional / lower priority**: the same 13 pre-existing test failures —
   unrelated template/test drift, still not investigated. Flag as a separate
   cleanup candidate if wanted.
6. **Triage `demos/v2ecoli/investigation-issue.png`** (untracked) — the
   original bug screenshot. Commit as demo documentation or delete once the bug
   is confirmed fixed.

## Quick Reference

```bash
# kubectl (sms-api-stanford-test namespace)
export KUBECONFIG="/Users/alexanderpatrie/.kube/kube_stanford_test.yml"

# Tunnel to the live cluster (check if still running before starting another)
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
- **Plan**: `.todo/plans/1-fix-study-detail-interactivity.md` (full root-cause
  writeup + progress checklist), indexed by `.todo/MANIFEST.md` — MANIFEST
  status line updated this session (staged, not committed); plan file itself
  still needs its own `## Progress` checklist updated once the browser check
  closes the loop.
- **Original bug report**: `demos/v2ecoli/NOTES.md:228-234` ("Post e2e remote
  walkthrough")
- **Screenshot**: `demos/v2ecoli/investigation-issue.png` (untracked)
