"""`RepoSource` — phase-1 staging (`docs/materialization-lifecycle.md` §2, §5a).

Resolve a `(repo, ref)` **managed** source (one not already on disk, §2a) to a
local **staging path** with that commit checked out — the directory the venv
materialization (§9b) and the env worker then use. Two-level, mirroring the
design:

- a **per-repo bare mirror** (`git clone --mirror` once, `git fetch` after) — a
  per-pod cache, so repeated materializes of the same repo only fetch (§2/§5);
- a **`git worktree`** checkout of the resolved commit — cheap, off the mirror.

**This slice — the git adapter behind the `RepoSource` seam** (§5a: GitHub/git
today, S3 later — the origin is any git URL or local path). The bare mirror is
keyed per repo; staging is keyed per `(repo, commit)` so the same commit reuses
one checkout (per-session worktree isolation, §5, is a later refinement).
Failures — an unreachable repo, an unknown ref (§6) — surface as
:class:`RepoStagingError` with the git tail, never a hang.

Deferred (design): the S3 `RepoSource` adapter (§5a, coordinated with sms-api),
per-session worktree isolation + GC (§5/§7), and wiring a managed `(repo, ref)`
switch that chains staging → `materialize` (§9b) → session prepare (§9c).
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from vivarium_workbench.lib.env_compat import get_env

# Clone/fetch of a large repo can be minutes (a different cost class from a query,
# §1) — a long, separate timeout. Config-overridable.
_DEFAULT_TIMEOUT_S = 900.0


class RepoStagingError(Exception):
    """A `(repo, ref)` could not be staged (§6): unreachable repo, unknown ref,
    clone/fetch/worktree failure, or timeout. ``tail`` carries the git output."""

    def __init__(self, message: str, *, tail: str = ""):
        super().__init__(message)
        self.tail = tail


class StagingResult:
    """A staged managed source: the checkout ``path`` and its resolved ``commit``
    (the ``source_version``, §2)."""

    __slots__ = ("path", "commit", "repo")

    def __init__(self, path: Path, commit: str, repo: str):
        self.path = path
        self.commit = commit
        self.repo = repo

    def as_dict(self) -> dict:
        return {"path": str(self.path), "commit": self.commit, "repo": self.repo}


def store_root() -> Path:
    """The repo store — bare mirrors + staging worktrees live under here.
    Override with ``VIVARIUM_WORKBENCH_REPO_STORE``; defaults under the user cache."""
    override = get_env("REPO_STORE")
    if override:
        return Path(override)
    return Path.home() / ".cache" / "vivarium-workbench" / "repos"


def _repo_key(repo: str) -> str:
    """Filesystem-safe key for a repo origin (its bare-mirror + staging bucket)."""
    return hashlib.sha256(repo.strip().encode("utf-8")).hexdigest()[:16]


def _git(args: list[str], *, timeout: float, what: str) -> "subprocess.CompletedProcess[str]":
    try:
        proc = subprocess.run(["git", *args], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise RepoStagingError(f"{what} timed out after {int(timeout)}s", tail="") from e
    except FileNotFoundError as e:  # git not installed
        raise RepoStagingError("git not found on PATH", tail="") from e
    if proc.returncode != 0:
        raise RepoStagingError(what, tail=(proc.stderr or proc.stdout or "")[-2000:])
    return proc


def _ensure_mirror(repo: str, mirror: Path, *, timeout: float) -> None:
    """Bare mirror of ``repo`` at ``mirror``: clone once (all refs), else fetch."""
    if (mirror / "HEAD").is_file():
        # Existing mirror — refresh refs (a --mirror remote fetches +refs/*:refs/*).
        _git(["-C", str(mirror), "fetch", "--prune", "--quiet"],
             timeout=timeout, what=f"could not fetch {repo}")
        return
    mirror.parent.mkdir(parents=True, exist_ok=True)
    _git(["clone", "--mirror", "--quiet", repo, str(mirror)],
         timeout=timeout, what=f"could not reach {repo}")


def _resolve_commit(mirror: Path, ref: str) -> str:
    """Resolve ``ref`` (branch / tag / sha) to a commit sha in the mirror."""
    proc = subprocess.run(
        ["git", "-C", str(mirror), "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        capture_output=True, text=True)
    sha = proc.stdout.strip()
    if proc.returncode != 0 or not sha:
        raise RepoStagingError(f"ref {ref!r} not found in {mirror.name}", tail=proc.stderr[-500:])
    return sha


def stage(repo: str, ref: str, *, timeout: float = _DEFAULT_TIMEOUT_S) -> StagingResult:
    """Stage a managed `(repo, ref)` → a local checkout at its resolved commit.

    Ensures the per-repo bare mirror (clone once / fetch after), resolves ``ref``
    → commit, and `git worktree add`s that commit into a per-`(repo, commit)`
    staging dir (reused if already present). Raises :class:`RepoStagingError`
    (with the git tail) on any failure (§6)."""
    if not (repo or "").strip():
        raise RepoStagingError("empty repo")
    key = _repo_key(repo)
    root = store_root()
    mirror = root / "mirrors" / f"{key}.git"

    _ensure_mirror(repo, mirror, timeout=timeout)
    commit = _resolve_commit(mirror, ref)

    staging = root / "staging" / key / commit
    if _is_worktree(staging):
        return StagingResult(staging, commit, repo)          # cache hit

    staging.parent.mkdir(parents=True, exist_ok=True)
    # `--force`: tolerate a stale/partial dir from an interrupted prior add.
    _git(["-C", str(mirror), "worktree", "add", "--detach", "--force", "--quiet",
          str(staging), commit], timeout=timeout, what=f"could not check out {commit[:12]}")
    return StagingResult(staging, commit, repo)


def _is_worktree(path: Path) -> bool:
    """A usable existing checkout — a `git worktree` marks it with a `.git` file."""
    return path.is_dir() and (path / ".git").exists()
