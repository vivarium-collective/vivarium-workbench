"""Pinned-build remote runs — resolve the latest **built** simulator for a
configured repo@branch straight from in-cluster sms-api, with NO git push, NO
local-repo access, and NO GitHub login.

This backs the demo's "Run against pinned build" path (Direction 1): the demo
targets one already-built commit (the latest tip of ``main`` that sms-api has a
completed build for) and submits many simulation configs against it. Only the
stock build-first flow (``remote_run_build_start``) pushes git / needs login;
this module skips all of that.

Enabled by declarative deployment config (env), so the trust boundary is the
network + the in-cluster dashboard↔sms-api call — no human credentials, nothing
to rotate, fully reproducible.

    VIVARIUM_WORKBENCH_REMOTE_PINNED     truthy ⇒ pinned mode on
    VIVARIUM_WORKBENCH_REMOTE_REPO_URL   repo whose builds to run (required when on)
    VIVARIUM_WORKBENCH_REMOTE_BRANCH     branch to pin (default "main")

Resolution gotcha: sms-api registers builds under the bare repo URL
(``github.com/org/repo``) while ``latest_simulator`` may echo an *unbuilt*
git-tip for the ``.git`` form. So we normalize the ``.git`` suffix and pick the
newest matching entry from ``/core/v1/simulator/versions`` (which carries the
real ``database_id``), never trusting ``latest_simulator``.
"""

from __future__ import annotations

from dataclasses import dataclass

from vivarium_workbench.lib.env_compat import get_env
from vivarium_workbench.lib.sms_api_client import SmsApiClient

_TRUTHY = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class PinnedConfig:
    repo_url: str
    branch: str


def pinned_config() -> PinnedConfig | None:
    """Return the pinned-run config when enabled + a repo is set, else None.

    None means "pinned mode off" — callers fall back to the stock build-first
    flow (which keeps its GitHub-login gate).
    """
    if (get_env("REMOTE_PINNED", "") or "").strip().lower() not in _TRUTHY:
        return None
    repo_url = (get_env("REMOTE_REPO_URL", "") or "").strip()
    if not repo_url:
        return None
    branch = (get_env("REMOTE_BRANCH", "main") or "main").strip() or "main"
    return PinnedConfig(repo_url=repo_url, branch=branch)


def is_pinned_enabled() -> bool:
    return pinned_config() is not None


def _normalize_repo(url: str) -> str:
    """Canonical repo key for matching: lower-case, no trailing slash, no ``.git``."""
    u = (url or "").strip().rstrip("/")
    if u.lower().endswith(".git"):
        u = u[: -len(".git")]
    return u.lower()


class NoPinnedBuildError(RuntimeError):
    """No completed build exists for the configured repo@branch."""


def resolve_pinned_build(client: SmsApiClient, repo_url: str, branch: str) -> dict:
    """Resolve the newest registered build for ``repo_url``@``branch``.

    Reads ``/core/v1/simulator/versions`` (each entry carries ``database_id``),
    filters by normalized repo + exact branch, and returns the most-recently
    created match::

        {"simulator_id": int, "commit": str, "branch": str, "repo_url": str}

    Raises :class:`NoPinnedBuildError` when nothing matches. (Does NOT re-verify
    ``simulator_status`` — the submit call surfaces a not-ready build clearly;
    keeping this to one GET is what makes Phase 1 instant.)
    """
    want_repo = _normalize_repo(repo_url)
    versions = (client.list_simulators() or {}).get("versions") or []
    matches = [
        v for v in versions
        if _normalize_repo(v.get("git_repo_url", "")) == want_repo
        and (v.get("git_branch") or "") == branch
        and v.get("database_id") is not None
    ]
    if not matches:
        raise NoPinnedBuildError(
            f"no built simulator for {repo_url}@{branch} — register/build one first"
        )
    # Newest by created_at (ISO-8601 strings sort lexically); fall back to id.
    latest = max(matches, key=lambda v: (str(v.get("created_at") or ""), int(v.get("database_id", 0))))
    return {
        "simulator_id": int(latest["database_id"]),
        "commit": str(latest.get("git_commit_hash") or ""),
        "branch": branch,
        "repo_url": repo_url,
    }
