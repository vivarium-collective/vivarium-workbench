# Shared "Configure & Run" Widget (SP-C) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** A native "Configure & Run" widget — auto-generated config form + context-aware run + durable results + save-as-variant/delete — embedded in the Composite Explorer, Composites list, and study Runs tab, wiring SP-A+B into one run experience.

**Architecture:** Backend first (the v4 save path + two thin persist routes wiring SP-B's libs), then a native JS module `static/configure-run.js` (`ConfigureRun.mount(el,{composite,target,study})`) that generates the form from `/api/composite-resolve`'s `parameters`, routes the run by context (study `study-run-*` / ad-hoc `composite-test-run` / remote two-phase), and calls the new persist routes. Then three thin embeddings.

**Tech Stack:** Python 3.12 (FastAPI routes, `yaml`, `sqlite3`), vanilla JS (no framework), the repo's string-presence JS-test convention.

## Global Constraints
- **No new dependencies.** Backend reuses `study_variants`, `composite_runs`; JS is vanilla (match `study-detail.js` / `walkthrough.js` style).
- **`parameters` shape** (from `/api/composite-resolve`): `{name: {type: "string"|"float"|"int"|"bool", default, description}}`; may be `null` or `{}` (→ no fields, steps only).
- **Run-request body** for ad-hoc: `POST /api/composite-test-run {id, overrides, steps, label?}` → 202 `{run_id, status:"running"}`.
- **JS is tested by string-presence** (repo convention — no browser harness): assert the function names, endpoint strings, and key logic tokens exist in the served `.js`/template. Backend is unit-tested with fakes.
- Work in worktree `/Users/eranagmon/code/vdash-sp-c` (branch `feat/unified-run-ui-sp-c`, stacked on SP-A+B). Tests: `cd /Users/eranagmon/code/vdash-sp-c && PYTHONPATH=$PWD /Users/eranagmon/code/v2ecoli/.venv/bin/python -m pytest <path> -v`. Ruff: `/Users/eranagmon/code/v2ecoli/.venv/bin/ruff check <file>`.

---

### Task 1: v4 `conditions.variants` save path in `save_run_as_variant`
**Files:** Modify `vivarium_dashboard/lib/study_variants.py`; Test `tests/test_save_run_as_variant.py` (append).
**Interfaces — Produces:** `save_run_as_variant` writes to `conditions.variants` for a `schema_version: 4` study (else top-level `variants:`), same return shape.

- [ ] **Step 1: Write the failing test**
```python
def test_save_run_as_variant_v4_writes_conditions_variants(tmp_path):
    import time, yaml
    from vivarium_dashboard.lib import composite_runs as cr
    from vivarium_dashboard.lib import study_variants
    src = tmp_path / "r.db"; conn = cr.connect(src)
    cr.save_metadata(conn, spec_id="pkg.composites.cell", run_id="r1",
                     params={"k": 5}, label="fast", started_at=time.time(), n_steps=3)
    sd = tmp_path / "studies" / "demo"; sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 4, "name": "demo",
        "conditions": {"baseline": {"composite": "pkg.composites.cell"}, "variants": []},
    }))
    body, status = study_variants.save_run_as_variant(
        tmp_path, run_id="r1", source_db=src, study="demo", variant_name="fast")
    assert status == 200
    spec = yaml.safe_load((sd / "study.yaml").read_text())
    assert "variants" not in spec  # NOT written to the ignored top-level
    var = [v for v in spec["conditions"]["variants"] if v["name"] == "fast"][0]
    assert var["composite"] == "pkg.composites.cell" and var["parameter_overrides"] == {"k": 5}
```
- [ ] **Step 2: Run → fail** (currently writes top-level `variants:`).
- [ ] **Step 3: Implement** — in `save_run_as_variant`, after loading `spec`, choose the variants list by schema:
```python
    spec = yaml.safe_load(sf.read_text(encoding="utf-8")) or {}
    if spec.get("schema_version") == 4 and isinstance(spec.get("conditions"), dict):
        variants = spec["conditions"].setdefault("variants", [])
    else:
        variants = spec.setdefault("variants", [])
```
(Replace the existing `variants = spec.setdefault("variants", [])` line; the rest of the append/overwrite + write-back is unchanged.)
- [ ] **Step 4: Run → pass** (new v4 test + the existing v3 tests in the file).
- [ ] **Step 5: Commit** `feat(study-variants): v4 conditions.variants save path`.

---

### Task 2: `POST /api/save-run-as-variant` route
**Files:** Modify `vivarium_dashboard/lib/models.py` (request model), `vivarium_dashboard/api/app.py` (route + readonly allowlist); Test `tests/test_save_run_as_variant_route.py`.
**Interfaces — Consumes:** `study_variants.save_run_as_variant(workspace, *, run_id, source_db, study, variant_name)`. **Produces:** `POST /api/save-run-as-variant {run_id, source_db?, study, variant_name}` → JSONResponse passing through `(body, status)`. `source_db` defaults to `<ws>/.pbg/composite-runs.db` when omitted.

- [ ] **Step 1: Write the failing test** (drive the FastAPI app with a fake `study_variants`):
```python
def test_save_run_as_variant_route(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from vivarium_dashboard.api import app as appmod
    monkeypatch.setattr(appmod, "get_workspace", lambda: tmp_path, raising=False)
    captured = {}
    def _fake(ws, *, run_id, source_db, study, variant_name):
        captured.update(run_id=run_id, study=study, variant=variant_name)
        return {"study": study, "variant": variant_name, "composite": "c"}, 200
    monkeypatch.setattr(appmod._study_variants, "save_run_as_variant", _fake, raising=False)
    client = TestClient(appmod.create_app())
    r = client.post("/api/save-run-as-variant",
                    json={"run_id": "r1", "study": "demo", "variant_name": "fast"})
    assert r.status_code == 200 and r.json()["variant"] == "fast"
    assert captured["run_id"] == "r1"
```
- [ ] **Step 2: Run → fail** (404 — route absent).
- [ ] **Step 3: Implement** — add a request model in `models.py`:
```python
class SaveRunAsVariantRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    run_id: str = ""
    source_db: Optional[str] = None
    study: str = ""
    variant_name: str = ""
```
Import `study_variants as _study_variants` at the top of `app.py` (next to the other `lib` imports). Add the route near the other run routes:
```python
    @app.post("/api/save-run-as-variant", tags=["Runs"],
              summary="Save a run (composite+config) as a named study variant")
    def save_run_as_variant(req: SaveRunAsVariantRequest,
                            ws: Path = Depends(get_workspace)) -> JSONResponse:
        src = req.source_db or str(ws / ".pbg" / "composite-runs.db")
        body, status = _study_variants.save_run_as_variant(
            ws, run_id=req.run_id, source_db=src, study=req.study, variant_name=req.variant_name)
        return JSONResponse(status_code=status, content=body)
```
Add `"/api/save-run-as-variant"` to the `_READONLY_ALLOWED_MUTATIONS` set.
- [ ] **Step 4: Run → pass.**  **Step 5: Commit** `feat(api): save-run-as-variant route (SP-C)`.

---

### Task 3: `POST /api/run-delete` route
**Files:** Modify `vivarium_dashboard/lib/models.py`, `vivarium_dashboard/api/app.py`; Test `tests/test_run_delete_route.py`.
**Interfaces — Consumes:** `composite_runs.connect`, `composite_runs.delete_run`. **Produces:** `POST /api/run-delete {run_id, db_path}` → `{"deleted": bool}`; also removes `<ws>/.pbg/runs/<run_id>/` if present.

- [ ] **Step 1: Write the failing test**
```python
def test_run_delete_route(monkeypatch, tmp_path):
    import time
    from fastapi.testclient import TestClient
    from vivarium_dashboard.api import app as appmod
    from vivarium_dashboard.lib import composite_runs as cr
    monkeypatch.setattr(appmod, "get_workspace", lambda: tmp_path, raising=False)
    db = tmp_path / ".pbg" / "composite-runs.db"; db.parent.mkdir(parents=True)
    conn = cr.connect(db)
    cr.save_metadata(conn, spec_id="s", run_id="s__1__a", params={}, label="L",
                     started_at=time.time(), n_steps=1)
    client = TestClient(appmod.create_app())
    r = client.post("/api/run-delete", json={"run_id": "s__1__a", "db_path": str(db)})
    assert r.status_code == 200 and r.json()["deleted"] is True
    assert cr.query_run_meta(cr.connect(db), run_id="s__1__a") is None
```
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** — request model:
```python
class RunDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    run_id: str = ""
    db_path: str = ""
```
Route (uses `composite_runs as _composite_runs`, already imported as `cr`/similar — confirm the alias in app.py and reuse it; below assumes `from vivarium_dashboard.lib import composite_runs as _composite_runs`):
```python
    @app.post("/api/run-delete", tags=["Runs"], summary="Delete a run (row + artifacts)")
    def run_delete(req: RunDeleteRequest, ws: Path = Depends(get_workspace)) -> JSONResponse:
        import shutil
        if not req.run_id:
            return JSONResponse(status_code=400, content={"error": "run_id required"})
        # A blank db_path defaults to the ad-hoc composite-runs store, so the
        # widget needn't know the workspace path (open-question #2).
        db_path = req.db_path or str(ws / ".pbg" / "composite-runs.db")
        conn = _composite_runs.connect(db_path)
        try:
            deleted = _composite_runs.delete_run(conn, run_id=req.run_id)
        finally:
            conn.close()
        art = ws / ".pbg" / "runs" / req.run_id
        if art.is_dir():
            shutil.rmtree(art, ignore_errors=True)
        return JSONResponse(status_code=200, content={"deleted": deleted})
```
Add `"/api/run-delete"` to `_READONLY_ALLOWED_MUTATIONS`.
- [ ] **Step 4: Run → pass.**  **Step 5: Commit** `feat(api): run-delete route (SP-C)`.

---

### Task 4: `configure-run.js` — config-form generation + override collection
**Files:** Create `vivarium_dashboard/static/configure-run.js`; Test `tests/test_configure_run_widget.py`.
**Interfaces — Produces:** a `window.ConfigureRun` object with `mount(el, ctx)`, `_buildConfigForm(parameters)` (returns the form HTML), `_collectOverrides(formEl, parameters)` (returns the type-cast overrides dict). `parameters` may be `null`/`{}`.

- [ ] **Step 1: Write the failing test** (string-presence — the repo convention; assert the served JS contains the generation logic + type handling):
```python
from pathlib import Path
from vivarium_dashboard import server

def _js():
    return (Path(server.__file__).parent / "static" / "configure-run.js").read_text(encoding="utf-8")

def test_configure_run_form_generation_present():
    js = _js()
    assert "window.ConfigureRun" in js
    assert "function mount" in js or "mount:" in js
    assert "_buildConfigForm" in js and "_collectOverrides" in js
    assert "/api/composite-resolve" in js
    # type-driven inputs: number for float/int, checkbox for bool, text for string
    assert "'number'" in js or '"number"' in js
    assert "checkbox" in js
    # handles null/empty parameters without crashing
    assert "parameters || {}" in js or "|| {}" in js
    # collects overrides with type casting
    assert "parseFloat" in js or "Number(" in js
    assert "parseInt" in js
```
- [ ] **Step 2: Run → fail** (file absent).
- [ ] **Step 3: Implement** `vivarium_dashboard/static/configure-run.js` — the form-generation core (run-routing added in Task 5):
```javascript
// Shared "Configure & Run" widget (SP-C). Native, embeddable in the Composite
// Explorer, Composites list, and study Runs tab. mount(el, {composite, target, study}).
(function () {
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
    return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]; }); }

  // Build a config form from composite-resolve's parameters:
  // {name: {type:"string"|"float"|"int"|"bool", default, description}}. May be null/{}.
  function _buildConfigForm(parameters) {
    var params = parameters || {};
    var names = Object.keys(params);
    if (!names.length) return '<p class="muted">This composite has no configurable parameters.</p>';
    return names.map(function (name) {
      var p = params[name] || {};
      var t = p.type, d = p.default, desc = p.description || "";
      var inputId = "cfg-" + name;
      var field;
      if (t === "bool") {
        field = '<input type="checkbox" id="' + esc(inputId) + '" data-param="' + esc(name) +
          '" data-type="bool"' + (d ? " checked" : "") + ">";
      } else if (t === "float" || t === "int") {
        field = '<input type="number" id="' + esc(inputId) + '" data-param="' + esc(name) +
          '" data-type="' + esc(t) + '" value="' + esc(d) + '"' + (t === "float" ? ' step="any"' : "") + ">";
      } else {
        field = '<input type="text" id="' + esc(inputId) + '" data-param="' + esc(name) +
          '" data-type="string" value="' + esc(d) + '">';
      }
      return '<label class="cfg-row" title="' + esc(desc) + '"><span class="cfg-name">' +
        esc(name) + "</span>" + field + "</label>";
    }).join("");
  }

  // Read the form back into a type-cast overrides dict (only changed-from-default need not be
  // tracked — send all; the runner overlays them).
  function _collectOverrides(formEl, parameters) {
    var out = {};
    var inputs = formEl.querySelectorAll("[data-param]");
    for (var i = 0; i < inputs.length; i++) {
      var el = inputs[i], name = el.getAttribute("data-param"), type = el.getAttribute("data-type");
      if (type === "bool") out[name] = !!el.checked;
      else if (type === "float") out[name] = parseFloat(el.value);
      else if (type === "int") out[name] = parseInt(el.value, 10);
      else out[name] = el.value;
    }
    return out;
  }

  var ctxState = {};
  function mount(el, ctx) {
    ctxState = ctx || {};
    el.innerHTML = '<div class="cfg-run"><div class="cfg-loading">Loading composite…</div></div>';
    var id = ctxState.composite;
    if (!id) { el.querySelector(".cfg-run").innerHTML = '<p class="muted">Pick a composite to configure.</p>'; return; }
    fetch("/api/composite-resolve?id=" + encodeURIComponent(id) + "&overrides=%7B%7D")
      .then(function (r) { return r.text().then(function (t) { try { return JSON.parse(t); } catch (e) { return { error: "HTTP " + r.status }; } }); })
      .then(function (d) {
        var box = el.querySelector(".cfg-run");
        if (!d || d.error || d.unresolved) {
          box.innerHTML = '<div class="inv-run-err">' + esc((d && (d.error || "composite not found")) ) + "</div>";
          return;
        }
        box.innerHTML =
          '<h4>' + esc(d.name || id) + '</h4>' +
          '<form class="cfg-form">' + _buildConfigForm(d.parameters) + '</form>' +
          '<label class="cfg-row"><span class="cfg-name">steps</span>' +
          '<input type="number" class="cfg-steps" value="' + esc(d.default_n_steps != null ? d.default_n_steps : 5) + '"></label>' +
          '<div class="cfg-actions"><button type="button" class="btn-mini cfg-run-btn">▶ Run</button></div>' +
          '<div class="cfg-status" hidden></div>';
        ConfigureRun._wireRun(el, d);   // defined in Task 5
      });
  }

  window.ConfigureRun = {
    mount: mount, _buildConfigForm: _buildConfigForm, _collectOverrides: _collectOverrides,
    _ctx: function () { return ctxState; },
  };
})();
```
- [ ] **Step 4: Run → pass.**  **Step 5: Commit** `feat(sp-c): configure-run widget — config-form generation`.

---

### Task 5: `configure-run.js` — context-aware run routing + poll + save/delete
**Files:** Modify `vivarium_dashboard/static/configure-run.js`; Test `tests/test_configure_run_widget.py` (append).
**Interfaces — Consumes:** `_collectOverrides` (Task 4), `/api/composite-test-run`, `/api/study-run-baseline`/`-variant`, `/api/save-run-as-variant` (Task 2), `/api/run-delete` (Task 3). **Produces:** `ConfigureRun._wireRun(el, resolved)` + `_runAdhoc`/`_runStudy`/`_poll`/`_saveAsVariant`/`_deleteRun`.

- [ ] **Step 1: Write the failing test** (string-presence):
```python
def test_configure_run_routing_and_persist_present():
    js = _js()
    assert "_wireRun" in js
    # context-aware routing
    assert "/api/composite-test-run" in js          # ad-hoc
    assert "/api/study-run-baseline" in js or "/api/study-run-variant" in js  # study
    assert "_ctx()" in js or "ctxState" in js        # reads {target, study}
    assert "'study'" in js or '"study"' in js
    # durable persist actions
    assert "/api/save-run-as-variant" in js
    assert "/api/run-delete" in js
    # tolerant polling (WS1 pattern)
    assert "consecutiveErrors" in js or "setTimeout" in js
    assert ".catch(" in js
```
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** — append the run/poll/persist logic. `_wireRun` binds the Run button; routing reads `ctxState.target`:
```javascript
  function _post(url, body) {
    return fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
      .then(function (r) { return r.text().then(function (t) { var j; try { j = JSON.parse(t); } catch (e) { j = { error: "HTTP " + r.status }; } return { status: r.status, body: j }; }); });
  }
  function _status(el, html) { var s = el.querySelector(".cfg-status"); if (s) { s.hidden = false; s.innerHTML = html; } }

  function _poll(el, statusUrl) {
    var tries = 0;
    function tick() {
      fetch(statusUrl).then(function (r) { return r.json(); }).then(function (d) {
        var phase = String((d && (d.status || d.phase)) || "").toLowerCase();
        if (phase === "completed" || phase === "done") { _onDone(el); return; }
        if (phase === "failed" || phase === "error") { _status(el, '<span class="inv-run-err">✗ Failed</span>'); return; }
        _status(el, "Running… (" + esc(phase || "queued") + ")");
        setTimeout(tick, 2500);
      }).catch(function () { tries += 1; if (tries < 4) setTimeout(tick, 3000); else _status(el, '<span class="inv-run-err">poll error</span>'); });
    }
    tick();
  }

  function _onDone(el) {
    var ctx = ctxState, run = el._lastRunId || "";
    var actions = '<button type="button" class="btn-mini cfg-view-btn">View results</button>';
    if (ctx.target !== "study") actions += ' <button type="button" class="btn-mini cfg-savevar-btn">Save as variant</button>';
    actions += ' <button type="button" class="btn-mini cfg-del-btn">Delete</button>';
    _status(el, '<strong>✓ Done</strong> <code>' + esc(run) + '</code> ' + actions);
    var sv = el.querySelector(".cfg-savevar-btn"); if (sv) sv.onclick = function () { _saveAsVariant(el); };
    var dl = el.querySelector(".cfg-del-btn"); if (dl) dl.onclick = function () { _deleteRun(el); };
  }

  function _wireRun(el, resolved) {
    var btn = el.querySelector(".cfg-run-btn");
    btn.onclick = function () {
      var overrides = _collectOverrides(el.querySelector(".cfg-form"), resolved.parameters);
      var steps = parseInt(el.querySelector(".cfg-steps").value, 10) || 5;
      btn.disabled = true; _status(el, "Starting…");
      if (ctxState.target === "study") _runStudy(el, overrides);
      else _runAdhoc(el, resolved.id || ctxState.composite, overrides, steps);
    };
  }

  function _runAdhoc(el, id, overrides, steps) {
    _post("/api/composite-test-run", { id: id, overrides: overrides, steps: steps }).then(function (res) {
      if (res.status !== 202 || !res.body.run_id) { _status(el, '<span class="inv-run-err">' + esc((res.body && res.body.error) || res.status) + '</span>'); return; }
      el._lastRunId = res.body.run_id;
      _poll(el, "/api/composite-run/" + encodeURIComponent(res.body.run_id) + "/status");
    }).catch(function (e) { _status(el, '<span class="inv-run-err">' + esc(String(e)) + '</span>'); });
  }

  function _runStudy(el, overrides) {
    // Study context: the study's baseline composite + this config = the variant run.
    // (Local pipeline runs sync; reload the Runs tab on done.)
    _post("/api/study-run-baseline", { study: ctxState.study, overrides: overrides }).then(function (res) {
      if (res.status !== 200) { _status(el, '<span class="inv-run-err">' + esc((res.body && res.body.error) || res.status) + '</span>'); return; }
      _status(el, '<strong>✓ Run complete</strong> — refresh the Runs tab.');
    }).catch(function (e) { _status(el, '<span class="inv-run-err">' + esc(String(e)) + '</span>'); });
  }

  function _saveAsVariant(el) {
    var name = window.prompt("Variant name:"); if (!name) return;
    var study = ctxState.study || window.prompt("Save into which study (slug)?"); if (!study) return;
    _post("/api/save-run-as-variant", { run_id: el._lastRunId, study: study, variant_name: name }).then(function (res) {
      _status(el, res.status === 200 ? '<strong>✓ Saved as variant</strong> ' + esc(name) : '<span class="inv-run-err">' + esc((res.body && res.body.error) || res.status) + '</span>');
    });
  }

  function _deleteRun(el) {
    if (!window.confirm("Delete this run?")) return;
    var db = ctxState.dbPath || "";  // adhoc → .pbg/composite-runs.db resolved server-side if blank
    _post("/api/run-delete", { run_id: el._lastRunId, db_path: db || (ctxState.composite ? "" : "") }).then(function () {
      _status(el, "Deleted.");
    });
  }

  // expose for _wireRun call in mount() + tests
  window.ConfigureRun._wireRun = _wireRun;
  window.ConfigureRun._runAdhoc = _runAdhoc;
  window.ConfigureRun._runStudy = _runStudy;
  window.ConfigureRun._saveAsVariant = _saveAsVariant;
  window.ConfigureRun._deleteRun = _deleteRun;
```
(Note: `_deleteRun` sends an empty `db_path` for ad-hoc; in planning-resolution #2 below, the route already defaults nothing — for ad-hoc the widget should send `<ws>/.pbg/composite-runs.db`; since the JS can't know the ws path, send `""` and have the route default it. Adjust Task 3's route to default `db_path` to `<ws>/.pbg/composite-runs.db` when blank — update Task 3 accordingly.)
- [ ] **Step 4: Run → pass.**  **Step 5: Commit** `feat(sp-c): configure-run widget — routing + poll + persist`.

---

### Task 6: Embed the widget in the three surfaces
**Files:** Modify `vivarium_dashboard/templates/index.html.j2` (Explorer `#page-composite-explore`, Composites list cards), `vivarium_dashboard/templates/study-detail.html` (`#panel-runs`), and load the script; Test `tests/test_configure_run_embeddings.py`.
**Interfaces — Consumes:** `ConfigureRun.mount(el, {composite, target, study})`.

- [ ] **Step 1: Write the failing test** (string-presence on the templates + a render check):
```python
from pathlib import Path
from vivarium_dashboard import server

def _read(rel):
    return (Path(server.__file__).parent / rel).read_text(encoding="utf-8")

def test_widget_script_loaded_and_mounted():
    idx = _read("templates/index.html.j2")
    assert "configure-run.js" in idx                       # script included
    assert "ConfigureRun.mount" in idx                     # explorer + list mount
    assert 'target: "adhoc"' in idx or "target:'adhoc'" in idx
    sd = _read("templates/study-detail.html")
    assert "ConfigureRun.mount" in sd                      # study Runs tab
    assert 'target: "study"' in sd or "target:'study'" in sd
```
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** —
  (a) Load the script once in `index.html.j2` and `study-detail.html` (next to the other `<script src=…>` tags): `<script src="/configure-run.js"></script>`.
  (b) **Explorer** (`#page-composite-explore`): add a mount container `<div id="ce-configure-run"></div>` and, where `_loadCompositeExplorer` runs, mount the widget for the current composite ad-hoc:
  ```javascript
  if (window.ConfigureRun) ConfigureRun.mount(document.getElementById("ce-configure-run"),
      { composite: data.id, target: "adhoc" });
  ```
  Keep `#composite-explore-frame` (the loom wiring view) as-is.
  (c) **Composites list** card "Explore" action gains a sibling **"Configure & Run"** button that mounts the widget inline (or routes to the Explorer with the widget): in the card render, add
  `<button class="btn-mini" onclick="_openConfigureRun('<id>')">Configure & Run</button>` and a small `_openConfigureRun(id)` that navigates to the Explorer page for that id (which now hosts the widget) — reuse `_openCompositeExplorer(id)`.
  (d) **study Runs tab** (`#panel-runs`): add a "New run" container `<div id="study-configure-run"></div>` + a button that mounts the widget in study context:
  ```javascript
  ConfigureRun.mount(document.getElementById("study-configure-run"),
      { composite: <study baseline composite>, target: "study", study: studyName() });
  ```
  (the baseline composite ref is available in the study payload the Runs tab already loads).
- [ ] **Step 4: Run → pass** (string-presence test + the existing `_render_study_detail_html` render test still renders).
- [ ] **Step 5: Commit** `feat(sp-c): embed Configure & Run in Explorer, list, study Runs`.

## Self-Review
**Spec coverage:** native widget (Tasks 4–5) · config-form generation from `parameters` (Task 4) · context-aware run routing (Task 5) · save-as-variant + delete (Tasks 2,3,5) · v4 save path (Task 1) · 3 embeddings (Task 6). **Placeholder scan:** none — every step has runnable code + commands; the JS tests follow the repo's string-presence convention (spec open-question #1 resolved: string-presence, no browser harness). **Type consistency:** `ConfigureRun.mount(el, {composite, target, study})`, `_buildConfigForm(parameters)`, `_collectOverrides(formEl, parameters)`, `_wireRun(el, resolved)` are used identically across Tasks 4–6; the routes `/api/save-run-as-variant` (Task 2) and `/api/run-delete` (Task 3) match the JS calls in Task 5.

**Open-question resolutions:** (1) JS tested by string-presence (repo convention); (2) `run-delete` defaults a blank `db_path` to `<ws>/.pbg/composite-runs.db` server-side (update Task 3's route to do so) so the widget needn't know the ws path; (3) Explorer cutover = mount the native widget alongside, keeping the loom iframe for the wiring view (replace the loom Configure/Run interactions in a later cleanup once the widget is proven).

## Follow-on (separate)
- **SP-D** — fill the deployment seam so ad-hoc remote generator composites run (the widget already shows the 409 message until then).
- Extend the 409 route-by-source seam to the investigation batch-runners.
- Optional: replace the loom iframe's Configure/Run entirely once the native widget is proven.
