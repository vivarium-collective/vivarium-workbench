"""Opaque run ids (docs/backend-adapters.md §2.5, pulled forward from P3).

A remote run's id is an opaque string end to end: no route rejects it for its form, and the run's stored record
(``backends.for_existing``) decides which backend answers. An SMS integer id behaves exactly as before.
"""
from __future__ import annotations

import pytest

from test_backends_existing import HERE, _record, asked, ws  # noqa: F401 -- fixtures, found by name
from vivarium_workbench.lib import composite_run_views
from vivarium_workbench.lib import remote_run_views as rrv


def _raf():
    """The SMS figure library (plugin); the tests patching it are marked ``plugin``."""
    from vivarium_workbench_sms import remote_analysis_figures as raf
    return raf

FIGURE_ROUTES = ("/api/remote-analysis-figures?simulation_id={id}&study=s",
                 "/api/remote-analysis-figure?simulation_id={id}&analysis=a&path=viz/x.png&study=s")


@pytest.fixture
def no_sms(monkeypatch):
    """The SMS figure libraries must not be reached (without the plugin there are none to reach)."""
    rrv._FACET_MEMO.clear()
    try:
        raf = _raf()
    except ImportError:   # the core-only job
        return
    monkeypatch.setattr(raf, "list_remote_analysis_figures", lambda *a, **k: pytest.fail("asked SMS for figures"))
    monkeypatch.setattr(raf, "fetch_by_analysis", lambda *a, **k: pytest.fail("asked SMS for a figure"))


@pytest.mark.parametrize("url", FIGURE_ROUTES)
@pytest.mark.parametrize("rid, kind", [pytest.param("simulation-X", None, marks=pytest.mark.plugin),
                                       pytest.param("simulation-V", "viva-core", marks=pytest.mark.plugin),
                                       ("compose-7", None)])
def test_the_figure_routes_refuse_a_string_id_with_409_not_422(ws, asked, no_sms, inproc_client, url, rid, kind):
    """A compose-api / viva-core run is refused by its record (or, with none, its form) -- never a 422."""
    if kind:
        _record(ws, rid, kind, HERE)
    res = inproc_client(ws).get(url.format(id=rid))
    assert res.status_code == 409, res.text
    body = res.json()
    assert body["reason"] == "not-offered" and body["terminal"] is True and body["simulation_id"] == rid
    assert asked == []


@pytest.mark.parametrize("url", FIGURE_ROUTES)
@pytest.mark.parametrize("given", ["0", "", "%20"])
def test_the_figure_routes_answer_no_id_with_the_old_400(ws, asked, no_sms, inproc_client, url, given):
    assert inproc_client(ws).get(url.format(id=given)).status_code == 400 and asked == []


@pytest.mark.plugin
@pytest.mark.parametrize("given", ["12", "%2012%20", "%2B12"])
def test_an_sms_id_still_reaches_the_figure_libs_as_an_int(ws, asked, inproc_client, monkeypatch, given):
    seen: list = []
    rrv._FACET_MEMO.clear()
    raf = _raf()
    monkeypatch.setattr(raf, "list_remote_analysis_figures",
                        lambda client, sim, **k: seen.append(("list", sim)) or {"available": True, "analyses": []})
    monkeypatch.setattr(raf, "fetch_by_analysis",
                        lambda client, sim, analysis, path: seen.append(("fetch", sim)) or (b"png", "image/png"))
    c = inproc_client(ws)
    assert c.get(FIGURE_ROUTES[0].format(id=given)).status_code == 200
    assert c.get(FIGURE_ROUTES[1].format(id=given)).status_code == 200
    assert seen == [("list", 12), ("fetch", 12)]
    assert all(type(sim) is int for _, sim in seen)


@pytest.mark.plugin
def test_an_sms_record_whose_id_is_not_an_integer_is_refused(ws, asked):
    """A record naming SMS with a non-integer id is refused, not a 500 from the callers' int()."""
    _record(ws, "simulation-Z", "sms-legacy", HERE)
    body, status = rrv.analysis_figures(ws, "simulation-Z", study="s")
    assert status == 409 and body["reason"] == "not-offered"
    assert rrv.remote_run_analysis(ws, {"simulation_id": "simulation-Z", "study": "s"})[1] == 409
    assert rrv.remote_run_land_artifacts(ws, {"simulation_id": "simulation-Z", "run_id": "r1", "study": "s"})[1] == 409
    assert asked == []


@pytest.mark.parametrize("rid", ["simulation-X", "compose-7"])
def test_land_artifacts_and_reanalysis_refuse_a_string_id_of_another_backend(ws, asked, rid):
    assert rrv.remote_run_land_artifacts(ws, {"simulation_id": rid, "run_id": "r1"})[1] == 409
    assert rrv.remote_run_analysis(ws, {"simulation_id": rid})[1] == 409
    assert asked == []


@pytest.mark.plugin
def test_a_string_sms_id_is_the_same_request_as_the_integer(ws, asked, monkeypatch):
    """The Runs tab now sends the row's id as a string ("12"); SMS is asked with the integer 12 as before."""
    from vivarium_workbench_sms import sms_landing

    landed: list = []
    monkeypatch.setattr(sms_landing, "land_remote_simulation_artifacts",
                        lambda ws_root, sim, run_id, client: landed.append(sim) or {"run_id": run_id})
    for given in ("12", 12):
        assert rrv.remote_run_land_artifacts(ws, {"simulation_id": given, "run_id": "r1"})[1] == 200
        body, status = rrv.remote_run_analysis(ws, {"simulation_id": given})
        assert status == 202 and body["simulation_id"] == 12
    assert landed == [12, 12]
    assert asked == [("sms", "run_analysis"), ("sms", "run_analysis")]


@pytest.mark.parametrize("given, want", [("12", "12"), (" 12 ", "12"), ("+12", "12"), (12, "12"), ("0", ""),
                                         ("", ""), (None, ""), ("-3", "-3"), ("simulation-E9Ec819", "simulation-E9Ec819"),
                                         (" compose-7 ", "compose-7")])
def test_opaque_run_id(given, want):
    assert rrv.opaque_run_id(given) == want


# --- /api/composite-run/remote-sim-<id>/status ----------------------------------------------------------------

@pytest.mark.plugin
def test_composite_run_status_takes_a_string_remote_id_by_its_record(ws, asked):
    _record(ws, "simulation-V", "viva-core", HERE)
    body, status = composite_run_views.build_composite_run_status(ws, "remote-sim-simulation-V")
    assert status == 200 and asked == [("viva", "status")]
    assert body["simulation_id"] == "simulation-V" and body["remote"] is True


@pytest.mark.plugin
def test_composite_run_status_keeps_an_sms_integer(ws, asked):
    body, status = composite_run_views.build_composite_run_status(ws, "remote-sim-9")
    assert status == 200 and asked == [("sms", "simulation_status")]
    assert body["simulation_id"] == 9 and type(body["simulation_id"]) is int


def test_composite_run_status_without_an_id_is_still_404(ws, asked):
    assert composite_run_views.build_composite_run_status(ws, "remote-sim-")[1] == 404 and asked == []


# --- the pages that cannot be loaded in a harness: pinned by source --------------------------------------------

def test_the_study_page_never_picks_the_id_key_by_the_ids_form():
    from pathlib import Path

    static = Path(__file__).resolve().parent.parent / "vivarium_workbench" / "static"
    detail = (static / "study-detail.js").read_text(encoding="utf-8")
    assert "'/api/remote-run-chain-progress?run_id=' + encodeURIComponent(runId)" in detail
    assert "/^\\d+$/.test(String(runId))" not in detail
    walk = (static / "walkthrough.js").read_text(encoding="utf-8")
    assert "function _simIdNum(r)" in walk  # active-run sort: a string id never makes the comparator NaN


# --- non-ASCII digits never pass for an SMS integer (review of #1660) -------------------------------------------------

@pytest.mark.parametrize("rid", ["²", "١٢", "1_2"])
def test_non_ascii_or_underscored_digits_are_not_an_sms_id(rid):
    from vivarium_workbench.lib import remote_run_views as rrv
    from vivarium_workbench.lib.backends import SMS_LEGACY

    assert SMS_LEGACY.runs.owns(rid) is False
    assert rrv.opaque_run_id(rid) == rid  # passed on as an opaque string, never int()-normalised


@pytest.mark.parametrize("given, expected", [("12", "12"), (" 12 ", "12"), ("+12", "12"), ("0012", "12"),
                                             ("0", ""), ("00", ""), ("", ""), ("compose-7", "compose-7")])
def test_opaque_run_id_keeps_the_integer_spellings(given, expected):
    from vivarium_workbench.lib import remote_run_views as rrv

    assert rrv.opaque_run_id(given) == expected


def test_composite_status_for_a_non_ascii_digit_id_is_not_a_500(tmp_path):
    from vivarium_workbench.lib import composite_run_views as crv

    try:
        body, status = crv.build_composite_run_status(tmp_path, "remote-sim-²")
    except Exception as e:  # noqa: BLE001
        pytest.fail(f"raised {type(e).__name__}: {e}")
    assert status != 500
