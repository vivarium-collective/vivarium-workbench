"""Remote sms-api runs surfaced in the Simulations DB.

The listing must work for a plain LOCAL checkout (scoped to the workspace's
repo), not only a materialized remote build, and tag each row's Origin with the
deployment name + an S3 Location. sms-api is stubbed — no network.
"""
from __future__ import annotations

import pytest

import vivarium_workbench.lib.remote_simulations as rs


# ---- fake sms-api ---------------------------------------------------------

_BUILDS = [
    {"database_id": 70, "git_repo_url": "https://github.com/CovertLabEcoli/sms-ecoli", "git_commit_hash": "aaa1111"},
    {"database_id": 71, "git_repo_url": "https://github.com/CovertLabEcoli/sms-ecoli", "git_commit_hash": "bbb2222"},
    {"database_id": 10, "git_repo_url": "https://github.com/vivarium-collective/vEcoli", "git_commit_hash": "ccc3333"},
]

# The list endpoint ignores its simulator_id filter and returns every sim.
_SIMS = [
    {"simulator_id": 70, "database_id": 501, "experiment_id": "exp-sms-a",
     "status": "completed", "created_at": "2026-09-01 12:00:00",
     "config": {"emitter": "parquet", "emitter_arg": {"out_uri": "s3://bkt/vecoli-output/exp-sms-a"},
                "description": "sms run A", "aws": {"batch_queue": "ray-q"}}},
    {"simulator_id": 71, "database_id": 502, "experiment_id": "exp-sms-b",
     "status": "completed", "created_at": "2026-09-02 12:00:00",
     "config": {"emitter": "parquet", "emitter_arg": {"out_uri": "s3://bkt/vecoli-output/exp-sms-b"}}},
    {"simulator_id": 10, "database_id": 999, "experiment_id": "exp-vecoli",
     "status": "completed", "created_at": "2026-09-03 12:00:00",
     "config": {"emitter": "parquet", "emitter_arg": {"out_uri": "s3://bkt/vecoli-output/exp-vecoli"}}},
]


class _FakeClient:
    def __init__(self, *a, **k):
        pass

    def list_simulators(self):
        return {"versions": _BUILDS}

    def list_build_simulations(self, simulator_id):
        return _SIMS


@pytest.fixture
def _stub(monkeypatch):
    monkeypatch.setattr("vivarium_workbench.lib.sms_api_client.SmsApiClient", _FakeClient)
    monkeypatch.setattr("vivarium_workbench.lib.git_status.remote_repo_url",
                        lambda ws: "https://github.com/CovertLabEcoli/sms-ecoli")
    monkeypatch.setenv("VIVARIUM_WORKBENCH_REMOTE_DEPLOYMENT", "govcloud-test")


def test_local_workspace_lists_repo_scoped_remote_runs(_stub, tmp_path):
    rows = rs.list_remote_simulations(tmp_path)
    # Only the two sms-ecoli-build sims — the vEcoli sim is filtered out.
    assert {r["run_id"] for r in rows} == {"exp-sms-a", "exp-sms-b"}
    a = next(r for r in rows if r["run_id"] == "exp-sms-a")
    assert a["remote_origin"]["deployment"] == "govcloud-test"      # Origin = deployment
    assert a["remote_origin"]["simulation_id"] == 501               # for on-demand fetch/land
    assert a["remote_origin"]["build"] == 70
    assert a["store_path"] == "s3://bkt/vecoli-output/exp-sms-a"     # Location = S3 URI
    assert a["source"] == "remote"
    # newest first
    assert [r["run_id"] for r in rows] == ["exp-sms-b", "exp-sms-a"]


def test_no_repo_resolvable_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setattr("vivarium_workbench.lib.sms_api_client.SmsApiClient", _FakeClient)
    monkeypatch.setattr("vivarium_workbench.lib.git_status.remote_repo_url", lambda ws: None)
    assert rs.list_remote_simulations(tmp_path) == []


def test_unreachable_sms_api_returns_empty(monkeypatch, tmp_path):
    class _Boom:
        def __init__(self, *a, **k): pass
        def list_simulators(self): raise RuntimeError("tunnel down")
    monkeypatch.setattr("vivarium_workbench.lib.sms_api_client.SmsApiClient", _Boom)
    monkeypatch.setattr("vivarium_workbench.lib.git_status.remote_repo_url",
                        lambda ws: "https://github.com/CovertLabEcoli/sms-ecoli")
    assert rs.list_remote_simulations(tmp_path) == []


def test_limit_caps_rows(_stub, tmp_path):
    assert len(rs.list_remote_simulations(tmp_path, limit=1)) == 1


def test_repo_key_matches_across_url_forms():
    for form in ("https://github.com/CovertLabEcoli/sms-ecoli",
                 "https://github.com/CovertLabEcoli/sms-ecoli.git",
                 "git@github.com:CovertLabEcoli/sms-ecoli.git",
                 "CovertLabEcoli/sms-ecoli"):
        assert rs._repo_key(form) == "covertlabecoli/sms-ecoli"
