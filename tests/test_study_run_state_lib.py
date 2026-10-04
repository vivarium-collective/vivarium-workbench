"""Behavioral tests for the extracted ``lib/study_run_state.py`` helpers.

E2 lib-extraction: ``_resolve_study_baseline_state`` /
``_investigation_emitter_for_study`` / ``_zarr_store_for_sim`` moved out of
``server.py`` (parameterized on ``ws_root`` / an explicit study-db path), with
server name-shims left behind for the live call-sites. These tests NEVER run a
real simulation — the generator registry is monkeypatched — and assert the
resolution result shapes, that resolution reads the passed ``ws_root`` (not a
global), and that the server shims are behavior-identical to the lib functions.
"""

import sqlite3
import types
from pathlib import Path

import yaml

import process_bigraph.composite_generator as cg
import process_bigraph.composite_discovery as cd
from vivarium_workbench.lib import study_run_state as srs

from conftest import register_generator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _entry(name="foo", parameters=None):
    return types.SimpleNamespace(name=name, parameters=parameters or {})


def _register(monkeypatch, spec_id, entry, *, build=None):
    """Put a generator entry in the registry and stub build_generator."""
    monkeypatch.setattr(cg, "discover_generators", lambda: None)
    register_generator(spec_id, entry)
    if build is not None:
        monkeypatch.setattr(cg, "build_generator", build)


def _empty_registry(monkeypatch):
    """Force a non-empty registry that does NOT contain the test spec_id, so
    discover_generators is not invoked and disk-discovery returns nothing."""
    monkeypatch.setattr(cg, "discover_generators", lambda: None)
    register_generator("_dummy_keep_registry_truthy", _entry())
    monkeypatch.setattr(cd, "discover_composites", lambda: {})


# ---------------------------------------------------------------------------
# resolve_study_baseline_state
# ---------------------------------------------------------------------------

def test_resolve_happy_returns_state_none(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    spec = "pkg.composites.baseline"
    _register(monkeypatch, spec, _entry(name="baseline"),
              build=lambda entry, overrides=None: {"state": {"a": 1}})

    state, err = srs.resolve_study_baseline_state(ws, "pkg", spec, {})
    assert err is None
    assert state == {"a": 1}


def test_resolve_unknown_generator_returns_none_error(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    _empty_registry(monkeypatch)

    state, err = srs.resolve_study_baseline_state(ws, "pkg", "pkg.composites.nope", {})
    assert state is None
    assert isinstance(err, dict) and "not found" in err["error"]


def test_resolve_generator_build_failure_returns_error(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    spec = "pkg.composites.boom"

    def _boom(entry, overrides=None):
        raise RuntimeError("kaboom")

    _register(monkeypatch, spec, _entry(name="boom"), build=_boom)
    state, err = srs.resolve_study_baseline_state(ws, "pkg", spec, {})
    assert state is None
    assert "generator build failed: kaboom" in err["error"]


# #1271: a workspace composite FILE resolves for a run the way the Composites
# tab lists it, without depending on installed-distribution metadata. Real
# files on disk; only the generator registry (the other path) is held empty.

_SPEC = {
    "name": "decay",
    "parameters": {
        "k": {"type": "float", "default": 0.5},
        "x0": {"type": "float", "default": 2.0},
    },
    "state": {
        "proc": {"_type": "process", "address": "local:Decay",
                 "config": {"k": "${k}"}, "interval": 1.0},
        "stores": {"x": "${x0}"},
    },
}


def _workspace_spec(tmp_path, spec=_SPEC, stem="decay", pkg="wspkg"):
    ws = tmp_path / "ws"
    comp_dir = ws / pkg / "composites"
    comp_dir.mkdir(parents=True)
    (comp_dir / f"{stem}.composite.yaml").write_text(yaml.safe_dump(spec), encoding="utf-8")
    return ws, f"{pkg}.composites.{stem}"


def test_resolve_workspace_yaml_substitutes_defaults(tmp_path, monkeypatch):
    ws, spec_id = _workspace_spec(tmp_path)
    _empty_registry(monkeypatch)
    state, err = srs.resolve_study_baseline_state(ws, "wspkg", spec_id, {})
    assert err is None
    assert state["proc"]["config"]["k"] == 0.5
    assert state["stores"]["x"] == 2.0


def test_resolve_workspace_yaml_applies_declared_overrides_only(tmp_path, monkeypatch):
    """Variants override declared parameters; run-time keys a study also
    stores (e.g. perturbations) are ignored, as for generators."""
    ws, spec_id = _workspace_spec(tmp_path)
    _empty_registry(monkeypatch)
    state, err = srs.resolve_study_baseline_state(
        ws, "wspkg", spec_id, {"k": 0.1, "max_generations": 3})
    assert err is None
    assert state["proc"]["config"]["k"] == 0.1
    assert state["stores"]["x"] == 2.0


def test_resolve_workspace_yaml_without_state_errors(tmp_path, monkeypatch):
    ws, spec_id = _workspace_spec(tmp_path, spec={"name": "decay", "parameters": {}})
    _empty_registry(monkeypatch)
    state, err = srs.resolve_study_baseline_state(ws, "wspkg", spec_id, {})
    assert state is None and "no `state:` block" in err["error"]


def test_every_listed_workspace_composite_resolves_for_a_run(tmp_path, monkeypatch):
    """Listed implies runnable: each id the Composites tab lists resolves."""
    from vivarium_workbench.lib.composite_lookup import discover_workspace_composites
    ws, _ = _workspace_spec(tmp_path)
    second = dict(_SPEC, name="other")
    (ws / "wspkg" / "composites" / "other.composite.yaml").write_text(
        yaml.safe_dump(second), encoding="utf-8")
    _empty_registry(monkeypatch)
    listed = discover_workspace_composites(ws, "wspkg")
    assert len(listed) == 2
    for spec_id in listed:
        state, err = srs.resolve_study_baseline_state(ws, "wspkg", spec_id, {})
        assert err is None and isinstance(state, dict), spec_id


def test_resolve_reads_ws_root_for_cache_dir_not_global(tmp_path, monkeypatch):
    """The cache_dir existence check resolves relative to the passed ws_root
    (no global): when ws_root has the ParCa cache, cache_dir is KEPT (passed to
    the builder); when it doesn't, cache_dir is DROPPED."""
    spec = "pkg.composites.cached"
    captured = {}

    def _build(entry, overrides=None):
        captured["overrides"] = dict(overrides or {})
        return {"state": {"ok": True}}

    _register(monkeypatch, spec, _entry(name="cached",
              parameters={"cache_dir": None}), build=_build)

    # ws_root that HAS the cache -> kept.
    ws_good = tmp_path / "good"
    (ws_good / "mycache").mkdir(parents=True)
    (ws_good / "mycache" / "initial_state.json").write_text("{}", encoding="utf-8")

    srs.resolve_study_baseline_state(ws_good, "pkg", spec, {"cache_dir": "mycache"})
    assert captured["overrides"].get("cache_dir") == "mycache"

    # ws_root that LACKS the cache -> dropped.
    ws_bad = tmp_path / "bad"
    ws_bad.mkdir()
    srs.resolve_study_baseline_state(ws_bad, "pkg", spec, {"cache_dir": "mycache"})
    assert "cache_dir" not in captured["overrides"]


# ---------------------------------------------------------------------------
# investigation_emitter_for_study
# ---------------------------------------------------------------------------

def _write_inv(ws, slug, studies, runtime=None):
    d = ws / "investigations" / slug
    d.mkdir(parents=True, exist_ok=True)
    data = {"studies": studies}
    if runtime is not None:
        data["runtime"] = runtime
    (d / "investigation.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


def test_emitter_declared_xarray(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    _write_inv(ws, "inv-a", ["study-1", "study-2"],
               runtime={"default_emitter": "xarray"})
    assert srs.investigation_emitter_for_study(ws, "study-1") == "xarray"


def test_emitter_study_not_owned_returns_none(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    _write_inv(ws, "inv-a", ["study-1"], runtime={"default_emitter": "xarray"})
    assert srs.investigation_emitter_for_study(ws, "orphan-study") is None


def test_emitter_no_investigations_dir_returns_none(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    assert srs.investigation_emitter_for_study(ws, "study-1") is None


def test_emitter_none_study_name(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    assert srs.investigation_emitter_for_study(ws, None) is None


# ---------------------------------------------------------------------------
# zarr_store_for_sim
# ---------------------------------------------------------------------------

def _make_study_db(study_dir, rows):
    """rows: list of (run_id, sim_name, status, started_at)."""
    study_dir.mkdir(parents=True, exist_ok=True)
    db = study_dir / "runs.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE runs_meta (run_id TEXT, sim_name TEXT, "
                 "status TEXT, started_at REAL)")
    conn.executemany(
        "INSERT INTO runs_meta (run_id, sim_name, status, started_at) "
        "VALUES (?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()
    return db


def test_zarr_finds_most_recent_store(tmp_path):
    study = tmp_path / "study"
    db = _make_study_db(study, [
        ("run-old", "simA", "completed", 100.0),
        ("run-new", "simA", "completed", 200.0),
    ])
    # zarr dir for the most-recent completed run exists on disk.
    (study / "runs.run-new.zarr").mkdir()
    (study / "runs.run-old.zarr").mkdir()
    got = srs.zarr_store_for_sim(db, "simA")
    assert got == study / "runs.run-new.zarr"


def test_zarr_missing_store_returns_none(tmp_path):
    study = tmp_path / "study"
    db = _make_study_db(study, [("run-1", "simA", "completed", 100.0)])
    # No zarr dir on disk -> None.
    assert srs.zarr_store_for_sim(db, "simA") is None


def test_zarr_no_sim_name_returns_none(tmp_path):
    study = tmp_path / "study"
    db = _make_study_db(study, [("run-1", "simA", "completed", 100.0)])
    assert srs.zarr_store_for_sim(db, None) is None


def test_zarr_nonexistent_db_returns_none(tmp_path):
    assert srs.zarr_store_for_sim(tmp_path / "nope.db", "simA") is None
