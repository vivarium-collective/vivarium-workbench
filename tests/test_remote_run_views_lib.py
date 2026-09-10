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
        self.analyzed = None

    def upload_simulator(self, simulator, force=False):
        self.uploaded = simulator
        return {"database_id": 66}

    def run_simulation(self, **kwargs):
        self.ran = kwargs
        return {"database_id": 199}

    # -- P1-11 (audit §3.9): execution-provenance lookups land() reads --------
    def get_simulation(self, simulation_id):
        return {
            "database_id": simulation_id,
            "simulator_id": 66,
            "config": {"generations": 1, "n_init_sims": 1},
            "num_seeds": 1,
        }

    def simulator_commit(self, simulator_id):
        return "deadbee1234"

    def download_data(self, simulation_id, dest_dir, timeout=None):
        self.downloaded = simulation_id
        p = Path(dest_dir) / f"sim_{simulation_id}.tar.gz"
        p.write_bytes(b"TAR")
        return p

    def run_analysis(self, simulation_id, modules):
        self.analyzed = (simulation_id, modules)
        return {"job_id": "ana-fake", "database_id": 7}

    def analysis_status(self, analysis_id):
        self.status_polls = getattr(self, "status_polls", 0) + 1
        return {"id": analysis_id, "status": "completed"}


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
    body, status = rrv.remote_run_submit(
        tmp_path, {"simulator_id": 66, "study": "s", "num_generations": 1, "num_seeds": 1})
    assert status == 202
    assert body["simulation_id"] == 199
    assert body["phase"] == "running"


# ---------------------------------------------------------------------------
# item 84: a dispatch-time pending-run placeholder, so the Runs tab shows a
# UI-dispatched campaign immediately instead of only once someone lands it
# (land_remote_run was the ONLY place a runs_meta row was ever written for a
# remote run — confirmed by reading the code, not assumed).
# ---------------------------------------------------------------------------

def test_submit_writes_a_pending_runs_meta_row(monkeypatch, tmp_path):
    from vivarium_workbench.lib import composite_runs as cr

    _wire_thin(monkeypatch, tmp_path)
    monkeypatch.setattr(rrv, "SmsApiClient", _FakeThinClient)
    monkeypatch.setattr(rrv.remote_pinned, "remote_deployment_name", lambda: "smscdk")
    body, status = rrv.remote_run_submit(
        tmp_path, {"simulator_id": 66, "study": "s", "num_generations": 10, "num_seeds": 1000})
    assert status == 202

    # _wire_thin stubs study_spec.study_dir to return tmp_path itself.
    conn = cr.connect(tmp_path / "runs.db")
    try:
        row = conn.execute(
            "SELECT spec_id, status, params_json FROM runs_meta WHERE run_id=?",
            (f"remote-pending-{body['simulation_id']}",),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    spec_id, status_col, params_json = row
    assert spec_id == "my-comp"  # from _wire_thin's baseline[0].composite
    assert status_col == "running"
    import json as _json
    params = _json.loads(params_json)
    assert params == {"source": "smscdk", "simulation_id": 199, "backend": "ray"}


def test_submit_still_succeeds_when_pending_row_write_fails(monkeypatch, tmp_path):
    """The pending-row write is cosmetic (Runs-tab visibility); a real,
    already-dispatched, real-money AWS Batch campaign must never be reported
    as failed just because this bookkeeping side effect couldn't happen."""
    _wire_thin(monkeypatch, tmp_path)
    monkeypatch.setattr(rrv, "SmsApiClient", _FakeThinClient)

    def _boom(ws, name):
        raise OSError("workspace paths unavailable")

    monkeypatch.setattr(rrv.study_spec, "study_dir", _boom)
    body, status = rrv.remote_run_submit(
        tmp_path, {"simulator_id": 66, "study": "s", "num_generations": 1, "num_seeds": 1})
    assert status == 202
    assert body["simulation_id"] == 199


def test_submit_missing_simulator_id_400(monkeypatch, tmp_path):
    _wire_thin(monkeypatch, tmp_path)
    monkeypatch.setattr(rrv, "SmsApiClient", _FakeThinClient)
    assert rrv.remote_run_submit(tmp_path, {"study": "s"})[1] == 400


# ---------------------------------------------------------------------------
# num_generations/num_seeds: no silent default (mirrors the client-side guard
# in study-detail.js:_dispatchRemotePinned -- these two size a real AWS Batch
# job, so an unset value must 400 here rather than quietly becoming 1x1).
# ---------------------------------------------------------------------------

def test_submit_missing_num_generations_400(monkeypatch, tmp_path):
    _wire_thin(monkeypatch, tmp_path)
    monkeypatch.setattr(rrv, "SmsApiClient", _FakeThinClient)
    body, status = rrv.remote_run_submit(
        tmp_path, {"simulator_id": 66, "study": "s", "num_seeds": 3})
    assert status == 400
    assert "num_generations" in body["error"]


def test_submit_missing_num_seeds_400(monkeypatch, tmp_path):
    _wire_thin(monkeypatch, tmp_path)
    monkeypatch.setattr(rrv, "SmsApiClient", _FakeThinClient)
    body, status = rrv.remote_run_submit(
        tmp_path, {"simulator_id": 66, "study": "s", "num_generations": 5})
    assert status == 400
    assert "num_seeds" in body["error"]


def test_submit_zero_num_generations_treated_as_unset_400(monkeypatch, tmp_path):
    """0 is falsy but numerically distinct from "missing" — still must block:
    a real dispatch can never run zero generations, and `not x` is the same
    truthiness check already used for simulator_id/study in this ladder."""
    _wire_thin(monkeypatch, tmp_path)
    monkeypatch.setattr(rrv, "SmsApiClient", _FakeThinClient)
    body, status = rrv.remote_run_submit(
        tmp_path, {"simulator_id": 66, "study": "s", "num_generations": 0, "num_seeds": 3})
    assert status == 400
    assert "num_generations" in body["error"]


def test_submit_forwards_explicit_num_generations_and_num_seeds(monkeypatch, tmp_path):
    """When num_generations/num_seeds ARE set, the exact submitted values —
    never a coerced-from-missing default — must reach
    SmsApiClient.run_simulation()."""
    _wire_thin(monkeypatch, tmp_path)
    client = _FakeThinClient()
    monkeypatch.setattr(rrv, "SmsApiClient", lambda base=None: client)
    body, status = rrv.remote_run_submit(
        tmp_path, {"simulator_id": 66, "study": "s", "num_generations": 7, "num_seeds": 3})
    assert status == 202
    assert client.ran["num_generations"] == 7
    assert client.ran["num_seeds"] == 3


def test_submit_threads_analysis_options_from_spec(monkeypatch, tmp_path):
    """spec.analyses must reach client.run_simulation()'s analysis_options —
    this was silently dropped for every remote dispatch (the "Analyses 404"
    root cause: sms-api never received a real analysis config to run)."""
    _wire_thin(monkeypatch, tmp_path)
    monkeypatch.setattr(rrv, "SmsApiClient", _FakeThinClient)
    monkeypatch.setattr(
        rrv, "load_spec",
        lambda p: {"baseline": [{"composite": "my-comp"}],
                   "analyses": [{"name": "ecocyc_table", "params": {}}]},
    )
    monkeypatch.setattr(
        "vivarium_workbench.lib.study_run_post.build_analysis_options",
        lambda entries, ws_root: ({"multiseed": {"ecocyc_table": {}}}, []),
    )
    client = _FakeThinClient()
    monkeypatch.setattr(rrv, "SmsApiClient", lambda base=None: client)
    body, status = rrv.remote_run_submit(
        tmp_path, {"simulator_id": 66, "study": "s", "num_generations": 1, "num_seeds": 1})
    assert status == 202
    assert client.ran["analysis_options"] == {"multiseed": {"ecocyc_table": {}}}


def test_submit_analysis_options_none_when_spec_has_no_analyses(monkeypatch, tmp_path):
    """A study with no analyses: entries passes analysis_options=None, not {}
    — matching run_simulation()'s default and sms-api's contract."""
    _wire_thin(monkeypatch, tmp_path)
    client = _FakeThinClient()
    monkeypatch.setattr(rrv, "SmsApiClient", lambda base=None: client)
    body, status = rrv.remote_run_submit(
        tmp_path, {"simulator_id": 66, "study": "s", "num_generations": 1, "num_seeds": 1})
    assert status == 202
    assert client.ran["analysis_options"] is None


def test_submit_surfaces_analysis_errors_in_response(monkeypatch, tmp_path):
    """A study whose spec.analyses includes an unresolvable name must still
    dispatch (the errors are informational, not blocking) but the response
    must surface which names failed, instead of silently dropping them from
    analysis_options with no trace anywhere (backlog item 39 continued)."""
    _wire_thin(monkeypatch, tmp_path)
    monkeypatch.setattr(
        rrv, "load_spec",
        lambda p: {"baseline": [{"composite": "my-comp"}],
                   "analyses": [{"name": "ecocyc_table"}, {"name": "totally_unknown"}]},
    )
    monkeypatch.setattr(
        "vivarium_workbench.lib.study_run_post.build_analysis_options",
        lambda entries, ws_root: (
            {"multiseed": {"ecocyc_table": {}}},
            [{"analysis": "totally_unknown", "error": "unknown analysis 'totally_unknown'"}],
        ),
    )
    client = _FakeThinClient()
    monkeypatch.setattr(rrv, "SmsApiClient", lambda base=None: client)
    body, status = rrv.remote_run_submit(
        tmp_path, {"simulator_id": 66, "study": "s", "num_generations": 1, "num_seeds": 1})
    assert status == 202
    assert body["analysis_errors"] == [
        {"analysis": "totally_unknown", "error": "unknown analysis 'totally_unknown'"}
    ]
    # the dispatch itself still proceeded with whatever DID resolve
    assert client.ran["analysis_options"] == {"multiseed": {"ecocyc_table": {}}}


def test_submit_omits_analysis_errors_key_when_none(monkeypatch, tmp_path):
    """No analysis errors -> no analysis_errors key at all, not an empty
    list, matching every other optional-field convention in this module."""
    _wire_thin(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "vivarium_workbench.lib.study_run_post.build_analysis_options",
        lambda entries, ws_root: ({}, []),
    )
    client = _FakeThinClient()
    monkeypatch.setattr(rrv, "SmsApiClient", lambda base=None: client)
    body, status = rrv.remote_run_submit(
        tmp_path, {"simulator_id": 66, "study": "s", "num_generations": 1, "num_seeds": 1})
    assert status == 202
    assert "analysis_errors" not in body


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


def test_land_sources_commit_image_and_config_hash_from_sms_api(monkeypatch, tmp_path):
    """P1-11 (audit §3.9): remote_run_land must resolve commit/image/
    merged_config_hash/expected_seeds from sms-api's OWN records
    (_resolve_execution_provenance) and forward them into land_remote_run --
    not leave land_remote_run to fall back to whatever body.get("commit")
    happened to be (which no real caller has ever actually populated)."""
    captured = _wire_thin(monkeypatch, tmp_path)
    monkeypatch.setattr(rrv, "SmsApiClient", _FakeThinClient)
    body, status = rrv.remote_run_land(tmp_path, {"study": "s", "simulation_id": 199})
    assert status == 200
    _study_dir, kw = captured["land"]
    # _FakeThinClient.get_simulation/simulator_commit resolve to these fixture
    # values -- NOT anything derived from the local workspace checkout.
    assert kw["commit"] == "deadbee1234"
    assert kw["image"] == "deadbee1234"
    assert kw["merged_config_hash"]  # a real sha256 hex digest of the fixture config
    assert kw["expected_seeds"] == 1


def test_land_explicit_body_commit_wins_over_sms_api_lookup(monkeypatch, tmp_path):
    """A caller that DOES supply a commit in the body is still honored -- the
    sms-api lookup is a fallback for when nothing better is known, not an
    override of an already-known-good value."""
    captured = _wire_thin(monkeypatch, tmp_path)
    monkeypatch.setattr(rrv, "SmsApiClient", _FakeThinClient)
    rrv.remote_run_land(tmp_path, {"study": "s", "simulation_id": 199, "commit": "explicit-commit"})
    _study_dir, kw = captured["land"]
    assert kw["commit"] == "explicit-commit"


def test_land_triggers_analysis_and_polls_real_status_when_spec_has_analyses(monkeypatch, tmp_path):
    """A study with spec.analyses configured must trigger sms-api's standalone
    analysis (the 14th-bug fix) before downloading, then poll its REAL status
    (gap-3/gap-5: GET /analyses/{id}/status, not a blind fixed sleep) until
    terminal, so a completed job has a real chance of being included in what
    the subsequent download picks up."""
    _wire_thin(monkeypatch, tmp_path)
    monkeypatch.setattr(
        rrv, "load_spec",
        lambda p: {"baseline": [{"composite": "my-comp"}],
                   "analyses": [{"name": "doubling_time_distribution", "params": {}}]},
    )
    monkeypatch.setattr(
        "vivarium_workbench.lib.study_run_post.build_analysis_options",
        lambda entries, ws_root: ({"multiseed": {"doubling_time_distribution": {}}}, []),
    )
    client = _FakeThinClient()
    monkeypatch.setattr(rrv, "SmsApiClient", lambda base=None: client)
    slept = []
    monkeypatch.setattr(rrv.time, "sleep", slept.append)

    body, status = rrv.remote_run_land(tmp_path, {"study": "s", "simulation_id": 199})

    assert status == 200
    assert client.analyzed == (199, {"multiseed": {"doubling_time_distribution": {}}})
    # Status came back "completed" on the FIRST poll -- no sleeping needed,
    # unlike the old blind-wait behavior this replaces.
    assert client.status_polls == 1
    assert slept == []
    # The trigger must happen BEFORE the download, so a completed job has a
    # real chance of being included in what gets downloaded.
    assert client.downloaded == 199


def test_land_polls_until_terminal_then_stops(monkeypatch, tmp_path):
    """A slow analysis job must be polled repeatedly (sleeping between
    attempts) until it reaches a terminal status, then stop -- not poll
    forever, not give up after one check."""
    _wire_thin(monkeypatch, tmp_path)
    monkeypatch.setattr(
        rrv, "load_spec",
        lambda p: {"baseline": [{"composite": "my-comp"}],
                   "analyses": [{"name": "doubling_time_distribution", "params": {}}]},
    )
    monkeypatch.setattr(
        "vivarium_workbench.lib.study_run_post.build_analysis_options",
        lambda entries, ws_root: ({"multiseed": {"doubling_time_distribution": {}}}, []),
    )

    class _SlowAnalysisClient(_FakeThinClient):
        def __init__(self, base=None):
            super().__init__(base)
            self._polls = 0

        def analysis_status(self, analysis_id):
            self._polls += 1
            return {"id": analysis_id, "status": "running" if self._polls < 3 else "completed"}

    client = _SlowAnalysisClient()
    monkeypatch.setattr(rrv, "SmsApiClient", lambda base=None: client)
    slept = []
    monkeypatch.setattr(rrv.time, "sleep", slept.append)

    body, status = rrv.remote_run_land(tmp_path, {"study": "s", "simulation_id": 199})

    assert status == 200
    assert client._polls == 3
    assert slept == [rrv._ANALYSIS_POLL_INTERVAL_SECONDS] * 2


def test_land_gives_up_after_poll_ceiling(monkeypatch, tmp_path):
    """A job that never reaches a terminal status must not poll forever --
    landing proceeds anyway after the ceiling, per the best-effort contract."""
    _wire_thin(monkeypatch, tmp_path)
    monkeypatch.setattr(
        rrv, "load_spec",
        lambda p: {"baseline": [{"composite": "my-comp"}],
                   "analyses": [{"name": "doubling_time_distribution", "params": {}}]},
    )
    monkeypatch.setattr(
        "vivarium_workbench.lib.study_run_post.build_analysis_options",
        lambda entries, ws_root: ({"multiseed": {"doubling_time_distribution": {}}}, []),
    )

    class _StuckAnalysisClient(_FakeThinClient):
        def analysis_status(self, analysis_id):
            self.status_polls = getattr(self, "status_polls", 0) + 1
            return {"id": analysis_id, "status": "running"}

    client = _StuckAnalysisClient()
    monkeypatch.setattr(rrv, "SmsApiClient", lambda base=None: client)
    monkeypatch.setattr(rrv.time, "sleep", lambda s: None)

    body, status = rrv.remote_run_land(tmp_path, {"study": "s", "simulation_id": 199})

    assert status == 200
    assert client.status_polls == rrv._ANALYSIS_POLL_MAX_ATTEMPTS


def test_land_skips_analysis_trigger_when_spec_has_no_analyses(monkeypatch, tmp_path):
    _wire_thin(monkeypatch, tmp_path)
    client = _FakeThinClient()
    monkeypatch.setattr(rrv, "SmsApiClient", lambda base=None: client)
    slept = []
    monkeypatch.setattr(rrv.time, "sleep", slept.append)

    body, status = rrv.remote_run_land(tmp_path, {"study": "s", "simulation_id": 199})

    assert status == 200
    assert client.analyzed is None
    assert slept == []


def test_land_analysis_trigger_failure_does_not_block_landing(monkeypatch, tmp_path):
    """run_analysis is best-effort: if sms-api rejects/can't reach the trigger,
    landing the simulation output itself must still succeed."""
    _wire_thin(monkeypatch, tmp_path)
    monkeypatch.setattr(
        rrv, "load_spec",
        lambda p: {"baseline": [{"composite": "my-comp"}],
                   "analyses": [{"name": "doubling_time_distribution", "params": {}}]},
    )
    monkeypatch.setattr(
        "vivarium_workbench.lib.study_run_post.build_analysis_options",
        lambda entries, ws_root: ({"multiseed": {"doubling_time_distribution": {}}}, []),
    )

    class _FailingAnalysisClient(_FakeThinClient):
        def run_analysis(self, simulation_id, modules):
            raise rrv.SmsApiError("sms-api unreachable")

    client = _FailingAnalysisClient()
    monkeypatch.setattr(rrv, "SmsApiClient", lambda base=None: client)
    slept = []
    monkeypatch.setattr(rrv.time, "sleep", slept.append)

    body, status = rrv.remote_run_land(tmp_path, {"study": "s", "simulation_id": 199})

    assert status == 200
    assert body["run_id"] == "run-xyz"
    assert slept == []  # never waits if the trigger itself failed


def test_land_surfaces_analysis_errors_in_response(monkeypatch, tmp_path):
    """A study whose spec.analyses includes an unresolvable name must still
    land normally (the errors are informational, not blocking) but the
    response must surface which names failed, instead of silently dropping
    them with no trace anywhere (backlog item 39 continued -- mirrors
    remote_run_submit's own analysis_errors field)."""
    _wire_thin(monkeypatch, tmp_path)
    monkeypatch.setattr(
        rrv, "load_spec",
        lambda p: {"baseline": [{"composite": "my-comp"}],
                   "analyses": [{"name": "doubling_time_distribution"}, {"name": "totally_unknown"}]},
    )
    monkeypatch.setattr(
        "vivarium_workbench.lib.study_run_post.build_analysis_options",
        lambda entries, ws_root: (
            {"multiseed": {"doubling_time_distribution": {}}},
            [{"analysis": "totally_unknown", "error": "unknown analysis 'totally_unknown'"}],
        ),
    )
    client = _FakeThinClient()
    monkeypatch.setattr(rrv, "SmsApiClient", lambda base=None: client)

    body, status = rrv.remote_run_land(tmp_path, {"study": "s", "simulation_id": 199})

    assert status == 200
    assert body["run_id"] == "run-xyz"
    assert body["analysis_errors"] == [
        {"analysis": "totally_unknown", "error": "unknown analysis 'totally_unknown'"}
    ]
    # landing itself still proceeded, and the trigger still fired with
    # whatever DID resolve
    assert client.analyzed == (199, {"multiseed": {"doubling_time_distribution": {}}})


def test_land_omits_analysis_errors_key_when_none(monkeypatch, tmp_path):
    """No analysis errors (including the common case: a study with no
    spec.analyses at all) -> no analysis_errors key, not an empty list --
    matching remote_run_submit's own convention."""
    _wire_thin(monkeypatch, tmp_path)
    client = _FakeThinClient()
    monkeypatch.setattr(rrv, "SmsApiClient", lambda base=None: client)

    body, status = rrv.remote_run_land(tmp_path, {"study": "s", "simulation_id": 199})

    assert status == 200
    assert "analysis_errors" not in body


def test_land_missing_simulation_id_400(monkeypatch, tmp_path):
    _wire_thin(monkeypatch, tmp_path)
    monkeypatch.setattr(rrv, "SmsApiClient", _FakeThinClient)
    assert rrv.remote_run_land(tmp_path, {"study": "s"})[1] == 400


# ---------------------------------------------------------------------------
# Backlog item 23 — on-demand analysis for an EXISTING completed simulation.
#
# Before this there was NO way to fire the analysis phase from the UI at all:
# `remote_run_land` triggers one, but only as a side effect of downloading a run
# into a study, and only when that study declares `analyses`. A completed
# simulation whose analysis failed, or that isn't being landed, or that has no
# study at all, could only be re-analysed with the `atlantis` CLI.
# ---------------------------------------------------------------------------


def _bind_analysis_client(monkeypatch, tmp_path, *, client=None, authed=True):
    monkeypatch.setattr(rrv.github_auth, "current_session", lambda: (object() if authed else None))
    monkeypatch.setattr(rrv, "_sms_api_base", lambda: "http://sms.local")
    c = client or _FakeThinClient()
    monkeypatch.setattr(rrv, "SmsApiClient", lambda base=None: c)
    return c


def test_run_analysis_fires_on_an_existing_simulation(monkeypatch, tmp_path):
    client = _bind_analysis_client(monkeypatch, tmp_path)
    body, status = rrv.remote_run_analysis(tmp_path, {"simulation_id": 199})
    assert status == 202
    assert client.analyzed == (199, {})
    assert body["simulation_id"] == 199
    assert body["analysis_id"] == 7
    assert body["phase"] == "analyzing"


def test_run_analysis_needs_no_study(monkeypatch, tmp_path):
    """A remote simulation row carries no study slug (remote builds aren't
    study-organized), so requiring one would make the trigger unusable exactly
    where it's needed."""
    client = _bind_analysis_client(monkeypatch, tmp_path)
    _body, status = rrv.remote_run_analysis(tmp_path, {"simulation_id": 199, "study": ""})
    assert status == 202
    assert client.analyzed[0] == 199


def test_run_analysis_uses_the_studys_own_analyses_when_given_one(monkeypatch, tmp_path):
    """Same source (`spec.analyses`) and same translator the local post-run
    pipeline and remote_run_submit already use — not a third opinion."""
    _wire_thin(monkeypatch, tmp_path)
    client = _bind_analysis_client(monkeypatch, tmp_path)
    monkeypatch.setattr(
        rrv, "load_spec",
        lambda p: {"analyses": [{"name": "cd1_fluxomics", "params": {}}]},
    )
    monkeypatch.setattr(
        "vivarium_workbench.lib.study_run_post.build_analysis_options",
        lambda entries, ws_root: ({"multiseed": {"cd1_fluxomics": {}}}, []),
    )
    _body, status = rrv.remote_run_analysis(tmp_path, {"simulation_id": 199, "study": "s"})
    assert status == 202
    assert client.analyzed == (199, {"multiseed": {"cd1_fluxomics": {}}})


def test_run_analysis_explicit_modules_win_over_the_study_spec(monkeypatch, tmp_path):
    _wire_thin(monkeypatch, tmp_path)
    client = _bind_analysis_client(monkeypatch, tmp_path)
    explicit = {"single": {"ptools_rna": {"n_tp": 3}}}
    _body, status = rrv.remote_run_analysis(
        tmp_path, {"simulation_id": 199, "study": "s", "modules": explicit})
    assert status == 202
    assert client.analyzed == (199, explicit)


def test_run_analysis_sends_no_guessed_default(monkeypatch, tmp_path):
    """With no study and no explicit modules, the workbench must send an EMPTY
    map and let viva-api resolve it (the simulation's own analysis_options, else
    the model image's own 'applicable' set). A default guessed here would be a
    third copy of a module list neither side can verify — the exact shape of the
    2026-08-05 wrong-scale outage."""
    client = _bind_analysis_client(monkeypatch, tmp_path)
    rrv.remote_run_analysis(tmp_path, {"simulation_id": 199})
    assert client.analyzed[1] == {}


def test_run_analysis_missing_simulation_id_400(monkeypatch, tmp_path):
    _bind_analysis_client(monkeypatch, tmp_path)
    assert rrv.remote_run_analysis(tmp_path, {})[1] == 400


def test_run_analysis_unauthenticated_401(monkeypatch, tmp_path):
    _bind_analysis_client(monkeypatch, tmp_path, authed=False)
    monkeypatch.setattr(rrv.remote_pinned, "is_pinned_enabled", lambda: False)
    assert rrv.remote_run_analysis(tmp_path, {"simulation_id": 199})[1] == 401


def test_run_analysis_surfaces_a_backend_failure(monkeypatch, tmp_path):
    """Unlike the land-time trigger (best-effort, must never block landing), this
    IS the requested action — swallowing the error would leave the operator
    believing an analysis is running when none is."""

    class _Boom(_FakeThinClient):
        def run_analysis(self, simulation_id, modules):
            raise rrv.SmsApiError("tunnel down")

    _bind_analysis_client(monkeypatch, tmp_path, client=_Boom())
    body, status = rrv.remote_run_analysis(tmp_path, {"simulation_id": 199})
    assert status == 502
    assert "tunnel down" in body["error"]


def test_run_analysis_surfaces_analysis_errors_in_response(monkeypatch, tmp_path):
    """A study whose spec.analyses includes an unresolvable name must still
    fire the trigger (the errors are informational, not blocking) but the
    response must surface which names failed, instead of silently dropping
    them with no trace anywhere (backlog item 39 continued -- mirrors
    remote_run_submit's own analysis_errors field)."""
    _wire_thin(monkeypatch, tmp_path)
    client = _bind_analysis_client(monkeypatch, tmp_path)
    monkeypatch.setattr(
        rrv, "load_spec",
        lambda p: {"analyses": [{"name": "cd1_fluxomics"}, {"name": "totally_unknown"}]},
    )
    monkeypatch.setattr(
        "vivarium_workbench.lib.study_run_post.build_analysis_options",
        lambda entries, ws_root: (
            {"multiseed": {"cd1_fluxomics": {}}},
            [{"analysis": "totally_unknown", "error": "unknown analysis 'totally_unknown'"}],
        ),
    )
    body, status = rrv.remote_run_analysis(tmp_path, {"simulation_id": 199, "study": "s"})
    assert status == 202
    assert body["analysis_errors"] == [
        {"analysis": "totally_unknown", "error": "unknown analysis 'totally_unknown'"}
    ]
    # the trigger itself still fired with whatever DID resolve
    assert client.analyzed == (199, {"multiseed": {"cd1_fluxomics": {}}})


def test_run_analysis_omits_analysis_errors_key_when_none(monkeypatch, tmp_path):
    """No analysis errors (including the common no-study/explicit-modules
    case) -> no analysis_errors key, not an empty list -- matching
    remote_run_submit's own convention."""
    _bind_analysis_client(monkeypatch, tmp_path)
    body, status = rrv.remote_run_analysis(tmp_path, {"simulation_id": 199})
    assert status == 202
    assert "analysis_errors" not in body


class _AnalysisStatusClient:
    def __init__(self, base=None, *, status=None):
        self._status = status

    def analysis_status(self, analysis_id):
        return self._status


def test_status_accepts_an_analysis_id(monkeypatch):
    """One poll endpoint for all three phases — the JS panel already polls
    remote-run-poll; the analysis phase must not need a second one."""
    monkeypatch.setattr(rrv, "_sms_api_base", lambda: "http://sms.local")
    monkeypatch.setattr(
        rrv, "SmsApiClient",
        lambda base=None: _AnalysisStatusClient(base, status={"status": "completed"}),
    )
    body, status = rrv.remote_run_status({"analysis_id": 7})
    assert status == 200
    assert body["kind"] == "analysis" and body["phase"] == "done" and body["analysis_id"] == 7


def test_status_analysis_failed_maps_to_failed(monkeypatch):
    monkeypatch.setattr(rrv, "_sms_api_base", lambda: "http://sms.local")
    monkeypatch.setattr(
        rrv, "SmsApiClient",
        lambda base=None: _AnalysisStatusClient(base, status={"status": "failed", "error_log": "boom"}),
    )
    body, _status = rrv.remote_run_status({"analysis_id": 7})
    assert body["phase"] == "failed" and body["error"] == "boom"


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


def test_status_run_partial_maps_to_failed_not_running(monkeypatch):
    # viva-api#609's new PARTIAL status is terminal (some generations emitted
    # before the run stopped short) -- it must not fall through into the
    # "running" bucket and look permanently stuck in the UI.
    _bind_status_client(monkeypatch, sim_status={"status": "partial"})
    assert rrv.remote_run_status({"simulation_id": 199})[0]["phase"] == "failed"


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


# ─── remote_run_chain_progress (backlog item 6) ─────────────────────────────


class _ChainProgressClient:
    """Fake for remote_run_chain_progress: returns a canned chain-progress dict
    (or raises SmsApiError with a message ending in the real HTTP code, matching
    how SmsApiClient._get actually raises: f"GET {url} -> {e.code}")."""
    def __init__(self, base=None, *, progress=None, raise_err=None):
        self._progress = progress
        self._raise = raise_err

    def simulation_chain_progress(self, sid):
        if self._raise:
            raise self._raise
        return self._progress


def _bind_chain_progress_client(monkeypatch, **kw):
    monkeypatch.setattr(rrv, "_sms_api_base", lambda: "http://sms.local")
    monkeypatch.setattr(rrv, "SmsApiClient", lambda base=None: _ChainProgressClient(base, **kw))


def test_chain_progress_in_progress_split(monkeypatch):
    _bind_chain_progress_client(monkeypatch, progress={
        "status": "running", "terminal": False,
        "seeds_total": 10, "seeds_succeeded": 5, "seeds_failed": 1, "seeds_in_progress": 4,
    })
    body, status = rrv.remote_run_chain_progress({"simulation_id": 163})
    assert status == 200
    assert body["kind"] == "chain_progress" and body["phase"] == "running"
    assert body["seeds_total"] == 10 and body["seeds_succeeded"] == 5
    assert body["seeds_failed"] == 1 and body["seeds_in_progress"] == 4
    assert body["terminal"] is False


def test_chain_progress_terminal_maps_to_done(monkeypatch):
    _bind_chain_progress_client(monkeypatch, progress={
        "status": "completed", "terminal": True,
        "seeds_total": 2, "seeds_succeeded": 2, "seeds_failed": 0, "seeds_in_progress": 0,
    })
    body, status = rrv.remote_run_chain_progress({"simulation_id": 163})
    assert status == 200 and body["phase"] == "done" and body["terminal"] is True


def test_chain_progress_requires_simulation_id(monkeypatch):
    _bind_chain_progress_client(monkeypatch)
    assert rrv.remote_run_chain_progress({})[1] == 400


def test_chain_progress_unknown_simulation_is_404(monkeypatch):
    _bind_chain_progress_client(monkeypatch, raise_err=rrv.SmsApiError("GET http://x -> 404"))
    body, status = rrv.remote_run_chain_progress({"simulation_id": 9999})
    assert status == 404 and body["phase"] == "not_found"


def test_chain_progress_non_chain_run_is_200_not_a_campaign(monkeypatch):
    """A plain (non-chain-dispatch) run has nothing to aggregate -- this is a
    real, expected case, not an error the panel should alarm on."""
    _bind_chain_progress_client(monkeypatch, raise_err=rrv.SmsApiError("GET http://x -> 409"))
    body, status = rrv.remote_run_chain_progress({"simulation_id": 42})
    assert status == 200 and body["phase"] == "not_a_campaign"


def test_chain_progress_sms_api_unreachable_is_502_not_crash(monkeypatch):
    _bind_chain_progress_client(monkeypatch, raise_err=rrv.SmsApiError("tunnel down"))
    body, status = rrv.remote_run_chain_progress({"simulation_id": 163})
    assert status == 502 and body["reachable"] is False
