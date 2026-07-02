"""Study-run state-resolution helpers extracted from server.py.

These are the ``ws_root``-parameterized leaf helpers for the study-run engine
(study-run engine extraction, phase E2). The legacy server.py module-level
helpers (``_resolve_study_baseline_state``, ``_investigation_emitter_for_study``,
``_zarr_store_for_sim``) now delegate to the corresponding functions here via
thin name-shims, keeping their existing call-sites + test imports intact and the
live path byte-identical.

None of these functions run a simulation or import ``server`` — they resolve a
composite spec_id to a state dict, read an investigation's declared emitter, and
map a sim_name to its most-recent zarr store, respectively. Each takes the
workspace root (or an explicit study db path) as an argument rather than reading
a module global, which keeps the module importable standalone and flip-ready.

Functions
---------
resolve_study_baseline_state    → generator/YAML composite spec_id + params → state
investigation_emitter_for_study → investigation-declared default emitter for a study
zarr_store_for_sim              → most-recent XArrayEmitter zarr store for a sim_name
"""

from __future__ import annotations

from pathlib import Path

import yaml

from vivarium_workbench.lib import run_store


def investigation_emitter_for_study(ws_root, study_name: str | None) -> str | None:
    """Preferred emitter declared by the investigation that owns this study.

    Reads ``investigations/<slug>/investigation.yaml`` → ``runtime.default_emitter``
    for whichever investigation lists ``study_name`` in its ``studies[]``.
    Sits in the emitter-precedence chain BETWEEN the per-study
    ``runtime.emitter`` override (higher) and the workspace default (lower),
    so an investigation can standardise its emitter once — e.g. the PDMP
    investigation declares ``xarray`` and every member study runs XArray
    without per-study config. Returns the emitter name or None.
    """
    if not study_name:
        return None
    inv_dir = ws_root / "investigations"
    if not inv_dir.is_dir():
        return None
    try:
        for invf in sorted(inv_dir.glob("*/investigation.yaml")):
            try:
                inv = yaml.safe_load(invf.read_text(encoding="utf-8")) or {}
            except Exception:
                continue
            studies = inv.get("studies") or []
            names = [s if isinstance(s, str) else (s or {}).get("study")
                     for s in studies]
            if study_name in names:
                rt = inv.get("runtime") or {}
                em = str((rt or {}).get("default_emitter") or "").strip().lower()
                return em or None
    except Exception:
        return None
    return None


def resolve_study_baseline_state(ws_root, pkg, spec_id, params):
    """Resolve a generator composite spec_id + params → a state dict.

    Returns (state, error_dict_or_None). Studies always reference generator
    composites (mirrors the generator branch of _post_composite_test_run).
    """
    import importlib
    import sys as _sys

    try:
        from pbg_superpowers.composite_generator import (
            _REGISTRY, build_generator, discover_generators,
        )
    except ImportError:
        return None, {"error": "pbg_superpowers not importable"}
    if not _REGISTRY:
        discover_generators()
    entry = _REGISTRY.get(spec_id)
    # Allow `local:<name>` shorthand: look up by entry.name when no exact id match.
    if entry is None and spec_id.startswith("local:"):
        short_name = spec_id[len("local:"):]
        entry = next((e for e in _REGISTRY.values() if e.name == short_name), None)
    if entry is None:
        # The registry may be stale (cleared by test teardown or a registry
        # reset). Force-reload the module that defines this composite so its
        # @composite_generator decorators re-fire, then retry.
        # spec_id is like "pkg.composites.name"; the defining module is
        # typically "pkg.composites" or "pkg.composites.name".
        candidate_mods = []
        if ".composites." in spec_id:
            # "pkg.composites.name" → try "pkg.composites.name", "pkg.composites", "pkg"
            parts = spec_id.split(".")
            for i in range(len(parts), 0, -1):
                candidate_mods.append(".".join(parts[:i]))
        for mod_name in candidate_mods:
            if mod_name in _sys.modules:
                try:
                    importlib.reload(_sys.modules[mod_name])
                except Exception:  # noqa: BLE001
                    pass
        if candidate_mods:
            discover_generators()
        entry = _REGISTRY.get(spec_id)
    if entry is None:
        # mem3dg-readdy friction #21: fall back to file-discovered composites
        # (the OTHER registry — pbg_superpowers.composite_discovery walks
        # *.composite.{yaml,json} on disk). A workspace that ships YAML
        # specs without @composite_generator decorators is still runnable
        # via this path, removing the "Composites tab lists it but Run
        # rejects it" foot-gun.
        try:
            from pbg_superpowers.composite_discovery import discover_composites
            specs = discover_composites()
        except Exception:  # noqa: BLE001
            specs = {}
        yaml_spec = specs.get(spec_id)
        # Allow the same `local:<name>` shorthand on the YAML side.
        if yaml_spec is None and spec_id.startswith("local:"):
            short_name = spec_id[len("local:"):]
            yaml_spec = next(
                (s for sid, s in specs.items() if sid.endswith("." + short_name)
                 or s.get("name") == short_name),
                None,
            )
        if yaml_spec is not None:
            state = yaml_spec.get("state") if isinstance(yaml_spec, dict) else None
            if isinstance(state, dict):
                # YAML composites don't support `params` overrides yet —
                # generators are the path for parametrized runs. Surface
                # this clearly rather than silently dropping the kwargs.
                if params:
                    return None, {"error": (
                        f"YAML composite {spec_id!r} resolved but `params:` "
                        "overrides aren't supported on file-discovered specs. "
                        "Promote to @composite_generator to use param overrides."
                    )}
                return state, None
            return None, {"error": (
                f"YAML composite {spec_id!r} has no `state:` block "
                "(check the spec shape)"
            )}
        return None, {"error": (
            f"composite {spec_id!r} not found in either the "
            "@composite_generator registry OR the file-discovery index "
            "(*.composite.{yaml,json}). Add an @composite_generator "
            "function or ship a composite YAML."
        )}
    # Pass only parameters the generator actually declares. Study baselines also
    # store run-time params (perturbations, single_daughters, max_generations, …)
    # that are NOT composite-build parameters; the strict build_generator rejects
    # unknown keys ("unknown parameter(s)"), which would surface as an error node
    # in the composite explorer. Filter to the declared set for this static view.
    declared = set((entry.parameters or {}).keys())
    params = {k: v for k, v in (params or {}).items() if k in declared}
    # Drop a cache_dir override whose ParCa cache is absent (e.g. a transient
    # per-run cache that no longer exists on disk) so the static structural view
    # falls back to the generator's default cache instead of FileNotFoundError.
    cdir = params.get("cache_dir")
    if cdir:
        from pathlib import Path as _P
        _root = ws_root if ws_root else _P(".")
        if not (_P(_root) / cdir / "initial_state.json").exists() \
           and not (_P(cdir) / "initial_state.json").exists():
            params.pop("cache_dir", None)
    try:
        doc = build_generator(entry, overrides=params)
    except Exception as e:  # noqa: BLE001
        return None, {"error": f"generator build failed: {e}"}
    if isinstance(doc, dict) and "state" in doc and isinstance(doc["state"], dict):
        return doc["state"], None
    return doc, None


def zarr_store_for_sim(study_db: Path, sim_name: str | None) -> Path | None:
    """Find the most-recent XArrayEmitter zarr store for a sim_name in a study.

    XArrayEmitter runs (via the subprocess template's xarray branch) write a
    per-run zarr directory next to the SQLite db at
    ``<study>/runs.<run_id>.zarr``. To map a sim_name → zarr path:

      1. Read runs_meta from the study's SQLite db to find the latest
         completed run_id for that sim_name (runs_meta is written for both
         SQLite-backed AND xarray-backed runs).
      2. Check whether the corresponding zarr dir exists on disk.

    Returns the zarr path if it exists, else None (caller falls back to SQLite).
    """
    if not sim_name or not study_db or not study_db.exists():
        return None
    try:
        import sqlite3
        conn = sqlite3.connect(str(study_db))
        try:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            if "runs_meta" not in tables:
                return None
            row = conn.execute(
                "SELECT run_id FROM runs_meta WHERE sim_name=? "
                "AND status='completed' ORDER BY started_at DESC LIMIT 1",
                (sim_name,),
            ).fetchone()
        finally:
            conn.close()
        if not row:
            return None
        run_id = row[0]
    except Exception:
        return None
    # Resolve the zarr path via the canonical run-store convention.
    zarr_dir = run_store.zarr_store_path_for_db(study_db, run_id)
    return zarr_dir if zarr_dir.is_dir() else None
