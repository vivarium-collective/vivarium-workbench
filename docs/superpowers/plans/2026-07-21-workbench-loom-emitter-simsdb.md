# Workbench: loom vendor, default-parquet baseline, auto-Results, JSONL run log, sortable Sims DB — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Co-develop bigraph-loom inside the workbench, make the Composite Explorer inject the baseline's declared Parquet emitter (emit-all) plus a live RAM emitter, ship auto-advance to Results, move run metadata to an append-only JSONL log, and make the Simulations DB sortable by column.

**Architecture:** Loom's source is vendored into `vivarium_workbench/loom/` and built into the served `_dist` bundle (plain copy, git dep dropped). The detached run worker honors a composite's declared emitter (Parquet, persistence) while keeping a lightweight RAM emitter for the live Results view. Run metadata is written through a single `vivarium_workbench.lib.run_log` append-only JSONL choke point (`<ws>/.pbg/runs.jsonl`); the Simulations DB folds that log and still reads legacy sqlite for back-compat. Sorting is client-side over the already-loaded rows.

**Tech Stack:** Python 3.12 (FastAPI-style app, sqlite3, pytest), TypeScript/React + Vite (loom), vanilla JS frontend (`static/walkthrough.js`), Jinja2 templates.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-21-workbench-loom-emitter-simsdb-design.md`.
- Branch: `feat/loom-vendor-emitter-simsdb` (already created off workbench `main`).
- JSONL boundary: **new writes → JSONL; legacy sqlite `runs.db`/`composite-runs.db` stay read-only. Do NOT remove sqlite in this change.**
- Single workspace-level log: `<workspace>/.pbg/runs.jsonl`. Events tagged with `study_slug`/`investigation_slug`. Live progress/heartbeat stays ephemeral (NOT in the durable log).
- Emitter injection for the explorer = **both** RAM (live Results) + declared Parquet (persistence). Declared emitter wins over the UI top-level-store default.
- Run id convention is unchanged: `<spec_id>__<unix-epoch-int>__<6-hex>` (`composite_runs.generate_run_id`).
- The baseline's declared emit-all schema is `["global_time","bulk","listeners"]` (already in `v2ecoli/composites/baseline.py:615`) — do not redefine it.
- Commit after every task. Run the workbench test suite with `uv run pytest` from `~/code/vivarium-dashboard`.

---

## File Structure

- Create: `vivarium_workbench/lib/run_log.py` — JSONL append/fold run-metadata log (new choke point).
- Create: `tests/test_run_log.py` — write/fold round-trip.
- Modify: `vivarium_workbench/lib/composite_runs.py` — `save_metadata`/`complete_metadata` also append run events; add `inject_declared_emitter`.
- Modify: `vivarium_workbench/lib/composite_subprocess.py:~229,~388-390` — inject declared Parquet + RAM.
- Modify: `vivarium_workbench/lib/simulations_index.py:~121,~265,~949` — fold JSONL, merge with legacy sqlite, ensure `emitter`/`completed_at` populated.
- Modify: `vivarium_workbench/static/walkthrough.js:~14678` + `templates/index.html.j2:1185-1193` — click-to-sort.
- Move: loom `src/`, configs, `bigraph_loom/__init__.py` → `vivarium_workbench/loom/`; wire build.
- Modify: `pyproject.toml:47,107`, `lib/static_serving.py:124`, `publish.py:964` — drop git dep, repoint shim.
- Modify (v2ecoli repo): `scripts/regenerate_viewers.py:304` — locate bundle via `vivarium_workbench`.
- Add loom test: `vivarium_workbench/loom/src/__tests__/tabSwitch.test.tsx` (or existing vitest dir) — lock auto-Results.

Phases are ordered: **4 (JSONL) → 2 (emitter) → 5 (sort) → 1 (loom vendor) → 3 (auto-Results)**. Rationale: JSONL and emitter are pure-Python and unblock the Sims DB display; loom vendor is a large mechanical move best done once the Python contracts are stable; auto-Results rides on the vendored loom.

---

## Phase 4 — JSONL run-metadata log

### Task 1: `run_log` module — append + fold

**Files:**
- Create: `vivarium_workbench/lib/run_log.py`
- Test: `tests/test_run_log.py`

**Interfaces:**
- Produces:
  - `append_run_event(workspace: Path, event: dict) -> None` — atomically append one JSON line to `<workspace>/.pbg/runs.jsonl`. Stamps `ts` (epoch float) if absent.
  - `fold_runs_jsonl(workspace: Path) -> dict[str, dict]` — fold the log to latest-record-per-`run_id`. Later events shallow-merge over earlier ones for the same `run_id`.
  - `RUN_LOG_RELPATH = ".pbg/runs.jsonl"`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_run_log.py
import json
from pathlib import Path
from vivarium_workbench.lib import run_log


def test_append_then_fold_merges_events(tmp_path: Path):
    ws = tmp_path
    run_log.append_run_event(ws, {
        "run_id": "spec__1__abc", "event": "started",
        "spec_id": "spec", "started_at": 1.0, "status": "running",
        "emitter": "parquet", "study_slug": "baseline",
    })
    run_log.append_run_event(ws, {
        "run_id": "spec__1__abc", "event": "completed",
        "completed_at": 2.0, "status": "completed", "n_steps": 10,
    })
    folded = run_log.fold_runs_jsonl(ws)
    rec = folded["spec__1__abc"]
    assert rec["status"] == "completed"
    assert rec["emitter"] == "parquet"       # carried from 'started'
    assert rec["started_at"] == 1.0
    assert rec["completed_at"] == 2.0
    assert rec["n_steps"] == 10
    assert "ts" in rec                        # auto-stamped


def test_append_is_line_delimited(tmp_path: Path):
    run_log.append_run_event(tmp_path, {"run_id": "a", "event": "started"})
    run_log.append_run_event(tmp_path, {"run_id": "b", "event": "started"})
    text = (tmp_path / run_log.RUN_LOG_RELPATH).read_text()
    lines = [l for l in text.splitlines() if l.strip()]
    assert len(lines) == 2
    assert json.loads(lines[0])["run_id"] == "a"


def test_fold_missing_log_returns_empty(tmp_path: Path):
    assert run_log.fold_runs_jsonl(tmp_path) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/code/vivarium-dashboard && uv run pytest tests/test_run_log.py -v`
Expected: FAIL — `ModuleNotFoundError: vivarium_workbench.lib.run_log`.

- [ ] **Step 3: Write minimal implementation**

```python
# vivarium_workbench/lib/run_log.py
"""Append-only JSONL run-metadata log — the single write path for run events.

Owns ``<workspace>/.pbg/runs.jsonl``. Each line is one event
(``started`` / ``completed`` / ``failed``). Readers fold the log to the
latest record per ``run_id``. This is the durable metadata store; live
progress/heartbeat is intentionally NOT logged here (it stays ephemeral).
"""
from __future__ import annotations
import json
import os
import time
from pathlib import Path

RUN_LOG_RELPATH = ".pbg/runs.jsonl"


def _log_path(workspace: Path) -> Path:
    return Path(workspace) / RUN_LOG_RELPATH


def append_run_event(workspace: Path, event: dict) -> None:
    """Atomically append one event as a JSON line. Stamps ``ts`` if absent."""
    ev = dict(event)
    ev.setdefault("ts", time.time())
    path = _log_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(ev, sort_keys=True) + "\n"
    # O_APPEND makes concurrent single-line writes atomic on POSIX.
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def fold_runs_jsonl(workspace: Path) -> dict[str, dict]:
    """Fold the log to the latest record per run_id (later events merge over earlier)."""
    path = _log_path(workspace)
    if not path.exists():
        return {}
    folded: dict[str, dict] = {}
    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                ev = json.loads(raw)
            except json.JSONDecodeError:
                continue  # tolerate a torn final line
            rid = ev.get("run_id")
            if not rid:
                continue
            folded.setdefault(rid, {}).update(ev)
    return folded
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_run_log.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add vivarium_workbench/lib/run_log.py tests/test_run_log.py
git commit -m "feat(run-log): append-only JSONL run-metadata log (write + fold)"
```

### Task 2: Route metadata writes through the JSONL log

**Files:**
- Modify: `vivarium_workbench/lib/composite_runs.py:118` (`save_metadata`), `:142` (`complete_metadata`)
- Test: `tests/test_composite_runs_jsonl.py`

**Interfaces:**
- Consumes: `run_log.append_run_event` (Task 1).
- Produces: `save_metadata(...)`/`complete_metadata(...)` gain a keyword `workspace: Path | None = None`; when provided they ALSO append a JSONL event (sqlite writes stay unchanged). Signature additions:
  - `save_metadata(conn, *, spec_id, run_id, params, label, started_at, n_steps, log_path=None, generation_id=None, workspace=None, emitter=None, study_slug=None, investigation_slug=None, origin="local")`
  - `complete_metadata(conn, *, run_id, n_steps, status, workspace=None)`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_composite_runs_jsonl.py
from pathlib import Path
from vivarium_workbench.lib import composite_runs, run_log


def test_save_and_complete_append_jsonl(tmp_path: Path):
    ws = tmp_path
    db = ws / ".pbg" / "composite-runs.db"
    conn = composite_runs.connect(db)
    rid = "spec__1__abc"
    composite_runs.save_metadata(
        conn, spec_id="spec", run_id=rid, params={}, label="baseline",
        started_at=1.0, n_steps=10, workspace=ws, emitter="parquet",
        study_slug="baseline", investigation_slug=None, origin="local")
    composite_runs.complete_metadata(
        conn, run_id=rid, n_steps=10, status="completed", workspace=ws)

    folded = run_log.fold_runs_jsonl(ws)
    rec = folded[rid]
    assert rec["emitter"] == "parquet"
    assert rec["study_slug"] == "baseline"
    assert rec["status"] == "completed"
    assert rec["started_at"] == 1.0 and rec["completed_at"] is not None


def test_save_without_workspace_still_writes_sqlite(tmp_path: Path):
    conn = composite_runs.connect(tmp_path / "x.db")
    composite_runs.save_metadata(
        conn, spec_id="s", run_id="s__1__a", params={}, label="l",
        started_at=1.0, n_steps=1)  # no workspace kwarg
    assert composite_runs.query_run_meta(conn, run_id="s__1__a") is not None
    assert not (tmp_path / run_log.RUN_LOG_RELPATH).exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_composite_runs_jsonl.py -v`
Expected: FAIL — `save_metadata() got an unexpected keyword argument 'workspace'`.

- [ ] **Step 3: Write minimal implementation**

At the top of `composite_runs.py` add `from vivarium_workbench.lib import run_log`. Replace `save_metadata` (currently `:118-139`) and `complete_metadata` (`:142-150`) with:

```python
def save_metadata(conn, *, spec_id, run_id, params, label, started_at,
                  n_steps, log_path=None, generation_id=None,
                  workspace=None, emitter=None, study_slug=None,
                  investigation_slug=None, origin="local"):
    """Insert a run row (status='running') and, if ``workspace`` is given,
    append a 'started' event to the JSONL run log (durable metadata)."""
    conn.execute(
        "INSERT INTO runs_meta "
        "(run_id, spec_id, label, params_json, started_at, status, "
        " n_steps, log_path, progress_step, generation_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
        (run_id, spec_id, label, json.dumps(params or {}),
         started_at, "running", n_steps, log_path, generation_id),
    )
    conn.commit()
    if workspace is not None:
        run_log.append_run_event(workspace, {
            "run_id": run_id, "event": "started", "spec_id": spec_id,
            "label": label, "started_at": started_at, "status": "running",
            "n_steps": n_steps, "emitter": emitter, "origin": origin,
            "study_slug": study_slug, "investigation_slug": investigation_slug,
        })


def complete_metadata(conn, *, run_id, n_steps, status, workspace=None):
    """Mark a run completed/failed; mirror the terminal event to the JSONL log."""
    completed_at = time.time()
    conn.execute(
        "UPDATE runs_meta "
        "SET completed_at=?, n_steps=?, status=? WHERE run_id=?",
        (completed_at, n_steps, status, run_id),
    )
    conn.commit()
    if workspace is not None:
        run_log.append_run_event(workspace, {
            "run_id": run_id,
            "event": "completed" if status == "completed" else "failed",
            "completed_at": completed_at, "n_steps": n_steps, "status": status,
        })
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_composite_runs_jsonl.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add vivarium_workbench/lib/composite_runs.py tests/test_composite_runs_jsonl.py
git commit -m "feat(run-log): mirror run save/complete events to the JSONL log"
```

### Task 3: Simulations DB folds the JSONL log + merges legacy sqlite

**Files:**
- Modify: `vivarium_workbench/lib/simulations_index.py` (`build_simulations_data:949`, `_row_to_dict:121`)
- Test: `tests/test_simulations_index_jsonl.py`

**Interfaces:**
- Consumes: `run_log.fold_runs_jsonl` (Task 1).
- Produces: `build_simulations_data(ws_root)` returns simulations whose records prefer folded JSONL fields (`emitter`, `started_at`, `completed_at`, `status`, `study_slug`, `investigation_slug`) when a `run_id` is present in the log, and still includes legacy-sqlite-only runs. Each row exposes `emitter_type` (label) and a `time` sort key = `completed_at or started_at`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_simulations_index_jsonl.py
from pathlib import Path
from vivarium_workbench.lib import run_log, simulations_index


def test_jsonl_run_appears_with_emitter_and_time(tmp_path: Path):
    ws = tmp_path
    (ws / "studies").mkdir(parents=True, exist_ok=True)
    run_log.append_run_event(ws, {
        "run_id": "v2ecoli.composites.baseline__9__ff", "event": "started",
        "spec_id": "v2ecoli.composites.baseline", "started_at": 100.0,
        "status": "running", "emitter": "parquet", "study_slug": "baseline",
    })
    run_log.append_run_event(ws, {
        "run_id": "v2ecoli.composites.baseline__9__ff", "event": "completed",
        "completed_at": 160.0, "status": "completed", "n_steps": 2700,
    })
    data = simulations_index.build_simulations_data(ws)
    rows = {r["run_id"]: r for r in data["simulations"]}
    row = rows["v2ecoli.composites.baseline__9__ff"]
    assert row["emitter_type"] == "Parquet"
    assert (row.get("completed_at") or row.get("started_at")) == 160.0
    assert row["status"] == "completed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_simulations_index_jsonl.py -v`
Expected: FAIL — the JSONL run is absent from `simulations` (only sqlite is read today).

- [ ] **Step 3: Write minimal implementation**

In `build_simulations_data` (`simulations_index.py:949`), after the existing sqlite gather produces its `rows` list and before the final sort (`:746-751`), fold the JSONL and merge:

```python
from vivarium_workbench.lib import run_log  # top of file

# ... inside build_simulations_data(ws_root), after sqlite `rows` are built:
folded = run_log.fold_runs_jsonl(Path(ws_root))
by_id = {r.get("run_id"): r for r in rows}
_EMITTER_LABEL = {"sqlite": "SQLite", "parquet": "Parquet",
                  "xarray": "XArray", "ram": "RAM", "none": "—"}
for rid, rec in folded.items():
    row = by_id.get(rid)
    if row is None:
        row = {"run_id": rid}
        rows.append(row)
        by_id[rid] = row
    # JSONL is the source of truth for these fields when present.
    for k in ("spec_id", "label", "status", "n_steps", "started_at",
              "completed_at", "study_slug", "investigation_slug"):
        if rec.get(k) is not None:
            row[k] = rec[k]
    if rec.get("emitter"):
        row["emitter"] = rec["emitter"]
        row["emitter_type"] = _EMITTER_LABEL.get(rec["emitter"], rec["emitter"])
    if rec.get("origin"):
        row["remote_origin"] = rec["origin"]
```

Keep the existing `emitter_type` derivation (`:970-972`) as the fallback for legacy-sqlite-only rows. Ensure the final sort key `_ts` (`:746`) uses `completed_at or started_at` (already the case).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_simulations_index_jsonl.py -v`
Expected: PASS. Then run the whole suite: `uv run pytest -q` — Expected: no regressions.

- [ ] **Step 5: Commit**

```bash
git add vivarium_workbench/lib/simulations_index.py tests/test_simulations_index_jsonl.py
git commit -m "feat(sims-db): fold JSONL run log, merge with legacy sqlite"
```

---

## Phase 2 — Default Parquet emitter, auto-injected (both RAM + Parquet)

### Task 4: `inject_declared_emitter` — honor a composite's declared emitter

**Files:**
- Modify: `vivarium_workbench/lib/composite_runs.py` (add function near `inject_sqlite_emitter:335`)
- Test: `tests/test_inject_declared_emitter.py`

**Interfaces:**
- Consumes: `pbg_superpowers.composite_generator.emitter_defaults` (reads `emitters=[...]`).
- Produces: `inject_declared_emitter(state: dict, *, spec_id: str, run_id: str, out_dir: str | Path) -> tuple[dict, str | None]` — if the composite (looked up from `spec_id`) declares a default emitter, append that emitter node to `state` (e.g. a `local:ParquetEmitter` step named `declared_emitter` with `config.emit` from its `paths` and `config.out_dir=out_dir`) and return `(new_state, emitter_kind)` where `emitter_kind` is e.g. `"parquet"`. If none declared, return `(state, None)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_inject_declared_emitter.py
from vivarium_workbench.lib import composite_runs


def test_baseline_declares_parquet(tmp_path):
    state, kind = composite_runs.inject_declared_emitter(
        {}, spec_id="v2ecoli.composites.baseline",
        run_id="v2ecoli.composites.baseline__1__aa",
        out_dir=str(tmp_path / "parquet-runs"))
    assert kind == "parquet"
    node = state["declared_emitter"]
    assert node["_type"] == "step"
    assert node["address"].lower().endswith("parquetemitter")
    assert node["config"]["out_dir"].endswith("parquet-runs")
    # emit-all schema derived from declared paths
    assert set(node["config"]["emit"]) >= {"global_time", "bulk", "listeners"}


def test_no_declared_emitter_is_noop():
    state, kind = composite_runs.inject_declared_emitter(
        {"x": 1}, spec_id="nonexistent.spec", run_id="r", out_dir="/tmp/p")
    assert kind is None
    assert state == {"x": 1}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_inject_declared_emitter.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'inject_declared_emitter'`.

- [ ] **Step 3: Write minimal implementation**

Add to `composite_runs.py`. Resolve the composite generator and read its declared emitters via `pbg_superpowers`:

```python
def inject_declared_emitter(state, *, spec_id, run_id, out_dir):
    """If the composite for ``spec_id`` declares a default emitter, append it.

    Returns (new_state, emitter_kind|None). Emit schema comes from the
    declaration's ``paths`` (baseline: ['global_time','bulk','listeners']).
    """
    try:
        from pbg_superpowers.composite_generator import (
            resolve_generator, emitter_defaults)
    except Exception:
        return state, None
    entry = resolve_generator(spec_id)          # generator lookup by id
    if entry is None:
        return state, None
    decls = emitter_defaults(entry) or []
    if not decls:
        return state, None
    decl = decls[0]
    addr = decl.get("address", "local:ParquetEmitter")
    paths = decl.get("paths") or ["global_time", "bulk", "listeners"]
    emit = {p: [p] for p in paths}
    cfg = dict(decl.get("config") or {})
    kind = "parquet" if addr.lower().endswith("parquetemitter") else \
           addr.split(":")[-1].lower().replace("emitter", "") or "custom"
    if kind == "parquet":
        cfg.setdefault("out_dir", str(out_dir))
    new_state = dict(state)
    new_state["declared_emitter"] = {
        "_type": "step", "address": addr,
        "config": {**cfg, "emit": emit, "simulation_id": run_id},
        "inputs": {p: [p] for p in paths},
    }
    return new_state, kind
```

If `resolve_generator` is not the exact name in `pbg_superpowers.composite_generator`, the implementer greps `~/code/pbg-superpowers/pbg_superpowers/composite_generator.py` for the registry lookup (the module exposes `GeneratorEntry` + `emitter_defaults`) and uses the correct resolver.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_inject_declared_emitter.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add vivarium_workbench/lib/composite_runs.py tests/test_inject_declared_emitter.py
git commit -m "feat(emitter): inject a composite's declared default emitter (parquet)"
```

### Task 5: Run worker injects both RAM + declared Parquet; records emitter

**Files:**
- Modify: `vivarium_workbench/lib/composite_subprocess.py:~388-390` (and the `save_metadata`/`complete_metadata` call sites; `composite_test_run_views.py:119`)
- Test: `tests/test_composite_subprocess_emitter.py`

**Interfaces:**
- Consumes: `inject_declared_emitter` (Task 4), `inject_emitter_for_paths` + `inject_sqlite_emitter` (existing), `save_metadata(workspace=, emitter=, ...)` (Task 2).
- Produces: after injection the state contains BOTH `user_emitter` (RAM, from `emit_paths`) AND (when declared) `declared_emitter` (Parquet). The recorded run `emitter` = the declared kind if present, else `"sqlite"`/`"ram"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_composite_subprocess_emitter.py
from pathlib import Path
from vivarium_workbench.lib import composite_subprocess as cs


def test_injects_both_ram_and_declared_parquet(tmp_path: Path):
    state = {"global_time": 0.0, "bulk": {}, "listeners": {}}
    out = cs.inject_run_emitters(
        state, spec_id="v2ecoli.composites.baseline",
        run_id="v2ecoli.composites.baseline__1__aa",
        emit_paths=["global_time"], workspace=tmp_path)
    assert "user_emitter" in out["state"]                 # RAM live view
    assert out["state"]["user_emitter"]["address"].lower().endswith("ramemitter")
    assert "declared_emitter" in out["state"]             # parquet persistence
    assert out["emitter"] == "parquet"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_composite_subprocess_emitter.py -v`
Expected: FAIL — `inject_run_emitters` does not exist.

- [ ] **Step 3: Write minimal implementation**

Extract the injection into a testable helper in `composite_subprocess.py` and call it where `:388-390` currently injects. The helper returns the mutated state + the emitter kind to record:

```python
def inject_run_emitters(state, *, spec_id, run_id, emit_paths, workspace):
    """Inject the live RAM emitter (from the UI selection) AND, when the
    composite declares one, its default Parquet emitter for persistence.
    Returns {"state": <dict>, "emitter": <kind str>}."""
    from vivarium_workbench.lib import composite_runs as cr
    state = cr.inject_emitter_for_paths(state, emit_paths)     # RAM user_emitter
    out_dir = Path(workspace) / ".pbg" / "parquet-runs"
    state, declared_kind = cr.inject_declared_emitter(
        state, spec_id=spec_id, run_id=run_id, out_dir=out_dir)
    if declared_kind is None:
        # Legacy path: persist via SQLite as before.
        db = Path(workspace) / ".pbg" / "composite-runs.db"
        state = cr.inject_sqlite_emitter(state, run_id=run_id, db_file=db)
        return {"state": state, "emitter": "sqlite"}
    return {"state": state, "emitter": declared_kind}
```

Then at the current injection site (`:388-390`) replace the two `cr.inject_*` lines with:

```python
_res = inject_run_emitters(state, spec_id=spec_id, run_id=run_id,
                           emit_paths=emit_paths, workspace=ws_root)
state = _res["state"]
run_emitter_kind = _res["emitter"]
```

Thread `run_emitter_kind` into the `save_metadata(...)` call (add `emitter=run_emitter_kind, workspace=ws_root`) and pass `workspace=ws_root` to `complete_metadata(...)`. In `composite_test_run_views.py:119`, pass `workspace=ws_root` (and `emitter=`, `study_slug=`, `investigation_slug=` if known at request time) to `save_metadata`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_composite_subprocess_emitter.py -v`
Expected: PASS. Then `uv run pytest -q`.

- [ ] **Step 5: Commit**

```bash
git add vivarium_workbench/lib/composite_subprocess.py vivarium_workbench/lib/composite_test_run_views.py tests/test_composite_subprocess_emitter.py
git commit -m "feat(emitter): explorer injects RAM (live) + declared Parquet (persist), records emitter kind"
```

### Task 6: Loom emit-selection defaults to the declared/emit-all set

**Files:**
- Modify: loom `src/App.tsx:79-136` (emit-set seeding), `src/api.ts` if a declared-emit hint is fetched.
- Test: loom vitest `src/__tests__/emitDefault.test.tsx` (or nearest existing test dir).

**Interfaces:**
- Consumes: composite state from `/api/composite-state`.
- Produces: initial `emitSet` = the composite's declared emit paths when present, else the current top-level-store default.

- [ ] **Step 1: Write the failing test** — assert that given a composite whose spec advertises declared emit paths, `initialEmitSet(composite)` returns those paths (not just top-level stores). (Write against the helper you extract from `App.tsx:79-81`.)

- [ ] **Step 2: Run** the loom test runner (`cd vivarium_workbench/loom && npm test -- emitDefault`) — Expected: FAIL.

- [ ] **Step 3: Implement** — extract `initialEmitSet(composite)` from `App.tsx:79-81`; if `composite.emitters`/declared paths exist, seed from them; else fall back to `topLevelStorePaths`. Update `App.tsx:132-136` re-seed on load to use the same helper.

- [ ] **Step 4: Run** the loom test — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add vivarium_workbench/loom/src/App.tsx vivarium_workbench/loom/src/__tests__/emitDefault.test.tsx
git commit -m "feat(loom): default emit selection to the composite's declared emit-all paths"
```

*(Task 6 lands after Phase 1 vendors loom into `vivarium_workbench/loom/`. If executed before the vendor, apply it in the standalone `~/code/bigraph-loom` checkout and re-apply during the vendor copy.)*

---

## Phase 5 — Sortable Simulations DB columns

### Task 7: Client-side column sort

**Files:**
- Modify: `vivarium_workbench/templates/index.html.j2:1185-1193` (header `<th>`s), `vivarium_workbench/static/walkthrough.js:14678` (`_applySimFilter`)
- Test: `tests/test_sims_sort_frontend.py` (asserts the template wiring) + manual browser check.

**Interfaces:**
- Consumes: `window._simRows` (already populated by `_initSimulations`).
- Produces: `_sortSimRows(rows, key, dir)` pure helper in `walkthrough.js`; clicking a `<th>` sets `{key, dir}` (toggling asc/desc), re-runs `_applySimFilter`, and shows a ▲/▼ indicator.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sims_sort_frontend.py
from pathlib import Path

WALK = Path("vivarium_workbench/static/walkthrough.js").read_text()
TPL = Path("vivarium_workbench/templates/index.html.j2").read_text()


def test_sort_helper_and_header_handlers_present():
    assert "function _sortSimRows(" in WALK
    assert "_simSortState" in WALK           # {key, dir}
    # headers carry a sort hook + data-sort-key
    assert "data-sort-key=" in TPL
    assert "_onSimHeaderClick" in WALK
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_sims_sort_frontend.py -v`
Expected: FAIL — helpers/attributes absent.

- [ ] **Step 3: Write minimal implementation**

In `index.html.j2:1185-1193`, add to each sortable `<th>`: `data-sort-key="investigation|study|run|origin|emitter_type|time|status"` and `onclick="_onSimHeaderClick(this)"`. Map the "Time" header to sort key `time` and "Emitter" to `emitter_type`.

In `walkthrough.js` add near `_applySimFilter` (`:14678`):

```javascript
let _simSortState = { key: null, dir: 'desc' };

function _simSortValue(row, key) {
  if (key === 'time') return row.completed_at || row.started_at || 0;
  if (key === 'emitter_type') return (row.emitter_type || '').toLowerCase();
  if (key === 'origin') return (row.remote_origin || 'local').toLowerCase();
  if (key === 'study') return (row.study_slug || '').toLowerCase();
  if (key === 'investigation') return (row.investigation_slug || '').toLowerCase();
  if (key === 'run') return (row.label || row.run_id || '').toLowerCase();
  if (key === 'status') return (row.status || '').toLowerCase();
  return '';
}

function _sortSimRows(rows, key, dir) {
  if (!key) return rows;
  const s = rows.slice().sort((a, b) => {
    const va = _simSortValue(a, key), vb = _simSortValue(b, key);
    if (va < vb) return -1; if (va > vb) return 1; return 0;
  });
  return dir === 'desc' ? s.reverse() : s;
}

function _onSimHeaderClick(th) {
  const key = th.getAttribute('data-sort-key');
  if (!key) return;
  if (_simSortState.key === key) {
    _simSortState.dir = _simSortState.dir === 'asc' ? 'desc' : 'asc';
  } else {
    _simSortState = { key, dir: 'asc' };
  }
  document.querySelectorAll('#page-simulations th[data-sort-key]')
    .forEach(h => { h.dataset.sortDir = ''; });
  th.dataset.sortDir = _simSortState.dir;   // CSS ::after renders ▲/▼
  _applySimFilter();
}
window._onSimHeaderClick = _onSimHeaderClick;
```

Inside `_applySimFilter` (`:14678`), before `.map(_renderSimRow)`, apply the sort:

```javascript
let _rows = /* existing filtered rows over window._simRows */;
_rows = _sortSimRows(_rows, _simSortState.key, _simSortState.dir);
```

Add a small CSS rule for `th[data-sort-dir="asc"]::after{content:" ▲"}` / `"desc"` `" ▼"`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_sims_sort_frontend.py -v`
Expected: PASS. Manual: `scripts/serve.sh`, open Simulations DB, click Emitter/Time/Origin headers → rows reorder, indicator toggles.

- [ ] **Step 5: Commit**

```bash
git add vivarium_workbench/static/walkthrough.js vivarium_workbench/templates/index.html.j2 tests/test_sims_sort_frontend.py
git commit -m "feat(sims-db): click column headers to sort (Origin, Emitter, Time, …)"
```

---

## Phase 1 — Vendor bigraph-loom into the workbench (plain copy)

### Task 8: Copy loom source into `vivarium_workbench/loom/` + wire build

**Files:**
- Create: `vivarium_workbench/loom/` ← copy of `~/code/bigraph-loom` (`src/`, `index.html`, `package.json`, `vite.config.ts`, `tsconfig*.json`, `vitest.config.ts`, `bigraph_loom/__init__.py`). Exclude `node_modules/`, `_dist/`, `.git/`, `deploy/`.
- Modify: `pyproject.toml:47` (drop git dep), `:107` (note), the workbench build/hatch config (build loom into the served bundle).
- Modify: `vivarium_workbench/lib/static_serving.py:124`, `vivarium_workbench/publish.py:964` (`from bigraph_loom import asset_dir` → vendored path).
- Test: `tests/test_loom_vendored.py`.

**Interfaces:**
- Produces: `vivarium_workbench.loom_assets.asset_dir() -> Path` (thin re-export of the vendored shim) returning the built `_dist` directory.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_loom_vendored.py
import importlib
from pathlib import Path


def test_no_external_bigraph_loom_dep():
    text = Path("pyproject.toml").read_text()
    assert "git+https://github.com/vivarium-collective/bigraph-loom" not in text


def test_vendored_asset_dir_importable():
    mod = importlib.import_module("vivarium_workbench.loom_assets")
    assert callable(mod.asset_dir)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_loom_vendored.py -v`
Expected: FAIL — git dep still present / `vivarium_workbench.loom_assets` missing.

- [ ] **Step 3: Implement**

```bash
# from ~/code/vivarium-dashboard
rsync -a --exclude node_modules --exclude _dist --exclude .git --exclude deploy \
  ~/code/bigraph-loom/ vivarium_workbench/loom/
```

Create `vivarium_workbench/loom_assets.py`:

```python
"""Locate the vendored bigraph-loom built bundle (_dist)."""
from pathlib import Path


def asset_dir() -> Path:
    return Path(__file__).resolve().parent / "loom" / "_dist"
```

- Edit `lib/static_serving.py:124` and `publish.py:964`: replace `from bigraph_loom import asset_dir` with `from vivarium_workbench.loom_assets import asset_dir`.
- `pyproject.toml`: delete the `bigraph-loom @ git+…@main` line (`:47`); update the PEP-508 note (`:107`). Add a build step / hatch hook (or a documented `scripts/build_loom.sh` invoked by the image build) that runs `cd vivarium_workbench/loom && npm ci && npm run build` to produce `_dist`. Ensure `_dist` is included in the wheel (`[tool.hatch.build] include`/`artifacts`).
- Add `vivarium_workbench/loom/node_modules/` and `vivarium_workbench/loom/_dist/` to `.gitignore` (build outputs), but ensure the built `_dist` is packaged into the image.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_loom_vendored.py -v` — Expected: PASS.
Build loom: `cd vivarium_workbench/loom && npm ci && npm run build && ls _dist` — Expected: `_dist` populated.
Full suite: `uv run pytest -q` — Expected: no regressions (static-serving tests still find the bundle).

- [ ] **Step 5: Commit**

```bash
git add vivarium_workbench/loom vivarium_workbench/loom_assets.py \
  vivarium_workbench/lib/static_serving.py vivarium_workbench/publish.py \
  pyproject.toml .gitignore tests/test_loom_vendored.py
git commit -m "feat(loom): vendor bigraph-loom into vivarium_workbench/loom (drop git dep)"
```

### Task 9: Repoint v2ecoli's bundle locator

**Files:**
- Modify (v2ecoli repo `~/code/v2ecoli`): `scripts/regenerate_viewers.py:303-305`
- Test: manual (`python scripts/regenerate_viewers.py --help` / a viewer regen dry-run).

- [ ] **Step 1:** In `~/code/v2ecoli`, change `regenerate_viewers.py:304` from `import bigraph_loom; return Path(bigraph_loom.__file__)...parent/"_dist"` to locate via the workbench: `from vivarium_workbench.loom_assets import asset_dir; return asset_dir()`.
- [ ] **Step 2:** Run `python scripts/regenerate_viewers.py` on a sample composite (or `--help`) — Expected: locates the vendored `_dist`, no `ModuleNotFoundError: bigraph_loom`.
- [ ] **Step 3: Commit** in the v2ecoli repo:

```bash
cd ~/code/v2ecoli && git add scripts/regenerate_viewers.py
git commit -m "chore(viewers): locate loom bundle via vivarium_workbench (loom vendored)"
```

---

## Phase 3 — Lock auto-advance to Results

### Task 10: Test-lock `onCompleted → setTab('results')`

**Files:**
- Modify/verify: loom `src/App.tsx:742`, trigger `src/panels/SetupRunPanel.tsx:182-184`.
- Test: `vivarium_workbench/loom/src/__tests__/tabSwitch.test.tsx`.

**Interfaces:**
- Consumes: the `onCompleted` prop wired at `App.tsx:742`.
- Produces: a vitest asserting that a `completed` run status drives the active tab to `results`.

- [ ] **Step 1: Write the failing/locking test** — render the panel, simulate a poll `tick` reaching terminal `completed` status (mock `fetchRunStatus`), assert `setTab` was called with `'results'` (or the Results panel becomes visible).
- [ ] **Step 2: Run** `cd vivarium_workbench/loom && npm test -- tabSwitch` — Expected: PASS if behavior present (it is in loom main); FAIL if the vendored copy predates it → then ensure `App.tsx:742 onCompleted={() => setTab('results')}` exists.
- [ ] **Step 3:** If missing, add the one-line wiring at `App.tsx:742`.
- [ ] **Step 4: Run** the test — Expected: PASS.
- [ ] **Step 5: Commit**

```bash
git add vivarium_workbench/loom/src/__tests__/tabSwitch.test.tsx vivarium_workbench/loom/src/App.tsx
git commit -m "test(loom): lock auto-advance to Results tab on run completion"
```

---

## Phase 6 — Ship it

### Task 11: Build image + redeploy + verify live

- [ ] **Step 1:** Push the branch and open a PR (do not auto-merge — user approves).
- [ ] **Step 2:** After merge to `main`, build + push the `vivarium-workbench` image (the repo's image build; tag bump per its convention).
- [ ] **Step 3:** `AWS_PROFILE=stanford-sso kubectl set image deploy/workbench workbench=<new-image> -n sms-api-stanford` (or bump the kustomize/manifest tag) then `kubectl rollout status deploy/workbench -n sms-api-stanford`.
- [ ] **Step 4:** Through the ptools-proxy tunnel, open `/workbench`, run `baseline` in the Composite Explorer, and verify: (a) it auto-advances to Results; (b) the run appears in the Simulations DB with **Parquet** emitter + a **Time**; (c) clicking the Emitter/Time/Origin headers sorts.

---

## Self-Review

**Spec coverage:**
- F1 vendor loom → Tasks 8, 9. ✓
- F2 default parquet, inject-both → Tasks 4, 5, 6. ✓
- F3 auto-Results → Task 10 (+ ships via Task 8 vendor). ✓
- F4 JSONL run log (Time+Emitter always, sortable source) → Tasks 1, 2, 3. ✓
- F5 sortable columns → Task 7. ✓
- Cross-cutting build/deploy → Task 11. ✓

**Placeholder scan:** New modules carry full code; edits show the current→new code with exact file:line targets. Two spots depend on names the implementer confirms by grep at implementation time and are flagged explicitly: `pbg_superpowers.composite_generator` resolver name (Task 4) and the exact `_applySimFilter` filtered-rows variable (Task 7). These are verification steps, not missing content.

**Type consistency:** `append_run_event`/`fold_runs_jsonl` (Task 1) used verbatim in Tasks 2–3. `inject_declared_emitter(state, *, spec_id, run_id, out_dir) -> (state, kind)` (Task 4) consumed by `inject_run_emitters` (Task 5). `_sortSimRows`/`_simSortState`/`_onSimHeaderClick` consistent across Task 7 template + JS. `asset_dir()` (Task 8) consumed by Task 9.
