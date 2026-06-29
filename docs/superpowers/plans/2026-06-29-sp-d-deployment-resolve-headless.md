# Deployment Composite Resolve (SP-D1) — Headless-Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** The dashboard half of SP-D1 — when the active workspace is a remote build, route the Composite Explorer's resolve to sms-api instead of local `build_core`, so the design+wiring is in place for the deployment cutover.

**Architecture:** A new stdlib `SmsApiClient.composite_resolve(...)` (POST to the new sms-api route) + a `resolve_composite_for_request(...)` dispatcher that picks local vs deployment by `run_core.run_target_for(ws)`. The local path is untouched; the deployment path reads the build's `simulator_id` from `.viv-build.json` and calls sms-api. All fakes-tested — no network.

**Tech Stack:** Python 3.12, stdlib `urllib` (the existing `SmsApiClient`), pytest `monkeypatch`.

## Global Constraints
- **No new dependencies.** `SmsApiClient` is stdlib urllib; reuse `_post` + the per-call `timeout` pattern (WS3).
- **Local resolve path unchanged** — `resolve_composite(...)` (used by `publish.build_bundle`) is not modified; the dispatcher wraps it.
- **Shape-compatible** — the deployment resolve returns the *same* dict shape the local resolve does (so the Explorer + SP-C config form work unchanged).
- **Fakes only** — never hit a real sms-api. Work in worktree `/Users/eranagmon/code/vdash-sp-d` (branch `feat/sp-d-deployment-resolve`). Tests: `cd /Users/eranagmon/code/vdash-sp-d && PYTHONPATH=$PWD:/Users/eranagmon/code/investigation-contracts /Users/eranagmon/code/v2ecoli/.venv/bin/python -m pytest <path> -v`. Ruff: `/Users/eranagmon/code/v2ecoli/.venv/bin/ruff check <file>`.

---

### Task 1: `SmsApiClient.composite_resolve`
**Files:** Modify `vivarium_dashboard/lib/sms_api_client.py`; Test `tests/test_sms_api_client.py` (append).
**Interfaces — Produces:** `SmsApiClient.composite_resolve(simulator_id: int, composite_ref: str, overrides: dict | None = None, timeout: float | None = None) -> dict` — `POST /core/v1/simulator/{simulator_id}/composite-resolve` with JSON body `{"composite_ref": composite_ref, "overrides": overrides or {}}`; returns the parsed JSON. Reuses `_post` (which raises `SmsApiError` on non-200/network).

- [ ] **Step 1: Write the failing test** (mirror the file's `fake_urlopen`/`_Resp` pattern; assert URL + JSON body + returned dict):
```python
def test_composite_resolve_posts_to_simulator_route(monkeypatch):
    seen = {}
    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["method"] = req.get_method()
        seen["body"] = req.data
        return _Resp({"name": "c", "parameters": {}, "state": {}})
    monkeypatch.setattr(sac, "urlopen", fake_urlopen)
    out = sac.SmsApiClient("http://x").composite_resolve(66, "pkg.composites.cell", {"k": 5})
    assert seen["url"] == "http://x/core/v1/simulator/66/composite-resolve"
    assert seen["method"] == "POST"
    import json
    assert json.loads(seen["body"]) == {"composite_ref": "pkg.composites.cell", "overrides": {"k": 5}}
    assert out["name"] == "c"
```
- [ ] **Step 2: Run → fail** (`composite_resolve` undefined).
- [ ] **Step 3: Implement** — add to `SmsApiClient` (place near `simulator_status`/`list_build_simulations`):
```python
    def composite_resolve(self, simulator_id: int, composite_ref: str,
                          overrides: dict | None = None, timeout: float | None = None) -> dict:
        """Resolve a composite IN a build's environment, on the deployment.

        POST /core/v1/simulator/{id}/composite-resolve — sms-api runs build_core
        for ``composite_ref`` (with ``overrides``) inside build ``simulator_id``'s
        image and returns the resolved-composite JSON (shape-compatible with the
        dashboard's local /api/composite-resolve). Raises SmsApiError on failure.
        """
        return self._post(
            f"/core/v1/simulator/{simulator_id}/composite-resolve",
            json_body={"composite_ref": composite_ref, "overrides": overrides or {}},
        )
```
(If `_post` does not accept a per-call timeout yet, leave the `timeout` param accepted-but-unused for signature parity with the other download methods; the resolve uses the client default. Do not change `_post`'s signature in this task.)
- [ ] **Step 4: Run → pass.**  **Step 5: Commit** `feat(sms-api-client): composite_resolve (SP-D1)`.

---

### Task 2: `resolve_composite_for_request` — local vs deployment dispatch
**Files:** Modify `vivarium_dashboard/lib/composite_resolve.py` (add the dispatcher); Modify `vivarium_dashboard/api/app.py` (the `/api/composite-resolve` route calls the dispatcher); Test `tests/test_composite_resolve_dispatch.py`.
**Interfaces — Consumes:** `SmsApiClient.composite_resolve` (Task 1), `run_core.run_target_for`, `remote_simulations._read_build_meta`. **Produces:** `resolve_composite_for_request(ws_root, spec_id, overrides=None) -> dict | None` — deployment target → sms-api; else local `resolve_composite`.

- [ ] **Step 1: Write the failing tests** (a `.viv-build.json` workspace → calls the faked client; a plain workspace → calls local `resolve_composite`):
```python
from pathlib import Path
from vivarium_dashboard.lib import composite_resolve as cr

def test_dispatch_local_when_no_viv_build(tmp_path, monkeypatch):
    called = {}
    monkeypatch.setattr(cr, "resolve_composite", lambda ws, sid, ov=None: called.setdefault("local", (sid, ov)) or {"name": "local"})
    out = cr.resolve_composite_for_request(tmp_path, "pkg.x", {"k": 1})
    assert out == {"name": "local"} and called["local"] == ("pkg.x", {"k": 1})

def test_dispatch_deployment_when_viv_build(tmp_path, monkeypatch):
    (tmp_path / ".viv-build.json").write_text('{"simulator_id": 66}')
    captured = {}
    class _FakeClient:
        def __init__(self, base=None): pass
        def composite_resolve(self, sid, ref, ov=None):
            captured.update(sid=sid, ref=ref, ov=ov); return {"name": "remote"}
    monkeypatch.setattr(cr, "SmsApiClient", _FakeClient)
    monkeypatch.setattr(cr, "_sms_api_base", lambda: "http://sms")
    out = cr.resolve_composite_for_request(tmp_path, "pkg.x", {"k": 2})
    assert out == {"name": "remote"}
    assert captured == {"sid": 66, "ref": "pkg.x", "ov": {"k": 2}}
```
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** — in `composite_resolve.py`, add the imports + dispatcher (the local `resolve_composite` stays as-is):
```python
from vivarium_dashboard.lib.run_core import run_target_for
from vivarium_dashboard.lib.remote_simulations import _read_build_meta
from vivarium_dashboard.lib.sms_api_client import SmsApiClient
from vivarium_dashboard.lib.workspace_deps_views import _sms_api_base


def resolve_composite_for_request(ws_root, spec_id, overrides=None):
    """Resolve a composite for a UI request, routing by source: a remote build
    (.viv-build.json) resolves on the deployment via sms-api; a local workspace
    resolves locally. Returns the resolve payload dict (or None on a local miss)."""
    ws_root = Path(ws_root)
    if run_target_for(ws_root) == "deployment":
        meta = _read_build_meta(ws_root) or {}
        sim_id = meta.get("simulator_id")
        if sim_id is None:
            return {"error": "remote build has no simulator_id stamp"}
        return SmsApiClient(_sms_api_base()).composite_resolve(int(sim_id), spec_id, overrides or {})
    return resolve_composite(ws_root, spec_id, overrides)
```
- [ ] **Step 4: Run → pass.**
- [ ] **Step 5: Wire the route** — in `app.py`, the `/api/composite-resolve` handler (line ~773) currently calls `resolve_composite(...)`; change it to `resolve_composite_for_request(...)` (same args). Run the existing composite-resolve route test (`grep -rl "composite-resolve" tests/`) to confirm the local path is unchanged.
- [ ] **Step 6: Commit** `feat(resolve): route remote-build composite-resolve to the deployment (SP-D1)`.

## Self-Review
**Spec coverage (headless dashboard slice):** Piece B — `SmsApiClient.composite_resolve` (Task 1) + the remote-build dispatch (Task 2). Piece A (sms-api endpoint) + the container exec + live E2E are the **in-the-loop** section below (out of this plan). **Placeholder scan:** none — every step has runnable code/commands. **Type consistency:** `composite_resolve(simulator_id, composite_ref, overrides, timeout=None) -> dict` is called by the dispatcher with `(int(sim_id), spec_id, overrides or {})`; `resolve_composite_for_request(ws_root, spec_id, overrides=None)` matches the route's existing `(ws, spec_id, overrides)` call.

## In-the-loop / follow-on (NOT in this headless plan)
- **sms-api `POST /core/v1/simulator/{id}/composite-resolve`** — add the route + a resolve runner that runs a short job from build `{id}`'s image (a resolve entrypoint: `build_core(composite_ref, overrides)` → JSON). The route + JSON passthrough are unit-testable with a **fake runner** (do this as its own small plan in the sms-api repo, against `simulation_service*`); the **real container execution** + the resolve entrypoint + **deploying the updated sms-api** + the **live E2E** (open a remote build's Explorer for a generator composite → resolves via the deployment) are **in-the-loop**, requiring a deployed sms-api + the tunnel.
- After deploy: validate end-to-end, then SP-D2 (composite **run** on the deployment).
