"""Live endpoints: POST /api/study-reproduce + POST /api/investigation-rerun.

Task 6 — thin HTTP wrappers over lib.rerun.run_rerun / rerun_investigation
(Tasks 1-5). (The generic ``/api/run-rerun`` was folded into the study-scoped
``/api/study-reproduce``, which delegates to the same ``lib.rerun.run_rerun``.)
Exercises the routes against a throwaway copy of the
ws_increase_demo fixture: an unknown run_id 404s (or returns an error body),
an investigation with no member studies returns a 200 empty-batch result,
and both mutating POSTs are guarded by the app-wide CSRF/origin middleware
(a present cross-origin Origin header is rejected before dispatch).
"""
from __future__ import annotations

import shutil
import urllib.error
import urllib.request
from pathlib import Path

import pytest

_FIXTURE = Path(__file__).parent / "_fixtures" / "ws_increase_demo"


@pytest.fixture
def ws_copy(tmp_path):
    ws = tmp_path / "ws"
    shutil.copytree(_FIXTURE, ws)
    return ws


def _post_with_origin(base_url: str, path: str, body: dict, *, origin: str) -> int:
    """POST with an explicit cross-origin ``Origin`` header against a real bound
    socket (the in-process client has no reachable base_url, and this must
    exercise the real CSRF/Origin guard — see test_csrf_origin_guard.py). The two
    cross-origin tests below therefore keep the subprocess ``dashboard_client``."""
    import json as _json

    req = urllib.request.Request(
        base_url + path,
        data=_json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Origin": origin},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def test_study_reproduce_unknown_run_404s(inproc_client, ws_copy):
    client = inproc_client(workspace=ws_copy)
    r = client.post("/api/study-reproduce", json={"study": "s", "run_id": "does-not-exist"})
    assert r.status_code == 404 or "error" in r.json()


def test_investigation_rerun_empty_investigation_200s(inproc_client, ws_copy):
    client = inproc_client(workspace=ws_copy)
    r = client.post("/api/investigation-rerun", json={"investigation": "nonexistent"})
    assert r.status_code == 200
    body = r.json()
    assert body["launched"] == []
    assert body["errors"] == []
    assert body["count"] == 0


def test_study_reproduce_cross_origin_is_rejected(dashboard_client, ws_copy):
    client = dashboard_client(workspace=ws_copy)
    code = _post_with_origin(
        client.base_url, "/api/study-reproduce", {"study": "s", "run_id": "x"},
        origin="http://evil.example.com")
    assert code in (400, 403)


def test_investigation_rerun_cross_origin_is_rejected(dashboard_client, ws_copy):
    client = dashboard_client(workspace=ws_copy)
    code = _post_with_origin(
        client.base_url, "/api/investigation-rerun", {"investigation": "inv1"},
        origin="http://evil.example.com")
    assert code in (400, 403)
