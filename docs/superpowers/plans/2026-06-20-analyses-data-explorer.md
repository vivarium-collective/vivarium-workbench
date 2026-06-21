# Analyses Data Explorer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a third Analyses-page card — a native, marimo-style reactive explorer for v2ecoli runs with Timeseries, Scatter, polygonal-Voronoi Allocation, and Escher-style Flux-map views.

**Architecture:** Thin Python HTTP + JSON endpoints in a new `lib/explorer_data.py` (handlers in `server.py` delegate to it, reusing existing run readers); a self-contained vanilla-JS controller in `static/explorer.js` rendering with Plotly + d3-voronoi-treemap + escher.js (all CDN). A one-shot `scripts/build_explorer_assets.py` generates the Escher map + reaction-ID-map static assets.

**Tech Stack:** Python stdlib (`http.server`, `sqlite3`, `json`, `urllib`), numpy, existing dashboard reader modules; client = vanilla JS, Plotly 2.27 (already loaded), d3 v7, d3-voronoi-treemap, escher.js (all via CDN); `cobra` (already in the v2ecoli venv) for asset generation only.

## Global Constraints

- **No new Python runtime dependencies.** All new runtime libs are client-side via CDN. `cobra` is used only by the offline asset-generator script. (AI-free / lightweight philosophy.)
- **New code in focused modules** — do not bloat `server.py` or `walkthrough.js`. Server logic → `vivarium_dashboard/lib/explorer_data.py`; client → `vivarium_dashboard/static/explorer.js`.
- **Reuse existing readers** — `lib/simulations_index.list_simulations`, `lib/comparative_viz._extract_trace`, `lib/study_charts` extractors. Do not reimplement run discovery or json_extract.
- **Endpoints never raise to the client** — return `self._json({"error": "..."}, <code>)` on failure and empty structures for sparse data, so one bad path never sinks the page.
- **Branch/worktree:** all work on branch `feat/analyses-data-explorer` in worktree `/Users/eranagmon/code/vdash-explorer`.
- **Single-cell scope:** v2ecoli single-cell composites nest listener stores under `agents/0/`; the reused `_extract_trace` already tries both the literal and `agents.0.` paths. New extraction code must preserve that fallback.
- **Run tests with:** `cd /Users/eranagmon/code/vdash-explorer && python -m pytest <path> -v` (use the worktree's interpreter; if a venv is needed, `python -m pytest`).

---

## File Structure

- `vivarium_dashboard/lib/explorer_data.py` (new) — all server-side data prep: run list, observable discovery, series extraction dispatch, flux extraction + ID remap. Pure functions taking a `workspace: Path`.
- `vivarium_dashboard/server.py` (modify) — four thin GET handlers + dispatch lines in `do_GET`.
- `vivarium_dashboard/static/explorer.js` (new) — the `Explorer` controller (mount, run picker, tab shell, four views).
- `vivarium_dashboard/static/walkthrough.js` (modify) — add the third card in `_loadAnalysesPage()`.
- `vivarium_dashboard/templates/index.html.j2` (modify) — load `explorer.js` + CDN libs.
- `vivarium_dashboard/static/explorer/ecoli_core.map.json` (new asset) — Escher e_coli_core map.
- `vivarium_dashboard/static/explorer/reaction_id_map.json` (new asset) — EcoCyc→BiGG reaction id map.
- `vivarium_dashboard/static/explorer/base_reaction_ids.json` (new asset) — ordered base reaction ids (flux vector ordering).
- `scripts/build_explorer_assets.py` (new) — generates the three assets above.
- `tests/test_explorer_data.py` (new) — backend unit tests against a synthetic runs.db.
- `tests/conftest.py` or inline helper — `make_fake_runs_db()` builder.

---

## Task 1: `explorer_data.py` + `/api/explorer/runs`

**Files:**
- Create: `vivarium_dashboard/lib/explorer_data.py`
- Create: `tests/test_explorer_data.py`
- Modify: `vivarium_dashboard/server.py` (do_GET dispatch + handler)

**Interfaces:**
- Consumes: `vivarium_dashboard.lib.simulations_index.list_simulations(workspace: Path) -> list[dict]` (keys include `run_id, sim_name, label, n_steps, status, db_path, source, study_slug, investigation_slug`).
- Produces: `explorer_data.list_runs(workspace: Path) -> list[dict]` returning `[{run_id, label, study, investigation, n_steps, status, db_path, source}]`.

- [ ] **Step 1: Write the failing test for the fake-db helper + list_runs**

Create `tests/test_explorer_data.py`:

```python
import json
import sqlite3
from pathlib import Path

from vivarium_dashboard.lib import explorer_data


def make_fake_runs_db(db_path: Path, states: list[dict], run_id="run-1", name="baseline"):
    """Write a process_bigraph SQLiteEmitter-shaped runs.db with one run."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE simulations (
            simulation_id TEXT PRIMARY KEY, name TEXT,
            started_at TEXT, completed_at TEXT, elapsed_seconds REAL
        );
        CREATE TABLE history (
            simulation_id TEXT, step INTEGER, global_time REAL, state TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO simulations VALUES (?,?,?,?,?)",
        (run_id, name, "2026-01-01T00:00:00", "2026-01-01T00:01:00", 60.0),
    )
    for step, st in enumerate(states):
        conn.execute(
            "INSERT INTO history VALUES (?,?,?,?)",
            (run_id, step, float(step), json.dumps(st)),
        )
    conn.commit()
    conn.close()


def _sample_states(n=5):
    return [
        {
            "agents": {"0": {
                "listeners": {
                    "mass": {"cell_mass": 100.0 + i},
                    "fba_results": {"base_reaction_fluxes": [1.0 + i, 2.0 + i, 3.0 + i]},
                },
                "bulk": [["GLC", 10 + i], ["ATP", 20 + i]],
            }},
        }
        for i in range(n)
    ]


def test_list_runs_returns_run_dicts(tmp_path):
    studies = tmp_path / "studies" / "demo"
    studies.mkdir(parents=True)
    make_fake_runs_db(studies / "runs.db", _sample_states())
    runs = explorer_data.list_runs(tmp_path)
    assert isinstance(runs, list)
    assert any(r["run_id"] == "run-1" for r in runs)
    r = next(r for r in runs if r["run_id"] == "run-1")
    assert {"run_id", "label", "n_steps", "status", "db_path", "source"} <= set(r)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_explorer_data.py::test_list_runs_returns_run_dicts -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'vivarium_dashboard.lib.explorer_data'`.

- [ ] **Step 3: Create `explorer_data.py` with `list_runs`**

Create `vivarium_dashboard/lib/explorer_data.py`:

```python
"""Server-side data prep for the Analyses Data Explorer card.

Thin, pure-Python functions over a workspace's simulation runs. Reuses the
existing run-discovery and trace-extraction readers; adds no new deps.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from vivarium_dashboard.lib import simulations_index
from vivarium_dashboard.lib import comparative_viz


def list_runs(workspace: Path) -> list[dict]:
    """Runs for the explorer's run-picker, projected to a small public shape."""
    out = []
    for r in simulations_index.list_simulations(Path(workspace)):
        studies = r.get("studies") or []
        out.append({
            "run_id": r.get("run_id"),
            "label": r.get("label") or r.get("sim_name") or r.get("run_id"),
            "study": r.get("study_slug") or (studies[0] if studies else None),
            "investigation": r.get("investigation_slug"),
            "n_steps": r.get("n_steps"),
            "status": r.get("status"),
            "db_path": r.get("db_path"),
            "source": r.get("source"),
        })
    return out
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_explorer_data.py::test_list_runs_returns_run_dicts -v`
Expected: PASS.

- [ ] **Step 5: Add the `/api/explorer/runs` handler + dispatch in server.py**

In `vivarium_dashboard/server.py`, inside `do_GET` (near the existing `if self.path.startswith("/api/simulations"):` at ~line 8345), add:

```python
        if self.path.startswith("/api/explorer/runs"):
            return self._get_explorer_runs()
```

Add the handler method to the request-handler class (near `_get_simulations`, ~line 10881):

```python
    def _get_explorer_runs(self):
        """GET /api/explorer/runs — runs for the Data Explorer run-picker."""
        from vivarium_dashboard.lib import explorer_data
        try:
            return self._json({"runs": explorer_data.list_runs(WORKSPACE)}, 200)
        except Exception as e:  # never sink the page
            return self._json({"error": str(e), "runs": []}, 200)
```

- [ ] **Step 6: Write an endpoint contract test**

Append to `tests/test_explorer_data.py`:

```python
def test_list_runs_empty_workspace(tmp_path):
    assert explorer_data.list_runs(tmp_path) == []
```

- [ ] **Step 7: Run the full test file**

Run: `python -m pytest tests/test_explorer_data.py -v`
Expected: PASS (2 tests).

- [ ] **Step 8: Commit**

```bash
git add vivarium_dashboard/lib/explorer_data.py tests/test_explorer_data.py vivarium_dashboard/server.py
git commit -m "feat(explorer): /api/explorer/runs + explorer_data.list_runs"
```

---

## Task 2: `/api/explorer/observables` — discovery + categorization

**Files:**
- Modify: `vivarium_dashboard/lib/explorer_data.py`
- Modify: `tests/test_explorer_data.py`
- Modify: `vivarium_dashboard/server.py`

**Interfaces:**
- Produces: `explorer_data.list_observables(db_path: str, run_id: str|None=None) -> dict` returning `{"categories": {<friendly>: [{"path": str, "index": int|None, "label": str, "kind": "scalar"|"vector"}]}}`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_explorer_data.py`:

```python
def test_list_observables_groups_by_category(tmp_path):
    db = tmp_path / "runs.db"
    make_fake_runs_db(db, _sample_states())
    obs = explorer_data.list_observables(str(db))
    cats = obs["categories"]
    # mass is a scalar leaf under listeners.mass.cell_mass
    assert any(o["path"].endswith("mass.cell_mass") for g in cats.values() for o in g)
    # fba_results.base_reaction_fluxes is a numeric vector
    flux = [o for g in cats.values() for o in g if "base_reaction_fluxes" in o["path"]]
    assert flux and flux[0]["kind"] == "vector"
    # bulk is a list-of-pairs; exposed as a category
    assert "Bulk molecules" in cats or any("bulk" in o["path"] for g in cats.values() for o in g)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_explorer_data.py::test_list_observables_groups_by_category -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'list_observables'`.

- [ ] **Step 3: Implement `list_observables`**

Add to `explorer_data.py`:

```python
# Top-level store key -> friendly category. Order defines display order.
_CATEGORY_MAP = [
    ("mass", "Mass"),
    ("bulk", "Bulk molecules"),
    ("fba_results", "Fluxes"),
    ("listeners", "Listeners"),
    ("growth", "Growth & division"),
]

_NUM = (int, float)


def _unwrap_agent(state: dict) -> dict:
    """v2ecoli single-cell composites nest everything under agents/0/."""
    ag = state.get("agents")
    if isinstance(ag, dict) and "0" in ag and isinstance(ag["0"], dict):
        return ag["0"]
    return state


def _category_for(top_key: str) -> str:
    for key, friendly in _CATEGORY_MAP:
        if top_key == key or top_key.startswith(key):
            return friendly
    return "Other"


def _walk(node, prefix, top_key, out):
    """Collect numeric scalar leaves and numeric vectors as observable dicts."""
    if isinstance(node, dict):
        for k, v in node.items():
            _walk(v, f"{prefix}.{k}" if prefix else k, top_key or k, out)
    elif isinstance(node, list) and node and all(isinstance(x, _NUM) for x in node):
        out.append({"path": prefix, "index": 0, "label": prefix.split(".")[-1],
                    "kind": "vector", "length": len(node)})
    elif isinstance(node, _NUM) and not isinstance(node, bool):
        out.append({"path": prefix, "index": None, "label": prefix.split(".")[-1],
                    "kind": "scalar"})


def _first_state(db_path: str, run_id: str | None) -> dict | None:
    try:
        conn = sqlite3.connect(str(db_path))
    except sqlite3.OperationalError:
        return None
    try:
        tbls = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "history" not in tbls:
            return None
        if run_id:
            row = conn.execute(
                "SELECT state FROM history WHERE simulation_id=? ORDER BY step LIMIT 1",
                (run_id,)).fetchone()
        else:
            row = conn.execute(
                "SELECT state FROM history ORDER BY step LIMIT 1").fetchone()
        return json.loads(row[0]) if row else None
    finally:
        conn.close()


def list_observables(db_path: str, run_id: str | None = None) -> dict:
    state = _first_state(db_path, run_id)
    if not state:
        return {"categories": {}}
    inner = _unwrap_agent(state)
    leaves: list[dict] = []
    for top_key, sub in inner.items():
        _walk(sub, top_key, top_key, leaves)
    categories: dict[str, list] = {}
    for leaf in leaves:
        cat = _category_for(leaf["path"].split(".")[0])
        categories.setdefault(cat, []).append(leaf)
    # stable ordering: by _CATEGORY_MAP order, then Other
    order = [f for _, f in _CATEGORY_MAP] + ["Other"]
    return {"categories": {c: sorted(categories[c], key=lambda o: o["path"])
                           for c in order if c in categories}}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_explorer_data.py::test_list_observables_groups_by_category -v`
Expected: PASS.

- [ ] **Step 5: Add the endpoint + dispatch**

In `server.py` `do_GET`, after the runs dispatch:

```python
        if self.path.startswith("/api/explorer/observables"):
            return self._get_explorer_observables()
```

Handler:

```python
    def _get_explorer_observables(self):
        """GET /api/explorer/observables?db=<path>&run=<id>"""
        import urllib.parse as _up
        from vivarium_dashboard.lib import explorer_data
        q = dict(_up.parse_qsl(_up.urlparse(self.path).query))
        db = q.get("db")
        if not db:
            return self._json({"error": "missing db", "categories": {}}, 200)
        try:
            return self._json(explorer_data.list_observables(db, q.get("run")), 200)
        except Exception as e:
            return self._json({"error": str(e), "categories": {}}, 200)
```

- [ ] **Step 6: Run the full test file**

Run: `python -m pytest tests/test_explorer_data.py -v`
Expected: PASS (3 tests).

- [ ] **Step 7: Commit**

```bash
git add vivarium_dashboard/lib/explorer_data.py tests/test_explorer_data.py vivarium_dashboard/server.py
git commit -m "feat(explorer): /api/explorer/observables with categorized discovery"
```

---

## Task 3: `/api/explorer/series` — multi-path extraction

**Files:**
- Modify: `vivarium_dashboard/lib/explorer_data.py`
- Modify: `tests/test_explorer_data.py`
- Modify: `vivarium_dashboard/server.py`

**Interfaces:**
- Consumes: `comparative_viz._extract_trace(db_path: Path, observable_path: str, observable_index: int|None, subsample: int, sim_name: str|None=None) -> tuple[list[float], list[float]]`.
- Produces: `explorer_data.get_series(db_path: str, paths: list[tuple[str, int|None]], subsample: int=400, run_id: str|None=None) -> dict` returning `{"time": [...], "series": {"<path>[#idx]": [...]}}`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_explorer_data.py`:

```python
def test_get_series_extracts_scalar_and_vector(tmp_path):
    db = tmp_path / "runs.db"
    make_fake_runs_db(db, _sample_states(n=5))
    res = explorer_data.get_series(
        str(db),
        paths=[("listeners.mass.cell_mass", None),
               ("listeners.fba_results.base_reaction_fluxes", 1)],
        subsample=100,
    )
    assert len(res["time"]) == 5
    mass = res["series"]["listeners.mass.cell_mass"]
    assert mass == [100.0, 101.0, 102.0, 103.0, 104.0]
    flux1 = res["series"]["listeners.fba_results.base_reaction_fluxes#1"]
    assert flux1 == [2.0, 3.0, 4.0, 5.0, 6.0]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_explorer_data.py::test_get_series_extracts_scalar_and_vector -v`
Expected: FAIL — `AttributeError: ... 'get_series'`.

- [ ] **Step 3: Implement `get_series`**

Add to `explorer_data.py`:

```python
def _series_key(path: str, index: int | None) -> str:
    return f"{path}#{index}" if index is not None else path


def get_series(db_path: str, paths, subsample: int = 400, run_id: str | None = None) -> dict:
    """Aligned (time, values-per-path). Time comes from the first non-empty path.
    `paths` is a list of (path, index|None). Reuses comparative_viz._extract_trace,
    which already handles the agents/0/ fallback and json_extract subsampling."""
    series: dict[str, list] = {}
    time: list[float] = []
    for path, index in paths:
        t, v = comparative_viz._extract_trace(
            Path(db_path), path, index, subsample, sim_name=None)
        series[_series_key(path, index)] = v
        if not time and t:
            time = t
    return {"time": time, "series": series}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_explorer_data.py::test_get_series_extracts_scalar_and_vector -v`
Expected: PASS.

- [ ] **Step 5: Add the endpoint + dispatch**

`do_GET`:

```python
        if self.path.startswith("/api/explorer/series"):
            return self._get_explorer_series()
```

Handler (parse `paths=a,b#2,c` where `#N` is a vector index):

```python
    def _get_explorer_series(self):
        """GET /api/explorer/series?db=<path>&paths=a,b#2&subsample=N&run=<id>"""
        import urllib.parse as _up
        from vivarium_dashboard.lib import explorer_data
        q = dict(_up.parse_qsl(_up.urlparse(self.path).query))
        db = q.get("db")
        if not db:
            return self._json({"error": "missing db", "time": [], "series": {}}, 200)
        specs = []
        for tok in (q.get("paths") or "").split(","):
            tok = tok.strip()
            if not tok:
                continue
            if "#" in tok:
                p, _, i = tok.partition("#")
                specs.append((p, int(i) if i.isdigit() else None))
            else:
                specs.append((tok, None))
        try:
            sub = int(q.get("subsample", "400"))
        except ValueError:
            sub = 400
        try:
            return self._json(
                explorer_data.get_series(db, specs, sub, q.get("run")), 200)
        except Exception as e:
            return self._json({"error": str(e), "time": [], "series": {}}, 200)
```

- [ ] **Step 6: Run the full test file**

Run: `python -m pytest tests/test_explorer_data.py -v`
Expected: PASS (4 tests).

- [ ] **Step 7: Commit**

```bash
git add vivarium_dashboard/lib/explorer_data.py tests/test_explorer_data.py vivarium_dashboard/server.py
git commit -m "feat(explorer): /api/explorer/series multi-path extraction"
```

---

## Task 4: Asset generator — Escher map + reaction-ID map + base-reaction-ids

**Files:**
- Create: `scripts/build_explorer_assets.py`
- Create: `vivarium_dashboard/static/explorer/reaction_id_map.json` (generated)
- Create: `vivarium_dashboard/static/explorer/base_reaction_ids.json` (generated)
- Create: `vivarium_dashboard/static/explorer/ecoli_core.map.json` (downloaded/embedded)
- Modify: `tests/test_explorer_data.py`

**Interfaces:**
- Produces: three JSON assets. `reaction_id_map.json` = `{"<ecocyc_or_base_id>": "<bigg_id>", ...}`. `base_reaction_ids.json` = `["<base_id>", ...]` (the flux-vector ordering). `ecoli_core.map.json` = the Escher map document (a 2-element `[meta, {reactions, nodes, ...}]` list, per the Escher format).

- [ ] **Step 1: Write the generator script**

Create `scripts/build_explorer_assets.py`:

```python
#!/usr/bin/env python3
"""Generate static assets for the Analyses Data Explorer flux map.

Outputs (under vivarium_dashboard/static/explorer/):
  - ecoli_core.map.json     Escher central-carbon map (BiGG-keyed)
  - reaction_id_map.json    v2ecoli/EcoCyc base reaction id -> BiGG id
  - base_reaction_ids.json  ordered base reaction ids (flux-vector ordering)

Run from a checkout with the v2ecoli venv on PATH (cobra available):
    python scripts/build_explorer_assets.py --ecoli-core path/to/e_coli_core.json
The Escher e_coli_core map ships with the `escher` package or can be fetched
from https://escher.github.io/#/ (Map: e_coli_core.Core metabolism).
"""
import argparse
import json
import shutil
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "vivarium_dashboard" / "static" / "explorer"


def build_id_map():
    """Map base/EcoCyc reaction ids -> BiGG ids using cobra iJO1366 annotations."""
    import cobra
    from cobra.io import load_model
    model = load_model("iJO1366")  # or cobra.io.read_sbml_model on the shipped xml.gz
    id_map = {}
    for rxn in model.reactions:
        bigg = rxn.id
        # cobra annotations often carry biocyc / ecocyc cross-refs
        for key in ("biocyc", "ecocyc", "metanetx.reaction"):
            ref = rxn.annotation.get(key)
            if isinstance(ref, str):
                id_map[ref.split(":")[-1]] = bigg
            elif isinstance(ref, list):
                for r in ref:
                    id_map[r.split(":")[-1]] = bigg
        id_map.setdefault(bigg, bigg)  # identity fallback
    return id_map


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ecoli-core", required=True,
                    help="path to the Escher e_coli_core map JSON")
    ap.add_argument("--base-reaction-ids", default=None,
                    help="optional JSON list of ordered base reaction ids (from sim_data)")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    shutil.copyfile(args.ecoli_core, OUT / "ecoli_core.map.json")

    id_map = build_id_map()
    (OUT / "reaction_id_map.json").write_text(json.dumps(id_map, indent=0))

    base_ids = []
    if args.base_reaction_ids:
        base_ids = json.loads(Path(args.base_reaction_ids).read_text())
    (OUT / "base_reaction_ids.json").write_text(json.dumps(base_ids))

    # Coverage report against the Escher map's reactions
    emap = json.loads((OUT / "ecoli_core.map.json").read_text())
    map_rxns = set()
    if isinstance(emap, list) and len(emap) >= 2:
        for r in emap[1].get("reactions", {}).values():
            map_rxns.add(r.get("bigg_id"))
    mapped = sum(1 for b in base_ids if id_map.get(b) in map_rxns) if base_ids else "n/a"
    print(f"reaction_id_map: {len(id_map)} entries")
    print(f"escher map reactions: {len(map_rxns)}")
    print(f"base ids covered by map: {mapped}/{len(base_ids) or 'n/a'}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Generate the assets**

Run (from a v2ecoli-venv checkout; obtain the e_coli_core map from the escher package or escher.github.io):

```bash
python scripts/build_explorer_assets.py --ecoli-core /path/to/e_coli_core.json
```

Expected: prints a coverage line; three files appear under `vivarium_dashboard/static/explorer/`.

- [ ] **Step 3: Write a test that the assets are valid JSON**

Append to `tests/test_explorer_data.py`:

```python
def test_explorer_assets_are_valid_json():
    import vivarium_dashboard
    base = Path(vivarium_dashboard.__file__).parent / "static" / "explorer"
    for name in ("ecoli_core.map.json", "reaction_id_map.json", "base_reaction_ids.json"):
        p = base / name
        if not p.exists():
            import pytest
            pytest.skip(f"asset {name} not generated yet")
        json.loads(p.read_text())  # raises if invalid
```

- [ ] **Step 4: Run the test**

Run: `python -m pytest tests/test_explorer_data.py::test_explorer_assets_are_valid_json -v`
Expected: PASS (or SKIP if assets not generated in this environment).

- [ ] **Step 5: Commit**

```bash
git add scripts/build_explorer_assets.py vivarium_dashboard/static/explorer/ tests/test_explorer_data.py
git commit -m "feat(explorer): asset generator + escher map / reaction-id assets"
```

---

## Task 5: `/api/explorer/flux` — flux at a step, remapped to BiGG

**Files:**
- Modify: `vivarium_dashboard/lib/explorer_data.py`
- Modify: `tests/test_explorer_data.py`
- Modify: `vivarium_dashboard/server.py`

**Interfaces:**
- Produces: `explorer_data.get_flux(db_path: str, step: int, base_ids: list[str], id_map: dict[str, str], run_id: str|None=None) -> dict` returning `{"step": int, "time": float|None, "fluxes": {"<bigg>": float}, "coverage": {"mapped": int, "total": int}}`. Reads `listeners.fba_results.base_reaction_fluxes` (under `agents/0/` when present) at the given step, zips with `base_ids`, remaps via `id_map`, drops unmapped.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_explorer_data.py`:

```python
def test_get_flux_remaps_to_bigg(tmp_path):
    db = tmp_path / "runs.db"
    make_fake_runs_db(db, _sample_states(n=4))
    base_ids = ["RXN-A", "RXN-B", "RXN-C"]
    id_map = {"RXN-A": "PGI", "RXN-C": "PFK"}  # RXN-B intentionally unmapped
    res = explorer_data.get_flux(str(db), step=2, base_ids=base_ids, id_map=id_map)
    # state at step 2: base_reaction_fluxes == [3.0, 4.0, 5.0]
    assert res["fluxes"] == {"PGI": 3.0, "PFK": 5.0}
    assert res["coverage"] == {"mapped": 2, "total": 3}
    assert res["step"] == 2
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_explorer_data.py::test_get_flux_remaps_to_bigg -v`
Expected: FAIL — `AttributeError: ... 'get_flux'`.

- [ ] **Step 3: Implement `get_flux`**

Add to `explorer_data.py`:

```python
_FLUX_PATH = "listeners.fba_results.base_reaction_fluxes"


def _state_at_step(db_path: str, step: int, run_id: str | None):
    try:
        conn = sqlite3.connect(str(db_path))
    except sqlite3.OperationalError:
        return None, None
    try:
        if run_id:
            row = conn.execute(
                "SELECT global_time, state FROM history WHERE simulation_id=? AND step=?",
                (run_id, step)).fetchone()
        else:
            row = conn.execute(
                "SELECT global_time, state FROM history WHERE step=? ORDER BY simulation_id LIMIT 1",
                (step,)).fetchone()
        if not row:
            return None, None
        return row[0], json.loads(row[1])
    finally:
        conn.close()


def _dig(node, dotted):
    for k in dotted.split("."):
        if not isinstance(node, dict) or k not in node:
            return None
        node = node[k]
    return node


def get_flux(db_path: str, step: int, base_ids, id_map, run_id: str | None = None) -> dict:
    time, state = _state_at_step(db_path, step, run_id)
    fluxes: dict[str, float] = {}
    total = len(base_ids)
    if state is not None:
        inner = _unwrap_agent(state)
        vec = _dig(inner, _FLUX_PATH)
        if isinstance(vec, list):
            for i, val in enumerate(vec):
                if i >= len(base_ids):
                    break
                bigg = id_map.get(base_ids[i])
                if bigg is not None and isinstance(val, _NUM):
                    fluxes[bigg] = float(val)
    return {"step": step, "time": time, "fluxes": fluxes,
            "coverage": {"mapped": len(fluxes), "total": total}}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_explorer_data.py::test_get_flux_remaps_to_bigg -v`
Expected: PASS.

- [ ] **Step 5: Add the endpoint + dispatch (loads the static assets once)**

`do_GET`:

```python
        if self.path.startswith("/api/explorer/flux"):
            return self._get_explorer_flux()
```

Handler:

```python
    def _get_explorer_flux(self):
        """GET /api/explorer/flux?db=<path>&step=<int>&run=<id>"""
        import urllib.parse as _up
        from vivarium_dashboard.lib import explorer_data
        q = dict(_up.parse_qsl(_up.urlparse(self.path).query))
        db = q.get("db")
        if not db:
            return self._json({"error": "missing db", "fluxes": {}}, 200)
        try:
            step = int(q.get("step", "0"))
        except ValueError:
            step = 0
        try:
            base_ids, id_map = explorer_data.load_flux_assets()
            return self._json(
                explorer_data.get_flux(db, step, base_ids, id_map, q.get("run")), 200)
        except Exception as e:
            return self._json({"error": str(e), "fluxes": {}}, 200)
```

Add the asset loader to `explorer_data.py`:

```python
_ASSET_DIR = Path(__file__).resolve().parents[1] / "static" / "explorer"
_flux_assets_cache = None


def load_flux_assets():
    """(base_reaction_ids, reaction_id_map), cached. Returns ([], {}) if absent."""
    global _flux_assets_cache
    if _flux_assets_cache is None:
        try:
            base = json.loads((_ASSET_DIR / "base_reaction_ids.json").read_text())
        except (OSError, ValueError):
            base = []
        try:
            idmap = json.loads((_ASSET_DIR / "reaction_id_map.json").read_text())
        except (OSError, ValueError):
            idmap = {}
        _flux_assets_cache = (base, idmap)
    return _flux_assets_cache
```

- [ ] **Step 6: Run the full test file**

Run: `python -m pytest tests/test_explorer_data.py -v`
Expected: PASS (all backend tests).

- [ ] **Step 7: Commit**

```bash
git add vivarium_dashboard/lib/explorer_data.py tests/test_explorer_data.py vivarium_dashboard/server.py
git commit -m "feat(explorer): /api/explorer/flux with BiGG remap + coverage"
```

---

## Task 6: Frontend scaffold — card, controller, run-picker, tab shell

**Files:**
- Create: `vivarium_dashboard/static/explorer.js`
- Modify: `vivarium_dashboard/static/walkthrough.js` (`_loadAnalysesPage`)
- Modify: `vivarium_dashboard/templates/index.html.j2` (script + CDN includes)

**Interfaces:**
- Consumes: `GET /api/explorer/runs`, `/observables`. Plotly global (already loaded).
- Produces: global `window.Explorer` with `mount(el, opts)`; card HTML via `_renderExplorerCard()`.

- [ ] **Step 1: Add CDN libs + explorer.js to the template**

In `templates/index.html.j2`, near the existing Plotly/script includes (find the `<script src="https://cdn.plot.ly/...">` line), add:

```html
    <script src="https://cdn.jsdelivr.net/npm/d3@7"></script>
    <script src="https://cdn.jsdelivr.net/npm/d3-voronoi-treemap@1"></script>
    <script src="https://unpkg.com/escher@1.7.3/dist/escher.min.js"></script>
    <script src="{{ static_url }}/explorer.js"></script>
```

(Match the existing `{{ static_url }}` / static path convention used by `walkthrough.js`'s include in the same file.)

- [ ] **Step 2: Create the controller skeleton**

Create `static/explorer.js`:

```javascript
/* Analyses Data Explorer — native marimo-style reactive panel.
   Views: Timeseries · Scatter · Allocation (voronoi) · Flux (escher). */
(function () {
  "use strict";
  var API = "/api/explorer";
  var state = { basePath: "", run: null, runs: [], observables: {}, view: "timeseries", el: null };

  function api(path) { return state.basePath + API + path; }
  function j(url) { return fetch(url).then(function (r) { return r.json(); }); }

  function mount(el, opts) {
    state.el = el; state.basePath = (opts && opts.basePath) || "";
    el.innerHTML = '<div class="explorer-loading">Loading runs…</div>';
    j(api("/runs")).then(function (d) {
      state.runs = (d && d.runs) || [];
      if (!state.runs.length) { renderEmpty(); return; }
      state.run = state.runs[0];
      loadObservables().then(renderShell);
    }).catch(function () { renderEmpty(); });
  }

  function renderEmpty() {
    state.el.innerHTML =
      '<p class="muted">Interactive exploration is available in the local dashboard ' +
      '(no simulation runs found here).</p>';
  }

  function loadObservables() {
    var u = api("/observables?db=" + encodeURIComponent(state.run.db_path) +
                "&run=" + encodeURIComponent(state.run.run_id || ""));
    return j(u).then(function (d) { state.observables = (d && d.categories) || {}; });
  }

  function renderShell() {
    var runOpts = state.runs.map(function (r) {
      return '<option value="' + r.run_id + '">' + (r.label || r.run_id) + '</option>';
    }).join("");
    var tabs = ["timeseries", "scatter", "allocation", "flux"].map(function (v) {
      return '<button class="exp-tab' + (v === state.view ? " active" : "") +
             '" data-view="' + v + '">' + v + "</button>";
    }).join("");
    state.el.innerHTML =
      '<div class="explorer">' +
        '<div class="exp-controls">' +
          '<label>Run <select id="exp-run">' + runOpts + "</select></label>" +
          '<div class="exp-tabs">' + tabs + "</div>" +
          '<div id="exp-view-controls"></div>' +
        "</div>" +
        '<div id="exp-view" class="exp-view"></div>' +
      "</div>";
    state.el.querySelector("#exp-run").value = state.run.run_id;
    state.el.querySelector("#exp-run").addEventListener("change", function (e) {
      state.run = state.runs.find(function (r) { return r.run_id === e.target.value; });
      loadObservables().then(renderView);
    });
    state.el.querySelectorAll(".exp-tab").forEach(function (b) {
      b.addEventListener("click", function () {
        state.view = b.getAttribute("data-view");
        state.el.querySelectorAll(".exp-tab").forEach(function (x) { x.classList.remove("active"); });
        b.classList.add("active");
        renderView();
      });
    });
    renderView();
  }

  function renderView() {
    var host = state.el.querySelector("#exp-view");
    var ctrls = state.el.querySelector("#exp-view-controls");
    host.innerHTML = ""; ctrls.innerHTML = "";
    if (state.view === "timeseries") Views.timeseries(host, ctrls);
    else if (state.view === "scatter") Views.scatter(host, ctrls);
    else if (state.view === "allocation") Views.allocation(host, ctrls);
    else if (state.view === "flux") Views.flux(host, ctrls);
  }

  // Filled in by later tasks.
  var Views = {
    timeseries: function (h) { h.textContent = "timeseries (todo)"; },
    scatter: function (h) { h.textContent = "scatter (todo)"; },
    allocation: function (h) { h.textContent = "allocation (todo)"; },
    flux: function (h) { h.textContent = "flux (todo)"; }
  };

  window.Explorer = { mount: mount, _state: state, _api: api, _j: j, _Views: Views };
})();
```

- [ ] **Step 3: Wire the third card into `_loadAnalysesPage`**

In `static/walkthrough.js`, find `_loadAnalysesPage()` (~line 1321) where `cards` are assembled. Add an explorer card to the `cards` array and mount after injection. Insert a render helper next to `_render3dVizCard`:

```javascript
  function _renderExplorerCard() {
    return '<div class="analyses-card" id="explorer-card">' +
      '<div class="analyses-card-head"><strong>Data Explorer</strong></div>' +
      '<p class="muted" style="font-size:0.85em;margin:2px 0 8px">' +
      'Interactively explore any run: timeseries, scatter, allocation, and flux maps.</p>' +
      '<div id="explorer-mount"></div></div>';
  }
```

In `_loadAnalysesPage`, after `container.innerHTML = cards.join('')` (the line that injects cards), add the explorer card to `cards` (push `_renderExplorerCard()`), then mount:

```javascript
    if (window.Explorer) {
      var mountEl = document.getElementById('explorer-mount');
      if (mountEl) window.Explorer.mount(mountEl, {
        basePath: (window.DataSource && window.DataSource.basePath) ? window.DataSource.basePath() : ''
      });
    }
```

- [ ] **Step 4: Add minimal CSS for the explorer**

In the dashboard's main stylesheet (find where `.analyses-card` is styled, likely `static/*.css` or a `<style>` block in `index.html.j2`), add:

```css
.explorer { display:flex; flex-direction:column; gap:8px; }
.exp-controls { display:flex; flex-wrap:wrap; gap:10px; align-items:center; }
.exp-tabs { display:flex; gap:4px; }
.exp-tab { text-transform:capitalize; padding:3px 10px; border:1px solid #2a313c;
  background:#11151b; color:#cfd6df; border-radius:4px; cursor:pointer; }
.exp-tab.active { border-color:#4c8bf5; color:#fff; }
.exp-view { min-height:460px; }
```

- [ ] **Step 5: Verify in the browser (manual smoke)**

Run the dashboard against a workspace with runs:

```bash
cd /Users/eranagmon/code/vdash-explorer
python -m vivarium_dashboard.server --workspace /Users/eranagmon/code/v2ecoli --port 8799
```

Open `http://localhost:8799/#visualizations`. Expected: a third "Data Explorer" card with a run dropdown and four tabs (timeseries/scatter/allocation/flux), each showing its "(todo)" placeholder. If no runs exist, the card shows the "available in the local dashboard" note.

- [ ] **Step 6: Commit**

```bash
git add vivarium_dashboard/static/explorer.js vivarium_dashboard/static/walkthrough.js vivarium_dashboard/templates/index.html.j2
git commit -m "feat(explorer): card scaffold, controller, run-picker, tab shell"
```

---

## Task 7: Timeseries view

**Files:**
- Modify: `vivarium_dashboard/static/explorer.js`

**Interfaces:**
- Consumes: `GET /api/explorer/series?db=&paths=&subsample=`; `state.observables`; Plotly.
- Produces: `Views.timeseries(host, ctrls)`.

- [ ] **Step 1: Implement the timeseries view**

Replace `Views.timeseries` in `explorer.js`:

```javascript
  function observableOptions() {
    var opts = [];
    Object.keys(state.observables).forEach(function (cat) {
      state.observables[cat].forEach(function (o) {
        var key = o.path + (o.index != null ? "#" + o.index : "");
        opts.push({ key: key, label: cat + " · " + o.label, kind: o.kind, len: o.length });
      });
    });
    return opts;
  }

  Views.timeseries = function (host, ctrls) {
    var opts = observableOptions();
    ctrls.innerHTML =
      '<label>Observables <select id="ts-obs" multiple size="6">' +
      opts.map(function (o) { return '<option value="' + o.key + '">' + o.label + "</option>"; }).join("") +
      "</select></label>" +
      '<label><input type="checkbox" id="ts-log"> log y</label>' +
      '<label><input type="checkbox" id="ts-norm"> normalize</label>';
    host.innerHTML = '<div id="ts-chart" style="height:460px"></div>';

    function draw() {
      var chosen = Array.prototype.map.call(
        ctrls.querySelectorAll("#ts-obs option:checked"), function (o) { return o.value; });
      if (!chosen.length) { Plotly.purge("ts-chart"); return; }
      var u = api("/series?db=" + encodeURIComponent(state.run.db_path) +
                  "&run=" + encodeURIComponent(state.run.run_id || "") +
                  "&paths=" + encodeURIComponent(chosen.join(",")));
      j(u).then(function (d) {
        var norm = ctrls.querySelector("#ts-norm").checked;
        var traces = Object.keys(d.series).map(function (k) {
          var y = d.series[k];
          if (norm) { var m = Math.max.apply(null, y.map(Math.abs)) || 1; y = y.map(function (v) { return v / m; }); }
          return { type: "scatter", mode: "lines", name: k, x: d.time, y: y };
        });
        Plotly.react("ts-chart", traces, {
          margin: { t: 10, r: 10 }, paper_bgcolor: "#0e1116", plot_bgcolor: "#0e1116",
          font: { color: "#cfd6df" },
          yaxis: { type: ctrls.querySelector("#ts-log").checked ? "log" : "linear" }
        }, { responsive: true });
      });
    }
    ctrls.querySelector("#ts-obs").addEventListener("change", draw);
    ctrls.querySelector("#ts-log").addEventListener("change", draw);
    ctrls.querySelector("#ts-norm").addEventListener("change", draw);
  };
```

- [ ] **Step 2: Verify in the browser**

Restart the server (Task 6 Step 5 command), open the Data Explorer → Timeseries, select a run + one or more observables (e.g. Mass · cell_mass). Expected: a multi-trace line chart that updates on selection; log/normalize toggles work.

- [ ] **Step 3: Commit**

```bash
git add vivarium_dashboard/static/explorer.js
git commit -m "feat(explorer): timeseries view"
```

---

## Task 8: Scatter / correlation view

**Files:**
- Modify: `vivarium_dashboard/static/explorer.js`

**Interfaces:**
- Consumes: `/api/explorer/series` (two paths); Plotly. Produces: `Views.scatter`.

- [ ] **Step 1: Implement the scatter view**

Replace `Views.scatter`:

```javascript
  Views.scatter = function (host, ctrls) {
    var opts = observableOptions();
    function sel(id, label) {
      return '<label>' + label + ' <select id="' + id + '">' +
        opts.map(function (o) { return '<option value="' + o.key + '">' + o.label + "</option>"; }).join("") +
        "</select></label>";
    }
    ctrls.innerHTML = sel("sc-x", "X") + sel("sc-y", "Y") +
      '<label><input type="checkbox" id="sc-time" checked> color by time</label>';
    host.innerHTML = '<div id="sc-chart" style="height:460px"></div>';

    function draw() {
      var x = ctrls.querySelector("#sc-x").value, y = ctrls.querySelector("#sc-y").value;
      if (!x || !y) return;
      var u = api("/series?db=" + encodeURIComponent(state.run.db_path) +
                  "&run=" + encodeURIComponent(state.run.run_id || "") +
                  "&paths=" + encodeURIComponent([x, y].join(",")));
      j(u).then(function (d) {
        var trace = {
          type: "scatter", mode: "markers", x: d.series[x], y: d.series[y],
          marker: ctrls.querySelector("#sc-time").checked
            ? { color: d.time, colorscale: "Viridis", showscale: true, size: 6 }
            : { size: 6 }
        };
        Plotly.react("sc-chart", [trace], {
          margin: { t: 10, r: 10 }, paper_bgcolor: "#0e1116", plot_bgcolor: "#0e1116",
          font: { color: "#cfd6df" }, xaxis: { title: x }, yaxis: { title: y }
        }, { responsive: true });
      });
    }
    ["sc-x", "sc-y", "sc-time"].forEach(function (id) {
      ctrls.querySelector("#" + id).addEventListener("change", draw);
    });
    draw();
  };
```

- [ ] **Step 2: Verify in the browser**

Data Explorer → Scatter: pick X and Y observables. Expected: a scatter plot, points colored by time when the toggle is on; updates on change.

- [ ] **Step 3: Commit**

```bash
git add vivarium_dashboard/static/explorer.js
git commit -m "feat(explorer): scatter / correlation view"
```

---

## Task 9: Allocation view — polygonal Voronoi treemap

**Files:**
- Modify: `vivarium_dashboard/static/explorer.js`

**Interfaces:**
- Consumes: `/api/explorer/series` (all members of a category, fetched as a multi-path request); `d3`, `d3.voronoiTreemap`. Produces: `Views.allocation`.

Approach: for the chosen category, request a series for every scalar/vector member, then at the slider's timepoint read each member's value at that time index to build the weighted treemap.

- [ ] **Step 1: Implement the allocation view**

Replace `Views.allocation`:

```javascript
  Views.allocation = function (host, ctrls) {
    var cats = Object.keys(state.observables);
    ctrls.innerHTML =
      '<label>Category <select id="al-cat">' +
        cats.map(function (c) { return '<option>' + c + "</option>"; }).join("") +
      "</select></label>" +
      '<label>Time <input type="range" id="al-t" min="0" max="0" value="0"></label>' +
      '<span id="al-tlabel" class="muted"></span>';
    host.innerHTML = '<svg id="al-svg" width="460" height="460"></svg>';
    var cache = { time: [], members: {} };

    function loadCategory() {
      var cat = ctrls.querySelector("#al-cat").value;
      var members = (state.observables[cat] || []).map(function (o) {
        return o.path + (o.index != null ? "#" + o.index : "");
      });
      if (!members.length) return;
      var u = api("/series?db=" + encodeURIComponent(state.run.db_path) +
                  "&run=" + encodeURIComponent(state.run.run_id || "") +
                  "&paths=" + encodeURIComponent(members.join(",")));
      j(u).then(function (d) {
        cache.time = d.time; cache.members = d.series;
        var slider = ctrls.querySelector("#al-t");
        slider.max = Math.max(0, d.time.length - 1); slider.value = slider.max;
        draw();
      });
    }

    function draw() {
      var ti = parseInt(ctrls.querySelector("#al-t").value, 10) || 0;
      ctrls.querySelector("#al-tlabel").textContent =
        cache.time.length ? "t = " + (cache.time[ti] != null ? cache.time[ti].toFixed(1) : ti) : "";
      var leaves = Object.keys(cache.members).map(function (k) {
        var v = cache.members[k][ti]; return { name: k.split(".").pop(), value: Math.abs(v || 0) };
      }).filter(function (d) { return d.value > 0; });
      var svg = d3.select("#al-svg"); svg.selectAll("*").remove();
      if (!leaves.length) return;
      var W = 460, H = 460, R = 220, cx = W / 2, cy = H / 2;
      var circle = [];
      for (var a = 0; a < 2 * Math.PI; a += Math.PI / 50)
        circle.push([cx + R * Math.cos(a), cy + R * Math.sin(a)]);
      var root = d3.hierarchy({ children: leaves }).sum(function (d) { return d.value; });
      var vt = d3.voronoiTreemap().clip(circle);
      vt(root);
      var color = d3.scaleOrdinal(d3.schemeCategory10);
      svg.selectAll("path").data(root.leaves()).enter().append("path")
        .attr("d", function (d) { return "M" + d.polygon.join("L") + "Z"; })
        .attr("fill", function (d, i) { return color(i); })
        .attr("stroke", "#0e1116").attr("stroke-width", 1.5)
        .append("title").text(function (d) { return d.data.name + ": " + d.data.value.toFixed(2); });
    }

    ctrls.querySelector("#al-cat").addEventListener("change", loadCategory);
    ctrls.querySelector("#al-t").addEventListener("input", draw);
    loadCategory();
  };
```

- [ ] **Step 2: Verify in the browser**

Data Explorer → Allocation: pick a category (e.g. Bulk molecules). Expected: an organic polygonal Voronoi treemap clipped to a circle; dragging the Time slider re-weights cells; hover shows member + value.

- [ ] **Step 3: Commit**

```bash
git add vivarium_dashboard/static/explorer.js
git commit -m "feat(explorer): allocation view (polygonal voronoi treemap)"
```

---

## Task 10: Flux-map view — Escher

**Files:**
- Modify: `vivarium_dashboard/static/explorer.js`

**Interfaces:**
- Consumes: `/api/explorer/flux?db=&step=`; the static asset `static/explorer/ecoli_core.map.json`; the `escher` global. Produces: `Views.flux`.

- [ ] **Step 1: Implement the flux view**

Replace `Views.flux`:

```javascript
  Views.flux = function (host, ctrls) {
    if (!window.escher) {
      host.innerHTML = '<p class="muted">Flux map library failed to load.</p>'; return;
    }
    ctrls.innerHTML =
      '<label>Step <input type="range" id="fx-t" min="0" max="' +
        Math.max(0, (state.run.n_steps || 1) - 1) + '" value="0"></label>' +
      '<span id="fx-cov" class="muted"></span>';
    host.innerHTML = '<div id="fx-map" style="height:460px;background:#fff;border-radius:6px"></div>';
    var builder = null;

    function ensureBuilder() {
      if (builder) return Promise.resolve(builder);
      var mapUrl = state.basePath + "/static/explorer/ecoli_core.map.json";
      return fetch(mapUrl).then(function (r) { return r.json(); }).then(function (mapData) {
        builder = escher.Builder(mapData, null, null, escher.libs.d3_select("#fx-map"), {
          never_ask_before_quit: true, menu: "zoom", scroll_behavior: "zoom",
          reaction_styles: ["color", "size", "abs"], enable_editing: false
        });
        return builder;
      });
    }

    function draw() {
      var step = parseInt(ctrls.querySelector("#fx-t").value, 10) || 0;
      var u = api("/flux?db=" + encodeURIComponent(state.run.db_path) +
                  "&run=" + encodeURIComponent(state.run.run_id || "") + "&step=" + step);
      Promise.all([ensureBuilder(), j(u)]).then(function (res) {
        var b = res[0], d = res[1];
        b.set_reaction_data(d.fluxes || {});
        var c = d.coverage || { mapped: 0, total: 0 };
        ctrls.querySelector("#fx-cov").textContent =
          "mapped " + c.mapped + "/" + c.total + " reactions";
      }).catch(function (e) {
        host.innerHTML = '<p class="muted">Flux map unavailable: ' + e + "</p>";
      });
    }
    ctrls.querySelector("#fx-t").addEventListener("input", draw);
    draw();
  };
```

- [ ] **Step 2: Verify in the browser**

Data Explorer → Flux (requires generated assets from Task 4 + a run with `fba_results`). Expected: the e_coli_core map renders; reactions are colored/sized by flux; the Step slider recolors; a "mapped N/M" badge shows coverage. With no assets, the view shows an honest "unavailable" message.

- [ ] **Step 3: Commit**

```bash
git add vivarium_dashboard/static/explorer.js
git commit -m "feat(explorer): flux-map view (escher e_coli_core)"
```

---

## Task 11: Snapshot-mode guard, docs, and integration sweep

**Files:**
- Modify: `vivarium_dashboard/static/explorer.js`
- Modify: `tests/test_saved_visualizations.py` (or a new `tests/test_explorer_endpoints.py`)
- Create/modify: `docs/` note for the feature (e.g. append to the dashboard README or a short `docs/data-explorer.md`)

- [ ] **Step 1: Add an endpoint smoke test through the data builders**

Create `tests/test_explorer_endpoints.py`:

```python
from pathlib import Path
from vivarium_dashboard.lib import explorer_data
from tests.test_explorer_data import make_fake_runs_db, _sample_states


def test_full_explorer_flow(tmp_path):
    studies = tmp_path / "studies" / "demo"
    studies.mkdir(parents=True)
    db = studies / "runs.db"
    make_fake_runs_db(db, _sample_states())
    runs = explorer_data.list_runs(tmp_path)
    assert runs
    obs = explorer_data.list_observables(str(db))
    assert obs["categories"]
    ser = explorer_data.get_series(str(db), [("listeners.mass.cell_mass", None)])
    assert ser["series"]["listeners.mass.cell_mass"]
    base, idmap = explorer_data.load_flux_assets()
    flux = explorer_data.get_flux(str(db), 0, base, idmap)
    assert "fluxes" in flux and "coverage" in flux
```

- [ ] **Step 2: Run it**

Run: `python -m pytest tests/test_explorer_endpoints.py -v`
Expected: PASS.

- [ ] **Step 3: Confirm snapshot mode shows the local-only note**

In `explorer.js` `mount`, the empty/`error` path already renders the local-only note. Add an explicit snapshot short-circuit at the top of `mount`:

```javascript
    if (opts && opts.snapshot) { renderEmpty(); return; }
```

And in `walkthrough.js` mount call, pass `snapshot: (window.__DASH_CONFIG__||{}).mode === 'snapshot'`.

- [ ] **Step 4: Run the whole dashboard test suite**

Run: `python -m pytest tests/ -q`
Expected: PASS (no regressions; explorer tests green).

- [ ] **Step 5: Write a short feature doc**

Create `docs/data-explorer.md` documenting: the four views, the endpoints, the asset-generation command (`scripts/build_explorer_assets.py`), and the flux ID-map coverage caveat.

- [ ] **Step 6: Commit**

```bash
git add tests/test_explorer_endpoints.py vivarium_dashboard/static/explorer.js vivarium_dashboard/static/walkthrough.js docs/data-explorer.md
git commit -m "feat(explorer): snapshot guard, end-to-end test, feature docs"
```

---

## Self-Review

**Spec coverage:**
- Third Analyses card, native reactive panel → Tasks 6–10. ✓
- Run-picker over Sim DB → Task 1 (`list_runs` reuses `list_simulations`). ✓
- Timeseries / Scatter / Allocation(voronoi) / Flux(escher) → Tasks 7/8/9/10. ✓
- Four endpoints in `explorer_data.py`, thin handlers → Tasks 1/2/3/5. ✓
- Polygonal Voronoi via d3-voronoi-treemap → Task 9. ✓
- Escher e_coli_core + reaction-id remap + coverage → Tasks 4/5/10. ✓
- AI-free / CDN-only client deps → Task 6 (template includes), Global Constraints. ✓
- Categories grouping → Task 2 `_CATEGORY_MAP`. ✓
- Snapshot mode honest note → Tasks 6 & 11. ✓
- Backend unit tests against fixture runs.db → Tasks 1–5, 11. ✓
- Focused modules (explorer_data.py / explorer.js) → File Structure. ✓
- Fast-follow exclusions (state scrubber, iJO1366) → not in tasks, matches spec non-goals. ✓

**Placeholder scan:** Frontend `Views` stubs in Task 6 are intentional scaffolding, each replaced wholesale in Tasks 7–10 (noted "Filled in by later tasks"). No "TBD"/"add error handling" placeholders; every code step contains complete code.

**Type consistency:** `list_runs`→`db_path`/`run_id` keys consumed by `list_observables`/`get_series`/`get_flux`; series key format `path#index` defined in Task 3 (`_series_key`) and reused verbatim by the frontend (Tasks 7–9); `get_flux` signature `(db_path, step, base_ids, id_map, run_id=None)` matches the handler call in Task 5 and the test in Task 5/11; `load_flux_assets()` returns `(base, idmap)` consumed by the flux handler. Consistent.

**Risk note carried from spec:** flux ID-map coverage is partial by design — surfaced via the coverage badge (Task 10) and the generator's coverage print (Task 4); zarr/parquet runs appear in the picker (via `list_simulations`) but full extraction for non-SQLite emitters is a follow-up — SQLite is the v1 extraction path. This is a known v1 limitation to confirm with the user during execution if their target runs are zarr-only.
