"""Unit tests for vivarium_workbench.lib.run_runner."""
import json
import sys
from pathlib import Path

import pytest

from vivarium_workbench.lib.run_runner import execute, _emit_paths_for, RunRequest
from vivarium_workbench.lib.composite_runs import connect, query_run_meta, query_run

_REPO_ROOT = Path(__file__).parent.parent
FIXTURE_WS = _REPO_ROOT / "tests" / "_fixtures" / "ws_increase_demo"


def _req(emit_paths):
    return RunRequest(
        run_id="r", spec_id="s", pkg="p", workspace=Path("/tmp"),
        overrides={}, steps=1, emit_paths=emit_paths,
        db_file="/tmp/x.db", log_path="x.log",
    )


def test_emit_paths_for_defaults_to_all_stores_when_request_empty():
    """Empty emit_paths (no wiring-view selection) → emit every store."""
    state = {
        "stores": {"level": 1.0},
        "increase": {"_type": "process", "address": "local:Foo"},
        "emitter": {"_type": "step", "address": "local:RAMEmitter"},
    }
    assert _emit_paths_for(_req([]), state) == ["stores"]


def test_emit_paths_for_uses_explicit_selection_verbatim():
    """A non-empty emit_paths is used as-is — hand-picked stores win."""
    state = {"stores": {"level": 1.0, "other": 2.0}}
    assert _emit_paths_for(_req(["stores/level"]), state) == ["stores/level"]


def _write_request(tmp_path, *, steps=3, spec_id=None, overrides=None):
    """Copy the fixture workspace to tmp and write a run-request file."""
    import shutil
    ws = tmp_path / "ws"
    shutil.copytree(FIXTURE_WS, ws)
    if str(ws) not in sys.path:
        sys.path.insert(0, str(ws))
    run_id = "test-run-1"
    run_dir = ws / ".pbg" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    request = {
        "run_id": run_id,
        "spec_id": spec_id or "pbg_ws_increase_demo.composites.increase-demo",
        "pkg": "pbg_ws_increase_demo",
        "workspace": str(ws),
        "overrides": overrides or {},
        "steps": steps,
        "emit_paths": [],
        "db_file": str(ws / ".pbg" / "composite-runs.db"),
        "log_path": f".pbg/runs/{run_id}/run.log",
    }
    request_path = run_dir / "request.json"
    request_path.write_text(json.dumps(request))
    # Seed the runs_meta row the way the POST handler would.
    conn = connect(request["db_file"])
    from vivarium_workbench.lib.composite_runs import save_metadata
    save_metadata(conn, spec_id=request["spec_id"], run_id=run_id, params={},
                  label="", started_at=0.0, n_steps=steps,
                  log_path=request["log_path"])
    conn.close()
    return ws, request_path, run_id


@pytest.mark.skipif(not FIXTURE_WS.is_dir(), reason="fixture workspace absent")
def test_execute_completes_and_persists_trajectory(tmp_path):
    ws, request_path, run_id = _write_request(tmp_path, steps=3)
    rc = execute(request_path)
    assert rc == 0
    conn = connect(ws / ".pbg" / "composite-runs.db")
    meta = query_run_meta(conn, run_id=run_id)
    assert meta["status"] == "completed"
    assert meta["progress_step"] == 3
    trajectory = query_run(conn, run_id=run_id)
    assert len(trajectory) >= 1
    conn.close()


@pytest.mark.skipif(not FIXTURE_WS.is_dir(), reason="fixture workspace absent")
def test_execute_deployment_target_dispatches_remote(tmp_path, monkeypatch):
    """SP-D2: a 'deployment'-target request routes to remote_run.run_remote and
    lands 'completed' in composite-runs.db without running locally."""
    ws, request_path, run_id = _write_request(tmp_path, steps=4)
    # Flip the request to the deployment target.
    req = json.loads(request_path.read_text())
    req["target"] = "deployment"
    request_path.write_text(json.dumps(req))

    calls = {}

    def _fake_run_remote(ws_root, spec_id, *, dest, n_steps, overrides=None):
        calls["ws_root"] = Path(ws_root)
        calls["spec_id"] = spec_id
        calls["n_steps"] = n_steps
        calls["overrides"] = overrides
        return dest / "results.zip"

    from vivarium_workbench.lib import remote_run
    monkeypatch.setattr(remote_run, "run_remote", _fake_run_remote)

    rc = execute(request_path)
    assert rc == 0
    assert calls["spec_id"] == req["spec_id"]
    assert calls["n_steps"] == 4
    # the run form's parameter overrides must reach the remote runner
    assert calls["overrides"] == (req.get("overrides") or {})
    conn = connect(ws / ".pbg" / "composite-runs.db")
    meta = query_run_meta(conn, run_id=run_id)
    assert meta["status"] == "completed"
    conn.close()


@pytest.mark.skipif(not FIXTURE_WS.is_dir(), reason="fixture workspace absent")
def test_execute_deployment_target_marks_failed_on_error(tmp_path, monkeypatch):
    ws, request_path, run_id = _write_request(tmp_path, steps=2)
    req = json.loads(request_path.read_text())
    req["target"] = "deployment"
    request_path.write_text(json.dumps(req))

    def _boom(*a, **k):
        raise RuntimeError("sms-api unreachable")

    from vivarium_workbench.lib import remote_run
    monkeypatch.setattr(remote_run, "run_remote", _boom)

    rc = execute(request_path)
    assert rc == 1
    conn = connect(ws / ".pbg" / "composite-runs.db")
    assert query_run_meta(conn, run_id=run_id)["status"] == "failed"
    conn.close()


@pytest.mark.skipif(not FIXTURE_WS.is_dir(), reason="fixture workspace absent")
def test_execute_marks_failed_on_bad_spec(tmp_path):
    ws, request_path, run_id = _write_request(
        tmp_path, steps=2, spec_id="pbg_ws_increase_demo.composites.does-not-exist")
    rc = execute(request_path)
    assert rc == 1
    conn = connect(ws / ".pbg" / "composite-runs.db")
    meta = query_run_meta(conn, run_id=run_id)
    assert meta["status"] == "failed"
    conn.close()
    # Traceback / error landed in the log.
    log = ws / meta["log_path"]
    assert log.is_file() and log.stat().st_size > 0


# --- #754: default emit_paths to the composite's DECLARED paths, not all stores ---

def test_emit_paths_for_defaults_to_declared_paths_for_a_generator(monkeypatch):
    """A generator that declares emit paths (e.g. ecoli_baseline →
    global_time/bulk/listeners) must default to THOSE, not every store — emitting
    the entire whole-cell state to stacked RAM/sqlite/parquet sinks each tick is
    the #754 memory blow-up. Mirrors what the direct composite.run() emits."""
    import vivarium_workbench.lib.run_runner as rr
    monkeypatch.setattr(
        rr, "_generator_emitter_defaults",
        lambda spec_id: [{"paths": ["global_time", "bulk", "listeners"]}])
    # all_store_paths must NOT be consulted when a declaration exists.
    monkeypatch.setattr(rr.cr, "all_store_paths",
                        lambda state: (_ for _ in ()).throw(AssertionError("emit-all")))
    out = rr._emit_paths_for(_req([]), {"bulk": {}}, spec=None, spec_id="ecoli_baseline")
    assert out == ["global_time", "bulk", "listeners"]


def test_emit_paths_for_falls_back_to_all_stores_when_nothing_declared(monkeypatch):
    """A composite with no declared emitters keeps the emit-all default."""
    import vivarium_workbench.lib.run_runner as rr
    monkeypatch.setattr(rr, "_generator_emitter_defaults", lambda spec_id: [])
    monkeypatch.setattr(rr.cr, "all_store_paths", lambda state: ["a", "b"])
    out = rr._emit_paths_for(_req([]), {"a": 1, "b": 2}, spec=None, spec_id="plain")
    assert out == ["a", "b"]


def test_emit_paths_for_explicit_selection_still_wins_over_declared(monkeypatch):
    """An explicit wiring-view selection always wins, even when paths are declared."""
    import vivarium_workbench.lib.run_runner as rr
    monkeypatch.setattr(
        rr, "_generator_emitter_defaults",
        lambda spec_id: [{"paths": ["global_time", "bulk"]}])
    out = rr._emit_paths_for(_req(["listeners/mass"]), {}, spec=None, spec_id="x")
    assert out == ["listeners/mass"]


# --- #754 robustness: registry-free resolution, source labels, kind-honoring ---

def test_emit_paths_from_state_recovers_nested_emitter_wires():
    """The declared paths are recoverable from the built state's own emitter node
    even when it's nested (ecoli_baseline's lives at agents/0/emitter) — the
    rebrand-proof source that needs no generator registry. Internal '_'-ports skip."""
    from vivarium_workbench.lib.run_runner import _emit_paths_from_state
    state = {"global_time": 0.0, "agents": {"0": {"emitter": {
        "address": "local:viva_emitters.parquet_emitter.ParquetEmitter",
        "inputs": {"global_time": ["global_time"], "bulk": ["bulk"],
                   "listeners": ["listeners"], "_layer_in_7": ["_loom"]}}}}}
    assert _emit_paths_from_state(state) == ["global_time", "bulk", "listeners"]


def test_resolve_emit_paths_falls_through_to_state_when_registry_empty(monkeypatch):
    """The core robustness: when generator-registry resolution returns nothing
    (the pbg->viva skew that made the first fix a no-op), the run still emits the
    declared observables via the state walk — NOT all-stores."""
    import vivarium_workbench.lib.run_runner as rr
    monkeypatch.setattr(rr, "_generator_emitter_defaults", lambda spec_id: [])
    monkeypatch.setattr(rr.cr, "all_store_paths",
                        lambda s: (_ for _ in ()).throw(AssertionError("must not emit-all")))
    state = {"emitter": {"address": "local:XArrayEmitter",
                         "inputs": {"global_time": ["global_time"], "bulk": ["bulk"]}}}
    paths, source = rr._resolve_emit_paths(_req([]), state, spec=None, spec_id="x")
    assert paths == ["global_time", "bulk"] and source == "state"


def test_resolve_emit_paths_source_labels(monkeypatch):
    import vivarium_workbench.lib.run_runner as rr
    # explicit
    p, s = rr._resolve_emit_paths(_req(["a/b"]), {}, spec=None, spec_id="x")
    assert (p, s) == (["a/b"], "explicit")
    # declared (registry)
    monkeypatch.setattr(rr, "_generator_emitter_defaults",
                        lambda spec_id: [{"paths": ["global_time", "bulk"]}])
    p, s = rr._resolve_emit_paths(_req([]), {}, spec=None, spec_id="x")
    assert (p, s) == (["global_time", "bulk"], "declared")
    # all-stores (nothing declared, no emitter node in state)
    monkeypatch.setattr(rr, "_generator_emitter_defaults", lambda spec_id: [])
    monkeypatch.setattr(rr.cr, "all_store_paths", lambda s: ["x", "y"])
    p, s = rr._resolve_emit_paths(_req([]), {"x": 1, "y": 2}, spec=None, spec_id="x")
    assert (p, s) == (["x", "y"], "all-stores")


def test_declared_emitter_name_maps_class_to_kind():
    from vivarium_workbench.lib.run_runner import _declared_emitter_name
    assert _declared_emitter_name([{"address": "local:XArrayEmitter"}]) == "xarray"
    assert _declared_emitter_name([{"address": "local:ParquetEmitter"}]) == "parquet"
    assert _declared_emitter_name([{"address": "local:SQLiteEmitter"}]) == "sqlite"
    assert _declared_emitter_name([]) is None


def test_state_has_process_detects_temporal():
    from vivarium_workbench.lib.run_runner import _state_has_process
    assert _state_has_process({"agents": {"0": {"c": {"_type": "process"}}}}) is True
    assert _state_has_process({"a": {"_type": "step"}}) is False
