# Simulations Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a workspace-wide **Simulations** tab to the dashboard that lists every persisted run across `.pbg/composite-runs.db` and every `studies/<name>/runs.db`, shows each row's composite + associated Studies + status + steps, and supports full deletion (DB rows + history + run dir + unlink from `study.yaml`).

**Architecture:** A pure aggregator module (`simulations_index.py`) walks every SQLite DB in the workspace, cross-references each `run_id` against every `study.yaml`'s `runs[]`, and returns one sorted list. A delete function performs the full-delete pass over a single `run_id`. Two thin HTTP handlers wrap the lib; one new frontend page + JS init renders the table.

**Tech Stack:** Python 3.12, `sqlite3` (WAL + busy-timeout via the existing `composite_runs.connect`), `pyyaml` for `study.yaml` round-trip. Frontend: vanilla JS in the existing `walkthrough.js`/`index.html.j2` style.

**Spec:** `docs/superpowers/specs/2026-05-15-simulations-tab-design.md`

**Branch:** All work goes on `simulations-tab` (already created off `main`). The worktree is at `/Users/eranagmon/code/vivarium-dashboard-sim`. Verify with `git branch --show-current` before each commit.

## Naming convention (resolves an existing collision)

The dashboard already has `_delete_simulation` handling `/api/simulation` for *workspace.yaml* `simulations:` entries (the Simulation Setup tab's concept — unrelated to runs). The new endpoints in this feature use a different name:

- **`GET /api/simulations`** (plural, list) → `_get_simulations` — no existing collision.
- **`DELETE /api/simulation-run`** (different path) → `_delete_simulation_run` with JSON body `{run_id}`. Matches the `do_DELETE` exact-path + body pattern already in use.

The frontend's user-facing tab label remains **"Simulations"** (per the spec) — only internal Python/route names dodge the conflict.

## File Structure

| File | Responsibility |
|---|---|
| `vivarium_dashboard/lib/simulations_index.py` *(create)* | Pure aggregator + delete: `list_simulations`, `delete_simulation`, `RunNotFound` |
| `tests/test_simulations_index.py` *(create)* | Unit tests for the aggregator + delete |
| `tests/test_simulations_api.py` *(create)* | Integration tests for both endpoints |
| `vivarium_dashboard/server.py` *(modify)* | Add `_get_simulations`, `_delete_simulation_run`; route both |
| `vivarium_dashboard/templates/index.html.j2` *(modify)* | New rail link + `<div data-page="simulations">` page scaffold |
| `vivarium_dashboard/static/walkthrough.js` *(modify)* | Add `simulations` to valid-pages; `_initSimulations`; `_deleteSimulationRun` |

---

## Task 1: `simulations_index.list_simulations`

**Files:**
- Create: `vivarium_dashboard/lib/simulations_index.py`
- Test: `tests/test_simulations_index.py`

The aggregator: walk SQLite DBs + cross-reference `study.yaml`, return a sorted list.

- [ ] **Step 1: Write the failing test**

Create `tests/test_simulations_index.py`:

```python
"""Unit tests for vivarium_dashboard.lib.simulations_index."""
from pathlib import Path

import yaml

from vivarium_dashboard.lib.composite_runs import connect, save_metadata
from vivarium_dashboard.lib.simulations_index import list_simulations


def _seed_run(db_file, *, spec_id, run_id, started_at, sim_name=None):
    conn = connect(db_file)
    save_metadata(conn, spec_id=spec_id, run_id=run_id, params={}, label="",
                  started_at=started_at, n_steps=3, log_path=None)
    if sim_name:
        conn.execute("UPDATE runs_meta SET sim_name=? WHERE run_id=?",
                     (sim_name, run_id))
        conn.commit()
    conn.close()


def test_list_walks_workspace_and_studies_dbs(tmp_path):
    ws = tmp_path / "ws"
    (ws / ".pbg").mkdir(parents=True)
    (ws / "studies" / "foo").mkdir(parents=True)
    _seed_run(ws / ".pbg" / "composite-runs.db",
              spec_id="pkg.x", run_id="r-scratch", started_at=10.0)
    _seed_run(ws / "studies" / "foo" / "runs.db",
              spec_id="pkg.y", run_id="r-baseline", started_at=20.0,
              sim_name="baseline")

    sims = list_simulations(ws)
    ids = [s["run_id"] for s in sims]
    assert ids == ["r-baseline", "r-scratch"]   # newest first
    assert sims[0]["db_path"] == "studies/foo/runs.db"
    assert sims[1]["db_path"] == ".pbg/composite-runs.db"
    assert sims[0]["sim_name"] == "baseline"
    # No study.yaml yet → empty studies annotation
    assert all(s["studies"] == [] for s in sims)


def test_list_cross_references_study_yaml_list_form(tmp_path):
    ws = tmp_path / "ws"
    (ws / "studies" / "foo").mkdir(parents=True)
    _seed_run(ws / "studies" / "foo" / "runs.db",
              spec_id="pkg.y", run_id="r-1", started_at=1.0)
    (ws / "studies" / "foo" / "study.yaml").write_text(
        yaml.safe_dump({"name": "foo", "runs": ["r-1"]}))

    sims = list_simulations(ws)
    assert len(sims) == 1
    assert sims[0]["studies"] == ["foo"]


def test_list_cross_references_study_yaml_dict_form(tmp_path):
    ws = tmp_path / "ws"
    (ws / "studies" / "foo").mkdir(parents=True)
    _seed_run(ws / "studies" / "foo" / "runs.db",
              spec_id="pkg.y", run_id="r-1", started_at=1.0)
    (ws / "studies" / "foo" / "study.yaml").write_text(
        yaml.safe_dump({"name": "foo",
                        "runs": [{"run_id": "r-1", "label": "baseline"}]}))

    sims = list_simulations(ws)
    assert sims[0]["studies"] == ["foo"]


def test_list_run_referenced_by_multiple_studies(tmp_path):
    ws = tmp_path / "ws"
    (ws / ".pbg").mkdir(parents=True)
    _seed_run(ws / ".pbg" / "composite-runs.db",
              spec_id="pkg.x", run_id="shared", started_at=1.0)
    for name in ("alpha", "beta"):
        sdir = ws / "studies" / name
        sdir.mkdir(parents=True)
        (sdir / "study.yaml").write_text(
            yaml.safe_dump({"name": name, "runs": ["shared"]}))

    sims = list_simulations(ws)
    assert sims[0]["studies"] == ["alpha", "beta"]


def test_list_tolerates_missing_dbs(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    # No .pbg/, no studies/ — should not raise
    assert list_simulations(ws) == []


def test_list_tolerates_malformed_study_yaml(tmp_path):
    ws = tmp_path / "ws"
    (ws / "studies" / "foo").mkdir(parents=True)
    _seed_run(ws / "studies" / "foo" / "runs.db",
              spec_id="pkg.y", run_id="r-1", started_at=1.0)
    (ws / "studies" / "foo" / "study.yaml").write_text("not: [valid: yaml")

    sims = list_simulations(ws)
    # The run still shows up; studies annotation is empty (yaml unparseable)
    assert len(sims) == 1
    assert sims[0]["studies"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/eranagmon/code/vivarium-dashboard-sim && python -m pytest tests/test_simulations_index.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'vivarium_dashboard.lib.simulations_index'`.

- [ ] **Step 3: Implement `list_simulations`**

Create `vivarium_dashboard/lib/simulations_index.py`:

```python
"""Workspace-wide simulations index: aggregate across SQLite DBs.

A *simulation* is one row in a ``runs_meta`` table written by an emitter
(today: ``SQLiteEmitter``). Rows live in two kinds of DBs:

- ``<workspace>/.pbg/composite-runs.db`` — Composite Explorer scratch runs.
- ``<workspace>/studies/<name>/runs.db`` — one per Study (baseline + variants).

``list_simulations`` walks both, cross-references each ``run_id`` against
every ``study.yaml``'s ``runs[]`` (Studies-association), and returns one
sorted list. ``delete_simulation`` performs the full-delete pass.
"""
from __future__ import annotations

import shutil
import sqlite3
import warnings
from pathlib import Path

import yaml

from vivarium_dashboard.lib import composite_runs as cr


class RunNotFound(Exception):
    """Raised by ``delete_simulation`` when ``run_id`` is in no known DB."""


def _discover_dbs(workspace: Path) -> list[tuple[Path, str]]:
    """Return list of (db_path, workspace_relative_str) for every runs DB.

    Skips missing files. Order: workspace-level DB first, then studies in
    alphabetical order (deterministic for tests).
    """
    dbs: list[tuple[Path, str]] = []
    scratch = workspace / ".pbg" / "composite-runs.db"
    if scratch.is_file():
        dbs.append((scratch, ".pbg/composite-runs.db"))
    studies_root = workspace / "studies"
    if studies_root.is_dir():
        for sdir in sorted(studies_root.iterdir()):
            if not sdir.is_dir():
                continue
            db = sdir / "runs.db"
            if db.is_file():
                dbs.append((db, f"studies/{sdir.name}/runs.db"))
    return dbs


def _row_to_dict(row, db_path_str: str) -> dict:
    """Convert a runs_meta SELECT row to the public dict shape."""
    return {
        "run_id": row["run_id"],
        "spec_id": row["spec_id"],
        "sim_name": row["sim_name"],
        "label": row["label"],
        "status": row["status"],
        "n_steps": row["n_steps"],
        "progress_step": row["progress_step"],
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
        "db_path": db_path_str,
        "studies": [],  # filled in by _annotate_studies
    }


def _read_runs_meta(db_path: Path, db_path_str: str) -> list[dict]:
    """SELECT every runs_meta row in a DB. Tolerates lock/timeout by returning []."""
    try:
        conn = cr.connect(db_path)
    except sqlite3.OperationalError as e:
        warnings.warn(f"simulations_index: skipping {db_path_str}: {e}")
        return []
    try:
        rows = conn.execute(
            "SELECT run_id, spec_id, sim_name, label, status, n_steps, "
            "progress_step, started_at, completed_at "
            "FROM runs_meta ORDER BY started_at DESC"
        ).fetchall()
    except sqlite3.OperationalError as e:
        warnings.warn(f"simulations_index: skipping {db_path_str}: {e}")
        return []
    finally:
        conn.close()
    return [_row_to_dict(r, db_path_str) for r in rows]


def _study_yaml_run_ids(yaml_path: Path) -> list[str]:
    """Extract run_ids from a study.yaml's runs[]. Accepts list-of-strings
    or list-of-dicts ({run_id: ...}). Malformed yaml → []."""
    try:
        data = yaml.safe_load(yaml_path.read_text()) or {}
    except yaml.YAMLError:
        warnings.warn(f"simulations_index: malformed yaml at {yaml_path}")
        return []
    runs = data.get("runs") or []
    if not isinstance(runs, list):
        return []
    out: list[str] = []
    for entry in runs:
        if isinstance(entry, str):
            out.append(entry)
        elif isinstance(entry, dict) and isinstance(entry.get("run_id"), str):
            out.append(entry["run_id"])
    return out


def _build_run_to_studies_map(workspace: Path) -> dict[str, list[str]]:
    """Return ``{run_id: [study_name, ...]}`` across every study.yaml."""
    result: dict[str, list[str]] = {}
    studies_root = workspace / "studies"
    if not studies_root.is_dir():
        return result
    for sdir in sorted(studies_root.iterdir()):
        if not sdir.is_dir():
            continue
        yml = sdir / "study.yaml"
        if not yml.is_file():
            continue
        for rid in _study_yaml_run_ids(yml):
            result.setdefault(rid, []).append(sdir.name)
    return result


def list_simulations(workspace: Path) -> list[dict]:
    """Return every persisted simulation in ``workspace``, newest first.

    Each dict contains: run_id, spec_id, sim_name, label, status, n_steps,
    progress_step, started_at, completed_at, db_path (workspace-relative),
    studies (list of study names that reference this run_id).
    """
    workspace = Path(workspace)
    rows: list[dict] = []
    for db_path, db_rel in _discover_dbs(workspace):
        rows.extend(_read_runs_meta(db_path, db_rel))
    rows.sort(key=lambda r: (r["started_at"] or 0.0), reverse=True)
    run_to_studies = _build_run_to_studies_map(workspace)
    for r in rows:
        r["studies"] = list(run_to_studies.get(r["run_id"], []))
    return rows
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/eranagmon/code/vivarium-dashboard-sim && python -m pytest tests/test_simulations_index.py -v`
Expected: PASS — all 6 tests.

- [ ] **Step 5: Commit**

```bash
cd /Users/eranagmon/code/vivarium-dashboard-sim
git add vivarium_dashboard/lib/simulations_index.py tests/test_simulations_index.py
git commit -m "feat(simulations-index): workspace-wide run aggregator across SQLite DBs"
```

---

## Task 2: `simulations_index.delete_simulation`

**Files:**
- Modify: `vivarium_dashboard/lib/simulations_index.py`
- Test: `tests/test_simulations_index.py` (append)

Full-delete pass: DB rows + history + run dir + unlink from `study.yaml`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_simulations_index.py`:

```python
import os

from vivarium_dashboard.lib.simulations_index import (
    delete_simulation, RunNotFound,
)


def _write_history_row(db_file, simulation_id, step):
    """Seed one row in the SQLiteEmitter-owned history table."""
    import sqlite3
    conn = sqlite3.connect(str(db_file))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS history "
        "(simulation_id TEXT, step INTEGER, global_time REAL, state TEXT)")
    conn.execute(
        "INSERT INTO history (simulation_id, step, global_time, state) "
        "VALUES (?, ?, ?, ?)",
        (simulation_id, step, float(step), "{}"))
    conn.commit()
    conn.close()


def test_delete_full_pass(tmp_path):
    ws = tmp_path / "ws"
    (ws / ".pbg" / "runs" / "r-1").mkdir(parents=True)
    (ws / ".pbg" / "runs" / "r-1" / "request.json").write_text("{}")
    db = ws / ".pbg" / "composite-runs.db"
    _seed_run(db, spec_id="pkg.x", run_id="r-1", started_at=1.0)
    _write_history_row(db, "r-1", 0)
    _write_history_row(db, "r-1", 1)
    # A study that references this run
    sdir = ws / "studies" / "alpha"
    sdir.mkdir(parents=True)
    (sdir / "study.yaml").write_text(
        yaml.safe_dump({"name": "alpha", "runs": ["r-1", "r-other"]}))

    summary = delete_simulation(ws, "r-1")
    assert summary["deleted_rows"] == 1
    assert summary["deleted_history"] == 2
    assert summary["removed_dir"] is True
    assert summary["unlinked_studies"] == ["alpha"]
    assert summary["errors"] == []

    # Listing now empty for this run
    assert list_simulations(ws) == []
    # study.yaml updated, other run preserved
    spec = yaml.safe_load((sdir / "study.yaml").read_text())
    assert spec["runs"] == ["r-other"]
    # Run dir gone
    assert not (ws / ".pbg" / "runs" / "r-1").exists()


def test_delete_unknown_raises(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    try:
        delete_simulation(ws, "ghost")
    except RunNotFound:
        return
    raise AssertionError("expected RunNotFound")


def test_delete_no_run_dir_no_studies(tmp_path):
    """Run lives only in DB — no run dir, no study refs. Clean delete."""
    ws = tmp_path / "ws"
    (ws / ".pbg").mkdir(parents=True)
    _seed_run(ws / ".pbg" / "composite-runs.db",
              spec_id="pkg.x", run_id="r-x", started_at=1.0)
    summary = delete_simulation(ws, "r-x")
    assert summary["deleted_rows"] == 1
    assert summary["removed_dir"] is False
    assert summary["unlinked_studies"] == []
    assert summary["errors"] == []


def test_delete_partial_failure_records_error(tmp_path):
    """A read-only study.yaml records an error but DB delete still succeeds."""
    ws = tmp_path / "ws"
    (ws / ".pbg").mkdir(parents=True)
    _seed_run(ws / ".pbg" / "composite-runs.db",
              spec_id="pkg.x", run_id="r-1", started_at=1.0)
    sdir = ws / "studies" / "alpha"
    sdir.mkdir(parents=True)
    yml = sdir / "study.yaml"
    yml.write_text(yaml.safe_dump({"name": "alpha", "runs": ["r-1"]}))
    # Make the file read-only AND its directory non-writable so atomic
    # write-then-rename fails (rename into a non-writable dir).
    os.chmod(sdir, 0o555)
    try:
        summary = delete_simulation(ws, "r-1")
        assert summary["deleted_rows"] == 1
        assert summary["errors"]   # some error recorded for alpha
        assert "alpha" in summary["errors"][0]
    finally:
        os.chmod(sdir, 0o755)   # restore so tmp_path cleanup works


def test_delete_dict_form_run_entry(tmp_path):
    """study.yaml runs[] can be list of dicts; delete removes the matching dict."""
    ws = tmp_path / "ws"
    (ws / ".pbg").mkdir(parents=True)
    _seed_run(ws / ".pbg" / "composite-runs.db",
              spec_id="pkg.x", run_id="r-1", started_at=1.0)
    sdir = ws / "studies" / "alpha"
    sdir.mkdir(parents=True)
    (sdir / "study.yaml").write_text(yaml.safe_dump({
        "name": "alpha",
        "runs": [{"run_id": "r-1", "label": "baseline"},
                 {"run_id": "r-other", "label": "v"}],
    }))
    delete_simulation(ws, "r-1")
    spec = yaml.safe_load((sdir / "study.yaml").read_text())
    assert spec["runs"] == [{"run_id": "r-other", "label": "v"}]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/eranagmon/code/vivarium-dashboard-sim && python -m pytest tests/test_simulations_index.py -v`
Expected: the 5 new tests FAIL — `delete_simulation`, `RunNotFound` not yet exported.

- [ ] **Step 3: Implement `delete_simulation`**

Append to `vivarium_dashboard/lib/simulations_index.py`:

```python
def _find_db_for_run(workspace: Path, run_id: str) -> tuple[Path, str] | None:
    """Locate which runs DB owns ``run_id``. Returns (path, rel) or None."""
    for db_path, db_rel in _discover_dbs(workspace):
        try:
            conn = cr.connect(db_path)
        except sqlite3.OperationalError:
            continue
        try:
            row = conn.execute(
                "SELECT 1 FROM runs_meta WHERE run_id=? LIMIT 1", (run_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            row = None
        finally:
            conn.close()
        if row is not None:
            return db_path, db_rel
    return None


def _delete_db_rows(db_path: Path, run_id: str) -> tuple[int, int]:
    """Delete runs_meta + history rows for ``run_id``. Single transaction.

    Returns (rows_deleted, history_rows_deleted).
    """
    conn = cr.connect(db_path)
    try:
        has_history = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='history'"
        ).fetchone()
        if has_history:
            cur = conn.execute(
                "DELETE FROM history WHERE simulation_id=?", (run_id,))
            history_rows = cur.rowcount or 0
        else:
            history_rows = 0
        cur = conn.execute(
            "DELETE FROM runs_meta WHERE run_id=?", (run_id,))
        meta_rows = cur.rowcount or 0
        conn.commit()
        return meta_rows, history_rows
    finally:
        conn.close()


def _rewrite_study_yaml_without(yaml_path: Path, run_id: str) -> bool:
    """Rewrite ``yaml_path``'s runs[] entry without ``run_id``.

    Atomic: write-then-rename through a sibling temp file. Returns True if
    a runs[] entry was removed, False if nothing changed. Raises OSError
    on write failure (caller catches per-file).
    """
    data = yaml.safe_load(yaml_path.read_text()) or {}
    runs = data.get("runs") or []
    if not isinstance(runs, list):
        return False
    new_runs: list = []
    changed = False
    for entry in runs:
        if isinstance(entry, str):
            if entry == run_id:
                changed = True
                continue
        elif isinstance(entry, dict):
            if entry.get("run_id") == run_id:
                changed = True
                continue
        new_runs.append(entry)
    if not changed:
        return False
    data["runs"] = new_runs
    tmp = yaml_path.with_suffix(yaml_path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(data, sort_keys=False))
    tmp.replace(yaml_path)
    return True


def delete_simulation(workspace: Path, run_id: str) -> dict:
    """Full delete of a simulation: DB rows + history + run dir + study refs.

    Returns a summary dict::

        {
          "deleted_rows": int,            # 1 on success
          "deleted_history": int,         # rows removed from history
          "removed_dir": bool,            # True if .pbg/runs/<id>/ existed and was removed
          "unlinked_studies": [str],      # study names whose study.yaml lost a ref
          "errors": [str],                # one entry per per-file failure
        }

    Raises ``RunNotFound`` if ``run_id`` is in no known DB.
    """
    workspace = Path(workspace)
    located = _find_db_for_run(workspace, run_id)
    if located is None:
        raise RunNotFound(run_id)
    db_path, _ = located

    errors: list[str] = []
    deleted_rows, deleted_history = _delete_db_rows(db_path, run_id)

    run_dir = workspace / ".pbg" / "runs" / run_id
    removed_dir = run_dir.exists()
    if removed_dir:
        shutil.rmtree(run_dir, ignore_errors=True)
        # If ignore_errors didn't fully remove it, surface that:
        if run_dir.exists():
            errors.append(f"run dir {run_dir.relative_to(workspace)}: partial removal")
            removed_dir = False

    unlinked: list[str] = []
    studies_root = workspace / "studies"
    if studies_root.is_dir():
        for sdir in sorted(studies_root.iterdir()):
            if not sdir.is_dir():
                continue
            yml = sdir / "study.yaml"
            if not yml.is_file():
                continue
            try:
                if _rewrite_study_yaml_without(yml, run_id):
                    unlinked.append(sdir.name)
            except (yaml.YAMLError, OSError) as e:
                errors.append(f"{sdir.name}: {type(e).__name__}: {e}")

    return {
        "deleted_rows": deleted_rows,
        "deleted_history": deleted_history,
        "removed_dir": removed_dir,
        "unlinked_studies": unlinked,
        "errors": errors,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/eranagmon/code/vivarium-dashboard-sim && python -m pytest tests/test_simulations_index.py -v`
Expected: PASS — all 11 tests.

- [ ] **Step 5: Commit**

```bash
cd /Users/eranagmon/code/vivarium-dashboard-sim
git add vivarium_dashboard/lib/simulations_index.py tests/test_simulations_index.py
git commit -m "feat(simulations-index): full-delete pass (DB rows + history + run dir + study refs)"
```

---

## Task 3: `GET /api/simulations` handler + routing

**Files:**
- Modify: `vivarium_dashboard/server.py` (add `_get_simulations` method; route in `do_GET` near line 1599 where `/api/composite-runs` is)
- Test: `tests/test_simulations_api.py`

- [ ] **Step 1: Write the failing integration test**

Create `tests/test_simulations_api.py`:

```python
"""End-to-end test of the Simulations API.

Spins up the dashboard server against the ws_increase_demo fixture and
exercises GET /api/simulations + DELETE /api/simulation-run.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent
FIXTURE_WORKSPACE = _REPO_ROOT / "tests" / "_fixtures" / "ws_increase_demo"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def server(tmp_path):
    if not FIXTURE_WORKSPACE.is_dir():
        pytest.skip(f"Fixture workspace not present at {FIXTURE_WORKSPACE}")
    ws = tmp_path / "ws"
    shutil.copytree(FIXTURE_WORKSPACE, ws)
    port = _free_port()
    env = os.environ.copy()
    # See test_composite_explorer_api.py — put the repo root first so the
    # detached run-composite child resolves the working-tree code.
    env["PYTHONPATH"] = (str(_REPO_ROOT) + os.pathsep + str(ws)
                         + os.pathsep + env.get("PYTHONPATH", ""))
    proc = subprocess.Popen(
        [sys.executable, "-m", "vivarium_dashboard.server",
         "--workspace", str(ws), "--port", str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    info_path = ws / ".pbg" / "server" / "server-info"
    for _ in range(40):
        if info_path.exists():
            break
        time.sleep(0.1)
    else:
        proc.terminate()
        out, err = proc.communicate(timeout=2)
        pytest.fail(f"server did not start:\n{out.decode()}\n{err.decode()}")
    yield {"url": f"http://127.0.0.1:{port}", "ws": ws}
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def _post(url, payload):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.status, json.loads(r.read().decode())


def _get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return r.status, json.loads(r.read().decode())


def _delete(url, payload):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json"}, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def _poll_until_terminal(base, run_id, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, body = _get(f"{base}/api/composite-run/{run_id}/status")
        if body.get("status") in ("completed", "failed", "orphaned"):
            return body
        time.sleep(0.3)
    raise AssertionError(f"run {run_id} did not finish within {timeout}s")


def test_get_simulations_lists_a_completed_run(server):
    base = server["url"]
    spec_id = "pbg_ws_increase_demo.composites.increase-demo"
    _, body = _post(f"{base}/api/composite-test-run",
                    {"id": spec_id, "steps": 3})
    run_id = body["run_id"]
    _poll_until_terminal(base, run_id)

    status, body = _get(f"{base}/api/simulations")
    assert status == 200
    sims = body["simulations"]
    matching = [s for s in sims if s["run_id"] == run_id]
    assert matching, f"expected our run in the list, got {sims}"
    assert matching[0]["status"] == "completed"
    assert matching[0]["spec_id"] == spec_id
    assert matching[0]["db_path"] == ".pbg/composite-runs.db"
    assert matching[0]["studies"] == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /Users/eranagmon/code/vivarium-dashboard-sim && python -m pytest tests/test_simulations_api.py::test_get_simulations_lists_a_completed_run -v`
Expected: FAIL — `/api/simulations` returns 404 (route not registered).

- [ ] **Step 3: Add the handler + route**

In `vivarium_dashboard/server.py`, find the `do_GET` routing block — around line 1599 there is:
```python
        if self.path.startswith("/api/composite-runs"):
```
Add this check **right after** the `/api/composite-runs` check:
```python
        if self.path.startswith("/api/simulations"):
            return self._get_simulations()
```

Add the `_get_simulations` method anywhere among the other `_get_*` handlers (near `_get_composite_runs` is fine). The method:

```python
    def _get_simulations(self):
        """GET /api/simulations — all persisted runs across the workspace.

        Returns ``{simulations: [...]}`` aggregated from ``.pbg/composite-runs.db``
        and every ``studies/<name>/runs.db``, with Studies-association annotated
        from each ``study.yaml``'s ``runs[]``. Newest first.
        """
        _ws_add_to_sys_path()
        try:
            from vivarium_dashboard.lib.simulations_index import list_simulations
            sims = list_simulations(WORKSPACE)
        except Exception as e:  # noqa: BLE001 — never blank-page the user
            return self._json({"error": f"simulations index failed: {e}"}, 500)
        return self._json({"simulations": sims}, 200)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd /Users/eranagmon/code/vivarium-dashboard-sim && python -m pytest tests/test_simulations_api.py::test_get_simulations_lists_a_completed_run -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/eranagmon/code/vivarium-dashboard-sim
git add vivarium_dashboard/server.py tests/test_simulations_api.py
git commit -m "feat(server): add GET /api/simulations — workspace-wide run listing"
```

---

## Task 4: `DELETE /api/simulation-run` handler + routing

**Files:**
- Modify: `vivarium_dashboard/server.py` (extend the existing `do_DELETE` `route_map` near line 1703; add `_delete_simulation_run` method)
- Test: `tests/test_simulations_api.py` (append)

**Naming note:** the route is `/api/simulation-run` (singular, hyphenated) not `/api/simulation` (which already exists for the Simulation Setup tab). See plan preamble.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_simulations_api.py`:

```python
def test_delete_simulation_run_removes_everything(server):
    base = server["url"]
    spec_id = "pbg_ws_increase_demo.composites.increase-demo"
    _, body = _post(f"{base}/api/composite-test-run",
                    {"id": spec_id, "steps": 2})
    run_id = body["run_id"]
    _poll_until_terminal(base, run_id)

    status, summary = _delete(f"{base}/api/simulation-run", {"run_id": run_id})
    assert status == 200
    assert summary["deleted_rows"] == 1
    assert summary["errors"] == []

    # No longer in the listing
    _, body = _get(f"{base}/api/simulations")
    assert all(s["run_id"] != run_id for s in body["simulations"])

    # Status endpoint now 404s for it
    req = urllib.request.Request(
        f"{base}/api/composite-run/{run_id}/status", method="GET")
    try:
        urllib.request.urlopen(req, timeout=5)
        raise AssertionError("expected 404 for deleted run status")
    except urllib.error.HTTPError as e:
        assert e.code == 404


def test_delete_simulation_run_404_unknown(server):
    base = server["url"]
    status, body = _delete(f"{base}/api/simulation-run",
                            {"run_id": "ghost-run"})
    assert status == 404
    assert "error" in body


def test_delete_simulation_run_400_missing_run_id(server):
    base = server["url"]
    status, body = _delete(f"{base}/api/simulation-run", {})
    assert status == 400
    assert "error" in body
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /Users/eranagmon/code/vivarium-dashboard-sim && python -m pytest tests/test_simulations_api.py::test_delete_simulation_run_removes_everything tests/test_simulations_api.py::test_delete_simulation_run_404_unknown tests/test_simulations_api.py::test_delete_simulation_run_400_missing_run_id -v`
Expected: FAIL — `/api/simulation-run` not in the DELETE route map → 404 "not found" generic.

- [ ] **Step 3: Add the handler + route**

In `vivarium_dashboard/server.py`, locate the `do_DELETE` `route_map` block (around line 1703). It looks like:
```python
        route_map = {
            "/api/simulation":    self._delete_simulation,
            "/api/visualization": self._delete_visualization,
            ...
        }
```
Add a new entry **just after** `"/api/simulation"`:
```python
            "/api/simulation-run": self._delete_simulation_run,
```

Add the `_delete_simulation_run` method near `_delete_simulation` (keep them adjacent so the naming distinction is visible to readers):

```python
    def _delete_simulation_run(self, body: dict):
        """DELETE /api/simulation-run — full delete of one persisted run.

        Body: ``{run_id}``. Removes the runs_meta row, all history rows for
        that simulation_id, the ``.pbg/runs/<run_id>/`` directory if any,
        and the run_id from any ``study.yaml`` ``runs[]`` that references
        it. Returns the summary dict from
        ``simulations_index.delete_simulation``.

        Does NOT go through ``_active_branch_action``. Run DBs and run dirs
        are gitignored; ``study.yaml`` edits are left in the working tree
        (same UX as a Studies-tab edit before commit).
        """
        run_id = (body.get("run_id") or "").strip()
        if not run_id:
            return self._json({"error": "run_id is required"}, 400)

        _ws_add_to_sys_path()
        from vivarium_dashboard.lib.simulations_index import (
            delete_simulation, RunNotFound,
        )
        try:
            summary = delete_simulation(WORKSPACE, run_id)
        except RunNotFound:
            return self._json({"error": "run not found"}, 404)
        except Exception as e:  # noqa: BLE001 — surface the failure, don't crash
            return self._json({"error": f"delete failed: {e}"}, 500)
        return self._json(summary, 200)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /Users/eranagmon/code/vivarium-dashboard-sim && python -m pytest tests/test_simulations_api.py -v`
Expected: PASS — all 4 simulation-API tests.

- [ ] **Step 5: Commit**

```bash
cd /Users/eranagmon/code/vivarium-dashboard-sim
git add vivarium_dashboard/server.py tests/test_simulations_api.py
git commit -m "feat(server): add DELETE /api/simulation-run — full delete of one run"
```

---

## Task 5: Template — rail link + page scaffold

**Files:**
- Modify: `vivarium_dashboard/templates/index.html.j2`

Add the **Simulations** entry between Investigations and Visualizations, plus an empty page scaffold the JS in Task 6 will populate.

- [ ] **Step 1: Add the rail link**

In `vivarium_dashboard/templates/index.html.j2`, find the Investigations rail link block. It ends with `<span class="viv-rail-link-label">Investigations</span></a>` (around line 159).

Immediately AFTER the closing `</a>` of the Investigations link, insert:

```html
      <a href="#simulations"       class="viv-rail-link menu-link" data-page="simulations">
        <span class="viv-rail-link-icon">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" class="viv-rail-link-svg">
            <ellipse cx="12" cy="6" rx="8" ry="3"/>
            <path d="M4 6v12c0 1.657 3.582 3 8 3s8-1.343 8-3V6"/>
            <path d="M4 12c0 1.657 3.582 3 8 3s8-1.343 8-3"/>
          </svg>
        </span>
        <span class="viv-rail-link-label">Simulations</span>
      </a>
```

(The icon is a stylized database/disk stack — a recognizable "saved runs" affordance.)

- [ ] **Step 2: Add the page scaffold**

Find where the existing page sections live. Each page is `<div class="viv-page" data-page="<name>">…</div>`. Locate the `data-page="investigations"` block. Immediately AFTER its closing `</div>`, add the new page section:

```html
  <!-- ============================== Simulations ============================== -->
  <div class="viv-page" data-page="simulations" hidden>
    <h2 class="page-title">Simulations</h2>
    <p class="page-lead">
      All persisted runs across this workspace, gathered from
      <code>.pbg/composite-runs.db</code> and every
      <code>studies/&lt;name&gt;/runs.db</code>. Delete a row to remove its
      DB rows, run artifacts, and any Study references.
    </p>

    <div style="margin: 8px 0 12px; display: flex; gap: 8px; align-items: center;">
      <input id="sim-filter" type="search" placeholder="Filter by composite, study, or label…"
             style="flex: 1; max-width: 480px; padding: 6px 10px; font-size: 14px;
                    border: 1px solid #d1d5db; border-radius: 4px;" />
      <button id="sim-refresh" class="action-btn">Refresh</button>
    </div>

    <div id="sim-loading" style="color: #6b7280; padding: 12px;">Loading simulations…</div>
    <div id="sim-empty"   style="color: #6b7280; padding: 12px; display: none;">
      No simulations yet. Run a composite from the
      <a href="#composite-explore">Composite Explorer</a> or from a
      <a href="#investigations">Study</a> to see entries here.
    </div>

    <table id="sim-table" style="width:100%; border-collapse: collapse; font-size: 13px; display: none;">
      <thead>
        <tr style="background:#f3f4f6;">
          <th style="text-align:left; padding:6px 8px;">Composite</th>
          <th style="text-align:left; padding:6px 8px;">Studies</th>
          <th style="text-align:left; padding:6px 8px; width:90px;">Status</th>
          <th style="text-align:left; padding:6px 8px; width:80px;">Steps</th>
          <th style="text-align:left; padding:6px 8px;">Label</th>
          <th style="text-align:left; padding:6px 8px; width:120px;">Started</th>
          <th style="text-align:left; padding:6px 8px; width:80px;">Run</th>
          <th style="text-align:left; padding:6px 8px; width:60px;"></th>
        </tr>
      </thead>
      <tbody id="sim-tbody"></tbody>
    </table>

    <!-- Delete confirmation dialog -->
    <div id="sim-delete-dialog" style="display:none; position:fixed; inset:0;
                                        background:rgba(0,0,0,0.45); z-index:1000;
                                        align-items:center; justify-content:center;">
      <div style="background:#fff; max-width:560px; padding:20px; border-radius:6px;
                   box-shadow:0 4px 16px rgba(0,0,0,0.2);">
        <h3 style="margin:0 0 12px;">Delete simulation?</h3>
        <div id="sim-delete-body" style="font-size:14px; color:#374151;"></div>
        <div style="text-align:right; margin-top:16px;">
          <button id="sim-delete-cancel" class="action-btn">Cancel</button>
          <button id="sim-delete-confirm" class="action-btn"
                  style="background:#dc2626; color:#fff;">Delete</button>
        </div>
      </div>
    </div>
  </div>
```

(The CSS class names — `viv-page`, `page-title`, `page-lead`, `action-btn` — mirror existing pages. The inline styles are a deliberate concession matching how other pages on this codebase do it.)

- [ ] **Step 3: Verify the dashboard still loads**

Run the dashboard against the fixture workspace and confirm the new tab appears + the page renders without errors:

```bash
cd /Users/eranagmon/code/vivarium-dashboard-sim
python -c "
import sys, http.client
from pathlib import Path
import subprocess, time, shutil, tempfile
fixture = Path('tests/_fixtures/ws_increase_demo')
tmp = Path(tempfile.mkdtemp()) / 'ws'
shutil.copytree(fixture, tmp)
proc = subprocess.Popen([sys.executable, '-m', 'vivarium_dashboard.server',
                         '--workspace', str(tmp), '--port', '0'],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
time.sleep(3)
info = (tmp / '.pbg/server/server-info').read_text()
import json; port = json.loads(info)['port']
conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
conn.request('GET', '/')
html = conn.getresponse().read().decode()
proc.terminate(); proc.wait(timeout=3)
assert 'data-page=\"simulations\"' in html, 'simulations page section missing'
assert 'href=\"#simulations\"' in html, 'simulations rail link missing'
print('template OK')"
```
Expected: prints `template OK`.

- [ ] **Step 4: Commit**

```bash
cd /Users/eranagmon/code/vivarium-dashboard-sim
git add vivarium_dashboard/templates/index.html.j2
git commit -m "feat(template): add Simulations rail link + page scaffold"
```

---

## Task 6: Frontend JS — load, render, delete

**Files:**
- Modify: `vivarium_dashboard/static/walkthrough.js`

Wire the page: register `simulations` in `fromHash`'s `validPages`, write `_initSimulations()` that fetches `/api/simulations` and renders the table, and `_deleteSimulationRun(run_id)` that opens the confirm dialog and calls the DELETE.

- [ ] **Step 1: Add `simulations` to both `validPages` arrays**

In `vivarium_dashboard/static/walkthrough.js`, find the two `validPages` arrays (lines 422 and 432). They are:
```javascript
      var validPages = ['workspace-inputs', 'simulation-setup', 'visualizations', 'registry', 'investigations', 'branches', 'composite-explore'];
```
Add `'simulations'` to both (e.g. between `'investigations'` and `'visualizations'`):
```javascript
      var validPages = ['workspace-inputs', 'simulation-setup', 'visualizations', 'registry', 'investigations', 'simulations', 'branches', 'composite-explore'];
```

(There are two arrays — one in the early-`focus` branch and one in `fromHash` itself. Update both.)

- [ ] **Step 2: Add the page-init and render functions**

Append the following block to the end of `vivarium_dashboard/static/walkthrough.js` (just before the file's closing `})();` / module footer, or right after the last `window._init…` export — wherever your IIFE convention places exports). The block defines: a fetch + render flow, the row template, the filter handler, and the delete-with-confirm flow.

```javascript
  // ===========================================================================
  // Simulations tab — workspace-wide run listing + delete
  // ===========================================================================

  function _escSim(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function _simRelativeTime(epoch) {
    if (!epoch) return '—';
    var d = Math.floor(Date.now() / 1000 - epoch);
    if (d < 60)        return d + 's ago';
    if (d < 3600)      return Math.floor(d / 60) + 'm ago';
    if (d < 86400)     return Math.floor(d / 3600) + 'h ago';
    return Math.floor(d / 86400) + 'd ago';
  }

  function _simStatusChip(status) {
    var colors = {
      completed: ['#dcfce7', '#166534'],
      running:   ['#dbeafe', '#1e40af'],
      failed:    ['#fee2e2', '#991b1b'],
      orphaned:  ['#e5e7eb', '#374151'],
    };
    var c = colors[status] || ['#e5e7eb', '#374151'];
    return '<span style="background:' + c[0] + '; color:' + c[1] +
      '; padding:2px 8px; border-radius:10px; font-size:12px;">' +
      _escSim(status || '?') + '</span>';
  }

  function _simStudyChips(studies) {
    if (!studies || !studies.length) return '<span style="color:#9ca3af;">—</span>';
    return studies.map(function (name) {
      return '<a href="#investigations?name=' + encodeURIComponent(name) +
        '" style="display:inline-block; background:#eef2ff; color:#3730a3; ' +
        'padding:1px 7px; margin:0 2px 2px 0; border-radius:10px; font-size:12px; ' +
        'text-decoration:none;">' + _escSim(name) + '</a>';
    }).join('');
  }

  function _simShortId(run_id) {
    if (!run_id) return '';
    // Show the last 6 chars (the hash suffix) of "<spec>__<ts>__<hash6>".
    return run_id.slice(-6);
  }

  // Module-scope cache so the filter and delete flows can read current rows.
  window._simRows = [];

  function _renderSimRow(sim) {
    var composite = _escSim(sim.spec_id || '');
    // Last segment bold for scannability
    var segs = composite.split('.');
    if (segs.length > 1) {
      segs[segs.length - 1] = '<strong>' + segs[segs.length - 1] + '</strong>';
      composite = segs.join('.');
    }
    var stepsTxt = (sim.status === 'running')
      ? (sim.progress_step || 0) + '/' + (sim.n_steps || '?')
      : (sim.n_steps != null ? String(sim.n_steps) : '—');
    var label = sim.sim_name || sim.label || '';
    var startedFull = sim.started_at
      ? new Date(sim.started_at * 1000).toISOString()
      : '';
    var runTooltip = (sim.run_id || '') + '\n' + (sim.db_path || '');
    return (
      '<tr data-run-id="' + _escSim(sim.run_id) + '" ' +
        'style="border-bottom:1px solid #f3f4f6;">' +
      '<td style="padding:6px 8px;"><code>' + composite + '</code></td>' +
      '<td style="padding:6px 8px;">' + _simStudyChips(sim.studies) + '</td>' +
      '<td style="padding:6px 8px;">' + _simStatusChip(sim.status) + '</td>' +
      '<td style="padding:6px 8px;">' + _escSim(stepsTxt) + '</td>' +
      '<td style="padding:6px 8px; color:#374151;">' + _escSim(label) + '</td>' +
      '<td style="padding:6px 8px; color:#6b7280;" title="' + _escSim(startedFull) +
        '">' + _escSim(_simRelativeTime(sim.started_at)) + '</td>' +
      '<td style="padding:6px 8px;"><code title="' + _escSim(runTooltip) +
        '" style="font-size:11px; color:#6b7280;">' + _escSim(_simShortId(sim.run_id)) +
        '</code></td>' +
      '<td style="padding:6px 8px; text-align:center;">' +
        '<button class="action-btn" title="Delete simulation" ' +
        'onclick="_deleteSimulationRun(\'' + _escSim(sim.run_id) + '\')">🗑</button>' +
      '</td>' +
      '</tr>'
    );
  }

  function _applySimFilter() {
    var q = (document.getElementById('sim-filter') || {}).value || '';
    q = q.toLowerCase().trim();
    var rows = window._simRows || [];
    var visible = q ? rows.filter(function (s) {
      var hay = (s.spec_id + ' ' + (s.sim_name || '') + ' ' + (s.label || '') +
                  ' ' + (s.studies || []).join(' ')).toLowerCase();
      return hay.indexOf(q) >= 0;
    }) : rows;
    var tbody = document.getElementById('sim-tbody');
    if (!tbody) return;
    tbody.innerHTML = visible.map(_renderSimRow).join('');
  }

  function _initSimulations() {
    var loading = document.getElementById('sim-loading');
    var empty   = document.getElementById('sim-empty');
    var table   = document.getElementById('sim-table');
    if (loading) loading.style.display = '';
    if (empty)   empty.style.display = 'none';
    if (table)   table.style.display = 'none';

    fetch('/api/simulations')
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) {
          if (loading) loading.innerHTML =
            '<span style="color:#c00;">Could not load simulations: ' +
            _escSim(data.error) + ' <button class="action-btn" ' +
            'onclick="_initSimulations()">Retry</button></span>';
          return;
        }
        window._simRows = data.simulations || [];
        if (loading) loading.style.display = 'none';
        if (!window._simRows.length) {
          if (empty) empty.style.display = '';
          return;
        }
        if (table) table.style.display = '';
        _applySimFilter();
      })
      .catch(function (err) {
        if (loading) loading.innerHTML =
          '<span style="color:#c00;">Network error: ' + _escSim(String(err)) +
          ' <button class="action-btn" onclick="_initSimulations()">Retry</button></span>';
      });
  }
  window._initSimulations = _initSimulations;

  // Wire the filter input + refresh button (once, on first init)
  function _wireSimulationsUiOnce() {
    var f = document.getElementById('sim-filter');
    if (f && !f.dataset.wired) {
      f.addEventListener('input', _applySimFilter);
      f.dataset.wired = '1';
    }
    var r = document.getElementById('sim-refresh');
    if (r && !r.dataset.wired) {
      r.addEventListener('click', _initSimulations);
      r.dataset.wired = '1';
    }
    var cancel = document.getElementById('sim-delete-cancel');
    if (cancel && !cancel.dataset.wired) {
      cancel.addEventListener('click', function () {
        var dlg = document.getElementById('sim-delete-dialog');
        if (dlg) dlg.style.display = 'none';
      });
      cancel.dataset.wired = '1';
    }
  }

  function _deleteSimulationRun(run_id) {
    _wireSimulationsUiOnce();
    var rows = window._simRows || [];
    var sim = null;
    for (var i = 0; i < rows.length; i++) {
      if (rows[i].run_id === run_id) { sim = rows[i]; break; }
    }
    if (!sim) return;

    var studiesTxt = (sim.studies && sim.studies.length)
      ? sim.studies.map(_escSim).join(', ')
      : '<em>none</em>';
    var stillRunning = (sim.status === 'running')
      ? '<p style="color:#b45309; margin:8px 0 0;"><strong>⚠ This run is still running.</strong> ' +
        'Deleting now will orphan the detached process (it will fail-write later, harmlessly).</p>'
      : '';
    var body = document.getElementById('sim-delete-body');
    if (body) body.innerHTML =
      '<p style="margin:0 0 8px;"><code>' + _escSim(run_id) + '</code></p>' +
      '<p style="margin:0 0 8px;">Composite: <code>' + _escSim(sim.spec_id) + '</code></p>' +
      '<p style="margin:0 0 4px;">This will permanently remove:</p>' +
      '<ul style="margin:0 0 4px 24px;">' +
        '<li>1 row in <code>' + _escSim(sim.db_path) + '</code></li>' +
        '<li>All history rows (trajectory data) for this run</li>' +
        '<li>The run directory <code>.pbg/runs/' + _escSim(run_id) + '/</code> (if any)</li>' +
        '<li>References from study.yaml(s): ' + studiesTxt + '</li>' +
      '</ul>' + stillRunning;
    var dlg = document.getElementById('sim-delete-dialog');
    if (dlg) dlg.style.display = 'flex';
    var confirm = document.getElementById('sim-delete-confirm');
    // Replace the confirm handler each time to bind the current run_id.
    confirm.onclick = function () {
      confirm.disabled = true;
      fetch('/api/simulation-run', {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ run_id: run_id }),
      }).then(function (r) { return r.json().then(function (d) {
        return { ok: r.ok, status: r.status, body: d };
      }); }).then(function (res) {
        confirm.disabled = false;
        if (dlg) dlg.style.display = 'none';
        if (!res.ok) {
          alert('Delete failed: ' + (res.body.error || 'HTTP ' + res.status));
          return;
        }
        if (res.body.errors && res.body.errors.length) {
          alert('Deleted, but with warnings:\n' + res.body.errors.join('\n'));
        }
        _initSimulations();
      }).catch(function (err) {
        confirm.disabled = false;
        if (dlg) dlg.style.display = 'none';
        alert('Network error: ' + err);
      });
    };
  }
  window._deleteSimulationRun = _deleteSimulationRun;
```

- [ ] **Step 3: Hook the page-init in the page switcher**

`_switchPage(page)` already exists in `walkthrough.js`. It calls the init for the page being activated (look near `_initMenuNav` and the page-switching block). Find where `_initCompositeExplorer` is called when `page === 'composite-explore'` — the same `if`-chain block. Add a parallel branch for simulations:

```javascript
        if (page === 'simulations') {
          _wireSimulationsUiOnce();
          _initSimulations();
        }
```

(Adjacent to the existing `if (page === 'composite-explore') { _initCompositeExplorer(); }`.)

- [ ] **Step 4: Manual smoke test**

Run a composite from the Composite Explorer (or use a fixture run), then navigate to `#simulations`:

```bash
cd /Users/eranagmon/code/v2ecoli-workspace   # or any workspace
pkill -f "vivarium-dashboard serve.*v2ecoli-workspace" 2>/dev/null; sleep 1
PYTHONPATH=/Users/eranagmon/code/vivarium-dashboard-sim \
  .venv/bin/python -m vivarium_dashboard.cli serve \
  --workspace /Users/eranagmon/code/v2ecoli-workspace --port 63830 \
  > /tmp/sim-smoke.log 2>&1 &
sleep 5
curl -sS -o /dev/null -w "root: HTTP %{http_code}\n" http://127.0.0.1:63830/
curl -sS http://127.0.0.1:63830/api/simulations | python3 -c "import sys,json; d=json.load(sys.stdin); print('count:', len(d.get('simulations',[])))"
```
Expected: `root: HTTP 200`; `/api/simulations` returns a count (≥1 if there's a prior run in `.pbg/composite-runs.db`).

In your browser, open `http://127.0.0.1:63830/#simulations`:
- Rail link visible + clickable
- Table renders with rows (or empty-state message)
- Filter input narrows rows
- 🗑 → dialog shows counts + Cancel/Delete
- Cancel keeps the row; Delete removes it and re-renders

- [ ] **Step 5: Run the full backend suite**

```bash
cd /Users/eranagmon/code/vivarium-dashboard-sim
python -m pytest tests/test_simulations_index.py tests/test_simulations_api.py \
                  tests/test_composite_explorer_api.py tests/test_composite_runs.py -q
```
Expected: all green (pre-existing unrelated failures noted earlier in the project — `test_post_create_from_composite_creates_v2_spec` etc. — do not appear in these files, so the result here should be 100% green).

- [ ] **Step 6: Commit**

```bash
cd /Users/eranagmon/code/vivarium-dashboard-sim
git add vivarium_dashboard/static/walkthrough.js
git commit -m "feat(simulations-ui): render Simulations table, filter, and full-delete dialog"
```

---

## Self-Review

**1. Spec coverage:**
- "Server-side aggregator across `.pbg/composite-runs.db` and `studies/<name>/runs.db`" → Task 1 `list_simulations` ✓
- "Cross-reference run_id against every study.yaml's runs[]" → Task 1 `_build_run_to_studies_map` (supports list-of-strings AND list-of-dicts) ✓
- "Full delete: rows + history + run dir + unlink studies" → Task 2 `delete_simulation` + tests cover all four artifacts ✓
- "RunNotFound" → Task 2 (handler maps to 404 in Task 4) ✓
- "GET /api/simulations" → Task 3 ✓
- "DELETE endpoint for one run" → Task 4 — spec said `/api/simulations/<run_id>`; plan uses `/api/simulation-run` + body to match the dashboard's existing `do_DELETE` exact-path-with-body convention (documented in plan preamble — semantically identical) ✓
- "Does NOT go through `_active_branch_action`" → Task 4 handler docstring + behavior; tests verify ✓
- "Rail link after Investigations, before Visualizations" → Task 5 ✓
- "Page section with heading, lead, filter, table, empty state, delete dialog" → Task 5 ✓
- "Columns: Composite, Studies, Status, Steps, Label, Started, Run (with db_path tooltip), Delete" → Task 6 `_renderSimRow` ✓
- "Status chips colored, Studies clickable chips" → Task 6 ✓
- "Confirm dialog with counts; not bulk" → Task 6 `_deleteSimulationRun` ✓
- "Re-render after delete" → Task 6 `_initSimulations()` re-fetch ✓
- "Error/empty/retry states" → Task 6 catch block + empty-state element ✓
- "Tolerates missing DBs / locked DBs / malformed yaml" → Task 1 tests + warnings.warn ✓
- "Partial-failure summary with errors[]" → Task 2 + Task 4 + integration test ✓

No spec requirement is missing a task.

**2. Placeholder scan:** No TBD/TODO/"implement later"/vague-error-handling phrases. All code blocks are complete.

**3. Type consistency:**
- `run_id` (string) used uniformly across lib, server, and JS.
- `list_simulations` returns `list[dict]` with the field set declared in Section "Data Flow" of the spec; `_renderSimRow` consumes exactly those fields.
- `delete_simulation` returns `{deleted_rows, deleted_history, removed_dir, unlinked_studies, errors}`; handler passes that through; JS consumes `errors` only.
- `RunNotFound` is defined in Task 1 and used in Tasks 2 + 4.
- Endpoint names: `GET /api/simulations` and `DELETE /api/simulation-run` are consistent throughout the plan.
- JS function names: `_initSimulations`, `_deleteSimulationRun`, `_applySimFilter`, `_renderSimRow`, `_wireSimulationsUiOnce` — used consistently within Task 6.
- Module-scope cache `window._simRows` is set in `_initSimulations` and read by `_applySimFilter` + `_deleteSimulationRun` — consistent name.
