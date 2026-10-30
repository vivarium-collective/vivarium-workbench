"""Derive a study's status evidence from what is on disk.

``evidence_for_study`` is a pure read of ``studies/<slug>/runs.db`` (runs_meta),
the report-card ``viz/report_card/*.verdict.json`` files, and the latest run's
``<ws>/.pbg/runs/<run_id>/report.json``. It never writes and never imports the
server, so status badges can be computed from evidence instead of frozen
``study.yaml`` fields.

Ground truth: ``runs_meta.status`` is one of running / completed / failed /
orphaned / cancelled (``ran`` / ``done`` also count as success). Gate mapping:
``passed`` = at least one graded card and all graded cards ``within_tol``;
``failed`` = any graded card is ``mismatch`` or ``drift``; else ``not_started``.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from vivarium_workbench.lib.workspace_paths import WorkspacePaths

_log = logging.getLogger(__name__)

_COMPLETE = {"complete", "completed", "ran", "done"}
_UNGRADED = {"", "ungraded", "pending", "planned", "not_run", "n/a", "none"}


@dataclass(frozen=True)
class StudyEvidence:
    execution: str   # completed | failed | running | not_run
    gate: str        # passed | failed | not_started
    evaluation: str  # evaluated | not_evaluated
    tests_passed: int
    tests_total: int
    n_runs: int


def _query_rows(uri: str) -> list[tuple[str, str]]:
    conn = sqlite3.connect(uri, uri=True, timeout=1.0)
    try:
        return [(r[0], str(r[1] or "").strip().lower()) for r in conn.execute(
            "SELECT run_id, status FROM runs_meta "
            "ORDER BY COALESCE(completed_at, started_at) DESC, started_at DESC")]
    finally:
        conn.close()


def _run_rows(runs_db: Path) -> list[tuple[str, str]]:
    """``[(run_id, status)]`` newest-first from runs_meta.

    A missing file means no runs. A file that exists but cannot be read is retried
    with ``immutable=1`` (read-only published bundles can't create a WAL ``-shm``);
    if that also fails the error is logged before falling back to ``[]``.
    """
    if not runs_db.is_file():
        return []
    try:
        return _query_rows(f"file:{runs_db}?mode=ro")
    except sqlite3.Error:
        pass
    try:
        return _query_rows(f"file:{runs_db}?mode=ro&immutable=1")
    except sqlite3.Error as exc:
        _log.warning("runs.db exists but could not be read (%s): %s; "
                     "treating as no runs", runs_db, exc)
        return []


def _execution(rows: list[tuple[str, str]]) -> str:
    """Aggregate over ALL runs: any running -> running; else any success ->
    completed (a later failed re-run does not erase a completed run); else
    rows exist -> failed; no rows -> not_run."""
    if not rows:
        return "not_run"
    statuses = {s for _, s in rows}
    if "running" in statuses:
        return "running"
    if statuses & _COMPLETE:
        return "completed"
    return "failed"


def _graded_overalls(study_dir: Path) -> list[str]:
    rc = study_dir / "viz" / "report_card"
    out: list[str] = []
    if not rc.is_dir():
        return out
    for vj in sorted(rc.glob("*.verdict.json")):
        try:
            overall = str((json.loads(vj.read_text(encoding="utf-8")) or {})
                          .get("overall") or "").strip().lower()
        except Exception:  # noqa: BLE001
            continue
        if overall not in _UNGRADED:
            out.append(overall)
    return out


def _gate(graded: list[str]) -> str:
    if not graded:
        return "not_started"
    if any(o in ("mismatch", "drift") for o in graded):
        return "failed"
    return "passed" if all(o == "within_tol" for o in graded) else "not_started"


def _test_counts(ws_root: Path, run_id: str | None) -> tuple[int, int]:
    """(passed, total) cards in the latest run's report.json; (0, 0) if none."""
    if not run_id:
        return 0, 0
    try:
        f = WorkspacePaths.load(ws_root).pbg / "runs" / run_id / "report.json"
        if not f.is_file():
            return 0, 0
        cards = (json.loads(f.read_text(encoding="utf-8")) or {}).get("cards") or {}
        overalls = [str((c or {}).get("overall") or "").strip().lower()
                    for c in cards.values()]
        return sum(o == "within_tol" for o in overalls), len(overalls)
    except Exception:  # noqa: BLE001
        return 0, 0


def evidence_for_study(ws_root: Path, slug: str) -> StudyEvidence:
    ws_root = Path(ws_root)
    study_dir = WorkspacePaths.load(ws_root).study_dir(slug)
    rows = _run_rows(study_dir / "runs.db")
    graded = _graded_overalls(study_dir)
    passed, total = _test_counts(ws_root, rows[0][0] if rows else None)
    return StudyEvidence(
        execution=_execution(rows),
        gate=_gate(graded),
        evaluation="evaluated" if graded else "not_evaluated",
        tests_passed=passed,
        tests_total=total,
        n_runs=len(rows),
    )
