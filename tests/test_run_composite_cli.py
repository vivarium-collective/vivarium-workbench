"""Test the `vivarium-dashboard run-composite` CLI subcommand."""
import json
import shutil
import sys
from pathlib import Path

import pytest

from vivarium_dashboard.cli import main
from vivarium_dashboard.lib.composite_runs import (
    connect, save_metadata, query_run_meta,
)

_REPO_ROOT = Path(__file__).parent.parent
FIXTURE_WS = _REPO_ROOT / "tests" / "_fixtures" / "ws_increase_demo"


@pytest.mark.skipif(not FIXTURE_WS.is_dir(), reason="fixture workspace absent")
def test_run_composite_subcommand_executes_request(tmp_path):
    ws = tmp_path / "ws"
    shutil.copytree(FIXTURE_WS, ws)
    if str(ws) not in sys.path:
        sys.path.insert(0, str(ws))
    run_id = "cli-run-1"
    run_dir = ws / ".pbg" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    db_file = str(ws / ".pbg" / "composite-runs.db")
    request = {
        "run_id": run_id,
        "spec_id": "pbg_ws_increase_demo.composites.increase-demo",
        "pkg": "pbg_ws_increase_demo",
        "workspace": str(ws),
        "overrides": {},
        "steps": 2,
        "emit_paths": [],
        "db_file": db_file,
        "log_path": f".pbg/runs/{run_id}/run.log",
    }
    request_path = run_dir / "request.json"
    request_path.write_text(json.dumps(request))
    conn = connect(db_file)
    save_metadata(conn, spec_id=request["spec_id"], run_id=run_id, params={},
                  label="", started_at=0.0, n_steps=2,
                  log_path=request["log_path"])
    conn.close()

    rc = main(["run-composite", "--request", str(request_path)])
    assert rc == 0
    conn = connect(db_file)
    assert query_run_meta(conn, run_id=run_id)["status"] == "completed"
    conn.close()
