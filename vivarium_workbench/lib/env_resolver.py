"""`EnvironmentResolver` — resolve a workspace to a runnable interpreter.

The seam that decides *which Python* a workspace's env worker runs on. See
`docs/materialization-lifecycle.md` §2a/§2b and `docs/workspace-store.md` §8.

**Slice scope — the in-place local adapter only.** If the workspace checkout has
its own venv (`<ws>/.venv`, the default `uv sync` layout, materialization §2a),
use *its* interpreter — so a v2ecoli workspace builds under its provisioned
3.12.12 (§2b) regardless of what Python the workbench runs. Otherwise fall back to
the running interpreter, which is today's shared-env behavior — **behavior-
preserving for a workspace without a venv** (the fixtures, the demo image where
v2ecoli is co-installed). The *managed* path (materialize a venv via clone +
`uv sync`, keyed by the environment coordinate) arrives with the materialization
lifecycle; this resolver is where that adapter plugs in.
"""
from __future__ import annotations

import sys
from pathlib import Path

# venv interpreter relative paths — POSIX first (macOS/Linux, day one), then the
# Windows layout (materialization-lifecycle §2b: Windows is a later target).
_VENV_INTERPRETERS = (".venv/bin/python", ".venv/Scripts/python.exe")


def resolve_interpreter(workspace: Path | str) -> str:
    """The interpreter the workspace's env worker should run on.

    In-place local adapter: the checkout's own `.venv` if present, else the
    running interpreter (`sys.executable`).
    """
    ws = Path(workspace)
    for rel in _VENV_INTERPRETERS:
        cand = ws / rel
        if cand.is_file():
            return str(cand)
    return sys.executable
