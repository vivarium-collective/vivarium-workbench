"""A declared interactive figure that can't be materialized into the static
snapshot must NOT keep its dead root-absolute ``iframe_url`` (which 404s on the
read-only dashboard's Visualizations tab under the hosting base path). It is
dropped and flagged ``snapshot_unavailable`` so the frontend shows a placeholder,
and returned so the publish can warn about it.
"""
from pathlib import Path

from vivarium_workbench.publish import _inline_declared_iframe_figures


def test_missing_declared_figure_marked_unavailable_not_dead_iframe(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    payload = {"charts": [
        {"key": "ghost", "media": "html", "iframe_url": "/multibody.html"},  # no such file
    ]}
    unresolved = _inline_declared_iframe_figures(payload, ws)
    rec = payload["charts"][0]
    assert "iframe_url" not in rec, "dead iframe_url must be dropped so it can't 404"
    assert rec.get("snapshot_unavailable") is True
    assert unresolved == ["ghost"]


def test_present_declared_figure_inlined_as_srcdoc(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "fig.html").write_text("<html><body>ok</body></html>", encoding="utf-8")
    payload = {"charts": [
        {"key": "real", "media": "html", "iframe_url": "/fig.html"},
    ]}
    unresolved = _inline_declared_iframe_figures(payload, ws)
    rec = payload["charts"][0]
    assert "iframe_url" not in rec
    assert "srcdoc" in rec and rec["srcdoc"]
    assert rec.get("snapshot_unavailable") is not True
    assert unresolved == []


def test_traversal_iframe_url_is_marked_unavailable(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    payload = {"charts": [
        {"key": "escape", "media": "html", "iframe_url": "/../secret.html"},
    ]}
    unresolved = _inline_declared_iframe_figures(payload, ws)
    rec = payload["charts"][0]
    assert "iframe_url" not in rec
    assert rec.get("snapshot_unavailable") is True
    assert unresolved == ["escape"]
