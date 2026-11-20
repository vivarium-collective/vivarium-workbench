"""Step 1c-1: every remote run records where it went, and an existing run is resolved by that record.

docs/backend-adapters.md step 1c (Jim, 2026-11-19). The writers add ``params["remote"]`` =
``{kind, id, base_url, deployment, server_id}``; ``backends.records.resolve_existing`` decides which backend may
answer for an existing run, refusing (409 ``other-backend``) one that belongs to another backend. The views start
using it in step 1c-2.

Step P3 K2a: records of the SMS plugin's kinds (``sms-legacy``, ``viva-core``) resolve only with the plugin
installed -- absent, every one is refused ``backend-not-installed`` -- so those cases are marked ``plugin``; the
compose-api cases and the record shape run core-only too.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from vivarium_workbench.lib import composite_runs as cr
from vivarium_workbench.lib import study_spec
from vivarium_workbench.lib.backends import records
from vivarium_workbench.lib.backend_config import BACKEND_BASE_ENV_VARS

NAMED, VIVA, SMS = BACKEND_BASE_ENV_VARS


def _sibling(name: str):
    """Another test module's helpers, loaded by path: ``tests`` is not a package here, and on CI the name
    ``tests`` can resolve to an installed third-party package instead."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(f"_sibling_{name}", Path(__file__).parent / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for v in BACKEND_BASE_ENV_VARS + ("VIVARIUM_WORKBENCH_REMOTE_DEPLOYMENT",):
        monkeypatch.delenv(v, raising=False)
    for cache in (records._IDENTITY, records._REFRESHED, records._FAILED):
        cache.clear()
    yield
    for cache in (records._IDENTITY, records._REFRESHED, records._FAILED):
        cache.clear()


@pytest.fixture
def ws(tmp_path) -> Path:
    root = tmp_path / "ws"
    (root / "studies" / "s").mkdir(parents=True)
    (root / ".pbg").mkdir()
    (root / "workspace.yaml").write_text("name: ws\n")
    return root


def _row(ws: Path, run_id: str, params: dict, study: str = "s") -> None:
    conn = cr.connect(ws / "studies" / study / "runs.db")
    try:
        cr.save_metadata(conn, spec_id="c", run_id=run_id, params=params, label=None, started_at=0.0, n_steps=0)
    finally:
        conn.close()


def _rec(kind="sms-legacy", rid="12", base="http://api:8000", dep="smsvpctest", server_id=None) -> dict:
    return {"kind": kind, "id": rid, "base_url": base, "deployment": dep, "server_id": server_id}


# --- the record ------------------------------------------------------------------------------------------------

@pytest.mark.plugin
def test_remote_record_carries_kind_id_url_deployment_and_the_servers_identity(monkeypatch):
    monkeypatch.setenv("VIVARIUM_WORKBENCH_REMOTE_DEPLOYMENT", "stanford-test")
    monkeypatch.setattr(records, "fetch_server_id", lambda url, **_: None)
    assert records.remote_record("sms-legacy", 12, "http://API:8000/") == _rec(dep="stanford-test")
    monkeypatch.setattr(records, "fetch_server_id", lambda url, **_: "st" if url == "http://api:8000" else None)
    assert records.remote_record("sms-legacy", 12, "http://api:8000")["server_id"] == "st"


@pytest.mark.plugin
def test_the_identity_is_read_from_health_and_a_failure_is_remembered_only_briefly(monkeypatch):
    from vivarium_workbench_sms import sms_api_client as sac

    answers = [RuntimeError("down"), {"deployment_id": "prod"}]

    class Client:
        def health_v1(self):
            a = answers.pop(0)
            if isinstance(a, Exception):
                raise a
            return a

    monkeypatch.setattr(sac.SmsApiClient, "for_", classmethod(lambda cls, kind, base=None, **k: Client()))
    assert records.fetch_server_id("http://api:8000") is None   # never raises
    assert records.fetch_server_id("http://api:8000") is None   # a failure is remembered briefly, not re-asked
    records._FAILED[records._key("http://api:8000")] -= records._IDENTITY_FAIL_TTL + 1  # ...only briefly
    assert records.fetch_server_id("http://api:8000") == "prod"
    assert records.cached_server_id("http://api:8000") == "prod"


# --- compose-api's identity (compose-api#543: deployment_id on GET /health) -----------------------------------------

@pytest.fixture
def compose_health(monkeypatch):
    """Stub compose-api's ``GET /health``: each answer is a health dict, or an exception the request raises (the
    last answer repeats). Returns ``(answers, asked)``, ``asked`` being the URLs asked."""
    import compose_api_client.ext as ext

    answers: list = []
    asked: list = []

    class Session:
        def __init__(self, base_url, **kw):
            self.base_url = base_url
            assert kw.get("timeout") == 3.0  # the probe policy, not the session's 300 s default

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def health(self):
            asked.append(self.base_url)
            a = answers.pop(0) if len(answers) > 1 else answers[0]
            if isinstance(a, Exception):
                raise a
            return a

    monkeypatch.setattr(ext, "ComposeSession", Session)
    return answers, asked


@pytest.fixture
def viva_health(monkeypatch):
    """Stub viva-api's ``GET /viva/v1/health`` (the viva family's identity). Returns ``(answers, asked)``. With the
    plugin absent (the core-only job) there is no viva family to ask, and ``asked`` stays empty."""
    import importlib

    try:
        sac = importlib.import_module("vivarium_workbench_sms.sms_api_client")
    except ImportError:
        sac = None
    answers: list = []
    asked: list = []

    class Client:
        def __init__(self, base):
            self.base = base

        def health_v1(self):
            asked.append(self.base)
            return answers[0]

    if sac is not None:
        monkeypatch.setattr(sac.SmsApiClient, "for_", classmethod(lambda cls, kind, base=None, **k: Client(base)))
    return answers, asked


def test_a_compose_api_record_carries_the_identity_from_health(compose_health):
    # compose-api identity (deliberate change): was server_id None, compose-api had no identity route.
    answers, asked = compose_health
    answers.append({"version": "1", "docs": "x", "deployment_id": "  c0ffee-1  "})
    assert records.remote_record("compose-api", "compose-1", "http://C.test/")["server_id"] == "c0ffee-1"
    assert asked == ["http://C.test/"]
    assert records.cached_server_id("http://c.test", kind="compose-api") == "c0ffee-1"
    assert records.cached_server_id("http://c.test") is None  # the viva family's entry is untouched


@pytest.mark.parametrize("health", [{"version": "1", "docs": "x"}, {"version": "1", "deployment_id": "   "}])
def test_a_compose_api_without_deployment_id_records_none(compose_health, health):
    answers, _ = compose_health
    answers.append(health)
    assert records.remote_record("compose-api", "compose-1", "http://c.test")["server_id"] is None


def test_an_unreachable_compose_api_records_none_and_is_not_asked_again_at_once(compose_health):
    from compose_api_client.ext import NetworkError

    from vivarium_workbench.lib import backend_config

    answers, asked = compose_health
    answers.extend([NetworkError("cannot reach http://c.test"), {"deployment_id": "c-1"}])
    assert records.remote_record("compose-api", "compose-1", "http://c.test")["server_id"] is None
    assert records.fetch_server_id("http://c.test", kind="compose-api") is None  # remembered, not re-asked
    assert len(asked) == 1
    records._FAILED[records._key("http://c.test", "compose-api")] -= records._IDENTITY_FAIL_TTL + 1
    backend_config.link("http://c.test").mark_up()  # ...and the breaker's own probe has found it back up
    assert records.fetch_server_id("http://c.test", kind="compose-api") == "c-1"
    assert len(asked) == 2


def test_an_unreachable_compose_api_opens_the_circuit_breaker(compose_health):
    from compose_api_client.ext import NetworkError

    from vivarium_workbench.lib import backend_config, backends

    answers, _ = compose_health
    answers.append(NetworkError("cannot reach http://c.test"))
    with pytest.raises(NetworkError):
        backends.COMPOSE.identity("http://c.test")
    assert backend_config.link("http://c.test").state == backend_config.STATE_DOWN


def test_an_error_answer_from_compose_api_does_not_open_the_circuit_breaker(compose_health):
    """429 and 502/503/504 are NetworkErrors in the client's hierarchy, but the server answered (review finding)."""
    from compose_api_client.ext import RateLimitedError, ServiceUnavailableError

    from vivarium_workbench.lib import backend_config, backends

    answers, _ = compose_health
    for err in (RateLimitedError("429"), ServiceUnavailableError("503")):
        answers[:] = [err]
        with pytest.raises(type(err)):
            backends.COMPOSE.identity("http://c.test")
        assert backend_config.link("http://c.test").state != backend_config.STATE_DOWN


def test_an_open_circuit_breaker_asks_compose_api_nothing(compose_health, monkeypatch):
    from vivarium_workbench.lib import backend_config, backends

    answers, asked = compose_health
    answers.append({"deployment_id": "c-1"})
    lk = backend_config.link("http://c.test")
    lk.mark_down("down")
    monkeypatch.setattr(lk, "probe", lambda: False)  # the half-open probe (if due) finds it still down
    with pytest.raises(backend_config.CircuitOpen):
        backends.COMPOSE.identity("http://c.test")
    assert asked == []
    assert records.fetch_server_id("http://c.test", kind="compose-api") is None  # records never raises


@pytest.mark.plugin
def test_compose_api_and_viva_core_at_one_url_never_cross_identities(compose_health, viva_health):
    """A backend named at a URL a viva-api also answers on: each kind asks its own server, and caches its own."""
    import time

    c_answers, c_asked = compose_health
    v_answers, v_asked = viva_health
    c_answers.append({"deployment_id": "compose-dep"})
    v_answers.append({"deployment_id": "viva-dep"})
    url = "http://shared:8000"
    assert records.remote_record("compose-api", "compose-1", url)["server_id"] == "compose-dep"
    assert records.remote_record("viva-core", "simulation-X", url)["server_id"] == "viva-dep"
    assert records.remote_record("sms-legacy", 12, url)["server_id"] == "viva-dep"  # one viva-api: one entry
    assert (len(c_asked), len(v_asked)) == (1, 1)
    assert records.cached_server_id(url, kind="compose-api") == "compose-dep"
    assert records.cached_server_id(url) == records.cached_server_id(url, kind="sms-legacy") == "viva-dep"
    # A failure remembered for one family is not the other's.
    records._IDENTITY.clear()
    records._FAILED[records._key(url, "compose-api")] = time.monotonic()
    assert records.fetch_server_id(url, kind="compose-api") is None
    assert records.fetch_server_id(url, kind="viva-core") == "viva-dep"


@pytest.fixture
def named_compose(monkeypatch):
    monkeypatch.setenv(NAMED, "http://shared:8000")


def test_a_compose_record_whose_identity_matches_routes_to_compose_api(ws, named_compose, compose_health,
                                                                      viva_health):
    """Identity first: the record was written through another address (a tunnel) and under another deployment
    name, but the configured compose-api reports the identity it recorded."""
    compose_health[0].append({"deployment_id": "compose-dep"})
    viva_health[0].append({"deployment_id": "viva-dep"})
    _row(ws, "remote-pending-compose-7",
         {"remote": _rec(kind="compose-api", rid="compose-7", base="http://localhost:9", dep="old-name",
                         server_id="compose-dep")})
    assert records.resolve_existing(ws, "compose-7") == records.Resolution(kind="compose-api", via="record")


def test_a_compose_record_from_another_compose_deployment_is_refused(ws, named_compose, compose_health):
    from vivarium_workbench.lib.backends import config as backend_cfg

    compose_health[0].append({"deployment_id": "compose-dep"})
    _row(ws, "remote-pending-compose-7",
         {"remote": _rec(kind="compose-api", rid="compose-7", base="http://shared:8000",
                         dep=backend_cfg.deployment_name(), server_id="other-compose")})
    res = records.resolve_existing(ws, "compose-7")
    assert res.refusal and res.refusal[0]["reason"] == "other-backend" and "other-compose" in res.refusal[0]["error"]
    assert len(compose_health[1]) == 2  # asked once more, bypassing the cache, before refusing


@pytest.mark.plugin
def test_a_compose_record_carrying_the_viva_identity_is_not_routed_by_it(ws, named_compose, compose_health,
                                                                         viva_health):
    """At a shared URL, the viva-api's identity is not compose-api's: a compose record that names it is refused."""
    from vivarium_workbench_sms import remote_pinned

    compose_health[0].append({"deployment_id": "compose-dep"})
    viva_health[0].append({"deployment_id": "viva-dep"})
    assert records.fetch_server_id("http://shared:8000") == "viva-dep"  # the viva family's entry is filled first
    _row(ws, "remote-pending-compose-7",
         {"remote": _rec(kind="compose-api", rid="compose-7", base="http://shared:8000",
                         dep=remote_pinned.remote_deployment_name(), server_id="viva-dep")})
    res = records.resolve_existing(ws, "compose-7")
    assert res.refusal and "compose-dep" in res.refusal[0]["error"]


@pytest.mark.parametrize("module, kind, rid", [
    # step P1b (patch target moved): remote_run_viva_v1 is in the SMS plugin package now.
    pytest.param("vivarium_workbench_sms.remote_run_viva_v1", "viva-core", "simulation-D55", marks=pytest.mark.plugin),
    ("vivarium_workbench.lib.remote_run_compose", "compose-api", "compose-7"),
])
def test_the_document_backends_placeholders_record_where_the_run_went(ws, monkeypatch, module, kind, rid):
    import importlib

    from vivarium_workbench.lib.backends import config as backend_cfg

    mod = importlib.import_module(module)
    monkeypatch.setattr(mod.study_spec, "study_dir", lambda w, s: ws / "studies" / s)
    monkeypatch.setattr(records, "fetch_server_id", lambda url, **_: None)  # no network: neither reports one here
    mod._record_pending(ws, "s", "c", rid, "http://named.test")
    conn = sqlite3.connect(ws / "studies" / "s" / "runs.db")
    params = json.loads(conn.execute("SELECT params_json FROM runs_meta").fetchone()[0])
    conn.close()
    assert params["remote"] == {**_rec(kind=kind, rid=rid, base="http://named.test"),
                                "deployment": backend_cfg.deployment_name()}
    assert params["simulation_id"] == rid  # the existing keys stay


@pytest.mark.plugin
def test_land_remote_run_records_the_run_and_keeps_its_id(tmp_path, monkeypatch):
    """The record is kept out of the run id's hash: the same landing with a different record gets the same id."""
    _make_remote_zarr_tar_with_analysis = _sibling("test_remote_run_landing")._make_remote_zarr_tar_with_analysis
    from vivarium_workbench_sms.sms_landing import land_remote_run

    monkeypatch.setattr(cr.time, "time", lambda: 1_700_000_000.0)  # the id embeds the second it was made
    ws_root = tmp_path / "workspace"
    ids, dbs = [], []
    for name, url in (("a", "http://a.test"), ("b", "http://b.test")):
        study = ws_root / "studies" / name
        study.mkdir(parents=True)
        tar = _make_remote_zarr_tar_with_analysis(tmp_path / name)
        ids.append(land_remote_run(study, spec_id="s", simulation_id=115, experiment_id="e", commit="c",
                                   tar_path=tar, ws_root=ws_root, base_url=url))
        dbs.append(study / "runs.db")
    assert ids[0] == ids[1]
    conn = sqlite3.connect(dbs[1])
    params = json.loads(conn.execute("SELECT params_json FROM runs_meta WHERE run_id = ?", (ids[1],)).fetchone()[0])
    conn.close()
    assert params["remote"]["base_url"] == "http://b.test"
    assert params["remote"]["kind"] == "sms-legacy" and params["remote"]["id"] == "115"


# --- finding records --------------------------------------------------------------------------------------------

def test_find_records_is_read_only_and_survives_bad_rows(ws):
    _row(ws, "remote-pending-12", {"source": "x", "simulation_id": 12, "remote": _rec()})
    conn = sqlite3.connect(ws / "studies" / "s" / "runs.db")
    conn.execute("UPDATE runs_meta SET params_json = params_json")  # keep one valid row
    conn.execute("INSERT INTO runs_meta (run_id, spec_id, params_json, started_at, status) "
                 "VALUES ('bad', 'c', '{not json', 0, 'x')")
    conn.commit()
    conn.close()
    (ws / "studies" / "empty").mkdir()  # no runs.db: must not be created
    found = records.find_records(ws, " 12 ")
    assert [f.run_id for f in found] == ["remote-pending-12"] and found[0].record == _rec()
    assert not (ws / "studies" / "empty" / "runs.db").exists()


def test_find_records_matches_an_integer_legacy_simulation_id(ws):
    _row(ws, "landed", {"source": "prod", "simulation_id": 12, "backend": "ray"})
    found = records.find_records(ws, "12")
    assert found and found[0].record is None and found[0].legacy_source == "prod"


# --- deciding ---------------------------------------------------------------------------------------------------

@pytest.fixture
def stanford(monkeypatch):
    monkeypatch.setenv(SMS, "http://api:8000")


@pytest.mark.plugin
def test_a_record_on_the_configured_backend_resolves_by_its_kind(ws, stanford):
    _row(ws, "remote-pending-12", {"remote": _rec()})
    assert records.resolve_existing(ws, "12") == records.Resolution(kind="sms-legacy", via="record")


@pytest.mark.plugin
def test_the_records_kind_wins_over_the_ids_form(ws, stanford):
    """An all-digit id reads as an SMS simulation by its form; a record saying viva-core decides."""
    _row(ws, "remote-pending-123", {"remote": _rec(kind="viva-core", rid="123")})
    assert records.resolve_existing(ws, "123") == records.Resolution(kind="viva-core", via="record")


@pytest.mark.plugin
def test_a_record_on_another_url_is_refused_without_a_network_call(ws, stanford, monkeypatch):
    monkeypatch.setattr(records, "fetch_server_id", lambda u, **_: pytest.fail("network"))
    _row(ws, "remote-pending-12", {"remote": _rec(base="http://other:8000", dep="aws")})
    res = records.resolve_existing(ws, "12")
    assert res.refusal and res.refusal[1] == 409 and res.refusal[0]["reason"] == "other-backend"


@pytest.mark.plugin
def test_a_record_from_another_deployment_on_the_same_url_is_refused_by_identity(ws, stanford, monkeypatch):
    monkeypatch.setattr(records, "fetch_server_id", lambda u, **_: "prod")
    asked = []
    monkeypatch.setattr(records, "refresh_server_id", lambda u, **_: asked.append(u) or "prod")
    _row(ws, "remote-pending-12", {"remote": _rec(server_id="stanford-test")})
    res = records.resolve_existing(ws, "12")
    assert res.refusal and "stanford-test" in res.refusal[0]["error"] and asked  # re-asked before refusing


@pytest.mark.plugin
def test_a_stale_identity_cache_does_not_refuse(ws, stanford, monkeypatch):
    monkeypatch.setattr(records, "fetch_server_id", lambda u, **_: "prod")
    monkeypatch.setattr(records, "refresh_server_id", lambda u, **_: "stanford-test")
    _row(ws, "remote-pending-12", {"remote": _rec(server_id="stanford-test")})
    assert records.resolve_existing(ws, "12").kind == "sms-legacy"


@pytest.mark.plugin
@pytest.mark.parametrize("now", [None, "stanford-test"])
def test_an_identity_missing_on_either_side_falls_back_to_the_url(ws, stanford, monkeypatch, now):
    monkeypatch.setattr(records, "fetch_server_id", lambda u, **_: now)
    _row(ws, "remote-pending-12", {"remote": _rec(server_id=None if now else "stanford-test")})
    assert records.resolve_existing(ws, "12").kind == "sms-legacy"


@pytest.mark.plugin
def test_one_id_recorded_on_two_backends_picks_the_configured_one(ws, stanford):
    _row(ws, "a", {"remote": _rec(base="http://other:8000", dep="aws")})
    _row(ws, "b", {"remote": _rec()})
    assert records.resolve_existing(ws, "12").kind == "sms-legacy"


@pytest.mark.plugin
def test_one_id_recorded_on_two_other_backends_is_another_backends(ws, stanford):
    """Neither record is this workbench's backend: refused as another backend's run, not as ambiguous."""
    _row(ws, "a", {"remote": _rec(base="http://x:1", dep="x")})
    _row(ws, "b", {"remote": _rec(base="http://y:1", dep="y")})
    assert records.resolve_existing(ws, "12").refusal[0]["reason"] == "other-backend"


@pytest.mark.plugin
def test_the_named_study_is_searched_first(ws, stanford):
    _row(ws, "a", {"remote": _rec(base="http://other:8000", dep="aws")}, study="t")
    _row(ws, "b", {"remote": _rec()})
    assert records.resolve_existing(ws, "12", study_db=ws / "studies" / "s" / "runs.db").kind == "sms-legacy"
    assert records.resolve_existing(ws, "12", study_db=ws / "studies" / "t" / "runs.db").refusal


def test_a_legacy_row_from_another_deployment_is_refused(ws, stanford):
    _row(ws, "landed", {"source": "aws-prod", "simulation_id": 12, "backend": "ray"})
    res = records.resolve_existing(ws, "12")
    assert res.refusal and res.via == "legacy-guard"


@pytest.mark.plugin
@pytest.mark.parametrize("source", ["smsvpctest", "api"])
def test_a_legacy_row_this_configuration_could_have_written_proceeds(ws, stanford, source):
    _row(ws, "landed", {"source": source, "simulation_id": 12, "backend": "ray"})
    assert records.resolve_existing(ws, "12") == records.Resolution(kind="sms-legacy", via="legacy-guard")


@pytest.mark.plugin
def test_a_live_listed_id_colliding_with_an_old_row_proceeds(ws, stanford):
    from vivarium_workbench_sms import remote_pinned

    _row(ws, "landed", {"source": "aws-prod", "simulation_id": 12, "backend": "ray"})
    res = records.resolve_existing(ws, "12", deployment=remote_pinned.remote_deployment_name())
    assert res.kind == "sms-legacy" and res.refusal is None


@pytest.mark.parametrize("rid, kind", [pytest.param("12", "sms-legacy", marks=pytest.mark.plugin),
                                       pytest.param("simulation-X", "viva-core", marks=pytest.mark.plugin),
                                       ("compose-3", "compose-api"), ("", None)])
def test_nothing_stored_falls_back_to_the_ids_form(ws, stanford, rid, kind):
    assert records.resolve_existing(ws, rid) == records.Resolution(kind=kind, via="id-form")


# --- review round 1 (step 1c-1 implementation) ------------------------------------------------------------------

@pytest.fixture
def no_identity(monkeypatch):
    monkeypatch.setattr(records, "fetch_server_id", lambda url, **_: None)
    monkeypatch.setattr(records, "refresh_server_id", lambda url, **_: None)


@pytest.mark.plugin
def test_same_url_other_deployment_name_is_refused_without_identity(ws, stanford, no_identity, monkeypatch):
    """stanford-test vs prod: same URL; with no server identity the recorded name must match."""
    monkeypatch.setenv("VIVARIUM_WORKBENCH_REMOTE_DEPLOYMENT", "prod")
    _row(ws, "remote-pending-12", {"remote": _rec(dep="stanford-test")})
    res = records.resolve_existing(ws, "12")
    assert res.refusal and res.refusal[0]["reason"] == "other-backend"


@pytest.mark.plugin
def test_matching_identity_accepts_even_if_the_name_changed(ws, stanford, monkeypatch):
    monkeypatch.setattr(records, "fetch_server_id", lambda url, **_: "st")
    monkeypatch.setenv("VIVARIUM_WORKBENCH_REMOTE_DEPLOYMENT", "renamed")
    _row(ws, "remote-pending-12", {"remote": _rec(dep="smsvpctest", server_id="st")})
    assert records.resolve_existing(ws, "12").kind == "sms-legacy"


def test_a_deployment_hint_that_matches_no_record_uses_no_record(ws, stanford, no_identity):
    _row(ws, "a", {"source": "stanford-test", "simulation_id": 12, "backend": "ray"})
    _row(ws, "b", {"remote": _rec()}, study="t")
    res = records.resolve_existing(ws, "12", deployment="stanford-test")
    assert res.refusal and res.refusal[0]["dispatched_to"] == "stanford-test"


def test_a_deployment_hint_this_workbench_could_not_have_written_is_refused(ws, stanford, no_identity):
    _row(ws, "a", {"source": "smsvpctest", "simulation_id": 12, "backend": "ray"})
    assert records.resolve_existing(ws, "12", deployment="aws").refusal


@pytest.mark.plugin
def test_one_run_recorded_with_and_without_identity_is_one_run(ws, stanford, monkeypatch):
    monkeypatch.setattr(records, "fetch_server_id", lambda url, **_: "st")
    _row(ws, "a", {"remote": _rec(server_id=None)})
    _row(ws, "b", {"remote": _rec(server_id="st")}, study="t")
    assert records.resolve_existing(ws, "12").kind == "sms-legacy"


@pytest.mark.plugin
def test_several_records_with_a_deployment_hint_are_ambiguous(ws, stanford, no_identity):
    _row(ws, "a", {"remote": _rec()})
    _row(ws, "b", {"remote": _rec(kind="viva-core")}, study="t")
    assert records.resolve_existing(ws, "12", deployment="smsvpctest").refusal[0]["reason"] == "ambiguous"


@pytest.mark.parametrize("params", [
    {"source": "aws", "backend": "viva-v1", "viva_v1": {"run_id": "simulation-X"}},
    {"source": "aws", "backend": "compose-api", "compose": {"run_id": "simulation-X"}},
])
def test_landed_document_runs_from_before_1c_are_found_by_their_own_blocks(ws, stanford, params):
    _row(ws, "landed", params)
    found = records.find_records(ws, "simulation-X")
    assert found and found[0].legacy_source == "aws"
    assert records.resolve_existing(ws, "simulation-X").refusal


def test_remote_is_not_shown_in_study_detail_or_manifests(ws):
    from vivarium_workbench.lib import study_spec

    _row(ws, "r1", {"source": "x", "simulation_id": 1, "remote": _rec(rid="1")})
    manifest = cr.build_run_manifest(spec_id="c", params={"a": 1, "remote": _rec()}, n_steps=1, emitter="sqlite",
                                     emit_paths=[], runtime={}, origin="test")
    assert "remote" not in manifest["params"] and manifest["params"]["a"] == 1
    rows = study_spec.read_runs_db_for_study(ws, "s") if hasattr(study_spec, "read_runs_db_for_study") else []
    assert all("remote" not in (r.get("params") or {}) for r in rows)


@pytest.mark.plugin
def test_the_same_deployment_behind_another_url_is_accepted_by_identity(ws, stanford, monkeypatch):
    """A tunnel or the in-cluster name: another address, the same server identity -- the same deployment."""
    monkeypatch.setattr(records, "fetch_server_id", lambda url, **_: "st")
    _row(ws, "remote-pending-12", {"remote": _rec(base="http://localhost:8080", server_id="st")})
    assert records.resolve_existing(ws, "12").kind == "sms-legacy"


# --- each writer records the URL its request used ----------------------------------------------------------------

@pytest.mark.plugin
def test_sms_submit_and_land_record_the_clients_url(monkeypatch, tmp_path):
    from vivarium_workbench.lib import remote_run_views as rrv
    from vivarium_workbench_sms import sms_landing, sms_legacy

    monkeypatch.setattr(records, "fetch_server_id", lambda url, **_: None)
    seen = {}

    class Client:
        base_url = "http://sms.client"

        def __init__(self, *a, **k):
            pass

        def run_simulation(self, **kw):
            return {"database_id": 7}

        def get_simulation(self, sid):
            return {"simulator_id": None}

        def download_data(self, sid, dest, timeout=None):
            p = Path(dest) / "t.tar.gz"
            p.write_bytes(b"x")
            return p

    spec = tmp_path / "study.yaml"
    spec.write_text("baseline: [{composite: c}]\n")
    monkeypatch.setattr(sms_legacy, "SmsApiClient", Client)
    monkeypatch.setattr(sms_legacy, "_sms_api_base", lambda: "http://configured")
    monkeypatch.setattr(sms_legacy, "_run_auth_ok", lambda: True)
    monkeypatch.setattr(sms_legacy, "load_spec", lambda p: {"baseline": [{"composite": "c"}]})
    monkeypatch.setattr(study_spec, "study_spec_path", lambda ws, name: spec)
    monkeypatch.setattr(study_spec, "study_dir", lambda ws, name: tmp_path)
    monkeypatch.setattr(study_spec, "collect_study_observables", lambda s: [])
    monkeypatch.setattr(sms_landing, "land_remote_run", lambda *a, **k: seen.update(k) or "r1")

    assert sms_legacy.submit_tail(tmp_path, {"study": "s", "simulator_id": 1, "num_generations": 1,
                                             "num_seeds": 1})[1] == 202
    conn = sqlite3.connect(tmp_path / "runs.db")
    params = json.loads(conn.execute("SELECT params_json FROM runs_meta").fetchone()[0])
    conn.close()
    assert params["remote"]["base_url"] == "http://sms.client" and params["remote"]["id"] == "7"

    assert sms_legacy.land_tail(tmp_path, {"study": "s", "simulation_id": 7})[1] == 200
    assert seen["base_url"] == "http://sms.client"


@pytest.mark.plugin
def test_the_pipeline_lands_with_its_clients_url(tmp_path):
    jobs = _sibling("test_remote_run_jobs")
    _ctx, _FakeClient = jobs._ctx, jobs._FakeClient
    from vivarium_workbench_sms.remote_run_jobs import RemoteRunJob, run_remote_pipeline

    client = _FakeClient()
    client.base_url = "http://pipeline.client"
    ctx, landed = _ctx(tmp_path, client)
    job = RemoteRunJob("s")
    run_remote_pipeline(job, ctx)
    assert job.error is None and landed["kw"]["base_url"] == "http://pipeline.client"


def test_the_compose_placeholder_records_the_named_url(monkeypatch, tmp_path):
    from vivarium_workbench.lib import remote_run_compose as rc

    monkeypatch.setenv(NAMED, "http://named.compose")
    assert rc._named_base() == "http://named.compose"
    monkeypatch.delenv(NAMED)
    assert rc._named_base() == ""


# --- review round 2: identity first ----------------------------------------------------------------------------

@pytest.mark.plugin
def test_two_deployments_sharing_url_and_name_are_told_apart_by_identity(ws, stanford, monkeypatch):
    """stanford-test and prod: same URL, same default name, same integer id; the server says it is prod."""
    monkeypatch.setattr(records, "fetch_server_id", lambda url, **_: "prod")
    monkeypatch.setattr(records, "refresh_server_id", lambda url, **_: "prod")
    _row(ws, "a", {"remote": _rec(server_id="stanford-test")})
    _row(ws, "b", {"remote": _rec(server_id="prod")}, study="t")
    assert records.resolve_existing(ws, "12").kind == "sms-legacy"


def test_a_mismatch_with_a_failed_refresh_falls_back_to_url_and_name(ws, stanford, monkeypatch):
    monkeypatch.setattr(records, "fetch_server_id", lambda url, **_: "prod")
    monkeypatch.setattr(records, "refresh_server_id", lambda url, **_: None)
    _row(ws, "a", {"remote": _rec(base="http://elsewhere:1", dep="aws", server_id="st")})
    assert records.resolve_existing(ws, "12").refusal


@pytest.mark.plugin
def test_one_deployment_under_two_urls_seen_from_a_third_is_one_run(ws, stanford, monkeypatch):
    monkeypatch.setattr(records, "fetch_server_id", lambda url, **_: "st")
    _row(ws, "a", {"remote": _rec(base="http://localhost:8080", server_id="st")})
    _row(ws, "b", {"remote": _rec(base="http://in-cluster:8000", server_id="st")}, study="t")
    assert records.resolve_existing(ws, "12").kind == "sms-legacy"


def test_a_malformed_remote_block_is_not_a_record(ws, stanford):
    """A local composite's own input named ``remote`` must not be read as a routing record (or crash the lookup)."""
    _row(ws, "local", {"simulation_id": 12, "remote": {"server_id": "x", "id": "12"}})
    found = records.find_records(ws, "12")
    assert found and found[0].record is None
    assert records.resolve_existing(ws, "12").refusal is None


# --- step 1c-2 --------------------------------------------------------------------------------------------------

def test_every_refusal_is_terminal_and_names_the_run(ws, stanford, no_identity):
    _row(ws, "a", {"remote": _rec(base="http://x:1", dep="x")})
    body, status = records.resolve_existing(ws, "12").refusal
    assert status == 409 and body["terminal"] is True and body["simulation_id"] == "12" and body["error"]


@pytest.mark.plugin
def test_a_local_runs_source_is_not_a_legacy_deployment(ws, stanford):
    _row(ws, "local", {"source": "somewhere", "simulation_id": 12})  # no remote backend label
    assert records.resolve_existing(ws, "12") == records.Resolution(kind="sms-legacy", via="id-form")


@pytest.mark.plugin
def test_a_mismatch_refreshes_at_most_once_per_ttl(ws, stanford, monkeypatch):
    from vivarium_workbench_sms import sms_api_client as sac

    asked = []

    class Client:
        def health_v1(self):
            asked.append(1)
            return {"deployment_id": "prod"}

    monkeypatch.setattr(sac.SmsApiClient, "for_", classmethod(lambda cls, kind, base=None, **k: Client()))
    _row(ws, "a", {"remote": _rec(server_id="stanford-test")})
    for _ in range(5):
        assert records.resolve_existing(ws, "12").refusal
    assert len(asked) == 2  # the first fetch, then one forced re-ask; the next four lookups ask nothing


@pytest.mark.plugin
def test_a_tunnel_repointed_after_the_cache_filled_does_not_refuse_its_runs(ws, stanford, monkeypatch):
    """Unpatched end to end: the cache says prod; the tunnel now reaches stanford-test; a stanford-test run must
    resolve (the forced re-ask sees the new identity) -- review finding on step 1c-2."""
    from vivarium_workbench_sms import sms_api_client as sac

    answers = ["prod", "stanford-test"]

    class Client:
        def health_v1(self):
            return {"deployment_id": answers.pop(0) if len(answers) > 1 else answers[0]}

    monkeypatch.setattr(sac.SmsApiClient, "for_", classmethod(lambda cls, kind, base=None, **k: Client()))
    assert records.fetch_server_id("http://api:8000") == "prod"
    _row(ws, "a", {"remote": _rec(server_id="stanford-test")})
    assert records.resolve_existing(ws, "12").kind == "sms-legacy"


@pytest.mark.plugin
def test_a_down_server_is_not_asked_by_every_lookup(ws, stanford, monkeypatch):
    from vivarium_workbench_sms import sms_api_client as sac

    asked = []

    class Client:
        def health_v1(self):
            asked.append(1)
            raise RuntimeError("down")

    monkeypatch.setattr(sac.SmsApiClient, "for_", classmethod(lambda cls, kind, base=None, **k: Client()))
    _row(ws, "a", {"remote": _rec(server_id="st")})
    for _ in range(5):
        records.resolve_existing(ws, "12")
    assert len(asked) == 1


# --- compose_health_identity through the REAL compose-api client (review of wb#1665) --------------------------------

def _stub_compose(body, *, delay=0.0):
    """A local HTTP server answering GET /health like compose-api (compose_api/api/main.py check_health)."""
    import json as _json
    import threading
    import time as _time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if delay:
                _time.sleep(delay)
            data = _json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def test_compose_health_identity_reads_deployment_id_with_the_real_client():
    from vivarium_workbench.lib.backends import compose_api

    srv, url = _stub_compose({"docs": "x/docs", "version": "0.7.93", "deployment_id": " dep-1 "})
    try:
        assert compose_api.compose_health_identity(url) == "dep-1"
    finally:
        srv.shutdown()
    srv, url = _stub_compose({"docs": "x/docs", "version": "0.7.91"})  # a compose-api from before the field
    try:
        assert compose_api.compose_health_identity(url) is None
    finally:
        srv.shutdown()


def test_a_slow_compose_health_does_not_mark_the_link_down(monkeypatch):
    """A /health that does not answer in time raises (so records remembers the failure) but leaves the breaker
    alone: a slow server is not a down one, and a down link would refuse new runs."""
    from vivarium_workbench.lib import backend_config
    from vivarium_workbench.lib.backends import compose_api

    monkeypatch.setattr(compose_api, "IDENTITY_TIMEOUT", 0.2)
    srv, url = _stub_compose({"deployment_id": "late"}, delay=1.0)
    try:
        with pytest.raises(Exception):
            compose_api.compose_health_identity(url)
        assert backend_config.link(url).state != backend_config.STATE_DOWN
    finally:
        srv.shutdown()


def test_a_refused_compose_connection_marks_the_link_down():
    import socket

    from vivarium_workbench.lib import backend_config
    from vivarium_workbench.lib.backends import compose_api

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing listens here now
    url = f"http://127.0.0.1:{port}"
    with pytest.raises(Exception):
        compose_api.compose_health_identity(url)
    assert backend_config.link(url).state == backend_config.STATE_DOWN
