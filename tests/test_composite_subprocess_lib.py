"""Behavioral tests for the extracted ``lib/composite_subprocess.py`` engine.

E1 lib-extraction: ``run_composite_subprocess`` / ``invoke_v2ecoli_workflow`` /
``strip_process_instances`` moved out of ``server.py`` (parameterized on
``ws_root``), with server name-shims left behind for the live call-sites. These
tests NEVER run a real simulation — ``subprocess.run`` is always monkeypatched —
and assert the built command/script, the runtime-emitter resolution, the
timeout/returncode handling, the ``(dict, status)`` return shape, and that the
server shims are behavior-identical to the lib functions.
"""

import ast
import json
import re
import subprocess
import sys
import types
from pathlib import Path

import pytest

from conftest import register_generator

from vivarium_workbench.lib import composite_subprocess as cs
from vivarium_workbench.lib import composite_runs as cr
from vivarium_workbench.lib import _root
import pbg_superpowers.composite_generator as cg


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok_stdout(results=None, viz_html=None):
    """Build a subprocess stdout that the runner parses as success."""
    payload = {"results": results or {"foo.bar": [1, 2, 3]},
               "viz_html": viz_html or {"foo": "<html/>"}}
    return "noise\n@@@RESULTS@@@\n" + json.dumps(payload) + "\n"


class FakeRun:
    """Records calls to ``subprocess.run`` and returns/raises a canned result."""

    def __init__(self, *, returncode=0, stdout="", stderr="", exc=None):
        self.calls = []
        self._returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self._exc = exc

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        if self._exc is not None:
            raise self._exc
        return types.SimpleNamespace(
            returncode=self._returncode, stdout=self._stdout, stderr=self._stderr)

    @property
    def cmd(self):
        return self.calls[0][0]

    @property
    def script(self):
        return self.calls[0][0][2]

    @property
    def kwargs(self):
        return self.calls[0][1]


def _extract_payload(script):
    """Pull the ``_payload = {...}`` dict literal out of a generator-path script."""
    m = re.search(r"^\s*_payload = (\{.*\})\s*$", script, re.MULTILINE)
    assert m, "no _payload literal found in generator-path script"
    return ast.literal_eval(m.group(1))


def _make_ws(tmp_path, runtime=None):
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    data = {}
    if runtime is not None:
        data["runtime"] = runtime
    import yaml
    (ws / "workspace.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    return ws


def _gen_spec(monkeypatch, spec_id="gen-spec"):
    """Force the generator path: spec_id present in the registry."""
    monkeypatch.setattr(cg, "discover_generators", lambda: None)
    register_generator(spec_id)
    return spec_id


def _nongen_spec(monkeypatch, spec_id="nongen-spec-xyz"):
    """Force the legacy state-serialization path: spec_id absent, registry
    non-empty so ``discover_generators`` is not invoked."""
    monkeypatch.setattr(cg, "discover_generators", lambda: None)
    register_generator("_dummy_keep_registry_truthy")
    assert spec_id not in cg._REGISTRY
    return spec_id


def _run_kwargs(ws, db_file, *, spec_id, run_id, **over):
    base = dict(pkg="mypkg", state={}, steps=4, db_file=str(db_file),
                run_id=run_id, spec_id=spec_id, label="lbl")
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# run_composite_subprocess — generator path
# ---------------------------------------------------------------------------

def test_generator_path_success_shape(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path, runtime={"default_emitter": "sqlite"})
    db = tmp_path / "runs.db"
    spec = _gen_spec(monkeypatch)
    fake = FakeRun(stdout=_ok_stdout())
    monkeypatch.setattr(cs.subprocess, "run", fake)

    resp, code = cs.run_composite_subprocess(
        ws, **_run_kwargs(ws, db, spec_id=spec, run_id="r1"))

    assert code == 200
    assert resp["simulation_id"] == "r1"
    assert resp["results"] == {"foo.bar": [1, 2, 3]}
    assert resp["viz_html"] == {"foo": "<html/>"}
    assert resp["steps"] == 4
    # Built command is [python, "-c", <script>], executed in ws_root.
    assert fake.cmd[0] == sys.executable
    assert fake.cmd[1] == "-c"
    assert str(fake.kwargs["cwd"]) == str(ws)
    # Generator path embeds the registry build, NOT a state tempfile load.
    assert "build_generator(entry" in fake.script
    assert "object_hook=bigraph_json_hook" not in fake.script


def test_generator_script_is_byte_identical_to_sidecar(tmp_path, monkeypatch):
    """The script passed to subprocess.run must be byte-identical to the
    ``sims/<run_id>.subprocess.py`` sidecar the runner writes."""
    ws = _make_ws(tmp_path)
    db = tmp_path / "d" / "runs.db"
    db.parent.mkdir()
    spec = _gen_spec(monkeypatch)
    fake = FakeRun(stdout=_ok_stdout())
    monkeypatch.setattr(cs.subprocess, "run", fake)

    cs.run_composite_subprocess(ws, **_run_kwargs(ws, db, spec_id=spec, run_id="rS"))

    sidecar = (db.parent / "sims" / "rS.subprocess.py").read_text()
    assert sidecar == fake.script


def test_runtime_emitter_resolution_workspace_default(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path, runtime={
        "default_emitter": "xarray", "max_generations": 7, "single_daughters": True})
    db = tmp_path / "runs.db"
    spec = _gen_spec(monkeypatch)
    fake = FakeRun(stdout=_ok_stdout())
    monkeypatch.setattr(cs.subprocess, "run", fake)

    cs.run_composite_subprocess(ws, **_run_kwargs(ws, db, spec_id=spec, run_id="rW"))

    payload = _extract_payload(fake.script)
    assert payload["default_emitter"] == "xarray"
    assert payload["max_generations"] == 7
    assert payload["single_daughters"] is True
    # zarr store derives from db_file + run_id.
    assert payload["zarr_store"] == str(Path(db).with_suffix("")) + ".rW.zarr"


def test_runtime_emitter_per_study_override_wins(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path, runtime={
        "default_emitter": "xarray", "max_generations": 7, "single_daughters": True})
    db = tmp_path / "runs.db"
    spec = _gen_spec(monkeypatch)
    fake = FakeRun(stdout=_ok_stdout())
    monkeypatch.setattr(cs.subprocess, "run", fake)

    cs.run_composite_subprocess(
        ws, **_run_kwargs(ws, db, spec_id=spec, run_id="rO",
                          study_emitter="sqlite", study_max_generations=2,
                          study_single_daughters=False))

    payload = _extract_payload(fake.script)
    assert payload["default_emitter"] == "sqlite"   # per-study override
    assert payload["max_generations"] == 2
    assert payload["single_daughters"] is False


def test_reads_ws_root_workspace_yaml_not_a_global(tmp_path, monkeypatch):
    """Resolution reads the passed ws_root/workspace.yaml — point the global
    workspace root at an unrelated dir and confirm the ws_root arg is used."""
    ws = _make_ws(tmp_path, runtime={"max_generations": 9})
    other = _make_ws(tmp_path / "elsewhere", runtime={"max_generations": 1})
    _root.set_workspace_root(other)
    db = tmp_path / "runs.db"
    spec = _gen_spec(monkeypatch)
    fake = FakeRun(stdout=_ok_stdout())
    monkeypatch.setattr(cs.subprocess, "run", fake)

    cs.run_composite_subprocess(ws, **_run_kwargs(ws, db, spec_id=spec, run_id="rR"))
    assert _extract_payload(fake.script)["max_generations"] == 9


def test_runtime_defaults_when_no_workspace_yaml(tmp_path, monkeypatch):
    ws = tmp_path / "ws_noyaml"
    ws.mkdir()  # no workspace.yaml -> defaults
    db = tmp_path / "runs.db"
    spec = _gen_spec(monkeypatch)
    fake = FakeRun(stdout=_ok_stdout())
    monkeypatch.setattr(cs.subprocess, "run", fake)

    cs.run_composite_subprocess(ws, **_run_kwargs(ws, db, spec_id=spec, run_id="rD"))
    payload = _extract_payload(fake.script)
    assert payload["default_emitter"] == "sqlite"
    assert payload["max_generations"] == 3
    assert payload["single_daughters"] is False


# ---------------------------------------------------------------------------
# run_composite_subprocess — non-generator (state-serialization) path
# ---------------------------------------------------------------------------

def test_non_generator_path_serializes_state(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)
    db = tmp_path / "runs.db"
    spec = _nongen_spec(monkeypatch)
    fake = FakeRun(stdout=_ok_stdout())
    monkeypatch.setattr(cs.subprocess, "run", fake)

    resp, code = cs.run_composite_subprocess(
        ws, **_run_kwargs(ws, db, spec_id=spec, run_id="n1"))

    assert code == 200
    assert resp["simulation_id"] == "n1"
    assert resp["results"] == {"foo.bar": [1, 2, 3]}
    # Legacy path loads a serialized state tempfile in the child; the generator
    # build helper must be ABSENT.
    assert "object_hook=bigraph_json_hook" in fake.script
    assert "build_generator(entry" not in fake.script
    # Command shape identical to the generator path.
    assert fake.cmd[0] == sys.executable and fake.cmd[1] == "-c"
    assert str(fake.kwargs["cwd"]) == str(ws)


def test_non_generator_script_is_byte_identical_to_sidecar(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)
    db = tmp_path / "d" / "runs.db"
    db.parent.mkdir()
    spec = _nongen_spec(monkeypatch)
    fake = FakeRun(stdout=_ok_stdout())
    monkeypatch.setattr(cs.subprocess, "run", fake)

    cs.run_composite_subprocess(ws, **_run_kwargs(ws, db, spec_id=spec, run_id="nS"))
    sidecar = (db.parent / "sims" / "nS.subprocess.py").read_text()
    assert sidecar == fake.script


# ---------------------------------------------------------------------------
# run_composite_subprocess — error / timeout / returncode handling
# ---------------------------------------------------------------------------

def test_run_child_error_block_returns_502(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)
    db = tmp_path / "runs.db"
    spec = _gen_spec(monkeypatch)
    fake = FakeRun(stdout="@@@ERROR@@@\nTraceback: boom\n")
    monkeypatch.setattr(cs.subprocess, "run", fake)

    resp, code = cs.run_composite_subprocess(
        ws, **_run_kwargs(ws, db, spec_id=spec, run_id="e1"))
    assert code == 502
    assert resp["error"] == "run failed"
    assert "boom" in resp["traceback"]


def test_run_unparseable_output_returns_502(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)
    db = tmp_path / "runs.db"
    spec = _gen_spec(monkeypatch)
    fake = FakeRun(stdout="no markers here", stderr="stderr-text")
    monkeypatch.setattr(cs.subprocess, "run", fake)

    resp, code = cs.run_composite_subprocess(
        ws, **_run_kwargs(ws, db, spec_id=spec, run_id="e2"))
    assert code == 502
    assert resp["error"] == "could not parse run output"
    assert resp["stderr"] == "stderr-text"


def test_run_timeout_returns_504(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)
    db = tmp_path / "runs.db"
    spec = _gen_spec(monkeypatch)
    fake = FakeRun(exc=subprocess.TimeoutExpired(cmd="x", timeout=1))
    monkeypatch.setattr(cs.subprocess, "run", fake)

    resp, code = cs.run_composite_subprocess(
        ws, **_run_kwargs(ws, db, spec_id=spec, run_id="e3", timeout=1))
    assert code == 504
    assert resp["error"] == "run timed out"


def test_run_duplicate_run_id_returns_500(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)
    db = tmp_path / "runs.db"
    spec = _gen_spec(monkeypatch)
    fake = FakeRun(stdout=_ok_stdout())
    monkeypatch.setattr(cs.subprocess, "run", fake)

    _, code1 = cs.run_composite_subprocess(
        ws, **_run_kwargs(ws, db, spec_id=spec, run_id="dup"))
    assert code1 == 200
    resp, code2 = cs.run_composite_subprocess(
        ws, **_run_kwargs(ws, db, spec_id=spec, run_id="dup"))
    assert code2 == 500
    assert "duplicate run_id" in resp["error"]


# ---------------------------------------------------------------------------
# v2ecoli applicability gate (review CRITICAL 2)
#
# Task 6 flipped the GLOBAL default emitter to "xarray". The xarray study-run
# write path in the child script is wired ONLY for v2ecoli (its multigen loop in
# v2ecoli.library.xarray_run); the generic flat-Step xarray study-run loop is the
# deferred Task 3. So a study run on a NON-v2ecoli workspace whose default
# resolves to "xarray" must NOT hit the v2ecoli import — it must fall back to the
# single-generation sqlite path and SUCCEED. These tests lock that seam (it had
# no coverage: the e2e tests exercise run_with_emitter directly, never
# composite_subprocess).
# ---------------------------------------------------------------------------

# A minimal, real, registered @composite_generator workspace package. Imported in
# the parent (so the parent takes the generator path) and `from genpkg.core import
# build_core`'d in the child (which registers the spec + Counter in the child's
# core). No v2ecoli anywhere.
_GENPKG_CORE_SRC = '''
from bigraph_schema import allocate_core
from process_bigraph.composite import Process
from pbg_superpowers.composite_generator import composite_generator


class Counter(Process):
    config_schema = {}
    def inputs(self): return {"value": "float"}
    def outputs(self): return {"value": "float"}
    def update(self, state, interval): return {"value": 1.0}


def build_core():
    core = allocate_core()
    core.register_link("Counter", Counter)
    return core


@composite_generator(name="counter-seam", parameters={})
def make_counter(core=None):
    return {
        "counter": {"_type": "process", "address": "local:Counter", "config": {},
                    "inputs": {"value": ["counter_store", "value"]},
                    "outputs": {"value": ["counter_store", "value"]},
                    "interval": 1.0},
        "counter_store": {"value": 0.0},
    }
'''


def _make_genpkg_ws(tmp_path, *, default_emitter="xarray"):
    """A non-v2ecoli workspace whose runtime.default_emitter resolves to xarray,
    carrying a real registered composite generator (``genpkg``)."""
    ws = tmp_path / "ws"
    (ws / "genpkg").mkdir(parents=True, exist_ok=True)
    (ws / "genpkg" / "__init__.py").write_text("", encoding="utf-8")
    (ws / "genpkg" / "core.py").write_text(_GENPKG_CORE_SRC, encoding="utf-8")
    import yaml
    (ws / "workspace.yaml").write_text(
        yaml.safe_dump({"runtime": {"default_emitter": default_emitter}}),
        encoding="utf-8")
    return ws


def _register_genpkg(ws):
    """Import the real generator package in ws into the PARENT so the parent
    takes the generator path; return (spec_id, cleanup)."""
    sys.path.insert(0, str(ws))
    import genpkg.core  # noqa: F401
    spec_id = next(k for k in cg._REGISTRY if k.endswith("counter-seam"))

    def _cleanup():
        try:
            sys.path.remove(str(ws))
        except ValueError:
            pass
        sys.modules.pop("genpkg.core", None)
        sys.modules.pop("genpkg", None)
    return spec_id, _cleanup


def test_xarray_default_child_script_gates_on_v2ecoli(tmp_path, monkeypatch):
    """For an xarray-default workspace the generated child script must gate the
    v2ecoli xarray (and multigen-sqlite) branches on v2ecoli being importable —
    NOT on default_emitter alone — so a non-v2ecoli workspace can fall back."""
    ws = _make_genpkg_ws(tmp_path, default_emitter="xarray")
    db = ws / "runs.db"
    spec_id, _cleanup = _register_genpkg(ws)
    fake = FakeRun(stdout=_ok_stdout())
    monkeypatch.setattr(cs.subprocess, "run", fake)
    try:
        cs.run_composite_subprocess(
            ws, pkg="genpkg", state={}, steps=4, db_file=str(db),
            run_id="g1", spec_id=spec_id, label="g")
    finally:
        _cleanup()

    script = fake.script
    # The xarray decision is gated on v2ecoli importability, not just the name.
    assert "import v2ecoli as _v2ecoli" in script
    assert "_payload.get('default_emitter') == 'xarray' and _v2ecoli_available" in script
    # The defensive submodule-import guard is present.
    assert "from v2ecoli.library.xarray_run import" in script
    # The multigen-sqlite branch (also v2ecoli-only) is gated too.
    assert "_mg > 1 and _v2ecoli_available" in script


def test_xarray_default_nonv2ecoli_multigen_falls_back_to_sqlite(tmp_path, monkeypatch):
    """The documented boundary (FU2b): a non-v2ecoli, xarray-default,
    MULTI-generation study run still resolves to sqlite — there is NO generic
    division-aware xarray drive, so multi-gen non-v2ecoli runs keep the sqlite
    path. (Single-gen now writes zarr — see the test below.) Pre-FU2b this whole
    case hit an unconditional ``from v2ecoli.library.xarray_run import ...`` →
    ImportError → 502."""
    import importlib.util
    if importlib.util.find_spec("v2ecoli") is not None:
        pytest.skip("v2ecoli is importable here; this test covers its ABSENCE")

    ws = _make_genpkg_ws(tmp_path, default_emitter="xarray")
    db = ws / "runs.db"
    spec_id, _cleanup = _register_genpkg(ws)
    try:
        # Default emitter genuinely resolves to xarray for this workspace (so the
        # test is exercising the boundary path, not a sqlite shortcut).
        from vivarium_workbench.lib import emitters as _em
        assert _em.default_emitter({"runtime": {"default_emitter": "xarray"}}, None) == "xarray"

        resp, code = cs.run_composite_subprocess(
            ws, pkg="genpkg", state={}, steps=4, db_file=str(db),
            run_id="seam1", spec_id=spec_id, label="seam",
            emit_paths=["counter_store"], study_max_generations=2)
    finally:
        _cleanup()

    # A generic multi-gen study run SUCCEEDS (writing sqlite), it does not error.
    assert code == 200, resp
    # It resolved to sqlite: the runs.db exists, and NO zarr store (neither the
    # v2ecoli ``<db_stem>.<run_id>.zarr`` nor the generic ``<db_dir>/<run_id>.zarr``
    # name) was created.
    assert db.exists()
    assert not Path(str(db.with_suffix("")) + ".seam1.zarr").exists(), \
        "v2ecoli xarray branch fired on a non-v2ecoli workspace"
    assert not (ws / "seam1.zarr").exists(), \
        "generic xarray fired on a MULTI-gen non-v2ecoli run (no division-aware drive yet)"


def test_xarray_default_nonv2ecoli_single_gen_writes_zarr(tmp_path, monkeypatch):
    """FU2b seam: a non-v2ecoli, xarray-default, SINGLE-generation study run now
    routes through the generic flat-Step XArray write path
    (``emitters.run_with_emitter``) and produces a real ZARR store at the STUDY
    convention (``runs.<run_id>.zarr`` next to runs.db), so the dashboard's study
    read-path actually finds it. Asserts BOTH that the store lands at the study
    path AND that ``study_run_state.zarr_store_for_sim`` resolves it (returns the
    store, not None) — the regression that the old hardcoded-path assertion
    masked. Also round-trips the data through the read broker (``read_source`` /
    ``reader_for``). Runs for real (no subprocess mock); skips when v2ecoli is
    importable or xarray/zarr are absent."""
    import importlib.util
    if importlib.util.find_spec("v2ecoli") is not None:
        pytest.skip("v2ecoli is importable here; this test covers its ABSENCE")
    pytest.importorskip("xarray")
    pytest.importorskip("zarr")

    ws = _make_genpkg_ws(tmp_path, default_emitter="xarray")
    db = ws / "runs.db"
    spec_id, _cleanup = _register_genpkg(ws)
    try:
        # 6 steps > the flat-Step buffer (size 3) so the run flushes real data
        # (a sub-buffer run would empty-store→sqlite-fall-back inside the broker).
        # Pass sim_name so runs_meta carries it and the study resolver can map
        # sim_name → run_id → zarr path.
        resp, code = cs.run_composite_subprocess(
            ws, pkg="genpkg", state={}, steps=6, db_file=str(db),
            run_id="zarr1", spec_id=spec_id, label="z", sim_name="z-sim",
            emit_paths=["counter_store"], study_max_generations=1)
    finally:
        _cleanup()

    assert code == 200, resp
    # (1) The store lands at the STUDY convention ``runs.<run_id>.zarr``
    # (== ``<db_stem>.<run_id>.zarr`` next to runs.db) — NOT the old generic
    # ``<out_dir>/<run_id>.zarr`` name, which the study resolver can't find.
    study_store = ws / "runs.zarr1.zarr"
    assert study_store.exists(), \
        f"generic single-gen xarray did not write the study-convention store: {resp}"
    assert not (ws / "zarr1.zarr").exists(), \
        "store still written at the generic <run_id>.zarr name (resolver-blind)"

    # (2) The dashboard's study read-path RESOLVES it: zarr_store_for_sim maps
    # sim_name → latest completed run_id → the on-disk zarr store. This is the
    # exact resolver the study UI uses; a None here is the original Critical.
    from vivarium_workbench.lib import study_run_state
    resolved_study = study_run_state.zarr_store_for_sim(db, "z-sim")
    assert resolved_study is not None, \
        "study read-path could not resolve the written zarr store (regression)"
    assert Path(resolved_study) == study_store

    # (3) Data round-trips through the read broker (read_source / reader_for).
    from vivarium_workbench.lib import emitters as _em
    kind, resolved = _em.read_source(str(study_store))
    assert kind == "zarr"
    assert Path(resolved) == study_store
    reader = _em.reader_for("zarr")
    times, values = reader(Path(resolved), "counter_store/value")
    assert isinstance(times, list) and isinstance(values, list)


# ---------------------------------------------------------------------------
# invoke_v2ecoli_workflow — all 4 status paths
# ---------------------------------------------------------------------------

def test_invoke_workflow_success_200(tmp_path, monkeypatch):
    out_dir = tmp_path / "out" / "run-ok"
    out_dir.mkdir(parents=True)
    fake = FakeRun(returncode=0, stdout="ok")
    monkeypatch.setattr(cs.subprocess, "run", fake)

    resp, code = cs.invoke_v2ecoli_workflow("cfg.json", out_dir, tmp_path, 5)
    assert code == 200
    assert resp == {"simulation_id": "run-ok", "ensemble": True,
                    "out_dir": str(out_dir), "steps": 0}
    assert fake.cmd[0].endswith("v2ecoli-workflow")
    assert fake.cmd == [str(tmp_path / ".venv" / "bin" / "v2ecoli-workflow"),
                        "--config", "cfg.json", "--out", str(out_dir)]


def test_invoke_workflow_timeout_504(tmp_path, monkeypatch):
    out_dir = tmp_path / "out" / "run-to"
    out_dir.mkdir(parents=True)
    monkeypatch.setattr(cs.subprocess, "run",
                        FakeRun(exc=subprocess.TimeoutExpired("x", 1)))
    resp, code = cs.invoke_v2ecoli_workflow("cfg.json", out_dir, tmp_path, 1)
    assert code == 504
    assert "timed out" in resp["error"]


def test_invoke_workflow_missing_binary_502(tmp_path, monkeypatch):
    out_dir = tmp_path / "out" / "run-nf"
    out_dir.mkdir(parents=True)
    monkeypatch.setattr(cs.subprocess, "run", FakeRun(exc=FileNotFoundError()))
    resp, code = cs.invoke_v2ecoli_workflow("cfg.json", out_dir, tmp_path, 5)
    assert code == 502
    assert "v2ecoli-workflow" in resp["error"]


def test_invoke_workflow_nonzero_returncode_502(tmp_path, monkeypatch):
    out_dir = tmp_path / "out" / "run-err"
    out_dir.mkdir(parents=True)
    monkeypatch.setattr(cs.subprocess, "run",
                        FakeRun(returncode=3, stdout="O", stderr="E"))
    resp, code = cs.invoke_v2ecoli_workflow("cfg.json", out_dir, tmp_path, 5)
    assert code == 502
    assert resp["error"] == "ensemble run failed"
    assert resp["stdout"] == "O" and resp["stderr"] == "E"


# ---------------------------------------------------------------------------
# strip_process_instances
# ---------------------------------------------------------------------------

def test_strip_process_instances():
    state = {
        "proc": {
            "_type": "process",
            "address": "local:Foo",
            "config": {"k": 1},
            "instance": object(),
            "_inputs": {"a": "b"},
            "_outputs": {"c": "d"},
            "keep": 1,
        },
        "step": {
            "_type": "step",
            "instance": object(),
            "_inputs": {},
            "nested": [{"_type": "process", "instance": object(), "v": 2}],
        },
        "plain": {"instance": "I-AM-NOT-AN-EDGE", "x": 5},
        "leaf": 42,
    }
    out = cs.strip_process_instances(state)
    # Edge sidecars stripped.
    assert "instance" not in out["proc"]
    assert "_inputs" not in out["proc"] and "_outputs" not in out["proc"]
    assert out["proc"]["address"] == "local:Foo"
    assert out["proc"]["keep"] == 1
    assert "instance" not in out["step"]
    # Lists recursed.
    assert "instance" not in out["step"]["nested"][0]
    assert out["step"]["nested"][0]["v"] == 2
    # Non-edge dict keeps its (coincidentally-named) 'instance' key.
    assert out["plain"]["instance"] == "I-AM-NOT-AN-EDGE"
    assert out["leaf"] == 42
    # Original not mutated.
    assert "instance" in state["proc"]
