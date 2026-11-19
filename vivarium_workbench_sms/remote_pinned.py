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
newest matching entry from the branch's registered builds (which carry the
real ``database_id``; ``SmsApiClient.list_branch_builds``), never trusting
``latest_simulator``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from vivarium_workbench.lib import backend_config as _backend_config
from vivarium_workbench.lib.env_compat import get_env
from vivarium_workbench_sms.sms_api_client import SmsApiClient

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


def resolved_from_session_build(ws_root: Path) -> dict | None:
    """This session's own switched build (WS3's ``.viv-build.json`` stamp,
    written by ``switch_build`` at ``vivarium_workbench_sms/source_build_views.py``), shaped
    identically to :func:`resolve_pinned_build`'s return value.

    A session that has switched to a specific repo@commit via the workspace
    picker should dispatch against THAT build, not the deployment's static
    ``VIVARIUM_WORKBENCH_REMOTE_REPO_URL`` pin — the two are unrelated, and
    without this check pinned-mode dispatch silently ignores which workspace
    the user actually selected. Returns ``None`` when ``ws_root`` isn't a
    materialized remote build (a local git checkout has no ``.viv-build.json``),
    so callers fall back to the deployment-wide pin unchanged.
    """
    meta_path = Path(ws_root) / ".viv-build.json"
    if not meta_path.is_file():
        return None
    try:
        data = json.loads(meta_path.read_text())
    except (OSError, ValueError):
        return None
    repo_url = str(data.get("repo_url") or "")
    simulator_id = data.get("simulator_id")
    if not repo_url or simulator_id is None:
        return None
    return {
        "simulator_id": int(simulator_id),
        "commit": str(data.get("commit") or ""),
        "branch": str(data.get("branch") or "main"),
        "repo_url": repo_url,
    }


# The deployment name is core configuration (docs/backend-adapters.md step P1a: ``backend_config.deployment_name``).
_DEFAULT_REMOTE_DEPLOYMENT = _backend_config._DEFAULT_DEPLOYMENT


def remote_deployment_name() -> str:
    """The deployment namespace a remote run targets: :func:`backend_config.deployment_name`, read at call time
    (tests patch this function, and ``backend_config.deployment_name``)."""
    return _backend_config.deployment_name()


def _normalize_repo(url: str) -> str:
    """Canonical repo key for matching: lower-case, no trailing slash, no ``.git``."""
    u = (url or "").strip().rstrip("/")
    if u.lower().endswith(".git"):
        u = u[: -len(".git")]
    return u.lower()


class NoPinnedBuildError(RuntimeError):
    """No completed build exists for the configured repo@branch."""


def resolve_pinned_simulator_id(client: SmsApiClient, ws_root: Path) -> int | None:
    """The simulator_id a deployment-target dispatch should use, via the same
    two-step precedence :func:`remote_run_pinned_build_start`/
    :func:`remote_run_config` already apply: this session's own switched
    build first, else the deployment-wide pin. Returns ``None`` when neither
    resolves (pinned mode off, or no build exists yet for the configured
    repo@branch) — callers surface that as their own error.

    Extracted here (item 83) so every dispatch entrypoint that needs "which
    build does a deployment-target run use" resolves it identically, instead
    of each re-deriving the session-build-then-pin fallback inline.
    """
    session_build = resolved_from_session_build(ws_root)
    if session_build is not None:
        return session_build["simulator_id"]
    cfg = pinned_config()
    if cfg is None:
        return None
    from vivarium_workbench_sms.sms_api_client import SmsApiError
    try:
        resolved = resolve_pinned_build(client, cfg.repo_url, cfg.branch)
    except (NoPinnedBuildError, SmsApiError):
        # Unreachable sms-api degrades the same as "no build resolved" — the
        # caller surfaces its own clear error rather than this bubbling up as
        # an unhandled exception (mirrors remote_run_config's own handling of
        # exactly these two exception types).
        return None
    return resolved["simulator_id"]


def resolve_pinned_build(client: SmsApiClient, repo_url: str, branch: str) -> dict:
    """Resolve the newest registered build for ``repo_url``@``branch``.

    Reads the branch's builds (``SmsApiClient.list_branch_builds``:
    ``/viva/v1/environments?repo_url=&branch=`` where the server advertises
    ``viva-v1-environments-filters``, else ``/core/v1/simulator/versions``; each
    entry carries ``database_id``), filters by normalized repo + exact branch,
    and returns the most-recently created match::

        {"simulator_id": int, "commit": str, "branch": str, "repo_url": str}

    Raises :class:`NoPinnedBuildError` when nothing matches. (Does NOT re-verify
    ``simulator_status`` — the submit call surfaces a not-ready build clearly;
    keeping this to one GET is what makes Phase 1 instant.)
    """
    want_repo = _normalize_repo(repo_url)
    versions = (client.list_branch_builds(repo_url, branch) or {}).get("versions") or []
    matches = [
        v
        for v in versions
        if _normalize_repo(v.get("git_repo_url", "")) == want_repo
        and (v.get("git_branch") or "") == branch
        and v.get("database_id") is not None
    ]
    if not matches:
        raise NoPinnedBuildError(
            f"no built simulator for {repo_url}@{branch} — register/build one first"
        )
    # Newest by created_at (ISO-8601 strings sort lexically); fall back to id.
    latest = max(
        matches,
        key=lambda v: (str(v.get("created_at") or ""), int(v.get("database_id", 0))),
    )
    return {
        "simulator_id": int(latest["database_id"]),
        "commit": str(latest.get("git_commit_hash") or ""),
        "branch": branch,
        "repo_url": repo_url,
    }
