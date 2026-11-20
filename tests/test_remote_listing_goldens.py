"""Goldens for the remote-runs listing (step P2, Remote tab R1), taken on the base BEFORE the listing facet.

Providers on, a fake SMS client: the ``/api/simulations`` payload and the client's outbound call sequence on
``?refresh=true``, the default stale-while-revalidate path after warming, ``include_remote=false``, the
``/api/investigations`` study counts, and ``?refresh=true``'s cache flush (stale vs unavailable). These must hold,
byte for byte, before and after the move behind ``Backend.listing``.
"""
from __future__ import annotations

import threading
import time

import pytest

import vivarium_workbench_sms.remote_simulations as rs
from vivarium_workbench.lib import simulations_index as si

BASE = "http://sms.golden.test"
REPO = "https://github.com/acme/model"

# Two builds of the workspace's repo, one of another repo.
BUILDS = {"versions": [
    {"database_id": 7, "git_repo_url": REPO + ".git", "git_commit_hash": "aaaaaaa1"},
    {"database_id": 8, "git_repo_url": "git@github.com:Acme/Model.git", "git_commit_hash": "bbbbbbb2"},
    {"database_id": 9, "git_repo_url": "https://github.com/other/thing", "git_commit_hash": "ccccccc3"},
]}


def _sim(db_id, sim_id, exp, *, ts, out_uri="s3://bucket/x", emitter="parquet"):
    return {"database_id": db_id, "simulator_id": sim_id, "experiment_id": exp, "last_updated": ts,
            "config": {"description": f"desc {exp}", "emitter": emitter,
                       "emitter_arg": {"out_uri": f"{out_uri}/{exp}"} if out_uri else {},
                       "aws": {"batch_queue": "q-golden"}}}


SIMS = [
    _sim(101, 7, "exp-a-1", ts="2026-01-02T03:04:05Z"),
    _sim(102, 8, "exp-a-2", ts="2026-01-03T03:04:05Z"),
    _sim(103, 7, "exp-b-1", ts="2026-01-04T03:04:05Z", out_uri=None),
    _sim(104, 9, "exp-other-done", ts="2026-01-05T03:04:05Z"),       # another repo, finished: dropped
    _sim(105, 9, "exp-other-running", ts="2026-01-06T03:04:05Z"),    # another repo, active: kept
]
STATUS = {101: "completed", 102: "failed", 103: "running", 104: "completed", 105: "queued"}


class _Fake:
    calls: list = []
    lock = threading.Lock()
    down = False

    def __init__(self, base_url="", timeout=30.0, max_retries=3):
        self._rec(("init", base_url, timeout, max_retries))

    @classmethod
    def _rec(cls, call):
        with cls.lock:
            cls.calls.append(call)

    def list_simulators(self):
        self._rec(("list_simulators",))
        if type(self).down:
            raise RuntimeError("tunnel down")
        return BUILDS

    def list_build_simulations(self, simulator_id):
        self._rec(("list_build_simulations", simulator_id))
        return [dict(s) for s in SIMS]

    def simulation_status(self, d):
        self._rec(("simulation_status", d))
        return {"status": STATUS[d]}


def _drain(timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with rs._REMOTE_REFRESH_LOCK:
            if not rs._REMOTE_REFRESH_INFLIGHT:
                return
        time.sleep(0.02)


@pytest.fixture
def fake(monkeypatch):
    _Fake.calls = []
    _Fake.down = False
    monkeypatch.setattr("vivarium_workbench_sms.sms_api_client.SmsApiClient", _Fake)
    monkeypatch.setattr("vivarium_workbench_sms.sms_api_client.sms_api_base", lambda: BASE)
    monkeypatch.setattr("vivarium_workbench.lib.git_status.remote_repo_url", lambda ws: REPO)
    monkeypatch.setattr("vivarium_workbench.lib.backend_config.deployment_name", lambda default=None: "golden-dep")
    for c in (rs._REMOTE_CACHE, rs._REMOTE_META):
        c.clear()
    si.clear_build_cache()
    yield _Fake
    _drain()
    for c in (rs._REMOTE_CACHE, rs._REMOTE_META):
        c.clear()
    si.clear_build_cache()


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "workspace.yaml").write_text(
        "name: golden\nremote_run_study_map:\n  - {pattern: '^exp-a', study: study-a}\n"
        "  - {pattern: '^exp-b', study: study-b}\n", encoding="utf-8")
    for slug, inv in (("study-a", "inv-1"), ("study-b", None)):
        d = tmp_path / "studies" / slug
        d.mkdir(parents=True)
        body = f"name: {slug}\ntitle: {slug}\ncomposite: comp-x\n" +(f"investigation: {inv}\n" if inv else "")
        (d / "study.yaml").write_text(body, encoding="utf-8")
    return tmp_path


def _ts(iso):
    import datetime as dt

    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def _row(exp, db_id, status, iso, *, study=None, inv=None, out_uri=True):
    uri = f"s3://bucket/x/{exp}" if out_uri else None
    return {"run_id": exp, "status": status, "started_at": _ts(iso), "completed_at": _ts(iso),
            "store_path": uri, "emitter_type": "Parquet", "sim_name": f"desc {exp}",
            "study_slug": study, "studies": [study] if study else [], "investigation_slug": inv,
            # The SimRow model keeps neither the row's ``source: "remote"`` nor ``remote_origin.build``.
            "source": None,
            "remote_origin": {"deployment": "golden-dep", "simulation_id": db_id, "experiment_id": exp,
                              "backend": "q-golden", "s3_uri": uri,
                              # Step P3 F0 (additive): a listed run's features are its listing backend's.
                              "features": ["analyses", "artifacts"]}}


#: The merged rows, in the payload's order (active runs pinned first, then newest-first).
GOLDEN_ROWS = [
    _row("exp-other-running", 105, "queued", "2026-01-06T03:04:05Z"),
    _row("exp-b-1", 103, "running", "2026-01-04T03:04:05Z", study="study-b", out_uri=False),
    _row("exp-a-2", 102, "failed", "2026-01-03T03:04:05Z", study="study-a", inv="inv-1"),
    _row("exp-a-1", 101, "completed", "2026-01-02T03:04:05Z", study="study-a", inv="inv-1"),
]

#: The build the list is seeded with: the first of the matching set {7, 8} in set order (8 hashes to slot 0).
SEED = 8

#: The outbound calls of one fetch (the status enrichment runs concurrently, so it is compared as a set).
GOLDEN_FRESH_CALLS = [
    ("init", BASE, rs._FRESH_TIMEOUT, rs._FRESH_MAX_RETRIES),
    ("list_simulators",),
    ("list_build_simulations", SEED),
    ("init", BASE, rs._STATUS_CALL_TIMEOUT_S, 3),
]
#: A background (stale-while-revalidate) refresh uses the default 30 s x 3 client.
GOLDEN_BACKGROUND_CALLS = [("init", BASE, 30.0, 3), *GOLDEN_FRESH_CALLS[1:]]
GOLDEN_STATUS_CALLS = {("simulation_status", d) for d in (101, 102, 103, 104, 105)}


def _project(rows):
    keys = GOLDEN_ROWS[0].keys()
    return [{k: r.get(k) for k in keys} for r in rows]


def _split(calls):
    return ([c for c in calls if c[0] != "simulation_status"],
            {c for c in calls if c[0] == "simulation_status"})


def test_refresh_payload_and_outbound_calls(fake, ws, inproc_client):
    r = inproc_client(ws).get("/api/simulations?refresh=true")
    assert r.status_code == 200
    body = r.json()
    assert _project(body["simulations"]) == GOLDEN_ROWS
    assert body["total"] == 4
    assert (body["remote"]["state"], body["remote"]["error"]) == ("fresh", None)
    assert isinstance(body["remote"]["as_of"], float)
    seq, status = _split(fake.calls)
    assert seq == GOLDEN_FRESH_CALLS
    assert status == GOLDEN_STATUS_CALLS


def test_default_path_after_warming_serves_the_cache_without_asking(fake, ws, inproc_client):
    client = inproc_client(ws)
    first = client.get("/api/simulations")  # cold: empty, a background refresh kicked
    assert first.status_code == 200
    assert first.json()["remote"]["state"] == "refreshing"
    assert [s["run_id"] for s in first.json()["simulations"]] == []
    _drain()
    seq, status = _split(fake.calls)
    # The background refresh uses the default (30 s x 3) client.
    assert seq == GOLDEN_BACKGROUND_CALLS
    assert status == GOLDEN_STATUS_CALLS
    fake.calls.clear()
    si.clear_build_cache()
    warm = client.get("/api/simulations")
    body = warm.json()
    assert _project(body["simulations"]) == GOLDEN_ROWS
    assert body["remote"]["state"] == "fresh"
    assert fake.calls == []  # served from the cache: nothing asked


def test_include_remote_false_lists_no_remote_rows(fake, ws, inproc_client):
    body = inproc_client(ws).get("/api/simulations?include_remote=false").json()
    assert body["simulations"] == [] and body["remote"] is None
    _drain()
    # Not a fetch of this listing's own: the analysis-tool run picker (``analysis_tools._run_candidates``, reached
    # through ``_attach_matched_tools``) builds the index WITH remote rows, and that stale-while-revalidate read
    # kicks one background refresh. Pinned as-is.
    seq, status = _split(fake.calls)
    assert seq == GOLDEN_BACKGROUND_CALLS and status == GOLDEN_STATUS_CALLS


def test_investigations_study_counts(fake, ws, inproc_client):
    from vivarium_workbench.lib.investigations_index import _epoch_to_iso

    client = inproc_client(ws)
    client.get("/api/simulations?refresh=true")  # warm the stale-while-revalidate cache
    fake.calls.clear()
    rows = {r["name"]: r for r in client.get("/api/investigations").json()["investigations"]}
    assert (rows["study-a"]["n_runs"], rows["study-b"]["n_runs"]) == (2, 1)
    assert rows["study-a"]["last_run"] == _epoch_to_iso(_ts("2026-01-03T03:04:05Z"))
    assert rows["study-b"]["last_run"] == _epoch_to_iso(_ts("2026-01-04T03:04:05Z"))
    assert fake.calls == []  # the warm cache answered


def test_investigations_without_a_study_map_count_no_remote_runs(fake, ws, inproc_client):
    (ws / "workspace.yaml").write_text("name: golden\n", encoding="utf-8")
    client = inproc_client(ws)
    rows = {r["name"]: r for r in client.get("/api/investigations").json()["investigations"]}
    assert (rows["study-a"]["n_runs"], rows["study-b"]["n_runs"]) == (0, 0)
    _drain()
    assert fake.calls == []  # the gate: no map, no listing asked


def test_refresh_flushes_the_cache_so_a_down_server_is_unavailable_not_stale(fake, ws, inproc_client):
    client = inproc_client(ws)
    assert client.get("/api/simulations?refresh=true").json()["remote"]["state"] == "fresh"
    fake.down = True
    body = client.get("/api/simulations?refresh=true").json()
    # Without the flush the previous success would make this "stale" and keep serving its rows.
    assert body["remote"]["state"] == "unavailable"
    assert body["remote"]["as_of"] is None
    assert "tunnel down" in body["remote"]["error"]
    assert body["simulations"] == []
