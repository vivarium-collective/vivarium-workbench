"""Workspace git helpers for a remote run, plus the deployment-run dispatcher.

Provides:
- ``git_pip_url(ws_root)`` — validates the git working tree is clean + pushed
  and returns a ``git+<origin>@<sha>`` pip-installable URL.
- ``remote_dispatch_preflight(ws_root)`` — the same guards, as a structured, non-raising answer.
- ``workspace_pinned_deps(ws_root)`` — the workspace's framework pins, as pip specs.
- ``run_remote(ws_root, composite_id, ...)`` — the detached runner's deployment run: the backend
  ``backends.select`` picks (else the ``unrouted`` one) runs the composite and returns its results
  archive (``RunFacet.run_composite``, step P2 Runs b; the SMS run itself is the plugin's
  ``deployment_run``).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

# Default poll interval in seconds
_DEFAULT_POLL_INTERVAL = 10.0
# Hardening (external users): bound the status-poll loop so a stuck remote run can't
# hang the poller forever. A backend may wait longer for a long run (compose-api:
# remote_run_compose.wait_timeout adds time per step).
_DEFAULT_POLL_TIMEOUT = 7200.0  # 2 h wall-clock base; <= 0 disables the deadline


class RemoteRunStopped(RuntimeError):
    """A deployment run that stopped before it completed (cancelled, timed out, out of memory, failed) and kept its
    results up to its last snapshot: ``archive`` is them, in the shape a completed run's archive has, and ``status``
    is the backend's word for the stop. The runner lands the archive and records the run as stopped."""

    def __init__(self, message: str, *, status: str, archive: Path) -> None:
        super().__init__(message)
        self.status = status
        self.archive = archive


# Paths the workbench itself (re)writes whenever it serves a workspace or runs something in it — the `.viv-build.json`
# provenance stamp (#858), the rendered report shell (`<reports>/index.html`, `<reports>/assets/`), its per-start
# registry/server state, and its run records and per-session state (the run registry, each study's/investigation's
# `runs.db` and its SQLite sidecars, the composite-state and loom caches, the chat audit log). They are generated
# output, not workspace code, and a remote dispatch installs only the code from git: counting them would make a clean,
# pushed workspace look dirty the moment `vivarium-workbench serve` has run, and again after every run it dispatched.
# Committed *results* under the reports dir (e.g. a per-model report) and any source or study definition are NOT
# excluded. The reports and `.pbg` dirs are resolved through the workspace's `layout:` (a template-scaffolded
# workspace keeps its reports under `workspace/reports/`), never assumed to sit at the root.
_GENERATED_IN_REPORTS = ("index.html", "assets")
_GENERATED_IN_PBG = ("registry-catalog", "server", "runs.jsonl", "composite-state-cache", "loom-layouts",
                     "ai-actions.jsonl")


def _not_workspace_code(ws_root: Path) -> "tuple[str, ...]":
    """Git exclude pathspecs for the workbench's own generated output in ``ws_root`` (see the note above)."""
    from vivarium_workbench.lib.workspace_paths import WorkspacePaths

    specs = [":!.viv-build.json", ":(exclude,glob)**/runs.db", ":(exclude,glob)**/runs.db-*"]
    try:
        wp = WorkspacePaths.load(ws_root)
    except Exception:  # noqa: BLE001 — an unparseable workspace.yaml: the default layout, as before layouts were read
        wp = WorkspacePaths.from_config(ws_root, {})
    root = ws_root.resolve()
    for d, names in ((wp.reports, _GENERATED_IN_REPORTS), (wp.pbg, _GENERATED_IN_PBG)):
        try:
            rel = d.resolve().relative_to(root).as_posix()
        except ValueError:                      # laid out outside the repo: git never reports it here
            continue
        # `literal`: a layout value is a path, never a pattern (`rep*` must not also hide `repX/index.html`)
        specs += [f":(exclude,literal){name}" if rel == "." else f":(exclude,literal){rel}/{name}" for name in names]
    return tuple(specs)


def git_pip_url(ws_root: "Path | str") -> str:
    """Return ``git+<origin>@<sha>`` for the workspace repo.

    Guards
    ------
    - Raises :exc:`RuntimeError` if the working tree has uncommitted changes
      or untracked files (``git status --porcelain`` is non-empty).
    - Raises :exc:`RuntimeError` if HEAD is not present on any configured
      remote (i.e. there are unpushed commits).

    Returns
    -------
    str
        A pip-installable VCS URL, e.g.
        ``"git+https://github.com/org/ws.git@abc1234"``.
    """
    ws_root = Path(ws_root).resolve()

    # --- dirty tree check ---
    # Exclude the `.viv-build.json` provenance stamp: it's bookkeeping the
    # workbench (re)writes on every build-switch, and a stale/regenerated stamp
    # must never block a remote dispatch (#858). The primary fix stamps it
    # before the baseline commit so it's normally clean anyway; this is
    # defense-in-depth for caches materialized by an older workbench.
    status = _git(ws_root, "status", "--porcelain", "--", ".", *_not_workspace_code(ws_root))
    if status.strip():
        raise RuntimeError(
            f"Workspace at {ws_root} has uncommitted or untracked changes "
            f"(git status --porcelain returned output).\n"
            f"Please commit and push all changes before running remotely.\n"
            f"Dirty files:\n{status}"
        )

    # --- get HEAD sha ---
    sha = _git(ws_root, "rev-parse", "HEAD").strip()

    # --- unpushed check: HEAD must be reachable from at least one remote ref ---
    # `git branch -r --contains <sha>` lists remotes that contain the commit.
    remote_refs = _git(ws_root, "branch", "-r", "--contains", sha).strip()
    if not remote_refs:
        raise RuntimeError(
            f"HEAD commit {sha[:8]} is not pushed to any remote branch.\n"
            f"Please push before running remotely."
        )

    # --- get origin URL ---
    try:
        origin_url = _git(ws_root, "remote", "get-url", "origin").strip()
    except RuntimeError:
        # Fall back to listing all remotes and taking the first
        remotes = _git(ws_root, "remote").strip().split()
        if not remotes:
            raise RuntimeError(
                f"Workspace at {ws_root} has no git remotes configured."
            )
        origin_url = _git(ws_root, "remote", "get-url", remotes[0]).strip()

    # Normalise: file:// absolute paths are valid pip VCS URLs
    return f"git+{origin_url}@{sha}"


def remote_dispatch_preflight(ws_root: "Path | str") -> dict:
    """Non-raising, structured counterpart to :func:`git_pip_url`'s guards.

    A remote dispatch installs the workspace FROM GIT on the deployment, so the
    tree must be clean AND HEAD must be pushed. Instead of letting the detached
    run hit ``git_pip_url``'s ``RuntimeError`` (which surfaces as a raw traceback
    in the run log), callers use this to check UP FRONT and return a clean,
    actionable message — so the Run button warns + tells the user exactly what to
    fix rather than dumping a stack trace.

    Returns ``{ok, reason, message, sha, dirty_files, remote}`` where
    ``reason`` ∈ {``"ok"``, ``"dirty"``, ``"unpushed"``, ``"no-remote"``,
    ``"error"``}. Never raises."""
    try:
        ws_root = Path(ws_root).resolve()
        # Same exclusion as git_pip_url: the .viv-build.json stamp is workbench
        # bookkeeping and must not block a dispatch (#858).
        dirty = _git(ws_root, "status", "--porcelain", "--", ".", *_not_workspace_code(ws_root)).strip()
        sha = _git(ws_root, "rev-parse", "HEAD").strip()
    except Exception as e:  # noqa: BLE001 — a git failure is a clean preflight fail, not a crash
        return {"ok": False, "reason": "error", "sha": "", "dirty_files": "",
                "remote": "", "message": f"Could not read the workspace's git state: {e}"}

    if dirty:
        return {"ok": False, "reason": "dirty", "sha": sha, "dirty_files": dirty, "remote": "",
                "message": "This workspace has uncommitted changes — a remote run installs the "
                           "workspace from git, so commit and push them first "
                           "(Branches tab → GitHub → Commit + Push), then run remotely."}

    try:
        remote_refs = _git(ws_root, "branch", "-r", "--contains", sha).strip()
    except Exception:  # noqa: BLE001
        remote_refs = ""
    if not remote_refs:
        return {"ok": False, "reason": "unpushed", "sha": sha, "dirty_files": "", "remote": "",
                "message": f"Workspace commit {sha[:8]} isn't pushed to any remote branch — a remote "
                           "run installs the workspace from git, so push it first "
                           "(Branches tab → GitHub → Commit + Push), then run remotely."}

    try:
        origin = _git(ws_root, "remote", "get-url", "origin").strip()
    except Exception:  # noqa: BLE001
        try:
            remotes = _git(ws_root, "remote").strip().split()
            origin = _git(ws_root, "remote", "get-url", remotes[0]).strip() if remotes else ""
        except Exception:  # noqa: BLE001
            origin = ""
    if not origin:
        return {"ok": False, "reason": "no-remote", "sha": sha, "dirty_files": "", "remote": "",
                "message": "This workspace has no git remote configured, so a remote build can't "
                           "install it — add a remote and push before running remotely."}

    return {"ok": True, "reason": "ok", "sha": sha, "dirty_files": "", "remote": origin,
            "message": "Workspace is clean and pushed — ready to run remotely."}


def run_remote(
    ws_root: "Path | str",
    composite_id: str,
    *,
    poll_interval: float = _DEFAULT_POLL_INTERVAL,
    dest: "Path | None" = None,
    n_steps: float = 1,
    overrides: "dict | None" = None,
    poll_timeout: float = _DEFAULT_POLL_TIMEOUT,
    analysis_options: "dict | None" = None,
    build_ref: "dict | None" = None,
    emit_paths: "list[str] | None" = None,
) -> Path:
    """Run a workspace composite on the deployment and return its results archive in *dest* (default
    ``<ws_root>/.pbg/remote-results/``): a ``.zip`` of emitter history, or a ``.tar.gz`` the runner folds.

    The backend is the one :func:`backends.select` picks for ``ws_root`` (a provider's claim -- the session's own
    build or the deployment's pin -- else the named backend); a deployment run that none claims (the opt-in compose
    path) goes to the backend that answers unrouted requests (:func:`backends.unrouted`). With neither, a
    ``RuntimeError``. A run that stopped but kept results raises :class:`RemoteRunStopped` with them.
    ``analysis_options`` and ``build_ref`` are carried to the backend unread (``RunFacet.run_composite``)."""
    from vivarium_workbench.lib import backends

    ws_root = Path(ws_root).resolve()
    if dest is None:
        dest = ws_root / ".pbg" / "remote-results"
    chosen = backends.select(ws_root)
    facet: "backends.RunFacet | backends.UnroutedFacet | None" = chosen.runs if chosen is not None else None
    if facet is None:
        ub = backends.unrouted()  # a deployment run no backend claimed (step P2, Runs a1's facet)
        facet = ub.unrouted if ub is not None else None
    if facet is None:
        raise RuntimeError("this run targets a deployment, but no remote backend is named (serve --backend-base-url) "
                           "or provided by an installed backend plugin")
    return facet.run_composite(ws_root, composite_id, dest=Path(dest), n_steps=n_steps, overrides=overrides,
                               emit_paths=emit_paths, analysis_options=analysis_options, build_ref=build_ref,
                               poll_interval=poll_interval, poll_timeout=poll_timeout)


# The process-bigraph framework packages a workspace pins in its own uv.lock. We
# forward these pins to the remote runner image (§3.12) so it doesn't float to the
# latest PyPI release at container-build time and silently mismatch the workspace.
_PINNED_FRAMEWORK_PKGS = ("process-bigraph", "bigraph-schema", "viva-emitters", "pbg-superpowers")


def workspace_pinned_deps(ws_root: "Path | str") -> list[str]:
    """Pip specs for the workspace's own framework pins, read from its ``uv.lock``.

    Best-effort + defensive: returns ``[]`` if the lockfile is absent/unparseable
    or a package isn't pinned (the runner image then floats, as it does today).
    A git-sourced pin becomes ``name @ git+<url>@<sha>``; a PyPI pin becomes
    ``name==version``. Threaded into ``extra_pip_deps`` via the existing mechanism —
    no new pinning concept, no sms-api change.
    """
    try:
        import tomllib

        lock_path = Path(ws_root) / "uv.lock"
        if not lock_path.is_file():
            return []
        data = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    except Exception:
        return []

    deps: list[str] = []
    for pkg in data.get("package", []):
        name = pkg.get("name")
        if name not in _PINNED_FRAMEWORK_PKGS:
            continue
        source = pkg.get("source") or {}
        git = source.get("git")
        if git:
            # uv encodes the resolved commit as a ``#<sha>`` fragment on the git URL.
            url, _, frag = str(git).partition("#")
            sha = frag or ""
            url = url.split("?")[0]  # strip ?branch=… — the sha is authoritative
            deps.append(f"{name} @ git+{url}@{sha}" if sha else f"{name} @ git+{url}")
        elif pkg.get("version"):
            deps.append(f"{name}=={pkg['version']}")
    return deps


def _git(cwd: Path, *args: str) -> str:
    """Run a git command in *cwd*, return stdout. Raises RuntimeError on failure."""
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed (rc={result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout
