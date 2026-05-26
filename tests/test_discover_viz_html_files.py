"""Unit tests for vivarium_dashboard.server._discover_viz_html_files.

v2ecoli friction #17: an unconditional glob surfaced eagerly-rendered
topology.html / workflow.html as "(auto)" tabs that persisted forever on
un-run studies. The first fix added an mtime gate that *silently dropped*
any viz older than runs.db.

mem3dg-readdy (2026-05-20): that gate dropped legitimate freshly-rendered
charts — a WAL checkpoint on the render's own read connection bumped runs.db
mtime a few seconds AFTER the HTML was written, so every chart vanished with
no error. The robustness rule is now "no silent drops": surface every viz
once a study has run; the only hard guard is "no runs.db → nothing"; past-run
staleness is surfaced via a `stale` flag (derived from runs_meta timestamps,
which WAL checkpoints don't pollute), never swallowed.
"""
import os
import sqlite3
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


def _runs_db(path, *, latest_completed_at: float | None):
    """Create a minimal runs_meta table with one row at the given time."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE runs_meta (run_id TEXT PRIMARY KEY, spec_id TEXT, "
        "started_at REAL, completed_at REAL, status TEXT)"
    )
    if latest_completed_at is not None:
        conn.execute(
            "INSERT INTO runs_meta VALUES (?, ?, ?, ?, ?)",
            ("r1", "spec", latest_completed_at - 1, latest_completed_at, "completed"),
        )
    conn.commit()
    conn.close()


def test_no_viz_dir_returns_empty(_ws):
    from vivarium_dashboard.server import _discover_viz_html_files
    assert _discover_viz_html_files("foo") == []


def test_no_runs_db_skips_all_viz(_ws):
    """The genuine pre-data guard: no runs.db == no real run == no auto-viz.
    Eagerly-rendered junk on study-creation must not surface."""
    from vivarium_dashboard.server import _discover_viz_html_files
    _touch(_ws / "studies" / "s1" / "viz" / "topology.html", mtime=time.time())
    assert _discover_viz_html_files("s1") == []


def test_viz_after_run_surfaces_not_stale(_ws):
    """Normal case: run completed, viz rendered just after → surfaced, fresh."""
    from vivarium_dashboard.server import _discover_viz_html_files
    now = time.time()
    _runs_db(_ws / "studies" / "s1" / "runs.db", latest_completed_at=now - 10)
    _touch(_ws / "studies" / "s1" / "viz" / "coupling-trace.html", mtime=now)
    out = _discover_viz_html_files("s1")
    assert len(out) == 1
    assert out[0]["name"] == "coupling-trace (auto)"
    assert out[0]["stale"] is False


def test_viz_rendered_before_db_file_mtime_still_surfaces(_ws):
    """Regression: a WAL checkpoint bumps the runs.db FILE mtime after the
    viz was written, but the recorded run completed_at is older. The viz is
    legitimate and must NOT be dropped (the original silent-drop bug)."""
    from vivarium_dashboard.server import _discover_viz_html_files
    now = time.time()
    db = _ws / "studies" / "s1" / "runs.db"
    # run completed 20s ago; viz rendered right after (10s ago) ...
    _runs_db(db, latest_completed_at=now - 20)
    _touch(_ws / "studies" / "s1" / "viz" / "coupling-trace.html", mtime=now - 10)
    # ... but the db FILE mtime is "now" (a later read-connection checkpoint).
    os.utime(db, (now, now))
    out = _discover_viz_html_files("s1")
    assert len(out) == 1, "legit post-run viz must not be dropped on db file mtime"
    assert out[0]["stale"] is False


def test_stale_viz_is_surfaced_with_flag_not_dropped(_ws):
    """A new run happened after the viz was rendered: the chart predates the
    latest run. It is SURFACED with stale=True (and a warning note), never
    silently dropped."""
    from vivarium_dashboard.server import _discover_viz_html_files
    now = time.time()
    _runs_db(_ws / "studies" / "s1" / "runs.db", latest_completed_at=now)
    _touch(_ws / "studies" / "s1" / "viz" / "stale.html", mtime=now - 120)
    out = _discover_viz_html_files("s1")
    assert len(out) == 1
    assert out[0]["name"] == "stale (auto)"
    assert out[0]["stale"] is True
    assert "predate" in out[0]["description"].lower()


def test_mixed_fresh_and_stale_both_surface(_ws):
    """Both fresh and stale viz surface; staleness is flagged, not hidden."""
    from vivarium_dashboard.server import _discover_viz_html_files
    now = time.time()
    _runs_db(_ws / "studies" / "s1" / "runs.db", latest_completed_at=now)
    _touch(_ws / "studies" / "s1" / "viz" / "stale.html", mtime=now - 120)
    _touch(_ws / "studies" / "s1" / "viz" / "fresh.html", mtime=now + 1)
    out = {e["name"]: e["stale"] for e in _discover_viz_html_files("s1")}
    assert out == {"fresh (auto)": False, "stale (auto)": True}


# ---------------------------------------------------------------------------
# Second source: reports/figures/<name>/*.html (hand-authored cross-skill output)
# Added 2026-05-25 after v2ecoli-pdmp friction (had to author embed_visualizations:
# entries by hand because auto-discovery only knew about studies/<name>/viz/).
# ---------------------------------------------------------------------------


def test_discovers_reports_figures_without_runs_db_gate(_ws):
    """reports/figures/<name>/*.html surfaces even with no runs.db. These
    are hand-authored (not run-derived), so the runs.db gate doesn't apply."""
    from vivarium_dashboard.server import _discover_viz_html_files
    now = time.time()
    _touch(_ws / "reports" / "figures" / "s1" / "diagram.html", mtime=now)
    _touch(_ws / "reports" / "figures" / "s1" / "summary.html", mtime=now)
    # NO runs.db — would have hidden source 1 viz, but source 2 is unaffected.
    out = _discover_viz_html_files("s1")
    assert len(out) == 2
    names = sorted(e["name"] for e in out)
    assert names == ["diagram", "summary"]
    # Hand-authored URL path uses reports/figures/<name>/
    urls = sorted(e["url"] for e in out)
    assert urls == ["/reports/figures/s1/diagram.html", "/reports/figures/s1/summary.html"]
    # No stale flag on the hand-authored side.
    assert all(e["stale"] is False for e in out)


def test_both_sources_concat(_ws):
    """A study with BOTH studies/<name>/viz/*.html (auto) and
    reports/figures/<name>/*.html (hand-authored) shows ALL entries."""
    from vivarium_dashboard.server import _discover_viz_html_files
    now = time.time()
    _runs_db(_ws / "studies" / "s1" / "runs.db", latest_completed_at=now)
    _touch(_ws / "studies" / "s1" / "viz" / "auto.html", mtime=now + 1)
    _touch(_ws / "reports" / "figures" / "s1" / "hand.html", mtime=now)
    out = _discover_viz_html_files("s1")
    names = sorted(e["name"] for e in out)
    assert names == ["auto (auto)", "hand"]
