# Next Steps — demo-v2ecoli e2e walkthrough: 1 of 3 bugs fixed, 2 still broken post-deploy

**Updated:** 2026-07-13 — synced to `SAVE_SLOT.md` (the current ground truth).
Supersedes the earlier subpath-deployment content, which is **resolved** (see
`SAVE_SLOT.md` history + `.todo/plans/1-*.md`). The active work is now the two
remaining demo bugs whose code fixes shipped in `481b3f2`, deployed to
`sms-api-stanford-test`, but **failed browser verification**.

## Ground truth from the browser walkthrough

| Bug | Status | Where it stands |
|---|---|---|
| 1. Study-detail base-path | ✅ **CONFIRMED FIXED** (browser-verified) | No further action. |
| 2. CSRF/origin guard 403 | ❌ **STILL BROKEN** | `--trust-proxy` fix is present in the running pod's args, yet `POST /workbench/api/study-run-baseline → 403` still reproduces server-side (pod logs). *Why the fix isn't taking effect is not yet root-caused.* |
| 3. Composite Explorer 500 | ❌ **STILL BROKEN** | Tier-1 logging (shipped in `481b3f2`) worked and surfaced a new traceback: `ModuleNotFoundError: No module named 'bigraph_loom'` on the loom-asset route. Leading candidate for the real cause — **not yet confirmed for the colony click specifically**. |

## Remaining gaps (priority order)

1. **Bug 2 — find why `--trust-proxy` isn't working.** Read `lib/csrf.py`
   (`is_request_allowed`, `is_trust_proxy_via_env`) + `api/app.py`'s `_csrf_mw`
   together. Three live hypotheses (see `SAVE_SLOT.md` "Key Finding — Bug 2"):
   (a) the ALB/tunnel chain never sets `X-Forwarded-Host` (only `-For`/`-Proto`),
   so `forwarded_host` is empty and falls back to the mismatching raw `Host`;
   (b) `is_trust_proxy_via_env()` reads the wrong env var (old vs new prefix);
   (c) uvicorn's `ProxyHeadersMiddleware` strips `X-Forwarded-Host` first.
   **Blocked on**: inspecting the actual header values arriving through
   ALB→SSM-tunnel→k8s — needs a `curl -v` through the live tunnel or a temporary
   debug-log redeploy.

2. **Bug 3 — confirm the missing-dependency theory (Tier 2a).** The combined
   image builds its env from **v2ecoli's lockfile** (`Dockerfile:43-45`), not the
   workbench's own; `bigraph-loom` is declared in *workbench's* `pyproject.toml:47`
   but may be absent from v2ecoli's lock, so it never installs. The build sanity
   check (`Dockerfile:70`) imports `vivarium_workbench` but **not** `bigraph_loom`,
   so a missing module ships silently and only fails at runtime — consistent with
   the symptom. **Cheapest decisive test (local, no cluster):** check whether
   `bigraph-loom`/`bigraph_loom` resolves in v2ecoli's `uv.lock`. If missing:
   add an explicit `uv pip install --no-deps bigraph-loom` overlay + extend the
   `Dockerfile:70` sanity import to include `bigraph_loom`. Separately, re-click
   "colony" while tailing `kubectl logs -f deploy/workbench` to correlate the
   request with its traceback directly.

3. **Rebuild → deploy → re-walkthrough** once both root causes are fixed
   (`gh workflow run build-and-push.yml --ref demo-v2ecoli` → bump sms-api overlay
   tag → `kubectl apply -k` → re-verify in browser). Only then are the PRs
   review-ready.

4. **Triage the full test suite.** `F` failures appeared ~28% into the last run;
   never characterized as pre-existing vs. new. The run log was session-scoped
   (likely gone) — a fresh `uv run pytest` may be needed to re-baseline.

5. **PRs remain open, not merged.** #465 (workbench→main) and sms-api #169 both
   REVIEW_REQUIRED; per `SAVE_SLOT.md` both need follow-up commits (the real
   root-cause fixes for bugs 2 & 3) before they are actually ready for review.

## Quick reference

```bash
# Tunnel (check if still alive first)
ps aux | grep sms-proxy
AWS_PROFILE=stanford-sso AWS_DEFAULT_REGION=us-gov-west-1 \
  ~/sms/sms-cdk/scripts/sms-proxy.sh -s smsvpctest   # if not — → localhost:8080/workbench

# Cluster
export AWS_PROFILE=stanford-sso AWS_DEFAULT_REGION=us-gov-west-1 \
  KUBECONFIG=/Users/alexanderpatrie/.kube/kube_stanford_test.yml
kubectl -n sms-api-stanford-test logs -f deploy/workbench

# PRs (both open, not merged)
gh pr view 465
gh pr view 169 -R vivarium-collective/sms-api
```

**See also:** `SAVE_SLOT.md` (full mid-diagnosis checkpoint), `.todo/MANIFEST.md`,
`.todo/plans/2-*.md` + `3-*.md` (per-bug plans, now advanced to deployed+unverified).
