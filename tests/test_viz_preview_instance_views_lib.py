"""Tests for ``lib.viz_preview_instance_views.visualization_preview_instance``.

Behaviour-preserving port of
``server.Handler._post_visualization_preview_instance`` — preview a
``workspace.yaml``-registered visualization instance BY NAME.  All tests are
hermetic: they write a tmp ``workspace.yaml`` and (for the description-only
status_block variants) create/omit ``.pbg/viz-requests/<name>.md`` /
``.pbg/viz-responses/<name>.py``.  The has-class delegation monkeypatches
``viz_preview_instance_views.viz_preview_views.visualization_preview`` with a
canned ``(dict, 200)`` so no real viz library is touched.

Covers: 400 (no name), 404 (not registered), the description-only stub for each
of the 3 status_block variants (neither file → "description-only"; request .md →
"Request pending"; response .py → "Code generated"), and the has-class
delegation (asserting the ``{address, config, source}`` passed through).
"""
from __future__ import annotations

from pathlib import Path

import yaml

from vivarium_dashboard.lib import viz_preview_instance_views as views


def _make_ws(tmp_path: Path, visualizations=None, *, name: str = "demo-ws") -> Path:
    data: dict = {"name": name}
    if visualizations is not None:
        data["visualizations"] = visualizations
    (tmp_path / "workspace.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# 400 / 404 validation
# ---------------------------------------------------------------------------

def test_missing_name_400(tmp_path):
    ws = _make_ws(tmp_path, [])
    body, status = views.visualization_preview_instance(ws, {})
    assert status == 400
    assert body == {"error": "name is required"}


def test_blank_name_400(tmp_path):
    ws = _make_ws(tmp_path, [])
    body, status = views.visualization_preview_instance(ws, {"name": "   "})
    assert status == 400
    assert body == {"error": "name is required"}


def test_not_registered_404(tmp_path):
    ws = _make_ws(tmp_path, [{"name": "other", "class": "Foo"}])
    body, status = views.visualization_preview_instance(ws, {"name": "missing"})
    assert status == 404
    assert body == {"error": "visualization 'missing' not registered"}


def test_not_registered_404_no_visualizations_key(tmp_path):
    # workspace.yaml without a `visualizations` list at all → still 404.
    ws = _make_ws(tmp_path)
    body, status = views.visualization_preview_instance(ws, {"name": "x"})
    assert status == 404
    assert body == {"error": "visualization 'x' not registered"}


# ---------------------------------------------------------------------------
# description-only stub — the 3 status_block variants
# ---------------------------------------------------------------------------

def test_stub_description_only_neither_file(tmp_path):
    ws = _make_ws(tmp_path, [{"name": "viz1", "description": "My <chart>"}])
    body, status = views.visualization_preview_instance(ws, {"name": "viz1"})
    assert status == 200
    assert body["ok"] is True
    assert body["source_used"] == "stub"
    assert body["notes"] == "description-only entry; nothing to render against demo data"
    # description-only status block (neither response nor request file exists)
    assert "<strong>description-only</strong>" in body["html"]
    assert "Click <strong>Create</strong> on this row" in body["html"]
    # the description is html-escaped into the stub
    assert "My &lt;chart&gt;" in body["html"]
    assert "<h3 style=\"margin:0 0 8px\">viz1</h3>" in body["html"]


def test_stub_no_description_default_text(tmp_path):
    ws = _make_ws(tmp_path, [{"name": "viz1"}])  # no class, no description
    body, status = views.visualization_preview_instance(ws, {"name": "viz1"})
    assert status == 200
    assert body["source_used"] == "stub"
    assert "<em>(no description)</em>" in body["html"]


def test_stub_request_pending(tmp_path):
    ws = _make_ws(tmp_path, [{"name": "viz1", "description": "d"}])
    req = ws / ".pbg" / "viz-requests"
    req.mkdir(parents=True, exist_ok=True)
    (req / "viz1.md").write_text("request", encoding="utf-8")
    body, status = views.visualization_preview_instance(ws, {"name": "viz1"})
    assert status == 200
    assert body["source_used"] == "stub"
    assert "<strong>Request pending</strong>" in body["html"]
    assert ".pbg/viz-requests/viz1.md" in body["html"]
    assert "/pbg-viz viz1" in body["html"]


def test_stub_code_generated_takes_precedence(tmp_path):
    # When BOTH the response .py and request .md exist, "Code generated" wins.
    ws = _make_ws(tmp_path, [{"name": "viz1", "description": "d"}])
    req = ws / ".pbg" / "viz-requests"
    req.mkdir(parents=True, exist_ok=True)
    (req / "viz1.md").write_text("request", encoding="utf-8")
    resp = ws / ".pbg" / "viz-responses"
    resp.mkdir(parents=True, exist_ok=True)
    (resp / "viz1.py").write_text("# generated", encoding="utf-8")
    body, status = views.visualization_preview_instance(ws, {"name": "viz1"})
    assert status == 200
    assert body["source_used"] == "stub"
    assert "<strong>Code generated</strong>" in body["html"]
    assert ".pbg/viz-responses/viz1.py" in body["html"]
    assert "<strong>Add to project</strong>" in body["html"]


def test_blank_class_treated_as_description_only(tmp_path):
    # class present but blank/whitespace → still the stub path.
    ws = _make_ws(tmp_path, [{"name": "viz1", "class": "   ", "description": "d"}])
    body, status = views.visualization_preview_instance(ws, {"name": "viz1"})
    assert status == 200
    assert body["source_used"] == "stub"


# ---------------------------------------------------------------------------
# has-class delegation
# ---------------------------------------------------------------------------

def test_has_class_delegates_to_visualization_preview(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path, [{
        "name": "viz1",
        "class": "MyViz",
        "config": {"k": "v"},
    }])
    captured = {}

    def _fake_preview(ws_root, body):
        captured["ws_root"] = ws_root
        captured["body"] = body
        return {"ok": True, "html": "<b>ok</b>", "source_used": "demo", "notes": ""}, 200

    monkeypatch.setattr(views.viz_preview_views, "visualization_preview", _fake_preview)
    body, status = views.visualization_preview_instance(
        ws, {"name": "viz1", "source": "investigation:abc"})
    assert status == 200
    assert body == {"ok": True, "html": "<b>ok</b>", "source_used": "demo", "notes": ""}
    assert captured["ws_root"] == ws
    assert captured["body"] == {
        "address": "local:MyViz",
        "config": {"k": "v"},
        "source": "investigation:abc",
    }


def test_has_class_default_source_demo_and_empty_config(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path, [{"name": "viz1", "class": "MyViz"}])  # no config, no source
    captured = {}

    def _fake_preview(ws_root, body):
        captured["body"] = body
        return {"ok": True}, 200

    monkeypatch.setattr(views.viz_preview_views, "visualization_preview", _fake_preview)
    views.visualization_preview_instance(ws, {"name": "viz1"})
    assert captured["body"] == {
        "address": "local:MyViz",
        "config": {},
        "source": "demo",
    }


def test_has_class_strips_class_whitespace(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path, [{"name": "viz1", "class": "  MyViz  "}])
    captured = {}
    monkeypatch.setattr(
        views.viz_preview_views, "visualization_preview",
        lambda ws_root, body: (captured.update(body) or {"ok": True}, 200),
    )
    views.visualization_preview_instance(ws, {"name": "viz1"})
    assert captured["address"] == "local:MyViz"
