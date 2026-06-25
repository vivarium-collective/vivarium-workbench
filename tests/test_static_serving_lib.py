"""Tests for vivarium_dashboard.lib.static_serving (Phase C, Batch 16).

Covers the asset-resolution priority (bundled → assets-strip → workspace →
reports), the mime table, the loom traversal guard, and the parsimony
feature-detect None path — all without the HTTP layer.
"""

from pathlib import Path

import pytest

from vivarium_dashboard.lib import static_serving as ss


def test_guess_mime_table():
    assert ss.guess_mime("a.css") == "text/css"
    assert ss.guess_mime("a.js") == "application/javascript"
    assert ss.guess_mime("a.json") == "application/json"
    assert ss.guess_mime("a.png") == "image/png"
    assert ss.guess_mime("a.svg") == "image/svg+xml"
    assert ss.guess_mime("a.html") == "text/html"
    assert ss.guess_mime("a.tsv") == "text/tab-separated-values"
    # No charset suffix — bare values only.
    assert "charset" not in ss.guess_mime("a.css")
    # Unknown extensions fall back to text/plain.
    assert ss.guess_mime("a.weird") == "text/plain"
    assert ss.guess_mime("noext") == "text/plain"


def test_index_html_path(tmp_path):
    assert ss.index_html_path(tmp_path) == tmp_path / "reports" / "index.html"


def test_resolve_asset_bundled_wins(tmp_path, monkeypatch):
    """Step 1: a file present in STATIC_DIR wins over the workspace/reports."""
    static_dir = tmp_path / "bundled"
    static_dir.mkdir()
    (static_dir / "style.css").write_text("/* bundled */")
    monkeypatch.setattr(ss, "STATIC_DIR", static_dir)
    # Also seed a workspace copy + a reports copy — the bundled one must win.
    (tmp_path / "style.css").write_text("workspace")
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "style.css").write_text("reports")
    assert ss.resolve_asset(tmp_path, "style.css") == static_dir / "style.css"


def test_resolve_asset_assets_strip(tmp_path, monkeypatch):
    """Step 2: `assets/<f>` with no bundled `assets/<f>` retries STATIC_DIR/<f>."""
    static_dir = tmp_path / "bundled"
    static_dir.mkdir()
    (static_dir / "walkthrough.js").write_text("// bundled")
    monkeypatch.setattr(ss, "STATIC_DIR", static_dir)
    # A stale reports/assets copy must NOT shadow the bundled source.
    (tmp_path / "reports" / "assets").mkdir(parents=True)
    (tmp_path / "reports" / "assets" / "walkthrough.js").write_text("stale")
    got = ss.resolve_asset(tmp_path, "assets/walkthrough.js")
    assert got == static_dir / "walkthrough.js"


def test_resolve_asset_workspace_wins_over_reports(tmp_path, monkeypatch):
    """Step 3: a workspace-tree file wins over the reports fallback."""
    static_dir = tmp_path / "bundled"
    static_dir.mkdir()
    monkeypatch.setattr(ss, "STATIC_DIR", static_dir)
    (tmp_path / "data.txt").write_text("workspace")
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "data.txt").write_text("reports")
    assert ss.resolve_asset(tmp_path, "data.txt") == tmp_path / "data.txt"


def test_resolve_asset_reports_fallback(tmp_path, monkeypatch):
    """Step 4: nothing bundled/workspace → the reports path (even if absent)."""
    static_dir = tmp_path / "bundled"
    static_dir.mkdir()
    monkeypatch.setattr(ss, "STATIC_DIR", static_dir)
    # Absent everywhere → returns the reports path unconditionally (route 404s).
    got = ss.resolve_asset(tmp_path, "nope.txt")
    assert got == tmp_path / "reports" / "nope.txt"
    assert not got.is_file()
    # Present only in reports → that path is returned.
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "ok.txt").write_text("hi")
    assert ss.resolve_asset(tmp_path, "ok.txt") == tmp_path / "reports" / "ok.txt"


def test_resolve_loom_asset_default_index(monkeypatch):
    fake_dir = Path("/tmp/loom-assets")
    monkeypatch.setattr("bigraph_loom.asset_dir", lambda: fake_dir, raising=False)
    assert ss.resolve_loom_asset("") == fake_dir / "index.html"
    assert ss.resolve_loom_asset("sub/app.js") == fake_dir / "sub/app.js"


def test_resolve_loom_asset_traversal_rejected(monkeypatch):
    monkeypatch.setattr("bigraph_loom.asset_dir", lambda: Path("/tmp/x"), raising=False)
    with pytest.raises(ss.AssetTraversal):
        ss.resolve_loom_asset("../secret")


def test_resolve_parsimony_asset_none_when_no_dir(monkeypatch):
    monkeypatch.setattr(ss, "parsimony_viewer_dir", lambda: None)
    assert ss.resolve_parsimony_asset("") is None
    assert ss.resolve_parsimony_asset("foo.js") is None


def test_resolve_parsimony_asset_serves_when_present(monkeypatch):
    fake_dir = Path("/tmp/pv")
    monkeypatch.setattr(ss, "parsimony_viewer_dir", lambda: fake_dir)
    assert ss.resolve_parsimony_asset("") == fake_dir / "index.html"
    assert ss.resolve_parsimony_asset("viz/x.js") == fake_dir / "viz/x.js"


def test_resolve_parsimony_asset_traversal_rejected(monkeypatch):
    monkeypatch.setattr(ss, "parsimony_viewer_dir", lambda: Path("/tmp/pv"))
    with pytest.raises(ss.AssetTraversal):
        ss.resolve_parsimony_asset("../etc")
