"""Backfill the per-study runs.db from on-disk emitter artifacts.

VENDORED COPY. Canonical source: pbg_superpowers/backfill_runs.py in the
pbg-superpowers repo. The dashboard venv has no pbg_superpowers, so this is a
copy of ONLY ``backfill_study_runs`` (the workspace-wide ``backfill`` + CLI in
canonical pull extra deps and are not vendored). ``backfill_study_runs`` is
kept byte-identical to canonical — the drift guard in
tests/test_backfill_runs_mirror.py compares it. Its
``from .run_registry import RUNS_META_DDL`` resolves to the vendored
``lib/run_registry.py`` (which also carries RUNS_META_DDL + latest_run).
"""
from __future__ import annotations

from pathlib import Path


def backfill_study_runs(study_dir, spec_id: str, *, emitter_subdir: str = "out") -> int:
    """Register any on-disk emitter run directory under
    ``<study_dir>/<emitter_subdir>/<run_id>/`` (containing *.parquet / *.zarr
    partitions) that is not already a runs_meta row in the study's runs.db.

    completed_at/started_at = newest partition mtime; emitter_path = the run
    dir relative to study_dir. Returns the number of rows inserted. Idempotent.
    """
    import sqlite3
    from pathlib import Path
    from .run_registry import RUNS_META_DDL

    study_dir = Path(study_dir)
    emitter_root = study_dir / emitter_subdir
    if not emitter_root.is_dir():
        return 0
    runs_db = study_dir / "runs.db"
    conn = sqlite3.connect(runs_db)
    try:
        conn.executescript(RUNS_META_DDL)  # ensure the table exists
        existing = {r[0] for r in conn.execute("SELECT run_id FROM runs_meta")}
        inserted = 0
        for run_dir in sorted(p for p in emitter_root.iterdir() if p.is_dir()):
            parts = (list(run_dir.glob("**/*.parquet"))
                     + list(run_dir.glob("*.zarr"))
                     + list(run_dir.glob("**/*.zarr")))
            if not parts:
                continue
            run_id = run_dir.name
            if run_id in existing:
                continue
            mtime = max((p.stat().st_mtime for p in parts),
                        default=run_dir.stat().st_mtime)
            rel = str(run_dir.relative_to(study_dir))
            conn.execute(
                "INSERT INTO runs_meta(run_id, spec_id, started_at, completed_at,"
                " status, emitter_path) VALUES(?,?,?,?,?,?)",
                (run_id, spec_id, mtime, mtime, "complete", rel),
            )
            inserted += 1
        conn.commit()
        return inserted
    finally:
        conn.close()
