"""Pure builder for the GitHub-PR-create POST route.

Behaviour-preserving port of the stdlib handler
``server.Handler._post_work_create_pr``.  It creates a GitHub pull request for
the active workstream branch via the ``gh`` CLI, shelling out in the active
workspace, so it is a pure ``(body, status)`` builder parameterised on
``ws_root`` — the FastAPI route wraps every path in ``JSONResponse`` so the
lib-returned status code is preserved verbatim.  No ``import server`` here.

The git/gh/state names are referenced as module-level attributes
(:mod:`subprocess`, :mod:`shutil`, ``work_state.load_state_or_adopt_current``,
``report._detect_github_repo``) rather than ``from ... import`` bindings, so
tests can monkeypatch ``work_pr_views.subprocess.run`` /
``work_pr_views.shutil.which`` / ``work_pr_views.work_state.<fn>`` /
``work_pr_views.report._detect_github_repo`` with fakes and never touch real
git or gh.

``work_create_pr`` reproduces the legacy handler byte-identically with
``WORKSPACE``/``workspace_paths()`` → ``ws_root``/``WorkspacePaths.load(ws_root)``:

  * no active workstream                         → 409
  * opportunistic ``git rev-list`` pushed-mark   (state["pushed"] = True)
  * branch not yet pushed (the long UI message)  → 409
  * a PR already exists                           → 409
  * default PR title from investigation.yaml ``title:`` else ``Workstream: <b>``
  * ``investigation:`` prefix heuristic (diff touches ``investigations/``)
  * ``gh`` CLI not installed (with manual compare URL) → 500
  * ``gh pr create`` non-zero                     → 500
  * happy path → ``{ok, pr_url, pr_number}``      → 200
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

from vivarium_dashboard.lib import report
from vivarium_dashboard.lib import work_state
from vivarium_dashboard.lib.workspace_paths import WorkspacePaths


def _default_pr_title(ws_root: Path, branch_name: str) -> str:
    """Prefer the matching investigation's ``title:`` field, else legacy default.

    Reads ``investigations/<branch_name>/investigation.yaml`` (via
    ``WorkspacePaths.load(ws_root)``) and returns its ``title:`` when present;
    falls back to ``f"Workstream: {branch_name}"`` otherwise.
    """
    inv_yaml = WorkspacePaths.load(ws_root).investigations / branch_name / "investigation.yaml"
    if inv_yaml.is_file():
        try:
            inv_spec = yaml.safe_load(inv_yaml.read_text(encoding="utf-8")) or {}
            inv_title = (inv_spec.get("title") or "").strip()
            if inv_title:
                return inv_title
        except Exception:
            pass
    return f"Workstream: {branch_name}"


def work_create_pr(ws_root: Path, body: dict | None) -> tuple[dict, int]:
    """POST /api/work-create-pr — create a GitHub PR for the active workstream.

    Port of ``_post_work_create_pr`` (``cwd=ws_root``):

      * no active workstream  → ``({"error": "no active workstream"}, 409)``
      * not yet pushed        → ``({"error": <long UI message>}, 409)``
      * PR already exists      → ``({"error": "PR already exists: <url>", "pr_url"}, 409)``
      * gh not installed       → ``({"error": "gh CLI not installed. Open manually:", "manual_url"}, 500)``
      * gh pr create failure   → ``({"error": "gh pr create failed: <err[:300]>"}, 500)``
      * happy path             → ``({"ok": True, "pr_url", "pr_number"}, 200)``
    """
    body = body or {}
    # Replicate _ws_add_to_sys_path(): make the workspace's own package importable.
    ws = str(ws_root)
    if ws not in sys.path:
        sys.path.insert(0, ws)

    state = work_state.load_state_or_adopt_current()
    branch = state.get("active_branch")
    if not branch:
        return {"error": "no active workstream"}, 409
    # Opportunistic: if local matches origin/<branch>, mark pushed automatically.
    if not state.get("pushed"):
        check = subprocess.run(
            ["git", "rev-list", "--left-right", "--count", f"origin/{branch}...HEAD"],
            cwd=ws_root, capture_output=True, text=True,
        )
        if check.returncode == 0:
            parts = (check.stdout or "").strip().split()
            if len(parts) == 2 and parts[1] == "0":
                state["pushed"] = True
                work_state.save_state(state)
    if not state.get("pushed"):
        # mem3dg-readdy friction #35: the old error said "click the Push
        # button" but that button only renders when the branch has an
        # upstream AND is ahead of it. For a never-pushed branch the
        # workstream strip shows "Link branch to upstream" instead, and
        # the user ended up stuck. Spell out BOTH UI paths plus the
        # terminal fallback so the user has an actionable next step
        # regardless of branch state.
        return {
            "error": (
                "branch not yet pushed. Use the workstream strip at "
                "the top of the dashboard — click `Link branch to "
                "upstream` (if shown) to create the remote and push, "
                "or `Push` (if the branch already has an upstream). "
                "Terminal fallback: `git push -u origin <branch>`; "
                "the dashboard picks it up on the next refresh."
            ),
        }, 409
    if state.get("pr_url"):
        return {"error": f"PR already exists: {state['pr_url']}", "pr_url": state["pr_url"]}, 409

    base = state.get("base") or "main"
    # PR title default: prefer the matching investigation's `title:`
    # field (from investigations/<branch>/investigation.yaml) so the
    # PR reads like "PDMP whole-cell model reformulation" rather than
    # the technical "Workstream: <branch>". Branch and investigation
    # slug are kept in 1:1 correspondence by the Investigation ≡
    # branch convention, so we look up by branch name. Falls back
    # to the legacy "Workstream: <branch>" when no matching
    # investigation.yaml is present (e.g., generic feature branches).
    title = (body.get("title") or "").strip() or _default_pr_title(ws_root, branch)
    body_text = (body.get("body") or "").strip() or "Created via pbg-template dashboard."

    # Investigation PR convention: if the branch touches anything under
    # investigations/ AND the title isn't already prefixed, prepend
    # `investigation: `. Investigation PRs are living integration
    # branches — not merge targets — so they need to be visually
    # distinguishable in the PR list. Combined with the `draft=True`
    # default below, this enforces the convention end-to-end without
    # asking the user to remember it.
    if not title.lower().startswith("investigation:"):
        try:
            _diff = subprocess.run(
                ["git", "diff", "--name-only", f"{base}...{branch}"],
                cwd=ws_root, capture_output=True, text=True, timeout=10,
            )
            if _diff.returncode == 0 and any(
                line.startswith("investigations/") for line in _diff.stdout.splitlines()
            ):
                title = f"investigation: {title}"
        except Exception:  # noqa: BLE001 — heuristic is best-effort
            pass

    if not shutil.which("gh"):
        repo = report._detect_github_repo(ws_root)
        manual = f"https://github.com/{repo}/compare/{base}...{branch}?expand=1" if repo else None
        return {
            "error": "gh CLI not installed. Open manually:",
            "manual_url": manual,
        }, 500

    draft = bool(body.get("draft", True))
    cmd = ["gh", "pr", "create", "--base", base, "--head", branch,
           "--title", title, "--body", body_text]
    if draft:
        cmd.append("--draft")
    r = subprocess.run(cmd, cwd=ws_root, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        return {"error": f"gh pr create failed: {(r.stderr or r.stdout)[:300]}"}, 500
    pr_url = r.stdout.strip().splitlines()[-1] if r.stdout else ""
    m = re.search(r"/pull/(\d+)", pr_url)
    if m:
        state["pr_url"] = pr_url
        state["pr_number"] = int(m.group(1))
        work_state.save_state(state)
    return {"ok": True, "pr_url": pr_url, "pr_number": state.get("pr_number")}, 200


def default_upstream_repo(ws_root: Path) -> str:
    """Auto-detect upstream repo from workspace.yaml or external/v2ecoli/.git/config.

    Behaviour-preserving extraction of the stdlib instance method
    ``server.Handler._default_upstream_repo`` (``WORKSPACE`` → ``ws_root``):

      * ``ws_root/workspace.yaml`` ``upstream_repo:`` when set,
      * else ``ws_root/external/v2ecoli`` git ``remote get-url origin`` parsed
        through ``github\\.com[:/]([\\w.-]+/[\\w.-]+?)(?:\\.git)?$``,
      * else the ``vivarium-collective/v2ecoli`` fallback.

    ``subprocess`` is referenced module-level so tests monkeypatch
    ``work_pr_views.subprocess.run`` and never touch real git.
    """
    ws_path = ws_root / "workspace.yaml"
    if ws_path.exists():
        try:
            ws_data = yaml.safe_load(ws_path.read_text(encoding="utf-8")) or {}
            ur = (ws_data.get("upstream_repo") or "").strip()
            if ur:
                return ur
        except yaml.YAMLError:
            pass
    # Try external/v2ecoli's origin.
    external = ws_root / "external" / "v2ecoli"
    if external.is_dir():
        r = subprocess.run(["git", "remote", "get-url", "origin"],
                           cwd=external, capture_output=True, text=True)
        if r.returncode == 0:
            url = r.stdout.strip()
            # https://github.com/owner/name.git or git@github.com:owner/name.git
            m = re.search(r"github\.com[:/]([\w.-]+/[\w.-]+?)(?:\.git)?$", url)
            if m:
                return m.group(1)
    return "vivarium-collective/v2ecoli"
