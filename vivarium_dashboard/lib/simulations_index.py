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
        # Match the SQLiteEmitter shape so JS consumers can rely on the
        # keys existing regardless of which emitter wrote the row.
        "study_slug": None,
        "investigation_slug": None,
    }


def _read_sqlite_emitter(db_path: Path, db_path_str: str) -> list[dict]:
    """Read process_bigraph.emitter.SQLiteEmitter's `simulations` + `history`
    tables and translate to the dashboard's run-dict shape. Returns [] when
    the DB doesn't have a `simulations` table (this isn't an emitter DB)."""
    try:
        raw = sqlite3.connect(str(db_path))
        raw.row_factory = sqlite3.Row
    except sqlite3.OperationalError as e:
        warnings.warn(f"simulations_index: skipping {db_path_str}: {e}")
        return []
    try:
        tbls = {r[0] for r in raw.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        if "simulations" not in tbls or "history" not in tbls:
            return []
        # study_slug + investigation_slug are added by v2ecoli's
        # sqlite_emitter() helper (PR: fix/workspace-shared-sim-db). Older
        # DBs may not have the columns yet — detect and skip in that case.
        sim_cols = {row[1] for row in raw.execute(
            "PRAGMA table_info(simulations)"
        ).fetchall()}
        has_study_slug = 'study_slug' in sim_cols
        has_investigation_slug = 'investigation_slug' in sim_cols
        select_extras = ''
        if has_study_slug:
            select_extras += ', s.study_slug'
        if has_investigation_slug:
            select_extras += ', s.investigation_slug'
        rows = raw.execute(
            "SELECT s.simulation_id, s.name, s.started_at, s.completed_at, "
            "       s.elapsed_seconds, "
            "       (SELECT COUNT(*) FROM history h WHERE h.simulation_id = s.simulation_id) AS n_rows, "
            "       (SELECT MAX(step) FROM history h WHERE h.simulation_id = s.simulation_id) AS max_step"
            + select_extras +
            " FROM simulations s ORDER BY s.started_at DESC"
        ).fetchall()
    except sqlite3.OperationalError as e:
        warnings.warn(f"simulations_index: sqlite-emitter read failed for {db_path_str}: {e}")
        return []
    finally:
        raw.close()
    out = []
    for r in rows:
        n_rows = r["n_rows"] or 0
        out.append({
            "run_id":             r["simulation_id"],
            "spec_id":            "",
            "sim_name":           r["name"] or "",
            "label":              r["name"] or "",
            "status":             "completed" if r["completed_at"] else "running",
            "n_steps":            (r["max_step"] + 1) if r["max_step"] is not None else n_rows,
            "progress_step":      r["max_step"] if r["max_step"] is not None else n_rows,
            "started_at":         r["started_at"],
            "completed_at":       r["completed_at"],
            "db_path":            db_path_str,
            "source":             "sqlite_emitter",
            "studies":            [],
            "study_slug":         r["study_slug"] if has_study_slug else None,
            "investigation_slug": r["investigation_slug"] if has_investigation_slug else None,
        })
    return out


def _read_runs_meta(db_path: Path, db_path_str: str) -> list[dict]:
    """SELECT every runs_meta row in a DB. Tolerates lock/timeout by returning []."""
    try:
        conn = cr.connect(db_path)
    except sqlite3.OperationalError as e:
        warnings.warn(f"simulations_index: skipping {db_path_str}: {e}")
        return []
    try:
        # Skip if no runs_meta table (DB is SQLiteEmitter-format, not dashboard-format).
        tbls = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        if "runs_meta" not in tbls:
            return []
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
        # Try both schemas; each returns [] when its table is absent so the
        # two are non-overlapping per DB.
        rows.extend(_read_runs_meta(db_path, db_rel))
        rows.extend(_read_sqlite_emitter(db_path, db_rel))

    def _ts(r):
        v = r.get("started_at")
        return v if v is not None else ""
    rows.sort(key=_ts, reverse=True)

    run_to_studies = _build_run_to_studies_map(workspace)
    # SQLiteEmitter runs are study-scoped by path (studies/<name>/runs.db),
    # so derive the study name from db_path when no explicit study.yaml
    # cross-reference exists.
    for r in rows:
        # Explicit cross-reference takes priority
        explicit = list(run_to_studies.get(r["run_id"], []))
        r["studies"] = explicit
        if not explicit and r.get("source") == "sqlite_emitter":
            p = (r.get("db_path") or "")
            if p.startswith("studies/") and p.endswith("/runs.db"):
                study = p[len("studies/"):-len("/runs.db")]
                r["studies"] = [study]
        # Fall back to path-derived study_slug for legacy per-study DBs
        # written before sqlite_emitter() stamped the column.
        if not r.get("study_slug") and r.get("studies"):
            r["study_slug"] = r["studies"][0]
    return rows


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
    if not isinstance(data, dict):
        return False
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
    try:
        tmp.write_text(yaml.safe_dump(data, sort_keys=False))
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
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
