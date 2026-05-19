"""Unit tests for vivarium_dashboard.server._discover_viz_html_files.

v2ecoli friction #17: the previous unconditional glob surfaced eagerly-
rendered topology.html / workflow.html as "(auto)" tabs that persisted
forever, even on un-run studies. The mtime gate skips any viz file older
than runs.db (no data has updated since) and skips all viz when runs.db
doesn't exist at all.
"""
import os
import time
import pytest


@pytest.fixture
def _ws(tmp_path, monkeypatch):
    import vivarium_dashboard.server as srv
    monkeypatch.setattr(srv, "WORKSPACE", tmp_path)
    return tmp_path


def _touch(path, *, mtime: float):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("<html></html>")
    os.utime(path, (mtime, mtime))


def test_no_viz_dir_returns_empty(_ws):
    from vivarium_dashboard.server import _discover_viz_html_files
    # studies/foo/ doesn't even exist
    assert _discover_viz_html_files("foo") == []


def test_no_runs_db_skips_all_viz(_ws):
    """The pre-fix bug: empty topology.html lived in viz/ on study-creation,
    no runs ever happened, but the dashboard kept surfacing it. After the
    fix, no runs.db == no data == no auto-viz."""
    from vivarium_dashboard.server import _discover_viz_html_files
    _touch(_ws / "studies" / "s1" / "viz" / "topology.html", mtime=time.time())
    assert _discover_viz_html_files("s1") == []


def test_viz_fresher_than_runs_db_is_surfaced(_ws):
    """The normal case: run completed → viz rendered → viz mtime > runs.db mtime."""
    from vivarium_dashboard.server import _discover_viz_html_files
    now = time.time()
    _touch(_ws / "studies" / "s1" / "runs.db", mtime=now - 10)
    _touch(_ws / "studies" / "s1" / "viz" / "comparative.html", mtime=now)
    out = _discover_viz_html_files("s1")
    assert len(out) == 1
    assert out[0]["name"] == "comparative (auto)"


def test_viz_older_than_runs_db_is_skipped(_ws):
    """The stale-render case: viz was rendered earlier, then someone ran a
    new sim. The dashboard should not show pre-sim viz as if it reflected
    the latest run."""
    from vivarium_dashboard.server import _discover_viz_html_files
    now = time.time()
    _touch(_ws / "studies" / "s1" / "runs.db", mtime=now)
    _touch(_ws / "studies" / "s1" / "viz" / "stale.html", mtime=now - 60)
    assert _discover_viz_html_files("s1") == []


def test_mixed_fresh_and_stale_only_fresh_surface(_ws):
    """A study with several viz files; only the post-run ones should show."""
    from vivarium_dashboard.server import _discover_viz_html_files
    now = time.time()
    _touch(_ws / "studies" / "s1" / "runs.db", mtime=now)
    _touch(_ws / "studies" / "s1" / "viz" / "stale.html", mtime=now - 100)
    _touch(_ws / "studies" / "s1" / "viz" / "fresh.html", mtime=now + 1)
    out = _discover_viz_html_files("s1")
    names = sorted(e["name"] for e in out)
    assert names == ["fresh (auto)"]
