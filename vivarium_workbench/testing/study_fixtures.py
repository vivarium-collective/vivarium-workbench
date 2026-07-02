"""Shared pytest fixtures for study behavioral tests.

Usage in a per-study ``tests/conftest.py``::

    from vivarium_dashboard.testing.study_fixtures import (  # noqa: F401
        baseline_history,
        variant_history_factory,
        bulk_count,
        listener_value,
    )

Pattern: per-study conftest.py shrinks to a thin re-export (≤ 10 lines)
plus any study-specific variant fixtures that delegate to
:func:`_load_history_for_variant`.

DB schema assumed
-----------------
The runs.db written by ``process_bigraph.emitter.SQLiteEmitter`` has::

    CREATE TABLE runs_meta (
        run_id     TEXT PRIMARY KEY,
        label      TEXT,       -- variant label or None for baseline
        status     TEXT,       -- 'completed' | 'running' | 'failed'
        completed_at TEXT,
        ...
    );

    CREATE TABLE history (
        simulation_id TEXT,
        step          INTEGER,
        global_time   REAL,
        state         TEXT,    -- JSON-serialised snapshot
        ...
    );
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest


# ─── Internal DB helpers ─────────────────────────────────────────────────────


def _open_db(db_path: Path) -> sqlite3.Connection | None:
    """Return a connection to *db_path*, or ``None`` if the file doesn't exist."""
    if not db_path.exists():
        return None
    return sqlite3.connect(str(db_path))


def _latest_run_id(
    conn: sqlite3.Connection,
    variant_label: str | None,
) -> str | None:
    """Return the most recently completed run_id, optionally filtered by *variant_label*."""
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='runs_meta'"
    )
    if cur.fetchone() is None:
        return None
    if variant_label is None:
        row = conn.execute(
            "SELECT run_id FROM runs_meta WHERE status='completed' "
            "ORDER BY completed_at DESC LIMIT 1"
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT run_id FROM runs_meta "
            "WHERE status='completed' AND label=? "
            "ORDER BY completed_at DESC LIMIT 1",
            (variant_label,),
        ).fetchone()
    return row[0] if row else None


def _load_history(conn: sqlite3.Connection, run_id: str) -> list[dict]:
    """Return ordered ``{step, time, state}`` dicts for *run_id*."""
    rows = conn.execute(
        "SELECT step, global_time, state FROM history "
        "WHERE simulation_id=? ORDER BY step ASC",
        (run_id,),
    ).fetchall()
    return [
        {"step": step, "time": time, "state": json.loads(state)}
        for step, time, state in rows
    ]


def _history_for(
    db_path: Path,
    variant_label: str | None = None,
) -> list[dict] | None:
    """Load history for the most recent completed run (optionally by variant label).

    Returns ``None`` when:
    - runs.db doesn't exist, or
    - no completed run with the requested label exists.
    """
    conn = _open_db(db_path)
    if conn is None:
        return None
    try:
        run_id = _latest_run_id(conn, variant_label)
        if run_id is None:
            return None
        return _load_history(conn, run_id)
    finally:
        conn.close()


# ─── Public study-slug accessors ─────────────────────────────────────────────


def baseline_history_for(study_slug: str, studies_root: Path | None = None) -> list[dict] | None:
    """Read the latest baseline run from ``studies/<study_slug>/runs.db``.

    Parameters
    ----------
    study_slug:
        The study directory name (e.g. ``"dnaa-01-expression-dynamics"``).
    studies_root:
        Directory that contains study sub-directories. Defaults to
        ``<cwd>/studies``.

    Returns ``None`` when runs.db is absent or has no completed baseline.
    """
    root = studies_root or (Path.cwd() / "studies")
    db = root / study_slug / "runs.db"
    return _history_for(db, variant_label=None)


def variant_history_for(
    study_slug: str,
    variant_name: str,
    studies_root: Path | None = None,
) -> list[dict] | None:
    """Read the latest variant run from ``studies/<study_slug>/runs.db``.

    Parameters
    ----------
    study_slug:
        The study directory name.
    variant_name:
        Label stored in ``runs_meta.label`` (e.g. ``"stop-dnaA-synthesis"``).
    studies_root:
        Directory that contains study sub-directories.

    Returns ``None`` when runs.db is absent or no matching completed run exists.
    """
    root = studies_root or (Path.cwd() / "studies")
    db = root / study_slug / "runs.db"
    return _history_for(db, variant_label=variant_name)


# ─── Pytest fixtures (session-scoped) ────────────────────────────────────────


def _make_baseline_fixture(db_path: Path):
    """Factory: create a session-scoped ``baseline_history`` fixture for *db_path*."""

    @pytest.fixture(scope="session")
    def baseline_history():
        """Latest completed baseline run, or pytest.skip if none."""
        conn = _open_db(db_path)
        if conn is None:
            pytest.skip(f"no runs.db at {db_path}; run the baseline first")
        try:
            run_id = _latest_run_id(conn, variant_label=None)
            if run_id is None:
                pytest.skip("no completed baseline run in runs.db")
            return _load_history(conn, run_id)
        finally:
            conn.close()

    return baseline_history


def _make_variant_fixture(db_path: Path, variant_label: str):
    """Factory: create a session-scoped variant-history fixture for *db_path* / *variant_label*."""

    @pytest.fixture(scope="session")
    def variant_history():
        """Latest completed variant run, or pytest.skip if none."""
        conn = _open_db(db_path)
        if conn is None:
            pytest.skip(f"no runs.db at {db_path}; run the {variant_label!r} variant first")
        try:
            run_id = _latest_run_id(conn, variant_label=variant_label)
            if run_id is None:
                pytest.skip(f"no completed {variant_label!r} run in runs.db")
            return _load_history(conn, run_id)
        finally:
            conn.close()

    return variant_history


def make_study_fixtures(
    study_dir: Path,
    variant_names: list[str] | None = None,
) -> dict[str, Any]:
    """Build a dict of pytest fixtures for *study_dir*.

    Returns a mapping suitable for injection into a per-study ``conftest.py``::

        from vivarium_dashboard.testing.study_fixtures import make_study_fixtures
        _f = make_study_fixtures(Path(__file__).resolve().parents[1],
                                 variant_names=["stop-dnaA-synthesis"])
        baseline_history = _f["baseline_history"]
        stop_dnaA_synthesis_history = _f["stop-dnaA-synthesis"]

    Keys:
      - ``"baseline_history"`` — fixture returning the latest baseline history.
      - one entry per name in *variant_names* — fixture for that variant.
    """
    db_path = study_dir / "runs.db"
    fixtures: dict[str, Any] = {
        "baseline_history": _make_baseline_fixture(db_path),
    }
    for vname in (variant_names or []):
        fixtures[vname] = _make_variant_fixture(db_path, vname)
    return fixtures


# ─── State accessor helpers (re-exported from lib for test convenience) ───────


def bulk_count(state: dict, molecule_id: str) -> int | float | None:
    """Return count of *molecule_id* in one snapshot's bulk store.

    The bulk store is a ``bulk_array``: either ``{"id": [...], "count": [...]}``
    or a list of ``(id, count)`` pairs.  Searches the first agent.
    """
    agents = state.get("agents") or {}
    if not agents:
        return None
    first_agent = next(iter(agents.values()))
    bulk = first_agent.get("bulk")
    if bulk is None:
        return None
    if isinstance(bulk, dict) and "id" in bulk and "count" in bulk:
        ids = bulk["id"]
        counts = bulk["count"]
    elif isinstance(bulk, list) and bulk and isinstance(bulk[0], (list, tuple)):
        ids = [row[0] for row in bulk]
        counts = [row[1] for row in bulk]
    else:
        return None
    try:
        idx = ids.index(molecule_id)
    except ValueError:
        return None
    return counts[idx]


def listener_value(state: dict, path: str) -> Any:
    """Walk a dotted *path* inside the first agent's subtree.

    Example::

        listener_value(state, 'listeners.rnap_data.rna_init_event')

    Returns ``None`` if any segment is missing.
    """
    agents = state.get("agents") or {}
    if not agents:
        return None
    cursor = next(iter(agents.values()))
    for seg in path.split("."):
        if not isinstance(cursor, dict) or seg not in cursor:
            return None
        cursor = cursor[seg]
    return cursor
