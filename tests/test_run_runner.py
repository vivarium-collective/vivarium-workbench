"""Unit tests for vivarium_dashboard.lib.run_runner."""
import json
import sys
from pathlib import Path

import pytest

from vivarium_dashboard.lib.run_runner import execute
from vivarium_dashboard.lib.composite_runs import connect, query_run_meta, query_run

_REPO_ROOT = Path(__file__).parent.parent
FIXTURE_WS = _REPO_ROOT / "tests" / "_fixtures" / "ws_increase_demo"


def _write_request(tmp_path, *, steps=3, spec_id=None, overrides=None):
    """Copy the fixture workspace to tmp and write a run-request file."""
    import shutil
    ws = tmp_path / "ws"
    shutil.copytree(FIXTURE_WS, ws)
    if str(ws) not in sys.path:
        sys.path.insert(0, str(ws))
    run_id = "test-run-1"
    run_dir = ws / ".pbg" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    request = {
        "run_id": run_id,
        "spec_id": spec_id or "pbg_ws_increase_demo.composites.increase-demo",
        "pkg": "pbg_ws_increase_demo",
        "workspace": str(ws),
        "overrides": overrides or {},
        "steps": steps,
        "emit_paths": [],
        "db_file": str(ws / ".pbg" / "composite-runs.db"),
        "log_path": f".pbg/runs/{run_id}/run.log",
    }
    request_path = run_dir / "request.json"
    request_path.write_text(json.dumps(request))
    # Seed the runs_meta row the way the POST handler would.
    conn = connect(request["db_file"])
    from vivarium_dashboard.lib.composite_runs import save_metadata
    save_metadata(conn, spec_id=request["spec_id"], run_id=run_id, params={},
                  label="", started_at=0.0, n_steps=steps,
                  log_path=request["log_path"])
    conn.close()
    return ws, request_path, run_id


@pytest.mark.skipif(not FIXTURE_WS.is_dir(), reason="fixture workspace absent")
def test_execute_completes_and_persists_trajectory(tmp_path):
    ws, request_path, run_id = _write_request(tmp_path, steps=3)
    rc = execute(request_path)
    assert rc == 0
    conn = connect(ws / ".pbg" / "composite-runs.db")
    meta = query_run_meta(conn, run_id=run_id)
    assert meta["status"] == "completed"
    assert meta["progress_step"] == 3
    trajectory = query_run(conn, run_id=run_id)
    assert len(trajectory) >= 1
    conn.close()


@pytest.mark.skipif(not FIXTURE_WS.is_dir(), reason="fixture workspace absent")
def test_execute_marks_failed_on_bad_spec(tmp_path):
    ws, request_path, run_id = _write_request(
        tmp_path, steps=2, spec_id="pbg_ws_increase_demo.composites.does-not-exist")
    rc = execute(request_path)
    assert rc == 1
    conn = connect(ws / ".pbg" / "composite-runs.db")
    meta = query_run_meta(conn, run_id=run_id)
    assert meta["status"] == "failed"
    conn.close()
    # Traceback / error landed in the log.
    log = ws / meta["log_path"]
    assert log.is_file() and log.stat().st_size > 0
