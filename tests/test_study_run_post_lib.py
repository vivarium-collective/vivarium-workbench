"""Behavioral tests for the extracted ``lib/study_run_post.py`` helpers.

E3 lib-extraction: the post-run side-effect stage moved out of ``server.py``
into ``vivarium_workbench.lib.study_run_post`` (parameterized on ``ws_root``),
with server name-shims left behind for the live call-sites:

  ``render_study_visualizations`` / ``run_post_run_scripts`` /
  ``run_study_analyses`` (public, shimmed) and the leaf helpers
  ``build_analysis_options`` / ``purge_stale_viz`` / ``latest_run_timestamp``.

These tests NEVER run a real simulation, script, or viz render — every heavy
bit (``subprocess.run``, the v2ecoli analysis runner, the in-process Composite
viz renderer, the core build) is monkeypatched / faked. They assert the
side-effect shapes, that ``render_study_visualizations`` reads the passed
``ws_root`` (not a global), and that the server shims are behavior-identical to
the lib functions.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import types
from pathlib import Path

import yaml

from vivarium_workbench.lib import study_run_post as srp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_runs_db(study_dir: Path, *, started=100.0, completed=None) -> Path:
    """Create a study_dir/runs.db with a runs_meta row."""
    runs_db = study_dir / "runs.db"
    conn = sqlite3.connect(str(runs_db))
    try:
        conn.execute(
            "CREATE TABLE runs_meta (sim_name TEXT, run_id TEXT, status TEXT, "
            "started_at REAL, completed_at REAL)"
        )
        conn.execute(
            "INSERT INTO runs_meta VALUES (?, ?, ?, ?, ?)",
            ("sim", "r1", "completed", started, completed),
        )
        conn.commit()
    finally:
        conn.close()
    return runs_db


def _set_mtime(path: Path, ts: float) -> None:
    import os
    os.utime(path, (ts, ts))


# ---------------------------------------------------------------------------
# latest_run_timestamp
# ---------------------------------------------------------------------------

def test_latest_run_timestamp_prefers_completed(tmp_path):
    sd = tmp_path / "s"
    sd.mkdir()
    _make_runs_db(sd, started=100.0, completed=200.0)
    assert srp.latest_run_timestamp(sd / "runs.db") == 200.0


def test_latest_run_timestamp_falls_back_to_started(tmp_path):
    sd = tmp_path / "s"
    sd.mkdir()
    _make_runs_db(sd, started=150.0, completed=None)
    assert srp.latest_run_timestamp(sd / "runs.db") == 150.0


def test_latest_run_timestamp_missing_db_returns_none(tmp_path):
    assert srp.latest_run_timestamp(tmp_path / "nope.db") is None


# ---------------------------------------------------------------------------
# purge_stale_viz
# ---------------------------------------------------------------------------

def test_purge_stale_viz_deletes_old_keeps_fresh_and_comparative(tmp_path):
    sd = tmp_path / "study"
    viz = sd / "viz"
    viz.mkdir(parents=True)
    _make_runs_db(sd, started=1000.0, completed=1000.0)  # cutoff = 1000.0

    stale = viz / "stale.html"
    stale.write_text("old", encoding="utf-8")
    _set_mtime(stale, 500.0)            # older than cutoff -> purged

    fresh = viz / "fresh.html"
    fresh.write_text("new", encoding="utf-8")
    _set_mtime(fresh, 500.0)            # old, BUT in just_written -> kept

    comp = viz / "comparative_x.html"
    comp.write_text("comp", encoding="utf-8")
    _set_mtime(comp, 500.0)            # old, BUT comparative_ -> kept

    keep = viz / "current.html"
    keep.write_text("cur", encoding="utf-8")
    _set_mtime(keep, 2000.0)           # newer than cutoff -> kept

    srp.purge_stale_viz(sd, [str(fresh)])

    assert not stale.exists()
    assert fresh.exists()
    assert comp.exists()
    assert keep.exists()


def test_purge_stale_viz_noop_without_runs_db(tmp_path):
    sd = tmp_path / "study"
    viz = sd / "viz"
    viz.mkdir(parents=True)
    old = viz / "a.html"
    old.write_text("x", encoding="utf-8")
    _set_mtime(old, 1.0)
    srp.purge_stale_viz(sd, [])       # no runs.db -> no-op
    assert old.exists()


# ---------------------------------------------------------------------------
# build_analysis_options
# ---------------------------------------------------------------------------

def _inject_analysis_registry(name_to_scale: dict[str, str]) -> None:
    """Stub both v2ecoli.workflow.analysis (the registry) AND the sibling
    v2ecoli.workflow.analyses package (plural -- what build_analysis_options
    now also imports, see item 39 continued) so these tests, which exist to
    check the grouping/error-handling LOGIC, don't need a real v2ecoli
    install. The plural stub is an empty no-op module: these tests
    pre-populate the registry directly, they don't rely on real
    __init_subclass__ registration -- that's what
    test_build_analysis_options_real_import_chain_registers_plural_package
    below exists to cover, without any mocking."""
    registry = {n: type(n, (), {"scale": s}) for n, s in name_to_scale.items()}
    mod = types.ModuleType("v2ecoli.workflow.analysis")
    mod.ANALYSIS_REGISTRY = registry  # type: ignore[attr-defined]
    sys.modules["v2ecoli.workflow.analysis"] = mod
    # A bare `import a.b.c` (unlike `from a.b.c import x`) requires every
    # parent package to resolve too, not just the leaf -- stub the whole
    # chain so the plural `import v2ecoli.workflow.analyses` build_analysis_
    # options now also does (see item 39 continued) doesn't try to load a
    # real (nonexistent, in this test env) v2ecoli package.
    sys.modules.setdefault("v2ecoli", types.ModuleType("v2ecoli"))
    sys.modules.setdefault("v2ecoli.workflow", types.ModuleType("v2ecoli.workflow"))
    sys.modules.setdefault(
        "v2ecoli.workflow.analyses", types.ModuleType("v2ecoli.workflow.analyses")
    )


def test_build_analysis_options_groups_by_scale(tmp_path):
    _inject_analysis_registry({"ptools_rna": "single", "ccm": "multiseed"})
    entries = [{"name": "ptools_rna", "params": {"n": 8}}, {"name": "ccm"}]
    opts, errors = srp.build_analysis_options(entries, tmp_path)
    assert errors == []
    assert opts["single"]["ptools_rna"] == {"n": 8}
    assert opts["multiseed"]["ccm"] == {}


def test_build_analysis_options_unknown_name_records_error(tmp_path):
    _inject_analysis_registry({"ptools_rna": "single"})
    opts, errors = srp.build_analysis_options(
        [{"name": "ptools_rna"}, {"name": "missing"}], tmp_path)
    assert len(errors) == 1
    assert errors[0]["analysis"] == "missing"
    assert "single" in opts and "ptools_rna" in opts["single"]


def test_build_analysis_options_empty(tmp_path):
    _inject_analysis_registry({})
    assert srp.build_analysis_options([], tmp_path) == ({}, [])


def test_build_analysis_options_adds_ws_root_to_sys_path(tmp_path):
    """ws_root must land on sys.path before the ANALYSIS_REGISTRY import, so a
    live served workspace's own v2ecoli shadows any build-time-baked one
    (backlog item 39)."""
    _inject_analysis_registry({})
    assert str(tmp_path) not in sys.path
    try:
        srp.build_analysis_options([], tmp_path)
        assert str(tmp_path) in sys.path
    finally:
        if str(tmp_path) in sys.path:
            sys.path.remove(str(tmp_path))


def _write_real_v2ecoli_fixture(ws_root: Path) -> None:
    """Build a REAL, on-disk v2ecoli package (no mocking) mirroring the exact
    shape of the live bug found in backlog item 39: ANALYSIS_REGISTRY starts
    empty in the singular `analysis` module and is only populated by
    __init_subclass__ side effects when the sibling PLURAL `analyses` package
    gets imported. `workflow/__init__.py` deliberately does NOT import
    `analyses` -- matching the real v2ecoli package -- so this fixture would
    genuinely reproduce the bug if build_analysis_options's own explicit
    plural import were ever removed."""
    pkg = ws_root / "v2ecoli" / "workflow"
    pkg.mkdir(parents=True)
    (ws_root / "v2ecoli" / "__init__.py").write_text("")
    (pkg / "__init__.py").write_text("")
    (pkg / "analysis.py").write_text(
        "ANALYSIS_REGISTRY = {}\n"
        "\n"
        "class AnalysisStep:\n"
        "    scale = 'single'\n"
        "    def __init_subclass__(cls, **kwargs):\n"
        "        super().__init_subclass__(**kwargs)\n"
        "        if 'name' in cls.__dict__:\n"
        "            ANALYSIS_REGISTRY[cls.name] = cls\n"
    )
    analyses_pkg = pkg / "analyses"
    analyses_pkg.mkdir()
    (analyses_pkg / "__init__.py").write_text(
        "from v2ecoli.workflow.analyses import cd1_fake  # noqa: F401\n"
    )
    (analyses_pkg / "cd1_fake.py").write_text(
        "from v2ecoli.workflow.analysis import AnalysisStep\n"
        "\n"
        "class Cd1Fake(AnalysisStep):\n"
        "    name = 'cd1_fake'\n"
        "    scale = 'multiseed'\n"
    )


def test_build_analysis_options_real_import_chain_registers_plural_package(tmp_path):
    """Regression test for item 39 continued: a real (unmocked) v2ecoli
    fixture, exercised in a FRESH subprocess so no other test's sys.modules
    state can mask the bug via caching. Reproduces the exact live finding:
    `cd1_fake` (standing in for the real cd1_*/ptools_* suite) is defined
    ONLY in the plural `analyses` package, never imported by
    `workflow/__init__.py` or `workflow/analysis.py` themselves -- if
    build_analysis_options ever regresses to importing just the singular
    `analysis` module, this resolves as an unknown analysis on the very
    first call, exactly as it did on the real smscdk deployment."""
    _write_real_v2ecoli_fixture(tmp_path)
    script = (
        "import sys, json\n"
        "from vivarium_workbench.lib.study_run_post import build_analysis_options\n"
        f"opts, errors = build_analysis_options([{{'name': 'cd1_fake'}}], {str(tmp_path)!r})\n"
        "print(json.dumps({'opts': opts, 'errors': errors}))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"subprocess failed: {result.stderr}"
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["errors"] == [], (
        f"cd1_fake should resolve cleanly: {payload['errors']}"
    )
    assert payload["opts"]["multiseed"]["cd1_fake"] == {}


def _evict_v2ecoli_modules() -> None:
    for name in [m for m in list(sys.modules) if m == "v2ecoli" or m.startswith("v2ecoli.")]:
        del sys.modules[name]


def test_image_analysis_registry_resolves_against_boot_path(tmp_path, monkeypatch):
    """_image_analysis_registry must import v2ecoli from _BOOT_SYS_PATH (the
    image's own baked-in install), not whatever a workspace has since
    prepended to sys.path (backlog item 39 continued -- the PVC-staleness
    fallback)."""
    _write_real_v2ecoli_fixture(tmp_path)
    monkeypatch.setattr(srp, "_BOOT_SYS_PATH", [str(tmp_path), *sys.path])
    _evict_v2ecoli_modules()
    try:
        registry = srp._image_analysis_registry()
        assert "cd1_fake" in registry
    finally:
        _evict_v2ecoli_modules()


def test_build_analysis_options_falls_back_to_image_registry_when_workspace_stale(
    tmp_path, monkeypatch
):
    """A name missing from the (possibly stale, PVC-seeded) workspace
    registry must still resolve against the image's own boot-time registry
    instead of being recorded as an error (backlog item 39 continued)."""
    _inject_analysis_registry({"ptools_rna": "single"})  # the "stale workspace" registry
    image_root = tmp_path / "image_root"
    image_root.mkdir()
    _write_real_v2ecoli_fixture(image_root)
    monkeypatch.setattr(srp, "_BOOT_SYS_PATH", [str(image_root), *sys.path])
    ws_root = tmp_path / "workspace"
    ws_root.mkdir()
    try:
        opts, errors = srp.build_analysis_options(
            [{"name": "ptools_rna"}, {"name": "cd1_fake"}], ws_root
        )
        assert errors == [], f"expected both names to resolve: {errors}"
        assert opts["single"]["ptools_rna"] == {}
        assert opts["multiseed"]["cd1_fake"] == {}
    finally:
        _evict_v2ecoli_modules()


def test_build_analysis_options_still_errors_when_truly_unknown_everywhere(
    tmp_path, monkeypatch
):
    """A name absent from BOTH the workspace and the image registry is a
    real error, not silently swallowed by the fallback."""
    _inject_analysis_registry({"ptools_rna": "single"})
    image_root = tmp_path / "image_root"
    image_root.mkdir()
    _write_real_v2ecoli_fixture(image_root)
    monkeypatch.setattr(srp, "_BOOT_SYS_PATH", [str(image_root), *sys.path])
    ws_root = tmp_path / "workspace"
    ws_root.mkdir()
    try:
        opts, errors = srp.build_analysis_options([{"name": "totally_unknown"}], ws_root)
        assert len(errors) == 1
        assert errors[0]["analysis"] == "totally_unknown"
    finally:
        _evict_v2ecoli_modules()


# ---------------------------------------------------------------------------
# run_post_run_scripts
# ---------------------------------------------------------------------------

def test_run_post_run_scripts_no_entries_is_noop(tmp_path):
    assert srp.run_post_run_scripts({}, tmp_path) == ([], [])
    assert srp.run_post_run_scripts({"post_run_scripts": []}, tmp_path) == ([], [])


def test_run_post_run_scripts_invokes_and_collects(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    (ws / "scripts").mkdir(parents=True)
    script = ws / "scripts" / "render.py"
    script.write_text("print('hi')", encoding="utf-8")
    viz_dir = ws / "studies" / "s1" / "viz"
    viz_dir.mkdir(parents=True)

    calls = []

    def fake_run(cmd, **kw):
        calls.append((cmd, kw))
        # the script "writes" an HTML file as a side effect
        (viz_dir / "rendered.html").write_text("<html>", encoding="utf-8")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    spec = {"post_run_scripts": [
        {"path": "scripts/render.py", "args": ["--study", "s1"], "timeout_s": 42},
    ]}
    written, errors = srp.run_post_run_scripts(spec, ws)

    assert errors == []
    assert len(calls) == 1
    cmd, kw = calls[0]
    assert cmd == [sys.executable, str(script), "--study", "s1"]
    assert kw["cwd"] == str(ws)
    assert kw["timeout"] == 42
    assert "studies/s1/viz/rendered.html" in written


def test_run_post_run_scripts_missing_script_records_error(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    spec = {"post_run_scripts": [{"path": "scripts/absent.py"}]}
    written, errors = srp.run_post_run_scripts(spec, ws)
    assert written == []
    assert errors == [{"script": "scripts/absent.py", "error": "script not found"}]


def test_run_post_run_scripts_nonzero_returncode_records_error(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    (ws / "scripts").mkdir(parents=True)
    (ws / "scripts" / "boom.py").write_text("x", encoding="utf-8")

    def fake_run(cmd, **kw):
        return types.SimpleNamespace(returncode=3, stdout="out", stderr="bad happened")

    monkeypatch.setattr(subprocess, "run", fake_run)
    written, errors = srp.run_post_run_scripts(
        {"post_run_scripts": [{"path": "scripts/boom.py"}]}, ws)
    assert written == []
    assert len(errors) == 1
    assert errors[0]["returncode"] == 3
    assert "bad happened" in errors[0]["stderr_tail"]


# ---------------------------------------------------------------------------
# run_study_analyses
# ---------------------------------------------------------------------------

def test_run_study_analyses_no_entries_is_noop(tmp_path):
    assert srp.run_study_analyses(tmp_path, {}, "r1", tmp_path) == ([], [])


def _mock_pool(monkeypatch, result):
    """Make the env-worker pool's ``call`` return ``result`` (or raise it)."""
    from vivarium_workbench.lib import env_worker_pool

    class _Pool:
        def call(self, ws, method, params):
            assert method == "run_study_analyses"
            if isinstance(result, Exception):
                raise result
            return result

    monkeypatch.setattr(env_worker_pool, "get_pool", lambda: _Pool())


def test_run_study_analyses_passes_worker_errors_through(tmp_path, monkeypatch):
    """The v2ecoli scale-lookup/build-options now happens in the worker; the
    workbench passes its errors straight through."""
    import vivarium_workbench.lib.study_charts as sc
    study_dir = tmp_path / "study"
    (study_dir / "exp" / "history").mkdir(parents=True)
    monkeypatch.setattr(sc, "_latest_parquet_for_study", lambda sd: study_dir / "exp" / "history")
    _mock_pool(monkeypatch, {"written": [], "errors": [{"error": "v2ecoli not installed"}]})
    written, errors = srp.run_study_analyses(
        study_dir, {"analyses": [{"name": "x"}]}, "r1", tmp_path)
    assert written == []
    assert errors == [{"error": "v2ecoli not installed"}]


def test_run_study_analyses_soft_degrades_when_worker_unavailable(tmp_path, monkeypatch):
    import vivarium_workbench.lib.study_charts as sc
    from vivarium_workbench.lib.env_worker_client import EnvWorkerUnavailable
    study_dir = tmp_path / "study"
    (study_dir / "exp" / "history").mkdir(parents=True)
    monkeypatch.setattr(sc, "_latest_parquet_for_study", lambda sd: study_dir / "exp" / "history")
    _mock_pool(monkeypatch, EnvWorkerUnavailable("down"))
    written, errors = srp.run_study_analyses(
        study_dir, {"analyses": [{"name": "x"}]}, "r1", tmp_path)
    assert written == []
    assert "environment worker unavailable" in errors[0]["error"]


def test_run_study_analyses_passes_worker_written_and_errors(tmp_path, monkeypatch):
    """The workbench resolves the sweep dir and passes the worker's written files
    + per-group errors straight through (the run happens worker-side)."""
    import vivarium_workbench.lib.study_charts as sc
    study_dir = tmp_path / "study"
    (study_dir / "exp" / "history").mkdir(parents=True)
    monkeypatch.setattr(sc, "_latest_parquet_for_study", lambda sd: study_dir / "exp" / "history")
    _mock_pool(monkeypatch, {
        "written": ["/x/ptools/rna.tsv", "/x/viz/plot.html", "/x/analysis.json"],
        "errors": [{"analysis": "ptools_rna", "group": "all", "error": "boom"}]})
    written, errors = srp.run_study_analyses(
        study_dir, {"analyses": [{"name": "ptools_rna"}]}, "r1", tmp_path)
    names = {Path(p).name for p in written}
    assert {"rna.tsv", "plot.html", "analysis.json"} <= names
    assert any(e.get("analysis") == "ptools_rna" and e.get("error") == "boom"
               for e in errors)


# ---------------------------------------------------------------------------
# render_study_visualizations
# ---------------------------------------------------------------------------

def _write_workspace_yaml(ws: Path, *, package_path="fakepkg", visualizations=None):
    ws.mkdir(parents=True, exist_ok=True)
    data = {"name": "fake-ws", "package_path": package_path,
            "visualizations": visualizations or []}
    (ws / "workspace.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


def _install_fake_core(monkeypatch, pkg="fakepkg"):
    """Inject a fake <pkg>.core with build_core() -> a minimal core object."""
    class _Core:
        def __init__(self):
            self.link_registry = {}

        def register_link(self, name, cls):
            self.link_registry[name] = cls

    core_mod = types.ModuleType(f"{pkg}.core")
    core_mod.build_core = lambda: _Core()  # type: ignore[attr-defined]
    pkg_mod = types.ModuleType(pkg)
    pkg_mod.core = core_mod  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, pkg, pkg_mod)
    monkeypatch.setitem(sys.modules, f"{pkg}.core", core_mod)


def _quiet_discover(monkeypatch):
    import process_bigraph.composite_generator as cg
    monkeypatch.setattr(cg, "discover_generators", lambda: None)
    # keep registry truthy + ensure the test spec_id is absent (entry None).
    from conftest import register_generator
    register_generator("_dummy_keep_truthy",
                       types.SimpleNamespace(name="_dummy", visualizations=[]))


def test_render_study_visualizations_no_viz_is_noop(tmp_path, monkeypatch):
    _quiet_discover(monkeypatch)
    ws = tmp_path / "ws"
    ws.mkdir()
    study_dir = tmp_path / "study"
    study_dir.mkdir()
    out = srp.render_study_visualizations(ws, study_dir, {"name": "s"}, "spec.absent")
    assert out == ([], [])


def test_render_study_visualizations_happy_writes_and_passes_effective_spec(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    _write_workspace_yaml(ws)
    study_dir = tmp_path / "study"
    (study_dir / "viz").mkdir(parents=True)

    # The default-viz discovery + the core/render hooks now come from the env
    # worker — stub both seams (no worker spawned, no in-process build_core).
    import vivarium_workbench.lib.composite_lookup as cl
    import vivarium_workbench.lib.viz_render as vr
    monkeypatch.setattr(cl, "_discover_generators_via_worker", lambda ws_root: {})
    monkeypatch.setattr(vr, "viz_render_hooks",
                        lambda ws_root: ({}, lambda doc, _r=None: "<html>"))

    captured = {}

    def fake_render_visualizations(effective_spec, sd, name, inputs_by_class=None,
                                   core_registry=None, build_and_run=None):
        captured["spec"] = effective_spec
        captured["name"] = name
        p = sd / "viz" / "v1.html"
        p.write_text("<html>", encoding="utf-8")
        return [str(p)]

    import vivarium_workbench.lib.investigations as inv
    monkeypatch.setattr(inv, "render_visualizations", fake_render_visualizations)

    spec = {"name": "study-x",
            "visualizations": [{"name": "v1", "address": "local:Foo"}]}
    written, errors = srp.render_study_visualizations(ws, study_dir, spec, "spec.id")

    assert errors == []
    assert written == ["viz/v1.html"]
    assert (study_dir / "viz" / "v1.html").exists()
    assert captured["name"] == "study-x"


def test_render_study_visualizations_skips_addressless_prerendered_viz(tmp_path, monkeypatch):
    """REGRESSION: a viz with no resolvable address (pre-rendered externally — it
    declares a `rendered` file but no live render class, and workspace.yaml has no
    matching class) must be EXCLUDED, not sent to render_visualizations — which would
    KeyError on the missing 'address' and overwrite its good html with an error stub."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: w\nvisualizations: []\n", encoding="utf-8")
    study_dir = tmp_path / "study"
    (study_dir / "viz").mkdir(parents=True)
    # the externally pre-rendered figure the flush must not clobber
    prer = study_dir / "viz" / "eigen.html"
    prer.write_text("<svg>real figure</svg>", encoding="utf-8")

    import vivarium_workbench.lib.composite_lookup as cl
    import vivarium_workbench.lib.viz_render as vr
    monkeypatch.setattr(cl, "_discover_generators_via_worker", lambda ws_root: {})
    monkeypatch.setattr(vr, "viz_render_hooks", lambda ws_root: ({}, lambda doc, _r=None: "<html>"))

    captured = {}

    def fake_render_visualizations(effective_spec, sd, name, inputs_by_class=None,
                                   core_registry=None, build_and_run=None):
        captured["viz"] = [v.get("name") for v in effective_spec.get("visualizations", [])]
        return []

    import vivarium_workbench.lib.investigations as inv
    monkeypatch.setattr(inv, "render_visualizations", fake_render_visualizations)

    spec = {"name": "s", "visualizations": [
        {"name": "addressed", "address": "local:Foo"},
        {"name": "eigen", "rendered": "workspace/.../eigen.html"},  # no address
    ]}
    srp.render_study_visualizations(ws, study_dir, spec, "spec.id")

    # the addressless viz was excluded (not passed to the renderer) ...
    assert captured.get("viz") == ["addressed"]
    # ... and its pre-rendered figure was left intact (not clobbered with a stub).
    assert prer.read_text(encoding="utf-8") == "<svg>real figure</svg>"


def test_run_post_run_scripts_detects_write_with_stale_mtime(tmp_path, monkeypatch):
    """REGRESSION: detection must not compare st_mtime against a wall clock.

    The old implementation captured `t_start = time.time()` and reported files
    whose `st_mtime >= t_start`. Those are two different clocks: when the
    filesystem's mtime granularity is coarser than time.time() (common on CI
    filesystems, which truncate to the second), a file written AFTER t_start
    carries an mtime BELOW it and was silently dropped — so post-run viz never
    surfaced. It passed serially on CI and failed under parallel scheduling,
    i.e. it was luck either way.

    Here the "script" writes its HTML and then backdates the mtime, which is the
    coarse-granularity case in its most explicit form.
    """
    ws = tmp_path / "ws"
    (ws / "scripts").mkdir(parents=True)
    script = ws / "scripts" / "render.py"
    script.write_text("print('hi')", encoding="utf-8")
    viz_dir = ws / "studies" / "s1" / "viz"
    viz_dir.mkdir(parents=True)

    def fake_run(cmd, **kw):
        out = viz_dir / "rendered.html"
        out.write_text("<html>", encoding="utf-8")
        # Backdate well below any plausible t_start.
        os.utime(out, (1_000_000, 1_000_000))
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    written, errors = srp.run_post_run_scripts(
        {"post_run_scripts": [{"path": "scripts/render.py"}]}, ws)

    assert errors == []
    assert "studies/s1/viz/rendered.html" in written


def test_run_post_run_scripts_ignores_untouched_files(tmp_path, monkeypatch):
    """Pre-existing viz that this batch did not touch must NOT be reported."""
    ws = tmp_path / "ws"
    (ws / "scripts").mkdir(parents=True)
    (ws / "scripts" / "render.py").write_text("print('hi')", encoding="utf-8")
    viz_dir = ws / "studies" / "s1" / "viz"
    viz_dir.mkdir(parents=True)
    stale = viz_dir / "old.html"
    stale.write_text("<html>old", encoding="utf-8")

    def fake_run(cmd, **kw):
        (viz_dir / "new.html").write_text("<html>new", encoding="utf-8")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    written, errors = srp.run_post_run_scripts(
        {"post_run_scripts": [{"path": "scripts/render.py"}]}, ws)

    assert written == ["studies/s1/viz/new.html"]
    assert "studies/s1/viz/old.html" not in written
