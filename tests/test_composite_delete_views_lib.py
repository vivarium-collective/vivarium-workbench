"""Tests for ``lib.composite_delete_views.delete_composite_run``.

Verifies the outcome→HTTP-status mapping, that the run's ``runs_meta`` row and
``.pbg/runs/<run_id>/`` artifact directory are removed, and that a crafted
``run_id`` cannot delete a directory outside the runs-root.
"""
from __future__ import annotations

from pathlib import Path

from vivarium_workbench.lib import composite_delete_views as views
from vivarium_workbench.lib.composite_runs import (
    connect, save_metadata, complete_metadata, query_run_meta,
)


def _make_ws(tmp_path: Path) -> Path:
    (tmp_path / "workspace.yaml").write_text("name: demo-ws\n", encoding="utf-8")
    (tmp_path / ".pbg").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _db(ws: Path) -> Path:
    return ws / ".pbg" / "composite-runs.db"


def test_missing_run_id_400(tmp_path):
    ws = _make_ws(tmp_path)
    body, status = views.delete_composite_run(ws, "  ")
    assert status == 400
    assert body["error"] == "missing run_id"


def test_unknown_run_404(tmp_path):
    ws = _make_ws(tmp_path)
    connect(_db(ws)).close()  # empty db exists
    body, status = views.delete_composite_run(ws, "ghost")
    assert status == 404
    assert body["deleted"] is False
    assert body["outcome"] == "not_found"


def test_completed_run_is_deleted_with_artifacts_200(tmp_path):
    ws = _make_ws(tmp_path)
    conn = connect(_db(ws))
    save_metadata(conn, spec_id="s", run_id="gone", params={}, label="",
                  started_at=1.0, n_steps=5)
    complete_metadata(conn, run_id="gone", n_steps=5, status="completed")
    conn.close()
    # seed an artifact dir .pbg/runs/gone/
    run_dir = ws / ".pbg" / "runs" / "gone"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "viz.json").write_text("{}", encoding="utf-8")

    body, status = views.delete_composite_run(ws, "gone")
    assert status == 200
    assert body["deleted"] is True
    assert body["outcome"] == "deleted"
    # row and artifact dir are both gone
    conn = connect(_db(ws))
    assert query_run_meta(conn, run_id="gone") is None
    conn.close()
    assert not run_dir.exists()


def test_crafted_run_id_cannot_escape_runs_root(tmp_path):
    ws = _make_ws(tmp_path)
    connect(_db(ws)).close()
    # a sibling dir that must survive a traversal-style run_id
    victim = ws / ".pbg" / "precious"
    victim.mkdir(parents=True, exist_ok=True)
    (victim / "keep.txt").write_text("x", encoding="utf-8")

    body, status = views.delete_composite_run(ws, "../precious")
    # no such run row → 404, and the guard leaves the sibling dir intact
    assert status == 404
    assert victim.exists()
    assert (victim / "keep.txt").exists()
