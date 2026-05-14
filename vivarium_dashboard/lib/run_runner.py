"""Detached composite-run executor.

``execute(request_path)`` is the entry point the ``vivarium-dashboard
run-composite`` CLI calls in a detached process. It is pure: no HTTP, no
module globals — everything it needs comes from the run-request file. State
is loaded from that file, never from argv, which structurally eliminates the
``OSError: [Errno 7] Argument list too long`` failure mode.
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path

from vivarium_dashboard.lib import composite_runs as cr

# A run exceeding this self-terminates with status='failed'. Matches the
# "tens of minutes" target from the design spec.
MAX_RUNTIME_SEC = 1800


@dataclass
class RunRequest:
    run_id: str
    spec_id: str
    pkg: str
    workspace: Path
    overrides: dict
    steps: int
    emit_paths: list
    db_file: str
    log_path: str

    @classmethod
    def from_file(cls, path: Path) -> "RunRequest":
        data = json.loads(Path(path).read_text())
        return cls(
            run_id=data["run_id"],
            spec_id=data["spec_id"],
            pkg=data["pkg"],
            workspace=Path(data["workspace"]),
            overrides=data.get("overrides") or {},
            steps=int(data["steps"]),
            emit_paths=data.get("emit_paths") or [],
            db_file=data["db_file"],
            log_path=data["log_path"],
        )


def _resolve_state(req: RunRequest) -> dict:
    """Resolve the composite state — generator entry first, then file spec.

    Mirrors the resolution the old _post_composite_test_run handler did.
    Raises a clear error if neither path yields a state.
    """
    # Generator-kind branch.
    try:
        from pbg_superpowers.composite_generator import (
            _REGISTRY, build_generator, discover_generators,
        )
        if not _REGISTRY:
            discover_generators()
        entry = _REGISTRY.get(req.spec_id)
        if entry is not None:
            doc = build_generator(entry, overrides=req.overrides)
            if isinstance(doc, dict) and isinstance(doc.get("state"), dict):
                return doc["state"]
            return doc
    except ImportError:
        pass

    # File-based spec branch.
    from vivarium_dashboard.lib.composite_lookup import (
        find_composite_path, substitute_parameters,
    )
    path = find_composite_path(req.workspace, req.pkg, req.spec_id)
    if path is None:
        raise FileNotFoundError(
            f"composite spec not found: {req.spec_id} "
            f"(not a registered generator, no spec file)"
        )
    text = path.read_text()
    spec = json.loads(text) if path.suffix.lower() == ".json" else __import__(
        "yaml").safe_load(text)
    return substitute_parameters(spec.get("state") or {},
                                 spec.get("parameters") or {},
                                 req.overrides)


def _render_viz(composite, run_dir: Path) -> None:
    """Render Visualization-step HTML to viz.json. Best-effort — never raises."""
    try:
        from pbg_superpowers.visualization import render_results
        rendered = render_results(composite)
        viz_html = {
            ".".join(str(p) for p in path_tuple): payload
            for path_tuple, payload in rendered.items()
        }
        (run_dir / "viz.json").write_text(json.dumps(viz_html, default=str))
    except Exception:
        traceback.print_exc()


def execute(request_path: Path) -> int:
    """Run one composite to completion. Returns 0 on success, 1 on failure.

    All progress and results are written to the shared SQLite DB; stdout/stderr
    (captured by the spawning process into run.log) carries diagnostics.
    """
    request_path = Path(request_path)
    req = RunRequest.from_file(request_path)
    run_dir = request_path.parent

    if str(req.workspace) not in sys.path:
        sys.path.insert(0, str(req.workspace))

    conn = cr.connect(req.db_file)
    try:
        try:
            state = _resolve_state(req)
        except FileNotFoundError as e:
            # Most common: the ParCa cache (out/cache/initial_state.json) is
            # missing. Fail fast with a legible message rather than a crash.
            msg = f"composite build failed: {e}"
            print(msg, flush=True)
            _write_log(req, msg)
            cr.complete_metadata(conn, run_id=req.run_id, n_steps=0,
                                 status="failed")
            return 1

        if req.emit_paths:
            state = cr.inject_emitter_for_paths(state, req.emit_paths)
        state = cr.inject_sqlite_emitter(state, run_id=req.run_id,
                                         db_file=req.db_file)

        # build_core lives in the workspace's own package (e.g.
        # pbg_ws_increase_demo.core). Import it dynamically by package name.
        core_mod = __import__(f"{req.pkg}.core", fromlist=["build_core"])
        from process_bigraph import Composite
        from process_bigraph.emitter import SQLiteEmitter

        core = core_mod.build_core()
        core.register_link("SQLiteEmitter", SQLiteEmitter)
        composite = Composite({"state": state}, core=core)

        started = time.monotonic()
        for step in range(1, req.steps + 1):
            composite.run(1)
            cr.update_progress(conn, run_id=req.run_id, progress_step=step,
                               heartbeat_at=time.time())
            if time.monotonic() - started > MAX_RUNTIME_SEC:
                msg = (f"run exceeded max runtime ({MAX_RUNTIME_SEC}s) — "
                       f"terminating at step {step}")
                print(msg, flush=True)
                _write_log(req, msg)
                cr.complete_metadata(conn, run_id=req.run_id, n_steps=step,
                                     status="failed")
                return 1

        _render_viz(composite, run_dir)
        cr.complete_metadata(conn, run_id=req.run_id, n_steps=req.steps,
                             status="completed")
        print(f"run {req.run_id} completed: {req.steps} steps", flush=True)
        return 0
    except Exception:
        tb = traceback.format_exc()
        print(tb, flush=True)
        _write_log(req, tb)
        cr.complete_metadata(conn, run_id=req.run_id, n_steps=0, status="failed")
        return 1
    finally:
        conn.close()


def _write_log(req: RunRequest, text: str) -> None:
    """Append diagnostic text to the run's log file. Best-effort.

    The spawning process normally redirects stdout/stderr into run.log, but
    execute() also writes failure diagnostics here directly so the log is
    populated even when called in-process (e.g. by tests).
    """
    try:
        log_full = req.workspace / req.log_path
        log_full.parent.mkdir(parents=True, exist_ok=True)
        with open(log_full, "a") as fh:
            fh.write(text + "\n")
    except Exception:
        pass
