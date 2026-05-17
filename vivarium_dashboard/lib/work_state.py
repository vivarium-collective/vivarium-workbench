"""Per-developer workstream state (active branch, push status, PR linkage).

Persisted at .pbg/state.json — gitignored, never committed.
"""
from __future__ import annotations
import json
import subprocess
from pathlib import Path

from ._root import workspace_root


_STATE_FILENAME = "state.json"
# Branches we refuse to auto-adopt as a workstream — committing to these
# from the dashboard would write to the integration branch directly.
_PROTECTED_BRANCHES = frozenset({"main", "master", "develop", "trunk", "HEAD"})


def _state_path() -> Path:
    return workspace_root() / ".pbg" / _STATE_FILENAME


def load_state() -> dict:
    """Return state dict; empty {} if file missing or unparseable."""
    p = _state_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text()) or {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(state: dict) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2) + "\n")


def clear_state() -> None:
    p = _state_path()
    if p.exists():
        p.unlink()


def get_active_branch() -> str | None:
    return load_state().get("active_branch")


def _current_git_branch(ws_root: Path) -> str | None:
    """Return the workspace's current git HEAD branch, or None on failure."""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=ws_root, capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    branch = r.stdout.strip()
    return branch or None


def _is_branch_pushed(ws_root: Path, branch: str) -> bool:
    """True iff local HEAD matches origin/<branch> (nothing local-only)."""
    try:
        r = subprocess.run(
            ["git", "rev-list", "--left-right", "--count", f"origin/{branch}...HEAD"],
            cwd=ws_root, capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if r.returncode != 0:
        return False
    parts = (r.stdout or "").strip().split()
    # parts == [left, right]; right == 0 means HEAD has no commits beyond origin.
    return len(parts) == 2 and parts[1] == "0"


def load_state_or_adopt_current() -> dict:
    """Like ``load_state`` but, if no ``active_branch`` is set, adopt the
    workspace's current git HEAD as the workstream — provided it's a
    non-protected branch.

    Lets workstream-gated endpoints (push, create-pr, commit-via-action)
    work on branches created outside the dashboard (git worktree add,
    a manual ``git checkout -b``, cloning into a feature branch, etc.)
    without requiring the user to click "Start workstream" first.

    Idempotent: once adopted, the new state is persisted to .pbg/state.json
    and subsequent calls see ``active_branch`` set the normal way.

    No-op (returns the loaded state unchanged) when:
      - state.json already has ``active_branch``
      - workspace_root() isn't a git repo (or git fails)
      - current branch is in _PROTECTED_BRANCHES (main/master/develop/trunk/HEAD)
    """
    state = load_state()
    if state.get("active_branch"):
        return state
    ws_root = workspace_root()
    branch = _current_git_branch(ws_root)
    if not branch or branch in _PROTECTED_BRANCHES:
        return state
    adopted = {
        "active_branch": branch,
        "base": "main",
        "pushed": _is_branch_pushed(ws_root, branch),
        "pr_number": None,
        "pr_url": None,
        "adopted": True,  # marker so callers/UI can tell this wasn't explicitly started
    }
    save_state(adopted)
    return adopted
