"""The ``artifacts`` and ``analyses`` facets (docs/backend-adapters.md §2.1, step P2 "Analyses and figures").

The core keeps the five routes and routes each request; SMS's facets (``vivarium_workbench_sms/analyses.py``) answer.
The route-level answers are pinned by ``test_analyses_goldens.py``; these tests pin the dispatch itself, the study
gallery's fan-out, and the seams that must not come back.
"""
from __future__ import annotations

import ast
import types
from pathlib import Path

import pytest

from test_backends_existing import HERE, _record, asked, ws  # noqa: F401 -- fixtures, found by name
from vivarium_workbench.lib import backends
from vivarium_workbench.lib import remote_run_views as rrv


def _sms_legacy():
    from vivarium_workbench_sms import sms_legacy
    return sms_legacy


def _sms_landing():
    from vivarium_workbench_sms import sms_landing
    return sms_landing


@pytest.fixture
def recorded(monkeypatch):
    """SMS's two facets, replaced method by method with recorders (the Backend itself is frozen)."""
    seen: list = []
    art, ana = backends.SMS_LEGACY.artifacts, backends.SMS_LEGACY.analyses
    monkeypatch.setattr(art, "land_outputs", lambda ws_root, body: seen.append(("land", body)) or ({"ok": 1}, 200))
    monkeypatch.setattr(art, "list_figures", lambda rid: seen.append(("list", rid)) or ({"available": True}, 200))
    monkeypatch.setattr(art, "read_figure",
                        lambda rid, analysis, path: seen.append(("read", rid, analysis, path)) or (b"P", 200, "x/y"))
    monkeypatch.setattr(art, "study_figures",
                        lambda ws_root, study, max_runs: seen.append(("study", study, max_runs))
                        or ({"available": True, "study": study, "sims": []}, 200))
    monkeypatch.setattr(ana, "rerun", lambda ws_root, body: seen.append(("rerun", body)) or ({"ok": 1}, 202))
    return seen


# --- dispatch, providers on ------------------------------------------------------------------------------------------

@pytest.mark.plugin
@pytest.mark.parametrize("given", [12, "12", " 12 "])
def test_a_run_goes_to_its_backends_facet_with_the_normalised_id(ws, asked, recorded, given):
    assert rrv.remote_run_land_artifacts(ws, {"simulation_id": given, "run_id": " r1 ", "study": "s"}) == ({"ok": 1}, 200)
    assert rrv.remote_run_analysis(ws, {"simulation_id": given, "modules": {"a": {}}}) == ({"ok": 1}, 202)
    assert rrv.analysis_figures(ws, given, study="s") == ({"available": True}, 200)
    assert rrv.analysis_figure(ws, given, "a", "viz/x.png", study="s") == (b"P", 200, "x/y")
    assert recorded == [
        ("land", {"simulation_id": "12", "run_id": "r1", "study": "s"}),
        ("rerun", {"simulation_id": "12", "modules": {"a": {}}}),
        ("list", "12"),
        ("read", "12", "a", "viz/x.png"),
    ]
    assert asked == []


@pytest.mark.parametrize("body, forwarded", [
    ({"run_id": "r"}, {"run_id": "r"}),
    ({"simulation_id": "0", "run_id": "r"}, {"run_id": "r"}),        # a dead id is dropped, run_id kept
    ({"simulation_id": 12, "run_id": " "}, {"simulation_id": 12, "run_id": " "}),  # an id, no folder: as given
])
@pytest.mark.plugin
def test_a_land_that_names_no_run_goes_to_the_unrouted_backends_facet(ws, asked, recorded, body, forwarded):
    rrv.remote_run_land_artifacts(ws, body)
    assert recorded == [("land", forwarded)]


@pytest.mark.plugin
@pytest.mark.parametrize("body, forwarded", [({}, {}), ({"simulation_id": 0, "study": "s"}, {"study": "s"})])
def test_a_rerun_that_names_no_run_goes_to_the_unrouted_backends_facet(ws, asked, recorded, body, forwarded):
    rrv.remote_run_analysis(ws, body)
    assert recorded == [("rerun", forwarded)]


@pytest.mark.plugin
def test_with_no_id_the_sms_login_check_still_comes_first(ws, asked, monkeypatch):
    monkeypatch.setattr(_sms_legacy(), "_run_auth_ok", lambda: False)
    assert rrv.remote_run_land_artifacts(ws, {"run_id": "r"}) == ({"error": "not authenticated"}, 401)
    assert rrv.remote_run_analysis(ws, {}) == ({"error": "not authenticated"}, 401)
    assert asked == []


@pytest.mark.plugin
@pytest.mark.parametrize("limit, max_runs", [(None, 10), (0, 10), (4, 4), (99, 30), (-5, 1)])
def test_the_study_gallery_asks_the_offering_facet_with_the_clamped_limit(ws, recorded, limit, max_runs):
    assert rrv.study_figures(ws, "s", limit) == ({"available": True, "study": "s", "sims": []}, 200)
    assert recorded == [("study", "s", max_runs)]


@pytest.mark.plugin
def test_the_study_gallery_without_a_study_is_the_old_400(ws, recorded):
    assert rrv.study_figures(ws, "", 10) == ({"error": "study required"}, 400) and recorded == []


@pytest.mark.plugin
def test_the_declared_ordering_record_before_login(ws, asked, monkeypatch):
    """DECLARED: another backend's run is refused by its record before SMS's login check (as remote_run_land is)."""
    monkeypatch.setattr(_sms_legacy(), "_run_auth_ok", lambda: pytest.fail("the SMS login check was consulted"))
    _record(ws, "simulation-V", "viva-core", HERE)
    for call in (lambda: rrv.remote_run_land_artifacts(ws, {"simulation_id": "simulation-V", "run_id": "r"}),
                 lambda: rrv.remote_run_analysis(ws, {"simulation_id": "simulation-V"})):
        body, status = call()
        assert (status, body["reason"], body["terminal"]) == (409, "not-offered", True)
    assert asked == []


@pytest.mark.parametrize("run_id", ["../x", "a/b", "..", "/abs"])
def test_the_core_refuses_a_landing_folder_that_is_a_path(ws, asked, monkeypatch, run_id):
    """DECLARED: ``run_id`` becomes ``.pbg/runs/<run_id>``; the view refuses a path itself (the app-wide guard covers
    only requests that come through a route). Core: without the plugin there is no landing to trip."""
    try:
        landing = _sms_landing()
    except ImportError:   # the core-only job
        pass
    else:
        monkeypatch.setattr(landing, "land_remote_simulation_artifacts", lambda *a, **k: pytest.fail("landed"))
    assert rrv.remote_run_land_artifacts(ws, {"simulation_id": 12, "run_id": run_id}) == (
        {"error": "invalid run_id: it must be a plain name, not a path"}, 400)
    assert asked == []


@pytest.mark.plugin
def test_a_non_string_landing_folder_is_read_as_text(ws, asked, monkeypatch):
    landed: list = []
    monkeypatch.setattr(_sms_landing(), "land_remote_simulation_artifacts",
                        lambda ws_root, sim, run_id, client: landed.append((sim, run_id)) or {"run_id": run_id})
    assert rrv.remote_run_land_artifacts(ws, {"simulation_id": 12, "run_id": 5}) == ({"run_id": "5"}, 200)
    assert landed == [(12, "5")]


# --- the study gallery's fan-out over the offering backends ------------------------------------------------------

def _offering(*answers):
    class Facet:
        def __init__(self, answer):
            self.answer = answer

        def study_figures(self, ws_root, study, max_runs):
            return self.answer

    return lambda facet: tuple(types.SimpleNamespace(artifacts=Facet(a)) for a in answers)


def test_the_first_available_gallery_wins(ws, monkeypatch):
    first = ({"available": False, "reason": "no-sims", "study": "s", "sims": []}, 200)
    second = ({"available": True, "study": "s", "sims": [1]}, 200)
    monkeypatch.setattr(rrv.backends, "offering", _offering(first, second))
    assert rrv.study_figures(ws, "s", 10) == second


def test_with_no_available_gallery_the_first_answer_is_returned(ws, monkeypatch):
    first = ({"available": False, "reason": "no-sims", "study": "s", "sims": []}, 200)
    second = ({"available": False, "reason": "s3-unavailable", "study": "s", "sims": []}, 200)
    monkeypatch.setattr(rrv.backends, "offering", _offering(first, second))
    assert rrv.study_figures(ws, "s", 10) == first


@pytest.mark.plugin
def test_the_figure_routing_answer_is_memoised(ws, monkeypatch):
    calls = []
    real = rrv.backends.for_existing
    monkeypatch.setattr(rrv.backends, "for_existing", lambda *a, **k: calls.append(1) or real(*a, **k))
    for _ in range(5):
        assert rrv._artifacts_of(ws, "88", {"study": "s"}, memo=True) is backends.SMS_LEGACY.artifacts
    assert len(calls) == 1


# --- seams that must not come back ----------------------------------------------------------------------------------

def test_backend_caches_names_the_facet_memo():
    """``forget_backend_caches`` skips an attribute a module does not have, so a stale name there would go silently
    dead: the routing memo is listed under its current name, and that name exists."""
    from conftest import BACKEND_CACHES

    assert ("vivarium_workbench.lib.remote_run_views", "_FACET_MEMO") in BACKEND_CACHES
    assert isinstance(rrv._FACET_MEMO, dict)


def test_remote_run_views_binds_no_facet_seam():
    """The SMS-only guard, its memo and the re-run's imports left the core; the app no longer imports the figure
    library. A patch on any of the old names now fails loudly instead of going dead."""
    from vivarium_workbench.api import app as app_module

    for name in ("_sms_only", "_SMS_ONLY_MEMO", "_SMS_ONLY_TTL", "study_spec", "warnings"):
        assert not hasattr(rrv, name), name
    assert not hasattr(app_module, "_remote_analysis_figures")


@pytest.mark.plugin
def test_the_plugin_module_reads_its_seams_through_their_modules():
    """One patch target per seam: ``analyses.py`` never from-imports a name the tests patch on its module
    (``remote_analysis_figures``' functions, ``sms_legacy``'s seams, the landing), and imports
    ``study_run_post.build_analysis_options`` only at call time (a module-level binding would outlive a patch)."""
    from vivarium_workbench_sms import analyses

    tree = ast.parse(Path(analyses.__file__).read_text(encoding="utf-8"))
    anywhere = {"vivarium_workbench_sms.remote_analysis_figures", "vivarium_workbench_sms.sms_legacy",
                "vivarium_workbench_sms.sms_landing", "vivarium_workbench.lib.remote_run_landing"}
    bad = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module in anywhere]
    bad += [n.module for n in tree.body
            if isinstance(n, ast.ImportFrom) and n.module == "vivarium_workbench.lib.study_run_post"]
    assert not bad, bad
