"""Standalone runner for a single investigation array-task on HPC.

This module is invoked from within a SLURM array-task inside the
Singularity container as::

    python3 -m vivarium_dashboard.lib.run_investigation_task <base64_params>

The base64-decoded JSON blob contains:

    run_id      — unique run identifier
    state_json  — pre-resolved composite state (emitter already injected)
    steps       — number of simulation ticks
    pkg         — workspace package name (e.g. ``pbg_colonies``)

The runner builds the core from the workspace package, registers the
SQLiteEmitter, instantiates the Composite, runs for the requested number
of steps, and prints ``@@@OK@@@`` on success.
"""
from __future__ import annotations

import base64
import json
import sys


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
    state_json = params.get("state_json", "{}")
    steps = int(params.get("steps", 1))
    pkg = params.get("pkg", "")

    try:
        state = json.loads(state_json)

        if pkg:
            import importlib
            mod = importlib.import_module(f"{pkg}.core")
            build_core = getattr(mod, "build_core")
            core = build_core()
        else:
            from process_bigraph.core import core as _core
            core = _core

        from process_bigraph import Composite
        from process_bigraph.emitter import SQLiteEmitter

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
