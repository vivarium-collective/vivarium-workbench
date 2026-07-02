"""Per-developer workstream state (active branch, push status, PR linkage).

Persisted at .pbg/state.json — gitignored, never committed.
"""
from __future__ import annotations
import json
import subprocess
import sys
from pathlib import Path

from ._root import workspace_root
from .workspace_paths import WorkspacePaths


_STATE_FILENAME = "state.json"
# Branches we refuse to auto-adopt as a workstream — committing to these
# from the dashboard would write to the integration branch directly.
_PROTECTED_BRANCHES = frozenset({"main", "master", "develop", "trunk", "HEAD"})


def _state_path() -> Path:
    return WorkspacePaths.load(workspace_root()).pbg / _STATE_FILENAME


def load_state() -> dict:
    """Return state dict; empty {} if file missing or unparseable."""
    p = _state_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8")) or {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(state: dict) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


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


def active_branch_action(ws_root: Path, commit_message: str, action_fn) -> tuple[dict, int]:
    """Run ``action_fn`` on the active workstream branch, commit, stay on it.

    Returns ``(payload, status)``. Relocated from the retired
    ``server._active_branch_action`` — parameterised on ``ws_root`` (used as the
    git cwd) instead of the module-global ``WORKSPACE``. The workstream-state
    helpers (load/save/adopt) read the active workspace via ``_root``, so the
    caller must have registered ``ws_root`` via ``set_workspace_root`` (the FastAPI
    seam + CLI do this at startup).
    """
    from vivarium_workbench.lib.git_status import dirty_workspace

    ws_root = Path(ws_root)
    ws = str(ws_root)
    if ws not in sys.path:
        sys.path.insert(0, ws)

    state = load_state_or_adopt_current()
    branch = state.get("active_branch")
    if not branch:
        return {"error": "no active workstream — click Start workstream at the top of the dashboard, or check out a feature branch first"}, 409

    # Make sure we're on the active branch (auto-recover from drift)
    current = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=ws_root, capture_output=True, text=True, check=True,
    ).stdout.strip()
    if current != branch:
        r = subprocess.run(["git", "checkout", branch], cwd=ws_root, capture_output=True, text=True)
        if r.returncode != 0:
            return {"error": f"could not check out workstream branch '{branch}': {r.stderr[:200]}"}, 500

    if dirty_workspace(ws_root).strip():
        return {"error": f"working tree dirty: {dirty_workspace(ws_root)[:300]}"}, 409

    try:
        action_fn()
        # Stage only the content the dashboard authors. A blanket `git add -A`
        # can sweep large untracked artifact dirs (out/, the ~175 MB ParCa
        # cache) into the commit; scoping the pathspec makes that impossible.
        # reports/ is intentionally excluded — it is generated, not authored.
        _STAGE_PATHS = [
            "studies/", "investigations/", "models/", "scripts/",
            "workspace.yaml", "pyproject.toml", ".gitmodules", ".gitignore",
            "external/",
        ]
        present = [p for p in _STAGE_PATHS if (ws_root / p).exists()]
        if present:
            subprocess.run(
                ["git", "add", "-A", "--", *present],
                cwd=ws_root, check=True, capture_output=True,
            )
        # Also stage any already-tracked top-level *.py / *.yaml the action
        # touched, without picking up untracked files.
        subprocess.run(
            ["git", "add", "--update"],
            cwd=ws_root, check=True, capture_output=True,
        )
        diff = subprocess.run(
            ["git", "diff", "--cached", "--stat"],
            cwd=ws_root, capture_output=True, text=True, check=True,
        ).stdout
        if not diff.strip():
            return {"error": "action made no changes (already at this state?)"}, 409
        subprocess.run([
            "git", "-c", "user.email=pbg-template@local",
                  "-c", "user.name=pbg-template",
                  "commit", "-m", commit_message,
        ], cwd=ws_root, check=True, capture_output=True)
        commit_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ws_root, capture_output=True, text=True, check=True,
        ).stdout.strip()

        # Reload state (action_fn may have side-effects) and keep file fresh
        state = load_state()
        if state.get("active_branch") == branch:
            save_state(state)

        return {"branch": branch, "commit": commit_sha[:7], "message": commit_message}, 200
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
        return {"error": f"git operation failed: {stderr[:300]}"}, 500
    except Exception as e:
        return {"error": str(e)}, 500
