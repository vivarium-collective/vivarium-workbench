"""Worktree-aware runner for the discovery cycle's consequential actions (G5).

The engine's ``execute_action`` dispatches a consequential action (run a study,
or the compound seed-then-run for a branch) by calling an injectable ``runner``
with the rendered ``command``. Its own fallback (``discover._default_runner``)
shells the command against the **canonical editable install** — so it cannot run
a *freshly-built* composite that lives only in a git worktree (e.g. one a
``build_hook`` just built). This is the workbench's runner impl behind the agreed
seam:

    run(command, ws_root, *, worktree=None, timeout=3600)
        -> {"ok": bool, "returncode": int, "run_id": str | None, "tail": str}

``worktree=None`` reproduces the engine fallback's behavior exactly (shell the
command in ``ws_root`` with the running interpreter's venv bin prepended to PATH,
so a bare ``vivarium-workbench`` console script resolves to THIS venv). When a
``worktree`` is given, its path is additionally prepended to ``PYTHONPATH`` so the
freshly-built composite there resolves **over** the canonical editable — the whole
point of G5.

The workbench injects this at ``cycle_trace``'s sandbox_advance / targeted-advance
call sites so cycle runs can target a build worktree; it also surfaces the
authoritative ``run_id`` (the engine fallback only parses it best-effort).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path


def _study_slug_from_command(command: str) -> str | None:
    """Best-effort: pull the study slug from a ``... run study <slug> ...`` command
    (the run-study op, and the tail of the compound seed-then-run branch command)."""
    m = re.search(r"\brun\s+study\s+([A-Za-z0-9._-]+)", command)
    return m.group(1) if m else None


def _latest_run_id(ws_root: Path, slug: str | None) -> str | None:
    """Authoritative run_id: the newest run recorded for ``slug`` after the run.

    Best-effort and never raises — a missing registry / study just yields ``None``
    (same contract as the engine fallback, but authoritative when available)."""
    if not slug:
        return None
    try:
        from vivarium_workbench.lib.workspace_paths import WorkspacePaths
        from vivarium_workbench.lib import run_registry  # noqa: F401 — presence check
        wp = WorkspacePaths.load(Path(ws_root))
        db = wp.studies / slug / "runs.db"
        if not db.is_file():
            return None
        import sqlite3
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            # Newest run row; tolerate schema variants (run_id | id) and ts column.
            cur = con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            tables = {r[0] for r in cur.fetchall()}
            table = "runs" if "runs" in tables else next(iter(tables), None)
            if not table:
                return None
            cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
            id_col = "run_id" if "run_id" in cols else ("id" if "id" in cols else None)
            if not id_col:
                return None
            order = "ts" if "ts" in cols else ("created" if "created" in cols else id_col)
            row = con.execute(
                f"SELECT {id_col} FROM {table} ORDER BY {order} DESC LIMIT 1"
            ).fetchone()
            return str(row[0]) if row and row[0] is not None else None
        finally:
            con.close()
    except Exception:  # noqa: BLE001 — run_id is best-effort provenance, never fatal
        return None


def run(command: str, ws_root: str | Path, *, worktree: str | Path | None = None,
        timeout: int = 3600) -> dict:
    """Run a cycle action's ``command`` as a subprocess; return its outcome.

    Args:
        command: The rendered executor command (run-study, the compound
            seed-then-run for a branch, or a render-viz fallback).
        ws_root: The workspace the command runs in (``cwd``).
        worktree: If given, prepended to ``PYTHONPATH`` so a composite freshly
            built in that worktree resolves over the canonical editable install.
        timeout: Seconds before the subprocess is killed (default 1h).

    Returns:
        ``{"ok": bool, "returncode": int, "run_id": str | None, "tail": str}`` —
        ``tail`` is the last ~400 chars of stdout/stderr; ``run_id`` is the
        authoritative newest run recorded for the study (best-effort, else None).
    """
    ws_root = Path(ws_root)
    env = dict(os.environ)
    # The running interpreter's venv bin first, so a bare console script in the
    # command resolves to THIS venv's workspace package (discover._default_runner's
    # lesson: a sibling venv earlier on PATH runs against the wrong workspace and
    # reports the composite 'not found').
    env["PATH"] = os.path.dirname(sys.executable) + os.pathsep + env.get("PATH", "")
    if worktree is not None:
        wt = str(Path(worktree).resolve())
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = wt + (os.pathsep + existing if existing else "")
    try:
        proc = subprocess.run(command, shell=True, cwd=str(ws_root), env=env,
                              capture_output=True, text=True, timeout=timeout)
        returncode = proc.returncode
        tail = (proc.stdout or proc.stderr or "")[-400:]
    except subprocess.TimeoutExpired as e:
        return {"ok": False, "returncode": 124, "run_id": None,
                "tail": f"timeout after {timeout}s: {str(e)[-300:]}"}
    run_id = _latest_run_id(ws_root, _study_slug_from_command(command)) if returncode == 0 else None
    return {"ok": returncode == 0, "returncode": returncode, "run_id": run_id, "tail": tail}
