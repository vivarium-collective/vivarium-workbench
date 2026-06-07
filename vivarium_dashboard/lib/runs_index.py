"""Global run index across all studies, tagged by investigation/study/emitter.

VENDORED COPY. Canonical source: pbg_superpowers/runs_index.py in the
pbg-superpowers repo. The dashboard venv has no pbg_superpowers, so this is
a near-byte-faithful copy. ``emitter_type_of`` + ``_all_runs`` are kept
byte-identical to canonical (the drift guard in
tests/test_runs_index_mirror.py compares them). ``list_all_runs`` differs in
ONE way: the dashboard's ``lib/`` has no ``backfill_runs`` module, so the
``from .backfill_runs import backfill_study_runs`` import is wrapped in
try/except and backfill is skipped when unavailable. ``list_all_runs`` is
therefore EXCLUDED from the byte-compare.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


def emitter_type_of(emitter_path: str | None) -> str:
    p = str(emitter_path or "").lower()
    if ".zarr" in p:
        return "XArray"
    if ".parquet" in p:
        return "Parquet"
    return "SQLite"


def _all_runs(runs_db: Path) -> list[dict]:
    runs_db = Path(runs_db)
    if not runs_db.is_file():
        return []
    cols = ("run_id", "started_at", "completed_at", "status", "emitter_path")
    try:
        conn = sqlite3.connect(f"file:{runs_db}?mode=ro", uri=True, timeout=1.0)
        try:
            have = {r[1] for r in conn.execute("PRAGMA table_info(runs_meta)")}
            use = [c for c in cols if c in have]
            rows = conn.execute(
                f"SELECT {', '.join(use)} FROM runs_meta "
                "ORDER BY COALESCE(completed_at, started_at) DESC"
            ).fetchall()
        finally:
            conn.close()
        return [dict(zip(use, r)) for r in rows]
    except sqlite3.Error:
        return []


def list_all_runs(ws_root: Path) -> list[dict]:
    """All runs across every study, tagged investigation/study/emitter, newest first."""
    from .workspace_paths import WorkspacePaths
    try:
        from .backfill_runs import backfill_study_runs
    except ImportError:
        backfill_study_runs = None
    wp = WorkspacePaths.load(Path(ws_root))
    out: list[dict] = []
    for sd in wp.iter_study_dirs():
        slug = sd.name
        owner = wp.study_owner(slug)
        if backfill_study_runs is not None:
            try:
                backfill_study_runs(sd, spec_id=slug)
            except Exception:
                pass
        for r in _all_runs(sd / "runs.db"):
            out.append({
                "investigation": owner,
                "study": slug,
                "run_id": r.get("run_id"),
                "started_at": r.get("started_at"),
                "completed_at": r.get("completed_at"),
                "status": r.get("status"),
                "emitter_path": r.get("emitter_path"),
                "emitter_type": emitter_type_of(r.get("emitter_path")),
            })
    out.sort(key=lambda x: (x.get("completed_at") or x.get("started_at") or 0), reverse=True)
    return out
