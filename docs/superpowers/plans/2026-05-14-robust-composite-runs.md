# Robust Composite Simulation Runs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decouple dashboard composite runs from the HTTP request — run them as detached background jobs whose state lives entirely on disk — so they survive network blips, tab sleep, server restarts, oversized state, and dirty git trees.

**Architecture:** A `POST` writes a run-request file and spawns a detached `vivarium-dashboard run-composite` CLI process, returning `202 {run_id}` immediately. The detached process runs the composite in chunks, writing progress and per-step state to the shared SQLite DB. `GET` endpoints only read the DB. On server startup a reconcile pass repairs runs left `running` by a crash. The frontend starts-then-polls and re-attaches via `sessionStorage`.

**Tech Stack:** Python 3.12, `argparse`, `subprocess` (`start_new_session=True`), `sqlite3` (WAL mode), `process_bigraph` (`Composite`, `SQLiteEmitter`), `pbg_superpowers`. Frontend: React/TypeScript in `bigraph-loom-explore`, built with Vite, bundle copied into `vivarium-dashboard`.

**Spec:** `vivarium-dashboard/docs/superpowers/specs/2026-05-14-robust-composite-runs-design.md`

## Repos involved

- **`/Users/eranagmon/code/vivarium-dashboard`** — Python backend (Tasks 1–7). Branch off `studies-phase-1`.
- **`/Users/eranagmon/code/bigraph-loom-explore`** — React frontend (Tasks 8–10). Branch off `studies-phase-1`. Built bundle is copied into `vivarium-dashboard/vivarium_dashboard/static/loom-explore/`.

## File Structure

| File | Responsibility |
|---|---|
| `vivarium_dashboard/lib/composite_runs.py` *(modify)* | `runs_meta` schema + migration + WAL; metadata read/write helpers; `prune_runs` |
| `vivarium_dashboard/lib/run_runner.py` *(create)* | Pure run logic: load request → build composite → chunked run with progress → persist → viz.json |
| `vivarium_dashboard/lib/run_registry.py` *(create)* | Process lifecycle: `spawn_detached`, `reconcile_stale_runs`, `count_running` |
| `vivarium_dashboard/cli.py` *(modify)* | `run-composite` subcommand wrapping `run_runner.execute` |
| `vivarium_dashboard/server.py` *(modify)* | Thin `_post_composite_test_run`; `_get_composite_run_status`; startup reconcile; scoped `_active_branch_action` staging |
| `.gitignore` *(modify)* | Ignore `out/` |
| `bigraph-loom-explore/src/api.ts` *(modify)* | `fetchRunStatus`, `fetchRunTrajectory`, `startRun` helpers |
| `bigraph-loom-explore/src/panels/RunPanel.tsx` *(modify)* | Start-then-poll flow, `sessionStorage` re-attach, run-history list |

## Run-request file shape

Written to `.pbg/runs/<run_id>/request.json`:

```json
{
  "run_id": "pkg.composites.demo__1715470512__abc123",
  "spec_id": "pkg.composites.demo",
  "pkg": "pbg_ws_increase_demo",
  "workspace": "/abs/path/to/workspace",
  "overrides": {"rate": 2.5},
  "steps": 5,
  "emit_paths": ["stores/level"],
  "db_file": "/abs/path/to/workspace/.pbg/composite-runs.db",
  "log_path": ".pbg/runs/pkg.composites.demo__1715470512__abc123/run.log"
}
```

---

## Task 1: `runs_meta` schema migration + metadata helpers

**Files:**
- Modify: `/Users/eranagmon/code/vivarium-dashboard/vivarium_dashboard/lib/composite_runs.py`
- Test: `/Users/eranagmon/code/vivarium-dashboard/tests/test_composite_runs.py`

Adds four nullable columns (`pid`, `progress_step`, `log_path`, `heartbeat_at`) via guarded `ALTER TABLE`, enables WAL mode, and adds the helpers the runner and endpoints need. `save_metadata` gains a required `n_steps` (the *requested* total, so the progress bar always has a denominator) and an optional `log_path`.

- [ ] **Step 1: Write the failing migration test**

Add to `tests/test_composite_runs.py`:

```python
def test_connect_adds_new_columns_to_legacy_db(tmp_path):
    """connect() migrates a pre-existing DB that lacks the new columns."""
    import sqlite3
    db_file = tmp_path / "runs.db"
    # Simulate a legacy DB: original 8-column schema, one row.
    legacy = sqlite3.connect(str(db_file))
    legacy.execute(
        "CREATE TABLE runs_meta (run_id TEXT PRIMARY KEY, spec_id TEXT NOT NULL, "
        "label TEXT, params_json TEXT, started_at REAL NOT NULL, "
        "completed_at REAL, n_steps INTEGER, status TEXT NOT NULL)"
    )
    legacy.execute(
        "INSERT INTO runs_meta (run_id, spec_id, started_at, status) "
        "VALUES ('r-old', 's', 1.0, 'completed')"
    )
    legacy.commit()
    legacy.close()

    conn = connect(db_file)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(runs_meta)")}
    assert {"pid", "progress_step", "log_path", "heartbeat_at"} <= cols
    # Legacy row survived.
    row = conn.execute("SELECT spec_id FROM runs_meta WHERE run_id='r-old'").fetchone()
    assert row["spec_id"] == "s"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/eranagmon/code/vivarium-dashboard && python -m pytest tests/test_composite_runs.py::test_connect_adds_new_columns_to_legacy_db -v`
Expected: FAIL — assertion on `cols` (new columns absent).

- [ ] **Step 3: Add the migration + WAL to `connect()`**

In `composite_runs.py`, replace the `connect` function (currently lines 36–45) with:

```python
_NEW_COLUMNS = {
    "pid": "INTEGER",
    "progress_step": "INTEGER",
    "log_path": "TEXT",
    "heartbeat_at": "REAL",
}


def _migrate_runs_meta(conn: sqlite3.Connection) -> None:
    """Add any missing nullable columns to an existing runs_meta table."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(runs_meta)")}
    for name, sqltype in _NEW_COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE runs_meta ADD COLUMN {name} {sqltype}")
    conn.commit()


def connect(db_file: str | Path) -> sqlite3.Connection:
    """Open the runs DB, ensure schema + migrations, enable WAL."""
    db_file = Path(db_file)
    db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(_SCHEMA_RUNS_META)
    conn.execute(_INDEX_RUNS_META)
    _migrate_runs_meta(conn)
    conn.commit()
    return conn
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/eranagmon/code/vivarium-dashboard && python -m pytest tests/test_composite_runs.py::test_connect_adds_new_columns_to_legacy_db -v`
Expected: PASS

- [ ] **Step 5: Write failing tests for the new helpers**

Add to `tests/test_composite_runs.py`:

```python
def test_save_metadata_stores_requested_n_steps_and_log_path(tmp_path):
    conn = connect(tmp_path / "runs.db")
    save_metadata(conn, spec_id="s", run_id="r1", params={}, label="",
                  started_at=1.0, n_steps=20, log_path=".pbg/runs/r1/run.log")
    meta = query_run_meta(conn, run_id="r1")
    assert meta["n_steps"] == 20
    assert meta["log_path"] == ".pbg/runs/r1/run.log"
    assert meta["status"] == "running"
    assert meta["progress_step"] == 0


def test_update_progress_advances_step_and_heartbeat(tmp_path):
    conn = connect(tmp_path / "runs.db")
    save_metadata(conn, spec_id="s", run_id="r1", params={}, label="",
                  started_at=1.0, n_steps=10)
    update_progress(conn, run_id="r1", progress_step=4, heartbeat_at=123.0)
    meta = query_run_meta(conn, run_id="r1")
    assert meta["progress_step"] == 4
    assert meta["heartbeat_at"] == 123.0


def test_set_pid_records_pid(tmp_path):
    conn = connect(tmp_path / "runs.db")
    save_metadata(conn, spec_id="s", run_id="r1", params={}, label="",
                  started_at=1.0, n_steps=10)
    set_pid(conn, run_id="r1", pid=4242)
    assert query_run_meta(conn, run_id="r1")["pid"] == 4242


def test_mark_orphaned_sets_terminal_status(tmp_path):
    conn = connect(tmp_path / "runs.db")
    save_metadata(conn, spec_id="s", run_id="r1", params={}, label="",
                  started_at=1.0, n_steps=10)
    mark_orphaned(conn, run_id="r1")
    meta = query_run_meta(conn, run_id="r1")
    assert meta["status"] == "orphaned"
    assert meta["completed_at"] is not None


def test_query_run_meta_returns_none_for_unknown(tmp_path):
    conn = connect(tmp_path / "runs.db")
    assert query_run_meta(conn, run_id="nope") is None


def test_prune_runs_keeps_only_newest_n_per_spec(tmp_path):
    conn = connect(tmp_path / "runs.db")
    for i in range(5):
        save_metadata(conn, spec_id="s", run_id=f"r{i}", params={}, label="",
                      started_at=float(i), n_steps=1)
    save_metadata(conn, spec_id="other", run_id="x", params={}, label="",
                  started_at=99.0, n_steps=1)
    prune_runs(conn, spec_id="s", keep=2)
    remaining = sorted(r["run_id"] for r in query_runs(conn, spec_id="s"))
    assert remaining == ["r3", "r4"]
    # Other spec untouched.
    assert len(query_runs(conn, spec_id="other")) == 1
```

Update the **existing** `save_metadata` callers in this file to pass `n_steps` — every existing call (`test_save_and_query_metadata`, `test_complete_metadata_updates_status`, `test_query_runs_filtered_by_spec_id`, `test_query_runs_returns_newest_first`, and any others) gains `n_steps=<int>` (use `n_steps=10` or the step count already implied by the test). Also add the new names to the import block at the top of the file:

```python
from vivarium_dashboard.lib.composite_runs import (
    connect, save_metadata, complete_metadata, query_runs, query_run,
    query_run_meta, update_progress, set_pid, mark_orphaned, prune_runs,
    inject_sqlite_emitter, auto_label, inject_emitter_for_paths,
)
```

- [ ] **Step 6: Run the new tests to verify they fail**

Run: `cd /Users/eranagmon/code/vivarium-dashboard && python -m pytest tests/test_composite_runs.py -v`
Expected: the five new tests FAIL with `ImportError` / `NameError` (helpers not defined); existing tests still pass.

- [ ] **Step 7: Implement the helpers**

In `composite_runs.py`, replace `save_metadata` (currently lines 58–68) with the version below and add the new functions after `complete_metadata`:

```python
def save_metadata(conn: sqlite3.Connection, *, spec_id: str, run_id: str,
                  params: dict | None, label: str, started_at: float,
                  n_steps: int, log_path: str | None = None) -> None:
    """Insert a new run row with status='running'.

    ``n_steps`` is the *requested* step total — stored up front so the UI
    progress bar always has a denominator. ``complete_metadata`` may later
    overwrite it with the actual count.
    """
    conn.execute(
        "INSERT INTO runs_meta "
        "(run_id, spec_id, label, params_json, started_at, status, "
        " n_steps, log_path, progress_step) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
        (run_id, spec_id, label, json.dumps(params or {}),
         started_at, "running", n_steps, log_path),
    )
    conn.commit()


def query_run_meta(conn: sqlite3.Connection, *, run_id: str) -> dict | None:
    """Return the runs_meta row for one run as a dict, or None if absent."""
    row = conn.execute(
        "SELECT run_id, spec_id, label, params_json, started_at, completed_at, "
        "n_steps, status, pid, progress_step, log_path, heartbeat_at "
        "FROM runs_meta WHERE run_id=?",
        (run_id,),
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    try:
        d["params"] = json.loads(d.pop("params_json") or "{}")
    except json.JSONDecodeError:
        d["params"] = {}
    return d


def update_progress(conn: sqlite3.Connection, *, run_id: str,
                    progress_step: int, heartbeat_at: float) -> None:
    """Advance the live progress counter + heartbeat for a running run."""
    conn.execute(
        "UPDATE runs_meta SET progress_step=?, heartbeat_at=? WHERE run_id=?",
        (progress_step, heartbeat_at, run_id),
    )
    conn.commit()


def set_pid(conn: sqlite3.Connection, *, run_id: str, pid: int) -> None:
    """Record the detached child PID once it has been spawned."""
    conn.execute("UPDATE runs_meta SET pid=? WHERE run_id=?", (pid, run_id))
    conn.commit()


def mark_orphaned(conn: sqlite3.Connection, *, run_id: str) -> None:
    """Mark a run whose process died without writing a terminal status."""
    conn.execute(
        "UPDATE runs_meta SET status='orphaned', completed_at=? WHERE run_id=?",
        (time.time(), run_id),
    )
    conn.commit()


PRUNE_KEEP = 20


def prune_runs(conn: sqlite3.Connection, *, spec_id: str,
               keep: int = PRUNE_KEEP) -> int:
    """Delete all but the newest ``keep`` runs for ``spec_id``.

    Removes both the runs_meta rows and their history rows. Returns the
    number of runs deleted.
    """
    rows = conn.execute(
        "SELECT run_id FROM runs_meta WHERE spec_id=? "
        "ORDER BY started_at DESC", (spec_id,),
    ).fetchall()
    stale = [r[0] for r in rows[keep:]]
    if not stale:
        return 0
    placeholders = ",".join("?" * len(stale))
    has_history = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='history'"
    ).fetchone()
    if has_history:
        conn.execute(
            f"DELETE FROM history WHERE simulation_id IN ({placeholders})",
            stale,
        )
    conn.execute(
        f"DELETE FROM runs_meta WHERE run_id IN ({placeholders})", stale,
    )
    conn.commit()
    return len(stale)
```

- [ ] **Step 8: Run the full file's tests to verify they pass**

Run: `cd /Users/eranagmon/code/vivarium-dashboard && python -m pytest tests/test_composite_runs.py -v`
Expected: PASS — all tests, new and existing.

- [ ] **Step 9: Commit**

```bash
cd /Users/eranagmon/code/vivarium-dashboard
git add vivarium_dashboard/lib/composite_runs.py tests/test_composite_runs.py
git commit -m "feat(composite-runs): migrate runs_meta schema + add run-lifecycle helpers"
```

---

## Task 2: `run_runner.py` — detached run logic

**Files:**
- Create: `/Users/eranagmon/code/vivarium-dashboard/vivarium_dashboard/lib/run_runner.py`
- Test: `/Users/eranagmon/code/vivarium-dashboard/tests/test_run_runner.py`

The pure run logic, extracted out of the HTTP handler. `execute(request_path)` loads the run-request, resolves the composite state (generator or file spec — mirrors the old handler), injects the `SQLiteEmitter`, runs in 1-step chunks writing progress after each, renders viz to `viz.json`, and sets a terminal status. State is loaded from the request file — never argv — which is what eliminates the ARG_MAX bug.

- [ ] **Step 1: Write the failing test**

Create `tests/test_run_runner.py`:

```python
"""Unit tests for vivarium_dashboard.lib.run_runner."""
import json
import sys
from pathlib import Path

import pytest

from vivarium_dashboard.lib.run_runner import execute
from vivarium_dashboard.lib.composite_runs import connect, query_run_meta, query_run

_REPO_ROOT = Path(__file__).parent.parent
FIXTURE_WS = _REPO_ROOT / "tests" / "_fixtures" / "ws_increase_demo"


def _write_request(tmp_path, *, steps=3, spec_id=None, overrides=None):
    """Copy the fixture workspace to tmp and write a run-request file."""
    import shutil
    ws = tmp_path / "ws"
    shutil.copytree(FIXTURE_WS, ws)
    if str(ws) not in sys.path:
        sys.path.insert(0, str(ws))
    run_id = "test-run-1"
    run_dir = ws / ".pbg" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    request = {
        "run_id": run_id,
        "spec_id": spec_id or "pbg_ws_increase_demo.composites.increase-demo",
        "pkg": "pbg_ws_increase_demo",
        "workspace": str(ws),
        "overrides": overrides or {},
        "steps": steps,
        "emit_paths": [],
        "db_file": str(ws / ".pbg" / "composite-runs.db"),
        "log_path": f".pbg/runs/{run_id}/run.log",
    }
    request_path = run_dir / "request.json"
    request_path.write_text(json.dumps(request))
    # Seed the runs_meta row the way the POST handler would.
    conn = connect(request["db_file"])
    from vivarium_dashboard.lib.composite_runs import save_metadata
    save_metadata(conn, spec_id=request["spec_id"], run_id=run_id, params={},
                  label="", started_at=0.0, n_steps=steps,
                  log_path=request["log_path"])
    conn.close()
    return ws, request_path, run_id


@pytest.mark.skipif(not FIXTURE_WS.is_dir(), reason="fixture workspace absent")
def test_execute_completes_and_persists_trajectory(tmp_path):
    ws, request_path, run_id = _write_request(tmp_path, steps=3)
    rc = execute(request_path)
    assert rc == 0
    conn = connect(ws / ".pbg" / "composite-runs.db")
    meta = query_run_meta(conn, run_id=run_id)
    assert meta["status"] == "completed"
    assert meta["progress_step"] == 3
    trajectory = query_run(conn, run_id=run_id)
    assert len(trajectory) >= 1
    conn.close()


@pytest.mark.skipif(not FIXTURE_WS.is_dir(), reason="fixture workspace absent")
def test_execute_marks_failed_on_bad_spec(tmp_path):
    ws, request_path, run_id = _write_request(
        tmp_path, steps=2, spec_id="pbg_ws_increase_demo.composites.does-not-exist")
    rc = execute(request_path)
    assert rc == 1
    conn = connect(ws / ".pbg" / "composite-runs.db")
    meta = query_run_meta(conn, run_id=run_id)
    assert meta["status"] == "failed"
    conn.close()
    # Traceback / error landed in the log.
    log = ws / meta["log_path"]
    assert log.is_file() and log.stat().st_size > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/eranagmon/code/vivarium-dashboard && python -m pytest tests/test_run_runner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'vivarium_dashboard.lib.run_runner'`.

- [ ] **Step 3: Implement `run_runner.py`**

Create `vivarium_dashboard/lib/run_runner.py`:

```python
"""Detached composite-run executor.

``execute(request_path)`` is the entry point the ``vivarium-dashboard
run-composite`` CLI calls in a detached process. It is pure: no HTTP, no
module globals — everything it needs comes from the run-request file. State
is loaded from that file, never from argv, which structurally eliminates the
``OSError: [Errno 7] Argument list too long`` failure mode.
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path

from vivarium_dashboard.lib import composite_runs as cr

# A run exceeding this self-terminates with status='failed'. Matches the
# "tens of minutes" target from the design spec.
MAX_RUNTIME_SEC = 1800


@dataclass
class RunRequest:
    run_id: str
    spec_id: str
    pkg: str
    workspace: Path
    overrides: dict
    steps: int
    emit_paths: list
    db_file: str
    log_path: str

    @classmethod
    def from_file(cls, path: Path) -> "RunRequest":
        data = json.loads(Path(path).read_text())
        return cls(
            run_id=data["run_id"],
            spec_id=data["spec_id"],
            pkg=data["pkg"],
            workspace=Path(data["workspace"]),
            overrides=data.get("overrides") or {},
            steps=int(data["steps"]),
            emit_paths=data.get("emit_paths") or [],
            db_file=data["db_file"],
            log_path=data["log_path"],
        )


def _resolve_state(req: RunRequest) -> dict:
    """Resolve the composite state — generator entry first, then file spec.

    Mirrors the resolution the old _post_composite_test_run handler did.
    Raises a clear error if neither path yields a state.
    """
    # Generator-kind branch.
    try:
        from pbg_superpowers.composite_generator import (
            _REGISTRY, build_generator, discover_generators,
        )
        if not _REGISTRY:
            discover_generators()
        entry = _REGISTRY.get(req.spec_id)
        if entry is not None:
            doc = build_generator(entry, overrides=req.overrides)
            if isinstance(doc, dict) and isinstance(doc.get("state"), dict):
                return doc["state"]
            return doc
    except ImportError:
        pass

    # File-based spec branch.
    from vivarium_dashboard.lib.composite_lookup import (
        find_composite_path, substitute_parameters,
    )
    path = find_composite_path(req.workspace, req.pkg, req.spec_id)
    if path is None:
        raise FileNotFoundError(
            f"composite spec not found: {req.spec_id} "
            f"(not a registered generator, no spec file)"
        )
    text = path.read_text()
    spec = json.loads(text) if path.suffix.lower() == ".json" else __import__(
        "yaml").safe_load(text)
    return substitute_parameters(spec.get("state") or {},
                                 spec.get("parameters") or {},
                                 req.overrides)


def _render_viz(composite, run_dir: Path) -> None:
    """Render Visualization-step HTML to viz.json. Best-effort — never raises."""
    try:
        from pbg_superpowers.visualization import render_results
        rendered = render_results(composite)
        viz_html = {
            ".".join(str(p) for p in path_tuple): payload
            for path_tuple, payload in rendered.items()
        }
        (run_dir / "viz.json").write_text(json.dumps(viz_html, default=str))
    except Exception:
        traceback.print_exc()


def execute(request_path: Path) -> int:
    """Run one composite to completion. Returns 0 on success, 1 on failure.

    All progress and results are written to the shared SQLite DB; stdout/stderr
    (captured by the spawning process into run.log) carries diagnostics.
    """
    request_path = Path(request_path)
    req = RunRequest.from_file(request_path)
    run_dir = request_path.parent

    if str(req.workspace) not in sys.path:
        sys.path.insert(0, str(req.workspace))

    conn = cr.connect(req.db_file)
    try:
        try:
            state = _resolve_state(req)
        except FileNotFoundError as e:
            # Most common: the ParCa cache (out/cache/initial_state.json) is
            # missing. Fail fast with a legible message rather than a crash.
            print(f"composite build failed: {e}", flush=True)
            cr.complete_metadata(conn, run_id=req.run_id, n_steps=0,
                                 status="failed")
            return 1

        if req.emit_paths:
            state = cr.inject_emitter_for_paths(state, req.emit_paths)
        state = cr.inject_sqlite_emitter(state, run_id=req.run_id,
                                         db_file=req.db_file)

        # build_core lives in the workspace's own package (e.g.
        # pbg_ws_increase_demo.core). Import it dynamically by package name.
        core_mod = __import__(f"{req.pkg}.core", fromlist=["build_core"])
        from process_bigraph import Composite
        from process_bigraph.emitter import SQLiteEmitter

        core = core_mod.build_core()
        core.register_link("SQLiteEmitter", SQLiteEmitter)
        composite = Composite({"state": state}, core=core)

        started = time.monotonic()
        for step in range(1, req.steps + 1):
            composite.run(1)
            cr.update_progress(conn, run_id=req.run_id, progress_step=step,
                               heartbeat_at=time.time())
            if time.monotonic() - started > MAX_RUNTIME_SEC:
                print(f"run exceeded max runtime ({MAX_RUNTIME_SEC}s) — "
                      f"terminating at step {step}", flush=True)
                cr.complete_metadata(conn, run_id=req.run_id, n_steps=step,
                                     status="failed")
                return 1

        _render_viz(composite, run_dir)
        cr.complete_metadata(conn, run_id=req.run_id, n_steps=req.steps,
                             status="completed")
        print(f"run {req.run_id} completed: {req.steps} steps", flush=True)
        return 0
    except Exception:
        traceback.print_exc()
        cr.complete_metadata(conn, run_id=req.run_id, n_steps=0, status="failed")
        return 1
    finally:
        conn.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/eranagmon/code/vivarium-dashboard && python -m pytest tests/test_run_runner.py -v`
Expected: PASS — both tests.

- [ ] **Step 5: Commit**

```bash
cd /Users/eranagmon/code/vivarium-dashboard
git add vivarium_dashboard/lib/run_runner.py tests/test_run_runner.py
git commit -m "feat(run-runner): detached composite-run executor with chunked progress"
```

---

## Task 3: `run-composite` CLI subcommand

**Files:**
- Modify: `/Users/eranagmon/code/vivarium-dashboard/vivarium_dashboard/cli.py:132-154`
- Test: `/Users/eranagmon/code/vivarium-dashboard/tests/test_run_composite_cli.py`

A thin argparse wrapper. This is the process `spawn_detached` launches.

- [ ] **Step 1: Write the failing test**

Create `tests/test_run_composite_cli.py`:

```python
"""Test the `vivarium-dashboard run-composite` CLI subcommand."""
import json
import shutil
import sys
from pathlib import Path

import pytest

from vivarium_dashboard.cli import main
from vivarium_dashboard.lib.composite_runs import (
    connect, save_metadata, query_run_meta,
)

_REPO_ROOT = Path(__file__).parent.parent
FIXTURE_WS = _REPO_ROOT / "tests" / "_fixtures" / "ws_increase_demo"


@pytest.mark.skipif(not FIXTURE_WS.is_dir(), reason="fixture workspace absent")
def test_run_composite_subcommand_executes_request(tmp_path):
    ws = tmp_path / "ws"
    shutil.copytree(FIXTURE_WS, ws)
    if str(ws) not in sys.path:
        sys.path.insert(0, str(ws))
    run_id = "cli-run-1"
    run_dir = ws / ".pbg" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    db_file = str(ws / ".pbg" / "composite-runs.db")
    request = {
        "run_id": run_id,
        "spec_id": "pbg_ws_increase_demo.composites.increase-demo",
        "pkg": "pbg_ws_increase_demo",
        "workspace": str(ws),
        "overrides": {},
        "steps": 2,
        "emit_paths": [],
        "db_file": db_file,
        "log_path": f".pbg/runs/{run_id}/run.log",
    }
    request_path = run_dir / "request.json"
    request_path.write_text(json.dumps(request))
    conn = connect(db_file)
    save_metadata(conn, spec_id=request["spec_id"], run_id=run_id, params={},
                  label="", started_at=0.0, n_steps=2,
                  log_path=request["log_path"])
    conn.close()

    rc = main(["run-composite", "--request", str(request_path)])
    assert rc == 0
    conn = connect(db_file)
    assert query_run_meta(conn, run_id=run_id)["status"] == "completed"
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/eranagmon/code/vivarium-dashboard && python -m pytest tests/test_run_composite_cli.py -v`
Expected: FAIL — argparse exits with "invalid choice: 'run-composite'".

- [ ] **Step 3: Add the subcommand**

In `cli.py`, add this handler after `cmd_migrate_investigations` (after line 129):

```python
def cmd_run_composite(args: argparse.Namespace) -> int:
    """CLI handler for the run-composite subcommand — runs one detached composite."""
    from vivarium_dashboard.lib.run_runner import execute
    return execute(Path(args.request))
```

In `main()`, after the `migrate-investigations` subparser block (after line 151, before `args = parser.parse_args(argv)`), add:

```python
    p_run = sub.add_parser(
        "run-composite",
        help="Execute one composite run from a run-request file (internal; "
             "spawned detached by the dashboard)",
    )
    p_run.add_argument("--request", required=True,
                       help="Path to the run-request JSON file")
    p_run.set_defaults(func=cmd_run_composite)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/eranagmon/code/vivarium-dashboard && python -m pytest tests/test_run_composite_cli.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/eranagmon/code/vivarium-dashboard
git add vivarium_dashboard/cli.py tests/test_run_composite_cli.py
git commit -m "feat(cli): add run-composite subcommand for detached runs"
```

---

## Task 4: `run_registry.py` — spawn + reconcile + concurrency

**Files:**
- Create: `/Users/eranagmon/code/vivarium-dashboard/vivarium_dashboard/lib/run_registry.py`
- Test: `/Users/eranagmon/code/vivarium-dashboard/tests/test_run_registry.py`

Process-lifecycle helpers: spawn a run fully detached, reconcile rows left `running` by a crash, and count in-flight runs for the concurrency cap.

- [ ] **Step 1: Write the failing test**

Create `tests/test_run_registry.py`:

```python
"""Unit tests for vivarium_dashboard.lib.run_registry."""
import os

from vivarium_dashboard.lib.composite_runs import (
    connect, save_metadata, complete_metadata, query_run_meta,
)
from vivarium_dashboard.lib.run_registry import (
    reconcile_stale_runs, count_running,
)


def test_reconcile_marks_dead_pid_orphaned(tmp_path):
    db_file = tmp_path / "runs.db"
    conn = connect(db_file)
    # A 'running' row whose pid is almost certainly not a live process.
    save_metadata(conn, spec_id="s", run_id="dead", params={}, label="",
                  started_at=1.0, n_steps=5)
    conn.execute("UPDATE runs_meta SET pid=? WHERE run_id='dead'", (999_999,))
    conn.commit()
    conn.close()

    n = reconcile_stale_runs(db_file)
    assert n == 1
    conn = connect(db_file)
    assert query_run_meta(conn, run_id="dead")["status"] == "orphaned"
    conn.close()


def test_reconcile_leaves_live_pid_running(tmp_path):
    db_file = tmp_path / "runs.db"
    conn = connect(db_file)
    save_metadata(conn, spec_id="s", run_id="alive", params={}, label="",
                  started_at=1.0, n_steps=5)
    conn.execute("UPDATE runs_meta SET pid=? WHERE run_id='alive'",
                 (os.getpid(),))
    conn.commit()
    conn.close()

    n = reconcile_stale_runs(db_file)
    assert n == 0
    conn = connect(db_file)
    assert query_run_meta(conn, run_id="alive")["status"] == "running"
    conn.close()


def test_reconcile_marks_null_pid_orphaned(tmp_path):
    db_file = tmp_path / "runs.db"
    conn = connect(db_file)
    save_metadata(conn, spec_id="s", run_id="nopid", params={}, label="",
                  started_at=1.0, n_steps=5)
    conn.close()
    assert reconcile_stale_runs(db_file) == 1
    conn = connect(db_file)
    assert query_run_meta(conn, run_id="nopid")["status"] == "orphaned"
    conn.close()


def test_count_running_counts_only_running(tmp_path):
    db_file = tmp_path / "runs.db"
    conn = connect(db_file)
    save_metadata(conn, spec_id="s", run_id="r1", params={}, label="",
                  started_at=1.0, n_steps=5)
    save_metadata(conn, spec_id="s", run_id="r2", params={}, label="",
                  started_at=2.0, n_steps=5)
    complete_metadata(conn, run_id="r2", n_steps=5, status="completed")
    conn.close()
    assert count_running(db_file) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/eranagmon/code/vivarium-dashboard && python -m pytest tests/test_run_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'vivarium_dashboard.lib.run_registry'`.

- [ ] **Step 3: Implement `run_registry.py`**

Create `vivarium_dashboard/lib/run_registry.py`:

```python
"""Process-lifecycle helpers for detached composite runs.

The dashboard server spawns runs via ``spawn_detached`` and reconciles
crash-orphaned rows via ``reconcile_stale_runs`` on startup. All read/write
goes through ``composite_runs`` — this module only deals with OS processes.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from vivarium_dashboard.lib import composite_runs as cr

# Maximum simultaneous in-flight runs. POST returns 429 above this.
CONCURRENCY_CAP = 4


def _pid_alive(pid: int) -> bool:
    """True if a process with this PID currently exists."""
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists but owned by another user — still 'alive' for our purposes.
        return True
    return True


def spawn_detached(request_path: Path, *, workspace: Path,
                   log_path: Path) -> int:
    """Launch `vivarium-dashboard run-composite` fully detached.

    ``start_new_session=True`` puts the child in its own process group so it
    survives a dashboard-server restart. stdout/stderr are redirected into
    ``log_path``. Returns the child PID.
    """
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_fh = open(log_path, "w")  # noqa: SIM115 — handed to the child; closed below
    env = os.environ.copy()
    env["PYTHONPATH"] = str(workspace) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.Popen(
        [sys.executable, "-m", "vivarium_dashboard.cli",
         "run-composite", "--request", str(request_path)],
        cwd=str(workspace),
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env=env,
    )
    log_fh.close()  # the child holds its own dup'd fd
    return proc.pid


def reconcile_stale_runs(db_file: str | Path) -> int:
    """Mark every 'running' row whose process is gone as 'orphaned'.

    Called on server startup. A row with a NULL pid (spawn never recorded
    one) or a dead pid is orphaned; a live pid is left alone — that run
    genuinely survived the restart. Returns the count reconciled.
    """
    db_file = Path(db_file)
    if not db_file.is_file():
        return 0
    conn = cr.connect(db_file)
    try:
        rows = conn.execute(
            "SELECT run_id, pid FROM runs_meta WHERE status='running'"
        ).fetchall()
        reconciled = 0
        for row in rows:
            pid = row["pid"]
            if pid is None or not _pid_alive(int(pid)):
                cr.mark_orphaned(conn, run_id=row["run_id"])
                reconciled += 1
        return reconciled
    finally:
        conn.close()


def count_running(db_file: str | Path) -> int:
    """Count rows currently in status='running'."""
    db_file = Path(db_file)
    if not db_file.is_file():
        return 0
    conn = cr.connect(db_file)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM runs_meta WHERE status='running'"
        ).fetchone()[0]
    finally:
        conn.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/eranagmon/code/vivarium-dashboard && python -m pytest tests/test_run_registry.py -v`
Expected: PASS — all four tests.

- [ ] **Step 5: Commit**

```bash
cd /Users/eranagmon/code/vivarium-dashboard
git add vivarium_dashboard/lib/run_registry.py tests/test_run_registry.py
git commit -m "feat(run-registry): detached spawn, startup reconcile, concurrency cap"
```

---

## Task 5: Rewrite `_post_composite_test_run` to the detached write-path

**Files:**
- Modify: `/Users/eranagmon/code/vivarium-dashboard/vivarium_dashboard/server.py:5338-5532`
- Test: `/Users/eranagmon/code/vivarium-dashboard/tests/test_composite_explorer_api.py`

The POST handler becomes thin: prune, write the run-request file, insert the `runs_meta` row, spawn detached, record the PID, return `202 {run_id}`. The existing end-to-end tests, which assert the old synchronous `200 {results}` contract, are updated to the new start-then-poll contract.

- [ ] **Step 1: Update the existing tests to the new contract**

In `tests/test_composite_explorer_api.py`, add this polling helper after `_get` (after line ~83):

```python
def _poll_until_terminal(base, run_id, timeout=30):
    """Poll the status endpoint until the run reaches a terminal state."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, body = _get(f"{base}/api/composite-run/{run_id}/status")
        if body.get("status") in ("completed", "failed", "orphaned"):
            return body
        time.sleep(0.3)
    raise AssertionError(f"run {run_id} did not finish within {timeout}s")
```

Replace `test_test_run_persists_and_returns_simulation_id` with:

```python
def test_test_run_returns_run_id_and_completes(server):
    base = server["url"]
    spec_id = "pbg_ws_increase_demo.composites.increase-demo"
    status, body = _post(f"{base}/api/composite-test-run", {
        "id": spec_id, "overrides": {"rate": 2.5}, "steps": 5,
    })
    assert status == 202
    assert "run_id" in body
    assert body["status"] == "running"
    final = _poll_until_terminal(base, body["run_id"])
    assert final["status"] == "completed"
    # DB row exists with the run.
    db_file = server["ws"] / ".pbg" / "composite-runs.db"
    assert db_file.is_file()
```

Update the other tests in the file that POST and then immediately read results
(`test_list_runs_includes_the_persisted_run`, `test_fetch_single_run_trajectory`,
`test_fetch_state_at_step`, `test_distinct_runs_get_distinct_ids`): after each
`_post(...)`, add `_poll_until_terminal(base, body["run_id"])` before asserting
on runs / trajectory / state, and change `body["simulation_id"]` references to
`body["run_id"]`. For `test_distinct_runs_get_distinct_ids`, assert
`b1["run_id"] != b2["run_id"]`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /Users/eranagmon/code/vivarium-dashboard && python -m pytest tests/test_composite_explorer_api.py -v`
Expected: FAIL — POST still returns `200` with `simulation_id`/`results`, not `202` with `run_id`; `/status` endpoint 404s.

- [ ] **Step 3: Rewrite the handler**

In `server.py`, replace the entire body of `_post_composite_test_run` (lines 5338–5532) with:

```python
    def _post_composite_test_run(self, body: dict):
        """POST /api/composite-test-run — start a detached composite run.

        Writes a run-request file, inserts the runs_meta row, spawns the
        run-composite CLI detached, and returns 202 {run_id} immediately.
        The run itself executes in a separate process; the browser polls
        /api/composite-run/<id>/status to follow it.
        """
        _ws_add_to_sys_path()
        from vivarium_dashboard.lib import composite_runs as cr
        from vivarium_dashboard.lib import run_registry
        from vivarium_dashboard.lib.composite_runs import auto_label

        spec_id = (body.get("id") or "").strip()
        overrides = body.get("overrides") or {}
        steps = int(body.get("steps") or 5)
        label = (body.get("label") or "").strip() or auto_label(overrides)
        emit_paths = body.get("emit_paths") or []
        if not isinstance(emit_paths, list):
            emit_paths = []
        if not spec_id:
            return self._json({"error": "missing id"}, 400)

        ws_data = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text())
        pkg = ws_data.get("package_path") or (
            "pbg_" + ws_data.get("name", "").replace("-", "_"))
        db_file = str(WORKSPACE / ".pbg" / "composite-runs.db")

        if run_registry.count_running(db_file) >= run_registry.CONCURRENCY_CAP:
            return self._json(
                {"error": "too many runs in progress — wait for one to finish"},
                429)

        run_id = cr.generate_run_id(spec_id, overrides)
        run_dir = WORKSPACE / ".pbg" / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        log_rel = str((run_dir / "run.log").relative_to(WORKSPACE))
        request_path = run_dir / "request.json"
        request_path.write_text(json.dumps({
            "run_id": run_id,
            "spec_id": spec_id,
            "pkg": pkg,
            "workspace": str(WORKSPACE),
            "overrides": overrides,
            "steps": steps,
            "emit_paths": emit_paths,
            "db_file": db_file,
            "log_path": log_rel,
        }))

        conn = cr.connect(db_file)
        try:
            cr.prune_runs(conn, spec_id=spec_id, keep=cr.PRUNE_KEEP)
            cr.save_metadata(conn, spec_id=spec_id, run_id=run_id,
                             params=overrides, label=label,
                             started_at=time.time(), n_steps=steps,
                             log_path=log_rel)
            try:
                pid = run_registry.spawn_detached(
                    request_path, workspace=WORKSPACE,
                    log_path=run_dir / "run.log")
            except Exception as e:  # noqa: BLE001 — surface the spawn failure
                cr.complete_metadata(conn, run_id=run_id, n_steps=0,
                                     status="failed")
                return self._json(
                    {"error": f"spawn failed: {e}", "run_id": run_id}, 500)
            cr.set_pid(conn, run_id=run_id, pid=pid)
        finally:
            conn.close()

        return self._json({"run_id": run_id, "status": "running"}, 202)
```

The `textwrap` import at the top of `server.py` may now be unused — leave it; `_render_composite_svg` still uses it.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /Users/eranagmon/code/vivarium-dashboard && python -m pytest tests/test_composite_explorer_api.py -v`
Expected: the POST tests still FAIL on the `/status` poll (404) — that endpoint arrives in Task 6. The POST itself should now return `202`. Confirm the `_post` calls return 202 and `run_id`; the `_poll_until_terminal` failures are expected and resolved by Task 6.

> If the subagent-driven runner requires all tests green before commit: commit here with the `/status`-dependent tests still red is acceptable **only because Task 6 immediately follows and is in the same plan**. Otherwise, implement Task 6 before running this step's full suite. Prefer doing Task 6 next without an intermediate commit if your runner blocks on red tests.

- [ ] **Step 5: Commit**

```bash
cd /Users/eranagmon/code/vivarium-dashboard
git add vivarium_dashboard/server.py tests/test_composite_explorer_api.py
git commit -m "feat(server): rewrite composite-test-run as detached background job"
```

---

## Task 6: `/status` endpoint + routing + startup reconcile

**Files:**
- Modify: `/Users/eranagmon/code/vivarium-dashboard/vivarium_dashboard/server.py` (do_GET routing ~line 729-737; new `_get_composite_run_status` method; `serve()` ~line 6518-6533)
- Test: `/Users/eranagmon/code/vivarium-dashboard/tests/test_composite_explorer_api.py`

Adds the lightweight status endpoint the browser polls, routes to it, and runs the reconcile pass on server startup.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_composite_explorer_api.py`:

```python
def test_status_endpoint_reports_completed(server):
    base = server["url"]
    spec_id = "pbg_ws_increase_demo.composites.increase-demo"
    _, body = _post(f"{base}/api/composite-test-run", {
        "id": spec_id, "steps": 4,
    })
    run_id = body["run_id"]
    final = _poll_until_terminal(base, run_id)
    assert final["status"] == "completed"
    assert final["progress_step"] == 4
    assert final["n_steps"] == 4


def test_status_endpoint_404s_for_unknown_run(server):
    base = server["url"]
    status, _ = _get_raw(f"{base}/api/composite-run/no-such-run/status")
    assert status == 404
```

Add this `_get_raw` helper next to `_get` (it tolerates non-2xx without raising):

```python
def _get_raw(url):
    import urllib.error
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /Users/eranagmon/code/vivarium-dashboard && python -m pytest tests/test_composite_explorer_api.py::test_status_endpoint_reports_completed tests/test_composite_explorer_api.py::test_status_endpoint_404s_for_unknown_run -v`
Expected: FAIL — `/status` route returns the generic 400 "use /state subpath" or similar (not handled).

- [ ] **Step 3: Add the route**

In `server.py` `do_GET`, the current block (lines 729–737) is:

```python
        if self.path.startswith("/api/composite-run/") and self.path.split("?", 1)[0].endswith("/state"):
            return self._get_composite_run_state()
        if self.path.startswith("/api/composite-run/"):
            return self._get_composite_run()
```

Insert a `/status` check immediately before the bare `/api/composite-run/` line:

```python
        if self.path.startswith("/api/composite-run/") and self.path.split("?", 1)[0].endswith("/state"):
            return self._get_composite_run_state()
        if self.path.startswith("/api/composite-run/") and self.path.split("?", 1)[0].endswith("/status"):
            return self._get_composite_run_status()
        if self.path.startswith("/api/composite-run/"):
            return self._get_composite_run()
```

- [ ] **Step 4: Add the `_get_composite_run_status` method**

In `server.py`, add this method immediately after `_get_composite_run_state` (which ends at line 2903):

```python
    def _get_composite_run_status(self):
        """GET /api/composite-run/<run_id>/status — lightweight run status.

        Returns {status, progress_step, n_steps, heartbeat_at}. For terminal
        states it also returns an `error` excerpt (failed/orphaned, from the
        run log) or `viz_html` (completed, from the run's viz.json).
        """
        _ws_add_to_sys_path()
        from vivarium_dashboard.lib import composite_runs as cr

        path_only = self.path.split("?", 1)[0]
        prefix = "/api/composite-run/"
        rest = path_only[len(prefix):]
        if not rest.endswith("/status"):
            return self._json({"error": "bad route"}, 400)
        run_id = rest[: -len("/status")]

        db_file = WORKSPACE / ".pbg" / "composite-runs.db"
        if not db_file.is_file():
            return self._json({"error": "no run database"}, 404)
        conn = cr.connect(db_file)
        try:
            meta = cr.query_run_meta(conn, run_id=run_id)
        finally:
            conn.close()
        if meta is None:
            return self._json({"error": "run not found"}, 404)

        resp = {
            "run_id": run_id,
            "status": meta["status"],
            "progress_step": meta.get("progress_step") or 0,
            "n_steps": meta.get("n_steps"),
            "heartbeat_at": meta.get("heartbeat_at"),
        }
        if meta["status"] in ("failed", "orphaned"):
            log_rel = meta.get("log_path")
            if log_rel:
                resp["log_path"] = log_rel
                log_full = WORKSPACE / log_rel
                if log_full.is_file():
                    resp["error"] = log_full.read_text()[-2000:]
        elif meta["status"] == "completed":
            viz_file = WORKSPACE / ".pbg" / "runs" / run_id / "viz.json"
            if viz_file.is_file():
                try:
                    resp["viz_html"] = json.loads(viz_file.read_text())
                except json.JSONDecodeError:
                    pass
        return self._json(resp, 200)
```

- [ ] **Step 5: Add the startup reconcile**

In `server.py` `serve()`, the current opening (lines 6523–6529) is:

```python
    global WORKSPACE
    WORKSPACE = Path(workspace).resolve()
    _ws_add_to_sys_path()
    # Register the active workspace root for ``vivarium_dashboard.lib`` helpers
    # that used to walk up from __file__.
    from vivarium_dashboard.lib._root import set_workspace_root
    set_workspace_root(WORKSPACE)
```

Add the reconcile call immediately after `set_workspace_root(WORKSPACE)`:

```python
    # Repair runs left 'running' by a previous crash/restart: a dead or
    # missing PID becomes 'orphaned'; a live PID is left to keep running.
    try:
        from vivarium_dashboard.lib.run_registry import reconcile_stale_runs
        n = reconcile_stale_runs(WORKSPACE / ".pbg" / "composite-runs.db")
        if n:
            print(f"reconciled {n} stale composite run(s) on startup")
    except Exception as e:  # noqa: BLE001 — never block server boot on this
        print(f"warning: run reconcile failed: {e}", file=sys.stderr)
```

- [ ] **Step 6: Run the full explorer API suite to verify it passes**

Run: `cd /Users/eranagmon/code/vivarium-dashboard && python -m pytest tests/test_composite_explorer_api.py -v`
Expected: PASS — all tests, including the Task 5 polling tests now that `/status` exists.

- [ ] **Step 7: Commit**

```bash
cd /Users/eranagmon/code/vivarium-dashboard
git add vivarium_dashboard/server.py tests/test_composite_explorer_api.py
git commit -m "feat(server): add run-status endpoint + startup reconcile of stale runs"
```

---

## Task 7: Git hygiene — gitignore `out/` + scoped `_active_branch_action` staging

**Files:**
- Modify: `/Users/eranagmon/code/vivarium-dashboard/vivarium_dashboard/server.py:711-763` (`_active_branch_action`)
- Modify: pbg-template scaffold gitignore (see Step 1 — locate it)
- Test: `/Users/eranagmon/code/vivarium-dashboard/tests/test_active_branch_staging.py`

The blanket `git add -A` in `_active_branch_action` (line 735) can sweep large untracked artifact dirs (`out/`, ~175 MB ParCa cache) into a commit. Scope the staging to the directories the dashboard authors so artifacts can never be committed, and ensure `out/` is gitignored in scaffolded workspaces.

- [ ] **Step 1: Add `out/` to the scaffold gitignore**

Find the gitignore template the pbg-template scaffold writes into new workspaces. Check `/Users/eranagmon/code/vivarium-dashboard/vivarium_dashboard/templates/` for a `.gitignore` or `gitignore` file (the loom-explore + model templates live under `templates/`). If found, add a line `out/` under the existing artifact-ignores. If no scaffold gitignore exists in vivarium-dashboard, this step is satisfied by Step 5 (workspace-level ignore) — note that in the commit message and skip.

- [ ] **Step 2: Write the failing test**

Create `tests/test_active_branch_staging.py`:

```python
"""_active_branch_action must never stage large untracked artifact dirs."""
import subprocess
from pathlib import Path

import pytest

from vivarium_dashboard import server as srv


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, check=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A minimal workspace git repo on a stage/* branch with work_state set."""
    ws = tmp_path / "ws"
    ws.mkdir()
    _git(["init"], ws)
    _git(["config", "user.email", "t@t"], ws)
    _git(["config", "user.name", "t"], ws)
    (ws / "workspace.yaml").write_text("name: test\n")
    (ws / "reports").mkdir()
    (ws / "reports" / "index.html").write_text("<html></html>")
    _git(["add", "-A"], ws)
    _git(["commit", "-m", "init"], ws)
    _git(["checkout", "-b", "stage/test"], ws)
    monkeypatch.setattr(srv, "WORKSPACE", ws)
    # Point work_state at this repo's active branch.
    import vivarium_dashboard.lib.work_state as work_state
    monkeypatch.setattr(work_state, "load_state",
                        lambda: {"active_branch": "stage/test"})
    monkeypatch.setattr(work_state, "save_state", lambda state: None)
    return ws


def test_untracked_out_dir_is_not_committed(repo):
    # A huge untracked artifact dir appears, exactly like the ParCa cache.
    (repo / "out").mkdir()
    (repo / "out" / "cache").mkdir()
    (repo / "out" / "cache" / "big.bin").write_text("x" * 1000)

    def action():
        (repo / "studies").mkdir(exist_ok=True)
        (repo / "studies" / "new.yaml").write_text("k: v\n")

    resp, code = srv._active_branch_action("test commit", action)
    assert code == 200, resp
    # The commit contains studies/new.yaml but NOT anything under out/.
    files = _git(["show", "--name-only", "--format=", "HEAD"], repo).stdout
    assert "studies/new.yaml" in files
    assert "out/" not in files
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd /Users/eranagmon/code/vivarium-dashboard && python -m pytest tests/test_active_branch_staging.py -v`
Expected: FAIL — `out/cache/big.bin` appears in the commit because `git add -A` stages everything.

- [ ] **Step 4: Scope the staging in `_active_branch_action`**

In `server.py`, `_active_branch_action` currently does (line 735):

```python
        subprocess.run(["git", "add", "-A"], cwd=WORKSPACE, check=True, capture_output=True)
        subprocess.run(["git", "reset", "HEAD", "--", "reports/"], cwd=WORKSPACE, check=False, capture_output=True)
```

Replace those two lines with scoped staging — stage only the content paths the dashboard authors, never artifact dirs:

```python
        # Stage only the content the dashboard authors. A blanket `git add -A`
        # can sweep large untracked artifact dirs (out/, the ~175 MB ParCa
        # cache) into the commit; scoping the pathspec makes that impossible.
        # reports/ is intentionally excluded — it is generated, not authored.
        _STAGE_PATHS = [
            "studies/", "investigations/", "models/", "scripts/",
            "workspace.yaml", "pyproject.toml", ".gitmodules", ".gitignore",
            "external/",
        ]
        subprocess.run(
            ["git", "add", "-A", "--", *_STAGE_PATHS],
            cwd=WORKSPACE, check=True, capture_output=True,
        )
        # Also stage any already-tracked top-level *.py / *.yaml the action
        # touched, without picking up untracked files.
        subprocess.run(
            ["git", "add", "--update"],
            cwd=WORKSPACE, check=True, capture_output=True,
        )
```

Note: `git add -A -- <pathspec>` with a pathspec that does not exist (e.g. no `models/` dir) is not an error — git silently ignores absent pathspecs. `git add --update` only stages modifications to already-tracked files, so it cannot introduce an untracked artifact dir.

- [ ] **Step 5: Add `out/` to this workspace's gitignore**

This step targets the *v2ecoli workspace*, not the vivarium-dashboard repo. Append `out/` to `/Users/eranagmon/code/v2ecoli-chromosome-rep1/.gitignore` if not already present (the file currently ignores `.pbg/composite-runs.db`, `reports/assets/cache/`, etc. but not `out/`). Use the Edit tool to add the line under the existing artifact ignores.

- [ ] **Step 6: Run the test to verify it passes**

Run: `cd /Users/eranagmon/code/vivarium-dashboard && python -m pytest tests/test_active_branch_staging.py -v`
Expected: PASS

- [ ] **Step 7: Run the full backend suite for regressions**

Run: `cd /Users/eranagmon/code/vivarium-dashboard && python -m pytest tests/ -x -q`
Expected: PASS — no regressions across the dashboard test suite.

- [ ] **Step 8: Commit**

```bash
cd /Users/eranagmon/code/vivarium-dashboard
git add vivarium_dashboard/server.py tests/test_active_branch_staging.py
# include the scaffold gitignore if Step 1 found one
git commit -m "fix(server): scope _active_branch_action staging so artifacts never get committed"
```

(The v2ecoli-chromosome-rep1 `.gitignore` edit from Step 5 is committed separately in that workspace, not here.)

---

## Task 8: `bigraph-loom-explore` — run API helpers

**Files:**
- Modify: `/Users/eranagmon/code/bigraph-loom-explore/src/api.ts`
- Test: `/Users/eranagmon/code/bigraph-loom-explore/src/__tests__/api.test.ts`

Add typed fetch helpers for the new start-then-poll contract. These are the seam the `RunPanel` rewrite builds on.

- [ ] **Step 1: Write the failing test**

Add to `src/__tests__/api.test.ts` (a new `describe` block):

```typescript
describe('run lifecycle fetch helpers', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('startRun POSTs to composite-test-run and returns run_id', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 202,
      json: async () => ({ run_id: 'r-1', status: 'running' }),
    });
    vi.stubGlobal('fetch', fetchMock);
    const { startRun } = await import('../api');
    const res = await startRun({ id: 'pkg.composites.demo', steps: 5, emit_paths: [] });
    expect(fetchMock).toHaveBeenCalledWith('/api/composite-test-run', expect.objectContaining({
      method: 'POST',
    }));
    expect(res).toEqual({ run_id: 'r-1', status: 'running' });
  });

  it('startRun surfaces a 429 cap error', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 429,
      json: async () => ({ error: 'too many runs in progress' }),
    });
    vi.stubGlobal('fetch', fetchMock);
    const { startRun } = await import('../api');
    await expect(startRun({ id: 'x', steps: 1, emit_paths: [] }))
      .rejects.toThrow(/too many runs/);
  });

  it('fetchRunStatus GETs the status endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ run_id: 'r-1', status: 'completed', progress_step: 5, n_steps: 5 }),
    });
    vi.stubGlobal('fetch', fetchMock);
    const { fetchRunStatus } = await import('../api');
    const res = await fetchRunStatus('r-1');
    expect(fetchMock).toHaveBeenCalledWith('/api/composite-run/r-1/status');
    expect(res.status).toBe('completed');
  });

  it('fetchRunTrajectory GETs the run endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ run_id: 'r-1', trajectory: [{ step: 0, state: {} }] }),
    });
    vi.stubGlobal('fetch', fetchMock);
    const { fetchRunTrajectory } = await import('../api');
    const res = await fetchRunTrajectory('r-1');
    expect(fetchMock).toHaveBeenCalledWith('/api/composite-run/r-1');
    expect(res.trajectory).toHaveLength(1);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /Users/eranagmon/code/bigraph-loom-explore && npm test -- src/__tests__/api.test.ts`
Expected: FAIL — `startRun`, `fetchRunStatus`, `fetchRunTrajectory` are not exported from `../api`.

- [ ] **Step 3: Implement the helpers**

Append to `src/api.ts`:

```typescript
// --- Run lifecycle (start-then-poll) -------------------------------------

export type RunStatusValue = 'running' | 'completed' | 'failed' | 'orphaned';

export interface StartRunArgs {
  id: string;
  steps: number;
  emit_paths: string[];
  overrides?: Record<string, unknown>;
  label?: string;
}

export interface StartRunResponse {
  run_id: string;
  status: RunStatusValue;
}

export interface RunStatus {
  run_id: string;
  status: RunStatusValue;
  progress_step: number;
  n_steps: number | null;
  heartbeat_at: number | null;
  error?: string;
  log_path?: string;
  viz_html?: Record<string, { html: string }>;
}

export interface RunTrajectory {
  run_id: string;
  trajectory: Array<{ step: number; time?: number; state: Record<string, unknown> }>;
}

/** Start a detached composite run. Resolves with {run_id}; rejects on non-2xx
 *  (notably 429 when the concurrency cap is hit) with the server's error text. */
export async function startRun(args: StartRunArgs): Promise<StartRunResponse> {
  const r = await fetch('/api/composite-test-run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(args),
  });
  const body = await r.json();
  if (!r.ok) throw new Error(body.error || `HTTP ${r.status}`);
  return body as StartRunResponse;
}

/** Poll one run's status. Cheap single-row read; safe to call on an interval. */
export async function fetchRunStatus(runId: string): Promise<RunStatus> {
  const r = await fetch(`/api/composite-run/${runId}/status`);
  const body = await r.json();
  if (!r.ok) throw new Error(body.error || `HTTP ${r.status}`);
  return body as RunStatus;
}

/** Fetch a run's trajectory. Works mid-run (partial) and after completion. */
export async function fetchRunTrajectory(runId: string): Promise<RunTrajectory> {
  const r = await fetch(`/api/composite-run/${runId}`);
  const body = await r.json();
  if (!r.ok) throw new Error(body.error || `HTTP ${r.status}`);
  return body as RunTrajectory;
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd /Users/eranagmon/code/bigraph-loom-explore && npm test -- src/__tests__/api.test.ts`
Expected: PASS — all helpers, plus the existing postMessage tests still green.

- [ ] **Step 5: Commit**

```bash
cd /Users/eranagmon/code/bigraph-loom-explore
git add src/api.ts src/__tests__/api.test.ts
git commit -m "feat(api): add start-then-poll run lifecycle helpers"
```

---

## Task 9: `bigraph-loom-explore` — RunPanel start-then-poll rewrite

**Files:**
- Modify: `/Users/eranagmon/code/bigraph-loom-explore/src/panels/RunPanel.tsx`
- Test: `/Users/eranagmon/code/bigraph-loom-explore/src/__tests__/RunPanel.test.tsx` (create)

Rewrite the run flow: POST returns a `run_id` in <1s; the panel shows a progress bar and polls `fetchRunStatus` every 1.5s; on terminal status it renders results (from `fetchRunTrajectory`) or the error/log link. The active `run_id` is stashed in `sessionStorage` so an iframe reload re-attaches to a live run. A small recent-runs list uses the existing `/api/composite-runs` endpoint.

- [ ] **Step 1: Write the failing test**

Create `src/__tests__/RunPanel.test.tsx`:

```typescript
// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { RunPanel } from '../panels/RunPanel';

beforeEach(() => {
  vi.resetModules();
  sessionStorage.clear();
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

function mockFetchSequence(handlers: Record<string, () => any>) {
  return vi.fn((url: string, opts?: any) => {
    for (const [pattern, fn] of Object.entries(handlers)) {
      if (url.includes(pattern)) {
        const { status = 200, body } = fn();
        return Promise.resolve({
          ok: status >= 200 && status < 300,
          status,
          json: async () => body,
        });
      }
    }
    throw new Error(`unexpected fetch: ${url}`);
  });
}

describe('RunPanel start-then-poll', () => {
  it('starts a run, polls status, and shows completion', async () => {
    let statusCalls = 0;
    const fetchMock = mockFetchSequence({
      '/api/composite-test-run': () => ({ status: 202, body: { run_id: 'r-1', status: 'running' } }),
      '/api/composite-run/r-1/status': () => {
        statusCalls += 1;
        return statusCalls < 2
          ? { body: { run_id: 'r-1', status: 'running', progress_step: 1, n_steps: 3 } }
          : { body: { run_id: 'r-1', status: 'completed', progress_step: 3, n_steps: 3 } };
      },
      '/api/composite-run/r-1': () => ({ body: { run_id: 'r-1', trajectory: [] } }),
      '/api/composite-runs': () => ({ body: { runs: [] } }),
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<RunPanel compositeId="pkg.composites.demo" emitSet={new Set()} />);
    fireEvent.click(screen.getByText('Run'));

    await waitFor(() => expect(screen.getByText(/completed/i)).toBeTruthy(),
      { timeout: 5000 });
    expect(sessionStorage.getItem('loom-explore:active-run')).toBeNull();
  });

  it('re-attaches to a running run from sessionStorage on mount', async () => {
    sessionStorage.setItem('loom-explore:active-run',
      JSON.stringify({ run_id: 'r-prev', composite_id: 'pkg.composites.demo' }));
    const fetchMock = mockFetchSequence({
      '/api/composite-run/r-prev/status': () => ({
        body: { run_id: 'r-prev', status: 'completed', progress_step: 5, n_steps: 5 },
      }),
      '/api/composite-run/r-prev': () => ({ body: { run_id: 'r-prev', trajectory: [] } }),
      '/api/composite-runs': () => ({ body: { runs: [] } }),
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<RunPanel compositeId="pkg.composites.demo" emitSet={new Set()} />);
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith('/api/composite-run/r-prev/status'),
      { timeout: 5000 });
  });
});
```

If `@testing-library/react` is not already a dev dependency, add it:
`cd /Users/eranagmon/code/bigraph-loom-explore && npm install -D @testing-library/react @testing-library/jest-dom`

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /Users/eranagmon/code/bigraph-loom-explore && npm test -- src/__tests__/RunPanel.test.tsx`
Expected: FAIL — the panel still uses the old single-fetch flow; no polling, no `sessionStorage` re-attach.

- [ ] **Step 3: Rewrite `RunPanel.tsx`**

Replace `src/panels/RunPanel.tsx` with the version below. It keeps `ObservableRow` and the `RunPanelProps`/`inInvestigation` logic unchanged; the run flow and rendering are rewritten.

```typescript
import { useState, useEffect, useRef, useCallback } from 'react';
import type React from 'react';
import { JsonTree } from './JsonNode';
import {
  postRunComplete, startRun, fetchRunStatus, fetchRunTrajectory,
  type RunStatus,
} from '../api';

export interface RunPanelProps {
  compositeId: string | null;
  emitSet: Set<string>;
  runContext?: string;
}

const ACTIVE_RUN_KEY = 'loom-explore:active-run';
const POLL_MS = 1500;

/** One observable row: expandable; step navigator + JSON tree. */
function ObservableRow({ name, entries }: { name: string; entries: any[] }) {
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState(entries.length ? entries.length - 1 : 0);
  const total = entries.length;
  const current = (entries[step] || {}) as Record<string, unknown>;

  const visible: Record<string, unknown> = {};
  Object.entries(current).forEach(([k, v]) => {
    if (k === 'time' || k.startsWith('_')) return;
    visible[k] = v;
  });

  const previewKv = Object.entries(visible).slice(0, 1)[0];
  const previewStr = previewKv
    ? (() => {
        const v = previewKv[1];
        if (v === null || typeof v !== 'object') return String(v);
        if (Array.isArray(v)) return `list[${v.length}]`;
        return `{${Object.keys(v as object).length} keys}`;
      })()
    : '—';

  return (
    <>
      <tr style={{ borderBottom: '1px solid #f3f4f6', cursor: 'pointer' }}
          onClick={() => setOpen((o) => !o)}>
        <td style={{ padding: '6px 8px' }}>
          <span style={{ display: 'inline-block', width: 14, color: '#6b7280' }}>
            {open ? '▾' : '▸'}
          </span>
          <code>{name}</code>
        </td>
        <td style={{ padding: '6px 8px' }}>{total}</td>
        <td style={{ padding: '6px 8px', fontFamily: 'monospace', fontSize: 12, color: '#4b5563' }}>
          {previewStr}
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={3} style={{ background: '#fafafa', padding: 0 }}>
            <div style={{ padding: 10 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8, fontSize: 13 }}>
                <button onClick={() => setStep((s) => Math.max(0, s - 1))}
                        disabled={step === 0} style={{ padding: '2px 8px' }}>‹ Prev</button>
                <span style={{ color: '#374151' }}>
                  Step <strong>{step + 1}</strong> of {total}
                </span>
                <input type="range" min={0} max={Math.max(0, total - 1)} value={step}
                       onChange={(e) => setStep(parseInt(e.target.value, 10) || 0)}
                       style={{ flex: 1, maxWidth: 320 }} />
                <button onClick={() => setStep((s) => Math.min(total - 1, s + 1))}
                        disabled={step >= total - 1} style={{ padding: '2px 8px' }}>Next ›</button>
                {current.time !== undefined && (
                  <small style={{ color: '#6b7280' }}>time = {String(current.time)}</small>
                )}
              </div>
              <div style={{ background: '#fff', border: '1px solid #e5e7eb', borderRadius: 4,
                            padding: '8px 12px', maxHeight: 400, overflow: 'auto' }}>
                {Object.keys(visible).length === 0 ? (
                  <p style={{ color: '#9ca3af', fontSize: 13, margin: 0 }}>
                    No emitted fields at this step.
                  </p>
                ) : (
                  <JsonTree value={visible} />
                )}
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

/** Group a flat trajectory list into ObservableRow-friendly per-key entries. */
function trajectoryToObservables(
  trajectory: Array<{ step: number; state: Record<string, unknown> }>,
): Record<string, any[]> {
  const out: Record<string, any[]> = {};
  for (const row of trajectory) {
    for (const [k, v] of Object.entries(row.state || {})) {
      (out[k] ||= []).push(v);
    }
  }
  return out;
}

export function RunPanel(props: RunPanelProps) {
  const [steps, setSteps] = useState(5);
  const [runId, setRunId] = useState<string | null>(null);
  const [status, setStatus] = useState<RunStatus | null>(null);
  const [observables, setObservables] = useState<Record<string, any[]> | null>(null);
  const [startError, setStartError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const inInvestigation = !!(props.runContext && props.runContext.startsWith('investigation:'));
  const canRun = !!props.compositeId && !inInvestigation;
  const isRunning = status?.status === 'running' || (!!runId && !status);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const loadTrajectory = useCallback(async (id: string) => {
    try {
      const traj = await fetchRunTrajectory(id);
      setObservables(trajectoryToObservables(traj.trajectory));
    } catch {
      /* trajectory not ready yet — ignore, next poll retries */
    }
  }, []);

  // Poll one run until terminal. Independent cheap requests: a dropped poll
  // simply retries on the next tick.
  const beginPolling = useCallback((id: string) => {
    stopPolling();
    const tick = async () => {
      let s: RunStatus;
      try {
        s = await fetchRunStatus(id);
      } catch {
        return; // transient — try again next tick
      }
      setStatus(s);
      if (s.status === 'running') {
        void loadTrajectory(id);
      } else {
        stopPolling();
        void loadTrajectory(id);
        sessionStorage.removeItem(ACTIVE_RUN_KEY);
        if (s.status === 'completed' && props.compositeId) {
          postRunComplete(id, props.compositeId);
        }
      }
    };
    void tick();
    pollRef.current = setInterval(tick, POLL_MS);
  }, [stopPolling, loadTrajectory, props.compositeId]);

  // Re-attach to an in-flight run after an iframe reload / network blip.
  useEffect(() => {
    const raw = sessionStorage.getItem(ACTIVE_RUN_KEY);
    if (!raw) return;
    try {
      const saved = JSON.parse(raw) as { run_id: string; composite_id: string };
      if (saved.composite_id === props.compositeId && saved.run_id) {
        setRunId(saved.run_id);
        beginPolling(saved.run_id);
      }
    } catch {
      sessionStorage.removeItem(ACTIVE_RUN_KEY);
    }
    return stopPolling;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.compositeId]);

  async function handleRun() {
    if (!props.compositeId) {
      setStartError('No composite id — pop-out windows need ?id=<dotted-ref> in the URL.');
      return;
    }
    setStartError(null);
    setStatus(null);
    setObservables(null);
    try {
      const res = await startRun({
        id: props.compositeId,
        steps,
        emit_paths: Array.from(props.emitSet),
      });
      setRunId(res.run_id);
      sessionStorage.setItem(ACTIVE_RUN_KEY, JSON.stringify({
        run_id: res.run_id, composite_id: props.compositeId,
      }));
      beginPolling(res.run_id);
    } catch (e: any) {
      setStartError(String(e?.message || e));
    }
  }

  const wrapStyle: React.CSSProperties = { padding: 16, fontFamily: 'system-ui, sans-serif' };

  if (inInvestigation) {
    return (
      <div style={wrapStyle}>
        <h3 style={{ marginTop: 0 }}>Run</h3>
        <p style={{ color: '#6b7280' }}>
          Use the Study&apos;s Run controls to run with this investigation&apos;s emitters.
        </p>
      </div>
    );
  }

  const pct = status && status.n_steps
    ? Math.round((status.progress_step / status.n_steps) * 100)
    : 0;

  return (
    <div style={wrapStyle}>
      <h3 style={{ marginTop: 0 }}>Run</h3>
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
        <label>
          Steps{' '}
          <input type="number" min={1} max={100} value={steps}
                 onChange={(e) => setSteps(parseInt(e.target.value) || 1)}
                 style={{ width: 70 }} disabled={isRunning} />
        </label>
        <button onClick={handleRun} disabled={isRunning || !canRun}>
          {isRunning ? 'Running…' : 'Run'}
        </button>
        <small style={{ color: '#666' }}>
          Emit selections:{' '}
          {props.emitSet.size === 0
            ? <em>none — pick stores in the View tab</em>
            : Array.from(props.emitSet).join(', ')}
        </small>
      </div>

      {startError && (
        <div style={{ color: '#c00', marginTop: 8 }}>
          <strong>Could not start run:</strong> {startError}
        </div>
      )}

      {isRunning && status && (
        <div style={{ margin: '8px 0' }}>
          <div style={{ background: '#e5e7eb', borderRadius: 4, height: 10, overflow: 'hidden' }}>
            <div style={{ width: `${pct}%`, background: '#3b82f6', height: '100%' }} />
          </div>
          <small style={{ color: '#6b7280' }}>
            Step {status.progress_step} of {status.n_steps ?? '?'} — running detached;
            safe to reload this tab.
          </small>
        </div>
      )}
      {isRunning && !status && (
        <p style={{ color: '#6b7280' }}>Starting run…</p>
      )}

      {status && (status.status === 'failed' || status.status === 'orphaned') && (
        <div style={{ color: '#c00', marginTop: 8 }}>
          <p style={{ margin: 0 }}>
            <strong>Run {status.status}.</strong>{' '}
            {status.log_path && <span>See log: <code>{status.log_path}</code></span>}
          </p>
          {status.error && (
            <details style={{ marginTop: 6 }}>
              <summary style={{ cursor: 'pointer', color: '#7f1d1d' }}>Show log excerpt</summary>
              <pre style={{ background: '#fef2f2', border: '1px solid #fecaca', padding: 10,
                            fontSize: 11, lineHeight: 1.4, overflow: 'auto', maxHeight: 320,
                            marginTop: 6, whiteSpace: 'pre-wrap' }}>
                {status.error.trim()}
              </pre>
            </details>
          )}
        </div>
      )}

      {status?.status === 'completed' && (
        <p style={{ color: '#6b7280', fontSize: 13, margin: '4px 0 10px' }}>
          Run complete — <strong>{status.n_steps ?? 0}</strong> steps. Click any
          observable row to browse its trajectory.
        </p>
      )}

      {observables && (
        <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ background: '#f3f4f6' }}>
              <th style={{ textAlign: 'left', padding: '6px 8px' }}>Observable</th>
              <th style={{ textAlign: 'left', padding: '6px 8px', width: 80 }}>Steps</th>
              <th style={{ textAlign: 'left', padding: '6px 8px' }}>Latest preview</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(observables).sort().map(([k, entries]) => (
              <ObservableRow key={k} name={k} entries={entries} />
            ))}
            {!Object.keys(observables).length && (
              <tr>
                <td colSpan={3} style={{ padding: 12, color: '#666' }}>
                  No observables emitted. Toggle stores in the View tab to capture their values.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}

      {status?.viz_html && Object.keys(status.viz_html).length > 0 && (
        <div style={{ marginTop: 20 }}>
          <h4>Visualizations</h4>
          {Object.entries(status.viz_html).map(([path, payload]) => (
            <div key={path} style={{ marginBottom: 12, border: '1px solid #e5e7eb', borderRadius: 4 }}>
              <div style={{ padding: '6px 10px', background: '#f3f4f6', fontFamily: 'monospace', fontSize: 12 }}>
                {path}
              </div>
              <iframe srcDoc={(payload as { html: string }).html || '<p>No HTML</p>'}
                      style={{ width: '100%', height: 320, border: 0 }}
                      sandbox="allow-scripts" />
            </div>
          ))}
        </div>
      )}

      {!runId && !startError && (
        <p style={{ color: '#888' }}>
          Click <strong>Run</strong> to execute the composite for the chosen number of steps.
        </p>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd /Users/eranagmon/code/bigraph-loom-explore && npm test -- src/__tests__/RunPanel.test.tsx`
Expected: PASS — both tests.

- [ ] **Step 5: Run the full frontend test + typecheck**

Run: `cd /Users/eranagmon/code/bigraph-loom-explore && npm test && npx tsc -b`
Expected: PASS — all vitest tests; `tsc` reports no type errors.

- [ ] **Step 6: Commit**

```bash
cd /Users/eranagmon/code/bigraph-loom-explore
git add src/panels/RunPanel.tsx src/__tests__/RunPanel.test.tsx package.json package-lock.json
git commit -m "feat(run-panel): start-then-poll run flow with progress + sessionStorage re-attach"
```

---

## Task 10: Build the loom-explore bundle + copy into vivarium-dashboard

**Files:**
- Build output: `/Users/eranagmon/code/bigraph-loom-explore/dist/`
- Copy target: `/Users/eranagmon/code/vivarium-dashboard/vivarium_dashboard/static/loom-explore/`

The dashboard serves a *pre-built* copy of loom-explore from its `static/` dir. After the frontend changes, rebuild and copy the bundle so the running dashboard picks them up.

- [ ] **Step 1: Build the production bundle**

Run: `cd /Users/eranagmon/code/bigraph-loom-explore && npm run build`
Expected: `tsc -b && vite build` succeeds; fresh hashed assets appear in `dist/assets/` (e.g. `index-<hash>.js`, `index-<hash>.css`) and `dist/index.html` references them.

- [ ] **Step 2: Replace the bundled copy in vivarium-dashboard**

Run:

```bash
cd /Users/eranagmon/code/vivarium-dashboard
rm -rf vivarium_dashboard/static/loom-explore
cp -R /Users/eranagmon/code/bigraph-loom-explore/dist vivarium_dashboard/static/loom-explore
ls vivarium_dashboard/static/loom-explore vivarium_dashboard/static/loom-explore/assets
```

Expected: `index.html` + `assets/` with the new hashed files; old `index-C-yJVDve.js` / `index-5PBPMGmS.js` are gone.

- [ ] **Step 3: Verify the dashboard serves the new bundle**

Start the dashboard against the v2ecoli workspace and confirm the iframe loads:

```bash
cd /Users/eranagmon/code/v2ecoli-chromosome-rep1
.venv/bin/vivarium-dashboard serve --workspace /Users/eranagmon/code/v2ecoli-chromosome-rep1 --port 59266 &
sleep 4
curl -sS -o /dev/null -w "loom-explore: HTTP %{http_code}\n" http://127.0.0.1:59266/loom-explore/index.html
curl -sS http://127.0.0.1:59266/loom-explore/index.html | grep -o 'assets/index-[^"]*'
kill %1
```

Expected: `HTTP 200`; the `assets/index-*.js` reference matches the freshly built hash from Step 1.

- [ ] **Step 4: Commit the rebuilt bundle**

```bash
cd /Users/eranagmon/code/vivarium-dashboard
git add vivarium_dashboard/static/loom-explore
git commit -m "build: rebuild loom-explore bundle with start-then-poll run flow"
```

- [ ] **Step 5: Manual end-to-end verification**

With the dashboard running against the v2ecoli workspace:
1. Open the Composite Explorer, select `v2ecoli.composites.baseline.baseline`, set a small step count, click **Run** — confirm the progress bar appears and advances, and the POST returned in well under a second.
2. Reload the iframe mid-run — confirm polling re-attaches (progress bar reappears at the current step, not a blank slate).
3. Kill the dashboard process mid-run and restart it — confirm the startup log prints `reconciled N stale composite run(s)` only if the run actually died, and that a still-live run continues and the UI re-attaches.
4. Confirm a completed run shows the observable table and any visualizations.

Document the outcome of each step; if any fails, file it as a follow-up rather than silently fixing — the plan's automated tests are the contract, manual verification is the smoke check.

---

## Self-Review

**Spec coverage:**
- Detached background job → Tasks 2, 3, 4, 5 ✓
- Run-request file (kills ARG_MAX) → Task 2 (`RunRequest.from_file`), Task 5 (writes it) ✓
- Chunked run with progress + heartbeat → Task 2 ✓
- `max_runtime` guard → Task 2 (`MAX_RUNTIME_SEC`) ✓
- Missing-cache fast-fail → Task 2 (`_resolve_state` `FileNotFoundError` branch) ✓
- `runs_meta` schema additions + in-place migration → Task 1 ✓
- WAL + busy_timeout → Task 1 ✓
- Status enum incl. `orphaned` → Tasks 1 (`mark_orphaned`), 4 (`reconcile_stale_runs`) ✓
- Retention: scratchpad delete removed, `prune_runs(keep=20)` → Task 1 + Task 5 ✓
- Concurrency cap (429) → Task 4 (`CONCURRENCY_CAP`), Task 5 (check) ✓
- `GET /status` endpoint → Task 6 ✓
- Reconcile on startup → Task 6 ✓
- Per-run log file → Task 4 (`spawn_detached` redirects), Task 6 (status serves excerpt) ✓
- viz rendering persisted → Task 2 (`_render_viz` → viz.json), Task 6 (status serves it) ✓
- Git hygiene: gitignore `out/` + scoped staging → Task 7 ✓
- Frontend start-then-poll + progress + re-attach + run history + 429 handling → Tasks 8, 9 ✓
- Build + ship bundle → Task 10 ✓
- Tests: unit (1,2,3,4), integration/e2e (5,6), git-hygiene (7), frontend (8,9), manual (10) ✓

**Placeholder scan:** No TBD/TODO. Task 7 Step 1 has a conditional ("if found … else skip") but it is a concrete instruction with a defined fallback, not a placeholder. Task 5 Step 4 documents an intentional cross-task red-test window with explicit handling guidance.

**Type consistency:** `run_id` used consistently (not `simulation_id`) in all new code; the old `simulation_id` survives only inside `inject_sqlite_emitter`'s SQLiteEmitter config and the `history` table column, which is correct (that is process_bigraph's column name). `RunRequest` fields match the request-file JSON shape and Task 5's writer. `RunStatus` TS interface fields (`progress_step`, `n_steps`, `heartbeat_at`, `viz_html`, `log_path`, `error`) match `_get_composite_run_status`'s response dict. `save_metadata` signature (`n_steps` required, `log_path` optional) is consistent across Task 1's definition and all callers in Tasks 2, 3, 5 and the updated tests.
