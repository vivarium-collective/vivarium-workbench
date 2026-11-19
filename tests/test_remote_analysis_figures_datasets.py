"""Remote analysis figures come from viva-core's dataset registry when the backend serves it.

The registry (``GET /viva/v1/datasets?source=simulation:<id>``, ``/datasets/{id}/content``) lets the
workbench show a remote analysis's figures and ptools tables without storage credentials. S3 stays
the fallback for a backend that does not advertise ``viva-v1-datasets`` or has registered nothing for
the simulation. Row shapes below are copied from stanford-test (sms-api 0.9.194), 2026-11-18.
"""
from __future__ import annotations

import pytest

from vivarium_workbench.lib import remote_analysis_figures as raf

ADIR = "analysis-sim248-sim248-api_si-d7639e"
PREFIX = f"s3://bucket/vecoli-output/sim248-sim248-api_simulation_default-15f2/analyses/{ADIR}"


def _row(id_, kind, rel, adir=ADIR):
    return {"id": id_, "kind": kind, "uri": f"{PREFIX.replace(ADIR, adir)}/{rel}", "owner_kind": "analysis",
            "owner_id": "1315", "size_bytes": 10, "source": {"kind": "simulation", "ref": "2088"},
            "attributes": {"name": rel.rsplit("/", 1)[-1], "analysis_dir": adir, "origin": "walk"}}


class FakeClient:
    """Answers /viva/v1/datasets like viva-core: kind/source/attr.* filters, limit/offset paging."""

    def __init__(self, rows, caps=("viva-v1-datasets",), base_url="http://fake", fail_at_offset=None):
        self.rows, self.caps, self.base_url = rows, list(caps), base_url
        self.fail_at_offset = fail_at_offset
        self.calls: list = []
        self.content_calls: list = []

    def capabilities(self):
        return {"version": "test", "capabilities": self.caps}

    def list_datasets(self, params):
        self.calls.append(dict(params))
        if self.fail_at_offset is not None and int(params.get("offset", 0)) >= self.fail_at_offset:
            raise RuntimeError("GET /viva/v1/datasets -> 503 this deployment provides no dataset store")
        out = [r for r in self.rows
               if (params.get("kind") in (None, r["kind"]))
               and (params.get("q") is None or params["q"].lower() in r["uri"].lower())
               and params.get("source") == "simulation:" + r["source"]["ref"]
               and all(r["attributes"].get(k[len("attr."):]) == v
                       for k, v in params.items() if k.startswith("attr."))]
        off, lim = int(params.get("offset", 0)), int(params.get("limit", 100))
        page = out[off:off + lim]
        return {"datasets": page, "total": len(out), "limit": lim, "offset": off,
                # viva-core: a full page always offers a next one, even when it comes back empty
                "next_offset": off + lim if len(page) == lim else None}

    def dataset_content(self, dataset_id):
        self.content_calls.append(dataset_id)
        return f"bytes-of-{dataset_id}".encode()

    def list_analyses(self, sid):
        raise AssertionError("the S3 path must not be read here")

    def get_simulation(self, sid):
        raise AssertionError("the S3 path must not be read here")


def _no_s3(monkeypatch, analyses=()):
    """Stub the S3 listing: it returns ``analyses`` and records that it was asked."""
    asked = []

    def fake(client, sid):
        asked.append(sid)
        rows = list(analyses)
        return {"available": bool(rows), "reason": "ok" if rows else "no-analyses", "analyses": rows}
    monkeypatch.setattr(raf, "_list_via_s3", fake)
    return asked


@pytest.fixture(autouse=True)
def _fresh_caches(monkeypatch):
    raf._caps_cache.clear()
    raf._s3_check.clear()
    monkeypatch.setattr(raf, "_aws_creds_ok", lambda: True)   # never reach real AWS from a test
    yield
    raf._caps_cache.clear()
    raf._s3_check.clear()


def test_listing_groups_by_analysis_dir_and_hides_chromosome_views(monkeypatch):
    _no_s3(monkeypatch)
    client = FakeClient([
        _row(1, "figure", "viz/ribosome_usage__variant=0_seed=0.html"),
        _row(2, "figure", "viz/chromosome_state_view__variant=0_gen=1_agent=0.html"),
        _row(3, "ptools-analysis", "ptools/ptools_rxns__variant=0_seed=0.tsv"),
        _row(4, "figure", "viz/ptools_rxns_multiseed__variant=0.html", adir="analysis-ptools-multiseed"),
    ])
    out = raf.list_remote_analysis_figures(client, 2088)
    assert out["available"] is True and out["via"] == "datasets"
    by_name = {a["name"]: a for a in out["analyses"]}
    assert set(by_name) == {ADIR, "analysis-ptools-multiseed"}
    assert [f["path"] for f in by_name[ADIR]["figures"]] == ["viz/ribosome_usage__variant=0_seed=0.html"]
    assert [p["path"] for p in by_name[ADIR]["ptools"]] == ["ptools/ptools_rxns__variant=0_seed=0.tsv"]
    assert by_name[ADIR]["source"] == "datasets"


def test_listing_pages_through_the_registry(monkeypatch):
    _no_s3(monkeypatch)
    rows = [_row(i, "figure", f"viz/fig{i:03d}__variant=0.html") for i in range(400)]
    client = FakeClient(rows)
    out = raf.list_remote_analysis_figures(client, 2088)
    assert len(out["analyses"][0]["figures"]) == 400
    # 400 is a multiple of the page size, so viva-core offers a third, empty page; it ends the listing
    assert [c["offset"] for c in client.calls] == [0, 200, 400]
    assert all("kind" not in c for c in client.calls)
    assert "truncated" not in out


def test_listing_says_when_it_hits_the_page_cap(monkeypatch):
    _no_s3(monkeypatch)
    monkeypatch.setattr(raf, "_DATASET_MAX_PAGES", 2)
    client = FakeClient([_row(i, "figure", f"viz/f{i:03d}__v.html") for i in range(450)])
    out = raf.list_remote_analysis_figures(client, 2088)
    assert out["truncated"] is True and len(out["analyses"][0]["figures"]) == 400


def test_listing_drops_rows_repeated_across_pages(monkeypatch):
    """Offset paging over newest-change-first can return a row twice when the registry touches it."""
    _no_s3(monkeypatch)
    client = FakeClient([_row(i, "figure", f"viz/f{i:03d}__v.html") for i in range(250)])
    real = client.list_datasets

    def shifting(params):
        page = real(params)
        if params.get("offset") == 200:
            page["datasets"] = [client.rows[199]] + page["datasets"]
        return page
    client.list_datasets = shifting
    out = raf.list_remote_analysis_figures(client, 2088)
    paths = [f["path"] for f in out["analyses"][0]["figures"]]
    assert len(paths) == len(set(paths)) == 250


def test_partly_registered_simulation_keeps_its_s3_only_analyses(monkeypatch):
    """A registry that has one analysis must not hide another the S3 listing finds (partial backfill)."""
    asked = _no_s3(monkeypatch, analyses=[
        {"name": ADIR, "status": "completed", "result_uri": PREFIX, "source": "record",
         "figures": [{"path": "viz/ribosome_usage__variant=0_seed=0.html"}], "ptools": []},
        {"name": "analysis-ptools-percell", "status": "completed", "source": "s3",
         "result_uri": PREFIX.replace(ADIR, "analysis-ptools-percell"),
         "figures": [{"path": "viz/x__v.html"}], "ptools": []},
        {"name": "never-ran", "status": "failed", "result_uri": None, "figures": [], "ptools": [],
         "source": "record"},
    ])
    client = FakeClient([_row(1, "figure", "viz/ribosome_usage__variant=0_seed=0.html")])
    out = raf.list_remote_analysis_figures(client, 2088)
    assert asked == [2088]
    names = [a["name"] for a in out["analyses"]]
    assert names == [ADIR, "analysis-ptools-percell"]    # registered dir once; S3-only dir added; empty row not
    assert out["analyses"][0]["source"] == "datasets"
    assert out["via"] == "datasets+s3"


def test_rows_without_analysis_dir_are_placed_by_their_uri(monkeypatch):
    """Rows registered from run events need not carry attributes.analysis_dir."""
    _no_s3(monkeypatch)
    row = _row(5, "figure", "viz/mass__v.html")
    del row["attributes"]["analysis_dir"]
    client = FakeClient([row])
    out = raf.list_remote_analysis_figures(client, 2088)
    assert [(a["name"], [f["path"] for f in a["figures"]]) for a in out["analyses"]] == [(ADIR, ["viz/mass__v.html"])]
    assert raf.fetch_by_analysis(client, 2088, ADIR, "viz/mass__v.html") == (b"bytes-of-5", "text/html; charset=utf-8")


def test_a_failing_page_keeps_the_rows_read_before_it_and_is_reported(monkeypatch):
    _no_s3(monkeypatch)
    client = FakeClient([_row(i, "figure", f"viz/f{i:03d}__v.html") for i in range(250)], fail_at_offset=200)
    out = raf.list_remote_analysis_figures(client, 2088)
    assert out["available"] is True and len(out["analyses"][0]["figures"]) == 200
    assert out["errors"] and "503" in out["errors"][0]


def test_files_are_placed_by_folder_not_by_kind(monkeypatch):
    """The registry's ptools-analysis kind is only Omics Viewer series (ptools_*); cd1_* tables in the
    same folder are kind 'analysis'. The PTools contract is the folder, so both are listed, as on S3.
    Files outside viz/ and ptools/ (the analysis.json report) are not listed, as on the S3 path."""
    _no_s3(monkeypatch)
    client = FakeClient([
        _row(1, "ptools-analysis", "ptools/ptools_rxns__v.tsv"),
        _row(2, "analysis", "ptools/cd1_fluxomics__variant=0.tsv"),
        _row(3, "report", "analysis.json"),
        _row(4, "figure", "viz/a__v.html"),
    ])
    out = raf.list_remote_analysis_figures(client, 2088)
    row = out["analyses"][0]
    assert [p["path"] for p in row["ptools"]] == ["ptools/cd1_fluxomics__variant=0.tsv", "ptools/ptools_rxns__v.tsv"]
    assert [f["path"] for f in row["figures"]] == ["viz/a__v.html"]


def test_registry_failure_is_not_reported_as_missing_credentials(monkeypatch):
    """Registry down, S3 has nothing (no credentials): the reason names the registry."""
    _no_s3(monkeypatch)
    client = FakeClient([], fail_at_offset=0)
    out = raf.list_remote_analysis_figures(client, 2088)
    assert out["available"] is False and out["reason"] == "datasets-error"

    from vivarium_workbench.lib import remote_simulations as rs
    monkeypatch.setattr(rs, "list_remote_simulations", lambda ws, **k: [
        {"study_slug": "s", "status": "completed", "remote_origin": {"simulation_id": 2088}, "sim_name": "x"}])
    monkeypatch.setattr(raf, "_aws_creds_ok", lambda: False)
    assert raf.study_remote_figures("/ws", client=client, slug="s")["reason"] == "datasets-error"


def test_listing_falls_back_to_s3_when_nothing_is_registered(monkeypatch):
    _no_s3(monkeypatch)
    out = raf.list_remote_analysis_figures(FakeClient([]), 2088)
    assert out["via"] == "s3" and out["reason"] == "no-analyses" and out["available"] is False


def test_listing_uses_s3_when_the_capability_is_absent(monkeypatch):
    client = FakeClient([_row(1, "figure", "viz/a__v.html")], caps=())
    monkeypatch.setattr(raf, "_list_via_s3", lambda c, sid: {"available": True, "reason": "ok", "analyses": []})
    out = raf.list_remote_analysis_figures(client, 2088)
    assert out["via"] == "s3" and client.calls == []


def test_fetch_streams_the_registered_file_through_the_api():
    rel = "viz/ribosome_usage__variant=0_seed=0.html"
    client = FakeClient([_row(243206, "figure", rel), _row(7, "figure", "viz/other.html")])
    body, ctype = raf.fetch_by_analysis(client, 2088, ADIR, rel)
    assert body == b"bytes-of-243206" and ctype.startswith("text/html")
    assert client.content_calls == [243206]


def test_fetch_with_a_record_name_goes_straight_to_s3(monkeypatch):
    """A legacy record name containing '/' is not a directory name: S3 resolves it, as before."""
    client = FakeClient([])
    client.list_analyses = lambda sid: [{"name": "ptools/run 3", "result_uri": PREFIX}]
    monkeypatch.setattr(raf, "fetch_remote_analysis_object", lambda uri, rel: (b"s3", "text/plain"))
    assert raf.fetch_by_analysis(client, 2088, "ptools/run 3", "viz/a.html") == (b"s3", "text/plain")
    assert client.calls == []


def test_fetch_falls_back_to_s3_when_the_file_is_not_registered(monkeypatch):
    client = FakeClient([])
    client.list_analyses = lambda sid: [{"name": ADIR, "result_uri": PREFIX}]
    seen = {}
    monkeypatch.setattr(raf, "fetch_remote_analysis_object",
                        lambda uri, rel: seen.setdefault("args", (uri, rel)) and (b"s3", "text/plain"))
    assert raf.fetch_by_analysis(client, 2088, ADIR, "viz/a.html") == (b"s3", "text/plain")
    assert seen["args"] == (PREFIX, "viz/a.html")


@pytest.mark.parametrize("analysis,rel", [(ADIR, "../secret.html"), (ADIR, "viz/../../x"), (ADIR, "")])
def test_fetch_refuses_traversal_before_any_call(analysis, rel):
    client = FakeClient([])
    assert raf.fetch_by_analysis(client, 2088, analysis, rel) is None
    assert client.calls == [] and client.content_calls == []


def test_an_unreachable_backend_is_probed_once_not_per_figure(monkeypatch):
    client = FakeClient([])
    probes = []

    def down():
        probes.append(1)
        raise OSError("connection refused")
    client.capabilities = down
    client.list_analyses = lambda sid: []
    client.get_simulation = lambda sid: {}
    for _ in range(5):
        raf.fetch_by_analysis(client, 2088, ADIR, "viz/a.html")
    assert len(probes) == 1 and client.calls == []


def test_capability_check_is_cached_per_backend():
    client = FakeClient([_row(1, "figure", "viz/a__v.html")])
    calls = []
    real = client.capabilities
    client.capabilities = lambda: calls.append(1) or real()
    for _ in range(5):
        raf.fetch_by_analysis(client, 2088, ADIR, "viz/a__v.html")
    assert len(calls) == 1


def test_study_gallery_does_not_blame_credentials_when_the_registry_answered(monkeypatch):
    """Every sim answered from the registry with nothing to show -> 'no-figures', never 's3-auth-error'."""
    from vivarium_workbench.lib import remote_simulations as rs
    monkeypatch.setattr(rs, "list_remote_simulations", lambda ws, **k: [
        {"study_slug": "s", "status": "completed", "remote_origin": {"simulation_id": 2088}, "sim_name": "x"}])
    monkeypatch.setattr(raf, "list_remote_analysis_figures",
                        lambda c, sid, **k: {"available": True, "reason": "ok", "analyses": [], "via": "datasets"})
    monkeypatch.setattr(raf, "_aws_creds_ok", lambda: False)
    out = raf.study_remote_figures("/ws", client=FakeClient([]), slug="s")
    assert out["reason"] == "no-figures"


def test_without_s3_credentials_the_registry_alone_is_listed(monkeypatch):
    asked = _no_s3(monkeypatch)
    monkeypatch.setattr(raf, "_aws_creds_ok", lambda: False)
    client = FakeClient([_row(1, "figure", "viz/a__v.html")])
    for _ in range(3):
        out = raf.list_remote_analysis_figures(client, 2088)
        assert out["via"] == "datasets" and out["available"] is True
    assert asked == []


def test_same_directory_from_both_sources_merges_files(monkeypatch):
    """A directory the registry has only partly registered keeps its S3-only files, listed once each.
    The S3 row is named by its record ("percell run 3"), not its directory: it still lands in that directory."""
    _no_s3(monkeypatch, analyses=[
        {"name": "percell run 3", "status": "completed", "result_uri": PREFIX + "/", "source": "record",
         "figures": [{"path": "viz/a__v.html"}, {"path": "viz/b__v.html"}],
         "ptools": [{"path": "ptools/t.tsv"}]},
    ])
    client = FakeClient([_row(1, "figure", "viz/a__v.html")])
    out = raf.list_remote_analysis_figures(client, 2088)
    assert [a["name"] for a in out["analyses"]] == [ADIR]
    row = out["analyses"][0]
    assert [f["path"] for f in row["figures"]] == ["viz/a__v.html", "viz/b__v.html"]
    assert row["figures"][0]["dataset_id"] == 1                  # the registered copy wins
    assert [p["path"] for p in row["ptools"]] == ["ptools/t.tsv"]
    assert out["via"] == "datasets+s3"


def test_study_gallery_reports_truncated_sims(monkeypatch):
    from vivarium_workbench.lib import remote_simulations as rs
    monkeypatch.setattr(rs, "list_remote_simulations", lambda ws, **k: [
        {"study_slug": "s", "status": "completed", "remote_origin": {"simulation_id": 2088}, "sim_name": "x"}])
    monkeypatch.setattr(raf, "list_remote_analysis_figures", lambda c, sid, **k: {
        "available": True, "reason": "ok", "via": "datasets", "truncated": True,
        "analyses": [{"name": ADIR, "status": "completed", "figures": [{"path": "viz/a.html"}], "ptools": []}]})
    out = raf.study_remote_figures("/ws", client=FakeClient([]), slug="s")
    assert out["truncated_sims"] == 1
