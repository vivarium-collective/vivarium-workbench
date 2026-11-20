"""The ``listing`` facet (docs/backend-adapters.md §2.1, step P2 "Remote tab" R1).

The core merges the remote runs into ``/api/simulations``, its ``remote`` state, the study cards' counts and the
``?refresh=true`` flush through ``backends.offering("listing")``; SMS's ``SmsListing``
(``vivarium_workbench_sms/remote_simulations.py``) answers. The route-level answers are pinned by
``test_remote_listing_goldens.py`` (taken on the base); these tests pin the dispatch, the fan-out over several
listing backends, the no-blocking rule (#1106), the study-map gate, providers off, and the seams that must not
come back.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

import vivarium_workbench_sms.remote_simulations as rs
from vivarium_workbench.lib import backends
from vivarium_workbench.lib import investigations_index as ii
from vivarium_workbench.lib import simulations_index as si
from vivarium_workbench.lib.backends import provider as provider_mod

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def providers_off(monkeypatch):
    monkeypatch.setenv(provider_mod.PROVIDERS_ENV, "none")
    backends.reset_registry()
    yield
    monkeypatch.delenv(provider_mod.PROVIDERS_ENV, raising=False)
    backends.reset_registry()


@pytest.fixture(autouse=True)
def _isolated_caches():
    si.clear_build_cache()
    yield
    si.clear_build_cache()


@pytest.fixture
def sms_calls(monkeypatch):
    """The three plugin functions SmsListing calls, replaced by recorders on the module (where it reads them)."""
    calls: list = []
    monkeypatch.setattr(rs, "list_remote_simulations_swr",
                        lambda ws, *a, **k: calls.append(("swr", ws, a, k)) or [dict(SWR_ROW)])
    monkeypatch.setattr(rs, "list_remote_simulations",
                        lambda ws, *a, **k: calls.append(("list", ws, a, k)) or [dict(FRESH_ROW)])
    monkeypatch.setattr(rs, "remote_state", lambda ws, *a, **k: calls.append(("state", ws, a, k)) or {"state": "x"})
    return calls


SWR_ROW = {"run_id": "swr-row", "status": "completed"}
FRESH_ROW = {"run_id": "fresh-row", "status": "completed"}


# --- dispatch, providers on ------------------------------------------------------------------------------------------

def test_sms_listing_calls_the_plugin_functions_at_call_time(sms_calls, tmp_path):
    listing = backends.SMS_LEGACY.listing
    assert listing.list_runs(tmp_path) == [SWR_ROW]
    assert listing.list_runs(tmp_path, fresh=True) == [FRESH_ROW]
    assert listing.state(tmp_path) == {"state": "x"}
    assert sms_calls == [
        ("swr", tmp_path, (), {}),
        ("list", tmp_path, (), {"use_cache": False, "timeout": rs._FRESH_TIMEOUT,
                                "max_retries": rs._FRESH_MAX_RETRIES}),
        ("state", tmp_path, (), {}),
    ]


def test_sms_listing_invalidate_clears_the_module_caches(monkeypatch):
    cache, meta = {("k",): (0, [])}, {("k",): {"as_of": 1}}
    monkeypatch.setattr(rs, "_REMOTE_CACHE", cache)
    monkeypatch.setattr(rs, "_REMOTE_META", meta)
    backends.SMS_LEGACY.listing.invalidate()
    assert cache == {} and meta == {}


def test_the_core_merge_goes_through_sms_listing(sms_calls, tmp_path):
    local = [{"run_id": "swr-row", "local": True}, {"run_id": "other"}]
    assert si._append_remote_simulations(local, tmp_path) == local  # the one remote row is a landed local run
    merged = si._append_remote_simulations([{"run_id": "other"}], tmp_path, fresh=True)
    assert merged == [{"run_id": "other"}, FRESH_ROW]
    assert si._remote_listing_state(tmp_path) == {"state": "x"}
    assert [c[0] for c in sms_calls] == ["swr", "list", "state"]


def test_one_backends_own_duplicate_rows_are_kept(monkeypatch, tmp_path):
    """Identical to the base: the dedup is against local rows (and, new, earlier backends), never within one
    backend's answer (two SMS simulations may share an experiment id)."""
    monkeypatch.setattr(rs, "list_remote_simulations_swr", lambda ws: [{"run_id": "a", "n": 1}, {"run_id": "a", "n": 2}])
    assert si._append_remote_simulations([], tmp_path) == [{"run_id": "a", "n": 1}, {"run_id": "a", "n": 2}]


def test_the_refresh_flush_reaches_the_plugin_caches(monkeypatch):
    cache, meta = {("k",): (0, [])}, {("k",): {}}
    monkeypatch.setattr(rs, "_REMOTE_CACHE", cache)
    monkeypatch.setattr(rs, "_REMOTE_META", meta)
    si.forget_remote_listings()
    assert cache == {} and meta == {}


# --- fan-out over several listing backends --------------------------------------------------------------------------

class _Listing:
    def __init__(self, rows=(), state=None, raises=False):
        self.rows, self._state, self.raises = list(rows), state, raises
        self.asked: list = []

    def list_runs(self, ws_root, *, fresh=False):
        self.asked.append(("list", fresh))
        if self.raises:
            raise RuntimeError("listing down")
        return list(self.rows)

    def state(self, ws_root):
        self.asked.append(("state",))
        if self.raises:
            raise RuntimeError("listing down")
        return self._state

    def invalidate(self):
        self.asked.append(("invalidate",))
        if self.raises:
            raise RuntimeError("listing down")


def _offering(*listings):
    fakes = tuple(backends.Backend(f"fake-{i}", backends.COMPOSE.runs, listing=lst) for i, lst in enumerate(listings))

    def offering(facet):
        assert facet == "listing"
        return fakes
    return offering


def test_fan_out_concatenates_and_dedups_against_local_rows_and_across_backends(monkeypatch, tmp_path):
    broken = _Listing(raises=True)
    first = _Listing([{"run_id": "x", "by": 1}, {"run_id": "y", "by": 1}], state=None)
    second = _Listing([{"run_id": "y", "by": 2}, {"run_id": "z", "by": 2}, {"run_id": "local", "by": 2}],
                      state={"state": "stale", "as_of": 1.0, "error": None})
    monkeypatch.setattr(backends, "offering", _offering(broken, first, second))
    merged = si._append_remote_simulations([{"run_id": "local"}], tmp_path, fresh=True)
    assert merged == [{"run_id": "local"}, {"run_id": "x", "by": 1}, {"run_id": "y", "by": 1},
                      {"run_id": "z", "by": 2}]
    # The state is the first non-None answer; a raising backend hides neither.
    assert si._remote_listing_state(tmp_path) == {"state": "stale", "as_of": 1.0, "error": None}
    assert first.asked[0] == ("list", True) and second.asked[0] == ("list", True)


def test_fan_out_feeds_the_study_counts_deduped(monkeypatch, tmp_path):
    (tmp_path / "workspace.yaml").write_text("remote_run_study_map:\n  - {pattern: x, study: s}\n", encoding="utf-8")
    first = _Listing([{"run_id": "r1", "study_slug": "s", "completed_at": 5.0}])
    second = _Listing([{"run_id": "r1", "study_slug": "s", "completed_at": 9.0},
                       {"run_id": "r2", "study_slug": "s", "started_at": 7.0}])
    monkeypatch.setattr(backends, "offering", _offering(first, second))
    assert ii._remote_study_run_stats(tmp_path) == {"s": {"count": 2, "last_run_ts": 7.0}}
    assert first.asked == [("list", False)] and second.asked == [("list", False)]


def test_the_refresh_flush_asks_every_backend_and_survives_a_raising_one(monkeypatch):
    first, broken, last = _Listing(), _Listing(raises=True), _Listing()
    monkeypatch.setattr(backends, "offering", _offering(first, broken, last))
    si.forget_remote_listings()
    assert first.asked == broken.asked == last.asked == [("invalidate",)]


def test_a_broken_registry_leaves_the_listing_local(monkeypatch, tmp_path):
    def offering(facet):
        raise RuntimeError("a broken provider")

    monkeypatch.setattr(backends, "offering", offering)
    local = [{"run_id": "a"}]
    assert si._append_remote_simulations(local, tmp_path) is local
    assert si._remote_listing_state(tmp_path) is None
    si.forget_remote_listings()  # does not raise
    (tmp_path / "workspace.yaml").write_text("remote_run_study_map:\n  - {pattern: x, study: s}\n", encoding="utf-8")
    assert ii._remote_study_run_stats(tmp_path) == {}


# --- no blocking call on the listing path (#1106) -------------------------------------------------------------------

@pytest.fixture
def blocking_calls(monkeypatch):
    """The calls that may block (a reachability probe, a capability profile, a record lookup), as recorders."""
    from vivarium_workbench.lib import server_capabilities as sc
    from vivarium_workbench.lib.backends import records

    seen: list = []
    monkeypatch.setattr(backends, "active", lambda *a, **k: seen.append("active"))
    monkeypatch.setattr(backends, "for_existing", lambda *a, **k: seen.append("for_existing"))
    monkeypatch.setattr(sc, "explicit_backend_profile", lambda *a, **k: seen.append("profile"))
    monkeypatch.setattr(records, "resolve_existing", lambda *a, **k: seen.append("resolve_existing"))
    return seen


def test_the_listing_path_makes_no_blocking_call(blocking_calls, sms_calls, tmp_path):
    (tmp_path / "workspace.yaml").write_text("remote_run_study_map:\n  - {pattern: x, study: s}\n", encoding="utf-8")
    si._append_remote_simulations([], tmp_path)
    si._append_remote_simulations([], tmp_path, fresh=True)
    si._remote_listing_state(tmp_path)
    si.forget_remote_listings()
    ii._remote_study_run_stats(tmp_path)
    assert blocking_calls == []
    assert [c[0] for c in sms_calls] == ["swr", "list", "state", "swr"]


def test_api_simulations_answers_without_a_blocking_call(blocking_calls, sms_calls, tmp_path, inproc_client):
    client = inproc_client(tmp_path)
    for path in ("/api/simulations", "/api/simulations?refresh=true"):
        r = client.get(path)
        assert r.status_code == 200, r.text
        assert r.json()["remote"] == {"state": "x", "as_of": None, "error": None}
    assert blocking_calls == []


# --- the study-map gate ---------------------------------------------------------------------------------------------

def test_the_study_counts_ask_the_listing_only_with_a_study_map(monkeypatch, tmp_path):
    asked: list = []
    monkeypatch.setattr(backends.SMS_LEGACY.listing, "list_runs",
                        lambda ws_root, *, fresh=False: asked.append((ws_root, fresh)) or
                        [{"run_id": "r", "study_slug": "s", "started_at": 3.0}])
    (tmp_path / "workspace.yaml").write_text("name: t\n", encoding="utf-8")
    assert ii._remote_study_run_stats(tmp_path) == {}
    assert asked == []  # no map: the listing is never asked
    (tmp_path / "workspace.yaml").write_text("remote_run_study_map:\n  - {pattern: r, study: s}\n", encoding="utf-8")
    assert ii._remote_study_run_stats(tmp_path) == {"s": {"count": 1, "last_run_ts": 3.0}}
    assert asked == [(tmp_path, False)]


# --- providers off (declared change: SMS is no longer listed) -------------------------------------------------------

@pytest.fixture
def sms_tripwire(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("SMS was asked")

    monkeypatch.setattr("vivarium_workbench_sms.sms_api_client.SmsApiClient.__init__", _boom)


def test_with_providers_off_the_listing_is_local_and_sms_is_never_asked(providers_off, sms_tripwire, tmp_path,
                                                                        inproc_client):
    client = inproc_client(tmp_path)
    for path in ("/api/simulations", "/api/simulations?refresh=true"):
        r = client.get(path)
        assert r.status_code == 200, r.text
        assert r.json()["remote"] is None and r.json()["simulations"] == []
    (tmp_path / "workspace.yaml").write_text("remote_run_study_map:\n  - {pattern: x, study: s}\n", encoding="utf-8")
    assert ii._remote_study_run_stats(tmp_path) == {}


def test_the_analysis_tool_run_picker_follows_the_listing(monkeypatch, tmp_path):
    """``analysis_tools._run_candidates`` builds the index WITH remote rows: providers on it offers SMS's runs;
    providers off it does not (declared, with the listing)."""
    from vivarium_workbench.lib import analysis_tools

    monkeypatch.setattr(rs, "list_remote_simulations_swr", lambda ws: [{"run_id": "sms-run", "status": "completed"}])
    assert "sms-run" in [c["ref"] for c in analysis_tools._run_candidates(tmp_path)]
    monkeypatch.setenv(provider_mod.PROVIDERS_ENV, "none")
    backends.reset_registry()
    try:
        si.clear_build_cache()
        assert "sms-run" not in [c["ref"] for c in analysis_tools._run_candidates(tmp_path)]
    finally:
        monkeypatch.delenv(provider_mod.PROVIDERS_ENV, raising=False)
        backends.reset_registry()


# --- seams that must not come back ----------------------------------------------------------------------------------

def test_the_moved_study_map_names_are_gone_from_the_plugin():
    for name in ("_load_remote_study_map", "_infer_study_slug"):
        assert not hasattr(rs, name), name
    # The investigation lookup stays plugin-private (it globs study.yaml files itself).
    assert callable(rs._study_investigation_map)


def test_the_plugin_reads_the_study_map_through_its_module():
    """No ``from vivarium_workbench.lib.remote_study_map import ...`` in the plugin: a patch on the core module stays
    live."""
    offenders = []
    for path in sorted((ROOT / "vivarium_workbench_sms").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module == "vivarium_workbench.lib.remote_study_map":
                offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == []


@pytest.mark.parametrize("module", ["vivarium_workbench/lib/simulations_index.py",
                                    "vivarium_workbench/lib/investigations_index.py",
                                    "vivarium_workbench/api/app.py"])
def test_the_core_does_not_reach_into_the_plugin_listing(module):
    """Import-linter cannot see a ``sys.modules`` reach-in; the plugin's cache names and module must not appear."""
    text = (ROOT / module).read_text(encoding="utf-8")
    for name in ("_REMOTE_CACHE", "_REMOTE_META", "import remote_simulations", "sms.remote_simulations",
                 "list_remote_simulations", "remote_state("):
        assert name not in text, (module, name)


def test_backend_caches_names_the_listing_caches():
    """``forget_backend_caches`` skips an attribute a module does not have, so a renamed cache would go silently
    dead between tests."""
    from conftest import BACKEND_CACHES

    for attr in ("_REMOTE_CACHE", "_REMOTE_META"):
        assert ("vivarium_workbench_sms.remote_simulations", attr) in BACKEND_CACHES
        assert isinstance(getattr(rs, attr), dict)
