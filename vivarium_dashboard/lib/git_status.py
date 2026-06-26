"""Git/branch status helpers extracted from server.py for the FastAPI seam.

These are the ``ws_root``-parameterized public builders for the 6 git-related
read-only routes.  The legacy server.py module-level helpers (``_has_origin_remote``,
``_stale_branch_threshold``, ``_commits_behind``, ``_dirty_workspace``) now
delegate to the corresponding functions here, keeping their existing call-sites
intact.

Builders
--------
build_git_status     → GET /api/git-status
build_work_status    → GET /api/work-status
build_branch_staleness → GET /api/branch-staleness
build_dirty_status   → GET /api/dirty-status
list_branches        → GET /api/branches
build_branch_diff    → GET /api/branch-diff
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path


# ---------------------------------------------------------------------------
# Low-level helpers (parameterised on ws_root)
# ---------------------------------------------------------------------------

def is_generated_path(path: str) -> bool:
    """True if ``path`` is a generated report file, artifact dir, or dashboard state.

    Mirrors ``server._is_generated_path`` — see that function for the rationale.
    """
    return (
        path.startswith("reports/")
        or path.startswith("out/") or path == "out/"
        or path.startswith(".pbg/") or path == ".pbg/"
    )


def submodule_paths(ws_root: Path) -> set[str]:
    """Return the set of registered submodule paths from .gitmodules.

    Mirrors ``server._submodule_paths`` but parameterised on ``ws_root`` instead
    of the module-level ``WORKSPACE`` global.
    """
    gm = ws_root / ".gitmodules"
    if not gm.exists():
        return set()
    paths: set[str] = set()
    for line in gm.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("path"):
            _, _, val = line.partition("=")
            val = val.strip()
            if val:
                paths.add(val)
    return paths


def has_origin_remote(ws_root: Path) -> bool:
    """True if a git remote named ``'origin'`` is configured.

    Mirrors ``server._has_origin_remote`` parameterised on ``ws_root``.
    """
    r = subprocess.run(
        ["git", "remote"],
        cwd=ws_root, capture_output=True, text=True, check=False,
    )
    return "origin" in (r.stdout or "").split()


def diagnose_push_error(err: str) -> dict | None:
    """Return a structured diagnosis for known push failure patterns, else None.

    Verbatim copy of the pure ``server._diagnose_push_error`` (server keeps its
    own copy; dedup at the flip). Used by ``lib.work_mutations.work_push`` to
    attach a clickable diagnosis to a ``git push`` failure response.
    """
    if not err:
        return None
    if "does not appear to be a git repository" in err or "Could not read from remote repository" in err:
        return {
            "category": "no_origin",
            "summary": "Push failed because no GitHub remote is configured.",
            "suggestion": "Click `Create GitHub repo` in the workstream strip to create one and push in one step.",
        }
    if "Permission to" in err and "denied" in err:
        return {
            "category": "auth",
            "summary": "Push denied — your git credential doesn't have write access.",
            "suggestion": "Run `gh auth login` (or check your SSH key / token) and try again.",
        }
    if "rejected" in err and ("non-fast-forward" in err or "behind" in err):
        return {
            "category": "behind",
            "summary": "Remote has commits your local branch doesn't.",
            "suggestion": "Pull/rebase first: `git pull --rebase origin <branch>`, then push.",
        }
    return None


def remote_repo_url(ws_root: Path) -> str | None:
    """Return origin's normalized remote URL, or ``None`` when unresolved.

    Mirrors ``server._remote_repo_url`` parameterised on ``ws_root``: runs
    ``git remote get-url origin`` in ``cwd=ws_root``, returns ``None`` on a
    non-zero exit or empty URL, else the URL normalized via
    :func:`lib.source_build_views._normalize_repo_url` (reused, not re-copied —
    server keeps its own ``_normalize_repo_url``; dedup at the flip).
    """
    from vivarium_dashboard.lib.source_build_views import _normalize_repo_url

    r = subprocess.run(
        ["git", "remote", "get-url", "origin"], cwd=ws_root,
        capture_output=True, text=True, timeout=5,
    )
    if r.returncode != 0:
        return None
    raw = r.stdout.strip()
    return _normalize_repo_url(raw) if raw else None


def remote_push_and_sha(ws_root: Path) -> str:
    """Push the workspace's current branch to origin with the GH token, return HEAD SHA.

    Mirrors ``server._remote_push_and_sha`` parameterised on ``ws_root``:
    resolves the current branch (raises if detached/unnamed), pushes
    ``-u origin <branch>`` with ``os.environ | github_auth.current_token_env()``
    (raises with the stderr/stdout ``[-300:]`` tail on failure), then resolves
    and returns the HEAD SHA (raising if empty).
    """
    from vivarium_dashboard.lib import github_auth

    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=ws_root,
        capture_output=True, text=True, timeout=5,
    ).stdout.strip()
    if not branch or branch == "HEAD":
        raise RuntimeError("workspace is not on a named branch")
    env = os.environ | github_auth.current_token_env()
    push = subprocess.run(
        ["git", "push", "-u", "origin", branch], cwd=ws_root,
        capture_output=True, text=True, timeout=120, env=env,
    )
    if push.returncode != 0:
        raise RuntimeError(f"git push failed: {(push.stderr or push.stdout)[-300:]}")
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ws_root, capture_output=True, text=True, timeout=5,
    ).stdout.strip()
    if not sha:
        raise RuntimeError("could not resolve HEAD commit")
    return sha


def stale_branch_threshold() -> int:
    """Commits-behind-main threshold above which a branch is flagged stale.

    Default 20. Override per-server with ``PBG_STALE_BRANCH_THRESHOLD=<int>``.
    Mirrors ``server._stale_branch_threshold`` (no ws_root needed — env-only).
    """
    raw = os.environ.get("PBG_STALE_BRANCH_THRESHOLD")
    if raw:
        try:
            n = int(raw)
            return max(n, 1)
        except ValueError:
            pass
    return 20


def commits_behind(ws_root: Path, branch: str, base: str = "main") -> tuple[int, str]:
    """Return ``(commits_behind, ref_used)``.

    Probes ``origin/<base>`` first (matches what a ``git merge origin/<base>``
    would have to fast-forward over). Falls back to local ``<base>``.
    Returns ``(0, "")`` on any git failure.

    Mirrors ``server._commits_behind`` parameterised on ``ws_root``.
    """
    for ref in (f"origin/{base}", base):
        r = subprocess.run(
            ["git", "rev-list", "--count", f"{branch}..{ref}"],
            cwd=ws_root, capture_output=True, text=True, check=False,
        )
        if r.returncode == 0:
            try:
                return int(r.stdout.strip() or 0), ref
            except ValueError:
                pass
    return 0, ""


class NotAGitRepo(RuntimeError):
    """Raised by :func:`remote_commit_and_push` when *ws_root* is not a git work tree.

    Mirrors ``server._NotAGitRepo`` — the FastAPI route maps this to HTTP 409.
    """


def remote_commit_and_push(ws_root: Path, message: str) -> dict:
    """Stage+commit *ws_root* changes (skip if clean), push current branch, return result.

    Verbatim port of ``server._remote_commit_and_push`` parameterised on
    ``ws_root`` (same ``git -C str(ws_root)`` command form): probes
    ``rev-parse --is-inside-work-tree`` (raises :class:`NotAGitRepo` on a
    non-zero exit or a non-``"true"`` stdout), ``git add -A``, reads the
    porcelain status, commits with ``message or "dashboard commit"`` when dirty
    (raising with the ``[-300:]`` stderr/stdout tail on failure), then resolves
    the pushed SHA via :func:`remote_push_and_sha` and returns
    ``{"ok", "pushed", "commit", "branch"}``.
    """
    inside = subprocess.run(
        ["git", "-C", str(ws_root), "rev-parse", "--is-inside-work-tree"],
        capture_output=True, text=True,
    )
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        raise NotAGitRepo("active source is not a git workspace (no commit/push)")
    subprocess.run(["git", "-C", str(ws_root), "add", "-A"], capture_output=True, text=True, timeout=30)
    status = subprocess.run(
        ["git", "-C", str(ws_root), "status", "--porcelain"], capture_output=True, text=True, timeout=10,
    ).stdout.strip()
    if status:
        c = subprocess.run(
            ["git", "-C", str(ws_root), "commit", "-m", message or "dashboard commit"],
            capture_output=True, text=True, timeout=30,
        )
        if c.returncode != 0:
            raise RuntimeError(f"git commit failed: {(c.stderr or c.stdout)[-300:]}")
    sha = remote_push_and_sha(ws_root)
    return {"ok": True, "pushed": bool(status), "commit": sha,
            "branch": subprocess.run(["git", "-C", str(ws_root), "rev-parse", "--abbrev-ref", "HEAD"],
                                     capture_output=True, text=True).stdout.strip()}


def suggest_dirty_commit_message(paths: list[str]) -> str:
    """Auto-generate a conventional commit message from a list of dirty paths.

    Uses the top-level directory of each path to pick a category prefix. When all
    dirty files share one top-level directory we map it to a conventional scope
    (chore(scripts), docs, chore(composites), ...). Otherwise falls back to a
    generic ``chore:`` prefix.

    Verbatim copy of the pure ``server._suggest_dirty_commit_message``.
    """
    if not paths:
        return "chore: commit pending files"
    top_dirs = sorted(set(p.split('/')[0] for p in paths if p))
    n = len(paths)
    suffix = f"commit {n} pending file{'s' if n != 1 else ''}"
    if len(top_dirs) == 1:
        cat = top_dirs[0]
        # Map common top-level dirs to conventional categories
        known = {
            'scripts': 'chore(scripts)',
            'composites': 'chore(composites)',
            'investigations': 'chore(investigations)',
            'docs': 'docs',
            'tests': 'chore(tests)',
            'reports': 'chore(reports)',
            'pbg_chromosome_rep1': 'chore(pkg)',  # workspace package
        }
        # Generic fallback
        prefix = known.get(cat, f'chore({cat})')
        return f"{prefix}: {suffix}"
    return f"chore: {suffix}"


def dirty_workspace(ws_root: Path) -> str:
    """Return the porcelain status excluding generated reports + submodule pointers.

    Raises ``subprocess.CalledProcessError`` when ``git status`` itself fails.
    Mirrors ``server._dirty_workspace`` parameterised on ``ws_root``.
    """
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ws_root, capture_output=True, text=True, check=True,
    ).stdout
    smods = submodule_paths(ws_root)
    kept = []
    for raw in status.splitlines():
        if len(raw) < 4:
            continue
        path = raw[3:]
        if is_generated_path(path):
            continue
        if path in smods:
            continue
        kept.append(raw)
    return "\n".join(kept)


# ---------------------------------------------------------------------------
# Work-state helper (reads .pbg/state.json directly, ws_root-aware)
# ---------------------------------------------------------------------------

def _load_work_state(ws_root: Path) -> dict:
    """Read the workstream state.json for *ws_root*, returning {} on any failure.

    Resolves the ``.pbg`` directory via ``WorkspacePaths`` so a custom
    ``layout:`` in workspace.yaml is honoured — byte-identical to
    ``lib.work_state.load_state`` (which reads
    ``WorkspacePaths.load(workspace_root()).pbg / "state.json"``).
    """
    from vivarium_dashboard.lib.workspace_paths import WorkspacePaths

    state_path = WorkspacePaths.load(ws_root).pbg / "state.json"
    if not state_path.exists():
        return {}
    try:
        return json.loads(state_path.read_text(encoding="utf-8")) or {}
    except (json.JSONDecodeError, OSError):
        return {}


# ---------------------------------------------------------------------------
# Public builders
# ---------------------------------------------------------------------------

def build_git_status(ws_root: Path) -> dict:
    """Build the GET /api/git-status payload for *ws_root*.

    Returns a flat dict with keys: ``upstream_repo``, ``branch``,
    ``push_state``, ``ahead``, ``behind``, ``branch_url``, ``repo_url``,
    ``pr_number``, ``pr_url``, ``base``, ``ahead_of_base``, ``dirty_count``,
    ``compare_url``, ``pr_state``, ``gh_available``, ``has_active_workstream``.

    Always returns a 200 dict (never raises). Mirrors ``server._get_git_status``.
    """
    result: dict = {
        "upstream_repo": None, "branch": None, "push_state": "no_origin",
        "ahead": 0, "behind": 0,
        "branch_url": None, "repo_url": None,
        "pr_number": None, "pr_url": None,
        "base": "main", "ahead_of_base": 0,
        "dirty_count": 0, "compare_url": None, "pr_state": None,
        "gh_available": bool(shutil.which("gh")),
        "has_active_workstream": False,
    }
    # current branch
    r = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=ws_root, capture_output=True, text=True,
    )
    if r.returncode != 0:
        return result
    result["branch"] = (r.stdout or "").strip()
    # upstream repo (from origin remote)
    r = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=ws_root, capture_output=True, text=True,
    )
    if r.returncode != 0:
        return result
    origin_url = (r.stdout or "").strip()
    m = re.search(r"github\.com[:/]([\w.-]+/[\w.-]+?)(?:\.git)?$", origin_url)
    if m:
        result["upstream_repo"] = m.group(1)
        result["repo_url"] = f"https://github.com/{m.group(1)}"
        result["branch_url"] = (
            f"https://github.com/{m.group(1)}/tree/{result['branch']}"
        )
    # ahead/behind vs origin/<branch>
    ref = f"origin/{result['branch']}"
    r = subprocess.run(
        ["git", "rev-list", "--left-right", "--count", f"{ref}...HEAD"],
        cwd=ws_root, capture_output=True, text=True,
    )
    if r.returncode != 0:
        result["push_state"] = "no_origin"
    else:
        parts = (r.stdout or "").strip().split()
        if len(parts) == 2:
            behind = int(parts[0])
            ahead = int(parts[1])
            result["ahead"] = ahead
            result["behind"] = behind
            if ahead == 0 and behind == 0:
                result["push_state"] = "pushed"
            elif ahead > 0 and behind == 0:
                result["push_state"] = "ahead"
            elif ahead == 0 and behind > 0:
                result["push_state"] = "behind"
            else:
                result["push_state"] = "diverged"
    # PR info + base — read from .pbg/state.json (cheaper than gh API)
    try:
        state = _load_work_state(ws_root)
        result["pr_url"] = state.get("pr_url")
        result["pr_number"] = state.get("pr_number")
        result["base"] = state.get("base") or "main"
        result["has_active_workstream"] = bool(state.get("active_branch"))
    except Exception:
        pass
    # ahead_of_base: commits on branch not yet merged into base
    base: str = result["base"]
    branch: str | None = result["branch"]
    if branch:
        for base_ref in (base, f"origin/{base}"):
            r_aob = subprocess.run(
                ["git", "rev-list", "--count", f"{base_ref}..HEAD"],
                cwd=ws_root, capture_output=True, text=True,
            )
            if r_aob.returncode == 0:
                try:
                    result["ahead_of_base"] = int(r_aob.stdout.strip())
                except ValueError:
                    pass
                break
        if result["upstream_repo"]:
            result["compare_url"] = (
                f"https://github.com/{result['upstream_repo']}"
                f"/compare/{base}...{branch}"
            )
    # dirty_count: number of uncommitted files (filtered, same as dirty-status)
    try:
        dirty_output = dirty_workspace(ws_root)
        result["dirty_count"] = len(
            [ln for ln in dirty_output.splitlines() if len(ln) >= 4]
        )
    except Exception:
        pass
    # pr_state: query gh if a PR number is known
    if result.get("pr_number"):
        try:
            r_pr = subprocess.run(
                [
                    "gh", "pr", "view", str(result["pr_number"]),
                    "--json", "state", "--jq", ".state",
                ],
                cwd=ws_root, capture_output=True, text=True, timeout=5,
            )
            if r_pr.returncode == 0:
                result["pr_state"] = r_pr.stdout.strip() or None
        except Exception:
            pass
    return result


def build_work_status(ws_root: Path) -> dict:
    """Build the GET /api/work-status payload for *ws_root*.

    Returns ``{active: False}`` when no workstream is active, or a dict with
    ``active``, ``branch``, ``base``, ``commits_ahead``, ``commits_behind``,
    ``behind_ref``, ``stale``, ``stale_threshold``, ``unpushed``, ``pushed``,
    ``has_origin``, ``gh_available``, ``pr_number``, ``pr_url`` otherwise.

    Always returns a 200 dict. Mirrors ``server._get_work_status``.
    """
    state = _load_work_state(ws_root)
    if not state.get("active_branch"):
        return {"active": False}
    branch: str = state["active_branch"]
    base: str = state.get("base", "main")

    # commits ahead of base
    r = subprocess.run(
        ["git", "rev-list", "--count", f"{base}..{branch}"],
        cwd=ws_root, capture_output=True, text=True,
    )
    commits_ahead = int(r.stdout.strip() or 0) if r.returncode == 0 else 0

    # commits behind base (friction #5: long-running branches drift)
    cb, behind_ref = commits_behind(ws_root, branch, base)
    stale_threshold = stale_branch_threshold()

    # unpushed commits
    if state.get("pushed"):
        r2 = subprocess.run(
            ["git", "rev-list", "--count", f"origin/{branch}..{branch}"],
            cwd=ws_root, capture_output=True, text=True,
        )
        unpushed = int(r2.stdout.strip() or 0) if r2.returncode == 0 else commits_ahead
    else:
        unpushed = commits_ahead

    return {
        "active": True,
        "branch": branch,
        "base": base,
        "commits_ahead": commits_ahead,
        "commits_behind": cb,
        "behind_ref": behind_ref,
        "stale": cb >= stale_threshold,
        "stale_threshold": stale_threshold,
        "unpushed": unpushed,
        "pushed": state.get("pushed", False),
        "has_origin": has_origin_remote(ws_root),
        "gh_available": shutil.which("gh") is not None,
        "pr_number": state.get("pr_number"),
        "pr_url": state.get("pr_url"),
    }


class NoBranchError(ValueError):
    """Raised by build_branch_staleness when no branch can be determined."""


def build_branch_staleness(
    ws_root: Path,
    branch: str | None = None,
    base: str = "main",
) -> dict:
    """Build the GET /api/branch-staleness payload for *ws_root*.

    Probes ``origin/<base>`` first, falls back to local ``<base>``.

    Raises ``NoBranchError`` (a ``ValueError`` subclass) when ``branch`` is
    ``None`` AND the workspace's current HEAD cannot be determined — the
    FastAPI route maps this to HTTP 400.

    Mirrors ``server._get_branch_staleness``.
    """
    if not branch:
        r = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=ws_root, capture_output=True, text=True,
        )
        branch = r.stdout.strip() if r.returncode == 0 else ""
    if not branch:
        raise NoBranchError("could not determine current branch + no ?branch= given")

    cb, behind_ref = commits_behind(ws_root, branch, base)
    threshold = stale_branch_threshold()
    return {
        "branch": branch,
        "base": base,
        "behind_ref": behind_ref,
        "commits_behind": cb,
        "stale_threshold": threshold,
        "stale": cb >= threshold,
    }


def build_dirty_status(ws_root: Path) -> dict:
    """Build the GET /api/dirty-status payload for *ws_root*.

    Returns ``{count, files: [{status, path}]}``.

    Raises ``subprocess.CalledProcessError`` when ``git status`` fails —
    the FastAPI route maps this to HTTP 500.

    Mirrors ``server._get_dirty_status``.
    """
    dirty = dirty_workspace(ws_root)  # may raise CalledProcessError
    files = []
    for raw in dirty.splitlines():
        if len(raw) < 4:
            continue
        files.append({"status": raw[:2].strip(), "path": raw[3:]})
    return {"count": len(files), "files": files}


def list_branches(ws_root: Path) -> dict:
    """Build the GET /api/branches payload for *ws_root*.

    Returns ``{branches: [{name, last_commit: {sha, subject, date}, ahead_of_main}], current}``.

    Never raises — errors per-branch are swallowed (branch entry gets empty
    last_commit). A top-level git failure returns ``{error: "..."}``.

    Mirrors ``server._serve_branches``.
    """
    try:
        raw = subprocess.run(
            ["git", "branch", "--list", "stage/*"],
            cwd=ws_root, capture_output=True, text=True, check=True,
        ).stdout
        stage_branches = [
            b.strip().lstrip("* ")
            for b in raw.splitlines()
            if b.strip()
        ]

        current = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=ws_root, capture_output=True, text=True, check=True,
        ).stdout.strip()

        branches = []
        for bname in stage_branches:
            try:
                log = subprocess.run(
                    ["git", "log", "-1", "--format=%H|%s|%ci", bname],
                    cwd=ws_root, capture_output=True, text=True, check=True,
                ).stdout.strip()
                parts = log.split("|", 2)
                sha = parts[0] if parts else ""
                subject = parts[1] if len(parts) > 1 else ""
                date_str = parts[2] if len(parts) > 2 else ""

                ahead_raw = subprocess.run(
                    ["git", "rev-list", "--count", f"main..{bname}"],
                    cwd=ws_root, capture_output=True, text=True,
                ).stdout.strip()
                ahead = int(ahead_raw) if ahead_raw.isdigit() else 0

                branches.append({
                    "name": bname,
                    "last_commit": {
                        "sha": sha[:7],
                        "subject": subject,
                        "date": date_str,
                    },
                    "ahead_of_main": ahead,
                })
            except Exception:
                branches.append({"name": bname, "last_commit": {}, "ahead_of_main": 0})

        return {"branches": branches, "current": current}
    except Exception as e:
        return {"error": str(e)}


def build_branch_diff(ws_root: Path, branch: str) -> dict:
    """Build the GET /api/branch-diff payload for *ws_root*.

    Returns ``{branch, log, diff_stat}``. Validates *branch* against a safe
    pattern; raises ``ValueError`` on an invalid name.

    Mirrors ``server._get_branch_diff``.
    """
    if not branch or not re.match(r"^[A-Za-z0-9./_-]+$", branch) or ".." in branch:
        raise ValueError(f"invalid branch name: {branch!r}")
    log = subprocess.run(
        ["git", "log", "--oneline", f"main..{branch}"],
        cwd=ws_root, capture_output=True, text=True, check=False,
    )
    diff_stat = subprocess.run(
        ["git", "diff", "--stat", f"main...{branch}"],
        cwd=ws_root, capture_output=True, text=True, check=False,
    )
    return {
        "branch": branch,
        "log": log.stdout,
        "diff_stat": diff_stat.stdout,
    }
