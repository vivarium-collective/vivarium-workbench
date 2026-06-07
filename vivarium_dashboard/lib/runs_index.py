"""Global run index across all studies, tagged by investigation/study/emitter.

VENDORED COPY. Canonical source: pbg_superpowers/runs_index.py in the
pbg-superpowers repo. The dashboard venv has no pbg_superpowers, so this is
a near-byte-faithful copy. ``emitter_type_of`` + ``_all_runs`` are kept
byte-identical to canonical (the drift guard in
tests/test_runs_index_mirror.py compares emitter_type_of, _store_emitter_type,
and _all_runs). ``list_all_runs`` is EXCLUDED from that byte-compare: it is
functionally equivalent to canonical but wraps the per-study
``backfill_study_runs`` call in try/except so a backfill failure never breaks
the listing. The vendored ``lib/backfill_runs`` (and ``lib/run_registry``'s
RUNS_META_DDL) now satisfy the import.
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


def _store_emitter_type(store):
    """Classify a directory emitter store by name hint + bounded content scan.
    Returns 'XArray' | 'Parquet' | None (None -> caller keeps SQLite)."""
    from pathlib import Path
    store = Path(store)
    name = store.name.lower()
    if name.endswith(".zarr") or "zarr" in name:
        return "XArray"
    if "parquet" in name:
        return "Parquet"
    try:
        if not store.is_dir():
            return None
        for pat in ("*.zarr", "*/*.zarr", ".zgroup", "*/.zgroup", ".zarray"):
            if next(store.glob(pat), None) is not None:
                return "XArray"
        for pat in ("*.parquet", "*/*.parquet", "*/*/*.parquet"):
            if next(store.glob(pat), None) is not None:
                return "Parquet"
    except OSError:
        pass
    return None


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
    from .backfill_runs import backfill_study_runs
    wp = WorkspacePaths.load(Path(ws_root))
    out: list[dict] = []
    for sd in wp.iter_study_dirs():
        slug = sd.name
        owner = wp.study_owner(slug)
        try:
            backfill_study_runs(sd, spec_id=slug)
        except Exception:
            pass
        for r in _all_runs(sd / "runs.db"):
            ep = r.get("emitter_path")
            etype = emitter_type_of(ep)
            if etype == "SQLite" and ep:
                etype = _store_emitter_type(sd / ep) or "SQLite"
            out.append({
                "investigation": owner,
                "study": slug,
                "run_id": r.get("run_id"),
                "started_at": r.get("started_at"),
                "completed_at": r.get("completed_at"),
                "status": r.get("status"),
                "emitter_path": ep,
                "emitter_type": etype,
            })
    out.sort(key=lambda x: (x.get("completed_at") or x.get("started_at") or 0), reverse=True)
    return out
