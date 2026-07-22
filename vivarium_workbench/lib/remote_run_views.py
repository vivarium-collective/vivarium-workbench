"""Pure builder for the remote-run SUBMIT route.

Behaviour-preserving port of the stdlib handler
``server.Handler._post_remote_run_start`` — submits a remote (sms-api)
simulation pipeline job to the SAME in-process ``lib.remote_run_jobs.manager``
singleton the already-ported ``GET /api/remote-run-status`` reads, so a FastAPI
submit is visible to the status GET.  No ``import server`` here.

``remote_run_start(ws_root, body)`` returns ``(body, status)`` — the FastAPI
route wraps every path (incl. the 202 success) in ``JSONResponse``.

The externals — ``manager``, ``PipelineCtx``, ``run_remote_pipeline``,
``land_remote_run``, ``SmsApiClient``, ``load_spec``, ``github_auth`` — are
bound at MODULE level so tests monkeypatch them with fakes and never touch a
real network / git / auth service.  ``_sms_api_base`` is REUSED from
:mod:`lib.workspace_deps_views` (no new copy); the git/study helpers come from
:mod:`lib.git_status` / :mod:`lib.study_spec`.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from vivarium_workbench.lib import git_status
from vivarium_workbench.lib import github_auth
from vivarium_workbench.lib import remote_pinned
from vivarium_workbench.lib import study_spec
from vivarium_workbench.lib.investigations import load_spec
from vivarium_workbench.lib.remote_run_jobs import (
    PipelineCtx,
    manager,
    run_remote_pipeline,
)
from vivarium_workbench.lib.remote_run_landing import land_remote_run
from vivarium_workbench.lib.sms_api_client import SmsApiClient, SmsApiError
from vivarium_workbench.lib.workspace_deps_views import _sms_api_base

# sms-api JobStatus terminal sets (relocated here from remote_run_jobs, which R5
# deletes). The thin client maps a raw sms-api status into a UI phase.
_TERMINAL_OK = {"completed", "done", "succeeded"}
_TERMINAL_BAD = {"failed", "cancelled", "error"}


def _run_auth_ok() -> bool:
    """Gate for submit/land. A GitHub session satisfies it (stock build-first
    flow). Pinned mode ALSO satisfies it: those calls push nothing to GitHub, so
    requiring a human token would be neither production-grade nor reproducible —
    the operator authorizes remote runs declaratively by enabling pinned mode."""
    return github_auth.current_session() is not None or remote_pinned.is_pinned_enabled()


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

    Submits to the SAME ``remote_run_jobs.manager`` singleton; wires
    ``PipelineCtx`` identically, including the ZERO-ARG ``push_and_sha`` callable
    (a lambda closing over ``ws_root``).
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
    spec = load_spec(spec_path)
    observables = study_spec.collect_study_observables(spec)

    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=ws_root,
        capture_output=True, text=True, timeout=5,
    ).stdout.strip()

    client = SmsApiClient(_sms_api_base())
    # spec_id = the study's baseline COMPOSITE ref (what local runs use:
    # _post_study_run_baseline_for_test -> entry.get("composite")), NOT the
    # baseline entry's `name` (which is the study slug). Falls back to the
    # study slug only when no baseline composite is declared.
    _baseline = spec.get("baseline") or []
    _spec_id = (_baseline[0].get("composite") if _baseline else None) or study
    ctx = PipelineCtx(
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
        land=land_remote_run,
    )
    job = manager.submit(study, lambda j: run_remote_pipeline(j, ctx))
    return {"job_id": job.job_id}, 202


# ---------------------------------------------------------------------------
# WS1 — thin-client two-phase builders (ADDITIVE; the legacy pipeline above
# stays until the JS panel cuts over and R5 deletes it). sms-api separates
# build from run, so the flow is: build-start -> (JS polls status) -> submit
# -> (JS polls status) -> land. Each builder is one stateless sms-api call;
# durability lives in sms-api's Postgres, not an in-process manager.
# ---------------------------------------------------------------------------

def _resolve_repo_branch(ws_root: Path, body: dict) -> tuple[dict, int] | tuple[str, str]:
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
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=ws_root,
        capture_output=True, text=True, timeout=5,
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
    client = SmsApiClient(_sms_api_base())
    uploaded = client.upload_simulator(
        {"git_commit_hash": commit, "git_repo_url": repo_url, "git_branch": branch}
    )
    return {"simulator_id": uploaded["database_id"], "phase": "building",
            "branch": branch, "commit": commit}, 202


def remote_run_pinned_build_start(ws_root: Path, body: dict) -> tuple[dict, int]:
    """Phase 1, pinned variant: resolve the latest **built** simulator for the
    configured repo@branch (one in-cluster sms-api GET) and hand it back as an
    already-``built`` phase — NO git push, NO login, NO local-repo access.

    Returns ``({simulator_id, phase:"built", commit, branch, pinned:true}, 202)``
    so the JS panel skips build-polling and goes straight to submit. Returns
    ``409`` when pinned mode is off, ``502`` when sms-api is unreachable, ``404``
    when no build exists for the configured repo@branch."""
    cfg = remote_pinned.pinned_config()
    if cfg is None:
        return {"error": "pinned remote runs are not enabled"}, 409
    client = SmsApiClient(_sms_api_base())
    try:
        resolved = remote_pinned.resolve_pinned_build(client, cfg.repo_url, cfg.branch)
    except remote_pinned.NoPinnedBuildError as e:
        return {"error": str(e)}, 404
    except SmsApiError as e:
        return {"error": f"sms-api unreachable: {e}", "reachable": False}, 502
    return {"simulator_id": resolved["simulator_id"], "phase": "built",
            "commit": resolved["commit"], "branch": resolved["branch"],
            "pinned": True}, 202


def remote_run_config() -> tuple[dict, int]:
    """Report pinned-run config for the client to relabel the run card.

    ``{"pinned": false}`` when off; ``{"pinned": true, "repo_url", "branch",
    "commit"?, "simulator_id"?}`` when on. Both carry ``deployment`` — the
    config-derived Origin name (``VIVARIUM_WORKBENCH_REMOTE_DEPLOYMENT``) so the
    run form's origin selector labels "Remote:<deployment>" truthfully instead of
    a hardcoded "smsvpctest". Resolving the build is best-effort — a missing build
    or unreachable sms-api degrades to ``build_error`` rather than failing the card."""
    deployment = remote_pinned.remote_deployment_name()
    cfg = remote_pinned.pinned_config()
    if cfg is None:
        return {"pinned": False, "deployment": deployment}, 200
    out: dict = {"pinned": True, "repo_url": cfg.repo_url, "branch": cfg.branch, "deployment": deployment}
    try:
        resolved = remote_pinned.resolve_pinned_build(
            SmsApiClient(_sms_api_base()), cfg.repo_url, cfg.branch)
        out["commit"] = resolved["commit"]
        out["simulator_id"] = resolved["simulator_id"]
    except (remote_pinned.NoPinnedBuildError, SmsApiError) as e:
        out["build_error"] = str(e)
    return out, 200


def remote_run_submit(ws_root: Path, body: dict) -> tuple[dict, int]:
    """Phase 2: issue the run for a COMPLETED build. Returns
    ``({simulation_id, phase:"running"}, 202)``."""
    body = body or {}
    if not _run_auth_ok():
        return {"error": "not authenticated"}, 401
    study = (body.get("study") or "").strip()
    if not study:
        return {"error": "study is required"}, 400
    sim_id = body.get("simulator_id")
    if not sim_id:
        return {"error": "simulator_id is required"}, 400
    spec_path = study_spec.study_spec_path(ws_root, study)
    if spec_path is None or not spec_path.is_file():
        return {"error": f"study {study!r} not found"}, 404
    spec = load_spec(spec_path)
    observables = study_spec.collect_study_observables(spec)
    client = SmsApiClient(_sms_api_base())
    sim = client.run_simulation(
        simulator_id=int(sim_id),
        num_generations=int(body.get("num_generations") or 1),
        num_seeds=int(body.get("num_seeds") or 1),
        run_parca=bool(body.get("run_parca", True)),
        observables=observables,
    )
    return {"simulation_id": sim["database_id"], "phase": "running"}, 202


def remote_run_land(ws_root: Path, body: dict) -> tuple[dict, int]:
    """Phase 3 (on demand): download a COMPLETED sim's store and land it as a
    study run. Returns ``({run_id}, 200)``."""
    body = body or {}
    if not _run_auth_ok():
        return {"error": "not authenticated"}, 401
    study = (body.get("study") or "").strip()
    sim_id = body.get("simulation_id")
    if not study or not sim_id:
        return {"error": "study and simulation_id are required"}, 400
    spec_path = study_spec.study_spec_path(ws_root, study)
    if spec_path is None or not spec_path.is_file():
        return {"error": f"study {study!r} not found"}, 404
    spec = load_spec(spec_path)
    _baseline = spec.get("baseline") or []
    spec_id = (_baseline[0].get("composite") if _baseline else None) or study
    client = SmsApiClient(_sms_api_base())
    with tempfile.TemporaryDirectory() as td:
        tar_path = client.download_data(int(sim_id), Path(td))
        run_id = land_remote_run(
            study_spec.study_dir(ws_root, study),
            spec_id=spec_id,
            simulation_id=int(sim_id),
            experiment_id=body.get("experiment_id") or f"sim-{sim_id}-{study}",
            commit=body.get("commit") or "",
            tar_path=tar_path,
            s3_uri=body.get("s3_uri"),
        )
    return {"run_id": run_id}, 200


def remote_run_status(params: dict) -> tuple[dict, int]:
    """On-demand status read from sms-api (NO in-process state). The JS panel
    polls this per phase. Pass ``simulation_id`` (run phase) or ``simulator_id``
    (build phase). Maps the raw sms-api status into a UI ``phase``."""
    params = params or {}
    sim_id = params.get("simulation_id")
    sm_id = params.get("simulator_id")
    if not sim_id and not sm_id:
        return {"error": "simulator_id or simulation_id required"}, 400
    client = SmsApiClient(_sms_api_base())
    try:
        if sim_id:
            st = client.simulation_status(int(sim_id))
            raw = str(st.get("status", "")).lower()
            phase = ("done" if raw in _TERMINAL_OK
                     else "failed" if raw in _TERMINAL_BAD
                     else "queued" if raw == "queued" else "running")
            return {"kind": "run", "phase": phase, "raw_status": raw,
                    "error": st.get("error_message"), "simulation_id": int(sim_id)}, 200
        if sm_id:
            st = client.simulator_status(int(sm_id))
            raw = str(st.get("status", "")).lower()
            phase = ("built" if raw in _TERMINAL_OK
                     else "failed" if raw in _TERMINAL_BAD else "building")
            return {"kind": "build", "phase": phase, "raw_status": raw,
                    "error": st.get("error_message"), "simulator_id": int(sm_id)}, 200
        return {"error": "simulator_id or simulation_id required"}, 400
    except SmsApiError as e:
        # Tunnel down / SSO expired / sms-api error — surface a reachable=false
        # status so the panel shows it without the whole poll crashing.
        reason = "auth expired (re-run aws sso login)" if getattr(e, "status", None) == 401 \
            else "sms-api unreachable (is the tunnel up?)"
        return {"phase": "unreachable", "reachable": False, "reason": reason,
                "status": getattr(e, "status", None), "error": str(e)}, 502
