# Data Explorer v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the explorer's Timeseries (unit-grouped, class-filtered), Scatter (run-vs-run omics), and Allocation (labeled drill-down mass Voronoi) views, backed by per-observable unit/class tags and a new vector-snapshot endpoint.

**Architecture:** Two pure path-classifier helpers tag every observable with `unit` + `mclass`; a new `/api/explorer/vector` returns one vector observable's per-entity values at a step. The four client views move into a focused `explorer-views.js`; Timeseries/Scatter/Allocation are rewritten against the enriched data.

**Tech Stack:** Python stdlib + existing readers (no new deps); client = vanilla JS, Plotly 2.27, d3 v7 + d3-voronoi-treemap, escher (all CDN, already loaded).

## Global Constraints

- No new Python runtime dependencies; client libs are CDN-only.
- All backend logic in `vivarium_dashboard/lib/explorer_data.py`; thin `server.py` handlers that never raise to the client (return empty structures on failure).
- Emitter-aware: SQLite + zarr. SQLite behavior must stay unchanged where not explicitly modified.
- New client view code lives in `vivarium_dashboard/static/explorer-views.js`; `explorer.js` keeps only the controller + shared helpers.
- Run tests with: `cd /Users/eranagmon/code/vdash-explorer && python -m pytest tests/test_explorer_data.py -v`.
- Frontend verification: `node --check` each JS file + the dashboard serves the page and scripts (no JS test runner; visual pass is the user's).
- Branch/worktree: `feat/analyses-data-explorer` in `/Users/eranagmon/code/vdash-explorer`.

---

## File Structure

- `vivarium_dashboard/lib/explorer_data.py` (modify) — add `_unit_for`, `_mol_class`, `_annotate`; enrich `list_observables` (SQLite + zarr); add `_zarr_vector`, `get_vector`.
- `vivarium_dashboard/server.py` (modify) — add `/api/explorer/vector` handler + dispatch.
- `vivarium_dashboard/static/explorer.js` (modify) — expose `_obsOptions`; leave `Views` as stubs (impls move out).
- `vivarium_dashboard/static/explorer-views.js` (new) — the four view implementations (`timeseries`, `scatter`, `allocation`, `flux`).
- `vivarium_dashboard/templates/index.html.j2` + `static/explorer.html` (modify) — load `explorer-views.js` after `explorer.js`.
- `vivarium_dashboard/static/style.css` (modify) — styles for class filter, search, stacked panels, scatter, Voronoi labels/breadcrumb.
- `tests/test_explorer_data.py` (modify) — unit tests for tagging + `get_vector`.
- `docs/data-explorer.md` (modify) — document v2 views.

---

## Task 1: Backend — unit + molecule-class tagging

**Files:**
- Modify: `vivarium_dashboard/lib/explorer_data.py`
- Modify: `tests/test_explorer_data.py`

**Interfaces:**
- Produces: `_unit_for(path: str) -> str`, `_mol_class(path: str) -> str`, `_annotate(leaf: dict) -> dict`. `list_observables` entries gain `"unit"` and `"mclass"` keys (SQLite + zarr).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_explorer_data.py`:

```python
def test_unit_and_class_helpers():
    assert explorer_data._unit_for("listeners.mass.protein_mass") == "fg"
    assert explorer_data._unit_for("listeners.mass.protein_mass_fraction") == ""
    assert explorer_data._unit_for("listeners.fba_results.base_reaction_fluxes") == "mmol·s⁻¹"
    assert explorer_data._unit_for("listeners.monomer_counts") == "counts"
    assert explorer_data._unit_for("bulk[GLC]") == "counts"
    assert explorer_data._mol_class("listeners.rna_counts.mRNA_counts") == "RNA"
    assert explorer_data._mol_class("listeners.monomer_counts") == "Protein"
    assert explorer_data._mol_class("bulk[GLC]") == "Metabolite"
    assert explorer_data._mol_class("listeners.fba_results.base_reaction_fluxes") == "Flux"
    assert explorer_data._mol_class("listeners.mass.cell_mass") == "Mass"


def test_list_observables_carries_unit_and_class(tmp_path):
    db = tmp_path / "runs.db"
    make_fake_runs_db(db, _sample_states())
    obs = explorer_data.list_observables(str(db))
    flat = [o for g in obs["categories"].values() for o in g]
    assert flat and all("unit" in o and "mclass" in o for o in flat)
    mass = [o for o in flat if o["path"].endswith("mass.cell_mass")][0]
    assert mass["unit"] == "fg" and mass["mclass"] == "Mass"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_explorer_data.py::test_unit_and_class_helpers -v`
Expected: FAIL — `AttributeError: ... '_unit_for'`.

- [ ] **Step 3: Implement the helpers + apply them**

Add to `explorer_data.py` (near `_category_for`):

```python
def _unit_for(path: str) -> str:
    """Physical unit for an observable, inferred from its path (units live in
    listener port schemas, not in the emitted payload)."""
    p = path.lower()
    if "fraction" in p or "ratio" in p or "growth_rate" in p:
        return ""
    if "_mass" in p or p.endswith("mass"):
        return "fg"
    if "fba_results" in p or "flux" in p:
        return "mmol·s⁻¹"
    if "rna_counts" in p or "monomer_counts" in p or p.startswith("bulk["):
        return "counts"
    return ""


def _mol_class(path: str) -> str:
    """Molecule class for an observable, inferred from its path."""
    p = path.lower()
    if "rna_counts" in p:
        return "RNA"
    if "monomer_counts" in p:
        return "Protein"
    if p.startswith("bulk["):
        return "Metabolite"
    if "fba_results" in p or "flux" in p:
        return "Flux"
    if "mass" in p:
        return "Mass"
    return "Other"


def _annotate(leaf: dict) -> dict:
    leaf["unit"] = _unit_for(leaf["path"])
    leaf["mclass"] = _mol_class(leaf["path"])
    return leaf
```

In `_zarr_observables`, annotate each leaf — change the append to:

```python
        leaves.append(_annotate({"path": leaf, "index": 0 if is_vec else None,
                       "label": leaf, "kind": "vector" if is_vec else "scalar"}))
```

In `list_observables` (the SQLite path), after the `leaves` list is fully built
and before grouping into categories, add:

```python
    for _leaf in leaves:
        _annotate(_leaf)
```

(Place it right after the `for top_key, sub in inner.items(): _walk(...)` loop that
fills `leaves`.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_explorer_data.py::test_unit_and_class_helpers tests/test_explorer_data.py::test_list_observables_carries_unit_and_class -v`
Expected: PASS.

- [ ] **Step 5: Run the full file (no regressions)**

Run: `python -m pytest tests/test_explorer_data.py -q`
Expected: PASS (all existing + 2 new).

- [ ] **Step 6: Commit**

```bash
git add vivarium_dashboard/lib/explorer_data.py tests/test_explorer_data.py
git commit -m "feat(explorer): tag observables with unit + molecule class"
```

---

## Task 2: Backend — `get_vector` + `/api/explorer/vector`

**Files:**
- Modify: `vivarium_dashboard/lib/explorer_data.py`
- Modify: `tests/test_explorer_data.py`
- Modify: `vivarium_dashboard/server.py`

**Interfaces:**
- Consumes: `_resolve_run_source`, `_state_at_step`, `_dig`, `_unwrap_agent`, `_NUM` (existing).
- Produces: `get_vector(db_path, path, step, run_id=None, workspace=None) -> {"ids": [...], "values": [...], "step": int, "time": float|None}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_explorer_data.py`:

```python
def test_get_vector_sqlite_by_index(tmp_path):
    db = tmp_path / "runs.db"
    make_fake_runs_db(db, _sample_states(n=4))
    # step 2 base_reaction_fluxes == [3.0, 4.0, 5.0]
    res = explorer_data.get_vector(str(db),
        "listeners.fba_results.base_reaction_fluxes", step=2)
    assert res["values"] == [3.0, 4.0, 5.0]
    assert res["ids"] == ["0", "1", "2"]


def test_get_vector_zarr_by_coord(tmp_path):
    run = tmp_path / ".pbg" / "runs" / "r1"; run.mkdir(parents=True)
    make_fake_zarr(run / "store.zarr")  # base_reaction_fluxes vector w/ id coord
    res = explorer_data.get_vector(".pbg/runs/r1", "base_reaction_fluxes",
                                   step=2, workspace=tmp_path)
    assert res["ids"] == ["RXN-A", "RXN-B", "RXN-C"]
    assert res["values"] == [3.0, 4.0, 5.0]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_explorer_data.py -k get_vector -v`
Expected: FAIL — `AttributeError: ... 'get_vector'`.

- [ ] **Step 3: Implement `_zarr_vector` + `get_vector`**

Add to `explorer_data.py`:

```python
def _zarr_vector(store, leaf, step):
    """(ids, values) for one vector leaf at one emit step, ids from id_<leaf>."""
    try:
        import xarray as xr
    except ImportError:
        return [], []
    try:
        dt = xr.open_datatree(str(store), engine="zarr")
    except Exception:
        return [], []
    for node in dt.subtree:
        if node.name != leaf:
            continue
        gen_vars = sorted((v for v in (node.data_vars or {})
                           if str(v).startswith("generation=")),
                          key=lambda s: int(str(s).split("=")[1]))
        if not gen_vars:
            return [], []
        arr = node[gen_vars[0]]
        idcoord = "id_" + leaf
        if idcoord not in arr.dims:
            return [], []
        ids = ([str(x) for x in node[idcoord].values]
               if idcoord in node.coords
               else [str(i) for i in range(arr.sizes[idcoord])])
        emitdim = [d for d in arr.dims if d != idcoord]
        if not emitdim:
            return [], []
        nstep = arr.sizes[emitdim[0]]
        si = min(max(0, step), nstep - 1)
        vals = arr.isel({emitdim[0]: si}).values.tolist()
        return ids, [float(x) for x in vals]
    return [], []


def get_vector(db_path, path, step, run_id=None, workspace=None):
    """One vector observable's per-entity (ids, values) at a timepoint.
    zarr: ids from the id_<leaf> coord. sqlite: positional index ids."""
    kind, resolved = _resolve_run_source(db_path, workspace)
    if kind == "zarr":
        leaf = path.split(".")[-1].split("[")[0]
        ids, vals = _zarr_vector(resolved, leaf, step)
        return {"ids": ids, "values": vals, "step": step, "time": None}
    if kind == "sqlite":
        time, state = _state_at_step(resolved, step, run_id)
        vec = _dig(_unwrap_agent(state), path) if state is not None else None
        if isinstance(vec, list) and all(isinstance(x, _NUM) for x in vec):
            return {"ids": [str(i) for i in range(len(vec))],
                    "values": [float(x) for x in vec], "step": step, "time": time}
        return {"ids": [], "values": [], "step": step, "time": time}
    return {"ids": [], "values": [], "step": step, "time": None}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_explorer_data.py -k get_vector -v`
Expected: PASS (2).

- [ ] **Step 5: Add the endpoint + dispatch**

In `server.py` `do_GET`, after the `/api/explorer/flux` dispatch:

```python
        if self.path.startswith("/api/explorer/vector"):
            return self._get_explorer_vector()
```

Handler (next to `_get_explorer_flux`):

```python
    def _get_explorer_vector(self):
        """GET /api/explorer/vector?db=&run=&path=&step="""
        import urllib.parse as _up
        from vivarium_dashboard.lib import explorer_data
        q = dict(_up.parse_qsl(_up.urlparse(self.path).query))
        db = q.get("db"); path = q.get("path")
        if not db or not path:
            return self._json({"error": "missing db/path", "ids": [], "values": []}, 200)
        try:
            step = int(q.get("step", "0"))
        except ValueError:
            step = 0
        try:
            return self._json(
                explorer_data.get_vector(db, path, step, q.get("run"), WORKSPACE), 200)
        except Exception as e:
            return self._json({"error": str(e), "ids": [], "values": []}, 200)
```

- [ ] **Step 6: Run the full file**

Run: `python -m pytest tests/test_explorer_data.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add vivarium_dashboard/lib/explorer_data.py tests/test_explorer_data.py vivarium_dashboard/server.py
git commit -m "feat(explorer): /api/explorer/vector snapshot endpoint"
```

---

## Task 3: Frontend — split views out + expose helpers

**Files:**
- Modify: `vivarium_dashboard/static/explorer.js`
- Create: `vivarium_dashboard/static/explorer-views.js`
- Modify: `vivarium_dashboard/templates/index.html.j2`, `vivarium_dashboard/static/explorer.html`

**Interfaces:**
- Produces: `window.Explorer` exposes `_state`, `_api`, `_j`, `_obsOptions`, `_Views`. `explorer-views.js` assigns the four real view fns into `window.Explorer._Views`.

- [ ] **Step 1: Expose `_obsOptions` and keep `Views` stubs in `explorer.js`**

In `explorer.js`, change the export line to add `_obsOptions`:

```javascript
  window.Explorer = { mount: mount, _state: state, _api: api, _j: j,
                      _obsOptions: observableOptions, _Views: Views };
```

Move the four view function BODIES out of `explorer.js` into Task Step 2's new
file. In `explorer.js`, replace the four real `Views.*` implementations with the
original stubs so the file still parses and `renderView` has something to call:

```javascript
  var Views = {
    timeseries: function (h) { h.textContent = "timeseries (loading…)"; },
    scatter: function (h) { h.textContent = "scatter (loading…)"; },
    allocation: function (h) { h.textContent = "allocation (loading…)"; },
    flux: function (h) { h.textContent = "flux (loading…)"; }
  };
```

(`observableOptions` STAYS in `explorer.js` — it's exposed via `_obsOptions`.)

- [ ] **Step 2: Create `explorer-views.js` with the current four views (moved verbatim)**

Create `vivarium_dashboard/static/explorer-views.js`. Capture the controller and
re-bind the helpers the views use, then assign the FOUR CURRENT view
implementations (copy them verbatim from the pre-split `explorer.js`), rewriting
their internal helper references to the exposed ones:

```javascript
/* Data Explorer views — split out of explorer.js to keep each file focused.
   Assigns into window.Explorer._Views (the object renderView dispatches through). */
(function () {
  "use strict";
  var E = window.Explorer;
  if (!E) return;
  var api = E._api, j = E._j, state = E._state, observableOptions = E._obsOptions;
  var V = E._Views;

  V.timeseries = function (host, ctrls) { /* current timeseries body */ };
  V.scatter = function (host, ctrls) { /* current scatter body */ };
  V.allocation = function (host, ctrls) { /* current allocation body */ };
  V.flux = function (host, ctrls) { /* current flux body */ };
})();
```

Each `/* current ... body */` must be the EXACT body from the pre-split
`explorer.js`, with any `observableOptions(...)` / `api(...)` / `j(...)` /
`state.*` calls now resolving to the captured locals above (they already use
those names, so no rewrite is needed beyond the capture). Tasks 4–6 then replace
`V.timeseries`, `V.scatter`, `V.allocation` with the new implementations.

- [ ] **Step 3: Load `explorer-views.js` after `explorer.js` in both hosts**

In `templates/index.html.j2`, immediately AFTER the `assets/explorer.js` include:

```html
    <script src="assets/explorer-views.js{% if asset_version %}?v={{ asset_version }}{% endif %}" onerror="/* explorer views unavailable */"></script>
```

In `static/explorer.html`, after the `<script src="explorer.js"></script>` line:

```html
  <script src="explorer-views.js"></script>
```

- [ ] **Step 4: Verify syntax + serving**

Run:
```bash
cd /Users/eranagmon/code/vdash-explorer
node --check vivarium_dashboard/static/explorer.js
node --check vivarium_dashboard/static/explorer-views.js
```
Expected: both report no syntax errors.

Then (server is or can be started per the verify pattern):
```bash
curl -s -o /dev/null -w "views.js:%{http_code}\n" http://localhost:8799/assets/explorer-views.js
```
Expected: `views.js:200` (start the server first if needed — see Task 6 verify block).

- [ ] **Step 5: Commit**

```bash
git add vivarium_dashboard/static/explorer.js vivarium_dashboard/static/explorer-views.js vivarium_dashboard/templates/index.html.j2 vivarium_dashboard/static/explorer.html
git commit -m "refactor(explorer): split views into explorer-views.js"
```

---

## Task 4: Timeseries — class filter, search, stacked unit panels

**Files:**
- Modify: `vivarium_dashboard/static/explorer-views.js`
- Modify: `vivarium_dashboard/static/style.css`

**Interfaces:**
- Consumes: `E._obsOptions()` returns `[{key, label, kind, len, unit, mclass}]` (extend `observableOptions` in `explorer.js` to pass through `unit`/`mclass`); `GET /api/explorer/series`; Plotly.

- [ ] **Step 1: Extend `observableOptions` to carry unit + mclass**

In `explorer.js`, update `observableOptions` so each option includes `unit` and
`mclass` from the observable dict:

```javascript
  function observableOptions() {
    var opts = [];
    Object.keys(state.observables).forEach(function (cat) {
      state.observables[cat].forEach(function (o) {
        var key = o.path + (o.index != null ? "#" + o.index : "");
        opts.push({ key: key, label: cat + " · " + o.label, kind: o.kind,
                    len: o.length, unit: o.unit || "", mclass: o.mclass || "Other" });
      });
    });
    return opts;
  }
```

- [ ] **Step 2: Replace `V.timeseries` with the unit-grouped implementation**

In `explorer-views.js`, set:

```javascript
  V.timeseries = function (host, ctrls) {
    var opts = observableOptions();
    var classes = ["All", "RNA", "Protein", "Metabolite", "Flux", "Mass", "Other"];
    ctrls.innerHTML =
      '<label>Class <select id="ts-class">' +
        classes.map(function (c) { return '<option>' + c + "</option>"; }).join("") +
      "</select></label>" +
      '<label>Search <input id="ts-search" type="text" placeholder="filter…"></label>' +
      '<label>Observables <select id="ts-obs" multiple size="10"></select></label>' +
      '<label><input type="checkbox" id="ts-log"> log y</label>' +
      '<label><input type="checkbox" id="ts-norm"> normalize</label>';
    host.innerHTML = '<div id="ts-chart" style="height:520px"></div>';

    function refreshList() {
      var cls = ctrls.querySelector("#ts-class").value;
      var q = ctrls.querySelector("#ts-search").value.toLowerCase();
      var sel = ctrls.querySelector("#ts-obs");
      var chosen = {};
      Array.prototype.forEach.call(sel.selectedOptions, function (o) { chosen[o.value] = 1; });
      sel.innerHTML = opts.filter(function (o) {
        return (cls === "All" || o.mclass === cls) &&
               (!q || o.label.toLowerCase().indexOf(q) >= 0);
      }).map(function (o) {
        return '<option value="' + o.key + '"' + (chosen[o.key] ? " selected" : "") +
               ">" + o.label + " [" + (o.unit || "–") + "]</option>";
      }).join("");
    }

    function draw() {
      var chosen = Array.prototype.map.call(
        ctrls.querySelectorAll("#ts-obs option:checked"), function (o) { return o.value; });
      if (!chosen.length) { Plotly.purge("ts-chart"); return; }
      var unitOf = {};
      opts.forEach(function (o) { unitOf[o.key] = o.unit || ""; });
      var u = api("/series?db=" + encodeURIComponent(state.run.db_path) +
                  "&run=" + encodeURIComponent(state.run.run_id || "") +
                  "&paths=" + encodeURIComponent(chosen.join(",")));
      j(u).then(function (d) {
        var norm = ctrls.querySelector("#ts-norm").checked;
        var log = ctrls.querySelector("#ts-log").checked;
        // distinct units → one stacked panel each
        var units = [];
        chosen.forEach(function (k) { var un = unitOf[k] || "(unitless)";
          if (units.indexOf(un) < 0) units.push(un); });
        var n = units.length, traces = [], layout = {
          margin: { t: 10, r: 10 }, paper_bgcolor: "#0e1116", plot_bgcolor: "#0e1116",
          font: { color: "#cfd6df" }, showlegend: true,
          grid: { rows: n, columns: 1, pattern: "independent", roworder: "top to bottom" }
        };
        units.forEach(function (un, i) {
          var ax = i === 0 ? "y" : "y" + (i + 1);
          layout[i === 0 ? "yaxis" : "yaxis" + (i + 1)] =
            { title: un, type: log ? "log" : "linear" };
        });
        Object.keys(d.series).forEach(function (k) {
          var un = unitOf[k] || "(unitless)", i = units.indexOf(un);
          var y = d.series[k];
          if (norm) { var m = Math.max.apply(null, y.map(Math.abs)) || 1; y = y.map(function (v) { return v / m; }); }
          traces.push({ type: "scatter", mode: "lines", name: k, x: d.time, y: y,
                        xaxis: "x", yaxis: i === 0 ? "y" : "y" + (i + 1) });
        });
        Plotly.react("ts-chart", traces, layout, { responsive: true });
      });
    }

    ctrls.querySelector("#ts-class").addEventListener("change", refreshList);
    ctrls.querySelector("#ts-search").addEventListener("input", refreshList);
    ctrls.querySelector("#ts-obs").addEventListener("change", draw);
    ctrls.querySelector("#ts-log").addEventListener("change", draw);
    ctrls.querySelector("#ts-norm").addEventListener("change", draw);
    refreshList();
  };
```

- [ ] **Step 3: Add CSS for the wider rail control set**

In `style.css`, append:

```css
.exp-rail #ts-obs, .exp-rail #ts-search { width:100%; }
.exp-rail #ts-obs { height:220px; }
```

- [ ] **Step 4: Verify syntax + serving**

Run: `node --check vivarium_dashboard/static/explorer-views.js && echo OK`
Expected: OK. Then visually: select observables of mixed units (e.g. a `*_mass` and a `*counts`) → two stacked panels appear, each y-axis titled with its unit; class filter + search narrow the list.

- [ ] **Step 5: Commit**

```bash
git add vivarium_dashboard/static/explorer.js vivarium_dashboard/static/explorer-views.js vivarium_dashboard/static/style.css
git commit -m "feat(explorer): timeseries class filter + search + stacked unit panels"
```

---

## Task 5: Scatter — run-vs-run omics

**Files:**
- Modify: `vivarium_dashboard/static/explorer-views.js`

**Interfaces:**
- Consumes: `GET /api/explorer/vector`; `state.runs`; Plotly. Class → vector path map.

- [ ] **Step 1: Replace `V.scatter` with the run-vs-run implementation**

```javascript
  V.scatter = function (host, ctrls) {
    // class -> the vector observable path that represents it
    var CLASS_PATH = {
      Protein: "listeners.monomer_counts",
      RNA: "listeners.rna_counts.mRNA_counts",
      Flux: "listeners.fba_results.base_reaction_fluxes"
    };
    var classes = Object.keys(CLASS_PATH);
    if (state.runs.length < 2) {
      ctrls.innerHTML = "";
      host.innerHTML = '<p class="muted" style="padding:12px">Run-vs-run scatter ' +
        'needs at least two runs in this workspace.</p>';
      return;
    }
    function runOpts(sel) {
      return state.runs.map(function (r) {
        return '<option value="' + r.run_id + '"' +
          (r.run_id === sel ? " selected" : "") + ">" + (r.label || r.run_id) + "</option>";
      }).join("");
    }
    var a0 = state.runs[0].run_id, b0 = state.runs[1].run_id;
    ctrls.innerHTML =
      '<label>Class <select id="sc-class">' +
        classes.map(function (c) { return "<option>" + c + "</option>"; }).join("") +
      "</select></label>" +
      '<label>Run A (x) <select id="sc-a">' + runOpts(a0) + "</select></label>" +
      '<label>Run B (y) <select id="sc-b">' + runOpts(b0) + "</select></label>" +
      '<label>Step <input id="sc-step" type="range" min="0" max="0" value="0"></label>' +
      '<label><input type="checkbox" id="sc-log" checked> log-log</label>';
    host.innerHTML = '<div id="sc-chart" style="height:520px"></div>';

    function runById(id) { return state.runs.find(function (r) { return r.run_id === id; }); }

    function draw() {
      var cls = ctrls.querySelector("#sc-class").value;
      var path = CLASS_PATH[cls];
      var ra = runById(ctrls.querySelector("#sc-a").value);
      var rb = runById(ctrls.querySelector("#sc-b").value);
      var step = parseInt(ctrls.querySelector("#sc-step").value, 10) || 0;
      function vec(r) {
        return j(api("/vector?db=" + encodeURIComponent(r.db_path) +
                     "&run=" + encodeURIComponent(r.run_id || "") +
                     "&path=" + encodeURIComponent(path) + "&step=" + step));
      }
      Promise.all([vec(ra), vec(rb)]).then(function (res) {
        var A = res[0], B = res[1];
        // join by id when both provide ids, else by index
        var mapA = {}; A.ids.forEach(function (id, i) { mapA[id] = A.values[i]; });
        var xs = [], ys = [], labels = [];
        B.ids.forEach(function (id, i) {
          if (id in mapA) { xs.push(mapA[id]); ys.push(B.values[i]); labels.push(id); }
        });
        var log = ctrls.querySelector("#sc-log").checked;
        var lo = 1, hi = 1;
        xs.concat(ys).forEach(function (v) { if (v > hi) hi = v; if (v > 0 && v < lo) lo = v; });
        var trace = { type: "scattergl", mode: "markers", x: xs, y: ys, text: labels,
          hovertemplate: "%{text}<br>A=%{x:.3g} B=%{y:.3g}<extra></extra>",
          marker: { size: 5, opacity: 0.6, color: "#4c8bf5" } };
        var diag = { type: "scatter", mode: "lines", x: [lo, hi], y: [lo, hi],
          line: { color: "#888", dash: "dot" }, hoverinfo: "skip", showlegend: false };
        Plotly.react("sc-chart", [trace, diag], {
          margin: { t: 10, r: 10 }, paper_bgcolor: "#0e1116", plot_bgcolor: "#0e1116",
          font: { color: "#cfd6df" },
          xaxis: { title: (ra.label || ra.run_id) + " (" + cls + ")", type: log ? "log" : "linear" },
          yaxis: { title: (rb.label || rb.run_id), type: log ? "log" : "linear" }
        }, { responsive: true });
      });
    }
    // set step slider max from the larger run's n_steps
    var maxStep = Math.max(0, ((runById(a0).n_steps || 1) - 1));
    ctrls.querySelector("#sc-step").max = String(maxStep);
    ctrls.querySelector("#sc-step").value = String(maxStep);  // default final step
    ["sc-class", "sc-a", "sc-b", "sc-step", "sc-log"].forEach(function (id) {
      ctrls.querySelector("#" + id).addEventListener("change", draw);
    });
    draw();
  };
```

- [ ] **Step 2: Verify syntax**

Run: `node --check vivarium_dashboard/static/explorer-views.js && echo OK`
Expected: OK. Visual (needs ≥2 runs): each point an entity, X=run A vs Y=run B, dotted y=x diagonal, log-log default; with <2 runs the view shows the "needs two runs" note.

- [ ] **Step 3: Commit**

```bash
git add vivarium_dashboard/static/explorer-views.js
git commit -m "feat(explorer): run-vs-run omics scatter"
```

---

## Task 6: Allocation — labeled drill-down mass Voronoi

**Files:**
- Modify: `vivarium_dashboard/static/explorer-views.js`
- Modify: `vivarium_dashboard/static/style.css`

**Interfaces:**
- Consumes: `GET /api/explorer/series`; `state.observables`; `d3`, `d3.voronoiTreemap`.

- [ ] **Step 1: Replace `V.allocation` with the drill-down implementation**

```javascript
  V.allocation = function (host, ctrls) {
    // static mass hierarchy (intersected with what the run emits)
    var MASS_TREE = {
      "cell_mass": ["protein_mass", "rna_mass", "dna_mass", "smallMolecule_mass", "water_mass"],
      "rna_mass": ["rRna_mass", "tRna_mass", "mRna_mass"]
    };
    // available mass observable paths in this run (path endswith the field name)
    var massPaths = {};
    Object.keys(state.observables).forEach(function (cat) {
      state.observables[cat].forEach(function (o) {
        if ((o.mclass === "Mass") || /_mass$/.test(o.path)) {
          var field = o.path.split(".").pop();
          massPaths[field] = o.path;
        }
      });
    });
    function childrenOf(field) {
      return (MASS_TREE[field] || []).filter(function (f) { return massPaths[f]; });
    }
    var path = ["cell_mass"];               // breadcrumb of fields
    var cache = { time: [], byField: {} };

    function currentChildren() {
      var node = path[path.length - 1];
      var kids = childrenOf(node);
      return kids.length ? kids : [node];   // leaf → show itself
    }

    function load() {
      var fields = currentChildren();
      var paths = fields.map(function (f) { return massPaths[f]; }).filter(Boolean);
      if (!paths.length) { render(); return; }
      var u = api("/series?db=" + encodeURIComponent(state.run.db_path) +
                  "&run=" + encodeURIComponent(state.run.run_id || "") +
                  "&paths=" + encodeURIComponent(paths.join(",")));
      j(u).then(function (d) {
        cache.time = d.time; cache.byField = {};
        fields.forEach(function (f) { cache.byField[f] = d.series[massPaths[f]] || []; });
        var slider = ctrls.querySelector("#al-t");
        slider.max = Math.max(0, d.time.length - 1); slider.value = slider.max;
        render();
      });
    }

    function render() {
      ctrls.innerHTML =
        '<div class="al-crumb">' + path.map(function (f, i) {
          return '<span class="al-bc" data-i="' + i + '">' + f.replace(/_mass$/, "") + "</span>";
        }).join(" ▸ ") + "</div>" +
        '<label>Time <input id="al-t" type="range" min="0" max="' +
          Math.max(0, cache.time.length - 1) + '" value="' +
          Math.max(0, cache.time.length - 1) + '"></label>' +
        '<span id="al-tlabel" class="muted"></span>' +
        '<p class="muted" style="font-size:0.78em">double-click a cell to break it down</p>';
      ctrls.querySelectorAll(".al-bc").forEach(function (el) {
        el.addEventListener("click", function () {
          path = path.slice(0, parseInt(el.getAttribute("data-i"), 10) + 1); load();
        });
      });
      ctrls.querySelector("#al-t").addEventListener("input", draw);
      host.innerHTML = '<svg id="al-svg" width="520" height="520"></svg>';
      draw();
    }

    function draw() {
      var ti = parseInt(ctrls.querySelector("#al-t").value, 10) || 0;
      ctrls.querySelector("#al-tlabel").textContent =
        cache.time.length ? "t = " + (cache.time[ti] != null ? cache.time[ti].toFixed(1) : ti) : "";
      var fields = currentChildren();
      var leaves = fields.map(function (f) {
        return { name: f.replace(/_mass$/, ""), field: f,
                 value: Math.abs((cache.byField[f] || [])[ti] || 0) };
      }).filter(function (d) { return d.value > 0; });
      var svg = d3.select("#al-svg"); svg.selectAll("*").remove();
      if (!leaves.length) return;
      var W = 520, H = 520, R = 250, cx = W / 2, cy = H / 2, circle = [];
      for (var a = 0; a < 2 * Math.PI; a += Math.PI / 60)
        circle.push([cx + R * Math.cos(a), cy + R * Math.sin(a)]);
      var total = leaves.reduce(function (s, d) { return s + d.value; }, 0) || 1;
      var root = d3.hierarchy({ children: leaves }).sum(function (d) { return d.value; });
      d3.voronoiTreemap().clip(circle)(root);
      var color = d3.scaleOrdinal(d3.schemeCategory10);
      var cells = svg.selectAll("g").data(root.leaves()).enter().append("g");
      cells.append("path")
        .attr("d", function (d) { return "M" + d.polygon.join("L") + "Z"; })
        .attr("fill", function (d) { return color(d.data.name); })
        .attr("stroke", "#0e1116").attr("stroke-width", 1.5)
        .style("cursor", "pointer")
        .on("dblclick", function (ev, d) {
          if (childrenOf(d.data.field).length) { path.push(d.data.field); load(); }
        })
        .append("title").text(function (d) {
          return d.data.name + ": " + d.data.value.toFixed(2) + " fg (" +
                 (100 * d.data.value / total).toFixed(1) + "%)"; });
      cells.append("text")
        .attr("x", function (d) { return d.polygon.site.x; })
        .attr("y", function (d) { return d.polygon.site.y; })
        .attr("text-anchor", "middle").attr("fill", "#fff")
        .attr("font-size", "12px").style("pointer-events", "none")
        .text(function (d) {
          return (100 * d.data.value / total) > 4 ? d.data.name : ""; });
    }

    load();
  };
```

- [ ] **Step 2: Add CSS for the breadcrumb**

In `style.css`, append:

```css
.al-crumb { font-size:0.85em; color:#cfd6df; margin-bottom:6px; }
.al-bc { cursor:pointer; color:#9db4d6; }
.al-bc:hover { color:#fff; text-decoration:underline; }
```

- [ ] **Step 3: Verify syntax**

Run: `node --check vivarium_dashboard/static/explorer-views.js && echo OK`
Expected: OK. Visual: cells labeled with submass names + %; single-click highlights (via cursor), double-click on `rna` descends to rRNA/tRNA/mRNA; breadcrumb returns up; time slider scrubs.

- [ ] **Step 4: Commit**

```bash
git add vivarium_dashboard/static/explorer-views.js vivarium_dashboard/static/style.css
git commit -m "feat(explorer): labeled drill-down mass voronoi"
```

---

## Task 7: Docs + integration sweep

**Files:**
- Modify: `docs/data-explorer.md`
- Modify: `tests/test_explorer_endpoints.py`

- [ ] **Step 1: Extend the end-to-end test for the vector endpoint**

Append to `tests/test_explorer_endpoints.py`:

```python
def test_vector_flow(tmp_path):
    from tests.test_explorer_data import make_fake_runs_db, _sample_states
    studies = tmp_path / "studies" / "demo"; studies.mkdir(parents=True)
    db = studies / "runs.db"
    make_fake_runs_db(db, _sample_states(n=4))
    res = explorer_data.get_vector(str(db),
        "listeners.fba_results.base_reaction_fluxes", step=1)
    assert res["values"] and res["ids"]
```

- [ ] **Step 2: Run the whole explorer suite**

Run: `python -m pytest tests/test_explorer_data.py tests/test_explorer_endpoints.py -q`
Expected: PASS.

- [ ] **Step 3: Update the feature doc**

In `docs/data-explorer.md`, update the views section: Timeseries (class filter +
search + stacked unit panels), Scatter (run-vs-run omics, y=x diagonal, ids/index
join), Allocation (labeled drill-down mass Voronoi); add the `/api/explorer/vector`
endpoint and note units/`mclass` are path-derived and the experimental-omics
scatter is deferred.

- [ ] **Step 4: Commit**

```bash
git add docs/data-explorer.md tests/test_explorer_endpoints.py
git commit -m "docs+test(explorer): v2 views + vector end-to-end"
```

---

## Self-Review

**Spec coverage:**
- Unit + `mclass` tagging (SQLite + zarr) → Task 1. ✓
- `/api/explorer/vector` → Task 2. ✓
- Views split into `explorer-views.js` → Task 3. ✓
- Timeseries: class filter + search + stacked-per-unit panels → Task 4. ✓
- Scatter: run-vs-run omics, y=x diagonal, id/index join, step slider default final → Task 5. ✓
- Allocation: labeled cells, single-select (cursor)/double-click drill, breadcrumb, mass tree ∩ available → Task 6. ✓
- Flux unchanged (moved verbatim in Task 3). ✓
- Deferred: sim-vs-experiment, parquet — not in any task, matches spec non-goals. ✓
- Tests: tagging + get_vector unit tests (Tasks 1/2), end-to-end (Task 7); frontend code-verified. ✓

**Placeholder scan:** Task 3 Step 2 intentionally references "current ... body" — that means *copy the exact existing code*, with the verbatim source available in the pre-split `explorer.js` git state; this is a move, not a placeholder. All new code (helpers, get_vector, the three rewritten views) is given in full.

**Type consistency:** `observableOptions()` option shape `{key,label,kind,len,unit,mclass}` (Task 4 Step 1) is consumed by Task 4's timeseries (`o.unit`, `o.mclass`) and the class filter; `list_observables` entries gain `unit`/`mclass` in Task 1 and feed `observableOptions` + the Voronoi `o.mclass` check (Task 6). `get_vector` return `{ids,values,step,time}` (Task 2) is consumed by the scatter (`A.ids/A.values`) in Task 5. `CLASS_PATH` keys (Protein/RNA/Flux) match `_mol_class` outputs. Consistent.

**Risk (carried from spec):** run-vs-run scatter needs ≥2 runs (Task 5 shows a note otherwise); SQLite vectors join by index when ids absent; unit strings are heuristic. All surfaced in-UI or documented.
