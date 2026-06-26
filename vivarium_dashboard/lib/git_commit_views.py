"""Pure builders for the two git-subprocess commit/push POST routes.

Behaviour-preserving ports of the stdlib handlers
``server.Handler._post_branch_push`` and ``_post_dirty_commit_all``.  Both
shell out to ``git`` in the active workspace, so they are pure ``(body, status)``
builders parameterised on ``ws_root`` — the FastAPI route wraps every path in
``JSONResponse`` so the lib-returned status code is preserved verbatim.  No
``import server`` here.

The git/state names are referenced as module-level attributes
(:mod:`subprocess`, ``git_status.<fn>``, ``work_state.load_state_or_adopt_current``)
rather than ``from ... import`` bindings, so tests can monkeypatch
``git_commit_views.subprocess.run`` / ``git_commit_views.git_status.<fn>`` /
``git_commit_views.work_state.load_state_or_adopt_current`` with fakes and never
touch real git.

``branch_push`` delegates the whole commit+push to
``git_status.remote_commit_and_push`` (which itself calls the 3c
``remote_push_and_sha``); ``dirty_commit_all`` reproduces the legacy
dirty-commit git sequence (rev-parse → checkout-if-needed → dirty check →
``add -A`` → ``reset HEAD -- reports/`` → commit with the ``user.email`` /
``user.name`` flags → ``rev-parse HEAD``) byte-identically with ``cwd=ws_root``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from vivarium_dashboard.lib import git_status
from vivarium_dashboard.lib import work_state


def branch_push(ws_root: Path, body: dict | None) -> tuple[dict, int]:
    """POST /api/branch/push — commit *ws_root* changes + push current branch.

    Port of ``_post_branch_push``:

      * happy path        → ``(git_status.remote_commit_and_push(...), 200)``
      * ``NotAGitRepo``   → ``({"error": <msg>}, 409)``
      * any other error   → ``({"error": <msg>}, 500)``
    """
    message = (body or {}).get("message") or "dashboard commit"
    try:
        return git_status.remote_commit_and_push(ws_root, message), 200
    except git_status.NotAGitRepo as e:
        return {"error": str(e)}, 409
    except Exception as e:
        return {"error": str(e)}, 500


def dirty_commit_all(ws_root: Path, body: dict | None) -> tuple[dict, int]:
    """POST /api/dirty-commit-all — stage+commit all dirty files (minus reports/).

    Port of ``_post_dirty_commit_all`` (``cwd=ws_root``):

      * no active workstream  → ``({"error": "no active workstream"}, 409)``
      * rev-parse failure     → ``({"error": f"git rev-parse failed: {stderr[:200]}"}, 500)``
      * checkout failure      → ``({"error": f"could not check out '{branch}': {r.stderr[:200]}"}, 500)``
      * already clean         → ``({"error": "working tree is already clean"}, 409)``
      * git op failure        → ``({"error": f"git operation failed: {stderr[:300]}"}, 500)``
      * happy path            → ``({"commit_sha": sha[:7], "message", "paths"}, 200)``
    """
    state = work_state.load_state_or_adopt_current()
    branch = state.get("active_branch")
    if not branch:
        return {"error": "no active workstream"}, 409
    # Ensure we're on the active branch
    try:
        current = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=ws_root, capture_output=True, text=True, check=True,
        ).stdout.strip()
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
        return {"error": f"git rev-parse failed: {stderr[:200]}"}, 500
    if current != branch:
        r = subprocess.run(["git", "checkout", branch], cwd=ws_root, capture_output=True, text=True)
        if r.returncode != 0:
            return {"error": f"could not check out '{branch}': {r.stderr[:200]}"}, 500
    dirty = git_status.dirty_workspace(ws_root).strip()
    if not dirty:
        return {"error": "working tree is already clean"}, 409
    paths = [line[3:] for line in dirty.splitlines() if len(line) >= 4]
    message = git_status.suggest_dirty_commit_message(paths)
    try:
        subprocess.run(["git", "add", "-A"], cwd=ws_root, check=True, capture_output=True)
        subprocess.run(["git", "reset", "HEAD", "--", "reports/"], cwd=ws_root, check=False, capture_output=True)
        subprocess.run([
            "git", "-c", "user.email=pbg-template@local",
                  "-c", "user.name=pbg-template",
                  "commit", "-m", message,
        ], cwd=ws_root, check=True, capture_output=True)
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ws_root, capture_output=True, text=True, check=True,
        ).stdout.strip()
        return {"commit_sha": sha[:7], "message": message, "paths": paths}, 200
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
        return {"error": f"git operation failed: {stderr[:300]}"}, 500
