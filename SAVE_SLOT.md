# Checkpoint: Two demo-v2ecoli dashboard bugs — root-caused, fixed, tested; local commits made, not yet pushed/PR'd

**Updated:** 2026-07-13 (same day, later session) — Superseded the prior
`/workbench` subpath-deployment checkpoint (that work is unchanged: PR #465
still open, human browser click-through still the only remaining item there —
see the "Related Files" section below for that thread). This session's focus
shifted to two new bugs reported via screenshots
(`demos/v2ecoli/bugs/broken-runs.png`, `broken-composites.png`).

## Session Goal

User reported two bugs via screenshots and asked (via `/plan`, Todo Protocol)
to scope, then implement, robust fixes:

1. **Run button → `{"error":"cross-origin request forbidden"}` (403)** when
   accessed through the ALB reverse-proxy `/workbench` subpath deployment.
2. **Composite Explorer → `{"error":"internal server error"}`** for the
   "colony" composite (v2ecoli pymunk-based multi-cell physics), with zero
   diagnostic trail server-side.

Confirmed independent (different subsystems, no shared root cause). Both are
now fully implemented, tested, and committed locally on `demo-v2ecoli` — not
yet pushed, no PR opened yet (per this repo's fix-branch-to-main +
merge-to-demo-v2ecoli-in-parallel convention, still pending).

## Progress Table

| Issue | Status | Detail |
|---|---|---|
| Bug 1 root cause found | ✅ Done | `lib/csrf.py`'s `is_request_allowed()` compares raw `Host` vs `Origin` with zero reverse-proxy awareness; ALB→tunnel→k8s hop chain can rewrite `Host`. |
| Bug 1 fix implemented | ✅ Done | Opt-in `trust_forwarded`/`forwarded_host` params on the predicate; `VIVARIUM_WORKBENCH_TRUST_PROXY=1` / new `--trust-proxy` CLI flag gates it. Default behavior unchanged. |
| Bug 2 root cause found | ✅ Done | `/api/composite-resolve` is the one composite-introspection endpoint still running in-process; two unguarded seams (`_get_spec` lookup, route's `model_validate`); the app-wide catch-all handler's comment claimed logging happened but it never did. |
| Bug 2 fix implemented (Tier 1 + Tier 2) | ✅ Done | Catch-all handler now `logger.exception(...)`s the real traceback (Tier 1). Both unguarded seams now degrade to the existing `wiring_status:"unavailable"` + `notice` 200 shape via a new shared `_degraded_result()` helper (Tier 2). Tier 2a (dependency/Dockerfile fix, if needed) explicitly deferred — gated on reading the real traceback from deployed logs once Tier 1 ships. |
| Todo-protocol plan docs | ✅ Done | `.todo/plans/2-fix-csrf-origin-guard-reverse-proxy.md`, `.todo/plans/3-fix-composite-resolve-unhandled-errors.md`, cross-linked in `.todo/MANIFEST.md`. |
| Targeted tests (new + existing CSRF/composite-resolve suites) | ✅ Done | All new + pre-existing tests in `test_csrf_lib.py`, `test_csrf_origin_guard.py`, `test_composite_resolve_dispatch.py`, `test_composite_resolve_fallback.py`, and the relevant `test_api_app.py` classes pass. |
| Full repo test suite | 🔄 **In progress** | Background run (`uv run --no-sync pytest tests/ -q`) still running as of this checkpoint — see Verification below for what's confirmed so far and what to check when it finishes. |
| Local commits | ✅ Done | Two commits made on `demo-v2ecoli` (docs+screenshots, then code+tests) — see Key Files Touched. **Not pushed yet.** |
| Push to origin / PR to `main` | ❌ **PENDING** | User asked to "suggest, execute, and push commits... and update the PR description" — push + PR creation was in progress when this checkpoint was written. Per `project_demo_v2ecoli_fix_branch_strategy`: code fix needs its own branch off `main` (PR opened, review pending) in parallel with landing directly on `demo-v2ecoli` (already done via local commit) so the demo isn't blocked on review. |

## Key Files Touched

### Bug 1 (CSRF/reverse-proxy)
- `vivarium_workbench/lib/csrf.py` — `is_request_allowed()` gains
  `forwarded_host`/`trust_forwarded` kwargs (additive, default-safe); new
  `is_trust_proxy_via_env()`.
- `vivarium_workbench/lib/env_compat.py` — new `TRUST_PROXY_ENV` suffix constant.
- `vivarium_workbench/api/app.py` — `_csrf_mw` now passes
  `X-Forwarded-Host`/the new env check into the predicate.
- `vivarium_workbench/cli.py` — new `--trust-proxy` flag on `serve`, sets
  `VIVARIUM_WORKBENCH_TRUST_PROXY=1` in `cmd_serve`.
- Tests: `tests/test_csrf_lib.py`, `tests/test_csrf_origin_guard.py`,
  `tests/test_api_app.py::TestCsrfMiddleware` (new cases each).

### Bug 2 (composite-resolve error swallowing)
- `vivarium_workbench/api/app.py` — module-level `_error_logger`; the
  `@app.exception_handler(Exception)` now logs via `logger.exception(...)`;
  the `composite_resolve()` route wraps `model_validate` in try/except,
  degrading via `_degraded_result` on failure.
- `vivarium_workbench/lib/composite_resolve.py` — new `_degraded_result()`
  helper (factored out of the pre-existing `CompositeSpec.from_file` except
  branch, which now calls it with an overridden `notice` to preserve its
  original specific wording); `resolve_composite()`'s body wrapped in an outer
  try/except so an in-process `_get_spec`/discovery failure degrades instead
  of propagating.
- Tests: `tests/test_composite_resolve_dispatch.py` (new
  `test_resolve_degrades_when_get_spec_raises`), `tests/test_api_app.py` (new
  `test_composite_resolve_validation_failure_degrades_not_500`,
  `test_unhandled_exception_is_logged`).

### Docs (already committed, commit `c28c6a1`)
- `.todo/plans/2-fix-csrf-origin-guard-reverse-proxy.md`,
  `.todo/plans/3-fix-composite-resolve-unhandled-errors.md`,
  `.todo/MANIFEST.md` (items 2 and 3 added, cross-linked to item 1 and each
  other as independent).
- `demos/v2ecoli/bugs/broken-runs.png`, `broken-composites.png` — the
  original bug-report screenshots, now committed as demo documentation.

### Still uncommitted / untracked, NOT part of this work
- `scripts/set-govcloud-env.sh` — untracked, opened by the user in their IDE
  mid-session; unrelated to either bug fix; intentionally left untouched (not
  staged, not investigated) — this is the user's own in-progress file.

## Key Design Decisions

1. **Opt-in trust flag, not automatic forwarded-header trust.** Blindly
   trusting `X-Forwarded-Host` would let any direct client spoof it to bypass
   the CSRF guard. `--trust-proxy`/`VIVARIUM_WORKBENCH_TRUST_PROXY=1` requires
   an explicit operator decision, mirroring the existing `--disable-csrf`
   trust model in this repo.
2. **Tiered fix for bug 2, not a guess at the root cause.** Rather than
   speculatively patching a Dockerfile/dependency issue, Tier 1 (logging) +
   Tier 2 (defensive degrade) ship together now; the actual missing-dependency
   question (if any) is explicitly deferred until the new logging is deployed
   and the real traceback can be read from cluster logs.
3. **Shared `_degraded_result()` helper**, reused at both the
   `lib/composite_resolve.py` internal-guard site and the `api/app.py`
   route-level guard site, and also replacing a previously-duplicated dict
   literal in the pre-existing `CompositeSpec.from_file` except branch (with
   an overridable `notice` param so that branch's more specific message —
   "composite file could not be parsed" — is preserved; a first pass
   regressed `test_resolve_malformed_static_file_degrades` by losing this
   specific wording, caught and fixed before finalizing).
4. **`TestClient(raise_server_exceptions=False)` needed for the new
   exception-logging test.** Starlette's `ServerErrorMiddleware` always
   re-raises after running a registered `Exception` handler (by design, for
   dev/test visibility) — the default `TestClient` surfaces that as a raised
   exception rather than a response. Real deployments (uvicorn) never see
   this; the client already got the response before the re-raise. Used a
   second, lenient `TestClient` instance against the same `app` object for
   just that one test.
5. **Two separate commits**: docs+screenshots first, then code+tests — kept
   distinct so the doc/planning trail and the actual behavior change are each
   independently reviewable.

## Verification

- **Targeted suites** (`uv run --no-sync pytest tests/test_csrf_lib.py
  tests/test_csrf_origin_guard.py tests/test_composite_resolve_dispatch.py
  tests/test_composite_resolve_fallback.py tests/test_api_app.py -q`): **528
  passed**, 1 pre-existing unrelated failure
  (`TestStaticRoutes::test_index_shell_renders_then_serves` — confirmed via
  `git stash` to fail identically on the unmodified tree, a lambda/kwarg
  signature mismatch unrelated to either fix).
- **Full repo suite** (`uv run --no-sync pytest tests/ -q`, excluding the two
  known-pre-existing failures above plus
  `test_composite_subprocess_lib.py::test_generator_path_success_shape`
  — also confirmed pre-existing via `git stash`, an unrelated
  `pbg_superpowers` registry/dataclass issue): **was still running
  in the background when this checkpoint was written.** Next agent: check
  the result (rerun if the background task is gone) before pushing/opening
  the PR, in case something outside the targeted suites regressed.
- **CLI flag smoke test**: `uv run --no-sync python -m vivarium_workbench.cli
  serve --help` shows `--trust-proxy` with the expected help text.

## Next Steps

1. **Confirm the full test suite result** (see Verification above) — rerun
   `uv run --no-sync pytest tests/ -q --deselect tests/test_api_app.py::TestStaticRoutes::test_index_shell_renders_then_serves --deselect tests/test_composite_subprocess_lib.py::test_generator_path_success_shape`
   if the background run's outcome wasn't captured.
2. **Push `demo-v2ecoli`** (2 local commits ahead of `origin/demo-v2ecoli`:
   `c28c6a1` docs, `481b3f2` code+tests) — `git push`.
3. **Open the fix-to-main PR** per `project_demo_v2ecoli_fix_branch_strategy`:
   branch off `main`, cherry-pick `481b3f2` (the code+tests commit — not the
   docs commit, which is demo-v2ecoli-specific bookkeeping), push, `gh pr
   create` with a description covering both bugs (see the two `.todo/plans/`
   files for the full write-up to draw from). Do NOT merge — review required
   per `feedback_pr_review_required`.
4. **Once Tier 1 (logging) is deployed** to `sms-api-stanford-test`, re-click
   the Composite Explorer's colony composite and read the pod logs for the
   now-emitted traceback — this decides whether Tier 2a (a dependency/Dockerfile
   fix, or a `core_extensions` registration fix in the v2ecoli sibling repo)
   is actually needed. Do not guess ahead of that evidence.
5. **Unrelated, pre-existing, not touched this session**: the `/workbench`
   subpath-deployment thread (PR #465) is unchanged — still open, still
   `REVIEW_REQUIRED`, human browser click-through still the one remaining
   item there. See "Related Files" below.
6. **`scripts/set-govcloud-env.sh`** — untracked, not mine to touch; leave for
   the user.

## Quick Reference

```bash
# Targeted tests for these two fixes
uv run --no-sync pytest tests/test_csrf_lib.py tests/test_csrf_origin_guard.py \
  tests/test_composite_resolve_dispatch.py tests/test_composite_resolve_fallback.py \
  tests/test_api_app.py -q

# Full suite, excluding known-pre-existing-and-unrelated failures
uv run --no-sync pytest tests/ -q \
  --deselect tests/test_api_app.py::TestStaticRoutes::test_index_shell_renders_then_serves \
  --deselect tests/test_composite_subprocess_lib.py::test_generator_path_success_shape

# CLI flag smoke test
uv run --no-sync python -m vivarium_workbench.cli serve --help

# Push + PR flow (once tests confirmed green)
git push
git checkout -b fix/csrf-proxy-and-composite-resolve-errors main
git cherry-pick 481b3f2
git push -u origin fix/csrf-proxy-and-composite-resolve-errors
gh pr create --base main --title "..." --body "..."
```

## Related Files

- **This session's plans**: `.todo/plans/2-fix-csrf-origin-guard-reverse-proxy.md`,
  `.todo/plans/3-fix-composite-resolve-unhandled-errors.md`, indexed in
  `.todo/MANIFEST.md` items 2 and 3.
- **Bug screenshots**: `demos/v2ecoli/bugs/broken-runs.png`,
  `demos/v2ecoli/bugs/broken-composites.png`.
- **Unrelated, still-open prior thread** (`.todo/plans/1-fix-study-detail-interactivity.md`,
  MANIFEST item 1): PR https://github.com/vivarium-collective/vivarium-workbench/pull/465,
  still `OPEN`/`REVIEW_REQUIRED`; human browser click-through still pending;
  nothing in this session changed that thread's state.
