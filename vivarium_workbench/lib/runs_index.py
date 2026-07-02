"""Emitter-type classification helpers used by the dashboard's Simulations DB.

VENDORED COPY. Canonical source: pbg_superpowers/runs_index.py in the
pbg-superpowers repo. The dashboard venv has no pbg_superpowers, so this is
a byte-faithful copy. ``emitter_type_of`` + ``_store_emitter_type`` are kept
byte-identical to canonical (the drift guard in
tests/test_runs_index_mirror.py compares both).

The workspace-wide run listing itself is owned by
``vivarium_workbench.lib.simulations_index.list_simulations``; these helpers
just classify an emitter store path into its canonical type label and are
reused by the ``GET /api/simulations`` handler to tag each sim's emitter_type.
"""
from __future__ import annotations


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
