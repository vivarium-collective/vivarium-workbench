"""Tests for ``lib.investigation_run_one_views.investigation_run_one``.

Behaviour-preserving port of ``server.Handler._post_investigation_run_one`` (the
ad-hoc "Duplicate run" flow).  Every test monkeypatches
``investigation_run_one_views.subprocess.run`` so NO real composite is ever
spawned — the fake emits the ``@@@RESULTS@@@`` / ``@@@ERROR@@@`` markers the
parser reads — and ``cr.generate_run_id`` (fixed id).  ``investigations.load_spec``
and ``composite_lookup.find_composite_path`` are monkeypatched so resolution is
hermetic and decoupled from spec-migration internals; ``cr`` /
``substitute_parameters`` run for real against a tmp sqlite db + tmp files.

Covers: 400 (no inv / spec error / shape-less spec), 404 (spec missing / v2
baseline-variant missing / v2 sidecar missing / legacy composite not found),
the v2-variants + legacy resolution paths, viz persistence to
``<inv>/viz/<run_id>/<safe>.html``, the happy 200 (``ok: True``) and the
run-FAILURE 200 (``ok: False``) — only validation is non-200.
"""
from __future__ import annotations

import json
import re
import sqlite3
import types
from pathlib import Path

import pytest

from vivarium_workbench.lib import composite_runs as cr
from vivarium_workbench.lib import composite_lookup
from vivarium_workbench.lib import investigations
from vivarium_workbench.lib import remote_pinned
from vivarium_workbench.lib import study_run_state
from vivarium_workbench.lib import study_spec
from vivarium_workbench.lib import investigation_run_one_views as views
from vivarium_workbench.lib.errors import APIError

RID = "demo__1700000000__abcdef"


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------

def _make_ws(tmp_path: Path, *, name: str = "demo-ws") -> Path:
    (tmp_path / "workspace.yaml").write_text(f"name: {name}\n", encoding="utf-8")
    return tmp_path


def _write_spec(ws: Path, inv: str) -> Path:
    """Create a spec file at the resolved spec path so ``is_file()`` passes."""
    spec_path = study_spec.study_spec_path(ws, inv)
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(f"name: {inv}\n", encoding="utf-8")
    return spec_path


@pytest.fixture
def fixed_run_id(monkeypatch):
    monkeypatch.setattr(cr, "generate_run_id",
                        lambda spec_id, params=None, now=None: RID)
    return RID


def _fake_subprocess(stdout: str):
    def _run(cmd, *a, **k):
        return types.SimpleNamespace(stdout=stdout, stderr="", returncode=0)
    return _run


def _results_stdout(viz_html: dict) -> str:
    return "@@@RESULTS@@@\n" + json.dumps({"viz_html": viz_html})


def _error_stdout(traceback_tail: str) -> str:
    return "@@@ERROR@@@\n" + traceback_tail


# ---------------------------------------------------------------------------
# 400 / 404 validation
# ---------------------------------------------------------------------------

def test_missing_investigation_400(tmp_path):
    ws = _make_ws(tmp_path)
    body, status = views.investigation_run_one(ws, {})
    assert status == 400
    assert body == {"error": "investigation required"}


def test_spec_not_found_404(tmp_path):
    ws = _make_ws(tmp_path)
    body, status = views.investigation_run_one(ws, {"investigation": "inv-x"})
    assert status == 404
    assert body == {"error": "spec.yaml not found"}


def test_spec_error_400(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)
    _write_spec(ws, "inv-x")

    def _boom(path):
        raise investigations.InvestigationSpecError("bad spec")

    monkeypatch.setattr(investigations, "load_spec", _boom)
    body, status = views.investigation_run_one(ws, {"investigation": "inv-x"})
    assert status == 400
    assert body == {"error": "bad spec"}


def test_v2_baseline_variant_not_found_404(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)
    _write_spec(ws, "inv-x")
    monkeypatch.setattr(investigations, "load_spec",
                        lambda p: {"variants": [{"name": "other"}],
                                   "baseline": "missing"})
    body, status = views.investigation_run_one(ws, {"investigation": "inv-x"})
    assert status == 404
    assert body == {"error": "baseline variant not found: 'missing'"}


def test_v2_sidecar_not_found_404(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)
    _write_spec(ws, "inv-x")
    monkeypatch.setattr(
        investigations, "load_spec",
        lambda p: {"variants": [{"name": "baseline",
                                 "document": "./composites/baseline.yaml"}],
                   "baseline": "baseline"})
    body, status = views.investigation_run_one(ws, {"investigation": "inv-x"})
    assert status == 404
    assert body["error"].startswith("composite sidecar not found:")


def test_legacy_composite_not_found_404(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)
    _write_spec(ws, "inv-x")
    monkeypatch.setattr(investigations, "load_spec",
                        lambda p: {"composite": "demo"})
    monkeypatch.setattr(composite_lookup, "find_composite_path",
                        lambda ws_root, pkg, name: None)
    body, status = views.investigation_run_one(ws, {"investigation": "inv-x"})
    assert status == 404
    assert body == {"error": "composite not found: demo"}


def test_neither_variants_nor_composite_400(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)
    _write_spec(ws, "inv-x")
    monkeypatch.setattr(investigations, "load_spec", lambda p: {"name": "inv-x"})
    body, status = views.investigation_run_one(ws, {"investigation": "inv-x"})
    assert status == 400
    assert body == {
        "error": "spec has neither 'variants' (v2) nor 'composite' (legacy)"}


# ---------------------------------------------------------------------------
# v2-variants resolution + happy path + viz persistence + metadata
# ---------------------------------------------------------------------------

def test_v2_happy_200_and_viz_persisted(tmp_path, monkeypatch, fixed_run_id):
    ws = _make_ws(tmp_path)
    _write_spec(ws, "inv-x")
    inv_dir = study_spec.study_dir(ws, "inv-x")
    (inv_dir / "composites").mkdir(parents=True, exist_ok=True)
    # flat-state sidecar (no "state" key) → flat-override branch.
    (inv_dir / "composites" / "baseline.yaml").write_text(
        "foo: 1\nbar: 2\n", encoding="utf-8")
    monkeypatch.setattr(
        investigations, "load_spec",
        lambda p: {"variants": [{"name": "baseline",
                                 "document": "./composites/baseline.yaml"}],
                   "baseline": "baseline"})
    monkeypatch.setattr(
        views.subprocess, "run",
        _fake_subprocess(_results_stdout(
            {"agents.0.viz": {"html": "<div>hi</div>"}})))

    body, status = views.investigation_run_one(
        ws, {"investigation": "inv-x", "sim_name": "my run", "steps": 7})

    assert status == 200
    assert body["ok"] is True
    assert body["run_id"] == RID
    assert body["investigation"] == "inv-x"
    assert body["sim_name"] == "my run"
    # viz persisted under <inv>/viz/<run_id>/<safe>.html
    out_path = inv_dir / "viz" / RID / "agents_0_viz.html"
    assert out_path.is_file()
    assert out_path.read_text() == "<div>hi</div>"
    assert body["viz_html"]["agents_0_viz"]["html"] == "<div>hi</div>"
    assert body["viz_html"]["agents_0_viz"]["path"] == str(
        out_path.relative_to(ws))

    # runs_meta row completed, sim_name stamped.
    conn = sqlite3.connect(inv_dir / "runs.db")
    try:
        row = conn.execute(
            "SELECT spec_id, status, n_steps, sim_name FROM runs_meta "
            "WHERE run_id=?", (RID,)).fetchone()
    finally:
        conn.close()
    assert row == ("baseline", "completed", 7, "my run")


def test_default_sim_name_and_label(tmp_path, monkeypatch, fixed_run_id):
    ws = _make_ws(tmp_path)
    _write_spec(ws, "inv-x")
    inv_dir = study_spec.study_dir(ws, "inv-x")
    (inv_dir / "composites").mkdir(parents=True, exist_ok=True)
    (inv_dir / "composites" / "baseline.yaml").write_text("foo: 1\n", encoding="utf-8")
    monkeypatch.setattr(
        investigations, "load_spec",
        lambda p: {"variants": [{"name": "baseline",
                                 "document": "./composites/baseline.yaml"}],
                   "baseline": "baseline"})
    monkeypatch.setattr(views.subprocess, "run",
                        _fake_subprocess(_results_stdout({})))

    body, status = views.investigation_run_one(ws, {"investigation": "inv-x"})
    assert status == 200
    assert body["sim_name"] == "ad-hoc"  # default
    conn = sqlite3.connect(inv_dir / "runs.db")
    try:
        label = conn.execute(
            "SELECT label FROM runs_meta WHERE run_id=?", (RID,)).fetchone()[0]
    finally:
        conn.close()
    assert label == "ad-hoc ad-hoc"  # f"ad-hoc {sim_name}"


# ---------------------------------------------------------------------------
# legacy resolution + {state, parameters} substitution branch
# ---------------------------------------------------------------------------

def test_legacy_happy_200_with_substitution(tmp_path, monkeypatch, fixed_run_id):
    ws = _make_ws(tmp_path)
    _write_spec(ws, "inv-x")
    inv_dir = study_spec.study_dir(ws, "inv-x")
    composite_file = tmp_path / "demo.yaml"
    composite_file.write_text(
        "state:\n  rate: '{{ k }}'\nparameters:\n  k:\n    default: 1\n",
        encoding="utf-8")
    monkeypatch.setattr(investigations, "load_spec",
                        lambda p: {"composite": "demo"})
    monkeypatch.setattr(composite_lookup, "find_composite_path",
                        lambda ws_root, pkg, name: composite_file)
    monkeypatch.setattr(views.subprocess, "run",
                        _fake_subprocess(_results_stdout({})))

    body, status = views.investigation_run_one(
        ws, {"investigation": "inv-x", "overrides": {"k": 42}})
    assert status == 200
    assert body["ok"] is True
    conn = sqlite3.connect(inv_dir / "runs.db")
    try:
        row = conn.execute(
            "SELECT spec_id, status FROM runs_meta WHERE run_id=?",
            (RID,)).fetchone()
    finally:
        conn.close()
    assert row == ("demo", "completed")


# ---------------------------------------------------------------------------
# run FAILURE → 200 with ok:False (NOT 500), row marked failed
# ---------------------------------------------------------------------------

def test_run_failure_returns_200_ok_false(tmp_path, monkeypatch, fixed_run_id):
    ws = _make_ws(tmp_path)
    _write_spec(ws, "inv-x")
    inv_dir = study_spec.study_dir(ws, "inv-x")
    (inv_dir / "composites").mkdir(parents=True, exist_ok=True)
    (inv_dir / "composites" / "baseline.yaml").write_text("foo: 1\n", encoding="utf-8")
    monkeypatch.setattr(
        investigations, "load_spec",
        lambda p: {"variants": [{"name": "baseline",
                                 "document": "./composites/baseline.yaml"}],
                   "baseline": "baseline"})
    tb = "Traceback (most recent call last):\n  ...\nValueError: boom\n"
    monkeypatch.setattr(views.subprocess, "run",
                        _fake_subprocess(_error_stdout(tb)))

    body, status = views.investigation_run_one(ws, {"investigation": "inv-x"})
    assert status == 200  # NOT 500 — failure still 200
    assert body["ok"] is False
    assert body["run_id"] == RID
    assert body["error"] == tb.strip()[-500:]

    conn = sqlite3.connect(inv_dir / "runs.db")
    try:
        row = conn.execute(
            "SELECT status, n_steps FROM runs_meta WHERE run_id=?",
            (RID,)).fetchone()
    finally:
        conn.close()
    assert row == ("failed", 0)


def test_unparseable_results_falls_back_to_empty_viz(tmp_path, monkeypatch,
                                                     fixed_run_id):
    """``@@@RESULTS@@@`` present but the JSON block is junk → empty viz, 200."""
    ws = _make_ws(tmp_path)
    _write_spec(ws, "inv-x")
    inv_dir = study_spec.study_dir(ws, "inv-x")
    (inv_dir / "composites").mkdir(parents=True, exist_ok=True)
    (inv_dir / "composites" / "baseline.yaml").write_text("foo: 1\n", encoding="utf-8")
    monkeypatch.setattr(
        investigations, "load_spec",
        lambda p: {"variants": [{"name": "baseline",
                                 "document": "./composites/baseline.yaml"}],
                   "baseline": "baseline"})
    monkeypatch.setattr(views.subprocess, "run",
                        _fake_subprocess("@@@RESULTS@@@\nnot-json"))
    body, status = views.investigation_run_one(ws, {"investigation": "inv-x"})
    assert status == 200
    assert body["ok"] is True
    assert body["viz_html"] == {}


# ---------------------------------------------------------------------------
# v3/v4 conditions resolution (issue #751) — `baseline` is a list of
# {name, composite (dotted generator), params}; no sidecar files. Resolution
# goes through study_run_state.resolve_study_baseline_state.
# ---------------------------------------------------------------------------

def _v4_spec():
    """A down-converted v4 study: list baseline + synthesised variants."""
    return {
        "name": "mbp-03",
        "baseline": [{
            "name": "bird-coupled-baseline",
            "composite": "v2ecoli.composites.reactor_bird_coupled",
            "params": {"seed": 0, "cache_dir": "out/cache"},
        }],
        "variants": [
            {"name": "kla-sweep-coupled", "composite": None,
             "base_composite": "bird-coupled-baseline",
             "parameter_overrides": {"kla": 120}},
        ],
    }


def test_v4_conditions_happy_200(tmp_path, monkeypatch, fixed_run_id):
    ws = _make_ws(tmp_path)
    _write_spec(ws, "inv-x")
    inv_dir = study_spec.study_dir(ws, "inv-x")
    monkeypatch.setattr(investigations, "load_spec", lambda p: _v4_spec())

    captured = {}

    def _resolve(ws_root, pkg, spec_id, params):
        captured["spec_id"] = spec_id
        captured["params"] = params
        return {"foo": {"bar": 1}}, None

    monkeypatch.setattr(study_run_state, "resolve_study_baseline_state", _resolve)
    monkeypatch.setattr(views.subprocess, "run",
                        _fake_subprocess(_results_stdout({})))

    body, status = views.investigation_run_one(
        ws, {"investigation": "inv-x", "steps": 12,
             "overrides": {"seed": 3}})

    assert status == 200
    assert body["ok"] is True
    # baseline[0] resolved; body overrides win over authored params; n_steps
    # is NOT passed as a generator param (run length comes from `steps`).
    assert captured["spec_id"] == "v2ecoli.composites.reactor_bird_coupled"
    assert captured["params"] == {"seed": 3, "cache_dir": "out/cache"}
    conn = sqlite3.connect(inv_dir / "runs.db")
    try:
        row = conn.execute(
            "SELECT spec_id, status, n_steps FROM runs_meta WHERE run_id=?",
            (RID,)).fetchone()
    finally:
        conn.close()
    assert row == ("v2ecoli.composites.reactor_bird_coupled", "completed", 12)


def test_v4_composite_selector_picks_named_entry(tmp_path, monkeypatch,
                                                 fixed_run_id):
    ws = _make_ws(tmp_path)
    _write_spec(ws, "inv-x")
    spec = _v4_spec()
    spec["baseline"].append({
        "name": "henry-equilibrium",
        "composite": "v2ecoli.composites.henry",
        "params": {"seed": 7},
    })
    monkeypatch.setattr(investigations, "load_spec", lambda p: spec)
    captured = {}
    monkeypatch.setattr(
        study_run_state, "resolve_study_baseline_state",
        lambda ws_root, pkg, spec_id, params: (captured.update(spec_id=spec_id)
                                                or ({"s": {}}, None)))
    monkeypatch.setattr(views.subprocess, "run",
                        _fake_subprocess(_results_stdout({})))

    body, status = views.investigation_run_one(
        ws, {"investigation": "inv-x", "composite": "henry-equilibrium"})
    assert status == 200
    assert captured["spec_id"] == "v2ecoli.composites.henry"


def test_v4_composite_selector_not_found_404(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)
    _write_spec(ws, "inv-x")
    monkeypatch.setattr(investigations, "load_spec", lambda p: _v4_spec())
    body, status = views.investigation_run_one(
        ws, {"investigation": "inv-x", "composite": "nope"})
    assert status == 404
    assert body == {"error": "baseline composite 'nope' not found"}


def test_v4_resolver_error_404(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)
    _write_spec(ws, "inv-x")
    monkeypatch.setattr(investigations, "load_spec", lambda p: _v4_spec())
    monkeypatch.setattr(
        study_run_state, "resolve_study_baseline_state",
        lambda *a, **k: (None, {"error": "generator build failed: boom"}))
    body, status = views.investigation_run_one(ws, {"investigation": "inv-x"})
    assert status == 404
    assert body == {"error": "generator build failed: boom"}


# ---------------------------------------------------------------------------
# state handoff (issue #751, defect 3) — the state must NOT be inlined into
# the `python -c` source: a real composite state is megabytes, and embedding
# it in argv raises OSError(E2BIG, "Argument list too long") before the child
# starts. A registered v4 generator baseline ships only its ref + params (the
# child builds — a generator document holds live edge objects that do not
# survive a JSON round-trip); parent-resolved states travel via
# .pbg/runs/<run_id>/state.json.
# ---------------------------------------------------------------------------

def _capture_script(monkeypatch, stdout):
    """Monkeypatch subprocess.run, capturing the `python -c` script text."""
    captured = {}

    def _run(cmd, *a, **k):
        captured["script"] = cmd[2]
        return types.SimpleNamespace(stdout=stdout, stderr="", returncode=0)

    monkeypatch.setattr(views.subprocess, "run", _run)
    return captured


def _fake_entry(ref: str, declared: dict):
    return types.SimpleNamespace(id=ref, name=ref.rsplit(".", 1)[-1],
                                 parameters=declared)


def test_v4_registered_generator_builds_in_child(tmp_path, monkeypatch,
                                                 fixed_run_id):
    ws = _make_ws(tmp_path)
    _write_spec(ws, "inv-x")
    ref = "v2ecoli.composites.reactor_bird_coupled"
    monkeypatch.setattr(investigations, "load_spec", lambda p: _v4_spec())
    monkeypatch.setattr(views, "_generator_entry",
                        lambda r: _fake_entry(r, {"seed": {"type": "integer"}}))
    captured = _capture_script(monkeypatch, _results_stdout({}))

    body, status = views.investigation_run_one(
        ws, {"investigation": "inv-x", "steps": 3})

    assert status == 200
    assert body["ok"] is True
    # The CHILD builds the composite from the ref; the parent serialized no
    # state for this path.
    assert "build_generator" in captured["script"]
    assert ref in captured["script"]
    assert "state.json" not in captured["script"]


def test_v4_effective_params_reach_the_child(tmp_path, monkeypatch,
                                             fixed_run_id):
    """Baseline params + request overrides (overrides win), filtered to the
    generator's declared set — mirroring resolve_study_baseline_state."""
    ws = _make_ws(tmp_path)
    _write_spec(ws, "inv-x")
    # cache_dir survives the dead-cache drop only if the ParCa cache exists.
    (ws / "out" / "cache").mkdir(parents=True)
    (ws / "out" / "cache" / "initial_state.json").write_text("{}",
                                                             encoding="utf-8")
    monkeypatch.setattr(investigations, "load_spec", lambda p: _v4_spec())
    monkeypatch.setattr(
        views, "_generator_entry",
        lambda r: _fake_entry(r, {"seed": {"type": "integer"},
                                  "cache_dir": {"type": "string"}}))
    captured = _capture_script(monkeypatch, _results_stdout({}))

    body, status = views.investigation_run_one(
        ws, {"investigation": "inv-x", "overrides": {"seed": 7, "bogus": 1},
             "steps": 2})

    assert status == 200
    # The effective config is embedded double-JSON-encoded; assert on the
    # decoded payload rather than the escaped source text. `bogus` is not
    # declared, so it is filtered rather than sent to build_generator (whose
    # ValueError would fire only after minutes of building).
    embedded = re.search(r'build_generator\(.*?json\.loads\((".*?")\)',
                         captured["script"], re.S)
    assert embedded, captured["script"]
    assert json.loads(json.loads(embedded.group(1))) == {
        "seed": 7, "cache_dir": "out/cache"}


def test_sidecar_state_hands_over_via_file_not_argv(tmp_path, monkeypatch,
                                                    fixed_run_id):
    ws = _make_ws(tmp_path)
    _write_spec(ws, "inv-x")
    inv_dir = study_spec.study_dir(ws, "inv-x")
    (inv_dir / "composites").mkdir(parents=True, exist_ok=True)
    marker = "DISTINCTIVE_STATE_VALUE_9c41"
    (inv_dir / "composites" / "baseline.yaml").write_text(
        f"foo: {marker}\n", encoding="utf-8")
    monkeypatch.setattr(
        investigations, "load_spec",
        lambda p: {"variants": [{"name": "baseline",
                                 "document": "./composites/baseline.yaml"}],
                   "baseline": "baseline"})
    captured = _capture_script(monkeypatch, _results_stdout({}))

    body, status = views.investigation_run_one(ws, {"investigation": "inv-x"})

    assert status == 200
    assert body["ok"] is True
    # The state travels via file: the script loads state.json and does NOT
    # carry the state content itself.
    assert "state.json" in captured["script"]
    assert marker not in captured["script"]
    state_path = views.WorkspacePaths.load(ws).pbg / "runs" / RID / "state.json"
    assert state_path.is_file()
    written = json.loads(state_path.read_text(encoding="utf-8"))
    assert written["foo"] == marker  # emitter-injected copy of the state


def test_large_state_does_not_grow_argv(tmp_path, monkeypatch, fixed_run_id):
    """Regression for E2BIG: a multi-MB state must not inflate the argv."""
    ws = _make_ws(tmp_path)
    _write_spec(ws, "inv-x")
    inv_dir = study_spec.study_dir(ws, "inv-x")
    (inv_dir / "composites").mkdir(parents=True, exist_ok=True)
    big = {"blob": "x" * 3_000_000}  # ~3 MB — over common ARG_MAX limits
    (inv_dir / "composites" / "baseline.json").write_text(
        json.dumps(big), encoding="utf-8")
    monkeypatch.setattr(
        investigations, "load_spec",
        lambda p: {"variants": [{"name": "baseline",
                                 "document": "./composites/baseline.json"}],
                   "baseline": "baseline"})
    captured = _capture_script(monkeypatch, _results_stdout({}))

    body, status = views.investigation_run_one(ws, {"investigation": "inv-x"})

    assert status == 200
    assert len(captured["script"]) < 20_000, \
        "state leaked into the python -c source"


def test_v4_local_shorthand_ships_resolved_id_to_child(tmp_path, monkeypatch,
                                                       fixed_run_id):
    """A `local:<name>` baseline must ship entry.id to the child — _REGISTRY
    is keyed by the dotted id, never the shorthand, so shipping the raw ref
    raises KeyError in the child."""
    ws = _make_ws(tmp_path)
    _write_spec(ws, "inv-x")
    spec = _v4_spec()
    spec["baseline"][0]["composite"] = "local:reactor_bird_coupled"
    resolved_id = "v2ecoli.composites.reactor_bird_coupled"
    monkeypatch.setattr(investigations, "load_spec", lambda p: spec)
    monkeypatch.setattr(
        views, "_generator_entry",
        lambda r: _fake_entry(resolved_id, {"seed": {"type": "integer"}}))
    captured = _capture_script(monkeypatch, _results_stdout({}))

    body, status = views.investigation_run_one(
        ws, {"investigation": "inv-x", "steps": 2})

    assert status == 200
    assert body["ok"] is True
    assert f'_REGISTRY[{json.dumps(resolved_id)}]' in captured["script"]
    assert "local:" not in captured["script"]


# ---------------------------------------------------------------------------
# run-target gate (#1630 follow-up): this ad-hoc "Duplicate run" runs a
# composite in a LOCAL detached subprocess (the `subprocess.run` below). If the
# workspace resolves to a deployment run target, running here would put on THIS
# host the work the operator said to run on the named backend — the same
# silent-wrong-target hole #1630 closed on the other run entrypoints, left open
# here. It must refuse (409) rather than run local, and a NAMED backend that
# cannot be reached must surface the unreachable 502 from `resolve_run_target`,
# not be swallowed into a local run. The gate sits AFTER request validation, so
# a malformed request is still its own 4xx.
# ---------------------------------------------------------------------------

def test_deployment_target_refuses_409_without_running(tmp_path, monkeypatch):
    """target == 'deployment' → 409, and NOTHING local runs: neither the spec
    loader nor the subprocess is reached."""
    ws = _make_ws(tmp_path)
    monkeypatch.setattr(remote_pinned, "resolve_run_target", lambda p: "deployment")

    def _no_spec(*a, **k):
        raise AssertionError("load_spec must not be reached under a deployment target")

    def _no_run(*a, **k):
        raise AssertionError("subprocess.run must not be reached under a deployment target")

    monkeypatch.setattr(investigations, "load_spec", _no_spec)
    monkeypatch.setattr(views.subprocess, "run", _no_run)

    body, status = views.investigation_run_one(ws, {"investigation": "inv-x", "steps": 3})

    assert status == 409
    assert body["run_target"] == "deployment"
    assert body["name"] == "inv-x"
    # the hint must name a run path that actually honors the target, not just say "no"
    assert "/api/composite-test-run" in body["hint"]


def test_unreachable_named_backend_propagates_502(tmp_path, monkeypatch):
    """A NAMED backend that cannot be reached raises APIError(502) from
    resolve_run_target; the view must let it propagate (the app's APIError
    handler renders the 502) rather than fall through to a local subprocess."""
    ws = _make_ws(tmp_path)

    def _unreachable(p):
        raise APIError(502, "the backend named for this workbench cannot be reached",
                       phase="unreachable", reachable=False)

    def _no_run(*a, **k):
        raise AssertionError("subprocess.run must not be reached when the backend is unreachable")

    monkeypatch.setattr(remote_pinned, "resolve_run_target", _unreachable)
    monkeypatch.setattr(views.subprocess, "run", _no_run)

    with pytest.raises(APIError) as ei:
        views.investigation_run_one(ws, {"investigation": "inv-x", "steps": 3})
    assert ei.value.status_code == 502


def test_missing_inv_still_400_under_a_deployment_target(tmp_path, monkeypatch):
    """Ordering: request validation comes first, so a missing investigation is
    still a 400 rather than being masked by the run-target gate."""
    ws = _make_ws(tmp_path)
    monkeypatch.setattr(remote_pinned, "resolve_run_target", lambda p: "deployment")
    body, status = views.investigation_run_one(ws, {})
    assert status == 400
    assert body == {"error": "investigation required"}


def test_local_target_runs_as_before(tmp_path, monkeypatch, fixed_run_id):
    """A laptop (no pin, no named backend) resolves to 'local' and the ad-hoc
    run behaves exactly as before the gate: the happy 200 path is unchanged."""
    ws = _make_ws(tmp_path)
    _write_spec(ws, "inv-x")
    inv_dir = study_spec.study_dir(ws, "inv-x")
    (inv_dir / "composites").mkdir(parents=True, exist_ok=True)
    (inv_dir / "composites" / "baseline.yaml").write_text("foo: 1\n", encoding="utf-8")
    monkeypatch.setattr(remote_pinned, "resolve_run_target", lambda p: "local")
    monkeypatch.setattr(
        investigations, "load_spec",
        lambda p: {"variants": [{"name": "baseline",
                                 "document": "./composites/baseline.yaml"}],
                   "baseline": "baseline"})
    monkeypatch.setattr(views.subprocess, "run", _fake_subprocess(_results_stdout({})))

    body, status = views.investigation_run_one(ws, {"investigation": "inv-x", "steps": 2})

    assert status == 200
    assert body["ok"] is True
    assert body["run_id"] == RID
