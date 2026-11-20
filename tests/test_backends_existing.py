"""Step 1c-2: every operation on an existing remote run goes through its stored record.

docs/backend-adapters.md §2.3 (decided 2026-11-19): an existing run is answered by the backend that ran it, which its
record (``params.remote``) names; a run that belongs to another backend is refused (409, terminal) with no request
to the configured backend. Each entry point is driven with a real workspace holding the record, and every refusal
case asserts that no backend was asked anything.

Step P3 K2a: the cases whose record or id names a viva-core or sms-legacy run need the SMS plugin and are marked
``plugin``; the rest run core-only too, where ``asked`` records only compose-api (the SMS backends cannot be asked).
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from vivarium_workbench.lib import composite_runs as cr
from vivarium_workbench.lib import remote_run_compose
from vivarium_workbench.lib import remote_run_views as rrv
from vivarium_workbench.lib.backends import records
from vivarium_workbench.lib.backend_config import BACKEND_BASE_ENV_VARS

HERE = "http://api:8000"
ELSEWHERE = "http://other:8000"


@pytest.fixture
def ws(tmp_path, monkeypatch):
    for v in BACKEND_BASE_ENV_VARS + ("VIVARIUM_WORKBENCH_REMOTE_DEPLOYMENT",):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("SMS_API_BASE", HERE)  # this workbench's one backend (Stanford-style)
    monkeypatch.setattr(records, "fetch_server_id", lambda url, **_: None)
    monkeypatch.setattr(records, "refresh_server_id", lambda url, **_: None)
    root = tmp_path / "ws"
    (root / "studies" / "s").mkdir(parents=True)
    (root / "workspace.yaml").write_text("name: ws\n")
    return root


def _record(ws: Path, rid: str, kind: str, base: str, *, dep: str = "smsvpctest") -> None:
    conn = cr.connect(ws / "studies" / "s" / "runs.db")
    try:
        cr.save_metadata(conn, spec_id="c", run_id=f"remote-pending-{rid}", label=None, started_at=0.0, n_steps=0,
                         params={"source": dep, "simulation_id": rid, "backend": "ray",
                                 "remote": {"kind": kind, "id": rid, "base_url": base, "deployment": dep,
                                            "server_id": None}})
    finally:
        conn.close()


@pytest.fixture
def asked(monkeypatch):
    """Every request to any backend: compose-api and viva-core adapters' modules, and the SMS client (the latter two
    only where the plugin is installed)."""
    try:
        remote_run_viva_v1 = importlib.import_module("vivarium_workbench_sms.remote_run_viva_v1")
        sms_legacy = importlib.import_module("vivarium_workbench_sms.sms_legacy")
    except ImportError:   # the core-only job
        remote_run_viva_v1 = sms_legacy = None

    seen: list = []
    for mod, tag in ((remote_run_compose, "compose"), (remote_run_viva_v1, "viva")):
        if mod is None:
            continue
        for name in ("land", "status", "progress", "cancel"):
            monkeypatch.setattr(mod, name, lambda *a, _t=tag, _n=name: seen.append((_t, _n)) or ({"via": _t}, 200))
        monkeypatch.setattr(remote_run_compose, "trace", lambda rid: seen.append(("compose", "trace")) or (b"{}", 200, "t"))
    if sms_legacy is None:
        return seen

    class Client:
        base_url = HERE

        def __init__(self, *a, **k):
            pass

        def __getattr__(self, method):
            def fn(*a, **k):
                seen.append(("sms", method))
                return {"status": "running", "terminal": False, "cancelled": True, "database_id": 1}
            return fn

    monkeypatch.setattr(sms_legacy, "SmsApiClient", Client)
    monkeypatch.setattr(sms_legacy, "_sms_api_base", lambda: HERE)
    monkeypatch.setattr(sms_legacy, "_run_auth_ok", lambda: True)
    return seen


VIEWS = {
    "status": lambda ws, rid: rrv.remote_run_status({"simulation_id": rid}, ws_root=ws),
    "progress": lambda ws, rid: rrv.remote_run_chain_progress({"simulation_id": rid}, ws_root=ws),
    "cancel": lambda ws, rid: rrv.remote_run_cancel({"simulation_id": rid}, ws_root=ws),
}


@pytest.mark.plugin
@pytest.mark.parametrize("view", list(VIEWS))
def test_a_record_on_another_backend_is_refused_and_nothing_is_asked(ws, asked, view):
    _record(ws, "12", "sms-legacy", ELSEWHERE, dep="prod-elsewhere")
    body, status = VIEWS[view](ws, "12")
    assert status == 409 and body["terminal"] is True and body["phase"] == "other-backend"
    assert asked == []


@pytest.mark.plugin
@pytest.mark.parametrize("view, op", [("status", "status"), ("progress", "progress"), ("cancel", "cancel")])
def test_a_records_kind_decides_over_the_ids_form(ws, asked, view, op):
    """An all-digit id that the record says is a viva-core run goes to viva-core, not to the SMS path."""
    _record(ws, "123", "viva-core", HERE)
    VIEWS[view](ws, "123")
    assert asked == [("viva", op)]


@pytest.mark.plugin
@pytest.mark.parametrize("view", list(VIEWS))
def test_no_record_falls_back_to_the_ids_form(ws, asked, view):
    VIEWS[view](ws, "simulation-X")
    assert asked and asked[0][0] == "viva"


@pytest.mark.plugin
def test_land_routes_by_the_record_and_refuses_another_backends_run(ws, asked):
    _record(ws, "simulation-A", "viva-core", HERE)
    rrv.remote_run_land(ws, {"study": "s", "simulation_id": "simulation-A"})
    assert asked == [("viva", "land")]
    asked.clear()
    _record(ws, "simulation-B", "viva-core", ELSEWHERE, dep="prod-elsewhere")
    body, status = rrv.remote_run_land(ws, {"study": "s", "simulation_id": "simulation-B"})
    assert status == 409 and asked == []


def test_a_legacy_row_from_another_deployment_is_refused(ws, asked):
    conn = cr.connect(ws / "studies" / "s" / "runs.db")
    try:
        cr.save_metadata(conn, spec_id="c", run_id="landed", label=None, started_at=0.0, n_steps=0,
                         params={"source": "aws-prod", "simulation_id": 12, "backend": "ray"})
    finally:
        conn.close()
    assert VIEWS["status"](ws, "12")[1] == 409 and asked == []


@pytest.mark.plugin
@pytest.mark.parametrize("fn", ["land_artifacts", "analysis"])
def test_sms_only_operations_refuse_a_non_sms_or_foreign_run(ws, asked, fn, monkeypatch):
    call = {
        "land_artifacts": lambda rid: rrv.remote_run_land_artifacts(ws, {"simulation_id": rid, "run_id": "r1"}),
        "analysis": lambda rid: rrv.remote_run_analysis(ws, {"simulation_id": rid}),
    }[fn]
    _record(ws, "7", "viva-core", HERE)
    body, status = call("7")
    assert status == 409 and body["reason"] == "not-offered" and body["error"] and asked == []
    _record(ws, "8", "sms-legacy", ELSEWHERE, dep="prod-elsewhere")
    assert call("8")[1] == 409 and asked == []


@pytest.mark.plugin
def test_trace_routes_by_the_record(ws, asked, monkeypatch):
    from vivarium_workbench.lib import remote_trace
    from vivarium_workbench_sms import sms_api_client, sms_trace

    calls = []

    class Client:
        def composite_run_trace(self, rid):
            calls.append(("composite_run_trace", rid))
            return b"{}"

        def simulation_trace(self, sid):
            calls.append(("simulation_trace", sid))
            return b"{}"

    monkeypatch.setattr(sms_api_client, "require_capabilities", lambda client, *names: None)
    monkeypatch.setattr(sms_trace, "make_client", Client)
    _record(ws, "simulation-V", "viva-core", HERE)
    assert remote_trace.fetch_trace(ws_root=ws, simulation_id="simulation-V")[1] == 200
    assert calls == [("composite_run_trace", "simulation-V")]
    _record(ws, "44", "sms-legacy", ELSEWHERE, dep="prod-elsewhere")
    body, status, _ = remote_trace.fetch_trace(ws_root=ws, simulation_id="44")
    assert status == 409 and calls == [("composite_run_trace", "simulation-V")]


def test_resolve_or_fetch_store_never_fetches_another_backends_run(ws, monkeypatch):
    from vivarium_workbench.lib import simulations_index

    try:
        sms_api_client = importlib.import_module("vivarium_workbench_sms.sms_api_client")
    except ImportError:   # the core-only job: there is no SMS client to download with
        pass
    else:
        monkeypatch.setattr(sms_api_client.SmsApiClient, "download_data",
                            lambda *a, **k: pytest.fail("downloaded another backend's run"))
    _record(ws, "55", "sms-legacy", ELSEWHERE, dep="prod-elsewhere")
    row = {"store_path": None, "db_path": None, "study_slug": "s",
           "remote_origin": {"simulation_id": 55, "deployment": "prod-elsewhere"}}
    assert simulations_index.resolve_or_fetch_store(ws, row) == (None, None)
    row2 = {"store_path": None, "db_path": None, "study_slug": "s",
            "remote_origin": {"simulation_id": "simulation-X", "deployment": "smsvpctest"}}
    assert simulations_index.resolve_or_fetch_store(ws, row2) == (None, None)  # not an SMS simulation


@pytest.mark.plugin
def test_composite_run_status_asks_with_this_deployment(ws, asked, monkeypatch):
    """A ``remote-sim-<id>`` run is this process's own dispatch: an older row of another deployment with the same
    integer id must not be taken for it."""
    from vivarium_workbench.lib import composite_run_views

    conn = cr.connect(ws / "studies" / "s" / "runs.db")
    try:
        cr.save_metadata(conn, spec_id="c", run_id="old", label=None, started_at=0.0, n_steps=0,
                         params={"source": "aws-prod", "simulation_id": 9, "backend": "ray"})
    finally:
        conn.close()
    body, status = composite_run_views.build_composite_run_status(ws, "remote-sim-9")
    assert status == 200 and asked == [("sms", "simulation_status")]


def test_the_pages_send_where_the_run_went():
    """walkthrough.js and study-detail.js cannot be loaded in a test harness; their requests are pinned by source.
    (sim-table.js and perfetto-open.js are exercised in tests/js/.)"""
    static = Path(__file__).resolve().parent.parent / "vivarium_workbench" / "static"
    walk = (static / "walkthrough.js").read_text(encoding="utf-8")
    assert "'&deployment=' + encodeURIComponent(where.deployment)" in walk
    assert "window._remoteTerminalCache[key] = 'other backend'" in walk  # a refusal is terminal, never re-polled
    detail = (static / "study-detail.js").read_text(encoding="utf-8")
    assert "{ simulation_id: runId, study: studyName() }" in detail  # cancel
    assert "'&study=' + encodeURIComponent(studyName() || '')" in detail  # chain progress
    assert "d.phase === 'other-backend' || d.phase === 'not-offered' || d.phase === 'not-sms'" in detail
    sim = (static / "sim-table.js").read_text(encoding="utf-8")
    assert sim.count("_rowWhere(btn)") >= 3 and "_whereQuery(where)" in sim


# --- implementation review round 1 --------------------------------------------------------------------------------

@pytest.mark.parametrize("zero", [0, "0", " 00 "])
@pytest.mark.parametrize("view", list(VIEWS))
def test_a_zero_id_never_reaches_a_backend(ws, asked, view, zero):
    assert VIEWS[view](ws, zero)[1] == 400 and asked == []


def test_land_with_a_falsy_id_is_the_old_400(ws, asked):
    assert rrv.remote_run_land(ws, {"study": "s", "simulation_id": 0})[1] == 400 and asked == []


def test_land_reads_run_id_only_as_a_compose_run(ws, asked):
    """In the land family ``run_id`` is the LOCAL landed run; only compose-api's land ever took its id there."""
    assert rrv.remote_run_land(ws, {"study": "s", "run_id": "abc123-local"})[1] == 400 and asked == []
    rrv.remote_run_land(ws, {"study": "s", "run_id": "compose-7"})
    assert asked == [("compose", "land")]


@pytest.mark.plugin
@pytest.mark.parametrize("junk", [{"a": 1}, ["x"], 7, "  "])
def test_a_non_string_deployment_is_ignored_not_a_500(ws, asked, junk):
    _record(ws, "simulation-A", "viva-core", HERE)
    rrv.remote_run_cancel({"simulation_id": "simulation-A", "deployment": junk}, ws_root=ws)
    assert asked == [("viva", "cancel")]


@pytest.mark.plugin
def test_the_figures_routes_refuse_another_backends_run(ws, monkeypatch, inproc_client):
    from vivarium_workbench_sms import sms_api_client

    _record(ws, "66", "sms-legacy", ELSEWHERE, dep="prod-elsewhere")
    monkeypatch.setattr(sms_api_client.SmsApiClient, "__init__", lambda *a, **k: pytest.fail("asked a backend"))
    c = inproc_client(ws)
    for url in ("/api/remote-analysis-figures?simulation_id=66&study=s",
                "/api/remote-analysis-figure?simulation_id=66&analysis=a&path=viz/x.png&study=s"):
        res = c.get(url)
        assert res.status_code == 409 and res.json()["terminal"] is True, url


@pytest.mark.plugin
def test_the_study_gallery_skips_an_index_candidate_of_another_backend(ws, monkeypatch):
    from vivarium_workbench_sms import remote_analysis_figures as raf
    from vivarium_workbench.lib import simulations_index
    from vivarium_workbench_sms import remote_simulations

    _record(ws, "77", "sms-legacy", ELSEWHERE, dep="prod-elsewhere")
    monkeypatch.setattr(remote_simulations, "list_remote_simulations", lambda ws_root: [])
    monkeypatch.setattr(simulations_index, "build_simulations_data_cached", lambda *a, **k: {"simulations": [
        {"study_slug": "s", "status": "completed", "sim_name": "x",
         "remote_origin": {"simulation_id": 77, "deployment": "prod-elsewhere"}}]})
    monkeypatch.setattr(raf, "list_remote_analysis_figures", lambda *a, **k: pytest.fail("asked about 77"))
    monkeypatch.setattr(raf, "datasets_served", lambda client: False)
    out = raf.study_remote_figures(ws, object(), "s")
    assert not out.get("sims")


def test_the_per_image_guard_is_memoised(ws, monkeypatch):
    calls = []
    real = rrv.backends.for_existing
    monkeypatch.setattr(rrv.backends, "for_existing", lambda *a, **k: calls.append(1) or real(*a, **k))
    rrv._FACET_MEMO.clear()
    for _ in range(5):
        rrv._artifacts_of(ws, "88", {"study": "s"}, memo=True)
    assert len(calls) == 1


@pytest.mark.parametrize("fn", ["land_artifacts", "analysis"])
def test_sms_only_operations_refuse_a_zero_string_id(ws, asked, fn):
    call = {
        "land_artifacts": lambda: rrv.remote_run_land_artifacts(ws, {"simulation_id": "0", "run_id": "r1"}),
        "analysis": lambda: rrv.remote_run_analysis(ws, {"simulation_id": "0"}),
    }[fn]
    assert call()[1] == 400 and asked == []
