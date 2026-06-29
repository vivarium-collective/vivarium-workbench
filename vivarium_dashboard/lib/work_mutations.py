"""Pure builders for the four workstream-lifecycle POST routes.

Behaviour-preserving ports of the stdlib handlers
``server.Handler._post_work_start`` / ``_post_work_push`` / ``_post_work_end`` /
``_post_work_attach_report``.  All four shell out to ``git`` in the active
workspace and read/write the per-developer workstream state, so they are pure
``(body, status)`` builders parameterised on ``ws_root`` — the FastAPI route
wraps every path in ``JSONResponse`` so the lib-returned status code is
preserved verbatim.  No ``import server`` here.

The two big GitHub routes (``work-create-pr`` / ``work-link-branch``) are a
separate later batch and are NOT ported here.

Note: ``lib/work_views.py`` already holds the workstream GET builders — this is
the *mutations* module.

The git / state / path names are referenced as module-level attributes
(:mod:`subprocess`, :mod:`re`, ``work_state.<fn>``, ``git_status.<fn>``,
``WorkspacePaths``) rather than ``from ... import`` bindings, so tests can
monkeypatch ``work_mutations.subprocess.run`` /
``work_mutations.work_state.<fn>`` / ``work_mutations.git_status.<fn>`` with
fakes and never touch real git.

Every message, status code, git command/flag/timeout, and string slice is
reproduced byte-identically from the legacy handlers.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from vivarium_dashboard.lib import git_status
from vivarium_dashboard.lib import work_state
from vivarium_dashboard.lib.workspace_paths import WorkspacePaths


def work_start(ws_root: Path, body: dict | None) -> tuple[dict, int]:
    """POST /api/work-start — create a new working branch from base, set active.

    Port of ``_post_work_start`` (``cwd=ws_root``):

      * invalid branch name        → ``({"error": "invalid branch name"}, 400)``
      * already on a workstream     → ``({"error": "already on workstream '<b>'. End it first."}, 409)``
      * dirty working tree          → ``({"error": "working tree dirty — commit or stash first"}, 409)``
      * base branch not found       → ``({"error": "base branch '<base>' not found"}, 404)``
      * branch already exists        → ``({"error": "branch '<b>' already exists. ..."}, 409)``
      * branch create failed         → ``({"error": "branch create failed: <stderr[:300]>"}, 500)``
      * happy path                   → ``({"ok": True, "branch": <b>, "base": <base>}, 200)``
    """
    body = body or {}
    branch = (body.get("branch") or "").strip()
    base = (body.get("base") or "main").strip()
    if not branch or not re.match(r"^[A-Za-z0-9._/-]+$", branch) or len(branch) > 100:
        return {"error": "invalid branch name"}, 400

    state = work_state.load_state()
    if state.get("active_branch"):
        return {"error": f"already on workstream '{state['active_branch']}'. End it first."}, 409
    if git_status.dirty_workspace(ws_root).strip():
        return {"error": "working tree dirty — commit or stash first"}, 409

    # Verify base exists
    r = subprocess.run(["git", "rev-parse", "--verify", base], cwd=ws_root, capture_output=True, text=True)
    if r.returncode != 0:
        return {"error": f"base branch '{base}' not found"}, 404

    # Verify branch doesn't already exist locally
    r = subprocess.run(["git", "rev-parse", "--verify", branch], cwd=ws_root, capture_output=True, text=True)
    if r.returncode == 0:
        return {"error": f"branch '{branch}' already exists. Pick a different name or delete the old one."}, 409

    subprocess.run(["git", "checkout", base], cwd=ws_root, check=True, capture_output=True)
    r = subprocess.run(["git", "checkout", "-b", branch], cwd=ws_root, capture_output=True, text=True)
    if r.returncode != 0:
        return {"error": f"branch create failed: {r.stderr[:300]}"}, 500

    work_state.save_state({"active_branch": branch, "base": base, "pushed": False, "pr_number": None, "pr_url": None})
    return {"ok": True, "branch": branch, "base": base}, 200


def work_push(ws_root: Path, body: dict | None) -> tuple[dict, int]:
    """POST /api/work-push — push the active workstream branch to origin.

    Port of ``_post_work_push`` (``cwd=ws_root``):

      * no active workstream  → ``({"error": "no active workstream"}, 409)``
      * no origin remote      → 409 with the structured ``no_origin`` diagnosis body
      * push failed           → ``({"error": "push failed: <err[:300]>", "diagnosis"?}, 500)``
      * happy path            → ``({"ok": True, "branch": <b>, "log": <stdout[-300:]>}, 200)``
    """
    state = work_state.load_state_or_adopt_current()
    branch = state.get("active_branch")
    if not branch:
        return {"error": "no active workstream"}, 409

    # Pre-flight: refuse cleanly when no origin remote exists (the common
    # confusion on fresh workspaces). Surface a structured diagnosis the
    # JS layer can render as a clickable Create-GitHub-repo prompt.
    if not git_status.has_origin_remote(ws_root):
        return {
            "error": "no GitHub remote configured",
            "diagnosis": {
                "category": "no_origin",
                "summary": "This workspace has no `origin` remote yet.",
                "suggestion": "Click `Create GitHub repo` in the workstream strip to create one in your account and push in a single step.",
            },
        }, 409

    r = subprocess.run(
        ["git", "push", "-u", "origin", branch],
        cwd=ws_root, capture_output=True, text=True, timeout=60,
    )
    if r.returncode != 0:
        err = (r.stderr or r.stdout).strip()
        diag = git_status.diagnose_push_error(err)
        resp: dict = {"error": f"push failed: {err[:300]}"}
        if diag:
            resp["diagnosis"] = diag
        return resp, 500
    state["pushed"] = True
    work_state.save_state(state)
    return {"ok": True, "branch": branch, "log": r.stdout[-300:]}, 200


def work_end(ws_root: Path, body: dict | None) -> tuple[dict, int]:
    """POST /api/work-end — check out the base branch + clear workstream state.

    Port of ``_post_work_end`` (``cwd=ws_root``):

      * no active workstream  → ``({"error": "no active workstream"}, 409)``
      * dirty working tree    → ``({"error": "uncommitted changes — commit or stash before ending"}, 409)``
      * happy path            → ``({"ok": True}, 200)``
    """
    state = work_state.load_state()
    if not state.get("active_branch"):
        return {"error": "no active workstream"}, 409
    if git_status.dirty_workspace(ws_root).strip():
        return {"error": "uncommitted changes — commit or stash before ending"}, 409
    base = state.get("base", "main")
    subprocess.run(["git", "checkout", base], cwd=ws_root, check=True, capture_output=True)
    work_state.clear_state()
    return {"ok": True}, 200


def work_attach_report(ws_root: Path, body: dict | None) -> tuple[dict, int]:
    """POST /api/work-attach-report — write a report file + commit it on the branch.

    Port of ``_post_work_attach_report`` (``cwd=ws_root``):

      * no active branch       → ``({"error": "no active investigation branch"}, 409)``
      * filename + html missing → ``({"error": "filename + html required"}, 400)``
      * path-y filename         → ``({"error": "filename must be a bare name (no path / no leading .)"}, 400)``
      * git add failed          → ``({"error": "git add failed: <(stderr or stdout)[:300]>"}, 500)``
      * nothing to commit        → ``({"ok": True, "unchanged": True, "path": <rel>, "branch": <b>}, 200)``
      * git commit failed        → ``({"error": "git commit failed: <stderr[:300]>"}, 500)``
      * happy path               → ``({"ok": True, "path": <rel>, "branch": <b>, "commit_sha": <sha>}, 200)``
    """
    body = body or {}
    state = work_state.load_state()
    branch = state.get("active_branch")
    if not branch:
        return {"error": "no active investigation branch"}, 409

    filename = (body.get("filename") or "").strip()
    html = body.get("html")
    if not filename or not isinstance(html, str) or not html:
        return {"error": "filename + html required"}, 400
    if "/" in filename or filename.startswith("."):
        return {"error": "filename must be a bare name (no path / no leading .)"}, 400
    commit_message = (body.get("commit_message") or
                      f"docs(report): attach {filename}").strip()

    reports_dir = WorkspacePaths.load(ws_root).reports
    reports_dir.mkdir(parents=True, exist_ok=True)
    out_path = reports_dir / filename
    out_path.write_text(html, encoding="utf-8")

    # Stage + commit. Allow the commit to fail cleanly when the file
    # hasn't actually changed (caller still gets a success response with
    # an `unchanged: true` flag).
    rel = str(out_path.relative_to(ws_root))
    add = subprocess.run(["git", "add", "--", rel],
                         cwd=ws_root, capture_output=True, text=True, timeout=10)
    if add.returncode != 0:
        return {"error": f"git add failed: {(add.stderr or add.stdout)[:300]}"}, 500
    commit = subprocess.run(
        ["git", "commit", "-m", commit_message, "--", rel],
        cwd=ws_root, capture_output=True, text=True, timeout=15,
    )
    if commit.returncode != 0:
        stderr = (commit.stderr or commit.stdout)
        # git returns non-zero when there's nothing to commit — treat as a soft success.
        if "nothing to commit" in stderr or "nothing added" in stderr:
            return {"ok": True, "unchanged": True, "path": rel,
                    "branch": branch}, 200
        return {"error": f"git commit failed: {stderr[:300]}"}, 500
    sha = subprocess.run(["git", "rev-parse", "HEAD"],
                         cwd=ws_root, capture_output=True, text=True, timeout=5)
    return {"ok": True, "path": rel, "branch": branch,
            "commit_sha": sha.stdout.strip()}, 200
