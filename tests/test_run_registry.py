"""Unit tests for vivarium_workbench.lib.run_registry."""
import os

from vivarium_workbench.lib.composite_runs import (
    connect, save_metadata, complete_metadata, query_run_meta,
)
from vivarium_workbench.lib.run_registry import (
    reconcile_stale_runs, count_running,
)


def test_reconcile_marks_dead_pid_orphaned(tmp_path):
    db_file = tmp_path / "runs.db"
    conn = connect(db_file)
    # A 'running' row whose pid is almost certainly not a live process.
    save_metadata(conn, spec_id="s", run_id="dead", params={}, label="",
                  started_at=1.0, n_steps=5)
    conn.execute("UPDATE runs_meta SET pid=? WHERE run_id='dead'", (999_999,))
    conn.commit()
    conn.close()

    n = reconcile_stale_runs(db_file)
    assert n == 1
    conn = connect(db_file)
    assert query_run_meta(conn, run_id="dead")["status"] == "orphaned"
    conn.close()


def test_reconcile_leaves_live_pid_running(tmp_path):
    db_file = tmp_path / "runs.db"
    conn = connect(db_file)
    save_metadata(conn, spec_id="s", run_id="alive", params={}, label="",
                  started_at=1.0, n_steps=5)
    conn.execute("UPDATE runs_meta SET pid=? WHERE run_id='alive'",
                 (os.getpid(),))
    conn.commit()
    conn.close()

    n = reconcile_stale_runs(db_file)
    assert n == 0
    conn = connect(db_file)
    assert query_run_meta(conn, run_id="alive")["status"] == "running"
    conn.close()


def test_reconcile_marks_null_pid_orphaned(tmp_path):
    db_file = tmp_path / "runs.db"
    conn = connect(db_file)
    save_metadata(conn, spec_id="s", run_id="nopid", params={}, label="",
                  started_at=1.0, n_steps=5)
    conn.close()
    assert reconcile_stale_runs(db_file) == 1
    conn = connect(db_file)
    assert query_run_meta(conn, run_id="nopid")["status"] == "orphaned"
    conn.close()


def test_count_running_counts_only_running(tmp_path):
    db_file = tmp_path / "runs.db"
    conn = connect(db_file)
    save_metadata(conn, spec_id="s", run_id="r1", params={}, label="",
                  started_at=1.0, n_steps=5)
    save_metadata(conn, spec_id="s", run_id="r2", params={}, label="",
                  started_at=2.0, n_steps=5)
    complete_metadata(conn, run_id="r2", n_steps=5, status="completed")
    conn.close()
    assert count_running(db_file) == 1
