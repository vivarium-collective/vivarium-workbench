1. **(.todo/plans/1-fix-study-detail-interactivity.md)**:

### Name

Fix: Study-detail page unstyled/non-interactive under /workbench subpath

### Status

PR OPEN (REVIEW_REQUIRED) — https://github.com/vivarium-collective/vivarium-workbench/pull/465; merged into demo-v2ecoli (861aefa). k8s image rebuilt+deployed to sms-api-stanford-test (e74b644), curl-verified. Remaining: human browser click-through only (see NEXT_STEPS.md); PR merge to main is hygiene-only, non-blocking.

---

## 2. **(.todo/plans/2-fix-csrf-origin-guard-reverse-proxy.md)**:

### Name

Fix: CSRF/origin guard 403s all POST/DELETE behind ALB reverse-proxy subpath deployment

Linked tasks: independent of #1 and #3 (different subsystem, no shared files, no ordering dependency); shares the broader demo-v2ecoli e2e-walkthrough context with both.

### Status: IMPLEMENTED, TESTED, COMMITTED — not pushed, no PR yet

Fix landed in `481b3f2` (`demo-v2ecoli`, local only). Opt-in `trust_forwarded`/`forwarded_host` params on `is_request_allowed()` + `--trust-proxy`/`VIVARIUM_WORKBENCH_TRUST_PROXY=1`. Targeted suites (`test_csrf_lib.py`, `test_csrf_origin_guard.py`, `test_api_app.py::TestCsrfMiddleware`) pass. Remaining: push `demo-v2ecoli`, cherry-pick `481b3f2` onto a `main`-based branch, open PR (see `SAVE_SLOT.md`).

---

## 3. **(.todo/plans/3-fix-composite-resolve-unhandled-errors.md)**:

### Name

Fix: composite-resolve swallows real exceptions; colony (pymunk) composite 500s unobservably

Linked tasks: independent of #1 and #2 (different subsystem, no shared files, no ordering dependency); shares the broader demo-v2ecoli e2e-walkthrough context with both.

### Status: TIER 1 + TIER 2 IMPLEMENTED, TESTED, COMMITTED — not pushed, no PR yet

Fix landed in `481b3f2` (`demo-v2ecoli`, local only), same commit as #2. Catch-all handler now `logger.exception(...)`s; both unguarded seams in `resolve_composite()` degrade via new shared `_degraded_result()` helper instead of 500ing. Targeted suites (`test_composite_resolve_dispatch.py`, `test_composite_resolve_fallback.py`, `test_api_app.py`) pass. Tier 2a (dependency/Dockerfile fix) still explicitly deferred, gated on reading the deployed traceback once Tier 1 ships. Remaining: push, PR (see `SAVE_SLOT.md`).

---


