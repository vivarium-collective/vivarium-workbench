"""The views behind the SMS-only remote-run routes (docs/backend-adapters.md §2.2, step P2 Runs a2).

Moved verbatim from ``lib.remote_run_views``: the legacy pipeline submit (``POST /api/remote-run-start``, one
background job on ``remote_run_jobs.manager``) and phase 1 of the WS1 thin client (``POST /api/remote-run-build``,
``POST /api/remote-run-pinned-build``). Their routes are this plugin's (``routes.py``, ``Provider.router``); no
browser code calls them.

Only ``routes`` imports this module, so a process that never builds an app (an env worker that loads the provider
for the registry) never imports ``remote_run_jobs``. Every seam is read through its module at call time: the SMS
ones (``SmsApiClient``, ``_sms_api_base``, ``load_spec``) through ``sms_legacy``, ``land_remote_run`` through ``sms_landing``, and the
pipeline's (``manager``, ``PipelineCtx``, ``run_remote_pipeline``) through ``remote_run_jobs``. Each has one patch
target, and none is bound here.

Each view returns ``(body, status)``; the route wraps every path (incl. the 202 success) in ``JSONResponse``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from vivarium_workbench.lib import git_status, github_auth, study_spec
from vivarium_workbench_sms import remote_pinned, remote_run_jobs, sms_landing, sms_legacy


def _sms_client() -> "sms_legacy.SmsApiClient":
    """A client on the SMS host, built from ``sms_legacy``'s seams at call time (tests patch them there)."""
    return sms_legacy.SmsApiClient(sms_legacy._sms_api_base())


def remote_run_start(ws_root: Path, body: dict) -> tuple[dict, int]:
    """Submit a remote sms-api pipeline job for a study. Returns ``(body, status)``.

    Behaviour-preserving port of ``_post_remote_run_start`` (steps 2-12,
    byte-identical messages + status order):

      * not authenticated        → ``({"error": "not authenticated"}, 401)``
      * missing study            → ``({"error": "study is required"}, 400)``
      * no origin remote         → ``({"error": "no GitHub remote configured"}, 409)``
      * unresolved origin url     → ``({"error": "could not resolve origin remote url"}, 409)``
      * study spec not found     → ``({"error": f"study {study!r} not found"}, 404)``
      * happy path               → ``({"job_id": job.job_id}, 202)``

    Submits to the SAME ``remote_run_jobs.manager`` singleton the status route
    reads; wires ``PipelineCtx`` identically, including the ZERO-ARG
    ``push_and_sha`` callable (a lambda closing over ``ws_root``).
    """
    body = body or {}
    if github_auth.current_session() is None:
        return {"error": "not authenticated"}, 401
    study = (body.get("study") or "").strip()
    if not study:
        return {"error": "study is required"}, 400
    if not git_status.has_origin_remote(ws_root):
        return {"error": "no GitHub remote configured"}, 409
    repo_url = git_status.remote_repo_url(ws_root)
    if not repo_url:
        return {"error": "could not resolve origin remote url"}, 409

    spec_path = study_spec.study_spec_path(ws_root, study)
    if spec_path is None or not spec_path.is_file():
        return {"error": f"study {study!r} not found"}, 404
    spec = sms_legacy.load_spec(spec_path)
    observables = study_spec.collect_study_observables(spec)

    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=ws_root,
        capture_output=True,
        text=True,
        timeout=5,
    ).stdout.strip()

    client = _sms_client()
    # spec_id = the study's baseline COMPOSITE ref (what local runs use:
    # _post_study_run_baseline_for_test -> entry.get("composite")), NOT the
    # baseline entry's `name` (which is the study slug). Falls back to the
    # study slug only when no baseline composite is declared.
    _baseline = spec.get("baseline") or []
    _spec_id = (_baseline[0].get("composite") if _baseline else None) or study
    ctx = remote_run_jobs.PipelineCtx(
        study=study,
        study_dir=study_spec.study_dir(ws_root, study),
        spec_id=_spec_id,
        repo_url=repo_url,
        branch=branch,
        observables=observables,
        num_generations=int(body.get("num_generations") or 1),
        num_seeds=int(body.get("num_seeds") or 1),
        run_parca=bool(body.get("run_parca", True)),
        client=client,
        push_and_sha=lambda: git_status.remote_push_and_sha(ws_root),
        land=sms_landing.land_remote_run,
    )
    job = remote_run_jobs.manager.submit(study, lambda j: remote_run_jobs.run_remote_pipeline(j, ctx))
    return {"job_id": job.job_id}, 202


# ---------------------------------------------------------------------------
# WS1 — thin-client two-phase builders (ADDITIVE; the legacy pipeline above
# stays until the JS panel cuts over and R5 deletes it). sms-api separates
# build from run, so the flow is: build-start -> (JS polls status) -> submit
# -> (JS polls status) -> land. Each builder is one stateless sms-api call;
# durability lives in sms-api's Postgres, not an in-process manager.
# ---------------------------------------------------------------------------


def _resolve_repo_branch(
    ws_root: Path, body: dict
) -> tuple[dict, int] | tuple[str, str]:
    """Shared guard ladder: auth/study/remote. Returns (error_body, status) on
    failure, or (repo_url, branch) on success."""
    if github_auth.current_session() is None:
        return {"error": "not authenticated"}, 401
    study = (body.get("study") or "").strip()
    if not study:
        return {"error": "study is required"}, 400
    if not git_status.has_origin_remote(ws_root):
        return {"error": "no GitHub remote configured"}, 409
    repo_url = git_status.remote_repo_url(ws_root)
    if not repo_url:
        return {"error": "could not resolve origin remote url"}, 409
    spec_path = study_spec.study_spec_path(ws_root, study)
    if spec_path is None or not spec_path.is_file():
        return {"error": f"study {study!r} not found"}, 404
    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=ws_root,
        capture_output=True,
        text=True,
        timeout=5,
    ).stdout.strip()
    return repo_url, branch


def remote_run_build_start(ws_root: Path, body: dict) -> tuple[dict, int]:
    """Phase 1: push the workspace commit and register the simulator build with
    sms-api. Returns ``({simulator_id, phase:"building", branch, commit}, 202)``
    WITHOUT polling the build — the JS panel polls ``remote-run-status``."""
    body = body or {}
    resolved = _resolve_repo_branch(ws_root, body)
    if isinstance(resolved[0], dict):  # error tuple
        return resolved  # type: ignore[return-value]
    repo_url, branch = resolved  # type: ignore[misc]
    commit = git_status.remote_push_and_sha(ws_root)
    client = _sms_client()
    uploaded = client.upload_simulator(
        {"git_commit_hash": commit, "git_repo_url": repo_url, "git_branch": branch}
    )
    return {
        "simulator_id": uploaded["database_id"],
        "phase": "building",
        "branch": branch,
        "commit": commit,
    }, 202


def remote_run_pinned_build_start(ws_root: Path, body: dict) -> tuple[dict, int]:
    """Phase 1, pinned variant: resolve the latest **built** simulator for the
    configured repo@branch (one in-cluster sms-api GET) and hand it back as an
    already-``built`` phase — NO git push, NO login, NO local-repo access.

    Returns ``({simulator_id, phase:"built", commit, branch, pinned:true}, 202)``
    so the JS panel skips build-polling and goes straight to submit. Returns
    ``409`` when pinned mode is off, ``502`` when sms-api is unreachable, ``404``
    when no build exists for the configured repo@branch.

    A session that has switched to a specific repo@commit via the workspace
    picker (``ws_root`` carries a ``.viv-build.json`` stamp) dispatches against
    THAT build — the deployment's static repo@branch pin is only the fallback
    for a session that never switched."""
    session_build = remote_pinned.resolved_from_session_build(ws_root)
    if session_build is not None:
        return {
            "simulator_id": session_build["simulator_id"],
            "phase": "built",
            "commit": session_build["commit"],
            "branch": session_build["branch"],
            "pinned": True,
        }, 202
    cfg = remote_pinned.pinned_config()
    if cfg is None:
        return {"error": "pinned remote runs are not enabled"}, 409
    client = _sms_client()
    try:
        resolved = remote_pinned.resolve_pinned_build(client, cfg.repo_url, cfg.branch)
    except remote_pinned.NoPinnedBuildError as e:
        return {"error": str(e)}, 404
    except sms_legacy.SmsApiError as e:
        return {"error": f"sms-api unreachable: {e}", "reachable": False}, 502
    return {
        "simulator_id": resolved["simulator_id"],
        "phase": "built",
        "commit": resolved["commit"],
        "branch": resolved["branch"],
        "pinned": True,
    }, 202
