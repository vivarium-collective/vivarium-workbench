import io
import json
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from vivarium_dashboard.lib.sms_api_client import SmsApiClient, SmsApiError


class _Resp(io.BytesIO):
    status = 200

    def __init__(self, payload, status=200):
        super().__init__(json.dumps(payload).encode())
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


@contextmanager
def _patch_urlopen(monkeypatch, capture, payload, status=200):
    def fake_urlopen(req, timeout=None):
        capture["url"] = req.full_url
        capture["method"] = req.get_method()
        capture["body"] = req.data
        if status != 200:
            from urllib.error import HTTPError

            raise HTTPError(req.full_url, status, "err", {}, io.BytesIO(b"boom"))
        return _Resp(payload, status)

    monkeypatch.setattr("vivarium_dashboard.lib.sms_api_client.urlopen", fake_urlopen)
    yield


def test_latest_simulator_builds_query(monkeypatch):
    cap = {}
    with _patch_urlopen(monkeypatch, cap, {"git_commit_hash": "abc123"}):
        c = SmsApiClient("http://h:8080")
        out = c.latest_simulator("https://github.com/x/v2ecoli", "master")
    assert out["git_commit_hash"] == "abc123"
    assert cap["url"].startswith("http://h:8080/core/v1/simulator/latest?")
    assert "git_branch=master" in cap["url"]
    assert "git_repo_url=https%3A%2F%2Fgithub.com%2Fx%2Fv2ecoli" in cap["url"]


def test_observables_repeats_names_param(monkeypatch):
    cap = {}
    with _patch_urlopen(monkeypatch, cap, {"time": [0.0], "series": {"mass": [1.0]}}):
        c = SmsApiClient("http://h:8080")
        out = c.observables(49, ["mass", "volume"], seed=0)
    assert out["series"]["mass"] == [1.0]
    assert "/api/v1/simulations/49/observables?" in cap["url"]
    assert "names=mass%2Cvolume" in cap["url"]
    assert "seed=0" in cap["url"]


def test_non_200_raises(monkeypatch):
    cap = {}
    with _patch_urlopen(monkeypatch, cap, {}, status=404):
        c = SmsApiClient("http://h:8080")
        with pytest.raises(SmsApiError):
            c.simulation_status(999)


def test_run_simulation_query_and_repeated_observables(monkeypatch):
    cap = {}
    with _patch_urlopen(monkeypatch, cap, {"database_id": 50}):
        c = SmsApiClient("http://h:8080")
        out = c.run_simulation(
            simulator_id=15, num_generations=1, num_seeds=1, run_parca=True,
            observables=["mass", "volume"], experiment_id="exp1",
        )
    assert out["database_id"] == 50
    assert cap["method"] == "POST"
    qs = parse_qs(urlsplit(cap["url"]).query)
    assert qs["simulator_id"] == ["15"]
    assert qs["num_generations"] == ["1"]
    assert qs["run_parca"] == ["True"]
    assert qs["observables"] == ["mass", "volume"]  # repeated key, not comma-joined
    assert qs["experiment_id"] == ["exp1"]


def test_upload_simulator_sends_json_body(monkeypatch):
    cap = {}
    with _patch_urlopen(monkeypatch, cap, {"database_id": 16, "status": "running"}):
        c = SmsApiClient("http://h:8080")
        out = c.upload_simulator({"git_commit_hash": "abc", "git_repo_url": "u", "git_branch": "b"}, force=True)
    assert out["database_id"] == 16
    assert cap["method"] == "POST"
    assert json.loads(cap["body"].decode())["git_commit_hash"] == "abc"
    assert "force=true" in cap["url"]


def test_download_data_streams_to_file(monkeypatch, tmp_path):
    cap = {}
    payload = b"\x1f\x8b\x08fake-gzip-bytes"

    class _RawResp:
        status = 200

        def __init__(self):
            self._b = io.BytesIO(payload)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self._b.close()

        def read(self, n=-1):
            return self._b.read(n)

    def fake_urlopen(req, timeout=None):
        cap["url"] = req.full_url
        cap["method"] = req.get_method()
        return _RawResp()

    monkeypatch.setattr("vivarium_dashboard.lib.sms_api_client.urlopen", fake_urlopen)
    c = SmsApiClient("http://h:8080")
    out = c.download_data(49, tmp_path)
    assert out == tmp_path / "sim_49.tar.gz"
    assert out.read_bytes() == payload
    assert cap["method"] == "POST"
    assert cap["url"] == "http://h:8080/api/v1/simulations/49/data"
