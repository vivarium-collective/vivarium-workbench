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
import time
import warnings
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
from vivarium_workbench.lib.sms_api_client import DOWNLOAD_TIMEOUT, SmsApiClient, SmsApiError
from vivarium_workbench.lib.workspace_deps_views import _sms_api_base

# sms-api JobStatus terminal sets (relocated here from remote_run_jobs, which R5
# deletes). The thin client maps a raw sms-api status into a UI phase.
_TERMINAL_OK = {"completed", "done", "succeeded"}
_TERMINAL_BAD = {"failed", "cancelled", "error"}

# Real completion signal now exists (GET /analyses/{id}/status, S3-exists
# probe server-side) for Ray-backend triggers -- poll it instead of blindly
# sleeping. Interval/attempts sized to cover a cold image pull of the multi-GB
# v2ecoli image plus the analysis script's own (fast) run, without polling so
# tightly it hammers sms-api. _ANALYSIS_LAND_WAIT_SECONDS is the fallback for
# simulators where no database_id comes back at all (shouldn't happen for
# Ray-backend triggers post gap-3, kept only as a safety net).
_ANALYSIS_POLL_INTERVAL_SECONDS = 10
_ANALYSIS_POLL_MAX_ATTEMPTS = 30  # ~5 min ceiling
_ANALYSIS_LAND_WAIT_SECONDS = 90
_ANALYSIS_TERMINAL_STATUSES = {"completed", "failed"}


def _poll_analysis_until_terminal(client: SmsApiClient, database_id: int) -> None:
    """Poll GET /analyses/{id}/status until COMPLETED/FAILED or the attempt
    ceiling is reached. A miss just means the analysis isn't folded into THIS
    landing -- landing again later picks it up (see land_remote_run)."""
    for _ in range(_ANALYSIS_POLL_MAX_ATTEMPTS):
        try:
            status = client.analysis_status(database_id)
        except SmsApiError:
            return  # best-effort: a transient poll failure must not block landing
        if status.get("status") in _ANALYSIS_TERMINAL_STATUSES:
            return
        time.sleep(_ANALYSIS_POLL_INTERVAL_SECONDS)


def _resolve_execution_provenance(client: SmsApiClient, simulation_id: int) -> dict:
    """P1-11 (audit §3.9): the provenance of what sms-api actually ran for
    ``simulation_id``, read from sms-api's OWN records — never inferred from
    the landing laptop's checkout. Returns ``{commit, image, merged_config_hash,
    expected_seeds}``; any field sms-api's current API can't answer (or a
    reachability failure) comes back ``None`` rather than a locally-guessed
    value, so :func:`land_remote_run` records an honest gap instead of a
    fabricated one.

    ``image`` is the ECR tag sms-api actually built and ran under — which
    IS the commit itself (sms-api tags every image ``<repo>:<commit>``, see
    ``simulation_service_ray.SimulationServiceRay._image_uri``), not a
    laptop-side guess. Reconstructing the FULL image URI (registry account +
    region) would need deployment config this client has no access to; that
    stays a follow-up until sms-api's API surfaces it directly.
    """
    out: dict = {"commit": None, "image": None, "merged_config_hash": None, "expected_seeds": None}
    try:
        sim_record = client.get_simulation(simulation_id)
    except SmsApiError:
        return out
    simulator_id = sim_record.get("simulator_id")
    if simulator_id is not None:
        commit = client.simulator_commit(int(simulator_id))
        out["commit"] = commit
        out["image"] = commit
    out["expected_seeds"] = sim_record.get("num_seeds")
    config = sim_record.get("config")
    if config is not None:
        import hashlib
        import json as _json
        try:
            out["merged_config_hash"] = hashlib.sha256(
                _json.dumps(config, sort_keys=True, default=str).encode()
            ).hexdigest()
        except (TypeError, ValueError):
            pass
    return out


def _run_auth_ok() -> bool:
    """Gate for submit/land. A GitHub session satisfies it (stock build-first
    flow). Pinned mode ALSO satisfies it: those calls push nothing to GitHub, so
    requiring a human token would be neither production-grade nor reproducible —
    the operator authorizes remote runs declaratively by enabling pinned mode."""
    return (
        github_auth.current_session() is not None or remote_pinned.is_pinned_enabled()
    )


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
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=ws_root,
        capture_output=True,
        text=True,
        timeout=5,
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
    client = SmsApiClient(_sms_api_base())
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
    client = SmsApiClient(_sms_api_base())
    try:
        resolved = remote_pinned.resolve_pinned_build(client, cfg.repo_url, cfg.branch)
    except remote_pinned.NoPinnedBuildError as e:
        return {"error": str(e)}, 404
    except SmsApiError as e:
        return {"error": f"sms-api unreachable: {e}", "reachable": False}, 502
    return {
        "simulator_id": resolved["simulator_id"],
        "phase": "built",
        "commit": resolved["commit"],
        "branch": resolved["branch"],
        "pinned": True,
    }, 202


def remote_run_config(ws_root: Path) -> tuple[dict, int]:
    """Report pinned-run config for the client to relabel the run card.

    ``{"pinned": false}`` when off; ``{"pinned": true, "repo_url", "branch",
    "commit"?, "simulator_id"?}`` when on. Both carry ``deployment`` — the
    config-derived Origin name (``VIVARIUM_WORKBENCH_REMOTE_DEPLOYMENT``) so the
    run form's origin selector labels "Remote:<deployment>" truthfully instead of
    a hardcoded "smsvpctest". Resolving the build is best-effort — a missing build
    or unreachable sms-api degrades to ``build_error`` rather than failing the card.

    Mirrors :func:`remote_run_pinned_build_start`'s priority: this session's own
    switched build (``ws_root``'s ``.viv-build.json``) wins over the deployment's
    static repo@branch pin, so the label always matches what will actually run."""
    deployment = remote_pinned.remote_deployment_name()
    session_build = remote_pinned.resolved_from_session_build(ws_root)
    if session_build is not None:
        return {
            "pinned": True,
            "repo_url": session_build["repo_url"],
            "branch": session_build["branch"],
            "deployment": deployment,
            "commit": session_build["commit"],
            "simulator_id": session_build["simulator_id"],
        }, 200
    cfg = remote_pinned.pinned_config()
    if cfg is None:
        return {"pinned": False, "deployment": deployment}, 200
    out: dict = {
        "pinned": True,
        "repo_url": cfg.repo_url,
        "branch": cfg.branch,
        "deployment": deployment,
    }
    try:
        resolved = remote_pinned.resolve_pinned_build(
            SmsApiClient(_sms_api_base()), cfg.repo_url, cfg.branch
        )
        out["commit"] = resolved["commit"]
        out["simulator_id"] = resolved["simulator_id"]
    except (remote_pinned.NoPinnedBuildError, SmsApiError) as e:
        out["build_error"] = str(e)
    return out, 200


def remote_run_submit(ws_root: Path, body: dict) -> tuple[dict, int]:
    """Phase 2: issue the run for a COMPLETED build. Returns
    ``({simulation_id, phase:"running"}, 202)``, or ``({error, reachable:
    false}, 502)`` if the upstream sms-api/viva-api call itself fails.

    The 202 means what it says: this call returns as soon as sms-api hands
    back a ``database_id`` for the new simulation, NOT once the underlying
    compute is fully dispatched. For a canonical chain-dispatch request
    (many seeds x many generations), viva-api tracks the campaign's ongoing
    submission server-side and this call returns in seconds regardless of
    campaign size (backlog item 51 -- viva-api's own fix moves the slow
    per-seed submission loop off this request's response path, the same way
    ``remote_run_build_start`` already returns before a build finishes).
    The JS panel polls ``GET /api/remote-run-poll?simulation_id=<id>`` (->
    :func:`remote_run_status`) for real progress, exactly like every other
    phase in this module."""
    body = body or {}
    if not _run_auth_ok():
        return {"error": "not authenticated"}, 401
    study = (body.get("study") or "").strip()
    if not study:
        return {"error": "study is required"}, 400
    sim_id = body.get("simulator_id")
    if not sim_id:
        return {"error": "simulator_id is required"}, 400
    # num_generations/num_seeds directly size a real AWS Batch job -- unlike
    # ordinary composite params, an unset value must never be silently
    # defaulted to 1 (matching the client-side guard in
    # study-detail.js:_dispatchRemotePinned, which blocks the dispatch before
    # this route is ever called; this is the defense-in-depth twin for any
    # OTHER caller of this route). No default applies to these two fields.
    num_generations = body.get("num_generations")
    if not num_generations:
        return {"error": "num_generations is required"}, 400
    num_seeds = body.get("num_seeds")
    if not num_seeds:
        return {"error": "num_seeds is required"}, 400
    spec_path = study_spec.study_spec_path(ws_root, study)
    if spec_path is None or not spec_path.is_file():
        return {"error": f"study {study!r} not found"}, 404
    spec = load_spec(spec_path)
    observables = study_spec.collect_study_observables(spec)
    # spec.analyses (the same source study_run_post.run_study_analyses reads
    # for the LOCAL post-run pipeline) was never threaded into the remote
    # dispatch payload — every remote-dispatched run's analysis_options came
    # out empty regardless of what a study configured. build_analysis_options
    # translates it into v2ecoli's {scale: {name: params}} shape.
    from vivarium_workbench.lib.study_run_post import build_analysis_options

    analysis_options, analysis_errors = build_analysis_options(
        spec.get("analyses") or [], ws_root
    )
    for err in analysis_errors:
        warnings.warn(
            f"remote_run_submit: {study!r} analysis config: {err.get('error')}"
        )
    client = SmsApiClient(_sms_api_base())
    try:
        sim = client.run_simulation(
            simulator_id=int(sim_id),
            num_generations=int(num_generations),
            num_seeds=int(num_seeds),
            run_parca=bool(body.get("run_parca", True)),
            observables=observables,
            analysis_options=analysis_options or None,
            extra_params=body.get("extra_params") or None,
        )
    except SmsApiError as e:
        # backlog item 51: this call used to be left unguarded, so ANY
        # SmsApiError (a real timeout, a 5xx, a dropped connection) fell
        # through to api/app.py's generic Exception handler, which replaces
        # the real message with a bare "internal server error" 500 -- hiding
        # exactly the information that distinguishes "sms-api is genuinely
        # down" from "sms-api is fine, this one call just failed" (the
        # observed incident: viva-api's chain-dispatch submission loop kept
        # running server-side to completion after this client gave up, but
        # the opaque 500 gave the UI no hint of that, inviting a duplicate
        # retry of a real, expensive campaign). Mirrors the SAME
        # except-SmsApiError -> 502 pattern already used by
        # remote_run_pinned_build_start / remote_run_analysis in this file,
        # so the caller gets the actual upstream error message instead of a
        # generic crash, on this path too.
        return {"error": str(e), "reachable": False}, 502
    # item 84: land_remote_run is the ONLY place a runs_meta row has ever been
    # written for a remote run -- confirmed by reading the real code, not
    # assumed -- so a UI-dispatched campaign was completely invisible in the
    # Runs tab from dispatch until someone explicitly clicked "Land Results",
    # which could be hours later or never. Write a lightweight pending-dispatch
    # placeholder here, at the moment we actually have a real simulation_id,
    # so the Runs tab shows it immediately. Its run_id (`remote-pending-<id>`)
    # is deliberately NOT generate_run_id's own scheme -- that hash embeds the
    # CALL-TIME timestamp, so it can never be reproduced later at land time to
    # target the same row; a stable, simulation_id-derived id is what makes
    # land_remote_run's own cleanup below possible. Best-effort: a Runs-tab
    # visibility row must never block or fail a real, already-dispatched,
    # real-money AWS Batch campaign.
    try:
        from vivarium_workbench.lib import composite_runs as cr
        _baseline = spec.get("baseline") or []
        _spec_id = (_baseline[0].get("composite") if _baseline else None) or study
        _deployment = remote_pinned.remote_deployment_name()
        conn = cr.connect(study_spec.study_dir(ws_root, study) / "runs.db")
        try:
            cr.save_metadata(
                conn,
                spec_id=_spec_id,
                run_id=f"remote-pending-{sim['database_id']}",
                params={
                    "source": _deployment,
                    "simulation_id": sim["database_id"],
                    "backend": "ray",
                },
                label=f"Remote dispatch ({_deployment}) — in progress",
                started_at=time.time(),
                n_steps=0,
                workspace=ws_root,
            )
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — see docstring above; never block the real dispatch
        pass
    response = {"simulation_id": sim["database_id"], "phase": "running"}
    if analysis_errors:
        # backlog item 39: surface which requested analyses couldn't be
        # resolved (e.g. a stale workspace PVC missing a newer v2ecoli
        # analysis) instead of silently dropping them from analysis_options
        # -- the dispatch itself still proceeds, this is informational.
        response["analysis_errors"] = analysis_errors
    return response, 202


def remote_run_land(ws_root: Path, body: dict) -> tuple[dict, int]:
    """Phase 3 (on demand): download a COMPLETED sim's store and land it as a
    study run. Returns ``({run_id}, 200)``.

    If the study has ``spec.analyses`` configured, also triggers standalone
    analysis on the same simulation before downloading, then polls its real
    status (GET /analyses/{id}/status) so the download -- which streams
    everything under the experiment's S3 prefix -- has a real chance of
    picking up the analysis output too. If the job hasn't finished within the
    poll ceiling, the run still lands normally; the analysis just isn't
    folded in yet (landing again later will pick it up).
    """
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

    analyses = spec.get("analyses") or []
    analysis_errors: list[dict] = []
    if analyses:
        from vivarium_workbench.lib.study_run_post import build_analysis_options

        # backlog item 39: unresolvable analysis names are surfaced in the
        # response below (informational) but never block landing -- this
        # trigger is best-effort, and landing the simulation itself is the
        # primary, always-must-succeed action of this route.
        analysis_options, analysis_errors = build_analysis_options(analyses, ws_root)
        if analysis_options:
            try:
                triggered = client.run_analysis(int(sim_id), analysis_options)
            except SmsApiError:
                pass
            else:
                database_id = triggered.get("database_id")
                if database_id is not None:
                    _poll_analysis_until_terminal(client, database_id)
                else:
                    # No database_id came back -- this simulator's analysis path
                    # has no status endpoint (shouldn't happen for Ray-backend
                    # triggers post gap-3; kept as a safety net for anything else).
                    time.sleep(_ANALYSIS_LAND_WAIT_SECONDS)

    # P1-11 (audit §3.9): every provenance field below is sourced from sms-api
    # itself -- the server that actually ran this simulation -- not from the
    # landing laptop's local checkout. `commit` previously defaulted to
    # body.get("commit"), which no caller has ever actually supplied, so a
    # landed run's recorded commit silently fell back to save_metadata's own
    # local-workspace-HEAD inference (the found "partly wrong" bug). A caller
    # that DOES pass a commit/expected_seeds in the body still wins -- this is
    # a fallback for when sms-api's own lookup can't answer, not an override.
    exec_prov = _resolve_execution_provenance(client, int(sim_id))
    commit = body.get("commit") or exec_prov["commit"] or ""
    expected_seeds = body.get("num_seeds") or exec_prov["expected_seeds"]

    with tempfile.TemporaryDirectory() as td:
        # Explicit, generous timeout — this streams the run's whole native-store
        # tar.gz, which can be multi-GB; the 30s status-call default would abort
        # it mid-download (CD2 pipeline audit §3.12).
        tar_path = client.download_data(int(sim_id), Path(td), timeout=DOWNLOAD_TIMEOUT)
        run_id = land_remote_run(
            study_spec.study_dir(ws_root, study),
            spec_id=spec_id,
            simulation_id=int(sim_id),
            experiment_id=body.get("experiment_id") or f"sim-{sim_id}-{study}",
            commit=commit,
            tar_path=tar_path,
            ws_root=ws_root,
            s3_uri=body.get("s3_uri"),
            image=exec_prov["image"],
            merged_config_hash=exec_prov["merged_config_hash"],
            expected_seeds=int(expected_seeds) if expected_seeds else None,
        )
    response: dict = {"run_id": run_id}
    if analysis_errors:
        # backlog item 39: surface which requested analyses couldn't be
        # resolved (e.g. a stale workspace PVC missing a newer v2ecoli
        # analysis) instead of silently dropping them -- landing itself
        # still succeeds, this is informational.
        response["analysis_errors"] = analysis_errors
    return response, 200


def remote_run_land_artifacts(ws_root: Path, body: dict) -> tuple[dict, int]:
    """Land a remote run's analyses + PTools exports WITHOUT a study — the
    study-less counterpart of ``remote_run_land`` for a Runs-table remote row
    (a GovCloud run that isn't investigation-organized, so it has no study spec
    to land into). Downloads the sim's result tar and folds ``analyses.json`` +
    copies ``ptools/*.tsv`` into ``.pbg/runs/<run_id>/``, so the Analyses button
    resolves and the run shows in the PTools Omics Viewer's run menu.

    This is the "land on demand" endpoint the Runs-table remote-row actions call.
    Body: ``{simulation_id, run_id}``. Idempotent.
    """
    body = body or {}
    if not _run_auth_ok():
        return {"error": "not authenticated"}, 401
    sim_id = body.get("simulation_id")
    run_id = (body.get("run_id") or "").strip()
    if not sim_id or not run_id:
        return {"error": "simulation_id and run_id are required"}, 400
    from vivarium_workbench.lib.remote_run_landing import land_remote_simulation_artifacts

    client = SmsApiClient(_sms_api_base())
    try:
        result = land_remote_simulation_artifacts(ws_root, int(sim_id), run_id, client=client)
    except SmsApiError as e:
        return {"error": f"land failed: sms-api unavailable: {e}"}, 502
    except Exception as e:  # noqa: BLE001 — surface a shaped error, never a 500 traceback page
        return {"error": f"land failed: {type(e).__name__}: {e}"}, 500
    return result, 200


def remote_run_analysis(ws_root: Path, body: dict) -> tuple[dict, int]:
    """Fire the analysis phase on an EXISTING, already-completed simulation.

    The on-demand counterpart to the dispatch DAG's own analysis node (viva-api
    submits that one automatically when a run completes). This exists because a
    completed simulation whose analysis failed, or predates its study's current
    ``spec.analyses``, or was dispatched before the auto-trigger landed, was
    otherwise only re-analysable through the ``atlantis`` CLI — which the
    "everything through the Workbench" bar rules out.

    Deliberately NOT coupled to landing: ``remote_run_land`` already triggers an
    analysis as a side effect of downloading a run into a study, which cannot
    serve a simulation that is not being landed (or is not in a study at all).

    ``modules`` resolution, in order:
      * an explicit ``modules`` in the body (a ``{scale: {name: params}}`` map);
      * the named study's ``spec.analyses``, via the same
        ``study_run_post.build_analysis_options`` the local post-run pipeline and
        ``remote_run_submit`` use — so the button re-runs what the study asks for;
      * nothing, letting viva-api resolve the simulation's own configured
        analysis_options (and, failing that, the model image's own "every
        applicable analysis" set). Passing a guessed default from here would be a
        third, drift-prone copy of a list neither side can verify.

    Returns ``({analysis_id, analysis_name, simulation_id, phase}, 202)``.
    """
    body = body or {}
    if not _run_auth_ok():
        return {"error": "not authenticated"}, 401
    sim_id = body.get("simulation_id")
    if not sim_id:
        return {"error": "simulation_id is required"}, 400

    modules = body.get("modules") or {}
    study = (body.get("study") or "").strip()
    analysis_errors: list[dict] = []
    if not modules and study:
        spec_path = study_spec.study_spec_path(ws_root, study)
        if spec_path is None or not spec_path.is_file():
            return {"error": f"study {study!r} not found"}, 404
        from vivarium_workbench.lib.study_run_post import build_analysis_options

        modules, analysis_errors = build_analysis_options(load_spec(spec_path).get("analyses") or [], ws_root)
        for err in analysis_errors:
            warnings.warn(f"remote_run_analysis: {study!r} analysis config: {err.get('error')}")

    client = SmsApiClient(_sms_api_base())
    try:
        triggered = client.run_analysis(int(sim_id), modules)
    except SmsApiError as e:
        # Unlike the land-time trigger (best-effort, must never block landing),
        # this IS the requested action — a failure has to reach the operator.
        status = 401 if getattr(e, "status", None) == 401 else 502
        return {"error": str(e), "simulation_id": int(sim_id)}, status
    response: dict = {
        "analysis_id": triggered.get("database_id"),
        "analysis_name": triggered.get("analysis_name"),
        "simulation_id": int(sim_id),
        "phase": "analyzing",
    }
    if analysis_errors:
        # backlog item 39: surface which requested analyses couldn't be
        # resolved (e.g. a stale workspace PVC missing a newer v2ecoli
        # analysis) instead of silently dropping them -- the analysis trigger
        # itself still proceeds (via whatever modules DID resolve), this is
        # informational.
        response["analysis_errors"] = analysis_errors
    return response, 202


def remote_run_status(params: dict) -> tuple[dict, int]:
    """On-demand status read from sms-api (NO in-process state). The JS panel
    polls this per phase. Pass ``simulation_id`` (run phase), ``simulator_id``
    (build phase) or ``analysis_id`` (analysis phase). Maps the raw sms-api
    status into a UI ``phase``."""
    params = params or {}
    sim_id = params.get("simulation_id")
    sm_id = params.get("simulator_id")
    an_id = params.get("analysis_id")
    if not sim_id and not sm_id and not an_id:
        return {"error": "simulator_id, simulation_id or analysis_id required"}, 400
    client = SmsApiClient(_sms_api_base())
    try:
        if an_id:
            # GET /analyses/{id}/status resolves against the job's own S3
            # _manifest.json, so it answers for an auto-triggered (dispatch-DAG)
            # analysis and a button-triggered one identically.
            st = client.analysis_status(int(an_id))
            raw = str(st.get("status", "")).lower()
            phase = "done" if raw in _TERMINAL_OK else "failed" if raw in _TERMINAL_BAD else "running"
            return {
                "kind": "analysis",
                "phase": phase,
                "raw_status": raw,
                "error": st.get("error_log") or st.get("error_message"),
                "analysis_id": int(an_id),
            }, 200
        if sim_id:
            st = client.simulation_status(int(sim_id))
            raw = str(st.get("status", "")).lower()
            phase = (
                "done"
                if raw in _TERMINAL_OK
                else "failed"
                if raw in _TERMINAL_BAD
                else "queued"
                if raw == "queued"
                else "running"
            )
            return {
                "kind": "run",
                "phase": phase,
                "raw_status": raw,
                "error": st.get("error_message"),
                "simulation_id": int(sim_id),
            }, 200
        if sm_id:
            st = client.simulator_status(int(sm_id))
            raw = str(st.get("status", "")).lower()
            phase = (
                "built"
                if raw in _TERMINAL_OK
                else "failed"
                if raw in _TERMINAL_BAD
                else "building"
            )
            return {
                "kind": "build",
                "phase": phase,
                "raw_status": raw,
                "error": st.get("error_message"),
                "simulator_id": int(sm_id),
            }, 200
        return {"error": "simulator_id, simulation_id or analysis_id required"}, 400
    except SmsApiError as e:
        # Tunnel down / SSO expired / sms-api error — surface a reachable=false
        # status so the panel shows it without the whole poll crashing.
        reason = (
            "auth expired (re-run aws sso login)"
            if getattr(e, "status", None) == 401
            else "sms-api unreachable (is the tunnel up?)"
        )
        return {
            "phase": "unreachable",
            "reachable": False,
            "reason": reason,
            "status": getattr(e, "status", None),
            "error": str(e),
        }, 502


def remote_run_chain_progress(params: dict) -> tuple[dict, int]:
    """Backlog item 6: real per-seed aggregate progress for a chain-dispatch
    campaign, proxying viva-api's ``GET /simulations/{id}/chain-progress``
    (PR #257) the same way ``remote_run_status`` proxies ``/status`` — on-demand,
    no in-process state, the JS panel polls this per phase (session-status.js's
    proven interval pattern, not SSE — see item 6's own backlog note on why).

    ``simulation_id`` required. 404 (unknown simulation) and 409 (exists but
    isn't a chain-dispatch campaign — nothing to aggregate, the caller should
    fall back to plain ``remote_run_status`` for those) pass through as
    distinct, meaningful phases rather than collapsing into one generic error,
    so the frontend can tell "not a campaign" apart from "genuinely broken"."""
    params = params or {}
    sim_id = params.get("simulation_id")
    if not sim_id:
        return {"error": "simulation_id required"}, 400
    client = SmsApiClient(_sms_api_base())
    try:
        cp = client.simulation_chain_progress(int(sim_id))
        raw = str(cp.get("status", "")).lower()
        phase = (
            "done"
            if raw in _TERMINAL_OK
            else "failed"
            if raw in _TERMINAL_BAD
            else "running"
        )
        return {
            "kind": "chain_progress",
            "phase": phase,
            "terminal": bool(cp.get("terminal")),
            "seeds_total": cp.get("seeds_total"),
            "seeds_succeeded": cp.get("seeds_succeeded"),
            "seeds_failed": cp.get("seeds_failed"),
            "seeds_in_progress": cp.get("seeds_in_progress"),
            "simulation_id": int(sim_id),
        }, 200
    except SmsApiError as e:
        msg = str(e)
        if msg.endswith("-> 404"):
            return {"kind": "chain_progress", "phase": "not_found", "error": msg,
                     "simulation_id": int(sim_id)}, 404
        if msg.endswith("-> 409"):
            # Real, expected case for a plain (non-chain-dispatch) run — not an
            # error the panel should alarm on, just "nothing to aggregate here."
            return {"kind": "chain_progress", "phase": "not_a_campaign", "error": msg,
                     "simulation_id": int(sim_id)}, 200
        return {
            "kind": "chain_progress", "phase": "unreachable", "reachable": False,
            "reason": "sms-api unreachable (is the tunnel up?)", "error": msg,
            "simulation_id": int(sim_id),
        }, 502
