"""Parity tests for vivarium_workbench.lib.remote_run_views.remote_run_start.

The pure builder is a behaviour-preserving port of the stdlib handler
``server._post_remote_run_start``.  EVERY external is monkeypatched — these
tests never touch a real network, git, auth, or sms-api service.  The bar is
byte-identical error messages + status order (401 -> 400 -> 409 -> 409 -> 404
-> 202) and an identically-wired ``PipelineCtx`` submitted to the SAME
``remote_run_jobs.manager`` singleton.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from vivarium_workbench.lib import remote_run_views as rrv


# ---------------------------------------------------------------------------
# Fakes / helpers
# ---------------------------------------------------------------------------

class _FakeJob:
    def __init__(self, job_id: str = "J1") -> None:
        self.job_id = job_id


class _FakeClient:
    def __init__(self, base=None) -> None:
        self.base = base


def _wire_happy(monkeypatch, tmp_path: Path, spec: dict):
    """Monkeypatch every external so remote_run_start reaches the happy path.

    Returns a dict with the ``submit`` capture (study, worker_fn) and a
    ``ctx`` slot populated when the worker callable is invoked.
    """
    captured: dict = {"submit": None, "ctx": None}

    monkeypatch.setattr(rrv.github_auth, "current_session", lambda: object())
    monkeypatch.setattr(rrv.git_status, "has_origin_remote", lambda ws: True)
    monkeypatch.setattr(rrv.git_status, "remote_repo_url", lambda ws: "https://github.com/x/y")

    spec_file = tmp_path / "study.yaml"
    spec_file.write_text("baseline: []\n")  # presence only — load_spec is stubbed
    monkeypatch.setattr(rrv.study_spec, "study_spec_path", lambda ws, name: spec_file)
    monkeypatch.setattr(rrv.study_spec, "study_dir", lambda ws, name: tmp_path)
    monkeypatch.setattr(rrv, "load_spec", lambda p: spec)

    monkeypatch.setattr(
        rrv.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(args=[], returncode=0, stdout="feature/x\n"),
    )
    monkeypatch.setattr(rrv, "SmsApiClient", _FakeClient)
    monkeypatch.setattr(rrv, "_sms_api_base", lambda: "http://sms.local")

    def _fake_submit(study, worker_fn):
        captured["submit"] = (study, worker_fn)
        return _FakeJob("J1")

    monkeypatch.setattr(rrv.manager, "submit", _fake_submit)

    # Capture the PipelineCtx the builder wires by intercepting run_remote_pipeline
    # (the submitted worker is ``lambda j: run_remote_pipeline(j, ctx)``).
    def _fake_pipeline(job, ctx):
        captured["ctx"] = ctx

    monkeypatch.setattr(rrv, "run_remote_pipeline", _fake_pipeline)
    return captured


# ---------------------------------------------------------------------------
# Error paths (order + exact messages)
# ---------------------------------------------------------------------------

class TestErrorPaths:
    def test_not_authenticated_401(self, monkeypatch, tmp_path):
        monkeypatch.setattr(rrv.github_auth, "current_session", lambda: None)
        body, status = rrv.remote_run_start(tmp_path, {"study": "s"})
        assert (body, status) == ({"error": "not authenticated"}, 401)

    def test_missing_study_400(self, monkeypatch, tmp_path):
        monkeypatch.setattr(rrv.github_auth, "current_session", lambda: object())
        body, status = rrv.remote_run_start(tmp_path, {"study": "   "})
        assert (body, status) == ({"error": "study is required"}, 400)

    def test_no_origin_remote_409(self, monkeypatch, tmp_path):
        monkeypatch.setattr(rrv.github_auth, "current_session", lambda: object())
        monkeypatch.setattr(rrv.git_status, "has_origin_remote", lambda ws: False)
        body, status = rrv.remote_run_start(tmp_path, {"study": "s"})
        assert (body, status) == ({"error": "no GitHub remote configured"}, 409)

    def test_unresolved_repo_url_409(self, monkeypatch, tmp_path):
        monkeypatch.setattr(rrv.github_auth, "current_session", lambda: object())
        monkeypatch.setattr(rrv.git_status, "has_origin_remote", lambda ws: True)
        monkeypatch.setattr(rrv.git_status, "remote_repo_url", lambda ws: None)
        body, status = rrv.remote_run_start(tmp_path, {"study": "s"})
        assert (body, status) == ({"error": "could not resolve origin remote url"}, 409)

    def test_spec_not_found_404(self, monkeypatch, tmp_path):
        monkeypatch.setattr(rrv.github_auth, "current_session", lambda: object())
        monkeypatch.setattr(rrv.git_status, "has_origin_remote", lambda ws: True)
        monkeypatch.setattr(rrv.git_status, "remote_repo_url", lambda ws: "https://github.com/x/y")
        monkeypatch.setattr(
            rrv.study_spec, "study_spec_path", lambda ws, name: tmp_path / "missing.yaml",
        )
        body, status = rrv.remote_run_start(tmp_path, {"study": "ghost"})
        assert (body, status) == ({"error": "study 'ghost' not found"}, 404)


# ---------------------------------------------------------------------------
# Happy path + spec_id resolution
# ---------------------------------------------------------------------------

class TestHappyPath:
    def test_submits_and_returns_202(self, monkeypatch, tmp_path):
        spec = {
            "baseline": [{"composite": "my-composite"}],
            "readouts": [{"store_path": "agents.0.listeners.foo"}],
        }
        captured = _wire_happy(monkeypatch, tmp_path, spec)
        body, status = rrv.remote_run_start(tmp_path, {"study": "study-a"})
        assert (body, status) == ({"job_id": "J1"}, 202)

        # submit was called with the study slug + a callable worker.
        study_arg, worker_fn = captured["submit"]
        assert study_arg == "study-a"
        assert callable(worker_fn)

        # Submits to the SAME singleton (manager.submit was the patched attr).
        from vivarium_workbench.lib import remote_run_jobs
        assert rrv.manager is remote_run_jobs.manager

    def test_pipeline_ctx_spec_id_from_baseline_composite(self, monkeypatch, tmp_path):
        spec = {
            "baseline": [{"composite": "my-composite", "name": "study-a"}],
            "readouts": [{"store_path": "agents/0/listeners/foo"}],
        }
        captured = _wire_happy(monkeypatch, tmp_path, spec)
        body, status = rrv.remote_run_start(
            tmp_path, {"study": "study-a", "num_generations": 3, "num_seeds": 2, "run_parca": False},
        )
        assert status == 202
        # Drive the submitted worker so it constructs/forwards the ctx.
        _study, worker_fn = captured["submit"]
        worker_fn(object())
        ctx = captured["ctx"]
        assert ctx is not None
        assert ctx.spec_id == "my-composite"     # baseline composite, not the slug
        assert ctx.study == "study-a"
        assert ctx.repo_url == "https://github.com/x/y"
        assert ctx.branch == "feature/x"
        assert ctx.num_generations == 3
        assert ctx.num_seeds == 2
        assert ctx.run_parca is False
        assert ctx.observables == ["agents/0/listeners/foo"]
        # push_and_sha is a ZERO-ARG callable wrapping the lib helper.
        assert callable(ctx.push_and_sha)
        called = {}

        def _fake_push(ws):
            called["ws"] = ws
            return "sha123"

        monkeypatch.setattr(rrv.git_status, "remote_push_and_sha", _fake_push)
        assert ctx.push_and_sha() == "sha123"
        assert called["ws"] == tmp_path

    def test_pipeline_ctx_spec_id_falls_back_to_slug(self, monkeypatch, tmp_path):
        spec = {"baseline": [], "readouts": []}  # no baseline composite declared
        captured = _wire_happy(monkeypatch, tmp_path, spec)
        body, status = rrv.remote_run_start(tmp_path, {"study": "lonely-study"})
        assert status == 202
        _study, worker_fn = captured["submit"]
        worker_fn(object())
        ctx = captured["ctx"]
        assert ctx.spec_id == "lonely-study"     # falls back to the study slug
        assert ctx.num_generations == 1          # body defaults
        assert ctx.num_seeds == 1
        assert ctx.run_parca is True


# ---------------------------------------------------------------------------
# WS1 — thin-client two-phase builders (additive)
# ---------------------------------------------------------------------------

class _FakeThinClient:
    def __init__(self, base=None) -> None:
        self.base = base
        self.uploaded = None
        self.ran = None
        self.downloaded = None

    def upload_simulator(self, simulator, force=False):
        self.uploaded = simulator
        return {"database_id": 66}

    def run_simulation(self, **kwargs):
        self.ran = kwargs
        return {"database_id": 199}

    def download_data(self, simulation_id, dest_dir, timeout=None):
        self.downloaded = simulation_id
        p = Path(dest_dir) / f"sim_{simulation_id}.tar.gz"
        p.write_bytes(b"TAR")
        return p


def _wire_thin(monkeypatch, tmp_path, *, authed=True, study_exists=True):
    monkeypatch.setattr(rrv.github_auth, "current_session", lambda: (object() if authed else None))
    monkeypatch.setattr(rrv.git_status, "has_origin_remote", lambda ws: True)
    monkeypatch.setattr(rrv.git_status, "remote_repo_url", lambda ws: "https://github.com/x/y")
    monkeypatch.setattr(rrv.git_status, "remote_push_and_sha", lambda ws: "abc123def456")
    spec_file = tmp_path / "study.yaml"
    spec_file.write_text("baseline: [{composite: my-comp}]\n")
    monkeypatch.setattr(rrv.study_spec, "study_spec_path",
                        lambda ws, name: (spec_file if study_exists else None))
    monkeypatch.setattr(rrv.study_spec, "study_dir", lambda ws, name: tmp_path)
    monkeypatch.setattr(rrv.study_spec, "collect_study_observables", lambda spec: ["cell_mass"])
    monkeypatch.setattr(rrv, "load_spec", lambda p: {"baseline": [{"composite": "my-comp"}]})
    monkeypatch.setattr(
        rrv.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(args=[], returncode=0, stdout="feature/x\n"),
    )
    monkeypatch.setattr(rrv, "_sms_api_base", lambda: "http://sms.local")
    captured = {"land": None}
    monkeypatch.setattr(rrv, "land_remote_run",
                        lambda study_dir, **kw: captured.__setitem__("land", (study_dir, kw)) or "run-xyz")
    return captured


def test_build_start_returns_simulator_id_and_building_phase(monkeypatch, tmp_path):
    _wire_thin(monkeypatch, tmp_path)
    monkeypatch.setattr(rrv, "SmsApiClient", _FakeThinClient)
    body, status = rrv.remote_run_build_start(tmp_path, {"study": "s"})
    assert status == 202
    assert body["simulator_id"] == 66
    assert body["phase"] == "building"
    assert body["commit"] == "abc123def456"


def test_build_start_unauthenticated_401(monkeypatch, tmp_path):
    _wire_thin(monkeypatch, tmp_path, authed=False)
    monkeypatch.setattr(rrv, "SmsApiClient", _FakeThinClient)
    assert rrv.remote_run_build_start(tmp_path, {"study": "s"})[1] == 401


def test_build_start_missing_study_400(monkeypatch, tmp_path):
    _wire_thin(monkeypatch, tmp_path)
    monkeypatch.setattr(rrv, "SmsApiClient", _FakeThinClient)
    assert rrv.remote_run_build_start(tmp_path, {})[1] == 400


def test_submit_issues_run_and_returns_simulation_id(monkeypatch, tmp_path):
    _wire_thin(monkeypatch, tmp_path)
    monkeypatch.setattr(rrv, "SmsApiClient", _FakeThinClient)
    body, status = rrv.remote_run_submit(tmp_path, {"simulator_id": 66, "study": "s"})
    assert status == 202
    assert body["simulation_id"] == 199
    assert body["phase"] == "running"


def test_submit_missing_simulator_id_400(monkeypatch, tmp_path):
    _wire_thin(monkeypatch, tmp_path)
    monkeypatch.setattr(rrv, "SmsApiClient", _FakeThinClient)
    assert rrv.remote_run_submit(tmp_path, {"study": "s"})[1] == 400


def test_land_downloads_and_lands(monkeypatch, tmp_path):
    captured = _wire_thin(monkeypatch, tmp_path)
    monkeypatch.setattr(rrv, "SmsApiClient", _FakeThinClient)
    body, status = rrv.remote_run_land(tmp_path, {"study": "s", "simulation_id": 199})
    assert status == 200
    assert body["run_id"] == "run-xyz"
    assert captured["land"] is not None
    _study_dir, kw = captured["land"]
    assert kw["simulation_id"] == 199
    assert kw["spec_id"] == "my-comp"


def test_land_missing_simulation_id_400(monkeypatch, tmp_path):
    _wire_thin(monkeypatch, tmp_path)
    monkeypatch.setattr(rrv, "SmsApiClient", _FakeThinClient)
    assert rrv.remote_run_land(tmp_path, {"study": "s"})[1] == 400


class _StatusClient:
    """Fake for remote_run_status: returns canned status dicts (or raises)."""
    def __init__(self, base=None, *, sim_status=None, build_status=None, raise_err=None):
        self._sim = sim_status
        self._build = build_status
        self._raise = raise_err

    def simulation_status(self, sid):
        if self._raise:
            raise self._raise
        return self._sim

    def simulator_status(self, sid):
        if self._raise:
            raise self._raise
        return self._build


def _bind_status_client(monkeypatch, **kw):
    monkeypatch.setattr(rrv, "_sms_api_base", lambda: "http://sms.local")
    monkeypatch.setattr(rrv, "SmsApiClient", lambda base=None: _StatusClient(base, **kw))


def test_status_run_completed_maps_to_done(monkeypatch):
    _bind_status_client(monkeypatch, sim_status={"status": "completed"})
    body, status = rrv.remote_run_status({"simulation_id": 199})
    assert status == 200 and body["kind"] == "run" and body["phase"] == "done"


def test_status_run_running_maps_to_running(monkeypatch):
    _bind_status_client(monkeypatch, sim_status={"status": "running"})
    assert rrv.remote_run_status({"simulation_id": 199})[0]["phase"] == "running"


def test_status_run_queued_maps_to_queued(monkeypatch):
    _bind_status_client(monkeypatch, sim_status={"status": "queued"})
    assert rrv.remote_run_status({"simulation_id": 199})[0]["phase"] == "queued"


def test_status_build_completed_maps_to_built(monkeypatch):
    _bind_status_client(monkeypatch, build_status={"status": "completed"})
    body, status = rrv.remote_run_status({"simulator_id": 66})
    assert status == 200 and body["kind"] == "build" and body["phase"] == "built"


def test_status_requires_an_id(monkeypatch):
    _bind_status_client(monkeypatch)
    assert rrv.remote_run_status({})[1] == 400


def test_status_sms_api_unreachable_is_502_not_crash(monkeypatch):
    _bind_status_client(monkeypatch, raise_err=rrv.SmsApiError("tunnel down"))
    body, status = rrv.remote_run_status({"simulation_id": 199})
    assert status == 502 and body["reachable"] is False and "unreachable" in body["reason"]
