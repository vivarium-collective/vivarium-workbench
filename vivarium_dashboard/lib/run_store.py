"""Canonical run-store path & kind resolution for study runs.

A study run's output lands in one of a few store layouts next to the study's
``runs.db``. The primary XArray naming convention (``<study>/runs.<run_id>.zarr``)
was historically re-derived independently in the writer (``composite_subprocess``),
the reader (``study_run_state.zarr_store_for_sim``), and the chart layer
(``study_charts``), and the emitter kind was guessed by substring in
``simulations_index``. This module is the single home for that convention: a
layout change is a one-line edit here instead of a hunt across modules.

Store layouts:
  - XArray (zarr):  ``<study>/runs.<run_id>.zarr``      (the primary convention)
  - Parquet hive:   ``<study>/parquet-runs/<run>/...``
  - SQLite:         ``<study>/runs.db``                 (metadata + trajectory)
  - Remote/.pbg:    ``<workspace>/.pbg/runs/<run_id>/store.zarr``  (competing layout)
"""
from __future__ import annotations

from pathlib import Path

# The per-run XArray zarr store is named "<runs-db-stem>.<run_id>.zarr" and sits
# next to the study's runs.db. runs.db -> stem "runs" -> "runs.<run_id>.zarr".
ZARR_STORE_STEM = "runs"


def zarr_store_path(study_dir: Path | str, run_id: str) -> Path:
    """Canonical per-run XArray zarr store path: ``<study_dir>/runs.<run_id>.zarr``."""
    return Path(study_dir) / f"{ZARR_STORE_STEM}.{run_id}.zarr"


def zarr_store_path_for_db(study_db: Path | str, run_id: str) -> Path:
    """The same per-run store, keyed off the study's ``runs.db`` path.

    Yields ``<db_stem>.<run_id>.zarr`` beside the db — identical to
    :func:`zarr_store_path` when the db is the conventional ``runs.db``.
    """
    study_db = Path(study_db)
    return study_db.parent / f"{study_db.stem}.{run_id}.zarr"


def iter_zarr_stores(study_dir: Path | str) -> list[Path]:
    """All per-run zarr store directories in a study dir (matching ``runs.*.zarr``)."""
    d = Path(study_dir)
    if not d.is_dir():
        return []
    return [p for p in d.glob(f"{ZARR_STORE_STEM}.*.zarr") if p.is_dir()]


def detect_kind(store_path: Path | str | None) -> str | None:
    """Emitter/store kind from a store path: ``"zarr" | "parquet" | "sqlite" | None``.

    Uses pbg-emitters' ``EmitterContract.output_kind`` vocabulary. ``None`` means
    "unknown / the run's data lives in the ``runs.db`` SQLite" — callers fall back
    to the db path. This is the single place the kind is inferred from a path, so
    the substring heuristic lives here rather than being re-implemented per view.
    """
    s = str(store_path or "").lower()
    if not s:
        return None
    if ".zarr" in s:
        return "zarr"
    if "parquet" in s:
        return "parquet"
    if s.endswith(".db") or "sqlite" in s:
        return "sqlite"
    return None
