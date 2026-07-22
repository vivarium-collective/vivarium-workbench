"""Tests for ``env_worker._viz_preview`` — the class-touching viz-preview render
that used to run in the HTTP process (``viz_preview_views`` + ``viz_core``) and now
runs in the workspace's env worker.

Each test monkeypatches ``env_worker._build_viz_core`` to return a fake
``(core, registry)`` of plain Python Visualization stand-ins (``inputs()`` /
``update()`` / optional ``demo()`` + a ``config`` attr), so the render logic is
exercised without a real viz library or core. Covers the ported contract:

  * unregistered class → ``{"status": "not_registered"}`` (the workbench maps 404);
  * demo single-update happy path;
  * the streaming 12-step synth path (all-scalar inputs + empty demo state);
  * the no-html ``<div>`` fallback;
  * a demo render that RAISES → ``ok: False`` (still a render body, not an error);
  * investigation-source render from a supplied ``inputs_store``, plus the
    empty-html and render-exception fallbacks to demo (with the note).
"""
from __future__ import annotations

from vivarium_workbench import env_worker


class FakeViz:
    """List-input (non-streaming) viz: a single ``update()`` renders html."""

    def __init__(self, config=None, core=None):
        self.config = config or {}

    def inputs(self):
        return {"observable": "list[float]"}  # list type → non-streaming

    def update(self, state):
        return {"html": "<b>x</b>"}


class FakeNoHtmlViz(FakeViz):
    def update(self, state):
        return {}  # no html → <div> fallback


class FakeBoomViz(FakeViz):
    def update(self, state):
        raise ValueError("kaboom")


class FakeStreamingViz:
    """Scalar-input streaming viz: accumulates synth timesteps, renders at end."""

    def __init__(self, config=None, core=None):
        self.config = config or {}
        self.seen = []

    def inputs(self):
        return {"value": "float", "time": "float"}  # all-scalar → streaming

    def update(self, state):
        self.seen.append(state)
        return {"html": f"<svg>{len(self.seen)}</svg>"}


class FakeInvBoomViz(FakeViz):
    """Raises only when fed the investigation store; demo state renders fine."""

    def update(self, state):
        if state.get("observable") == "BOOM":
            raise RuntimeError("inv failed")
        return {"html": "<b>demo</b>"}


def _patch_core(monkeypatch, registry):
    monkeypatch.setattr(env_worker, "_build_viz_core",
                        lambda: (object(), dict(registry)))


# ---------------------------------------------------------------------------
# resolution
# ---------------------------------------------------------------------------

def test_not_registered(monkeypatch):
    _patch_core(monkeypatch, {})
    out = env_worker._viz_preview({"address": "local:Nope"})
    assert out == {"status": "not_registered"}


def test_resolves_short_name_from_fully_qualified_address(monkeypatch):
    _patch_core(monkeypatch, {"FakeViz": FakeViz})
    out = env_worker._viz_preview(
        {"address": "local:pkg.visualizations.mod.FakeViz"})
    assert out["ok"] is True
    assert out["html"] == "<b>x</b>"


# ---------------------------------------------------------------------------
# demo path
# ---------------------------------------------------------------------------

def test_demo_single_update_happy(monkeypatch):
    _patch_core(monkeypatch, {"FakeViz": FakeViz})
    out = env_worker._viz_preview({"address": "local:FakeViz"})
    assert out == {"ok": True, "html": "<b>x</b>",
                   "source_used": "demo", "notes": ""}


def test_demo_no_html_fallback_div(monkeypatch):
    _patch_core(monkeypatch, {"FakeNoHtmlViz": FakeNoHtmlViz})
    out = env_worker._viz_preview({"address": "local:FakeNoHtmlViz"})
    assert out["ok"] is True
    assert out["source_used"] == "demo"
    assert "no demo state available" in out["html"]
    assert "<strong>FakeNoHtmlViz</strong>" in out["html"]


def test_demo_render_exception_ok_false(monkeypatch):
    _patch_core(monkeypatch, {"FakeBoomViz": FakeBoomViz})
    out = env_worker._viz_preview({"address": "local:FakeBoomViz"})
    assert out["ok"] is False  # NOT an error — a render body
    assert out["source_used"] == "demo"
    assert "demo render failed: ValueError: kaboom" in out["html"]


def test_demo_streaming_synth_12_steps(monkeypatch):
    _patch_core(monkeypatch, {"FakeStreamingViz": FakeStreamingViz})
    out = env_worker._viz_preview({"address": "local:FakeStreamingViz"})
    assert out["ok"] is True
    assert out["source_used"] == "demo"
    # 12 synth timesteps → final render reflects 12 accumulated updates.
    assert out["html"] == "<svg>12</svg>"


def test_demo_uses_class_demo_classmethod(monkeypatch):
    class WithDemo(FakeViz):
        def inputs(self):
            return {"value": "float", "time": "float"}  # all-scalar

        @classmethod
        def demo(cls):
            return {"value": [1.0, 2.0]}  # non-empty → skips the streaming branch

        def update(self, state):
            return {"html": f"<d>{state.get('value')}</d>"}

    _patch_core(monkeypatch, {"WithDemo": WithDemo})
    out = env_worker._viz_preview({"address": "local:WithDemo"})
    # demo() state is non-empty, so despite all-scalar inputs the render is a
    # single update() fed that state (NOT the 12-step synth accumulator).
    assert out["ok"] is True
    assert out["html"] == "<d>[1.0, 2.0]</d>"


# ---------------------------------------------------------------------------
# investigation path (inputs_store supplied HTTP-side)
# ---------------------------------------------------------------------------

def test_investigation_render(monkeypatch):
    _patch_core(monkeypatch, {"FakeViz": FakeViz})
    out = env_worker._viz_preview({
        "address": "local:FakeViz",
        "source": "investigation:inv-x",
        "investigation_inputs_store": {"observable": [1.0, 2.0]},
    })
    assert out == {"ok": True, "html": "<b>x</b>",
                   "source_used": "investigation:inv-x", "notes": ""}


def test_investigation_empty_html_falls_back_to_demo(monkeypatch):
    _patch_core(monkeypatch, {"FakeNoHtmlViz": FakeNoHtmlViz})
    out = env_worker._viz_preview({
        "address": "local:FakeNoHtmlViz",
        "source": "investigation:inv-x",
        "investigation_inputs_store": {"observable": [1.0]},
    })
    assert out["source_used"] == "demo"
    assert "investigation render produced empty html; falling back to demo" in out["notes"]


def test_investigation_render_exception_falls_back_to_demo(monkeypatch):
    _patch_core(monkeypatch, {"FakeInvBoomViz": FakeInvBoomViz})
    out = env_worker._viz_preview({
        "address": "local:FakeInvBoomViz",
        "source": "investigation:inv-x",
        "investigation_inputs_store": {"observable": "BOOM"},
    })
    assert out["source_used"] == "demo"
    assert out["html"] == "<b>demo</b>"  # demo state renders fine
    assert "investigation render failed (RuntimeError: inv failed)" in out["notes"]


def test_note_prefix_preserved(monkeypatch):
    _patch_core(monkeypatch, {"FakeViz": FakeViz})
    out = env_worker._viz_preview({
        "address": "local:FakeViz",
        "note_prefix": ["seed note"],
    })
    assert out["notes"] == "seed note"
