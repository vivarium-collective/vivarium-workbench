"""Tests for ``lib.investigation_run_views.investigation_run``.

Behaviour-preserving port of ``server.Handler._post_investigation_run`` (run ALL
of an investigation's simulations + render its visualizations) with the
``_active_branch_action`` commit DEFERRED — the builder returns the summary
directly.

Hermetic: NO real core build, NO real composite subprocess.
``core_builder.build_viz_registry`` is monkeypatched to a fake ``(core,
registry)`` and ``investigations.run_investigation`` is monkeypatched to a canned
summary / raiser; the ``run_one_composite`` script-building closure is exercised
with ``investigation_run_views.subprocess.run`` monkeypatched to capture the
embedded ``python -c`` script (never spawning a child).
"""
from __future__ import annotations

import types
from pathlib import Path

import pytest

from vivarium_dashboard.lib import composite_lookup
from vivarium_dashboard.lib import core_builder
from vivarium_dashboard.lib import investigations
from vivarium_dashboard.lib import investigation_run_views as views


def _make_ws(tmp_path: Path, *, name: str = "demo-ws") -> Path:
    (tmp_path / "workspace.yaml").write_text(f"name: {name}\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def fake_registry(monkeypatch):
    """Stub out the in-process core build → ``(core, registry)``."""
    core = types.SimpleNamespace(link_registry={"X": object})
    registry = {"X": object, "TimeSeriesPlot": object}

    def _fake_build(ws_root, pkg):
        return core, registry

    monkeypatch.setattr(core_builder, "build_viz_registry", _fake_build)
    return core, registry


# ---------------------------------------------------------------------------
# validation + core-build + outcome mapping
# ---------------------------------------------------------------------------

def test_missing_name_400(tmp_path):
    ws = _make_ws(tmp_path)
    body, status = views.investigation_run(ws, {})
    assert status == 400
    assert body == {"error": "name is required"}


def test_core_build_failure_500(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)

    def _boom(ws_root, pkg):
        raise RuntimeError("no core")

    monkeypatch.setattr(core_builder, "build_viz_registry", _boom)
    body, status = views.investigation_run(ws, {"name": "inv-x"})
    assert status == 500
    assert body == {"error": "failed to build core: no core"}


def test_happy_200_passes_callables_and_registry(tmp_path, monkeypatch, fake_registry):
    ws = _make_ws(tmp_path)
    _core, registry = fake_registry
    captured = {}

    def _fake_run_investigation(ws_root, name, *, run_one_composite,
                                core_registry, build_and_run):
        captured["ws_root"] = ws_root
        captured["name"] = name
        captured["run_one_composite"] = run_one_composite
        captured["core_registry"] = core_registry
        captured["build_and_run"] = build_and_run
        return {"ran": 3, "rendered": 2}

    monkeypatch.setattr(investigations, "run_investigation", _fake_run_investigation)
    body, status = views.investigation_run(ws, {"name": "inv-x"})
    assert status == 200
    assert body == {"ran": 3, "rendered": 2}
    assert captured["name"] == "inv-x"
    assert captured["ws_root"] == ws
    assert callable(captured["run_one_composite"])
    assert callable(captured["build_and_run"])
    assert captured["core_registry"] is registry


def test_spec_error_400(tmp_path, monkeypatch, fake_registry):
    ws = _make_ws(tmp_path)

    def _raise(*a, **k):
        raise investigations.InvestigationSpecError("bad shape")

    monkeypatch.setattr(investigations, "run_investigation", _raise)
    body, status = views.investigation_run(ws, {"name": "inv-x"})
    assert status == 400
    assert body == {"error": "spec error: bad shape"}


def test_file_not_found_404(tmp_path, monkeypatch, fake_registry):
    ws = _make_ws(tmp_path)

    def _raise(*a, **k):
        raise FileNotFoundError("composites/missing.yaml")

    monkeypatch.setattr(investigations, "run_investigation", _raise)
    body, status = views.investigation_run(ws, {"name": "inv-x"})
    assert status == 404
    assert body == {"error": "composites/missing.yaml"}


def test_returned_error_summary_maps_to_404(tmp_path, monkeypatch, fake_registry):
    # run_investigation can RETURN (not raise) an error summary — e.g. the
    # concurrent run-lock guard. The original handler routes any "error"-keyed
    # summary through the 400/404 dispatch (here: 404, not a 200 with the raw
    # summary).
    ws = _make_ws(tmp_path)

    def _already_running(*a, **k):
        return {"name": "inv-x", "error": "investigation is already running",
                "status": "running"}

    monkeypatch.setattr(investigations, "run_investigation", _already_running)
    body, status = views.investigation_run(ws, {"name": "inv-x"})
    assert status == 404
    assert body == {"error": "investigation is already running"}


def test_name_resolved_from_study_and_investigation_keys(tmp_path, monkeypatch, fake_registry):
    ws = _make_ws(tmp_path)
    seen = {}
    monkeypatch.setattr(
        investigations, "run_investigation",
        lambda ws_root, name, **k: seen.setdefault("name", name) or {"ok": True})
    # 'study' key resolves when 'name' absent.
    views.investigation_run(ws, {"study": "via-study"})
    assert seen["name"] == "via-study"


# ---------------------------------------------------------------------------
# run_one_composite closure: builds the right embedded subprocess script
# ---------------------------------------------------------------------------

def _capture_run_one_composite(ws, monkeypatch, fake_registry):
    """Drive ``investigation_run`` so ``run_investigation`` captures the closure."""
    holder = {}

    def _fake_run_investigation(ws_root, name, *, run_one_composite,
                                core_registry, build_and_run):
        holder["fn"] = run_one_composite
        return {"ok": True}

    monkeypatch.setattr(investigations, "run_investigation", _fake_run_investigation)
    body, status = views.investigation_run(ws, {"name": "inv-x"})
    assert status == 200
    return holder["fn"]


def test_run_one_composite_legacy_script(tmp_path, monkeypatch, fake_registry):
    ws = _make_ws(tmp_path)
    # Legacy path resolves the composite from the registry by spec_id.
    comp = tmp_path / "demo.yaml"
    comp.write_text("state:\n  rate: 1\nparameters: {}\n", encoding="utf-8")
    monkeypatch.setattr(composite_lookup, "find_composite_path",
                        lambda ws_root, pkg, name: comp)

    captured = {}

    def _fake_subprocess_run(cmd, *a, **k):
        captured["cmd"] = cmd
        captured["cwd"] = k.get("cwd")
        return types.SimpleNamespace(stdout="@@@OK@@@\n", stderr="", returncode=0)

    monkeypatch.setattr(views.subprocess, "run", _fake_subprocess_run)

    run_one = _capture_run_one_composite(ws, monkeypatch, fake_registry)
    result = run_one(spec_id="demo", overrides={}, steps=5, sim_name="s",
                     run_id="rid", db_file="/tmp/x.db", state_doc=None)
    assert result == {"status": "completed"}
    script = captured["cmd"][2]  # [py, "-c", script]
    assert "from pbg_demo_ws.core import build_core" in script
    assert "composite.run(5)" in script
    assert "@@@OK@@@" in script
    assert captured["cwd"] == ws


def test_run_one_composite_legacy_missing_composite(tmp_path, monkeypatch, fake_registry):
    ws = _make_ws(tmp_path)
    monkeypatch.setattr(composite_lookup, "find_composite_path",
                        lambda ws_root, pkg, name: None)
    # subprocess must NOT be reached.
    monkeypatch.setattr(views.subprocess, "run",
                        lambda *a, **k: pytest.fail("subprocess should not run"))
    run_one = _capture_run_one_composite(ws, monkeypatch, fake_registry)
    result = run_one(spec_id="ghost", overrides={}, steps=1, sim_name="s",
                     run_id="r", db_file="db", state_doc=None)
    assert result == {"status": "failed", "error": "composite not found: ghost"}


def test_run_one_composite_multi_composite_script(tmp_path, monkeypatch, fake_registry):
    ws = _make_ws(tmp_path)
    captured = {}

    def _fake_subprocess_run(cmd, *a, **k):
        captured["cmd"] = cmd
        return types.SimpleNamespace(stdout="@@@OK@@@\n", stderr="", returncode=0)

    monkeypatch.setattr(views.subprocess, "run", _fake_subprocess_run)

    run_one = _capture_run_one_composite(ws, monkeypatch, fake_registry)
    state_doc = {"state": {"emitter": {"_type": "step", "config": {"keep": 1}},
                           "foo": 2}}
    result = run_one(spec_id="x", overrides={}, steps=3, sim_name="s",
                     run_id="RID-42", db_file="/db/runs.db", state_doc=state_doc)
    assert result == {"status": "completed"}
    script = captured["cmd"][2]
    # Emitter wiring landed in the embedded state JSON.
    assert "composite.run(3)" in script
    assert "RID-42" in script
    assert "local:SQLiteEmitter" in script
    # The input state_doc is deep-copied — caller's dict is untouched.
    assert state_doc["state"]["emitter"]["config"] == {"keep": 1}


def test_run_one_composite_error_marker_returns_failed(tmp_path, monkeypatch, fake_registry):
    ws = _make_ws(tmp_path)
    monkeypatch.setattr(
        views.subprocess, "run",
        lambda *a, **k: types.SimpleNamespace(
            stdout="@@@ERROR@@@\nTraceback…\nValueError: boom\n",
            stderr="", returncode=0))
    run_one = _capture_run_one_composite(ws, monkeypatch, fake_registry)
    result = run_one(spec_id="x", overrides={}, steps=1, sim_name="s",
                     run_id="r", db_file="db",
                     state_doc={"state": {"foo": 1}})
    assert result["status"] == "failed"
    assert result["error"].endswith("ValueError: boom")
