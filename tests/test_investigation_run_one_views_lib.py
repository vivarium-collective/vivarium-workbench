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
import sqlite3
import types
from pathlib import Path

import pytest

from vivarium_workbench.lib import composite_runs as cr
from vivarium_workbench.lib import composite_lookup
from vivarium_workbench.lib import investigations
from vivarium_workbench.lib import study_spec
from vivarium_workbench.lib import investigation_run_one_views as views

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
