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
from pathlib import Path

from vivarium_dashboard.lib import git_status
from vivarium_dashboard.lib import github_auth
from vivarium_dashboard.lib import study_spec
from vivarium_dashboard.lib.investigations import load_spec
from vivarium_dashboard.lib.remote_run_jobs import (
    PipelineCtx,
    manager,
    run_remote_pipeline,
)
from vivarium_dashboard.lib.remote_run_landing import land_remote_run
from vivarium_dashboard.lib.sms_api_client import SmsApiClient
from vivarium_dashboard.lib.workspace_deps_views import _sms_api_base


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
