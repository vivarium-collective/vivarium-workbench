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

import json
from dataclasses import dataclass
from pathlib import Path

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


def resolve_run_target(ws_root: Path) -> str:
    """Item 18: THE authoritative local-vs-deployment execution target for
    every dashboard run entrypoint (Composites tab, Study tab, CLI, batch
    worker, rerun, ...) — every one of them must resolve this the SAME way,
    never by which button happened to be clicked.

    Returns ``"deployment"`` when EITHER:
      - this session has its own materialized remote build, stamped by the
        workspace picker (``run_core.run_target_for``'s existing
        ``.viv-build.json`` check), or
      - the deployment itself is configured for pinned remote runs
        (``VIVARIUM_WORKBENCH_REMOTE_PINNED`` — :func:`is_pinned_enabled`).
    Returns ``"local"`` otherwise.

    Real bug this closes (backlog item 18, confirmed from source 2026-08-04):
    ``composite_test_run_views.composite_test_run`` special-cased the second
    condition inline (``target = "deployment" if is_pinned_enabled() else
    None``); ``study_runs.launch_into_study``/``run_study_variant`` only ever
    saw the first (via ``invoke_run``'s own ``run_target_for`` fallback) — so
    a deployment-wide pin with no session build silently fell through to a
    local subprocess on the study-run path, while the Composites-tab path
    correctly routed to the deployment. Every run entrypoint now resolves
    through this one function and threads the result into
    ``invoke_run(..., target=...)`` explicitly, so none of them can drift
    apart again.

    Deliberately NOT a change to ``run_core.run_target_for`` itself, which
    stays ``.viv-build.json``-only: ``composite_resolve.
    resolve_composite_for_request`` depends on that narrower meaning (a
    composite PREVIEW needs a concrete ``simulator_id`` stamp, which a bare
    deployment-wide pin doesn't provide) and must not be affected by this
    broader run-DISPATCH resolution.
    """
    from vivarium_workbench.lib.run_core import run_target_for

    if run_target_for(Path(ws_root)) == "deployment":
        return "deployment"
    return "deployment" if is_pinned_enabled() else "local"


def resolved_from_session_build(ws_root: Path) -> dict | None:
    """This session's own switched build (WS3's ``.viv-build.json`` stamp,
    written by ``switch_build`` at ``lib/source_build_views.py``), shaped
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


# Default when VIVARIUM_WORKBENCH_REMOTE_DEPLOYMENT is unset — the historical
# hardcoded value, kept only as the fallback so existing deployments don't change
# behavior on upgrade. New deployments set the env explicitly (e.g. "smscdk").
_DEFAULT_REMOTE_DEPLOYMENT = "smsvpctest"


def remote_deployment_name() -> str:
    """The deployment namespace a remote run targets (the run's truthful Origin).

    Config-derived via ``VIVARIUM_WORKBENCH_REMOTE_DEPLOYMENT`` (same env-driven
    pattern as :func:`pinned_config`), replacing the hardcoded ``"smsvpctest"`` so
    a run's recorded Origin reflects the deployment it actually ran on.
    """
    return (
        get_env("REMOTE_DEPLOYMENT", _DEFAULT_REMOTE_DEPLOYMENT)
        or _DEFAULT_REMOTE_DEPLOYMENT
    ).strip() or _DEFAULT_REMOTE_DEPLOYMENT


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
