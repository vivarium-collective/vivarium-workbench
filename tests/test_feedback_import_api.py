"""Tests for POST /api/feedback-import — direct feedback submit (B.2)."""
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
import yaml

_REPO_ROOT = Path(__file__).parent.parent
FIXTURE_WORKSPACE = _REPO_ROOT / "tests" / "_fixtures" / "ws_increase_demo"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _post(url, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data,
                                headers={"Content-Type": "application/json"},
                                method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


@pytest.fixture
def server(tmp_path):
    if not FIXTURE_WORKSPACE.is_dir():
        pytest.skip(f"Fixture workspace not present at {FIXTURE_WORKSPACE}")
    fb_mod = pytest.importorskip("pbg_superpowers.feedback_import")
    if not hasattr(fb_mod, "write_feedback_payload"):
        pytest.skip("needs pbg-superpowers #55 (write_feedback_payload)")
    ws = tmp_path / "ws"
    shutil.copytree(FIXTURE_WORKSPACE, ws)
    (ws / "investigations" / "dnaa").mkdir(parents=True)
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


def _payload(inv="dnaa"):
    return {
        "meta": {"investigation": inv, "report_id": "rpt-1",
                 "generated_at": "2026-05-21T02:32:39Z"},
        "annotations": {
            "study-dnaa-00-x": [
                {"author": "Haochen", "text": "longer time", "ts": "2026-05-21T00:03Z"},
            ],
        },
    }


def test_feedback_import_writes_file(server):
    status, body = _post(f"{server['url']}/api/feedback-import", _payload())
    assert status == 200, body
    assert body["ok"] is True
    assert body["n_entries"] == 1
    written = server["ws"] / body["path"]
    assert written.is_file()
    data = yaml.safe_load(written.read_text())
    assert data["annotations"]["study-dnaa-00-x"][0]["text"] == "longer time"


def test_feedback_import_bad_payload_400(server):
    status, body = _post(f"{server['url']}/api/feedback-import", {"annotations": {}})
    assert status == 400
    assert "meta.investigation" in body["error"]


def test_feedback_import_unknown_investigation_400(server):
    status, body = _post(f"{server['url']}/api/feedback-import", _payload(inv="nope"))
    assert status == 400
    assert "does not exist" in body["error"]
