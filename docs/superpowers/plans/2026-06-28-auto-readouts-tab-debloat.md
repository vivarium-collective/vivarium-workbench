# Auto-covered Readouts + investigation-tab de-bloat — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto-list the study Readouts table from the composite's emit plan (name + store_path always present), lint authored store_paths against emit-plan validity, and remove manual/destructive UI bloat across the Readouts, Visualizations, and Runs tabs.

**Architecture:** A new typed FastAPI worker (`lib/readouts_views.build_study_readouts` + `GET /api/study-readouts`) merges the composite's emit-plan leaves with authored `study.yaml readouts:` annotations and returns a typed payload; `static/study-detail.js` fetches it and renders the table async (the ~3s composite build is TTL-cached, same pattern as today's validation badges). A dashboard-side lint pass (`report_views._readout_emit_plan_findings`) turns missing/invalid store_paths into readiness gaps. Pure-removal tasks delete the "+ Add observable", "Registered visualization modules", and "Compare/Clear runs" affordances.

**Tech Stack:** Python 3, FastAPI + pydantic (dashboard `lib/models.py` convention), Jinja2 templates, vanilla JS (`static/study-detail.js`), pytest. Emit-plan engine: `pbg_superpowers.readout_validation.available_observables` (via `process_bigraph.emitter.collect_input_ports`).

## Global Constraints

- Dashboard is served by the FastAPI app under uvicorn (`cli.py:122`); the legacy `server.serve` path is retired. **All new server work lands on the FastAPI seam — do not revive `server.serve`.**
- Do **not** reinstall `-e` from this worktree over the canonical `main` dashboard install (memory `reference_dashboard_editable_install_from_main_only`). To test live, restart the dashboard pointed at this worktree; never leave the global install on a feature branch.
- New pydantic payload models follow `lib/models.py`: `from pydantic import BaseModel, ConfigDict, Field`; each model documents the worker it mirrors.
- Lint findings keep the existing shape consumed by `report_views.build_report_lint`: `{study, check, severity, message, field_path}`.
- Emit `store_path`s are dotted (`listeners.mass.instantaneous_growth_rate`); the whole-cell lineage nests under `agents.<n>.` — strip a leading `agents.<n>.` only when matching authored bare paths.
- Worktree: `/Users/eranagmon/code/vdash-readouts` (branch `feat/auto-readouts-tab-debloat`, off `origin/main`). Run pytest from there.
- Test command base: `cd /Users/eranagmon/code/vdash-readouts && python -m pytest`.

---

### Task 1: Typed payload models — `ReadoutRow` + `StudyReadouts`

**Files:**
- Modify: `vivarium_dashboard/lib/models.py` (add classes near `StudyObservableCheck`, ~line 972)
- Test: `tests/test_readouts_models.py` (create)

**Interfaces:**
- Produces: `ReadoutRow(store_path:str, name:str, description:str="", units:str="", index_by:Optional[dict]=None, notes:str="", annotated:bool, emit_status:Literal["emitted","not_in_emit_plan","derived"])`; `StudyReadouts(composite:str, rows:list[ReadoutRow], note:str="")`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_readouts_models.py
from vivarium_dashboard.lib.models import ReadoutRow, StudyReadouts


def test_readout_row_defaults_and_dump():
    r = ReadoutRow(store_path="listeners.mass.cell_mass", name="cell_mass",
                   annotated=True, emit_status="emitted")
    d = r.model_dump()
    assert d["store_path"] == "listeners.mass.cell_mass"
    assert d["name"] == "cell_mass"
    assert d["description"] == "" and d["units"] == "" and d["notes"] == ""
    assert d["index_by"] is None
    assert d["annotated"] is True
    assert d["emit_status"] == "emitted"


def test_study_readouts_wraps_rows():
    sr = StudyReadouts(composite="ecoli", rows=[
        ReadoutRow(store_path="a.b", name="b", annotated=False, emit_status="emitted"),
    ])
    payload = sr.model_dump()
    assert payload["composite"] == "ecoli"
    assert payload["note"] == ""
    assert payload["rows"][0]["name"] == "b"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_readouts_models.py -v`
Expected: FAIL with `ImportError: cannot import name 'ReadoutRow'`.

- [ ] **Step 3: Add the models**

In `vivarium_dashboard/lib/models.py`, after the `StudyObservableCheck` class (~line 972), add:

```python
class ReadoutRow(BaseModel):
    """One row of ``GET /api/study-readouts`` (lib.readouts_views.build_study_readouts).

    A merged view of one emit-plan leaf and any authored ``study.yaml`` readout
    annotation. ``emit_status``: ``emitted`` (a real emitter path) / ``derived``
    (authored derived-needed|aspirational metric, exempt from the emit-plan
    check) / ``not_in_emit_plan`` (authored ``available`` readout whose
    ``store_path`` is missing or not an emitted leaf — the never-fabricate flag).
    """

    store_path: str
    name: str
    description: str = ""
    units: str = ""
    index_by: Optional[dict] = None
    notes: str = ""
    annotated: bool
    emit_status: Literal["emitted", "not_in_emit_plan", "derived"]


class StudyReadouts(BaseModel):
    """``GET /api/study-readouts?study=<slug>`` payload.

    Backed by ``lib.readouts_views.build_study_readouts``. ``rows`` is the union
    of the composite's emit-plan leaves and authored readouts; ``note`` carries a
    human explanation when the composite could not be built (rows then come from
    authored readouts only, unverified).
    """

    composite: str
    rows: list[ReadoutRow]
    note: str = ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_readouts_models.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add vivarium_dashboard/lib/models.py tests/test_readouts_models.py
git commit -m "feat(models): typed ReadoutRow + StudyReadouts payloads"
```

---

### Task 2: Merge worker — `_merge_readouts` + `build_study_readouts`

**Files:**
- Create: `vivarium_dashboard/lib/readouts_views.py`
- Test: `tests/test_readouts_views.py` (create)

**Interfaces:**
- Consumes: `observables_views.build_composite_state_for_observables`, `pbg_superpowers.readout_validation.available_observables`; reuses `observables_views._OBS_CACHE` pattern (own cache here).
- Produces: `_merge_readouts(spec: dict, available: dict) -> list[dict]` (pure, no build); `build_study_readouts(ws_root: Path, slug: str) -> tuple[dict, int]` (payload dict + status).
- The pure `_merge_readouts` is the unit-test seam (inject `available={"leaves": [...]}`), mirroring `validate_readouts(..., available=...)`.

- [ ] **Step 1: Write the failing test (pure merge)**

```python
# tests/test_readouts_views.py
from vivarium_dashboard.lib.readouts_views import _merge_readouts


AVAIL = {"leaves": [
    "agents.0.listeners.mass.instantaneous_growth_rate",
    "agents.0.listeners.mass.cell_mass",
]}


def _row_by_path(rows, path):
    return next(r for r in rows if r["store_path"] == path)


def test_emit_leaves_become_rows_with_short_names():
    rows = _merge_readouts({"readouts": []}, AVAIL)
    paths = {r["store_path"] for r in rows}
    assert "agents.0.listeners.mass.cell_mass" in paths
    r = _row_by_path(rows, "agents.0.listeners.mass.cell_mass")
    assert r["name"] == "cell_mass"
    assert r["emit_status"] == "emitted"
    assert r["annotated"] is False


def test_authored_annotation_matches_by_lineage_stripped_path():
    spec = {"readouts": [{
        "name": "instantaneous_growth_rate", "status": "available",
        "store_path": "listeners.mass.instantaneous_growth_rate",
        "description": "the screen metric", "units": "1/s",
    }]}
    rows = _merge_readouts(spec, AVAIL)
    r = _row_by_path(rows, "agents.0.listeners.mass.instantaneous_growth_rate")
    assert r["name"] == "instantaneous_growth_rate"
    assert r["annotated"] is True
    assert r["description"] == "the screen metric"
    assert r["units"] == "1/s"
    assert r["emit_status"] == "emitted"
    # no duplicate raw row for the same leaf
    assert sum(1 for x in rows
               if x["store_path"].endswith("instantaneous_growth_rate")) == 1


def test_authored_available_not_in_plan_is_orphan():
    spec = {"readouts": [{
        "name": "phantom", "status": "available",
        "store_path": "listeners.does_not_exist",
    }]}
    rows = _merge_readouts(spec, AVAIL)
    r = _row_by_path(rows, "listeners.does_not_exist")
    assert r["emit_status"] == "not_in_emit_plan"
    assert r["annotated"] is True


def test_derived_metric_without_store_path_is_exempt():
    spec = {"readouts": [{
        "name": "effective_knob_count", "status": "derived-needed",
        "notes": "computed analysis scalar",
    }]}
    rows = _merge_readouts(spec, AVAIL)
    r = next(r for r in rows if r["name"] == "effective_knob_count")
    assert r["emit_status"] == "derived"
    assert r["store_path"] == ""
    assert r["annotated"] is True


def test_available_authored_without_store_path_flagged():
    spec = {"readouts": [{"name": "needs_path", "status": "available"}]}
    rows = _merge_readouts(spec, AVAIL)
    r = next(r for r in rows if r["name"] == "needs_path")
    assert r["emit_status"] == "not_in_emit_plan"
    assert r["store_path"] == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_readouts_views.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vivarium_dashboard.lib.readouts_views'`.

- [ ] **Step 3: Implement the module**

Create `vivarium_dashboard/lib/readouts_views.py`:

```python
"""``GET /api/study-readouts`` worker — auto-lists a study's Readouts table from
the composite emit plan, overlaying authored ``study.yaml readouts:`` annotations.

The table is the union of (a) every emitter leaf path the composite exposes
(``available_observables(...).leaves`` — the paths the run actually saves) and
(b) authored readouts. An authored readout overlays its name/description/units/
notes onto the matching leaf (matched after stripping a leading ``agents.<n>.``).
Authored ``available`` readouts whose ``store_path`` is missing or not an emitted
leaf surface as ``not_in_emit_plan`` (never-fabricate); ``derived-needed`` /
``aspirational`` readouts (computed metrics, not raw emit paths) surface as
``derived`` and are exempt from that check.
"""

from __future__ import annotations

import re
import time as _time
from pathlib import Path
from typing import Any

import yaml

from . import active_workspace as _aw
from .observables_views import build_composite_state_for_observables

_READOUTS_CACHE: dict = {}
_READOUTS_CACHE_TTL_S = 300.0

_LINEAGE_RE = re.compile(r"^agents\.\d+\.")
_GENERIC_LEAF = {"count", "id", "value"}


def clear_cache() -> None:
    _READOUTS_CACHE.clear()


def _strip_lineage(path: str) -> str:
    """Strip a leading ``agents.<n>.`` so authored bare paths match emit leaves."""
    return _LINEAGE_RE.sub("", path or "")


def _short_name(leaf: str) -> str:
    """Readable default name from a dotted leaf path."""
    segs = [s for s in (leaf or "").split(".") if s]
    if not segs:
        return leaf or ""
    last = segs[-1]
    if last in _GENERIC_LEAF and len(segs) >= 2:
        return f"{segs[-2]}_{last}"
    return last


def _merge_readouts(spec: dict, available: dict) -> list[dict]:
    """Pure merge of emit-plan leaves + authored readouts → ordered row dicts.

    Headless-friendly (no composite build): pass ``available={"leaves": [...]}``.
    """
    leaves = list(available.get("leaves") or [])
    # Index authored readouts by lineage-stripped store_path for overlay match.
    authored = [r for r in (spec.get("readouts") or []) if isinstance(r, dict)]
    overlay: dict[str, dict] = {}
    for r in authored:
        sp = r.get("store_path")
        if isinstance(sp, str) and sp.strip():
            overlay[_strip_lineage(sp.strip())] = r

    rows: list[dict] = []
    matched_ids: set[int] = set()

    for leaf in sorted(leaves):
        key = _strip_lineage(leaf)
        ann = overlay.get(key)
        if ann is not None:
            matched_ids.add(id(ann))
        rows.append({
            "store_path": leaf,
            "name": (ann or {}).get("name") or _short_name(leaf),
            "description": (ann or {}).get("description", "") or "",
            "units": (ann or {}).get("units", "") or "",
            "index_by": (ann or {}).get("index_by"),
            "notes": (ann or {}).get("notes", "") or "",
            "annotated": ann is not None,
            "emit_status": "emitted",
        })

    # Authored readouts that did not match any emit leaf.
    for r in authored:
        if id(r) in matched_ids:
            continue
        status = (r.get("status") or "").strip()
        derived = status in ("derived-needed", "aspirational")
        rows.append({
            "store_path": (r.get("store_path") or "") if not derived else "",
            "name": r.get("name") or "readout",
            "description": r.get("description", "") or "",
            "units": r.get("units", "") or "",
            "index_by": r.get("index_by"),
            "notes": r.get("notes", "") or "",
            "annotated": True,
            "emit_status": "derived" if derived else "not_in_emit_plan",
        })

    return rows


def build_study_readouts(ws_root: Path, slug: str) -> tuple[dict, int]:
    """Worker for ``GET /api/study-readouts?study=<slug>`` → ``(payload, status)``.

    200 with ``{composite, rows, note}``. Resolution/ref errors → 4xx. If the
    composite cannot build, returns 422 with authored-only rows + an explanatory
    ``note`` (never a 500).
    """
    from .study_spec import SLUG_RE, study_spec_file
    from .spec_migration import migrate_v2_to_v3

    ws_root = Path(ws_root)
    if not SLUG_RE.match(slug or ""):
        return {"error": "invalid slug"}, 400

    study_dir = ws_root / "studies" / slug
    if not study_dir.is_dir():
        study_dir = ws_root / "investigations" / slug
    sf = study_spec_file(study_dir)
    if not sf.is_file():
        return {"error": f"study not found: {slug}"}, 404
    try:
        spec = migrate_v2_to_v3(yaml.safe_load(sf.read_text(encoding="utf-8")) or {})
    except Exception as e:  # noqa: BLE001
        return {"error": f"study spec parse failed: {e}"}, 400

    baseline = spec.get("baseline") or []
    if not (isinstance(baseline, list) and baseline and isinstance(baseline[0], dict)):
        return {"error": "study has no baseline composite", "rows": []}, 422
    ref = baseline[0].get("composite")
    if not ref:
        return {"error": "baseline entry has no composite ref", "rows": []}, 422

    ckey = ("readouts", str(ws_root), slug)
    hit = _READOUTS_CACHE.get(ckey)
    if hit is not None and (_time.time() - hit[0]) < _READOUTS_CACHE_TTL_S:
        return {**hit[1], "cached": True}, 200

    try:
        from pbg_superpowers.readout_validation import available_observables
    except Exception as e:  # noqa: BLE001
        return {"error": f"readout_validation unavailable: {e}"}, 501

    try:
        core, state, schema = build_composite_state_for_observables(ws_root, ref)
        available = available_observables(core, state, schema)
    except Exception as e:  # noqa: BLE001
        rows = _merge_readouts(spec, {"leaves": []})
        return {"composite": ref, "rows": rows,
                "note": f"composite {ref!r} could not be built — rows unverified: {e}"}, 422

    payload = {"composite": ref, "rows": _merge_readouts(spec, available), "note": ""}
    _READOUTS_CACHE[ckey] = (_time.time(), payload)
    if len(_READOUTS_CACHE) > 32:
        _READOUTS_CACHE.pop(next(iter(_READOUTS_CACHE)))
    return payload, 200


_aw.register_clear_cb(clear_cache)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_readouts_views.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add vivarium_dashboard/lib/readouts_views.py tests/test_readouts_views.py
git commit -m "feat(readouts): emit-plan + authored merge worker"
```

---

### Task 3: FastAPI route `GET /api/study-readouts`

**Files:**
- Modify: `vivarium_dashboard/api/app.py` (add route near `study_observable_check`, ~line 1788; add import to the `lib.models` import block ~line 104; the `lib.readouts_views` module is imported where `_obs_views`/`_report_views` are aliased — find that alias block and add `from vivarium_dashboard.lib import readouts_views as _readouts_views`)
- Test: `tests/test_study_readouts_route.py` (create)

**Interfaces:**
- Consumes: `readouts_views.build_study_readouts`, `models.StudyReadouts`.
- Produces: route `GET /api/study-readouts?study=<slug>` → `StudyReadouts` (200) or `JSONResponse` (4xx/5xx).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_study_readouts_route.py
from fastapi.testclient import TestClient
from vivarium_dashboard.api.app import create_app


def _client(tmp_path):
    return TestClient(create_app(workspace=tmp_path))


def test_study_readouts_invalid_slug_400(tmp_path):
    r = _client(tmp_path).get("/api/study-readouts?study=Bad Slug!")
    assert r.status_code == 400


def test_study_readouts_missing_study_404(tmp_path):
    r = _client(tmp_path).get("/api/study-readouts?study=nope")
    assert r.status_code == 404
```

> Note: confirm the app factory name/signature — grep `def create_app` in `vivarium_dashboard/api/app.py` and match the existing route tests' client setup (e.g. `tests/test_*_route.py`). If the factory differs, copy the exact pattern those tests use.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_study_readouts_route.py -v`
Expected: FAIL with 404 from FastAPI's default (route not registered) → assertion on 400 fails, or import error.

- [ ] **Step 3: Add the import + route**

In the `lib.models` import block (~line 104) add `StudyReadouts` to the imported names. In the worker-alias block (where `_obs_views`/`_report_views` are defined) add:

```python
from vivarium_dashboard.lib import readouts_views as _readouts_views
```

After the `study_observable_check` route (~line 1788) add:

```python
    @app.get(
        "/api/study-readouts",
        response_model=StudyReadouts,
        tags=["Data, inputs & references"],
        summary="Auto-listed readouts (emit plan + authored annotations)",
    )
    def study_readouts(
        study: str = "",
        ws: Path = Depends(get_workspace),
    ) -> Union[StudyReadouts, JSONResponse]:
        """Readouts table for a study: every emitter leaf the composite exposes,
        overlaid with authored ``study.yaml readouts:`` annotations.

        Library-backed via ``lib.readouts_views.build_study_readouts``:
        - 200 — ``{composite, rows, note}`` (validated through ``StudyReadouts``).
        - 400 — invalid slug / spec parse failure.
        - 404 — study not found.
        - 422 — no baseline composite, or composite could not be built
          (authored-only rows + ``note``).
        - 501 — ``readout_validation`` validator absent.
        """
        body, status = _readouts_views.build_study_readouts(ws, (study or "").strip())
        if status == 200:
            return StudyReadouts.model_validate(body)
        return JSONResponse(status_code=status, content=body)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_study_readouts_route.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add vivarium_dashboard/api/app.py tests/test_study_readouts_route.py
git commit -m "feat(api): GET /api/study-readouts route"
```

---

### Task 4: Frontend — render the readouts table from `/api/study-readouts`

**Files:**
- Modify: `vivarium_dashboard/templates/study-detail.html:1263-1317` (replace table body with an async shell)
- Modify: `vivarium_dashboard/static/study-detail.js:36-88` (replace `_loadReadoutValidation` + `_readoutValidationBadge` with `_loadReadouts` + `_emitStatusBadge`; remove the `not_in_structure` re-author link that points at the removed picker)
- Test: `tests/test_study_detail_template.py` (create or extend — a Jinja render smoke test)

**Interfaces:**
- Consumes: `GET /api/study-readouts` (Task 3) → `{composite, rows:[{store_path,name,description,units,index_by,annotated,emit_status,notes}], note}`.

- [ ] **Step 1: Write the failing template test**

```python
# tests/test_study_detail_template.py
import re
from pathlib import Path

TPL = Path("vivarium_dashboard/templates/study-detail.html").read_text(encoding="utf-8")


def test_readouts_panel_has_async_shell_not_authored_loop():
    # New shell present, old authored {% for o in _obs %} table gone.
    assert 'id="readouts-table"' in TPL
    assert "{% for o in _obs %}" not in TPL
```

> The picker-removal assertion lives in Task 5 (where the picker is deleted), so Task 4's suite stays green at its review gate.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_study_detail_template.py -v`
Expected: FAIL — `id="readouts-table"` not yet present; the picker string still present.

- [ ] **Step 3: Replace the readouts panel shell**

In `study-detail.html`, replace lines **1263-1317** (the `<section data-kind="observables">` through the `{% endif %}` of the auto-readouts fallback) with:

```html
<section class="study-tab-panel" data-kind="observables" id="panel-observables" hidden>
  <div class="observables-section">
    <h3 class="section-title">Readouts</h3>
    <p class="muted">Quantities collected from each simulation run. Each row is an emitter <code>store_path</code> the composite will save; authored entries in <code>study.yaml</code> add names, descriptions, and units on top.</p>
    <div id="readouts-table" data-study="{{ name }}">
      <p class="empty-message">Loading readouts from the composite emit plan…</p>
    </div>
  </div>
</section>
```

> This deletes the authored `{% for o in _obs %}` table, the `.readout-validation` cells, the `#auto-readouts` fallback, AND (continues in Task 5) the picker block that immediately follows.

- [ ] **Step 4: Replace the JS loader**

In `static/study-detail.js`, replace the `_loadReadoutValidation` / `_readoutValidationBadge` block (**lines 36-88**) with an emit-plan renderer. Keep the existing trigger that called `_loadReadoutValidation` (find its call site — search `_loadReadoutValidation(` — and rename it to `_loadReadouts(`):

```javascript
  // ── Readouts table (emit plan + authored annotations) ───────────────────────
  // Fetch /api/study-readouts and render the table async (the composite build is
  // ~3s, TTL-cached). Tolerates failure (leaves the loading message).
  var _readoutsLoaded = false;
  function _loadReadouts() {
    if (_readoutsLoaded) return;
    _readoutsLoaded = true;
    var host = document.getElementById('readouts-table');
    if (!host) return;
    var slug = host.getAttribute('data-study') || studyName();
    if (!slug) return;
    fetch('/api/study-readouts?study=' + encodeURIComponent(slug),
          {headers: {Accept: 'application/json'}})
      .then(function(r) { return r.ok || r.status === 422 ? r.json() : null; })
      .then(function(j) {
        if (!j || !Array.isArray(j.rows)) {
          host.innerHTML = '<p class="empty-message">Readouts unavailable.</p>';
          return;
        }
        host.innerHTML = _renderReadoutsTable(j);
      })
      .catch(function() {
        host.innerHTML = '<p class="empty-message">Readouts unavailable.</p>';
      });
  }

  function _emitStatusBadge(status) {
    var e = escapeHtmlForTests;
    var styles = {
      emitted:          {bg: '#d1fae5', fg: '#065f46', bd: '#6ee7b7', glyph: '✓', label: 'emitted'},
      not_in_emit_plan: {bg: '#fee2e2', fg: '#991b1b', bd: '#fca5a5', glyph: '✗', label: 'not in emit plan'},
      derived:          {bg: '#f1f5f9', fg: '#475569', bd: '#cbd5e1', glyph: '⏳', label: 'derived'},
    };
    var s = styles[status] || styles.derived;
    return '<span style="display:inline-block;padding:2px 8px;border-radius:9999px;background:'
      + s.bg + ';color:' + s.fg + ';border:1px solid ' + s.bd + '">' + s.glyph + ' ' + e(s.label) + '</span>';
  }

  function _renderReadoutsTable(j) {
    var e = escapeHtmlForTests;
    var note = j.note ? '<p class="muted" style="color:#92400e">' + e(j.note) + '</p>' : '';
    var head = '<table class="observables-table" style="width:100%; border-collapse: collapse;"><thead><tr>'
      + ['Name', 'Store path', 'Emitted?', 'Indexed by', 'Units', 'Description'].map(function(h) {
          return '<th style="text-align:left; padding:6px; border-bottom:1px solid #e2e8f0;">' + h + '</th>';
        }).join('') + '</tr></thead><tbody>';
    var body = (j.rows || []).map(function(o) {
      var idx = o.index_by ? '<code style="font-size:0.85em;">' + e(o.index_by.type) + '=' + e(o.index_by.value) + '</code>'
                           : '<span class="muted">—</span>';
      return '<tr style="border-bottom:1px solid #f1f5f9;" data-readout="' + e(o.name) + '">'
        + '<td style="padding:6px; vertical-align:top;"><code>' + e(o.name) + '</code></td>'
        + '<td style="padding:6px; vertical-align:top;"><code style="font-size:0.85em;">' + e(o.store_path || '') + '</code></td>'
        + '<td style="padding:6px; vertical-align:top; font-size:0.75em;">' + _emitStatusBadge(o.emit_status) + '</td>'
        + '<td style="padding:6px; vertical-align:top;">' + idx + '</td>'
        + '<td style="padding:6px; vertical-align:top; font-size:0.9em;">' + e(o.units || '') + '</td>'
        + '<td style="padding:6px; vertical-align:top; max-width:380px; font-size:0.9em;">' + e(o.description || '') + '</td>'
        + '</tr>';
    }).join('');
    return note + head + body + '</tbody></table>';
  }
```

> If `escapeHtmlForTests` is not in scope at this location, use the same escape helper the surrounding file already uses (grep `function escapeHtml` / `escapeHtmlForTests` near the top of `study-detail.js`).

- [ ] **Step 5: Run the template test**

Run: `python -m pytest tests/test_study_detail_template.py::test_readouts_panel_has_async_shell_not_authored_loop -v`
Expected: PASS for the shell assertion. (`test_add_observable_picker_removed` still FAILS until Task 5 — that is expected and fine.)

- [ ] **Step 6: Commit**

```bash
git add vivarium_dashboard/templates/study-detail.html vivarium_dashboard/static/study-detail.js tests/test_study_detail_template.py
git commit -m "feat(readouts): render table async from /api/study-readouts"
```

---

### Task 5: Remove the "+ Add observable from bigraph state" picker + retire `study-bigraph-paths`

**Files:**
- Modify: `vivarium_dashboard/templates/study-detail.html:1319-1513` (delete the `<details>` block + embedded `<script>`)
- Modify: `vivarium_dashboard/api/app.py` (remove the `/api/study-bigraph-paths` route, ~608-636 in the legacy numbering — grep `study-bigraph-paths`)
- Modify: `vivarium_dashboard/lib/study_viz_views.py` (remove `build_study_bigraph_paths`, ~90-173)
- Modify: `vivarium_dashboard/lib/models.py` (remove `StudyBigraphPaths` if now unused)
- Test: `tests/test_study_detail_template.py` (add `test_add_observable_picker_removed`)

**Interfaces:**
- Removes: `GET /api/study-bigraph-paths`, `build_study_bigraph_paths`, `StudyBigraphPaths`.

- [ ] **Step 0: Add the removal test**

Add to `tests/test_study_detail_template.py`:

```python
def test_add_observable_picker_removed():
    assert "Add observable from bigraph state" not in TPL
    assert "bigraph-picker-details" not in TPL
```

- [ ] **Step 1: Confirm no other consumer**

Run:
```bash
cd /Users/eranagmon/code/vdash-readouts
grep -rn "study-bigraph-paths\|build_study_bigraph_paths\|StudyBigraphPaths\|_bigraphNodes\|bigraph-picker-details" vivarium_dashboard tests
grep -rn "study-bigraph-paths\|build_study_bigraph_paths" /Users/eranagmon/code/pbg-superpowers/pbg_superpowers 2>/dev/null
```
Expected: hits only in the picker template block, the route, the lib function, the model, and the `_readoutValidationBadge` re-author link (already removed in Task 4). No external/plugin consumer. If any unexpected consumer appears, STOP and report.

- [ ] **Step 2: Run the removal test (verify it currently fails)**

Run: `python -m pytest tests/test_study_detail_template.py::test_add_observable_picker_removed -v`
Expected: FAIL (picker block still present).

- [ ] **Step 3: Delete the template picker block**

Delete `study-detail.html` lines **1319-1513** (the `<details id="bigraph-picker-details">` block through the closing `</script>`).

- [ ] **Step 4: Remove the route, worker, and model**

- In `api/app.py`, delete the `@app.get("/api/study-bigraph-paths" ...)` route and its handler (grep to locate).
- In `lib/study_viz_views.py`, delete `build_study_bigraph_paths`.
- In `lib/models.py`, delete `StudyBigraphPaths` and remove it from `api/app.py`'s model imports.

- [ ] **Step 5: Run tests**

Run:
```bash
python -m pytest tests/test_study_detail_template.py -v
python -m pytest tests/ -k "bigraph or observable or readout" -v
```
Expected: `test_add_observable_picker_removed` PASS; no remaining test imports `StudyBigraphPaths` / `build_study_bigraph_paths` (fix any that do by deletion). Full file still imports cleanly: `python -c "import vivarium_dashboard.api.app"`.

- [ ] **Step 6: Commit**

```bash
git add vivarium_dashboard/templates/study-detail.html vivarium_dashboard/api/app.py vivarium_dashboard/lib/study_viz_views.py vivarium_dashboard/lib/models.py
git commit -m "refactor: remove manual bigraph-state observable picker"
```

---

### Task 6: Dashboard-side store_path lint → readiness gaps

**Files:**
- Modify: `vivarium_dashboard/lib/report_views.py` (add `_readout_emit_plan_findings`; call it from `build_report_lint` alongside `_composite_resolution_findings`, ~267)
- Test: `tests/test_report_views_readout_lint.py` (create)

**Interfaces:**
- Consumes: `readouts_views.build_study_readouts` (reuses the emit-plan build + merge; orphan rows already carry `emit_status="not_in_emit_plan"`).
- Produces: `_readout_emit_plan_findings(ws_root: Path) -> list[dict]` returning `{study, check, severity, message, field_path}` items.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_report_views_readout_lint.py
from vivarium_dashboard.lib import report_views


def test_emit_plan_findings_flag_orphan_rows(monkeypatch, tmp_path):
    # One study with an authored available readout that isn't an emit leaf.
    def fake_iter(ws):
        return [("demo", None)]
    def fake_build(ws, slug):
        return ({"composite": "ecoli", "rows": [
            {"name": "good", "store_path": "agents.0.listeners.mass.cell_mass",
             "emit_status": "emitted", "annotated": True},
            {"name": "phantom", "store_path": "listeners.nope",
             "emit_status": "not_in_emit_plan", "annotated": True},
            {"name": "derived_ok", "store_path": "",
             "emit_status": "derived", "annotated": True},
        ]}, 200)
    monkeypatch.setattr(report_views, "_iter_study_slugs", fake_iter, raising=False)
    monkeypatch.setattr(report_views._readouts_views, "build_study_readouts", fake_build)

    findings = report_views._readout_emit_plan_findings(tmp_path)
    checks = [(f["study"], f["check"], f["severity"]) for f in findings]
    assert ("demo", "readout-store-path", "error") in checks
    # Only the phantom row produces a finding (emitted + derived are clean).
    assert len(findings) == 1
    assert "phantom" in findings[0]["message"]
```

> Confirm the study-slug iterator name in `report_views`/`study_spec` (grep `def _iter_study` and how `_composite_resolution_findings` enumerates studies); use that exact helper in the implementation and the test's `monkeypatch` target. If studies are enumerated inline, mirror that.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_report_views_readout_lint.py -v`
Expected: FAIL — `_readout_emit_plan_findings` does not exist.

- [ ] **Step 3: Implement the findings pass**

In `report_views.py`, add the import near the top (`from vivarium_dashboard.lib import readouts_views as _readouts_views`) and:

```python
def _readout_emit_plan_findings(ws_root: Path) -> list[dict]:
    """Findings for authored readouts whose store_path isn't an emit-plan leaf.

    Reuses ``readouts_views.build_study_readouts`` — any row with
    ``emit_status == 'not_in_emit_plan'`` (authored ``available`` readout that is
    missing a store_path or points off the emit plan) becomes an error finding
    feeding the readiness gaps. ``derived`` / ``emitted`` rows are clean.
    """
    out: list[dict] = []
    for slug, _spec in _iter_study_slugs(ws_root):
        try:
            body, status = _readouts_views.build_study_readouts(ws_root, slug)
        except Exception:  # noqa: BLE001
            continue
        if status not in (200, 422):
            continue
        for row in body.get("rows", []) or []:
            if row.get("emit_status") != "not_in_emit_plan":
                continue
            sp = row.get("store_path") or "(missing)"
            out.append({
                "study": slug,
                "check": "readout-store-path",
                "severity": "error",
                "message": (f"readout {row.get('name')!r} store_path {sp} is not an "
                            f"emittable leaf of the composite (never-fabricate)."),
                "field_path": f"readouts.{row.get('name')}.store_path",
            })
    return out
```

Then in `build_report_lint`, after the `findings.extend(_composite_resolution_findings(ws_root))` line (~267), add:

```python
    findings.extend(_readout_emit_plan_findings(ws_root))
```

> If `_iter_study_slugs` does not exist, define it inline by reusing the same study enumeration `_composite_resolution_findings` uses (grep that function), or import the study iterator from `study_spec`. Keep one enumeration helper.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_report_views_readout_lint.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add vivarium_dashboard/lib/report_views.py tests/test_report_views_readout_lint.py
git commit -m "feat(lint): flag readout store_paths not on the emit plan"
```

---

### Task 7: Remove Visualizations "Registered modules" + Runs "Compare/Clear" affordances

**Files:**
- Modify: `vivarium_dashboard/templates/study-detail.html:1888-1902` (registered viz section + add button) and `:1743-1746` (runs buttons)
- Modify: `vivarium_dashboard/static/study-detail.js:812-824` (delete `.btn-compare-selected` handler) and the `_clearRuns` wiring for this view
- Test: `tests/test_study_detail_template.py` (extend)

**Interfaces:** Pure removals — no new interfaces.

- [ ] **Step 1: Write the failing assertions**

Add to `tests/test_study_detail_template.py`:

```python
def test_registered_viz_modules_removed():
    assert "Registered visualization modules" not in TPL
    assert "btn-add-viz" not in TPL


def test_runs_compare_and_clear_buttons_removed():
    assert "btn-compare-selected" not in TPL
    assert "btn-clear-runs" not in TPL
    assert "Compare selected" not in TPL
    assert "Clear all runs" not in TPL
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_study_detail_template.py -k "registered_viz or compare_and_clear" -v`
Expected: FAIL (both sections still present).

- [ ] **Step 3: Delete the template sections**

- Delete `study-detail.html` lines **1888-1902** (the `<h3>Registered visualization modules</h3>` heading, `#viz-list` loop, and `+ Add visualization` button). Keep the "LATEST-RUN VISUALIZATIONS" auto-charts above it untouched.
- Delete `study-detail.html` lines **1743-1746** (the `<div class="runs-actions">` wrapper with both buttons).

- [ ] **Step 4: Delete the dead JS**

- In `static/study-detail.js`, delete the `bindAll('.btn-compare-selected', ...)` block (**lines 813-825**).
- Grep `_clearRuns` and `btn-clear-runs` in `study-detail.js`; delete the `_clearRuns` wiring used by this view. Do **not** delete `_clearRuns` if `walkthrough.js:13682` still calls a shared definition — confirm scope first; if it's a separate function in `walkthrough.js`, leave that one alone.

Run to confirm scope:
```bash
grep -rn "btn-clear-runs\|_clearRuns\|btn-compare-selected\|study-comparison-add" vivarium_dashboard/static vivarium_dashboard/templates
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_study_detail_template.py -v`
Expected: PASS (all template assertions).

- [ ] **Step 6: Commit**

```bash
git add vivarium_dashboard/templates/study-detail.html vivarium_dashboard/static/study-detail.js tests/test_study_detail_template.py
git commit -m "refactor: remove registered-viz + runs compare/clear bloat"
```

---

### Task 8: Readout `store_path` migration helper + scaffold update

**Files:**
- Create: `vivarium_dashboard/lib/readout_migration.py`
- Modify: `vivarium_dashboard/lib/scaffold_yaml.py:224-229` (uncomment/promote `store_path` to a required field with a clearer note)
- Test: `tests/test_readout_migration.py` (create)

**Interfaces:**
- Produces: `lift_store_paths(spec: dict) -> tuple[dict, int]` — mutates readouts in place, returns `(spec, n_changed)`. Lifts a leading dotted path out of `notes` prose into `store_path` when `store_path` is absent and `status` is not derived/aspirational. Idempotent.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_readout_migration.py
from vivarium_dashboard.lib.readout_migration import lift_store_paths


def test_lifts_leading_dotted_path_from_notes():
    spec = {"readouts": [{
        "name": "instantaneous_growth_rate",
        "notes": "listeners.mass.instantaneous_growth_rate — % change low→high.",
    }]}
    out, n = lift_store_paths(spec)
    assert n == 1
    r = out["readouts"][0]
    assert r["store_path"] == "listeners.mass.instantaneous_growth_rate"
    assert r["notes"].startswith("listeners.mass.instantaneous_growth_rate")  # notes kept


def test_idempotent_and_skips_existing_store_path():
    spec = {"readouts": [{"name": "x", "store_path": "a.b", "notes": "c.d foo"}]}
    out, n = lift_store_paths(spec)
    assert n == 0
    assert out["readouts"][0]["store_path"] == "a.b"


def test_skips_derived_metric_without_dotted_notes():
    spec = {"readouts": [{
        "name": "effective_knob_count", "status": "derived-needed",
        "notes": "Number of candidates with >2% response (measured 3).",
    }]}
    out, n = lift_store_paths(spec)
    assert n == 0
    assert "store_path" not in out["readouts"][0]
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_readout_migration.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement the helper**

Create `vivarium_dashboard/lib/readout_migration.py`:

```python
"""One-shot: lift a readout's emit ``store_path`` out of ``notes`` prose into a
structured field, so authored readouts attach to the emit-plan table and the
store_path lint can validate them. Idempotent; leaves ``notes`` text intact.
"""

from __future__ import annotations

import re

# A leading dotted path like ``listeners.mass.instantaneous_growth_rate`` —
# 2+ dot-separated identifier segments at the start of the notes string.
_LEADING_PATH = re.compile(r"^\s*([A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)+)")
_DERIVED = {"derived-needed", "aspirational"}


def lift_store_paths(spec: dict) -> tuple[dict, int]:
    changed = 0
    for r in spec.get("readouts", []) or []:
        if not isinstance(r, dict):
            continue
        if r.get("store_path"):
            continue
        if (r.get("status") or "").strip() in _DERIVED:
            continue
        m = _LEADING_PATH.match(str(r.get("notes") or ""))
        if not m:
            continue
        r["store_path"] = m.group(1)
        changed += 1
    return spec, changed
```

- [ ] **Step 4: Update the scaffold note**

In `scaffold_yaml.py:224-229`, change the readouts template comment so `store_path` reads as required for emitted readouts:

```yaml
# ★ readouts:                       # annotations layered onto the emit-plan table
#   - name: kebab-readout-name
#     store_path: agents.0.listeners.x.y   # REQUIRED for status: available — must be an emitter leaf
#     description: ""
#     units: ""
#     status: derived-needed        # available | derived-needed | aspirational
```

- [ ] **Step 5: Run to verify pass**

Run: `python -m pytest tests/test_readout_migration.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add vivarium_dashboard/lib/readout_migration.py vivarium_dashboard/lib/scaffold_yaml.py tests/test_readout_migration.py
git commit -m "feat(readouts): store_path lift migration + scaffold note"
```

---

### Task 9: Full-suite regression + apply migration to v2ecoli + manual end-to-end verify

**Files:**
- Modify (data, separate repo): `v2ecoli/workspace/studies/*/study.yaml` (apply `lift_store_paths` in a clean v2ecoli worktree)
- No dashboard code changes.

**Interfaces:** none (verification task).

- [ ] **Step 1: Run the dashboard suite**

Run:
```bash
cd /Users/eranagmon/code/vdash-readouts
python -m pytest tests/ -q
```
Expected: green. Investigate any failure referencing removed symbols (`StudyBigraphPaths`, `build_study_bigraph_paths`, `.btn-compare-selected`, `_loadReadoutValidation`) and fix by deletion/rename.

- [ ] **Step 2: Apply the migration to v2ecoli studies (clean worktree)**

> v2ecoli's checkout sits on a stale feature branch (memory `project_v2ecoli_units_propagation` stale-branch hazard). Work from a fresh worktree off `origin/main`.

```bash
cd /Users/eranagmon/code/v2ecoli
git fetch origin -q
git worktree add -b chore/readout-store-paths /Users/eranagmon/code/v2e-readouts origin/main
```

Then run a one-shot over the workspace studies using the helper (PYTHONPATH the dashboard worktree so the import resolves without reinstalling):

```bash
cd /Users/eranagmon/code/v2e-readouts
PYTHONPATH=/Users/eranagmon/code/vdash-readouts python - <<'PY'
import sys, pathlib, yaml
from vivarium_dashboard.lib.readout_migration import lift_store_paths
for sf in pathlib.Path("workspace/studies").glob("*/study.yaml"):
    spec = yaml.safe_load(sf.read_text()) or {}
    spec, n = lift_store_paths(spec)
    if n:
        sf.write_text(yaml.safe_dump(spec, sort_keys=False, allow_unicode=True))
        print(f"{sf}: lifted {n}")
PY
```

> Caveat: `yaml.safe_dump` reflows the file (loses comment formatting). If the v2ecoli studies use ruamel-preserved YAML, instead edit `param-uq-00-screen/study.yaml` by hand — set `store_path: listeners.mass.instantaneous_growth_rate` on `instantaneous_growth_rate`, leave `effective_knob_count` as a derived metric. Verify the diff is annotation-only before committing.

Commit in the v2ecoli worktree:
```bash
git add workspace/studies && git commit -m "chore(readouts): lift store_path into structured field"
```

- [ ] **Step 3: Manual end-to-end verification (live dashboard)**

Restart the dashboard against the worktree (NOT a global `-e` reinstall):
```bash
cd /Users/eranagmon/code/v2e-readouts
PYTHONPATH=/Users/eranagmon/code/vdash-readouts \
  /Users/eranagmon/code/vdash-readouts/.venv/bin/vivarium-dashboard serve --workspace . --port 8799
```
Open `http://localhost:8799`, go to `param-uq-00-screen`, and confirm:
- **Readouts tab** — table auto-lists emit-plan paths; `store_path` filled on every row; `instantaneous_growth_rate` shows the authored name + ✓ emitted; `effective_knob_count` shows ⏳ derived; the "+ Add observable from bigraph state" panel is gone.
- **Visualizations tab** — no "Registered visualization modules" section / "+ Add visualization" button; the latest-run auto chart still renders.
- **Runs tab** — no "Compare selected" / "Clear all runs" buttons.
- **Readiness** — the "⚠ N gaps" panel reflects any readout whose store_path is off the emit plan (intentionally break one store_path to confirm a gap appears, then revert).

- [ ] **Step 4: Final commit (if any verification fixes were needed)**

```bash
cd /Users/eranagmon/code/vdash-readouts
git add -A && git commit -m "test: verification fixes for readouts/de-bloat" --allow-empty
```

---

## Self-review

**Spec coverage:**
- Component A (emit-plan auto-list table) → Tasks 1, 2, 3, 4. ✓
- Component B (store_path lint → readiness gaps) → Task 6 (dashboard-side, per refined decision). ✓
- Component C (schema + migration) → Task 8 (helper + scaffold) + Task 9 step 2 (apply to v2ecoli). ✓
- Component D (Visualizations de-bloat) → Task 7. ✓
- Component E (Runs de-bloat) → Task 7. ✓
- "+ Add observable" removal → Task 5. ✓
- Derived-metric nuance (`effective_knob_count` exempt) → Task 2 tests + Task 6 (only `not_in_emit_plan` flagged). ✓
- FastAPI-first / no `server.serve` → Global Constraints + Tasks 3/6 on the FastAPI seam. ✓

**Placeholder scan:** No "TBD"/"add error handling"-style gaps; every code step shows full code. The few "grep to confirm the exact helper/factory name" notes are deliberate verification guards against drift in code this plan doesn't fully quote (app factory signature, study-iterator name, escape-helper name), each with the exact grep to run — not placeholders for logic.

**Type consistency:** `emit_status` literals (`emitted` / `not_in_emit_plan` / `derived`) match across Task 1 (model), Task 2 (worker), Task 4 (JS badge), Task 6 (lint filter). `ReadoutRow` field names match the JS renderer (`store_path,name,description,units,index_by,annotated,emit_status,notes`). `build_study_readouts` signature consistent across Tasks 2/3/6.

**Decisions resolved at plan time (were flagged in the spec):** row granularity = one per raw emit leaf (Task 2); lint location = dashboard-side `report_views` (Task 6), not `pbg-superpowers` (avoids the ~3s build in a fast spec linter + plugin-propagation friction).
