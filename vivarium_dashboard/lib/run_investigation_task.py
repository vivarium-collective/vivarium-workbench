"""Standalone runner for a single investigation array-task on HPC.

This module is invoked from within a SLURM array-task inside the
Singularity container as::

    python3 -m vivarium_dashboard.lib.run_investigation_task <base64_params>

Two payload shapes are supported:

* v2 (legacy ``simulations:`` block): the dashboard pre-resolves the
  composite YAML into a ``state_json`` blob with the emitter already
  injected.  The runner just rebuilds the Composite from that state.

* v3 (``simulation_set:`` block): the dashboard sends ``base_model``
  (importable dotted path, e.g. ``v2ecoli.composites.colony.colony``)
  and per-task ``overrides``.  The runner imports the module, calls the
  builder with the overrides, injects a SQLiteEmitter post-build, and
  runs.

Both paths print ``@@@OK@@@`` on success or ``@@@ERROR@@@ ...`` to
stderr and exit non-zero on failure.
"""
from __future__ import annotations

import base64
import json
import sys


def _inject_sqlite_emitter(state: dict, run_id: str, db_file: str = "/workspace/runs.db") -> dict:
    """Add a SQLiteEmitter step at the root of ``state`` (v3 path).

    Mirrors :func:`vivarium_dashboard.lib.composite_runs.inject_sqlite_emitter`
    without importing from the dashboard (this module ships inside the
    container image and may not have the rest of the package available).
    """
    if not isinstance(state, dict):
        return state
    if "emitter" in state and isinstance(state["emitter"], dict):
        cfg = dict(state["emitter"].get("config") or {})
        cfg.setdefault("run_id", run_id)
        cfg.setdefault("db_file", db_file)
        state["emitter"]["config"] = cfg
        return state
    state["emitter"] = {
        "_type": "step",
        "address": "local:SQLiteEmitter",
        "config": {"run_id": run_id, "db_file": db_file},
        "inputs": {},
    }
    return state


def main() -> None:
    if len(sys.argv) < 2:
        print("@@@ERROR@@@ missing params argument", file=sys.stderr)
        sys.exit(1)

    raw = sys.argv[1]
    try:
        params = json.loads(base64.b64decode(raw).decode("utf-8"))
    except Exception as exc:
        print(f"@@@ERROR@@@ failed to decode params: {exc}", file=sys.stderr)
        sys.exit(1)

    run_id = params.get("run_id", "unknown")
    steps = int(params.get("steps", 1))
    pkg = params.get("pkg", "")
    base_model = params.get("base_model")

    try:
        import importlib
        from process_bigraph import Composite
        from process_bigraph.emitter import SQLiteEmitter

        if base_model:
            # v3 path: import + call the builder, inject emitter, run.
            module_path, fn_name = base_model.rsplit(".", 1)
            mod = importlib.import_module(module_path)
            builder = getattr(mod, fn_name)
            overrides = params.get("overrides") or {}
            doc = builder(**overrides)
            if not isinstance(doc, dict) or "state" not in doc:
                raise RuntimeError(
                    f"base_model {base_model!r} returned {type(doc).__name__}, "
                    "expected dict with 'state' key"
                )
            state = doc["state"]
            _inject_sqlite_emitter(state, run_id=run_id)
            # The builder bootstrapped a core internally but didn't return it.
            # Reconstruct one with the same registrations so the Composite can
            # resolve types like `pymunk_agent`.  We try the v2ecoli + viva_munk
            # combo (the only known case today) and fall back to the workspace
            # package's `core.build_core()` for non-pymunk composites.
            core = None
            try:
                from viva_munk import core_import as _vm_core_import
                from v2ecoli.bridge import EcoliWCM
                from v2ecoli.types import ECOLI_TYPES
                core = _vm_core_import()
                core.register_types(ECOLI_TYPES)
                core.register_link("EcoliWCM", EcoliWCM)
            except ImportError:
                if pkg:
                    try:
                        core_mod = importlib.import_module(f"{pkg}.core")
                        build_core = getattr(core_mod, "build_core", None)
                        if build_core:
                            core = build_core()
                    except Exception:
                        core = None
            if core is not None:
                core.register_link("SQLiteEmitter", SQLiteEmitter)
                composite = Composite({"state": state}, core=core)
            else:
                composite = Composite({"state": state})
            composite.run(steps)
            print("@@@OK@@@")
            return

        # v2 path (legacy): pre-resolved state_json.
        state_json = params.get("state_json", "{}")
        state = json.loads(state_json)

        if pkg:
            mod = importlib.import_module(f"{pkg}.core")
            build_core = getattr(mod, "build_core")
            core = build_core()
        else:
            from process_bigraph.core import core as _core
            core = _core

        core.register_link("SQLiteEmitter", SQLiteEmitter)
        composite = Composite({"state": state}, core=core)
        composite.run(steps)
        print("@@@OK@@@")
    except Exception as exc:
        import traceback
        print(f"@@@ERROR@@@ run_id={run_id} {exc}", file=sys.stderr)
        print(traceback.format_exc(), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
