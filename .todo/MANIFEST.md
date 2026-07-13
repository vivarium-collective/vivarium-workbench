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

### Status: ❌ DEPLOYED but STILL BROKEN — root cause of why the fix isn't taking effect NOT yet found

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

### Status: PLANNED — approved plan, no code written yet

The demo is redefined to run against the REMOTE `/workbench` k8s deployment via `sms-proxy.sh -s smsvpctest` → `localhost:8080/workbench`. Bug 3 root cause CONFIRMED (v2ecoli `uv.lock` has zero `bigraph-loom`; combined image builds from that lock → `bigraph_loom` never installed → `ModuleNotFoundError` on the always-visible loom panel). Bug 2 NARROWED (AWS ALB omits `X-Forwarded-Host`, so `--trust-proxy` is a no-op; one live header capture pending) → production-grade allowed-origins allowlist chosen. Six workstreams: WS-A Bug 3 Dockerfile install + broadened sanity import; WS-B Bug 2 diagnose→allowlist (code on `demo-v2ecoli`, env on `patch/db-filter`); WS-C rewrite `WALKTHROUGH.md` remote-first (local flow → Appendix G); WS-D iterative build(gh-action)→deploy→verify on the two feature branches ONLY; WS-E full e2e walkthrough as the acceptance gate; WS-F merge + release, gated on WS-E reproducibility. Full plan: `.todo/plans/4-remote-govcloud-demo-e2e.md` (mirror at `~/.claude/plans/giggly-hatching-globe.md`).

---


