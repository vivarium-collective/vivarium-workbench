## 3. **(.todo/plans/3-fix-composite-resolve-unhandled-errors.md)**:

### Name

Fix: composite-resolve swallows real exceptions; colony (pymunk) composite 500s unobservably

### Status

TIER 1 + TIER 2 IMPLEMENTED + COMMITTED + DEPLOYED — but ❌ **composite
still 500s in the browser**. Tier 1 (log the swallowed exception) + Tier 2
(degrade unguarded seams via `_degraded_result()`) landed in `481b3f2` on
`demo-v2ecoli`; targeted suites pass. **Tier 1 did its job**: the deployed
logging surfaced a real traceback — but not from the composite-resolve seams
Tier 2 guarded. It's `ModuleNotFoundError: No module named 'bigraph_loom'` on the
**loom-asset route** (`api/app.py` `bigraph_loom_asset` → `lib/static_serving.py`
→ `resolve_loom_asset()`). Because the Composite Explorer's loom panel is always
visible (`[[project_composite_explorer_layout]]`), opening *any* composite —
colony included — fires a loom-asset request, so a missing `bigraph_loom` renders
as a generic "internal server error" regardless of which composite is selected.
**This is now the leading candidate for the real Bug 3, and it IS the Tier 2a
dependency fix that was gated on this evidence — but it has NOT been confirmed as
the specific cause of the colony click** (the traceback came from an
already-scrolled log tail, not a live correlated repro). See "Tier 2a — now
unblocked" below. Independent of items #1/#2.

### Bug report

`demos/v2ecoli/bugs/broken-composites.png` — Composite Explorer, the "colony"
composite (v2ecoli multi-cell physics, "whole-cell E. coli agents embedded in a
pymunk 2D physics environment via the EcoliWCM bridge"): the wiring/pretty-print
panel shows `{"error":"internal server error"}` instead of the composite's
state. The composite's own metadata card (name/ID/module/description) renders
fine — only the resolve/state panel underneath fails.

### Root cause (verified against live source)

`GET /api/composite-resolve` (`vivarium_workbench/api/app.py:825-870`,
`composite_resolve()`) calls `resolve_composite_for_request(ws, id, ov)`
(`lib/composite_resolve.py:158-174`) → `resolve_composite()`
(`lib/composite_resolve.py:68-155`) for a local workspace.

This is the **one composite-introspection endpoint that still imports the
workspace package in-process** in the long-running server, unlike `/api/composites`
(`lib/composites_query.py`'s `composites_via_subprocess`, spawns a fresh
interpreter) or the run engine (`lib/composite_subprocess.py`, explicitly shells
out). This is a known, already-documented, never-finished gap:
`docs/superpowers/plans/2026-06-23-commit-agnostic-workspace-switch.md:23,467-478`
flags exactly this and calls for a follow-on subprocess-isolation plan that was
never written.

Reading the live code confirms `resolve_composite()` already guards most
failure paths gracefully — `CompositeSpec.from_file` exceptions
(`lib/composite_resolve.py:112-123`), `spec.default_state()` exceptions
(`:124-127`), and `attach_process_docs` (`:141-146`) all degrade to a
`wiring_status:"unavailable"` + honest `notice`, a 200 response, NOT a 500. The
two seams that are **not** guarded:
- `_get_spec(spec_id)` at `lib/composite_resolve.py:103` (and the
  `_prime_registry()`/`discover_generators()` call just before it, at
  `:30-37`, is itself wrapped in a bare `try/except: pass` — meaning a broken
  generator-module import during discovery is currently silently swallowed
  *there*, which could instead manifest as a wrong 404 rather than a 500;
  needs the Tier 1 evidence below to know which is actually happening for
  colony).
- `CompositeResolvePayload.model_validate(result)` at `api/app.py:870` — no
  surrounding try/except at all, unlike sibling routes (`publish.py`,
  `investigation_run_views.py`, `composite_mutations.py`) which route through
  `lib/json_serialize.py`'s `_json_default`/`_json_sanitize` numpy/inf-nan
  safety net; this seam never does.

Whatever throws in either spot falls through to the app-wide
`@app.exception_handler(Exception)` (`api/app.py:514-518`):
```python
@app.exception_handler(Exception)
async def _unhandled_error_handler(request, exc) -> JSONResponse:
    # Last resort: emit the canonical envelope instead of a bare 500. The
    # message is intentionally generic — details go to logs, not the client.
    return JSONResponse({"error": "internal server error"}, status_code=500)
```
**The comment is wrong: nothing is actually logged.** No `logger.exception`/
traceback call exists anywhere in this handler or in
`lib/request_logging.py:23-55` (which only logs method/path/status/duration via
`logger.info`). Confirmed against Starlette's dispatch: once a registered
`@app.exception_handler(Exception)` returns a response, the exception is
swallowed for good — it never reaches the layer that would otherwise print a
traceback. **The real exception is currently unobservable, even server-side.**

Confirmed independent of the (separately, already-resolved) `/workbench` ALB
subpath-deployment saga — that was pure AWS/CDK infra misconfig on an unrelated
code path, per `todo.md`/`SAVE_SLOT.md`.

Leading root-cause candidates for the actual colony failure (undetermined
without Tier 1's logging deployed): (a) a missing/broken `pymunk`/`viva_munk`
native dependency in the deployed combined image — `pbg_superpowers/composite_generator.py`'s
`apply_core_extensions()` docstring documents "v2ecoli friction #16": composites
using pymunk types need `core_extensions=[register_pymunk_types, register_processes]`
declared so the dashboard's subprocess core-build knows about them, else the
build "dies with 'cannot resolve types … pymunk_agent'" and states failures here
are "NOT swallowed" — though this friction is documented for the *build/run*
path, not necessarily this *resolve* path, so it needs confirming; or (b) a
pydantic validation/serialization gap at the unguarded `model_validate` call.
`demos/v2ecoli/NOTES.md:219` notes colony "always works" locally as of a
2026-07-06 walkthrough (local venv had `Viva-munk` installed) — the deployed
combined image's dependency set may differ.

### Fix design (tiered)

**Tier 1 — ship first, unconditionally. Pure observability, zero behavior
change to any response.**
In `api/app.py`, add `import logging` and a module logger
`_error_logger = logging.getLogger("vivarium_workbench.errors")` (distinct from
the access logger in `lib/request_logging.py`, so tracebacks are easy to
grep/filter separately from per-request access lines; `logging.basicConfig`
already called in `lib/startup.py:60-64` picks up any named logger
automatically). In `_unhandled_error_handler` (`api/app.py:514-518`), call
`_error_logger.exception("unhandled exception on %s %s", request.method, request.url.path, exc_info=exc)`
before returning the existing generic envelope. No response-shape or
status-code change — strictly additive.

**Tier 2 — ship in the same PR as Tier 1 (cheap, safe, closes the documented
unguarded-seam gap regardless of what Tier 1's logs eventually show).**
Factor the degrade-shape dict already at `lib/composite_resolve.py:112-123`
into a small reusable helper:
```python
def _degraded_result(spec_id: str, error: Exception, *, kind: str = "spec") -> dict:
    return {
        "id": spec_id, "name": spec_id.rsplit(".", 1)[-1],
        "description": "", "parameters": {}, "state": None,
        "schema": {}, "requires": {}, "tags": [], "analyses": [],
        "visualizations": [], "emitters": [], "kind": kind,
        "module": "", "default_n_steps": None, "svg": None,
        "wiring_status": "unavailable",
        "notice": f"composite could not be resolved: {error}",
    }
```
Use it in two places:
1. `lib/composite_resolve.py`'s `resolve_composite()` — wrap the section from
   `spec = _get_spec(spec_id)` (line 103) through the final return in a
   `try/except Exception as e: return _degraded_result(spec_id, e)`, so an
   in-process import blowup during generator lookup degrades instead of
   propagating (replaces the existing duplicate literal at `:112-123` with a
   call to the new helper too).
2. `api/app.py`'s `composite_resolve()` route (~846-870) — wrap the
   `CompositeResolvePayload.model_validate(result)` call; on failure, log via
   `_error_logger.exception(...)` and return
   `CompositeResolvePayload.model_validate(_degraded_result(id, e, kind=result.get("kind", "spec") if isinstance(result, dict) else "spec"))`
   instead of letting it fall through to the generic 500 handler. This also
   covers the `resolve_composite_for_request`'s remote/SMS-API branch
   (`SmsApiClient(...).composite_resolve(...)`), which isn't covered by guard
   #1 above.

This turns a bare 500 into an honest `wiring_status:"unavailable"` + `notice`
200, consistent with the endpoint's documented contract and with the sibling
degrade branches already in the same function.

**Tier 3 — explicitly deferred, not implemented now, pointer only.**
Finish the already-flagged SP2b subprocess-isolation of composite-resolve
(matching `/api/composites`'s `composites_via_subprocess` pattern) — see
`docs/superpowers/plans/2026-06-23-commit-agnostic-workspace-switch.md:467-478`.
Would make this whole class of in-process-import failure structurally
impossible. Do not block Tier 1/2 on this.

**Sequencing:** Tier 1 + Tier 2 ship together in one PR. Do **not**
speculatively implement a Dockerfile/dependency fix (candidate root cause (a))
until Tier 1's logging is deployed to `sms-api-stanford-test` and the real
traceback is read from cluster logs — that evidence decides whether a
follow-on dependency-fix PR is even needed, and if so whether it's a
Dockerfile/extras change or a `core_extensions` registration fix in the
v2ecoli sibling repo (out of this repo's scope either way).

### Tests

**Must keep passing:** `tests/test_composite_resolve_dispatch.py`,
`tests/test_composite_resolve_fallback.py`, `tests/test_api_app.py`'s existing
`composite_resolve` tests (missing→unresolved, typed passthrough, openapi
presence).

**New tests to add:**
- `tests/test_api_app.py::test_unhandled_exception_is_logged` — monkeypatch a
  route to raise, assert (via `caplog`) an ERROR-level record under
  `vivarium_workbench.errors` containing the exception, while the response
  stays `{"error": "internal server error"}` / 500 (regression guard on the
  unchanged client contract).
- `tests/test_composite_resolve_fallback.py::test_resolve_composite_degrades_on_in_process_exception` —
  monkeypatch `_get_spec` (or `_prime_registry`) to raise
  `ImportError("no module named viva_munk")`; assert `resolve_composite(...)`
  returns the standard degrade dict, not a raised exception.
- `tests/test_api_app.py::test_composite_resolve_validation_failure_degrades_not_500` —
  monkeypatch `resolve_composite_for_request` to return a payload that fails
  `CompositeResolvePayload` validation; assert the route returns 200 with
  `wiring_status: "unavailable"` instead of the generic 500.

### Verification

- **Tier 1**: fully local — `uv run pytest tests/test_api_app.py -k unhandled_exception_is_logged -x`;
  manually trigger a route to raise and tail server output for the new
  traceback line.
- **Tier 2**: fully local — new unit tests above, no pymunk/real v2ecoli
  workspace needed.
- **Deployed-environment-only, and the explicit gate to Tier 2a (dependency
  fix)**: deploy Tier 1+2, re-click the Composite Explorer's colony composite
  against the real `sms-api-stanford-test` deployment, read the pod logs for
  the now-emitted traceback. This is the only way to confirm whether a
  follow-on dependency/Dockerfile PR (candidate (a)) is actually needed —
  local dev may already have `Viva-munk` installed (per
  `demos/v2ecoli/NOTES.md:219`) and thus not reproduce the deployed failure at
  all.

### Tier 2a — now unblocked by Tier 1's deployed evidence (candidate, not yet confirmed)

The gate was "deploy Tier 1, read the real traceback." Done — and it points at a
**missing `bigraph_loom` in the deployed combined image**, not at the
composite-resolve seams. Supporting structural evidence found in the `Dockerfile`:

- The combined image builds its Python env from **v2ecoli's lockfile**
  (`Dockerfile:43-45`, `uv sync` inside `/app/v2ecoli`), NOT from the workbench's
  own lock. `bigraph-loom` is declared in *workbench's* `pyproject.toml:47` as a
  direct git URL — but if it's absent from v2ecoli's `uv.lock`, the `uv sync`
  never installs it, and the later `--no-deps` workbench overlay (`Dockerfile:55`)
  won't pull it either.
- The build sanity check (`Dockerfile:70`) imports `pbg_v2ecoli`,
  `vivarium_workbench`, `pbg_ptools.workbench_viewers` — but **not**
  `bigraph_loom`. So a missing `bigraph_loom` passes the build and only fails at
  runtime on the first loom-asset request. Exactly the observed symptom.

**Decisive local test (no cluster needed):** fetch v2ecoli's `uv.lock` and grep
for `bigraph-loom`/`bigraph_loom`. If absent → the fix is (1) add an explicit
`uv pip install --python /app/v2ecoli/.venv/bin/python --no-deps
"bigraph-loom @ git+..."` overlay in the Dockerfile (mirroring the pbg-ptools
overlay at `Dockerfile:63-66`), and (2) extend the `Dockerfile:70` sanity import
to include `import bigraph_loom` so this can never silently ship again.

**Still to confirm before/alongside the fix:** re-click "colony" in the Composite
Explorer while tailing `kubectl -n sms-api-stanford-test logs -f deploy/workbench`
to directly correlate the colony request with the `bigraph_loom` traceback (vs.
a coincidental loom-asset error from another tab). Note this may be a *distinct*
bug from the pymunk/`Viva-munk` candidate (a) originally hypothesized — the
resolve-panel-specific pymunk failure could still exist underneath once the loom
route is fixed.

### Progress notes

- **2026-07-13 (plan)**: Scoped and written. Root-caused via `Agent(Explore)`
  tracing the frontend call, backend endpoint, and the error-swallowing exception
  handler; fix designed via `Agent(Plan)`; cross-checked against
  `lib/composite_resolve.py` and `api/app.py`.
- **2026-07-13 (Tier 1+2 implemented)**: Landed in `481b3f2` (same commit as #2).
  Catch-all handler now `logger.exception(...)`s; both unguarded seams degrade via
  the new shared `_degraded_result()` helper. Targeted suites
  (`test_composite_resolve_dispatch.py`, `test_composite_resolve_fallback.py`,
  `test_api_app.py`) pass.
- **2026-07-13 (deployed — Tier 1 surfaced new evidence; still 500)**: Composite
  still 500s in browser. Tier 1 logging captured `ModuleNotFoundError: No module
  named 'bigraph_loom'` on the loom-asset route (not the guarded resolve seams),
  which now unblocks Tier 2a — see "Tier 2a — now unblocked" above. **This is
  where the work stopped**; Tier 2a not yet confirmed for the colony click or
  implemented.

---
