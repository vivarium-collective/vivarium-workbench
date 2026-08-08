"""Materialize a remote sms-api simulator build into a local workspace cache.

A build is a repo@commit; SP1's GET /api/v1/simulations/workspace streams it as
a gzipped tarball (GitHub's repo tarball). We download it once, extract it,
strip GitHub's single top-level `<org>-<repo>-<sha>/` dir, and cache it by
commit (immutable → reusable). The dashboard then re-points (SP2) to the cache
dir and serves the build as a full local workspace.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import warnings
from pathlib import Path
from typing import Any

from vivarium_workbench.lib.sms_api_client import SmsApiError

# A git commit ref (the only non-server-controlled value that flows into a
# filesystem path) must be plain hex. This closes the one allow-list gap in the
# build-switch path: a malicious/compromised sms-api can't smuggle `../` (path
# traversal) or an empty ref into cache_dir_for. Raising SmsApiError routes
# through the handler's existing 502 path (active workspace left unchanged).
_COMMIT_RE = re.compile(r"\A[0-9a-fA-F]{4,40}\Z")

# Session keys are server-minted (`session_registry.mint_key()`,
# `secrets.token_urlsafe(32)`) — already a filesystem-safe charset — but this
# is the second (and last) externally-influenced value that flows into a
# build-cache path, so it gets the same allow-list treatment as `_COMMIT_RE`.
_SESSION_KEY_RE = re.compile(r"\A[A-Za-z0-9_-]{8,128}\Z")

# A real workspace tarball is ~50MB and takes minutes over the SSM tunnel
# (measured ~224s); the client's 30s default would hard-fail the switch.
_DOWNLOAD_TIMEOUT_S = 600.0


def _stamp_build_meta(cache: Path, simulator_id: int, commit: str) -> None:
    """Mark a materialized cache as a remote build so the Simulations DB merges
    the deployment's runs (lib/remote_simulations.py reads this). No-clobber:
    switch-build writes a richer stamp (repo/branch/repo_url); never overwrite it."""
    meta = cache / ".viv-build.json"
    if meta.exists():
        return
    try:
        meta.write_text(json.dumps({"simulator_id": simulator_id, "commit": commit}), encoding="utf-8")
    except OSError:
        pass  # provenance stamp is best-effort, never block materialize


def build_cache_root() -> Path:
    """Root dir for materialized build workspaces.

    Overridable via ``VIVARIUM_WORKBENCH_BUILD_CACHE`` (``env_compat``'s
    deprecated-alias-aware lookup) — tests point this at ``tmp_path``; the
    Stanford/Stanford-test K8s deployments point it at ``/root/.pbg/build-cache``,
    a SECOND mount (``subPath: build-cache``) of the SAME ``workbench-workspace``
    EBS PVC already mounted at ``/workspace`` (see ``kustomize/base/workbench/
    workbench.yaml`` in the ``viva-api`` repo — this repo's own kustomize manifests
    were split out there; see ``deploy/README.md``). That mount is what makes this
    default path durable in prod: even with the env var unset, ``Path.home() /
    ".pbg" / "build-cache"`` IS ``/root/.pbg/build-cache`` (the container runs as
    uid 0), so the default and the deployed mount point deliberately coincide —
    belt-and-suspenders, not a coincidence to preserve carefully.

    This was the original intent, never fully carried out: docs/REFACTOR-PLAN.md
    §2B.3 already scoped the workbench's PVC to cover "workspace (git/YAML/SQLite)
    + caches (venv, ParCa ~175 MB, `~/.pbg/build-cache`)" — only the workspace half
    shipped. Backlog item 21's residual (this cache surviving a legitimate pod
    restart/image bump/OOM-kill, not just the unrelated-deploy blast-radius PR #227
    already fixed) is a deployment-manifest change, not a code change — check the
    live Deployment's ``volumeMounts`` (``kubectl get deploy workbench -o
    jsonpath='{.spec.template.spec.volumes}'``, the same check #227's own
    kustomization.yaml comment used) before assuming this path is durable on any
    given deployment; this function has no way to know from inside the process.
    """
    from vivarium_workbench.lib.env_compat import get_env
    env = get_env("BUILD_CACHE")
    return Path(env) if env else Path.home() / ".pbg" / "build-cache"


def cache_dir_for(simulator_id: int, commit: str) -> Path:
    return build_cache_root() / f"sim{simulator_id}-{commit}"


def _safe_commit(commit: str) -> str:
    if not commit or not _COMMIT_RE.match(commit):
        raise SmsApiError(f"refusing unsafe/empty commit ref from sms-api: {commit!r}")
    return commit


def _safe_session_key(session_key: str) -> str:
    if not session_key or not _SESSION_KEY_RE.match(session_key):
        raise SmsApiError(f"refusing unsafe/empty session key: {session_key!r}")
    return session_key


def session_cache_dir_for(session_key: str, simulator_id: int, commit: str) -> Path:
    """Where a given session's OWN writable clone of a build lives.

    Distinct from ``cache_dir_for`` (the shared immutable base) — see
    ``materialize_session_build``'s docstring for why the two must never be
    the same directory.
    """
    session_key = _safe_session_key(session_key)
    return build_cache_root() / "sessions" / session_key / f"sim{simulator_id}-{commit}"


def materialize_build(client: Any, simulator_id: int, commit: str, *, force: bool = False) -> Path:
    """Return a local workspace dir for the build, downloading+extracting once.

    Reuses the per-commit cache dir if present (immutable repo@commit). Extracts
    under the cache root (same filesystem) then os.replace()s into place, so a
    partial download never leaves a half-written cache.
    """
    commit = _safe_commit(commit)
    cache = cache_dir_for(simulator_id, commit)
    if cache.exists() and not force:
        _stamp_build_meta(cache, simulator_id, commit)
        return cache

    root = build_cache_root()
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".staging-sim{simulator_id}-", dir=root))
    try:
        tar_path = client.download_workspace(simulator_id, staging, timeout=_DOWNLOAD_TIMEOUT_S)
        extract_root = staging / "extract"
        extract_root.mkdir()
        with tarfile.open(tar_path, "r:gz") as tar:
            tar.extractall(extract_root, filter="data")  # noqa: S202 — trusted internal artifact

        # GitHub wraps everything in one top-level dir; lift it so the cache dir
        # is the workspace root. Fall back to the extract root if the shape is
        # unexpected (not exactly one top-level dir).
        entries = [p for p in extract_root.iterdir() if not p.name.startswith(".")]
        src = entries[0] if len(entries) == 1 and entries[0].is_dir() else extract_root

        if cache.exists():
            shutil.rmtree(cache)
        os.replace(str(src), str(cache))  # same-filesystem atomic move
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    _stamp_build_meta(cache, simulator_id, commit)
    return cache


def materialize_session_build(
    client: Any, session_key: str, simulator_id: int, commit: str, *, force: bool = False
) -> Path:
    """Return a workspace dir EXCLUSIVE to ``session_key`` for this build.

    ``materialize_build`` caches by ``(simulator_id, commit)`` only, on purpose
    — the download+extract is expensive (~224s over the SSM tunnel) and the raw
    tarball is immutable, so sharing that fetch across every session bound to
    the same build is correct. The bug was everything downstream of that: a
    session's own writes — ``ensure_git_workspace``'s real ``.git`` init/commit,
    and every study-authoring write a bound session makes afterwards — landed
    in that SAME shared directory. Two sessions (two tabs, or two different
    people) bound to the same build read and wrote one physical directory with
    no isolation.

    The fix keeps the shared immutable fetch exactly as-is (still reused, still
    cached by commit) and adds one more, per-session copy on top of it: this
    session's own real, independent clone at ``session_cache_dir_for``. A real
    ``shutil.copytree`` — NOT a hardlink — because a hardlink shares the
    underlying inode on most filesystems (ext4/xfs without reflink); writing
    to a hardlinked file mutates the SAME data for every other link, which
    would silently reintroduce the exact bug this fixes. The extra copy time
    is real but small next to the network fetch it avoids repeating, and
    correctness here matters more than the copy's cost.

    Idempotent per session (a session re-switching to the same build reuses
    its own existing clone, same as ``materialize_build``'s reuse-by-commit).

    KNOWN FOLLOW-UP (not this function's job): nothing ever deletes a session's
    clone once ``session_registry`` forgets that session (drop / in-memory-only
    state lost on restart) — every switch is a net-new directory under
    ``sessions/<key>/`` that outlives the binding that created it. On the old
    ephemeral cache this was harmless (a pod restart wiped it anyway, a crude
    accidental GC); once ``build_cache_root()`` is durable (see its docstring)
    that accidental GC is gone, so orphaned per-session clones now accumulate
    for real. Not fixed here — a real eviction policy (age? last-access? tied to
    ``session_registry.drop``?) is its own scoped decision, not an improvised
    TTL bolted on as a side effect of the storage-location fix. Worth watching
    against the PVC's 20Gi request if usage grows.
    """
    session_key = _safe_session_key(session_key)
    commit = _safe_commit(commit)
    session_dir = session_cache_dir_for(session_key, simulator_id, commit)
    if session_dir.exists() and not force:
        return session_dir

    base = materialize_build(client, simulator_id, commit, force=force)

    root = build_cache_root()
    staging = Path(tempfile.mkdtemp(prefix=f".staging-session-sim{simulator_id}-", dir=root))
    try:
        clone = staging / "clone"
        # symlinks=True: recreate symlinks as symlinks instead of dereferencing
        # them into copies of their target's content. Two reasons, not one:
        # (1) git itself tracks a symlink as a symlink, so dereferencing would
        # silently diverge the clone from what `ensure_git_workspace`'s `git
        # add -A` sees as clean; (2) a workspace can contain symlinks whose
        # target is missing (e.g. an authoring bug in the source repo) —
        # dereferencing tries to open the target and raises shutil.Error,
        # aborting the ENTIRE session switch over one unrelated broken link
        # elsewhere in the tree. Preserving the symlink as-is faithfully
        # mirrors the source (dangling or not) without crashing.
        shutil.copytree(base, clone, symlinks=True)
        session_dir.parent.mkdir(parents=True, exist_ok=True)
        if session_dir.exists():
            shutil.rmtree(session_dir)
        os.replace(str(clone), str(session_dir))  # same-filesystem atomic move
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return session_dir


def ensure_git_workspace(cache_dir: Path, repo_url: str, branch: str, commit: str, simulator_id: int) -> None:
    """Turn a tarball-materialized build into a real, push-ready git working copy.

    ``materialize_build`` extracts a GitHub tarball on purpose — no ``.git``, so
    the cache stays a plain immutable snapshot shared across sessions/commits.
    But the SAME cache dir is what a session binds to via switch-build, and
    every write path a session can take afterwards (study authoring commits,
    the non-pinned "Run on remote" push-based dispatch in
    ``lib.remote_run_views``) assumes a git working copy with an ``origin``
    remote — without this, dispatch fails with "no GitHub remote configured"
    for every switched build, every time (found live trying to dispatch a
    pilot run against a switched sms-ecoli build).

    Idempotent (no-ops if ``.git`` already exists) and best-effort (a failure
    here must never fail the switch itself — the build stays browsable even if
    git-readiness setup fails; dispatch will just keep 409ing as before). Runs
    on every switch regardless — cheap when ``.git`` already exists (one stat),
    and it must self-heal in the deployments/paths where ``build_cache_root()``
    is NOT durable (local/dev, or a K8s deployment that hasn't wired the PVC
    mount documented on ``build_cache_root()``): a pod restart wiping an
    ephemeral cache re-materializes from scratch, and this re-establishes
    ``.git`` on the fresh copy exactly as it would on a cold cache. Where the
    mount IS wired (Stanford/Stanford-test), this simply no-ops after the first
    run, same as any other idempotent setup step.

    Commits under a NEW local branch (``workbench/sim<id>-<commit>``), never
    the upstream ``branch`` itself, so a later push can never collide with or
    fast-forward over real history on that ref.
    """
    if not repo_url or (cache_dir / ".git").exists():
        return
    try:
        subprocess.run(["git", "init", "-q"], cwd=cache_dir, check=True, capture_output=True)
        subprocess.run(["git", "remote", "add", "origin", repo_url], cwd=cache_dir, check=True, capture_output=True)
        subprocess.run(["git", "add", "-A"], cwd=cache_dir, check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.email=pbg-template@local", "-c", "user.name=pbg-template",
             "commit", "-q", "--allow-empty", "-m", f"Materialized build baseline: {branch}@{commit[:12]}"],
            cwd=cache_dir, check=True, capture_output=True,
        )
        local_branch = f"workbench/sim{simulator_id}-{commit[:12]}"
        subprocess.run(["git", "checkout", "-q", "-b", local_branch], cwd=cache_dir, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or e.stdout or b"")
        detail = detail.decode() if isinstance(detail, bytes) else detail
        warnings.warn(f"ensure_git_workspace: git setup failed for {cache_dir}: {detail[:300]}")
        shutil.rmtree(cache_dir / ".git", ignore_errors=True)  # don't leave a half-initialized .git behind


def list_build_sources(client: Any) -> dict:
    """Map sms-api's simulator versions to dropdown build entries.

    Best-effort: returns {"builds": [], "error": <str>} when sms-api is
    unreachable so the dropdown degrades to Local-only.
    """
    try:
        data = client.list_simulators()
    except SmsApiError as e:
        return {"builds": [], "error": str(e)}
    builds = []
    for v in data.get("versions", []) or []:
        sim_id = v.get("database_id")
        commit = v.get("git_commit_hash", "")
        repo = (v.get("git_repo_url", "") or "").rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
        builds.append({
            "simulator_id": sim_id,
            "repo": repo,
            "repo_url": v.get("git_repo_url", ""),
            "commit": commit,
            "branch": v.get("git_branch", ""),
            "created_at": v.get("created_at", ""),
            "label": f"{repo} @ {commit} (build #{sim_id})",
        })
    return {"builds": builds, "error": None}
