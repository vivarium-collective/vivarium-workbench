"""Tests for ``lib.viz_preview_views.visualization_preview``.

Behaviour-preserving port of ``server.Handler._post_visualization_preview`` (the
in-process viz-preview render).  Every test uses a FAKE Visualization class (a
plain class with ``inputs()`` / ``update()`` / optional ``demo()`` + a ``config``
attr) and monkeypatches the three ``viz_core`` helpers
(``resolve_viz_class`` / ``demo_state_for`` / ``build_workspace_core``) so the
in-process render runs WITHOUT a real viz library and never builds a real core.

Covers: 400 (no address), 404 (class not registered), demo single-update happy,
the streaming 12-step synth path (all-scalar inputs + empty demo state), the
no-html ``<div>`` fallback, the demo-render EXCEPTION → 200 ``ok: False`` path,
the investigation-source render path, and the investigation no-runs.db → demo
fallback (with the note).  Only validation is non-200; a demo render that raises
still returns 200.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from vivarium_workbench.lib import viz_preview_views as views
from vivarium_workbench.lib import investigations
from vivarium_workbench.lib import study_spec


# ---------------------------------------------------------------------------
# FAKE Visualization classes (no real viz lib)
# ---------------------------------------------------------------------------

class FakeViz:
    """List-input (non-streaming) viz: a single ``update()`` renders html."""

    def __init__(self, config=None, core=None):
        self.config = config or {}

    def inputs(self):
        # list types → NOT all-scalar → non-streaming single-update path.
        return {"observable": "list[float]"}

    def update(self, state):
        return {"html": "<b>x</b>"}


class FakeNoHtmlViz(FakeViz):
    def update(self, state):
        return {}  # no html → triggers the <div> fallback


class FakeBoomViz(FakeViz):
    def update(self, state):
        raise ValueError("kaboom")


class FakeStreamingViz:
    """Scalar-input streaming viz: accumulates synth timesteps, renders at end."""

    def __init__(self, config=None, core=None):
        self.config = config or {}
        self.seen = []

    def inputs(self):
        # all-scalar → streaming path (when demo state is empty).
        return {"value": "float", "time": "float"}

    def update(self, state):
        self.seen.append(state)
        return {"html": f"<svg>{len(self.seen)}</svg>"}


def _make_ws(tmp_path: Path, *, name: str = "demo-ws") -> Path:
    (tmp_path / "workspace.yaml").write_text(f"name: {name}\n", encoding="utf-8")
    return tmp_path


def _patch_resolve(monkeypatch, cls, key):
    monkeypatch.setattr(views.viz_core, "resolve_viz_class",
                        lambda ws_root, address: (cls, key))


def _patch_demo(monkeypatch, state):
    monkeypatch.setattr(views.viz_core, "demo_state_for",
                        lambda cls, class_key: dict(state))


# ---------------------------------------------------------------------------
# 400 / 404 validation
# ---------------------------------------------------------------------------

def test_missing_address_400(tmp_path):
    ws = _make_ws(tmp_path)
    body, status = views.visualization_preview(ws, {})
    assert status == 400
    assert body == {"error": "address is required"}


def test_blank_address_400(tmp_path):
    ws = _make_ws(tmp_path)
    body, status = views.visualization_preview(ws, {"address": "   "})
    assert status == 400
    assert body == {"error": "address is required"}


def test_class_not_registered_404(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)
    monkeypatch.setattr(views.viz_core, "resolve_viz_class",
                        lambda ws_root, address: (None, None))
    body, status = views.visualization_preview(ws, {"address": "local:Nope"})
    assert status == 404
    assert body == {"error": "class not registered: local:Nope"}


# ---------------------------------------------------------------------------
# demo path: happy single-update / no-html fallback / exception
# ---------------------------------------------------------------------------

def test_demo_single_update_happy_200(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)
    _patch_resolve(monkeypatch, FakeViz, "FakeViz")
    _patch_demo(monkeypatch, {"observable": [1.0, 2.0]})
    body, status = views.visualization_preview(
        ws, {"address": "local:FakeViz"})
    assert status == 200
    assert body == {"ok": True, "html": "<b>x</b>",
                    "source_used": "demo", "notes": ""}


def test_demo_no_html_fallback_div_200(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)
    _patch_resolve(monkeypatch, FakeNoHtmlViz, "FakeNoHtmlViz")
    _patch_demo(monkeypatch, {"observable": [1.0]})
    body, status = views.visualization_preview(
        ws, {"address": "local:FakeNoHtmlViz"})
    assert status == 200
    assert body["ok"] is True
    assert body["source_used"] == "demo"
    assert "no demo state available" in body["html"]
    assert "<strong>FakeNoHtmlViz</strong>" in body["html"]


def test_demo_render_exception_returns_200_ok_false(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)
    _patch_resolve(monkeypatch, FakeBoomViz, "FakeBoomViz")
    _patch_demo(monkeypatch, {"observable": [1.0]})
    body, status = views.visualization_preview(
        ws, {"address": "local:FakeBoomViz"})
    assert status == 200  # NOT 500 — render failure still 200
    assert body["ok"] is False
    assert body["source_used"] == "demo"
    assert "demo render failed: ValueError: kaboom" in body["html"]


# ---------------------------------------------------------------------------
# demo streaming path: all-scalar inputs + empty demo → 12-step synth
# ---------------------------------------------------------------------------

def test_demo_streaming_synth_12_steps_200(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)
    _patch_resolve(monkeypatch, FakeStreamingViz, "FakeStreamingViz")
    _patch_demo(monkeypatch, {})  # empty → streaming detection trips
    # streaming path builds a workspace core; return a fake (None forces the
    # allocate_core branch, but a fake keeps it hermetic).
    monkeypatch.setattr(views.viz_core, "build_workspace_core",
                        lambda ws_root: (object(), {}))
    body, status = views.visualization_preview(
        ws, {"address": "local:FakeStreamingViz"})
    assert status == 200
    assert body["ok"] is True
    assert body["source_used"] == "demo"
    # 12 synth timesteps → final render reflects 12 accumulated updates.
    assert body["html"] == "<svg>12</svg>"


# ---------------------------------------------------------------------------
# investigation source path
# ---------------------------------------------------------------------------

def test_investigation_source_render_200(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)
    inv = "inv-x"
    inv_dir = study_spec.study_dir(ws, inv)
    inv_dir.mkdir(parents=True, exist_ok=True)
    (inv_dir / "runs.db").write_text("", encoding="utf-8")  # just needs to exist

    _patch_resolve(monkeypatch, FakeViz, "FakeViz")
    monkeypatch.setattr(investigations, "gather_emitter_outputs",
                        lambda db_path: {"gathered": True})
    monkeypatch.setattr(investigations, "build_viz_composite",
                        lambda viz_spec, gathered, registry: {
                            "inputs_store": {"observable": [1.0, 2.0]}})

    body, status = views.visualization_preview(
        ws, {"address": "local:FakeViz", "source": f"investigation:{inv}"})
    assert status == 200
    assert body["ok"] is True
    assert body["html"] == "<b>x</b>"
    assert body["source_used"] == f"investigation:{inv}"
    assert body["notes"] == ""


def test_investigation_no_runs_db_falls_back_to_demo(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)
    inv = "inv-x"
    # No runs.db created → fall back to demo with a note.
    _patch_resolve(monkeypatch, FakeViz, "FakeViz")
    _patch_demo(monkeypatch, {"observable": [1.0]})

    body, status = views.visualization_preview(
        ws, {"address": "local:FakeViz", "source": f"investigation:{inv}"})
    assert status == 200
    assert body["ok"] is True
    assert body["source_used"] == "demo"
    assert body["html"] == "<b>x</b>"
    assert "has no runs.db; falling back to demo" in body["notes"]


def test_investigation_empty_html_falls_back_to_demo(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)
    inv = "inv-x"
    inv_dir = study_spec.study_dir(ws, inv)
    inv_dir.mkdir(parents=True, exist_ok=True)
    (inv_dir / "runs.db").write_text("", encoding="utf-8")

    _patch_resolve(monkeypatch, FakeNoHtmlViz, "FakeNoHtmlViz")
    _patch_demo(monkeypatch, {"observable": [1.0]})
    monkeypatch.setattr(investigations, "gather_emitter_outputs",
                        lambda db_path: {})
    monkeypatch.setattr(investigations, "build_viz_composite",
                        lambda viz_spec, gathered, registry: {
                            "inputs_store": {"observable": [1.0]}})

    body, status = views.visualization_preview(
        ws, {"address": "local:FakeNoHtmlViz", "source": f"investigation:{inv}"})
    assert status == 200
    # FakeNoHtmlViz.update() returns {} → empty html → demo fallback note.
    assert body["source_used"] == "demo"
    assert "empty html; falling back to demo" in body["notes"]


def test_investigation_render_exception_falls_back_to_demo(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)
    inv = "inv-x"
    inv_dir = study_spec.study_dir(ws, inv)
    inv_dir.mkdir(parents=True, exist_ok=True)
    (inv_dir / "runs.db").write_text("", encoding="utf-8")

    _patch_resolve(monkeypatch, FakeViz, "FakeViz")
    _patch_demo(monkeypatch, {"observable": [1.0]})

    def _boom(db_path):
        raise RuntimeError("gather failed")

    monkeypatch.setattr(investigations, "gather_emitter_outputs", _boom)

    body, status = views.visualization_preview(
        ws, {"address": "local:FakeViz", "source": f"investigation:{inv}"})
    assert status == 200
    assert body["source_used"] == "demo"
    assert body["html"] == "<b>x</b>"
    assert "investigation render failed (RuntimeError: gather failed)" in body["notes"]
