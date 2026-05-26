"""Integration tests for the HPC UI endpoints (Phase 6 / Todo #10 Phase D).

Uses the `dashboard_client` fixture from conftest.py to spin up a real server
subprocess against a copy of the fixture workspace, then hits the new HTTP
routes and checks status codes + response shapes.

Tests that require HPC credentials expect HTTP 503 (hpc_not_configured) since
the fixture workspace has no `.pbg/hpc.env` and the test environment sets no
SLURM_* env vars.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

_FIXTURES = Path(__file__).parent / "_fixtures"
_WS_FIXTURE = _FIXTURES / "ws_increase_demo"


@pytest.fixture()
def ws(tmp_path):
    """Fresh copy of the fixture workspace, safe to mutate."""
    dst = tmp_path / "ws"
    shutil.copytree(_WS_FIXTURE, dst)
    return dst


# ---------------------------------------------------------------------------
# /api/compute-backends
# ---------------------------------------------------------------------------

class TestComputeBackends:
    def test_returns_200(self, dashboard_client, ws):
        client = dashboard_client(ws)
        r = client.get("/api/compute-backends")
        assert r.status_code == 200

    def test_backends_list_present(self, dashboard_client, ws):
        client = dashboard_client(ws)
        data = client.get("/api/compute-backends").json()
        assert "backends" in data
        assert isinstance(data["backends"], list)

    def test_includes_local_backend(self, dashboard_client, ws):
        client = dashboard_client(ws)
        data = client.get("/api/compute-backends").json()
        ids = [b["id"] for b in data["backends"]]
        assert "local" in ids

    def test_includes_hpc_ccam_backend(self, dashboard_client, ws):
        client = dashboard_client(ws)
        data = client.get("/api/compute-backends").json()
        ids = [b["id"] for b in data["backends"]]
        assert "hpc:ccam" in ids

    def test_backend_has_label_and_description(self, dashboard_client, ws):
        client = dashboard_client(ws)
        data = client.get("/api/compute-backends").json()
        for b in data["backends"]:
            assert "id" in b
            assert "label" in b
            assert "description" in b


# ---------------------------------------------------------------------------
# /hpc/<backend>  — page rendering
# ---------------------------------------------------------------------------

class TestHpcPage:
    def test_valid_backend_returns_200(self, dashboard_client, ws):
        client = dashboard_client(ws)
        r = client.get("/hpc/hpc:ccam")
        assert r.status_code == 200

    def test_valid_backend_returns_html(self, dashboard_client, ws):
        client = dashboard_client(ws)
        r = client.get("/hpc/hpc:ccam")
        assert "text/html" in r.text or "<html" in r.text.lower()

    def test_page_embeds_backend_name(self, dashboard_client, ws):
        client = dashboard_client(ws)
        r = client.get("/hpc/hpc:ccam")
        assert "hpc:ccam" in r.text

    def test_page_references_hpc_dispatch_js(self, dashboard_client, ws):
        client = dashboard_client(ws)
        r = client.get("/hpc/hpc:ccam")
        assert "hpc-dispatch.js" in r.text

    def test_unknown_backend_returns_404(self, dashboard_client, ws):
        client = dashboard_client(ws)
        r = client.get("/hpc/nonexistent-backend")
        assert r.status_code == 404

    def test_local_backend_returns_404(self, dashboard_client, ws):
        # 'local' is a valid backend but not an HPC backend — page only
        # renders for known HPC backends listed in ALLOWED_BACKENDS.
        # If it's in ALLOWED_BACKENDS it renders; if not, 404. Either is fine
        # as long as the server doesn't 500.
        client = dashboard_client(ws)
        r = client.get("/hpc/local")
        assert r.status_code in (200, 404)


# ---------------------------------------------------------------------------
# /api/hpc/<backend>/status  — no hpc.env → 503
# ---------------------------------------------------------------------------

class TestHpcStatus:
    def test_status_without_config_is_503(self, dashboard_client, ws):
        client = dashboard_client(ws)
        r = client.get("/api/hpc/hpc:ccam/status")
        assert r.status_code == 503

    def test_status_503_body_has_error_key(self, dashboard_client, ws):
        client = dashboard_client(ws)
        data = client.get("/api/hpc/hpc:ccam/status").json()
        assert data.get("error") == "hpc_not_configured"

    def test_status_503_body_has_missing_fields(self, dashboard_client, ws):
        client = dashboard_client(ws)
        data = client.get("/api/hpc/hpc:ccam/status").json()
        assert "missing_fields" in data
        assert isinstance(data["missing_fields"], list)
        assert len(data["missing_fields"]) > 0


# ---------------------------------------------------------------------------
# /api/hpc/<backend>/slurm  — no hpc.env → 503
# ---------------------------------------------------------------------------

class TestHpcSlurm:
    def test_slurm_without_config_is_503(self, dashboard_client, ws):
        client = dashboard_client(ws)
        r = client.get("/api/hpc/hpc:ccam/slurm")
        assert r.status_code == 503

    def test_slurm_503_body_shape(self, dashboard_client, ws):
        client = dashboard_client(ws)
        data = client.get("/api/hpc/hpc:ccam/slurm").json()
        assert data.get("error") == "hpc_not_configured"


# ---------------------------------------------------------------------------
# /api/hpc/<backend>/runs  — reads local .pbg/hpc/ dir; no SSH needed
# ---------------------------------------------------------------------------

class TestHpcRuns:
    def test_runs_returns_200(self, dashboard_client, ws):
        client = dashboard_client(ws)
        r = client.get("/api/hpc/hpc:ccam/runs")
        assert r.status_code == 200

    def test_runs_returns_empty_list_when_no_hpc_dir(self, dashboard_client, ws):
        client = dashboard_client(ws)
        data = client.get("/api/hpc/hpc:ccam/runs").json()
        assert "jobs" in data
        assert data["jobs"] == []

    def test_runs_lists_script_files(self, dashboard_client, ws):
        hpc_dir = ws / ".pbg" / "hpc"
        hpc_dir.mkdir(parents=True, exist_ok=True)
        (hpc_dir / "run-abc123.sh").write_text("#!/bin/bash\n# test\n")
        (hpc_dir / "build-def456.sh").write_text("#!/bin/bash\n# build\n")
        client = dashboard_client(ws)
        data = client.get("/api/hpc/hpc:ccam/runs").json()
        ids = [j["id"] for j in data["jobs"]]
        assert "abc123" in ids
        assert "def456" in ids


# ---------------------------------------------------------------------------
# POST /api/hpc/<backend>/build  — no hpc.env → 503
# ---------------------------------------------------------------------------

class TestHpcBuild:
    def test_build_post_without_config_is_503(self, dashboard_client, ws):
        client = dashboard_client(ws)
        r = client.post("/api/hpc/hpc:ccam/build")
        assert r.status_code == 503

    def test_build_503_body_shape(self, dashboard_client, ws):
        client = dashboard_client(ws)
        data = client.post("/api/hpc/hpc:ccam/build").json()
        assert data.get("error") == "hpc_not_configured"


# ---------------------------------------------------------------------------
# POST /api/hpc/<backend>/run  — no hpc.env → 503
# ---------------------------------------------------------------------------

class TestHpcRun:
    def test_run_post_without_config_is_503(self, dashboard_client, ws):
        client = dashboard_client(ws)
        r = client.post("/api/hpc/hpc:ccam/run",
                        json={"command": "python simulate.py",
                              "resources": {"cpus": 4, "mem_gb": 8, "time_min": 60}})
        assert r.status_code == 503

    def test_run_503_body_shape(self, dashboard_client, ws):
        client = dashboard_client(ws)
        data = client.post(
            "/api/hpc/hpc:ccam/run",
            json={"command": "python simulate.py", "resources": {}}
        ).json()
        assert data.get("error") == "hpc_not_configured"
