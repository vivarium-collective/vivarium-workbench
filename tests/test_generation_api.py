"""Tests for GET /api/generation — coordinated-generation provenance banner (A.3)."""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent
FIXTURE_WORKSPACE = _REPO_ROOT / "tests" / "_fixtures" / "ws_increase_demo"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return r.status, json.loads(r.read().decode())


@pytest.fixture
def server(tmp_path):
    if not FIXTURE_WORKSPACE.is_dir():
        pytest.skip(f"Fixture workspace not present at {FIXTURE_WORKSPACE}")
    pytest.importorskip("pbg_superpowers.generation")
    ws = tmp_path / "ws"
    shutil.copytree(FIXTURE_WORKSPACE, ws)
    pbg_home = tmp_path / "pbg-home"
    pbg_home.mkdir()
    port = _free_port()
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(_REPO_ROOT), str(ws), env.get("PYTHONPATH", "")])
    env["PBG_HOME"] = str(pbg_home)
    proc = subprocess.Popen(
        [sys.executable, "-m", "vivarium_dashboard.server",
         "--workspace", str(ws), "--port", str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
    )
    info_path = ws / ".pbg" / "server" / "server-info"
    for _ in range(40):
        if info_path.exists():
            break
        time.sleep(0.1)
    else:
        proc.terminate()
        out, err = proc.communicate(timeout=2)
        pytest.fail(f"server did not start:\n{out.decode()}\n{err.decode()}")
    yield {"url": f"http://127.0.0.1:{port}", "ws": ws}
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def test_generation_null_when_none_active(server):
    """No generation started → endpoint reports null, not an error."""
    status, body = _get(f"{server['url']}/api/generation")
    assert status == 200
    assert body == {"generation": None}


def test_generation_reports_current(server):
    """After start_generation, the endpoint surfaces the summary the report
    banner renders."""
    from pbg_superpowers import generation as gen
    g = gen.start_generation(
        server["ws"],
        git_sha_value="d146458",
        param_set={"translation_efficiency": 1},
        label="round-1 rerun",
    )
    status, body = _get(f"{server['url']}/api/generation")
    assert status == 200
    summary = body["generation"]
    assert summary["generation_id"] == g.generation_id
    assert summary["git_sha"] == "d146458"
    assert summary["param_set_hash"] == g.param_set_hash
    assert summary["label"] == "round-1 rerun"
    assert summary["n_runs"] == 0
