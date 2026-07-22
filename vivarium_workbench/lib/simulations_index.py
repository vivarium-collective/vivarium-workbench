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

import datetime as _dt
import json
import shutil
import sqlite3
import warnings
from pathlib import Path

import yaml
from pydantic import ValidationError

from vivarium_workbench.lib import composite_runs as cr
from vivarium_workbench.lib import emitters
from vivarium_workbench.lib import run_log
from vivarium_workbench.lib import run_store
from vivarium_workbench.lib.models import SimRow
from vivarium_workbench.lib.workspace_paths import WorkspacePaths


class RunNotFound(Exception):
    """Raised by ``delete_simulation`` when ``run_id`` is in no known DB."""


def _study_slug_from_db_path(db_path_str: str) -> str | None:
    """A runs.db lives at .../studies/<slug>/runs.db — return <slug> (last 'studies/' segment)."""
    parts = str(db_path_str).replace("\\", "/").split("/")
    if "studies" in parts:
        i = len(parts) - 1 - parts[::-1].index("studies")  # last 'studies'
        if i + 1 < len(parts):
            return parts[i + 1]
    return None


def _iter_all_study_dirs(workspace: Path):
    """Yield ``(study_dir, investigation_slug, rel_prefix)`` for every study
    directory in the workspace.

    Covers BOTH layouts:
      * root        — ``studies/<slug>/``  (investigation_slug ``None``,
        rel_prefix ``studies/<slug>``)
      * nested      — ``investigations/<inv>/studies/<slug>/``
        (investigation_slug ``<inv>``, rel_prefix
        ``investigations/<inv>/studies/<slug>``)

    De-dupes by study slug with root taking precedence. The Simulations DB
    MUST scan both: nested-layout investigations (colonies, ketchup-baseline-
    comparison, v2ecoli-pdmp, …) keep their ``runs.db`` / ``parquet-runs`` /
    ``study.yaml`` runs under ``investigations/<inv>/studies/<slug>/``, so a
    root-only walk made every one of their runs invisible.
    """
    wp = WorkspacePaths.load(workspace)
    seen: set[str] = set()
    root = wp.studies
    if root.is_dir():
        for sdir in sorted(root.iterdir()):
            if sdir.is_dir() and sdir.name not in seen:
                seen.add(sdir.name)
                yield sdir, None, f"studies/{sdir.name}"
    invs = wp.investigations
    if invs.is_dir():
        for inv in sorted(invs.iterdir()):
            nested = inv / "studies"
            if not (inv.is_dir() and nested.is_dir()):
                continue
            for sdir in sorted(nested.iterdir()):
                if sdir.is_dir() and sdir.name not in seen:
                    seen.add(sdir.name)
                    yield sdir, inv.name, f"investigations/{inv.name}/studies/{sdir.name}"


def _discover_dbs(workspace: Path) -> list[tuple[Path, str]]:
    """Return list of (db_path, workspace_relative_str) for every runs DB.

    Skips missing files. Order: workspace-level DBs first (composite-runs
    + default-baseline), then per-study (root then nested investigations) in
    alphabetical order (deterministic for tests).

    .pbg/default-baseline/runs.db is produced by ``scripts/run_default_baseline.py``
    (workspace.yaml:default_baseline). It's the "before any study runs"
    reference state — surfaces in the Simulations DB tab so evaluators can
    open it, and is read as a fallback when a study has no runs of its own.
    """
    wp = WorkspacePaths.load(workspace)
    dbs: list[tuple[Path, str]] = []
    scratch = wp.pbg / "composite-runs.db"
    if scratch.is_file():
        dbs.append((scratch, ".pbg/composite-runs.db"))
    default_baseline = wp.pbg / "default-baseline" / "runs.db"
    if default_baseline.is_file():
        dbs.append((default_baseline, ".pbg/default-baseline/runs.db"))
    for sdir, _inv, rel_prefix in _iter_all_study_dirs(workspace):
        db = sdir / "runs.db"
        if db.is_file():
            dbs.append((db, f"{rel_prefix}/runs.db"))
    return dbs


def discover_default_baseline_db(workspace: Path) -> Path | None:
    """Return the path to the workspace's default-baseline runs.db, or None.

    Used by viz pre-fill: when a study has no runs of its own, callers
    fall back to this db so the evaluator sees the cell's baseline
    behaviour against the study's measure paths.
    """
    p = WorkspacePaths.load(workspace).pbg / "default-baseline" / "runs.db"
    return p if p.is_file() else None


def _row_to_dict(row, db_path_str: str) -> dict:
    """Convert a runs_meta SELECT row to the public dict shape."""
    # Parse provenance JSON (may be absent in legacy DBs or None).
    prov: dict = {}
    try:
        raw_json = row["params_json"]
        if raw_json:
            prov = json.loads(raw_json) or {}
    except (KeyError, TypeError, ValueError):
        prov = {}
    # Detect remote run: must have both `source` (non-empty) and `simulation_id`.
    if prov.get("source") and prov.get("simulation_id") is not None:
        remote_origin = {
            "deployment": prov.get("source"),
            "simulation_id": prov.get("simulation_id"),
            "experiment_id": prov.get("experiment_id"),
            "backend": prov.get("backend"),
            "s3_uri": prov.get("s3_uri"),
        }
    else:
        remote_origin = None
    # A remote run lands its native store next to runs.db (a .zarr or parquet-runs
    # dir), so its emitter type must come from that store_path — NOT from db_path,
    # which is always the runs.db SQLite metadata file (would mislabel it "SQLite").
    # Emitter kind is inferred from the native store path (NOT db_path, which is
    # always the runs.db SQLite metadata file). Detection is centralized in
    # run_store.detect_kind; map its canonical kind to this view's display label.
    emitter: str | None = {"zarr": "xarray", "parquet": "parquet"}.get(
        run_store.detect_kind(prov.get("store_path"))
    )
    raw = {
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
        # Native data store: a .zarr/parquet dir (local) or an s3:// uri (remote);
        # None means the run's data lives in the runs.db SQLite at db_path.
        "store_path": (prov.get("store_path") or remote_origin and remote_origin.get("s3_uri")) or None,
        "emitter": emitter,  # store-derived (xarray/parquet) for remote runs; None → falls back to db_path
        "studies": [],  # filled in by _annotate_studies
        # Match the SQLiteEmitter shape so JS consumers can rely on the
        # keys existing regardless of which emitter wrote the row.
        "study_slug": _study_slug_from_db_path(db_path_str),
        "investigation_slug": None,
        "remote_origin": remote_origin,
    }
    # Validate/normalize through the typed model (single source of truth). The
    # dumped dict is identical to `raw` for well-formed rows; on an unexpected
    # row we keep serving the legacy dict and surface the drift as a warning
    # rather than 500-ing the whole simulations index.
    try:
        return SimRow.model_validate(raw).model_dump()
    except ValidationError as e:
        warnings.warn(
            f"simulations_index: row {raw.get('run_id')!r} failed SimRow "
            f"validation: {e}"
        )
        return raw


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
            "progress_step, started_at, completed_at, params_json "
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
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
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
        elif isinstance(entry, dict):
            # run_id is the canonical key; fall back to `name` (emitter-less
            # workspaces — e.g. numpy investigations — record runs as {name: ...}).
            rid = entry.get("run_id") or entry.get("name")
            if isinstance(rid, str):
                out.append(rid)
    return out


def _read_study_yaml_runs(workspace: Path) -> list[dict]:
    """Surface runs recorded only in ``study.yaml`` ``runs:`` as first-class
    simulation rows.

    Emitter-less workspaces (numpy investigations like pbg-autopoiesis) persist
    each run in the spec rather than a per-step ``runs.db`` / parquet hive / zarr
    store. Without this they never appear in the Simulations DB even though they
    ran. Rows are shaped like the DB sources so ``list_simulations``'s merge
    treats them uniformly; a real DB row wins on ``run_id`` collision (the DB is
    authoritative where it exists). ``source='study_yaml'``.
    """
    out: list[dict] = []
    for sdir, inv_slug, _rel in _iter_all_study_dirs(workspace):
        yml = sdir / "study.yaml"
        if not yml.is_file():
            continue
        try:
            data = yaml.safe_load(yml.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            warnings.warn(f"simulations_index: malformed yaml at {yml}")
            continue
        runs = data.get("runs")
        if not isinstance(runs, list):
            continue
        for entry in runs:
            if not isinstance(entry, dict):
                continue
            rid = str(entry.get("run_id") or entry.get("name") or "").strip()
            if not rid:
                continue
            out.append({
                "run_id": rid,
                "spec_id": entry.get("composite"),
                "sim_name": entry.get("name") or rid,
                "label": entry.get("name") or rid,
                "status": entry.get("status") or "completed",
                "n_steps": entry.get("n_steps"),
                "progress_step": entry.get("n_steps") or 0,
                # `record_runs` (pbg_superpowers.study_outcomes) writes the run's
                # completion time as `timestamp` (= db completed_at or started_at),
                # NOT as started_at/completed_at — so without this fallback the
                # Simulations DB Time column stayed blank for every study.yaml-
                # recorded run even though the time was recorded.
                "started_at": entry.get("started_at") or entry.get("timestamp"),
                "completed_at": (entry.get("completed_at")
                                 or entry.get("started_at")
                                 or entry.get("timestamp")),
                "db_path": None,
                # Native store recorded in the spec (parquet/zarr dir); the run's
                # data lives here even though there's no per-step runs.db.
                "store_path": (entry.get("parquet") or entry.get("store_path")
                               or entry.get("zarr") or None),
                "studies": [sdir.name],
                "study_slug": sdir.name,
                "investigation_slug": inv_slug,
                "emitter": entry.get("emitter"),  # declared in the spec, if any
                "source": "study_yaml",
            })
    return out


def _build_run_to_studies_map(workspace: Path) -> dict[str, list[str]]:
    """Return ``{run_id: [study_name, ...]}`` across every study.yaml."""
    result: dict[str, list[str]] = {}
    for sdir, _inv, _rel in _iter_all_study_dirs(workspace):
        yml = sdir / "study.yaml"
        if not yml.is_file():
            continue
        for rid in _study_yaml_run_ids(yml):
            result.setdefault(rid, []).append(sdir.name)
    return result


# ---------------------------------------------------------------------------
# Parquet hive discovery (workspace-default emitter per 2026-05-27 migration)
# ---------------------------------------------------------------------------
#
# ParquetEmitter runs land at
#   studies/<study_slug>/parquet-runs/<experiment_id>/{configuration,history,success}/...
# with the configuration/ subdir holding the per-generation metadata
# (study_slug, investigation_slug, generation, agent_id) the runner passed
# at construction. We discover them by walking the per-study parquet-runs/
# dirs and build the same dict shape sqlite_emitter readers produce, so
# the merge logic in list_simulations doesn't have to special-case parquet.
# ---------------------------------------------------------------------------


def _find_hive_dir(run_dir: Path, max_depth: int = 3) -> Path | None:
    """Shallowest directory at/under ``run_dir`` that looks like a
    ParquetEmitter hive — i.e. has both ``configuration/`` and ``history/``
    children. Returns None if none is found within ``max_depth`` levels.

    Why a search instead of a fixed path: the runner/sweep wrappers nest the
    emitter hive below the run dir the user launched, at variable depth and
    under variably-named intermediate dirs, e.g.::

        parquet-runs/<run>/                                   (flat — hive IS the run dir)
        parquet-runs/<run>/parquet/<experiment_id>/           (sweep output)
        parquet-runs/<run>/<inner>/                           (single repro run)

    BFS so the closest hive wins; the partition dirs themselves
    (history/configuration/success) are never descended into.
    """
    from collections import deque
    queue: deque[tuple[Path, int]] = deque([(run_dir, 0)])
    while queue:
        d, depth = queue.popleft()
        if (d / "history").is_dir() and (d / "configuration").is_dir():
            return d
        if depth >= max_depth:
            continue
        try:
            children = sorted(c for c in d.iterdir()
                              if c.is_dir()
                              and c.name not in ("history", "configuration", "success"))
        except OSError:
            continue
        for c in children:
            queue.append((c, depth + 1))
    return None


def _discover_parquet_hives(workspace: Path) -> list[tuple[Path, Path, str]]:
    """Yield ``(hive_dir, run_dir, study_slug)`` for every per-study parquet
    run under ``studies/*/parquet-runs/<run>/``.

    ``run_dir`` is the directory the user launched (the unique, meaningful run
    key); ``hive_dir`` is the ParquetEmitter hive located beneath it (see
    :func:`_find_hive_dir` — it may be the run dir itself for the flat shape,
    or nested for sweep output). Run dirs without any hive (in-progress writes
    whose first emit hasn't flushed yet) are skipped. Deterministic order:
    study slug first, then run dir mtime descending.
    """
    out: list[tuple[Path, Path, str]] = []
    for sdir, _inv, _rel in _iter_all_study_dirs(workspace):
        parquet_runs = sdir / "parquet-runs"
        if not parquet_runs.is_dir():
            continue
        run_dirs = [p for p in parquet_runs.iterdir() if p.is_dir()]
        run_dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for run_dir in run_dirs:
            hive = _find_hive_dir(run_dir)
            if hive is not None:
                out.append((hive, run_dir, sdir.name))
    return out


def _parquet_sim_name_from_yaml(yaml_path: Path, experiment_id: str) -> str | None:
    """Look up the human-readable sim name from the study.yaml's runs[]
    by matching ``simulation_id``. Returns None when the yaml is missing
    (e.g. cross-investigation pseudo-studies whose parquet-runs/ dir
    exists without a study.yaml), unreadable, or has no matching entry.
    """
    if not yaml_path.is_file():
        return None
    try:
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError):
        return None
    for entry in data.get("runs") or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("simulation_id") == experiment_id:
            return entry.get("simulation") or None
    return None


def _read_parquet_hive(
    hive_dir: Path, run_dir: Path, study_slug: str, workspace: Path
) -> dict | None:
    """Read one parquet run's metadata + counts and return the dashboard's
    standard run-dict shape. Returns None on read failure so a broken hive
    doesn't take down the whole simulations index.

    ``run_dir`` is the directory the user launched (the unique run key);
    ``hive_dir`` is the ParquetEmitter hive located beneath it, which holds
    the ``configuration/`` / ``history/`` / ``success/`` partitions. For the
    flat layout the two are the same dir.
    """
    try:
        import polars as pl  # lazy; not every dashboard install has it
    except ImportError:
        warnings.warn(
            "simulations_index: polars unavailable; "
            f"skipping parquet hive at {hive_dir}"
        )
        return None

    experiment_id = hive_dir.name
    run_id = run_dir.name
    history = hive_dir / "history"
    config = hive_dir / "configuration"
    success = hive_dir / "success"

    # Pull metadata from the configuration parquet (one row per generation;
    # use the first for study/investigation slugs since both rows carry the
    # same values for those columns by construction).
    investigation_slug = None
    metadata_slug = None
    try:
        if config.is_dir():
            cfg_df = pl.read_parquet(str(config / "**" / "*.pq"))
            if cfg_df.height > 0:
                first = cfg_df.row(0, named=True)
                metadata_slug = first.get("study_slug") or None
                investigation_slug = first.get("investigation_slug") or None
    except Exception as e:  # noqa: BLE001
        warnings.warn(
            f"simulations_index: parquet config read failed at {hive_dir}: {e}"
        )

    # Fall back to the study's owning investigation when the parquet metadata
    # didn't carry investigation_slug (older runner output, pre-convention).
    if not investigation_slug and study_slug:
        try:
            investigation_slug = WorkspacePaths.load(workspace).study_owner(study_slug)
        except Exception:  # noqa: BLE001
            pass

    # Row count = total emit count across all generations.
    try:
        n_rows = int(
            pl.scan_parquet(str(history / "**" / "*.pq"))
              .select(pl.len())
              .collect()
              .item()
        )
    except Exception:
        n_rows = 0

    # Status: success/ subdir means ParquetEmitter.close(success=True) ran.
    status = "completed" if success.is_dir() else "running"

    # Timestamps come from the filesystem since ParquetEmitter doesn't
    # stamp them itself. configuration/ is written at construction;
    # success/ at clean close.
    try:
        started_at = config.stat().st_mtime if config.is_dir() else run_dir.stat().st_mtime
    except OSError:
        started_at = None
    try:
        completed_at = success.stat().st_mtime if success.is_dir() else None
    except OSError:
        completed_at = None

    # sim_name: try the study.yaml first (gives the human-readable label) —
    # matched on the run id, then the inner experiment id — and finally fall
    # back to the run id (meaningful; the inner experiment id may be a shared
    # label reused across runs, so it's the worse default).
    yaml_path = WorkspacePaths.load(workspace).studies / study_slug / "study.yaml"
    sim_name = (
        _parquet_sim_name_from_yaml(yaml_path, run_id)
        or _parquet_sim_name_from_yaml(yaml_path, experiment_id)
        or run_id
    )

    # Workspace-relative path for the dashboard's UI (drill-down links) — the
    # hive dir, where configuration/ + history/ live.
    db_path = str(hive_dir.relative_to(workspace))

    return {
        "run_id":             run_id,
        "spec_id":            "",
        "sim_name":           sim_name,
        "label":              sim_name,
        "status":             status,
        "n_steps":            n_rows,
        "progress_step":      max(n_rows - 1, 0),
        "started_at":         started_at,
        "completed_at":       completed_at,
        "db_path":            db_path,
        "source":             "parquet",
        "studies":            [],
        # Prefer the slug recorded in metadata (authoritative for cross-
        # investigation reference runs whose path-slug is a pseudo-study);
        # fall back to the path slug for older hives.
        "study_slug":         metadata_slug or study_slug,
        "investigation_slug": investigation_slug,
    }


def _read_parquet_hives(workspace: Path) -> list[dict]:
    """Discover + read every parquet experiment under the workspace.
    Skips broken hives (returns the rest) so one bad write doesn't blank
    the Simulations tab."""
    out: list[dict] = []
    for hive_dir, run_dir, study_slug in _discover_parquet_hives(workspace):
        row = _read_parquet_hive(hive_dir, run_dir, study_slug, workspace)
        if row is not None:
            out.append(row)
    return out


# ---------------------------------------------------------------------------
# XArray (zarr) run discovery — the XArrayEmitter writes zarr stores under
# ``.pbg/runs/<run_id>/[seed_NN/]store.zarr`` (the PDMP investigation's default
# emitter). These never register a runs_meta row unless backfilled, so the
# Simulations DB must also discover them on disk to be XArray-aware.
# ---------------------------------------------------------------------------

def _discover_xarray_runs(workspace: Path) -> list[dict]:
    """Yield one row per ``.pbg/runs/<run_id>/`` dir that contains a
    ``store.zarr`` (directly or under ``seed_*/``). Shaped like the other
    readers (source/emitter = 'xarray') so the merge logic treats them
    uniformly. Metadata is recovered from the filesystem: ``run_id`` from the
    dir name, ``n_steps`` from the ``emitstep_gen=*`` partition count, and
    timestamps from mtime. Status is ``completed`` (a persisted zarr store is
    a finished write)."""
    runs_dir = Path(workspace) / ".pbg" / "runs"
    if not runs_dir.is_dir():
        return []
    out: list[dict] = []
    for run_dir in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
        # XArray stores nest at variable depth: <run>/store.zarr,
        # <run>/seed_NN/store.zarr (ensembles), or
        # <run>/<param-combo>/seed_NN/store.zarr (param-sweep ensembles).
        zarrs = (list(run_dir.glob("store.zarr"))
                 + list(run_dir.glob("*/store.zarr"))
                 + list(run_dir.glob("*/*/store.zarr")))
        if not zarrs:
            continue
        run_id = run_dir.name
        # Ensemble size = number of leaf zarr stores (seeds × param combos).
        n_leaves = len(zarrs)
        n_steps = None
        try:
            mtime = run_dir.stat().st_mtime
        except OSError:
            mtime = None
        out.append({
            "run_id": run_id,
            "spec_id": None,
            "sim_name": run_id,
            "label": run_id,
            "status": "completed",
            "n_steps": n_steps,
            "progress_step": 0,
            "ensemble_size": n_leaves,   # # of leaf zarr stores (seeds × params)
            "started_at": mtime,
            "completed_at": mtime,
            "db_path": str(run_dir.relative_to(workspace)),
            "studies": [],
            "study_slug": None,
            "investigation_slug": None,
            "source": "xarray",
        })
    return out


def _emitter_for_row(workspace: Path, row: dict) -> str:
    """Resolve the emitter that persisted a row: 'parquet' / 'xarray' / 'sqlite'.

    Thin back-compat wrapper over :func:`emitters.label_for_run` (the broker is
    now the single locus for emitter dispatch). parquet/xarray are known from
    their source tag; for SQLite-table rows we still disk-probe for a backfilled
    zarr store before defaulting to 'sqlite'."""
    return emitters.label_for_run(row, workspace)


def _discover_ce_store_path(workspace: Path, run_id: str) -> str | None:
    """Native store for a Composite Explorer run that was recorded without a
    ``store_path``. The Explorer writes its per-agent parquet sweep to
    ``<ws>/.pbg/runs/<run_id>/parquet/<run_id>`` (and, for xarray runs, a
    ``store.zarr`` under the same run dir), but runs_meta carries no store_path —
    so ``Location`` fell back to the sqlite runs.db. Return the workspace-relative
    store path when it exists on disk, else ``None``."""
    if not run_id:
        return None
    base = Path(workspace) / ".pbg" / "runs" / str(run_id)
    for cand in (base / "parquet" / str(run_id), base / "store.zarr"):
        if cand.is_dir():
            try:
                return cand.relative_to(workspace).as_posix()
            except ValueError:
                return str(cand)
    return None


def list_simulations(workspace: Path) -> list[dict]:
    """Return every persisted simulation in ``workspace``, newest first.

    Each dict contains: run_id, spec_id, sim_name, label, status, n_steps,
    progress_step, started_at, completed_at, db_path (workspace-relative),
    studies (list of study names that reference this run_id).

    When the same ``run_id`` is present in both the ``runs_meta`` and the
    SQLiteEmitter ``simulations`` table (the common case for runs created
    via ``pbg_runner``), the rows are MERGED into one entry — runs_meta
    fields (spec_id, status, n_steps) take priority over the emitter's,
    and the started_at is normalised to a float (unix epoch). Without
    this merge the dashboard's frontend JS sees mixed ISO-string /
    float started_at values and trips ``new Date(string * 1000)``,
    halting the table render.
    """
    def _to_float_ts(v):
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            try:
                return _dt.datetime.fromisoformat(
                    v.replace("Z", "+00:00")).timestamp()
            except ValueError:
                return None
        return None

    workspace = Path(workspace)
    rows: list[dict] = []
    for db_path, db_rel in _discover_dbs(workspace):
        # Try both schemas; each returns [] when its table is absent so the
        # two are non-overlapping per DB.
        rows.extend(_read_runs_meta(db_path, db_rel))
        rows.extend(_read_sqlite_emitter(db_path, db_rel))
    # Workspace-default emitter (post-2026-05-27 parquet migration). Each
    # row is shaped exactly like a sqlite_emitter row so the merge logic
    # below treats them uniformly. Source-tag "parquet" lets the frontend
    # render an emitter badge.
    rows.extend(_read_parquet_hives(workspace))
    # XArray (zarr) runs persisted under .pbg/runs/<id>/store.zarr — the PDMP
    # investigation's default emitter. Surfaced live so they show even when no
    # runs_meta row was recorded; dedup below merges with any backfilled row.
    rows.extend(_discover_xarray_runs(workspace))
    # Runs recorded only in study.yaml `runs:` (emitter-less workspaces — numpy
    # investigations like pbg-autopoiesis). Added last so a real DB row for the
    # same run_id wins in the dedup below; study.yaml-only runs still surface.
    rows.extend(_read_study_yaml_runs(workspace))

    # Deduplicate by run_id, preferring runs_meta over sqlite_emitter (so
    # spec_id / status / n_steps come from the canonical bookkeeping table).
    # Normalise started_at + completed_at to floats while we're here.
    merged: dict[str, dict] = {}
    for r in rows:
        rid = r.get("run_id")
        if not rid:
            continue
        r = dict(r)  # don't mutate the source
        r["started_at"]   = _to_float_ts(r.get("started_at"))
        r["completed_at"] = _to_float_ts(r.get("completed_at"))
        existing = merged.get(rid)
        if existing is None:
            merged[rid] = r
            continue
        # Prefer runs_meta on collision (it has spec_id and authoritative status).
        if existing.get("source") == "runs_meta":
            # Keep existing; fill in any None fields from the new row.
            for k, v in r.items():
                if existing.get(k) in (None, "", []) and v not in (None, "", []):
                    existing[k] = v
        else:
            # Existing was sqlite_emitter; r is preferred if it's runs_meta.
            if r.get("source") == "runs_meta":
                for k, v in existing.items():
                    if r.get(k) in (None, "", []) and v not in (None, "", []):
                        r[k] = v
                merged[rid] = r
            else:
                # Both sqlite_emitter — just fill in missing fields.
                for k, v in r.items():
                    if existing.get(k) in (None, "", []) and v not in (None, "", []):
                        existing[k] = v
    rows = list(merged.values())

    def _ts(r):
        # After dedupe above, started_at is already normalised to float
        # (or None). Trivial sort key here.
        v = r.get("started_at")
        return float(v) if isinstance(v, (int, float)) else 0.0
    rows.sort(key=_ts, reverse=True)

    run_to_studies = _build_run_to_studies_map(workspace)
    _wp = WorkspacePaths.load(workspace)
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
        # Derive the owning investigation from the study when the run record
        # didn't carry it (study.yaml-declared runs, and legacy DBs without the
        # investigation_slug column) — the workspace knows every study's owner.
        if not r.get("investigation_slug") and r.get("study_slug"):
            try:
                r["investigation_slug"] = _wp.study_owner(r["study_slug"]) or None
            except Exception:
                pass
        # Surface the Composite Explorer run's on-disk parquet sweep as the store
        # location (runs_meta didn't record it → store_path was None → Location
        # showed the sqlite db).
        _ce_store = False
        if not r.get("store_path") and r.get("run_id"):
            _sp = _discover_ce_store_path(workspace, r["run_id"])
            if _sp:
                r["store_path"] = _sp
                _ce_store = True
        # Emitter-awareness: tag each row with the emitter that persisted it
        # (xarray / parquet / sqlite) so the Simulations DB can show a column.
        r["emitter"] = _emitter_for_row(workspace, r)
        # A CE run writes BOTH a sqlite history sidecar and its declared
        # parquet/zarr sweep; when we found the native sweep, classify the row by
        # it (matches the Location) instead of the sqlite sidecar.
        if _ce_store:
            _k = {"zarr": "xarray", "parquet": "parquet"}.get(
                run_store.detect_kind(r["store_path"]))
            if _k:
                r["emitter"] = _k
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
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
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
    from .atomic_io import atomic_write_text
    atomic_write_text(yaml_path, yaml.safe_dump(data, sort_keys=False))
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

    wp = WorkspacePaths.load(workspace)
    errors: list[str] = []
    deleted_rows, deleted_history = _delete_db_rows(db_path, run_id)

    run_dir = wp.pbg / "runs" / run_id
    removed_dir = run_dir.exists()
    if removed_dir:
        shutil.rmtree(run_dir, ignore_errors=True)
        # If ignore_errors didn't fully remove it, surface that:
        if run_dir.exists():
            errors.append(f"run dir {run_dir.relative_to(workspace)}: partial removal")
            removed_dir = False

    unlinked: list[str] = []
    studies_root = wp.studies
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

    # Tombstone the run in the append-only JSONL log. Without this the next
    # fold re-synthesises the row from its surviving `started` event and the
    # run reappears — undeletable through the UI. Best-effort: a log failure
    # must not turn an otherwise-successful delete into a 500.
    try:
        run_log.append_deleted_event(workspace, run_id)
    except OSError as e:
        errors.append(f"run log tombstone: {type(e).__name__}: {e}")

    return {
        "deleted_rows": deleted_rows,
        "deleted_history": deleted_history,
        "removed_dir": removed_dir,
        "unlinked_studies": unlinked,
        "errors": errors,
    }


def _emitter_tag(emitter) -> str:
    """Normalise a row's ``emitter`` field to a lowercase string tag.

    The value may be a plain string ("parquet"), a structured dict
    ({"kind": "parquet", "store": ...}) declared in a study.yaml ``runs:``
    entry, or None. A dict reaching ``.lower()`` used to raise AttributeError
    inside the emitter_type loop — silently swallowed — which blanked every
    row's emitter_type and made the UI default the pill to "SQLite".
    """
    if isinstance(emitter, dict):
        emitter = emitter.get("kind")
    return emitter.lower() if isinstance(emitter, str) else ""


def _append_remote_simulations(sims: list, ws_root: Path) -> list:
    """Append the active remote build's server-side runs (scoped to the build's
    commit/repo) to the local Simulations-DB rows. No-op for local workspaces
    or when sms-api is unreachable — single source for the local+remote merge,
    shared by ``build_simulations_data`` and the ``/api/simulations`` handler."""
    try:
        from vivarium_workbench.lib.remote_simulations import list_remote_simulations
        remote = list_remote_simulations(ws_root)
    except Exception:
        remote = []
    return list(sims) + remote if remote else sims


def build_simulations_data(ws_root: Path) -> dict:
    """Data builder for GET /api/simulations — the ``list_simulations`` rows
    enriched with emitter_type labels + active remote build runs + current slug.

    Returns ``{"simulations": [...], "current": <slug|None>}``.  Tolerates
    missing DB / import errors → returns an empty list.  Relocated verbatim from
    the retired ``server._simulations_data`` so publish.build_bundle and the
    ``/api/simulations`` seam share one implementation.
    """
    ws = str(ws_root)
    import sys as _sys
    if ws not in _sys.path:
        _sys.path.insert(0, ws)
    try:
        sims = list_simulations(ws_root)
    except Exception:
        return {"simulations": [], "current": None}

    # Shared emitter-kind -> display-label map, used by both the JSONL merge
    # below and the sqlite/db_path fallback pass that follows it. Defined
    # once here (rather than duplicated per-block) so there's a single place
    # to add a kind's label -- e.g. "ram", needed by the JSONL branch.
    _emitter_label = {"sqlite": "SQLite", "parquet": "Parquet", "xarray": "XArray",
                       "ram": "RAM", "none": "—"}  # no step emitter (summary-only run)

    # Fold the workspace's append-only JSONL run log (Task 1/2) and merge it
    # with the sqlite-gathered `sims` rows above. JSONL is the source of
    # truth for a run_id's fields when present (it's written on every
    # save/complete, including emitter-less/in-progress runs the sqlite
    # gather can miss); legacy-sqlite-only rows pass through untouched.
    try:
        folded = run_log.fold_runs_jsonl(Path(ws_root))
        by_id = {s.get("run_id"): s for s in sims}
        for rid, rec in folded.items():
            row = by_id.get(rid)
            is_new_row = row is None
            if is_new_row:
                row = {"run_id": rid}
                sims.append(row)
                by_id[rid] = row
            # JSONL is the source of truth for these fields when present.
            for k in ("spec_id", "label", "status", "n_steps", "started_at",
                      "completed_at", "study_slug", "investigation_slug"):
                if rec.get(k) is not None:
                    row[k] = rec[k]
            if rec.get("emitter"):
                row["emitter"] = rec["emitter"]
                row["emitter_type"] = _emitter_label.get(rec["emitter"], rec["emitter"])
            elif is_new_row:
                # JSONL-only row with no emitter recorded -- don't let it
                # fall through to the SQLite-classification pass below,
                # which would default an unknown/empty emitter to "SQLite"
                # via emitter_type_of(None).
                row["emitter_type"] = "—"
            # `origin` in the JSONL is a *kind* string ("local" for every run
            # save_metadata logs). `remote_origin` is a RemoteOrigin mapping
            # ({deployment, simulation_id, ...}) -- assigning the string here
            # fails SimRow validation and 500s /api/simulations, and because
            # the log is append-only a single run would brick the page for
            # good. Only propagate a genuine remote mapping.
            origin = rec.get("origin")
            if isinstance(origin, dict) and origin:
                row["remote_origin"] = origin
    except Exception:
        pass

    try:
        from vivarium_workbench.lib.runs_index import emitter_type_of
        for s in sims:
            if s.get("emitter_type"):
                continue  # already labeled by the JSONL merge above
            s["emitter_type"] = _emitter_label.get(
                _emitter_tag(s.get("emitter"))) or emitter_type_of(s.get("db_path"))
    except Exception:
        pass

    # Re-sort newest-first: the JSONL merge above appends any JSONL-only
    # run_ids (e.g. a fresh Composite-Explorer parquet run recorded only in
    # the run log) onto the END of `sims`, so without this they'd sink to
    # the bottom of the newest-first table instead of surfacing at the top.
    # Prefers completed_at over started_at (a completed run's "newest"
    # instant is its completion), matching the existing sqlite-path sort key
    # used by the frontend/backend elsewhere; missing timestamps sort last
    # rather than raising.
    sims.sort(key=lambda r: (r.get("completed_at") or r.get("started_at") or 0),
              reverse=True)

    sims = _append_remote_simulations(sims, ws_root)
    from vivarium_workbench.lib.investigation_status import current_branch_slug
    return {"simulations": sims, "current": current_branch_slug(ws_root)}


def build_simulation_run_zip(workspace: Path, run_id: str) -> "tuple[bytes, str, int]":
    """Zip a run's RAW EMITTER DATA for download (GET /api/simulation-run-download).

    Resolves the run's on-disk store from ``run_id`` via the workspace scan, so
    NO filesystem path is trusted from the client. Prefers the native
    zarr/parquet store directory; falls back to the SQLite ``runs.db`` that holds
    the run's rows. Remote (``s3://``) stores can't be zipped locally, so those
    fall back to the local metadata DB.

    Returns ``(zip_bytes, filename, status)``:
      200 — zip built;  404 — run not found or its store is absent on disk.
    """
    import io
    import re as _re
    import zipfile

    workspace = Path(workspace)
    row = next(
        (r for r in list_simulations(workspace) if r.get("run_id") == run_id),
        None,
    )
    if row is None:
        return b"", "", 404

    def _resolve(p: "str | None") -> "Path | None":
        if not p or str(p).startswith(("s3://", "http://", "https://")):
            return None
        pp = Path(p)
        pp = pp if pp.is_absolute() else (workspace / pp)
        try:
            pp = pp.resolve()
        except OSError:
            return None
        return pp if pp.exists() else None

    target = _resolve(row.get("store_path")) or _resolve(row.get("db_path"))
    if target is None:
        return b"", "", 404

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if target.is_dir():
            base = target.parent
            for f in sorted(target.rglob("*")):
                if f.is_file():
                    zf.write(f, f.relative_to(base))
        else:
            zf.write(target, target.name)

    safe = _re.sub(r"[^A-Za-z0-9._-]+", "_", str(run_id)).strip("_") or "run"
    return buf.getvalue(), f"{safe}_emitter.zip", 200
