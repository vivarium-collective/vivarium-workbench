"""Scaffold a new workspace from ``pbg-template`` (todo #8 Phase C).

Backs ``POST /api/workspaces/create``. The scaffold pipeline is split into
small testable helpers so the route handler can validate inputs, call
:func:`create_workspace`, and then spawn the child dashboard separately
(reusing the same spawn-and-poll logic from ``_post_workspaces_start``).

Failure semantics: any exception after the target directory exists triggers
best-effort ``rmtree`` of the target so the user doesn't end up with a
half-written workspace they need to clean up by hand.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock

log = logging.getLogger(__name__)

# Concurrent /api/workspaces/create requests would race on directory creation
# and on the shared ``~/vivarium/workspaces/`` parent. Serialise them.
_CREATE_LOCK = Lock()

# Same slug rule used by the client-side validator (workspace-switcher.js).
_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?$|^[a-z0-9]$")

ALLOWED_BACKENDS: tuple[str, ...] = ("local", "hpc:ccam")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class WorkspaceCreateError(Exception):
    """Structured error surfaced to the route handler.

    ``code`` maps to an HTTP status; ``detail`` is JSON-serialisable extra
    context (captured stderr, normalised inputs, etc.).
    """
    def __init__(self, code: int, message: str, *, detail: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}


# ---------------------------------------------------------------------------
# Input validation / normalisation
# ---------------------------------------------------------------------------


def validate_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise WorkspaceCreateError(400, "workspace name required")
    if not _SLUG_RE.match(name):
        raise WorkspaceCreateError(
            400,
            "invalid workspace name",
            detail={"hint": "lowercase letters/digits/_/-, must start+end with alphanumeric"},
        )
    return name


def validate_backend(backend: str) -> str:
    backend = (backend or "").strip()
    if backend not in ALLOWED_BACKENDS:
        raise WorkspaceCreateError(
            400, f"unknown backend {backend!r}",
            detail={"allowed": list(ALLOWED_BACKENDS)},
        )
    return backend


def normalise_org(raw: str | None) -> str | None:
    """Accept bare ``<org>`` or ``https://github.com/<org>(/)?``; return bare
    org. None / empty / whitespace-only → None."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    s = re.sub(r"^https?://github\.com/", "", s, flags=re.IGNORECASE)
    s = s.rstrip("/")
    if "/" in s:
        s = s.split("/", 1)[0]
    if not re.match(r"^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?$", s):
        raise WorkspaceCreateError(
            400, f"could not parse GitHub org from {raw!r}",
        )
    return s


# ---------------------------------------------------------------------------
# Template resolution
# ---------------------------------------------------------------------------


_PBG_TEMPLATE_CACHE_REFRESH_H = 24
_PBG_TEMPLATE_REPO = "https://github.com/vivarium-collective/pbg-template.git"
_PBG_TEMPLATE_REF = "dynamic-workspace-images"


def _clone_or_refresh_cache(
    cache_root: Path, clone_dir: Path, template_dir: Path,
) -> None:
    """Ensure ``clone_dir`` has a fresh clone of pbg-template.

    If the clone doesn't exist, ``git clone --depth 1 --branch <ref>`` it.
    If it exists and is older than ``_PBG_TEMPLATE_CACHE_REFRESH_H`` hours,
    ``git fetch --depth 1`` + ``git reset --hard origin/<ref>``.
    On failure (no git available, network error), the stale clone is kept if
    its ``template/template-init.sh`` exists; otherwise a new failure is
    surfaced via the cache-miss path the caller handles.
    """
    now = datetime.now(timezone.utc)
    _fresh_enough = (
        _PBG_TEMPLATE_CACHE_REFRESH_H is not None
        and (clone_dir / ".git").is_dir()
        and clone_dir / "template" / "template-init.sh" in clone_dir.rglob("*")
        and now - datetime.fromtimestamp(
            (clone_dir / ".git").stat().st_mtime, tz=timezone.utc
        ) < timedelta(hours=_PBG_TEMPLATE_CACHE_REFRESH_H)
    )

    if _fresh_enough:
        return

    if not shutil.which("git"):
        return  # caller will fall through to the error path

    cache_root.mkdir(parents=True, exist_ok=True)

    if (clone_dir / ".git").is_dir():
        # Refresh: fetch + hard-reset to the target ref.
        try:
            subprocess.run(
                ["git", "fetch", "--depth", "1", "origin",
                 f"refs/heads/{_PBG_TEMPLATE_REF}"],
                cwd=str(clone_dir), capture_output=True, text=True,
                timeout=30, check=True,
            )
            subprocess.run(
                ["git", "reset", "--hard", f"origin/{_PBG_TEMPLATE_REF}"],
                cwd=str(clone_dir), capture_output=True, text=True,
                timeout=30, check=True,
            )
        except Exception:
            pass  # keep stale clone if template-init.sh exists
    else:
        # Fresh clone.
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", "--branch", _PBG_TEMPLATE_REF,
                 _PBG_TEMPLATE_REPO, str(clone_dir)],
                capture_output=True, text=True, timeout=60, check=True,
            )
        except Exception:
            pass  # caller will raise WorkspaceCreateError


def find_pbg_template() -> Path:
    """Locate the ``pbg-template/template/`` source directory.

    Resolution order:

    1. ``$PBG_TEMPLATE_PATH`` env var (developer override; expected to point
       at the ``template/`` sub-directory directly).
    2. Sibling checkout at ``<dashboard-repo>/../pbg-template/template/``
       (the typical dev layout — same source used by the existing
       ``template-init.sh`` integration).
    3. ``$HOME/.cache/vivarium-dashboard/pbg-template/template/`` — populated
       by a future ``git clone`` fallback (not yet implemented; raise a
       structured error so the operator knows how to fix it).

    Raises ``WorkspaceCreateError`` (500) if none of the candidates exist.
    """
    env_override = os.environ.get("PBG_TEMPLATE_PATH", "").strip()
    if env_override:
        p = Path(env_override).expanduser()
        if (p / "template-init.sh").is_file():
            return p
        if (p / "template" / "template-init.sh").is_file():
            return p / "template"

    # Sibling — resolved relative to *this* module's package root.
    here = Path(__file__).resolve()
    repo_root = here.parents[2]  # vivarium-dashboard/
    sibling = repo_root.parent / "pbg-template" / "template"
    if (sibling / "template-init.sh").is_file():
        return sibling

    # Cache clone — git clone vivarium-collective/pbg-template into
    # ~/.cache/vivarium-dashboard/pbg-template/, refreshed if older than 24h.
    cache_root = Path.home() / ".cache" / "vivarium-dashboard"
    cache_clone = cache_root / "pbg-template"
    cache_template = cache_clone / "template"
    _clone_or_refresh_cache(cache_root, cache_clone, cache_template)
    if (cache_template / "template-init.sh").is_file():
        return cache_template

    raise WorkspaceCreateError(
        500, "pbg-template not found",
        detail={
            "hint": ("set PBG_TEMPLATE_PATH to a local pbg-template checkout, "
                     "or clone vivarium-collective/pbg-template alongside "
                     "vivarium-dashboard"),
            "tried": [env_override or None, str(sibling),
                      str(cache_template)],
        },
    )


# ---------------------------------------------------------------------------
# Workspace scaffold
# ---------------------------------------------------------------------------


@dataclass
class CreateResult:
    path: Path
    workspace_yaml: Path
    backend: str
    github_org: str | None
    remote_url: str | None
    branch: str

    def as_dict(self) -> dict:
        return {
            "path": str(self.path),
            "workspace_yaml": str(self.workspace_yaml),
            "backend": self.backend,
            "github_org": self.github_org,
            "remote_url": self.remote_url,
            "branch": self.branch,
        }


def _utc_timestamp_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _copy_template(template_src: Path, target: Path) -> None:
    """Copy ``template_src`` → ``target``, excluding any ``.git`` directory.

    Uses :func:`shutil.copytree` with an ignore filter. Refuses if ``target``
    already exists (caller has already enforced this, but defensive).
    """
    if target.exists():
        raise WorkspaceCreateError(
            409, f"target directory exists: {target}",
            detail={"hint": "pick another name or remove the directory first"},
        )

    def _ignore(_src: str, names: list[str]) -> set[str]:
        return {n for n in names if n == ".git"}

    shutil.copytree(template_src, target, ignore=_ignore)


def _run_template_init(target: Path, name: str, *, dashboard_path: Path | None) -> None:
    """Invoke ``template-init.sh`` in ``target``, piping the workspace name
    on stdin (the script's interactive prompt accepts a single line).

    Raises ``WorkspaceCreateError`` if the script exits non-zero.
    """
    script = target / "template-init.sh"
    if not script.is_file():
        raise WorkspaceCreateError(
            500, "template-init.sh missing from template",
            detail={"path": str(script)},
        )
    env = os.environ.copy()
    if dashboard_path is not None:
        env["VIVARIUM_DASHBOARD_PATH"] = str(dashboard_path)
    try:
        r = subprocess.run(
            ["bash", str(script)],
            cwd=str(target),
            input=(name + "\n").encode(),
            capture_output=True, env=env, timeout=60,
        )
    except subprocess.TimeoutExpired as e:
        raise WorkspaceCreateError(
            500, "template-init.sh timed out",
            detail={"timeout_seconds": e.timeout},
        ) from None
    if r.returncode != 0:
        raise WorkspaceCreateError(
            500, "template-init.sh failed",
            detail={
                "returncode": r.returncode,
                "stderr": r.stderr.decode(errors="replace")[-2000:],
                "stdout": r.stdout.decode(errors="replace")[-2000:],
            },
        )


def _persist_compute_backend(workspace_yaml: Path, backend: str) -> None:
    """Append/overwrite the top-level ``compute_backend`` key in workspace.yaml.

    PyYAML round-trip would normalise the file's formatting and comments. To
    avoid that, append a plain ``compute_backend: <name>`` line if missing,
    or rewrite that single line in place. This stays close to the existing
    YAML edit conventions used elsewhere in the codebase (e.g.
    ``lib/workspace_yaml.py``).
    """
    if not workspace_yaml.is_file():
        raise WorkspaceCreateError(
            500, "workspace.yaml missing after scaffold",
            detail={"path": str(workspace_yaml)},
        )
    text = workspace_yaml.read_text()
    line = f"compute_backend: {backend}\n"
    pattern = re.compile(r"^compute_backend:.*$", re.MULTILINE)
    if pattern.search(text):
        text = pattern.sub(line.rstrip(), text)
    else:
        if not text.endswith("\n"):
            text += "\n"
        text += "\n" + line
    workspace_yaml.write_text(text)


def _persist_github_org(workspace_yaml: Path, github_org: str | None) -> None:
    """Append/overwrite the top-level ``github_org`` key in workspace.yaml.

    Same line-edit pattern as :func:`_persist_compute_backend`. No-op when
    ``github_org`` is ``None`` or empty.
    """
    if not github_org:
        return
    if not workspace_yaml.is_file():
        raise WorkspaceCreateError(
            500, "workspace.yaml missing after scaffold",
            detail={"path": str(workspace_yaml)},
        )
    text = workspace_yaml.read_text()
    line = f"github_org: {github_org}\n"
    pattern = re.compile(r"^github_org:.*$", re.MULTILINE)
    if pattern.search(text):
        text = pattern.sub(line.rstrip(), text)
    else:
        if not text.endswith("\n"):
            text += "\n"
        text += "\n" + line
    workspace_yaml.write_text(text)


def _maybe_remove_singularity(target: Path, backend: str) -> None:
    """Delete ``Singularity.def`` when backend is not HPC.

    Phase E ships ``Singularity.def.j2`` in pbg-template so HPC workspaces
    carry the file by default; until that ships, this function is a no-op
    for HPC backends (the file simply isn't there yet — flagged by the
    caller via :func:`_check_singularity_for_hpc`).
    """
    if backend.startswith("hpc:"):
        return
    f = target / "Singularity.def"
    if f.is_file():
        f.unlink()


def _check_singularity_for_hpc(target: Path, backend: str) -> str | None:
    """If backend is HPC and Singularity.def is missing, return a warning
    string describing the deferred-to-Phase-E gap. Otherwise return None."""
    if not backend.startswith("hpc:"):
        return None
    if (target / "Singularity.def").is_file():
        return None
    return (
        "Singularity.def not present in the scaffolded workspace — the "
        "pbg-template checkout may predate the Singularity.def.j2 template. "
        "compute_backend metadata is set; add a Singularity.def manually or "
        "re-scaffold against a newer pbg-template to populate it."
    )


def _git(target: Path, args: list[str], *, env: dict | None = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run ``git <args>`` inside ``target``. ``check=False`` returns the
    completed process even on failure (caller inspects ``returncode``)."""
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    return subprocess.run(
        ["git", *args], cwd=str(target),
        capture_output=True, text=True, env=full_env,
        timeout=30, check=check,
    )


def _ensure_venv(target: Path) -> None:
    """Create a virtual environment in the workspace if one doesn't exist.

    Uses ``uv venv`` (much faster) with a fallback to ``python3 -m venv``.
    The venv is needed for the Registry Install flow, which checks for
    ``.venv/bin/pip`` or ``.venv/bin/python3`` + ``uv`` on PATH.

    Raises ``WorkspaceCreateError`` if neither tool can create the venv.
    """
    venv_dir = target / ".venv"
    if venv_dir.is_dir():
        return  # already exists — nothing to do

    if shutil.which("uv"):
        try:
            subprocess.run(
                ["uv", "venv", ".venv"],
                cwd=str(target), capture_output=True, timeout=60, check=True,
            )
            return
        except Exception:
            pass  # fall through to python3 -m venv

    python = shutil.which("python3") or shutil.which("python")
    if python:
        try:
            subprocess.run(
                [python, "-m", "venv", ".venv"],
                cwd=str(target), capture_output=True, timeout=120, check=True,
            )
            return
        except Exception as e:
            raise WorkspaceCreateError(
                500, "failed to create virtual environment",
                detail={"error": str(e)[-500:]},
            ) from None

    raise WorkspaceCreateError(
        500, "cannot create virtual environment — neither uv nor python3 found on PATH",
        detail={"hint": "Install Python 3.10+ or uv, then retry."},
    )


def _git_init_first_commit(target: Path, name: str, *, env: dict | None = None) -> None:
    """Init git in ``target`` on branch ``main`` and create a scaffold commit.

    Uses ``-b main`` to skip the default-branch dance. Falls back to the
    older flow (``init`` + ``checkout -b``) on git <2.28.
    """
    try:
        _git(target, ["init", "-b", "main"], env=env)
    except subprocess.CalledProcessError:
        # Old git (<2.28): no -b flag.
        _git(target, ["init"], env=env)
        _git(target, ["checkout", "-b", "main"], env=env, check=False)

    # Identity for the scaffold commit. Don't write to the user's git config;
    # use env so the commit signature is owned by them if their config is set,
    # and falls back to a generic identity otherwise.
    commit_env = (env or {}).copy()
    commit_env.setdefault("GIT_AUTHOR_NAME", os.environ.get("GIT_AUTHOR_NAME") or "vivarium-dashboard")
    commit_env.setdefault("GIT_AUTHOR_EMAIL", os.environ.get("GIT_AUTHOR_EMAIL") or "vivarium-dashboard@localhost")
    commit_env.setdefault("GIT_COMMITTER_NAME", commit_env["GIT_AUTHOR_NAME"])
    commit_env.setdefault("GIT_COMMITTER_EMAIL", commit_env["GIT_AUTHOR_EMAIL"])

    _git(target, ["add", "-A"], env=commit_env)
    # ``-c commit.gpgsign=false`` because we don't want to error on a missing
    # GPG agent inside the dashboard subprocess. The user's interactive
    # commits via the UI still pick up their global signing settings.
    _git(target,
         ["-c", "commit.gpgsign=false", "commit", "-m", f"scaffold workspace {name} from pbg-template"],
         env=commit_env)


def _gh_repo_view(org: str, name: str, *, env: dict) -> bool:
    """Return True if ``<org>/<name>`` already exists on GitHub."""
    r = subprocess.run(
        ["gh", "repo", "view", f"{org}/{name}"],
        capture_output=True, text=True, env=env, timeout=15,
    )
    return r.returncode == 0


def _attach_or_create_remote(
    target: Path, name: str, org: str, *, env: dict,
) -> tuple[str, str]:
    """Either create a brand-new ``<org>/<name>`` GitHub repo or attach to
    an existing one. Always leaves the workspace checked out on a fresh
    ``scaffold/<utc-ts>`` branch that has been pushed with ``-u origin``,
    so the first dashboard-driven commit lands cleanly on a workstream
    branch (and ``main`` stays clean as the integration branch).

    Returns ``(remote_url, branch)`` where ``branch`` is always the
    scaffold branch.
    """
    remote_url = f"https://github.com/{org}/{name}.git"
    branch = f"scaffold/{_utc_timestamp_slug()}"

    if _gh_repo_view(org, name, env=env):
        # Existing repo: add it as origin, fetch, branch off the local
        # scaffold commit, and push set-upstream.
        _git(target, ["remote", "add", "origin", remote_url], env=env)
        _git(target, ["fetch", "origin"], env=env, check=False)
        _git(target, ["checkout", "-b", branch], env=env)
        push = _git(target, ["push", "-u", "origin", branch], env=env, check=False)
        if push.returncode != 0:
            raise WorkspaceCreateError(
                502, "git push failed",
                detail={"stderr": push.stderr[-2000:], "stdout": push.stdout[-2000:]},
            )
        return remote_url, branch

    # Fresh repo: gh repo create pushes the current branch (``main``) as
    # the default branch. Then create + push the workstream branch on top
    # so the user lands ready to commit immediately.
    r = subprocess.run(
        ["gh", "repo", "create", f"{org}/{name}",
         "--public", "--source=.", "--push"],
        cwd=str(target), capture_output=True, text=True, env=env, timeout=60,
    )
    if r.returncode != 0:
        # 403: org-restricted OAuth app. Surface with the canonical hint.
        if "third-party" in r.stderr.lower() or "saml" in r.stderr.lower():
            raise WorkspaceCreateError(
                403, "GitHub org rejected the OAuth app",
                detail={
                    "stderr": r.stderr[-2000:],
                    "hint": f"ask the {org} org admin to approve Vivarium Dashboard at "
                            f"https://github.com/orgs/{org}/policies/applications",
                },
            )
        raise WorkspaceCreateError(
            502, "gh repo create failed",
            detail={"stderr": r.stderr[-2000:], "stdout": r.stdout[-2000:]},
        )
    # Now branch off main onto scaffold/<ts> and push.
    _git(target, ["checkout", "-b", branch], env=env)
    push = _git(target, ["push", "-u", "origin", branch], env=env, check=False)
    if push.returncode != 0:
        raise WorkspaceCreateError(
            502, "git push of scaffold branch failed",
            detail={"stderr": push.stderr[-2000:], "stdout": push.stdout[-2000:]},
        )
    return remote_url, branch


def _write_active_workstream(target: Path, branch: str) -> None:
    """Pre-set ``.pbg/state.json`` so the workspace's dashboard treats
    ``branch`` as the active workstream from the very first request.

    Mirrors the shape produced by :func:`work_state.load_state_or_adopt_current`.
    Without this, the user would have to click "Start workstream" before
    the first Install button works (todo #8 Phase D).
    """
    import json as _json
    state_dir = target / ".pbg"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "state.json").write_text(_json.dumps({
        "active_branch": branch,
        "base": "main",
        "pushed": True,
        "pr_number": None,
        "pr_url": None,
        "adopted": False,
    }, indent=2) + "\n")


def create_workspace(
    name: str,
    backend: str,
    *,
    github_org: str | None = None,
    target_root: Path | None = None,
    template_source: Path | None = None,
    dashboard_path: Path | None = None,
    gh_env: dict[str, str] | None = None,
) -> CreateResult:
    """Scaffold a new workspace end-to-end.

    Caller has already validated ``name`` / ``backend`` / ``github_org`` via
    the helpers above — this function assumes they're in canonical form.

    On success returns a :class:`CreateResult`. On failure raises
    :class:`WorkspaceCreateError`; the partially-created directory is
    cleaned up before the exception propagates.
    """
    if target_root is None:
        target_root = Path.home() / "vivarium" / "workspaces"
    if template_source is None:
        template_source = find_pbg_template()
    if dashboard_path is None:
        # Pin to the local checkout so the scaffolded workspace's pyproject
        # uses the in-development dashboard via uv.sources path mode (matches
        # the existing template-init.sh fallback chain — see todo #1 progress
        # log for the rationale).
        here = Path(__file__).resolve()
        cand = here.parents[2]  # vivarium-dashboard/
        if (cand / "pyproject.toml").is_file():
            dashboard_path = cand

    target_root.mkdir(parents=True, exist_ok=True)
    target = target_root / name

    with _CREATE_LOCK:
        if target.exists():
            raise WorkspaceCreateError(
                409, f"workspace directory already exists: {target}",
                detail={"hint": "pick another name or remove the directory first"},
            )

        # Steps after target creation get rmtree cleanup on any failure.
        target_created = False
        try:
            _copy_template(template_source, target)
            target_created = True

            _run_template_init(target, name, dashboard_path=dashboard_path)

            # Create .venv so the Registry Install flow finds pip/python3.
            _ensure_venv(target)

            workspace_yaml = target / "workspace.yaml"
            _persist_compute_backend(workspace_yaml, backend)
            _persist_github_org(workspace_yaml, github_org)
            _maybe_remove_singularity(target, backend)

            _git_init_first_commit(target, name)

            remote_url: str | None = None
            branch = "main"
            if github_org:
                env = os.environ.copy()
                if gh_env:
                    env.update(gh_env)
                remote_url, branch = _attach_or_create_remote(
                    target, name, github_org, env=env,
                )
                # Phase D: pre-set the active workstream to the scaffold
                # branch so the first Install in the new workspace commits
                # without the user clicking "Start workstream" first.
                _write_active_workstream(target, branch)

            # Catalog the workspace so it appears in /api/workspaces switchers.
            try:
                from pbg_superpowers import workspace_catalog
                workspace_catalog.add(target)
            except Exception as e:
                log.warning("workspace_catalog.add failed (non-fatal): %s", e)

            return CreateResult(
                path=target,
                workspace_yaml=workspace_yaml,
                backend=backend,
                github_org=github_org,
                remote_url=remote_url,
                branch=branch,
            )
        except Exception:
            if target_created:
                try:
                    shutil.rmtree(target, ignore_errors=True)
                except Exception:
                    pass
            raise
