"""Regression tests for issue #1573.

Two defects on the Composite Explorer / loom "Run" path, both exercised here
WITHOUT mocking the interpreter resolver (the issue is explicit about that):

1. ``run_registry.spawn_detached`` launched the detached runner with the
   *server's* interpreter. When the workspace lives in its own venv (the
   published image's layout) whose package the server venv lacks, a generator
   composite never registers, so the run dies ~7 s later with
   ``composite build failed: composite spec not found``. The fix routes the spawn
   through ``env_resolver.resolve_run_interpreter`` (the same seam the study-run
   path already uses). ``test_run_completes_with_workspace_in_own_venv`` builds a
   real workspace + its own ``.venv`` whose interpreter can import a module the
   server interpreter cannot, runs a real detached subprocess, and asserts the
   run COMPLETES.

2. ``GET /api/composite-run/<id>/status`` returned ``error`` = the first line of
   the log tail — an early ``UserWarning`` — instead of the real failure. The fix
   anchors the excerpt at the ``composite build failed`` / final-traceback line.
   ``test_status_error_is_the_failure_not_the_warning`` asserts that.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from vivarium_workbench.lib import composite_runs as cr
from vivarium_workbench.lib import composite_test_run_views as ctr
from vivarium_workbench.lib import env_resolver
from vivarium_workbench.lib.composite_run_views import build_composite_run_status


# ---------------------------------------------------------------------------
# Defect 1 — detached run must use the WORKSPACE interpreter (real subprocess)
# ---------------------------------------------------------------------------

_MARKER = "venvtest_marker_1573"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


def _build_own_venv_workspace(tmp_path: Path) -> tuple[Path, Path]:
    """A workspace whose own ``.venv`` interpreter can import a module the server
    interpreter cannot, returning ``(ws_root, venv_python)``.

    The workspace's package imports ``venvtest_marker_1573`` at import time, so the
    generator it declares registers ONLY under an interpreter that can import that
    module. The server's ``sys.executable`` cannot (the module lives off its
    path); a thin ``.venv/bin/python`` wrapper around the server interpreter can,
    because it prepends a private dir — the stand-in for "the workspace venv has a
    package the server venv lacks" that keeps the subprocess real (and
    workbench-importable, so the resolver's capability probe passes) without
    provisioning a minutes-long full venv.
    """
    ws = tmp_path / "ws"
    pkg = "pbg_venvtest"

    _write(ws / "workspace.yaml", f"""
        schema_version: 2
        name: venvtest
        package_path: {pkg}
        runtime:
          default_emitter: sqlite
        phases: []
        observables: []
        visualizations: []
        simulations: []
        datasets: []
    """)
    (ws / ".pbg").mkdir(parents=True, exist_ok=True)

    # Importing the package pulls in the venv-only marker, so the generator only
    # registers where that marker is importable.
    _write(ws / pkg / "__init__.py", "from . import composites  # noqa: F401\n")
    _write(ws / pkg / "processes.py", """
        from process_bigraph import Process


        class IncreaseProcess(Process):
            config_schema = {'rate': {'_type': 'float', '_default': 1.0}}

            def inputs(self):
                return {'level': 'float'}

            def outputs(self):
                return {'level': 'float'}

            def update(self, state, interval=1.0):
                rate = (self.config or {}).get('rate', 1.0)
                return {'level': state.get('level', 0.0) * rate}
    """)
    _write(ws / pkg / "core.py", f"""
        from process_bigraph import allocate_core
        from process_bigraph.emitter import RAMEmitter
        from {pkg}.processes import IncreaseProcess


        def build_core():
            core = allocate_core()
            core.register_link('IncreaseProcess', IncreaseProcess)
            core.register_link('RAMEmitter', RAMEmitter)
            return core
    """)
    _write(ws / pkg / "composites" / "__init__.py", f"""
        import {_MARKER}  # noqa: F401 — present only in the workspace venv (#1573)
        from process_bigraph.composite_generator import composite_generator


        @composite_generator(name="venv_growth", description="",
                             parameters={{"rate": {{"default": 2.0}}}})
        def venv_growth(core=None, rate=2.0):
            return {{"state": {{
                "increase": {{
                    "_type": "process",
                    "address": "local:IncreaseProcess",
                    "config": {{"rate": rate}},
                    "inputs": {{"level": ["stores", "level"]}},
                    "outputs": {{"level": ["stores", "level"]}},
                    "interval": 1.0,
                }},
                "stores": {{"level": 1.0}},
            }}}}
    """)

    # The workspace's "own venv": a wrapper interpreter that is the server
    # interpreter PLUS a private dir carrying the marker module.
    venv_bin = ws / ".venv" / "bin"
    venv_bin.mkdir(parents=True, exist_ok=True)
    extra_site = ws / ".venv" / "extra-site"
    _write(extra_site / f"{_MARKER}.py", "OK = True\n")

    venv_python = venv_bin / "python"
    venv_python.write_text(
        "#!/bin/sh\n"
        f'PYTHONPATH="{extra_site}${{PYTHONPATH:+:$PYTHONPATH}}"\n'
        "export PYTHONPATH\n"
        f'exec "{sys.executable}" "$@"\n',
        encoding="utf-8",
    )
    venv_python.chmod(0o755)
    return ws, venv_python


def _poll_until_terminal(db_file: Path, run_id: str, timeout: float = 120.0) -> str:
    terminal = {"completed", "failed", "orphaned", "cancelled"}
    deadline = time.monotonic() + timeout
    status = "running"
    while time.monotonic() < deadline:
        conn = cr.connect(db_file)
        try:
            meta = cr.query_run_meta(conn, run_id=run_id)
        finally:
            conn.close()
        if meta is not None:
            status = meta["status"]
            if status in terminal:
                return status
        time.sleep(0.5)
    return status


def test_run_completes_with_workspace_in_own_venv(tmp_path):
    ws, venv_python = _build_own_venv_workspace(tmp_path)

    # Preconditions: the marker is ABSENT from the server interpreter but present
    # under the workspace venv, and the resolver genuinely picks the workspace
    # venv (no mock). If these do not hold, the test proves nothing — fail loud.
    assert subprocess.run(
        [sys.executable, "-c", f"import {_MARKER}"],
        capture_output=True,
    ).returncode != 0, "marker leaked into the server interpreter"
    assert subprocess.run(
        [str(venv_python), "-c", f"import {_MARKER}"],
        capture_output=True,
    ).returncode == 0, "workspace venv cannot import the marker"
    assert env_resolver.resolve_run_interpreter(ws) == str(venv_python), (
        "resolver did not select the workspace venv interpreter"
    )

    body, status = ctr.composite_test_run(
        ws, {"id": "pbg_venvtest.composites.venv_growth", "overrides": {}, "steps": 3}
    )
    assert status == 202, f"submit failed: {status} {body}"
    run_id = body["run_id"]

    db_file = ws / ".pbg" / "composite-runs.db"
    final = _poll_until_terminal(db_file, run_id)

    if final != "completed":
        log = (ws / ".pbg" / "runs" / run_id / "run.log")
        detail = log.read_text(encoding="utf-8") if log.is_file() else "(no run.log)"
        pytest.fail(
            f"run ended '{final}', expected 'completed'. The detached runner used "
            f"the wrong interpreter (server venv lacks the workspace package). "
            f"run.log:\n{detail[-3000:]}"
        )


# ---------------------------------------------------------------------------
# Defect 2 — status error is the failure, not the leading UserWarning
# ---------------------------------------------------------------------------

def _seed_failed_run(ws: Path, run_id: str, log_rel: str, log_text: str) -> None:
    (ws / log_rel).parent.mkdir(parents=True, exist_ok=True)
    (ws / log_rel).write_text(log_text, encoding="utf-8")
    (ws / ".pbg").mkdir(parents=True, exist_ok=True)
    db = ws / ".pbg" / "composite-runs.db"
    conn = cr.connect(db)
    try:
        cr.save_metadata(conn, spec_id="pkg.composites.x", run_id=run_id,
                         params={}, label="", started_at=1.0, n_steps=3,
                         log_path=log_rel)
        cr.complete_metadata(conn, run_id=run_id, n_steps=0, status="failed")
    finally:
        conn.close()


def test_status_error_is_the_failure_not_the_warning(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    # A real failing-run log: the FIRST line is the UserWarning the user was
    # shown as the "error"; the real failure is the last line.
    log_text = (
        "/x/process_bigraph/composite_generator.py:88: UserWarning: no generators "
        "discovered for this distribution; falling back to spec files\n"
        "  warnings.warn(msg)\n"
        "composite build failed: composite spec not found: "
        "viva_munk.composites.glucose_growth "
        "(not a registered generator, no spec file)\n"
    )
    _seed_failed_run(ws, "r1", ".pbg/runs/r1/run.log", log_text)

    body, status = build_composite_run_status(ws, "r1")
    assert status == 200
    assert body["status"] == "failed"
    err = body["error"]
    assert "composite build failed: composite spec not found" in err
    assert "UserWarning" not in err, f"error led with the warning: {err!r}"
    # The user-facing first line must be the real failure.
    assert err.splitlines()[0].startswith("composite build failed")
