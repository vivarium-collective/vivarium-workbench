# bigraph-loom revamp + reintegration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make bigraph-loom the single composite configure/run/results interface — a run-first "Setup & Run" tab by default, wiring demoted near Document, a full post-run analysis flush, and downloadable results — and delete the duplicate SP-C widget + two-button cards.

**Architecture:** Three workstreams. **WS1** (backend, `vivarium-dashboard`) adds a generic post-run flush (default figure + analyses dispatch + report card) and a run-download endpoint. **WS2** (frontend, `bigraph-loom` React) merges Configure+Run into a "Setup & Run" tab, reorders/renames tabs, prettifies the form, and adds a Download button. **WS3** (glue, `vivarium-dashboard` templates/JS) removes the outer tab pair + SP-C widget, collapses card buttons to one, and gives the study-page pop-out live configure/run. WS1 and WS2 are independent; WS3 depends on both.

**Tech Stack:** Python 3 (stdlib `BaseHTTPRequestHandler` server, pytest), React + TypeScript + Vite (bigraph-loom), vanilla JS + Jinja2 templates (dashboard frontend).

## Global Constraints

- **Two repos, two branches.** Dashboard work on `feat/loom-setup-run-revamp` (exists, spec committed) in `/Users/eranagmon/code/vivarium-dashboard`. Loom work on a NEW branch `feat/setup-run-revamp` off loom `main` in `/Users/eranagmon/code/bigraph-loom`.
- **No new deps.** Reuse existing libs (`zipfile`/`io` for zip; existing `pbg_superpowers.visualizations` for figures). `bigraph-loom` adds no npm deps.
- **Loom build loop:** `cd /Users/eranagmon/code/bigraph-loom && npm run build` (`tsc -b && vite build`) writes the committed `bigraph_loom/_dist`; the editable install serves it immediately. No pip reinstall. Loom has no JS unit-test harness — the verification gate for loom tasks is `npm run build` succeeding (typecheck + bundle) plus a manual browser check.
- **Dashboard tests:** `pytest` spawns a real server subprocess via the `dashboard_client` fixture against `tests/_fixtures/`. Mutating endpoints need `_csrf_ok()`; tests set `VIVARIUM_DASHBOARD_DISABLE_CSRF=1`.
- **New GET endpoints** need BOTH a `startswith` branch in the `do_GET` dispatcher (`server.py` ~line 3170+) AND a `_<name>(self)` handler; keep the handler a thin shim over a `lib/` function.
- **Snapshot safety:** every frontend change must degrade in published (`window.__DASH_CONFIG__.mode === 'snapshot'`) mode — no live backend there.
- **Run dir convention:** `workspace_paths().pbg / "runs" / <run_id> /` holds `request.json`, the store, `viz.json`, `run.log`. The flush adds `analyses.json` and `report.html` here.

---

## WS1 — Backend: post-run flush + download (`vivarium-dashboard`)

### Task 1: Generic default visualization when a composite declares none

**Why:** multiscale-BATS composites declare no visualizations; without a default, the Visualizations tab is empty and the feature looks broken. Synthesize a default TimeSeriesPlot over all numeric observables, rendered through the SAME machinery as canonical viz.

**Files:**
- Modify: `vivarium_dashboard/lib/run_runner.py` (`_render_viz` ~line 118-168; add `_render_default_viz` after `_render_canonical_viz` ~line 267)
- Test: `tests/test_run_runner_default_viz.py` (create)

**Interfaces:**
- Produces: `_render_default_viz(*, db_file: str, run_id: str, core) -> dict[str, str]` returning `{"observables_over_time": "<html>"}` (or `{}` if nothing numeric to plot). Called from `_render_viz` only when both inline and canonical sources yielded an empty `viz_html`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_run_runner_default_viz.py
from vivarium_dashboard.lib import run_runner


def test_default_viz_synthesized_when_empty(monkeypatch):
    # When inline + canonical produce nothing, _render_viz falls back to the
    # default observables-over-time figure and writes a non-empty viz.json.
    monkeypatch.setattr(run_runner, "_render_default_viz",
                        lambda **kw: {"observables_over_time": "<div>FIG</div>"})
    monkeypatch.setattr(run_runner, "_render_canonical_viz", lambda **kw: {})

    import tempfile, json
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        run_dir = Path(d)
        run_runner._render_viz(
            composite=None, run_dir=run_dir,
            spec_id="x", db_file="db", run_id="r", core=object(),
        )
        viz = json.loads((run_dir / "viz.json").read_text())
    assert "observables_over_time" in viz
    assert "FIG" in viz["observables_over_time"]["html"] if isinstance(
        viz["observables_over_time"], dict) else "FIG" in viz["observables_over_time"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/eranagmon/code/vivarium-dashboard && VIVARIUM_DASHBOARD_DISABLE_CSRF=1 pytest tests/test_run_runner_default_viz.py -x -q`
Expected: FAIL — `_render_default_viz` not defined / default not wired.

- [ ] **Step 3: Implement**

Add `_render_default_viz` modeled on `_render_canonical_viz` (reuse `gather_emitter_outputs`, `build_viz_composite`, `TimeSeriesPlot`). Synthesize one viz_spec:

```python
def _render_default_viz(*, db_file: str, run_id: str, core) -> dict:
    """A default 'observables over time' figure for composites that declare
    no visualizations. Renders a TimeSeriesPlot over every numeric observable
    in this run's emitter output. Best-effort; returns {} on any failure."""
    try:
        from vivarium_dashboard.lib.investigations import (
            build_viz_composite, gather_emitter_outputs,
        )
        from pbg_superpowers.visualizations import TimeSeriesPlot
        from process_bigraph import Composite
        from pathlib import Path as _P
    except ImportError:
        return {}
    try:
        core.register_link(TimeSeriesPlot.__name__, TimeSeriesPlot)
    except Exception:
        pass
    registry = dict(core.link_registry)
    registry[TimeSeriesPlot.__name__] = TimeSeriesPlot

    gathered = gather_emitter_outputs(_P(db_file))
    by_sim_filtered = {}
    for sim_name, runs in (gathered.get("by_sim") or {}).items():
        keep = [r for r in runs if r.get("run_id") == run_id]
        if keep:
            by_sim_filtered[sim_name] = keep
    if not by_sim_filtered:
        return {}
    gathered_filtered = {"schemas": gathered.get("schemas") or {}, "by_sim": by_sim_filtered}

    viz_spec = {
        "name": "observables_over_time",
        "address": "local:TimeSeriesPlot",
        # TimeSeriesPlot with no explicit observable selection plots all
        # numeric leaves — verify against the installed class signature.
        "config": {"title": "Observables over time"},
    }
    try:
        doc = build_viz_composite(viz_spec, gathered_filtered, registry)
        vc = Composite({"state": doc}, core=core)
        vc.run(1)
        html = vc.state.get("output_store")
        if isinstance(html, dict):
            html = html.get("value") or html.get("_value") or ""
        return {"observables_over_time": html} if isinstance(html, str) and html else {}
    except Exception:
        traceback.print_exc()
        return {}
```

Then in `_render_viz`, after the canonical block and before writing `viz.json`, add:

```python
    # 3. Default figure when a composite declares no visualizations.
    if not viz_html and db_file and run_id and core is not None:
        try:
            for k, html in _render_default_viz(
                    db_file=db_file, run_id=run_id, core=core).items():
                viz_html.setdefault(k, html)
        except Exception:
            traceback.print_exc()
```

> Note: `viz_html` values from source 1 are payload dicts; canonical/default are HTML strings. Keep the existing mixed shape — the frontend already handles both (`{html}` or string). The test tolerates both.

- [ ] **Step 4: Run the test — expect PASS**

Run: `VIVARIUM_DASHBOARD_DISABLE_CSRF=1 pytest tests/test_run_runner_default_viz.py -x -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add vivarium_dashboard/lib/run_runner.py tests/test_run_runner_default_viz.py
git commit -m "feat(run): default observables-over-time figure when no viz declared"
```

---

### Task 2: Post-run analysis flush (analyses dispatch + report card)

**Files:**
- Create: `vivarium_dashboard/lib/composite_flush.py`
- Modify: `vivarium_dashboard/lib/run_runner.py` (`execute`, after the `_render_viz(...)` call ~line 353-357)
- Test: `tests/test_composite_flush.py` (create)

**Interfaces:**
- Produces: `run_flush(run_dir: Path, *, req, spec_id, db_file, run_id, core) -> dict` — writes `run_dir/analyses.json` (list of `{name, ...}`; `[]` when none declared) and `run_dir/report.html` (always). Returns `{"has_analyses": bool, "has_report": bool}`. Never raises; a flush failure is logged and returns `has_*` = False.
- Produces: `render_report_card(*, req, viz_names: list[str], analyses: list) -> str` — self-contained HTML string (params, steps, observable/figure counts). Standalone, NOT the investigation `ReportCard`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_composite_flush.py
import json
from pathlib import Path
from vivarium_dashboard.lib import composite_flush


class _Req:
    steps = 10
    run_id = "r1"
    spec_id = "multiscale_bats.composites.bats_fba.bats_fba"


def test_flush_writes_report_and_empty_analyses(tmp_path, monkeypatch):
    monkeypatch.setattr(composite_flush, "_dispatch_analyses", lambda **kw: [])
    out = composite_flush.run_flush(
        tmp_path, req=_Req(), spec_id=_Req.spec_id,
        db_file=str(tmp_path / "runs.db"), run_id="r1", core=object(),
    )
    assert out["has_report"] is True
    assert out["has_analyses"] is False
    assert json.loads((tmp_path / "analyses.json").read_text()) == []
    html = (tmp_path / "report.html").read_text()
    assert "bats_fba" in html and "10" in html


def test_flush_never_raises(tmp_path, monkeypatch):
    def _boom(**kw):
        raise RuntimeError("analysis exploded")
    monkeypatch.setattr(composite_flush, "_dispatch_analyses", _boom)
    out = composite_flush.run_flush(
        tmp_path, req=_Req(), spec_id=_Req.spec_id,
        db_file=str(tmp_path / "runs.db"), run_id="r1", core=object(),
    )
    assert out["has_analyses"] is False        # swallowed, not raised
    assert (tmp_path / "report.html").is_file()  # report still written
```

- [ ] **Step 2: Run test to verify it fails**

Run: `VIVARIUM_DASHBOARD_DISABLE_CSRF=1 pytest tests/test_composite_flush.py -x -q`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `composite_flush.py`**

```python
# vivarium_dashboard/lib/composite_flush.py
"""Generic post-run flush for composite runs: analyses + a report card.

Called by run_runner.execute after visualizations render. Best-effort:
never raises into the run loop; a failure is logged and reflected in the
returned has_* flags."""
from __future__ import annotations

import html as _html
import json
import traceback
from pathlib import Path


def _dispatch_analyses(*, spec_id: str, db_file: str, run_id: str, core) -> list:
    """Render @composite_generator(analyses=[...]) entries over this run's
    emitter output. Returns a list of {name, result} dicts; [] when the
    composite declares no analyses. Mirrors run_runner._render_canonical_viz."""
    try:
        from pbg_superpowers.composite_generator import _REGISTRY, discover_generators
    except ImportError:
        return []
    if not _REGISTRY:
        discover_generators()
    entry = _REGISTRY.get(spec_id)
    analyses = list(getattr(entry, "analyses", []) or []) if entry else []
    if not analyses:
        return []
    out = []
    for a in analyses:
        name = a.get("name") if isinstance(a, dict) else str(a)
        out.append({"name": name, "status": "declared"})
    # NOTE: rendering the analysis composites over gathered_emitter_outputs is
    # the richer follow-up; day-one dispatch records declarations so the UI can
    # list them. Expand here when composites declare real analyses.
    return out


def render_report_card(*, req, viz_names: list, analyses: list) -> str:
    steps = getattr(req, "steps", "?")
    spec_id = getattr(req, "spec_id", "") or ""
    name = spec_id.rsplit(".", 1)[-1] if spec_id else "composite"
    rows = "".join(
        f"<li><code>{_html.escape(str(n))}</code></li>" for n in viz_names
    ) or "<li><em>none</em></li>"
    an = "".join(
        f"<li>{_html.escape(str(a.get('name', a)))}</li>" for a in analyses
    ) or "<li><em>none</em></li>"
    return (
        "<!doctype html><meta charset='utf-8'>"
        "<div style='font-family:system-ui;max-width:720px;margin:24px auto'>"
        f"<h2>Run report — <code>{_html.escape(name)}</code></h2>"
        f"<p><strong>Composite:</strong> <code>{_html.escape(spec_id)}</code><br>"
        f"<strong>Steps:</strong> {_html.escape(str(steps))}</p>"
        f"<h3>Figures ({len(viz_names)})</h3><ul>{rows}</ul>"
        f"<h3>Analyses ({len(analyses)})</h3><ul>{an}</ul>"
        "</div>"
    )


def run_flush(run_dir: Path, *, req, spec_id: str, db_file: str,
              run_id: str, core) -> dict:
    run_dir = Path(run_dir)
    analyses: list = []
    has_analyses = False
    try:
        analyses = _dispatch_analyses(
            spec_id=spec_id, db_file=db_file, run_id=run_id, core=core)
        has_analyses = bool(analyses)
    except Exception:
        traceback.print_exc()
    try:
        (run_dir / "analyses.json").write_text(
            json.dumps(analyses, default=str), encoding="utf-8")
    except Exception:
        traceback.print_exc()

    # Report card — always attempt; read viz names from the already-written viz.json.
    viz_names: list = []
    try:
        vj = run_dir / "viz.json"
        if vj.is_file():
            viz_names = list(json.loads(vj.read_text()).keys())
    except Exception:
        pass
    has_report = False
    try:
        (run_dir / "report.html").write_text(
            render_report_card(req=req, viz_names=viz_names, analyses=analyses),
            encoding="utf-8")
        has_report = True
    except Exception:
        traceback.print_exc()
    return {"has_analyses": has_analyses, "has_report": has_report}
```

- [ ] **Step 4: Wire into `execute`** — after the `_render_viz(...)` call (run_runner.py ~line 357), before `cr.complete_metadata(...status="completed")`:

```python
        try:
            from vivarium_dashboard.lib.composite_flush import run_flush
            run_flush(run_dir, req=req, spec_id=req.spec_id,
                      db_file=req.db_file, run_id=req.run_id, core=core)
        except Exception:
            traceback.print_exc()   # flush must never fail the run
```

- [ ] **Step 5: Run tests — expect PASS**

Run: `VIVARIUM_DASHBOARD_DISABLE_CSRF=1 pytest tests/test_composite_flush.py -x -q`
Expected: PASS (both tests).

- [ ] **Step 6: Commit**

```bash
git add vivarium_dashboard/lib/composite_flush.py vivarium_dashboard/lib/run_runner.py tests/test_composite_flush.py
git commit -m "feat(run): post-run flush — analyses dispatch + report card"
```

---

### Task 3: Status payload advertises analyses/report/downloadable

**Files:**
- Modify: `vivarium_dashboard/lib/composite_run_views.py` (`build_composite_run_status`)
- Modify: `bigraph-loom` `src/api.ts` `RunStatus` interface (WS2 — done in Task 8; note the fields here)
- Test: `tests/test_composite_run_status_flush_flags.py` (create)

**Interfaces:**
- Produces: `build_composite_run_status` terminal (completed) payload gains `has_analyses: bool`, `has_report: bool`, `downloadable: bool` (`downloadable = status == "completed"`). Computed from the presence of `analyses.json` / `report.html` in the run dir.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_composite_run_status_flush_flags.py
from pathlib import Path
from vivarium_dashboard.lib import composite_run_views as crv


def test_status_reports_downloadable_and_flags(tmp_path, monkeypatch):
    # Simulate a completed run dir with report + analyses present.
    run_dir = tmp_path / ".pbg" / "runs" / "rX"
    run_dir.mkdir(parents=True)
    (run_dir / "report.html").write_text("<i>ok</i>")
    (run_dir / "analyses.json").write_text("[]")
    # Point the view at a completed run (stub the metadata read the function uses).
    body, code = crv.build_composite_run_status(str(tmp_path), "rX")
    # For a completed run the flags must be present and downloadable True.
    if body.get("status") == "completed":
        assert body["has_report"] is True
        assert body["downloadable"] is True
```

> If `build_composite_run_status` requires a real metadata row, add the minimal fixture the other `composite_run_views` tests use (see `tests/test_composite_runs.py` for the run-metadata helper) so `status == "completed"`.

- [ ] **Step 2: Run — expect FAIL** (`KeyError: has_report`).

Run: `VIVARIUM_DASHBOARD_DISABLE_CSRF=1 pytest tests/test_composite_run_status_flush_flags.py -x -q`

- [ ] **Step 3: Implement** — in `build_composite_run_status`, for the completed branch, add:

```python
    run_dir = WorkspacePaths.load(ws_root).pbg / "runs" / run_id
    completed = (status == "completed")
    body["has_analyses"] = (run_dir / "analyses.json").is_file() and \
        (run_dir / "analyses.json").read_text().strip() not in ("", "[]")
    body["has_report"] = (run_dir / "report.html").is_file()
    body["downloadable"] = completed
```

(Adapt `run_dir` resolution to the module's existing path helper — grep the file for how it already locates the run dir / `viz.json`.)

- [ ] **Step 4: Run — expect PASS.**

- [ ] **Step 5: Commit**

```bash
git add vivarium_dashboard/lib/composite_run_views.py tests/test_composite_run_status_flush_flags.py
git commit -m "feat(run): status advertises has_analyses/has_report/downloadable"
```

---

### Task 4: Download endpoint — zip the run dir

**Files:**
- Modify: `vivarium_dashboard/lib/composite_run_views.py` (add `build_composite_run_zip`)
- Modify: `vivarium_dashboard/server.py` (dispatcher branch ~line 3178; new `_get_composite_run_download` handler near `_get_composite_run_status` ~line 5204)
- Test: `tests/test_composite_run_download.py` (create)

**Interfaces:**
- Consumes: run dir at `WorkspacePaths.load(ws).pbg / "runs" / <run_id>` containing store + `viz.json` + `analyses.json` + `report.html`.
- Produces: `build_composite_run_zip(ws_root, run_id) -> tuple[bytes, str, int]` → `(zip_bytes, filename, http_status)`. `409` if run not terminal; `404` if run dir missing.
- Produces route: `GET /api/composite-run/<run_id>/download` → `application/zip`, attachment `run_<id>.zip`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_composite_run_download.py
import io, zipfile
from pathlib import Path
from vivarium_dashboard.lib import composite_run_views as crv


def test_zip_contains_run_artifacts(tmp_path, monkeypatch):
    run_dir = tmp_path / ".pbg" / "runs" / "rZ"
    run_dir.mkdir(parents=True)
    (run_dir / "report.html").write_text("<i>r</i>")
    (run_dir / "viz.json").write_text("{}")
    (run_dir / "analyses.json").write_text("[]")
    (run_dir / "store.parquet").write_bytes(b"PAR1data")
    # Force "terminal" so the zip is allowed.
    monkeypatch.setattr(crv, "_run_is_terminal", lambda ws, rid: True, raising=False)
    data, fname, code = crv.build_composite_run_zip(str(tmp_path), "rZ")
    assert code == 200
    assert fname == "run_rZ.zip"
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert "report.html" in names and "store.parquet" in names
```

- [ ] **Step 2: Run — expect FAIL** (function missing).

- [ ] **Step 3: Implement `build_composite_run_zip`** (mirror `analysis_outputs.build_analysis_outputs_zip`):

```python
def build_composite_run_zip(ws_root, run_id: str):
    import io, zipfile
    from vivarium_dashboard.lib.workspace_paths import WorkspacePaths
    run_dir = WorkspacePaths.load(ws_root).pbg / "runs" / run_id
    if not run_dir.is_dir():
        return b"", f"run_{run_id}.zip", 404
    if not _run_is_terminal(ws_root, run_id):
        return b"", f"run_{run_id}.zip", 409
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for src in sorted(run_dir.rglob("*")):
            if src.is_file() and src.name != "request.json":
                zf.write(src, str(src.relative_to(run_dir)))
    return buf.getvalue(), f"run_{run_id}.zip", 200
```

Add a `_run_is_terminal(ws_root, run_id) -> bool` helper (read run metadata status ∈ {completed, failed, orphaned}) if one doesn't already exist in the module.

- [ ] **Step 4: Add the route.** In `server.py` `do_GET`, BEFORE the `/status` branch (line 3180) add:

```python
        if self.path.startswith("/api/composite-run/") and self.path.split("?", 1)[0].endswith("/download"):
            return self._get_composite_run_download()
```

Add the handler near `_get_composite_run_status`:

```python
    def _get_composite_run_download(self):
        """GET /api/composite-run/<run_id>/download — zip of the run dir."""
        _ws_add_to_sys_path()
        from vivarium_dashboard.lib.composite_run_views import build_composite_run_zip
        path_only = self.path.split("?", 1)[0]
        rest = path_only[len("/api/composite-run/"):]
        run_id = rest[: -len("/download")]
        data, fname, code = build_composite_run_zip(WORKSPACE, run_id)
        if code != 200:
            return self._json({"error": f"run not downloadable ({code})"}, code)
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
```

- [ ] **Step 5: Add an HTTP-level test** through the `dashboard_client` fixture (mirror an existing `test_composite_runs.py` run, then GET `/api/composite-run/<id>/download`, assert 200 + `application/zip`). Run:

`VIVARIUM_DASHBOARD_DISABLE_CSRF=1 pytest tests/test_composite_run_download.py -x -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add vivarium_dashboard/lib/composite_run_views.py vivarium_dashboard/server.py tests/test_composite_run_download.py
git commit -m "feat(run): GET /api/composite-run/<id>/download — results bundle zip"
```

---

## WS2 — Loom UI revamp (`bigraph-loom`)

> First: `cd /Users/eranagmon/code/bigraph-loom && git checkout main && git pull && git checkout -b feat/setup-run-revamp`.

### Task 5: Tab model — rename `view`→`wiring`, add `setup`, reorder, default `setup`

**Files:**
- Modify: `src/App.tsx` (tab type/list ~line 548-550; default ~line 83; view-block guard ~line 606-608; render blocks ~line 713-746)
- Grep for `TabId` definition (likely `src/types` or top of `App.tsx`) and update.

**Interfaces:**
- Produces: `type TabId = 'setup' | 'results' | 'visualizations' | 'wiring' | 'document'`; default tab `'setup'`; `STATIC` → `['wiring']`.

- [ ] **Step 1:** Update the `TabId` union (grep `TabId` — add `'setup'`, rename `'view'`→`'wiring'`, drop `'configure'`/`'run'`). 

- [ ] **Step 2:** `App.tsx` line 83: `const [tab, setTab] = useState<TabId>('setup');`

- [ ] **Step 3:** `App.tsx` lines 548-550:

```tsx
  const tabs: TabId[] = STATIC
    ? ['wiring']
    : ['setup', 'results', 'visualizations', 'wiring', 'document'];
```

- [ ] **Step 4:** The wiring canvas block (line 606): change `display: tab === 'view' ? 'flex' : 'none'` → `tab === 'wiring'`. Keep it always-mounted (the ReactFlow-state comment still applies).

- [ ] **Step 5:** Remove the `{tab === 'configure' && (...)}` and `{tab === 'run' && (...)}` blocks (lines 713-731) — replaced by Setup & Run in Task 6. Leave `results`/`visualizations`/`document` blocks intact. (App will not typecheck until Task 6 adds the `setup` block — that's expected; do Tasks 5+6 as one commit.)

- [ ] **Step 6:** Build gate — run after Task 6 (they land together).

---

### Task 6: `SetupRunPanel` — merge Configure + Run, auto-switch to Results on completion

**Files:**
- Create: `src/panels/SetupRunPanel.tsx`
- Modify: `src/App.tsx` (import; add `{tab === 'setup' && (...)}` block; pass an `onCompleted` that flips the tab)
- Delete: `src/panels/ConfigurePanel.tsx`, `src/panels/RunPanel.tsx` (their logic moves into SetupRunPanel)

**Interfaces:**
- Consumes: `parameters`, `overrides`, `compositeId`, `emitSet`, `runContext`, `defaultSteps`, `handleApplied` (existing App state/handlers), plus new `onCompleted: () => void`.
- Produces: `SetupRunPanel` combining the parameter form (from ConfigurePanel) and the run controls/polling (from RunPanel) in one scroll. On terminal `completed`, calls `props.onCompleted()` (App sets `tab='results'`) and still fires `postRunComplete`.

- [ ] **Step 1:** Create `SetupRunPanel.tsx` — lift the form rendering from `ConfigurePanel` (the `paramKeys.map` block + cast helpers) and the run lifecycle from `RunPanel` (steps state, `handleRun`, `beginPolling`, progress bar, error/completion UI). Structure top-to-bottom: **Parameters** (card section) → **Steps + Run** (sticky action bar) → progress/errors. On completion (`s.status === 'completed'`) call `onCompletedRef.current?.()` right after `postRunComplete`. Reuse `startRun/fetchRunStatus/fetchRunTrajectory/postRunComplete` from `../api` and `parseListString/formatListString` from `../parsers`. Apply-on-run: cast form values to overrides and pass them straight into `startRun({overrides})` (no separate Apply step required — Run applies), while still calling `/api/composite-resolve` to refresh the Wiring view if the user wants a preview (optional "Preview wiring" secondary button).

- [ ] **Step 2:** In `App.tsx`, replace the removed configure/run blocks with:

```tsx
          {tab === 'setup' && (
            <SetupRunPanel
              compositeId={compositeId}
              parameters={parameters}
              overrides={overrides}
              emitSet={emitSet}
              runContext={runContext}
              defaultSteps={defaultSteps}
              onApplied={handleApplied}
              onTrajectory={setTrajectory}
              onVizHtml={setVizHtml}
              onCompleted={() => setTab('results')}
            />
          )}
```

Add `import { SetupRunPanel } from './panels/SetupRunPanel';` and remove the `ConfigurePanel`/`RunPanel` imports.

- [ ] **Step 3:** Delete `ConfigurePanel.tsx` and `RunPanel.tsx`.

- [ ] **Step 4: Build gate**

Run: `cd /Users/eranagmon/code/bigraph-loom && npm run build`
Expected: `tsc -b` passes (no references to deleted panels / `'view'`/`'configure'`/`'run'`), `vite build` writes `bigraph_loom/_dist`.

- [ ] **Step 5: Manual check** — restart the multiscale-BATS dashboard (`python -m pbg_superpowers.dashboard restart` from the workspace, or the existing server auto-serves `_dist`), open `/bigraph-loom/index.html?id=multiscale_bats.composites.bats_fba.bats_fba`: default tab is **Setup & Run**, order is Setup&Run/Results/Visualizations/Wiring/Document, Run works, completion jumps to Results.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(loom): merged Setup & Run tab; wiring demoted; setup is default"
```

---

### Task 7: Aesthetic pass on the Setup & Run form

**Files:**
- Modify: `src/App.css` (add form/section classes)
- Modify: `src/panels/SetupRunPanel.tsx` (apply classes)

- [ ] **Step 1:** Add scoped classes to `App.css` — `.sr-panel`, `.sr-section` (card: white bg, `1px solid #e5e7eb`, radius 8, padding 16, subtle shadow), `.sr-field` (label + control spacing), `.sr-input`, `.sr-actionbar` (sticky bottom, run CTA), `.sr-run-btn` (indigo `#6366f1` primary). Keep the palette.

- [ ] **Step 2:** Apply the classes in `SetupRunPanel.tsx`, replacing the heaviest inline styles. Group parameters under a "Parameters" card and steps+Run under a sticky ".sr-actionbar".

- [ ] **Step 3: Build + manual check** — `npm run build`; reload; confirm the form reads as grouped cards with a clear primary Run.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "style(loom): card-styled Setup & Run form + sticky run bar"
```

---

### Task 8: Download button in Results + api helper + status fields

**Files:**
- Modify: `src/api.ts` (`RunStatus` gains `has_analyses?`, `has_report?`, `downloadable?`; add `runDownloadUrl(runId)`)
- Modify: `src/panels/ResultsPanel.tsx` (props gain `runId`, `downloadable`; render Download button)
- Modify: `src/App.tsx` (track latest `runId` + status flags lifted from SetupRunPanel; pass to ResultsPanel)

**Interfaces:**
- Consumes: `/api/composite-run/<id>/download` (WS1 Task 4), `RunStatus.downloadable` (WS1 Task 3).
- Produces: `runDownloadUrl(runId: string): string` → `/api/composite-run/${runId}/download`.

- [ ] **Step 1:** `api.ts` — extend `RunStatus`:

```ts
export interface RunStatus {
  run_id: string;
  status: RunStatusValue;
  progress_step: number;
  n_steps: number | null;
  heartbeat_at: number | null;
  error?: string;
  log_path?: string;
  viz_html?: Record<string, { html: string }>;
  has_analyses?: boolean;
  has_report?: boolean;
  downloadable?: boolean;
}

export function runDownloadUrl(runId: string): string {
  return `/api/composite-run/${runId}/download`;
}
```

- [ ] **Step 2:** Lift `runId` + `downloadable` to App: have `SetupRunPanel` report them up via a new `onRunState?: (s: {runId: string|null; downloadable: boolean}) => void` prop; App stores them and passes to `ResultsPanel`.

- [ ] **Step 3:** `ResultsPanel` — add props `runId?: string|null`, `downloadable?: boolean`; render a Download button when `downloadable && runId`:

```tsx
{downloadable && runId && (
  <a href={runDownloadUrl(runId)} download
     style={{ display: 'inline-block', margin: '4px 0 12px', padding: '6px 14px',
              fontSize: 13, fontWeight: 600, background: '#6366f1', color: '#fff',
              borderRadius: 6, textDecoration: 'none' }}>
    ⬇ Download results
  </a>
)}
```

- [ ] **Step 4: Build + manual check** — `npm run build`; run `bats_fba`; on completion the Results tab shows Download; clicking downloads `run_<id>.zip` containing store + figures + `report.html` + `analyses.json`.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(loom): download results bundle from the Results tab"
```

---

## WS3 — Reintegration (`vivarium-dashboard` templates/JS)

> Depends on WS2 built into `bigraph_loom/_dist` and WS1 endpoints live. Back on `feat/loom-setup-run-revamp`.

### Task 9: Composite-explore page — drop outer tabs + SP-C panel; keep only loom

**Files:**
- Modify: `vivarium_dashboard/templates/index.html.j2` (lines ~1313-1340 outer tabs + panels; ~1695-1717 `_ceShowPanel` + SP-C mount)
- Modify: `vivarium_dashboard/static/walkthrough.js` (`_ceScrollToConfigure` references)

- [ ] **Step 1:** In `index.html.j2` remove the `<nav class="ce-page-tabs">` block and the `<div class="ce-page-panel" data-cepanel="configure">` (with `#ce-configure-run`). Keep the loom iframe (`#composite-explore-frame`, `src="/bigraph-loom/index.html"`) and its "Pop out" toolbar, un-wrapped by the removed panel divs.

- [ ] **Step 2:** Remove the `_ceShowPanel` function (~1695-1717) and the SP-C `ConfigureRun.mount(...)` init call. Remove `_ceScrollToConfigure` reads.

- [ ] **Step 3:** The loom iframe now defaults to Setup & Run by itself (WS2). Confirm the page passes `?id=<composite>` to the iframe (it already does via the explorer bootstrap — grep `iframe.src = loomUrl` in walkthrough.js ~4347; ensure the URL carries `?id=` and NOT `?static=1`).

- [ ] **Step 4: Manual check** — open the Composites → a composite → the explorer page shows ONLY the loom viewer, defaulting to Setup & Run. No outer "Wiring viewer / Configure & Run" tabs.

- [ ] **Step 5: Commit**

```bash
git add vivarium_dashboard/templates/index.html.j2 vivarium_dashboard/static/walkthrough.js
git commit -m "refactor(ui): composite-explore shows only bigraph-loom (drop SP-C outer tabs)"
```

---

### Task 10: Single button on composite cards; retire SP-C widget

**Files:**
- Modify: `vivarium_dashboard/static/walkthrough.js` (card `exploreBtn` in grid ~2064-2107 and list ~2051-2054; delete `_openCompositeConfigureRun` ~3743-3748)
- Delete: `vivarium_dashboard/static/configure-run.js`
- Modify: `index.html.j2` / template head — remove the `<script src=".../configure-run.js">` include.

- [ ] **Step 1:** Replace both `exploreBtn` definitions with a single button:

```js
var exploreBtn = (_isSnapshot && !c.has_wiring)
  ? ''
  : '<button class="action-btn" onclick="_openCompositeExplorer(\'' + _esc(c.id) + '\')">Explore</button>';
```

- [ ] **Step 2:** Delete `_openCompositeConfigureRun` and any `window._openCompositeConfigureRun` / `_ceScrollToConfigure` assignment.

- [ ] **Step 3:** Delete `static/configure-run.js` and its `<script>` include (grep the templates for `configure-run.js`).

- [ ] **Step 4:** Repeat the single-button change for the investigation-embedded composite cards if they duplicate the two-button pattern (grep `_openCompositeConfigureRun`).

- [ ] **Step 5: Manual check** — composite cards show one **Explore** button that lands on Setup & Run. No console errors from the removed script.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor(ui): one Explore button per composite card; remove SP-C widget"
```

---

### Task 11: Study-page pop-out — live Setup & Run, snapshot stays read-only

**Files:**
- Modify: `vivarium_dashboard/static/study-detail.js` (`_openCompositeLoom` ~line 367-380)

- [ ] **Step 1:** Rewrite `_openCompositeLoom` so live mode opens the full viewer (no `static=1`) and snapshot keeps read-only:

```js
  function _openCompositeLoom(composite) {
    if (!composite) return;
    var cfg = (typeof window !== 'undefined' && window.__DASH_CONFIG__) || {};
    var isSnap = cfg.mode === 'snapshot';
    var origin = (typeof location !== 'undefined' && location.origin
                  && /^https?:/.test(location.origin)) ? location.origin : '';
    var base = origin + (isSnap ? (cfg.basePath || '') : '');
    var u;
    if (isSnap) {
      // Published bundle: no live backend → read-only wiring from a static snapshot.
      var stateUrl = base + '/api/composite-state/' + encodeURIComponent(composite) + '.json';
      u = base + '/bigraph-loom/index.html?static=1&stateUrl=' + encodeURIComponent(stateUrl);
    } else {
      // Live dashboard: full Setup & Run (loom self-hydrates via ?id= → /api/composite-state?ref=).
      u = base + '/bigraph-loom/index.html?id=' + encodeURIComponent(composite);
    }
    window.open(u, 'loom', 'width=1200,height=840');
  }
```

- [ ] **Step 2:** Update the button copy in `study-detail.html` (~line 700) from "explore in bigraph-loom ↗" to "configure & run ↗" (live intent). Keep the emoji.

- [ ] **Step 3: Manual check (live)** — Study → Model → the composite button opens loom on **Setup & Run** with working configure/run. **Snapshot check:** a published bundle still opens read-only Wiring only.

- [ ] **Step 4: Commit**

```bash
git add vivarium_dashboard/static/study-detail.js vivarium_dashboard/templates/study-detail.html
git commit -m "feat(ui): study-page pop-out opens Setup & Run in live mode"
```

---

### Task 12: End-to-end integration verification (multiscale-BATS)

**Files:** none (verification only).

- [ ] **Step 1:** Ensure loom is built (`cd /Users/eranagmon/code/bigraph-loom && npm run build`) and the dashboard serves the branch. Restart the multiscale-BATS dashboard.

- [ ] **Step 2:** Composites page → `bats_fba` card → single **Explore** → lands on Setup & Run → set a small `n_days`, Run → completion auto-switches to Results → Visualizations shows the default observables-over-time figure → **Download results** yields `run_<id>.zip` containing the store, `viz.json`/figures, `analyses.json`, `report.html`.

- [ ] **Step 3:** Study page (`bats-fba`) → Model → composite button → Setup & Run pop-out runs.

- [ ] **Step 4:** Wiring tab still renders the diagram (position 4). Document tab intact.

- [ ] **Step 5:** Run the dashboard test suite: `VIVARIUM_DASHBOARD_DISABLE_CSRF=1 pytest -q`. Expected: green (new tests + no regressions).

- [ ] **Step 6:** Record results in the branch (no code commit needed) and proceed to `finishing-a-development-branch` for the two PRs (loom first, then dashboard).

---

## Self-Review

**Spec coverage:**
- One viewer / one entry point → Tasks 9, 10 ✓
- Run-first, Setup & Run default, wiring demoted → Tasks 5, 6 ✓
- Prettier configure panel → Task 7 ✓
- Full post-run flush (analyses + viz + report) → Tasks 1, 2, 3 ✓
- Downloadable results → Tasks 4, 8 ✓
- Study-page parity (live vs snapshot) → Task 11 ✓
- Retire SP-C + two-button cards → Tasks 9, 10 ✓
- Generic default figure (empty-output risk) → Task 1 ✓

**Placeholder scan:** Task 1's default-viz `viz_spec` and Task 3's run-dir resolution are flagged as "verify against installed API" — acceptable because the exact `TimeSeriesPlot` config shape and the module's existing run-dir helper must be read at implementation time; both name the concrete pattern to copy (`_render_canonical_viz`, `analysis_outputs.build_analysis_outputs_zip`). No TODO/TBD left.

**Type consistency:** `RunStatus` fields (`has_analyses`/`has_report`/`downloadable`) defined in WS1 Task 3 (Python) and mirrored in WS2 Task 8 (`api.ts`). `runDownloadUrl` matches the route added in Task 4. `TabId` union (Task 5) drives the render blocks (Task 6). `onCompleted`/`onRunState` props consistent between SetupRunPanel and App.

**Notes / risks carried from spec:** flush is synchronous inside the detached run process (a slow analysis delays the "completed" mark — acceptable, analyses are declaration-only on day one); richer per-composite analyses are a follow-up; merge order is loom → dashboard.
