"""Process-lifecycle helpers for detached composite runs.

The dashboard server spawns runs via ``spawn_detached`` and reconciles
crash-orphaned rows via ``reconcile_stale_runs`` on startup. All read/write
goes through ``composite_runs`` — this module only deals with OS processes.

(Historical note: this module used to also carry a vendored ``runs_meta`` read
accessor — ``RUNS_META_DDL`` + ``latest_run`` — copied from
``viva_superpowers/run_registry.py``. That block was dead code once its only
caller, ``lib/backfill_runs.py``, was removed, so the Phase-2.1a DDL-drift
deferral is resolved by simply deleting it. The workbench's real ``runs_meta``
schema — including the ``manifest_json`` column the vendored copy lacked — is
owned and migrated by ``composite_runs`` (``connect``/``_migrate_runs_meta``).)
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

from vivarium_workbench.lib import composite_runs as cr

# Maximum simultaneous in-flight runs. POST returns 429 above this.
CONCURRENCY_CAP = 4

# Terminal statuses — a run in any of these is finished; stop_run is a no-op.
_TERMINAL_STATUSES = {"completed", "failed", "orphaned", "cancelled"}


def _pid_alive(pid: int) -> bool:
    """True if a process with this PID currently exists."""
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists but owned by another user — still 'alive' for our purposes.
        return True
    return True


def spawn_detached(request_path: Path, *, workspace: Path,
                   log_path: Path) -> int:
    """Launch `vivarium-dashboard run-composite` fully detached.

    ``start_new_session=True`` puts the child in its own process group so it
    survives a dashboard-server restart. stdout/stderr are redirected into
    ``log_path``. Returns the child PID.
    """
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_fh = open(log_path, "w")  # noqa: SIM115 — handed to the child, closed in finally
    try:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(workspace) + os.pathsep + env.get("PYTHONPATH", "")
        # Pin the ParCa cache-fingerprint source tree to this workspace (see
        # env_worker_client for the full rationale): v2ecoli's candidate_repo_roots
        # resolves INPUT_FILES against $V2E_ROOT before falling back to a cwd walk,
        # so a detached runner whose cwd resolves a different workspace.yaml can't
        # hash a divergent tree for the same out/cache.
        env["V2E_ROOT"] = str(workspace)
        proc = subprocess.Popen(
            [sys.executable, "-m", "vivarium_workbench.cli",
             "run-composite", "--request", str(request_path)],
            cwd=str(workspace),
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=env,
        )
    finally:
        log_fh.close()  # the child holds its own dup'd fd; the parent's copy is done
    return proc.pid


def reconcile_stale_runs(db_file: str | Path, workspace=None) -> int:
    """Mark every 'running' row whose process is gone as 'orphaned'.

    Called on server startup. A row with a NULL pid (spawn never recorded
    one) or a dead pid is orphaned; a live pid is left alone — that run
    genuinely survived the restart. Returns the count reconciled.

    ``workspace`` is threaded through to ``mark_orphaned`` so the terminal
    state also lands in the JSONL run log; the Simulations DB treats JSONL as
    authoritative, so a sqlite-only reconcile leaves the row reading "running".
    """
    db_file = Path(db_file)
    if not db_file.is_file():
        return 0
    conn = cr.connect(db_file)
    try:
        rows = conn.execute(
            "SELECT run_id, pid FROM runs_meta WHERE status='running'"
        ).fetchall()
        reconciled = 0
        for row in rows:
            pid = row["pid"]
            if pid is None or not _pid_alive(int(pid)):
                cr.mark_orphaned(conn, run_id=row["run_id"], workspace=workspace)
                reconciled += 1
        return reconciled
    finally:
        conn.close()


def stop_run(db_file: str | Path, run_id: str, *, workspace=None,
             sig: int = signal.SIGTERM) -> dict:
    """Stop an in-flight detached run and mark it ``cancelled`` (issue #754).

    Signals the run's process GROUP — ``spawn_detached`` starts each run in its
    own session (``start_new_session=True``), so the pid is a group leader and
    ``killpg`` reaches the worker AND any children it spawned (e.g. Ray workers),
    which a bare ``kill`` would orphan. The default ``SIGTERM`` lets the worker's
    faulthandler dump a traceback into its run.log before exiting, giving a
    starting point for diagnosing what a frozen run was stuck on.

    Returns a result dict with an ``outcome``:

      * ``not_found``       — no such run_id (caller → HTTP 404)
      * ``already_terminal``— run already finished; no-op (caller → 200)
      * ``no_pid``          — running but spawn never recorded a pid; marked
                              cancelled without signalling (caller → 200)
      * ``dead``            — pid recorded but the process is already gone;
                              marked cancelled without signalling (caller → 200)
      * ``signalled``       — live process group signalled + marked cancelled
                              (caller → 200)

    Idempotent: stopping an already-terminal run does nothing and never signals.
    """
    db_file = Path(db_file)
    if not db_file.is_file():
        return {"run_id": run_id, "outcome": "not_found"}
    conn = cr.connect(db_file)
    try:
        row = cr.query_run_meta(conn, run_id=run_id)
        if row is None:
            return {"run_id": run_id, "outcome": "not_found"}
        status = row["status"]
        if status in _TERMINAL_STATUSES:
            return {"run_id": run_id, "outcome": "already_terminal",
                    "status": status}

        pid = row["pid"]
        if pid is None:
            cr.mark_cancelled(conn, run_id=run_id, workspace=workspace)
            return {"run_id": run_id, "outcome": "no_pid", "status": "cancelled"}

        pid = int(pid)
        if not _pid_alive(pid):
            cr.mark_cancelled(conn, run_id=run_id, workspace=workspace)
            return {"run_id": run_id, "outcome": "dead", "status": "cancelled"}

        # Signal the whole process group; fall back to the bare pid if the
        # process is (unexpectedly) not a group leader.
        try:
            os.killpg(os.getpgid(pid), sig)
        except (ProcessLookupError, PermissionError):
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass  # raced to exit between the alive-check and the signal
        cr.mark_cancelled(conn, run_id=run_id, workspace=workspace)
        return {"run_id": run_id, "outcome": "signalled", "status": "cancelled",
                "pid": pid}
    finally:
        conn.close()


def count_running(db_file: str | Path) -> int:
    """Count rows currently in status='running'."""
    db_file = Path(db_file)
    if not db_file.is_file():
        return 0
    conn = cr.connect(db_file)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM runs_meta WHERE status='running'"
        ).fetchone()[0]
    finally:
        conn.close()
