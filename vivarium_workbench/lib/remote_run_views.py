"""Pure builder for the remote-run SUBMIT route.

Behaviour-preserving port of the stdlib handler
``server.Handler._post_remote_run_start`` — submits a remote (sms-api)
simulation pipeline job to the SAME in-process ``lib.remote_run_jobs.manager``
singleton the already-ported ``GET /api/remote-run-status`` reads, so a FastAPI
submit is visible to the status GET.  No ``import server`` here.

``remote_run_start(ws_root, body)`` returns ``(body, status)`` — the FastAPI
route wraps every path (incl. the 202 success) in ``JSONResponse``.

The pipeline externals — ``manager``, ``PipelineCtx``, ``run_remote_pipeline``,
``github_auth`` — are bound at MODULE level so tests monkeypatch them with fakes
and never touch a real network / git / auth service. The SMS seams
(``SmsApiClient``, ``SmsApiError``, ``_sms_api_base``, ``load_spec``,
``_run_auth_ok``, ``land_remote_run``) and constants live in
:mod:`vivarium_workbench_sms.sms_legacy` (step P2, Runs a1) and are read through
that module at call time: one patch target per name, none bound here. The
lifecycle requests no run record routes go to the backend's ``unrouted`` facet
(``backends.unrouted()``); with providers off the core answers them itself.
"""

from __future__ import annotations

import subprocess
import time
import warnings
from pathlib import Path

from vivarium_workbench.lib import git_status
from vivarium_workbench.lib import github_auth
from vivarium_workbench_sms import remote_pinned
from vivarium_workbench.lib import backends, remote_run_compose
from vivarium_workbench_sms import sms_legacy
from vivarium_workbench.lib import study_spec
from vivarium_workbench_sms.remote_run_jobs import (
    PipelineCtx,
    manager,
    run_remote_pipeline,
)


def _sms_client() -> "sms_legacy.SmsApiClient":
    """A client on the SMS host, built from ``sms_legacy``'s seams at call time (tests patch them there). Used by the
    SMS views still here and the figure routes (transitional: Runs a2, Analyses)."""
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
        land=sms_legacy.land_remote_run,
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
    # The profile first: reading it is how a named compose-api becomes known as one, and the pinned build below is
    # looked up on the SMS host (sms_api_base()) only once it is.
    backend = _backend_info()
    body, status = _remote_run_config(ws_root)
    if backend is not None:
        body["backend"] = backend
    return body, status


def _backend_info() -> "dict | None":
    """The explicitly named backend's negotiated profile for the UI; ``None`` (no key at all,
    no network call) when the operator named none, so the payload is byte-identical to before."""
    from vivarium_workbench.lib.server_capabilities import explicit_backend_profile

    return explicit_backend_profile()


def _remote_run_config(ws_root: Path) -> tuple[dict, int]:
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
            _sms_client(), cfg.repo_url, cfg.branch
        )
        out["commit"] = resolved["commit"]
        out["simulator_id"] = resolved["simulator_id"]
    except (remote_pinned.NoPinnedBuildError, sms_legacy.SmsApiError) as e:
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
    phase in this module.

    ``config_filename`` (optional): a real filename under ``configs/`` in the
    pinned repo (``GET /api/v1/simulations/discovery?simulator_id=<id>`` on
    sms-api lists the pinned commit's real options). sms-api's own default
    (``api_simulation_default.json``) only exists in the public vEcoli-lineage
    repos -- omitting this 404s on any repo (e.g. sms-ecoli) that has never had
    that file. Threaded straight through to
    :meth:`SmsApiClient.run_simulation`; unset when absent/blank, matching
    every other optional field here."""
    body = body or {}
    if not body.get("simulator_id"):
        # No simulator id: this submit belongs to the named backend (compose-api, or viva-v1 documents), unless a
        # pinned or session build sends it to SMS. Resolve the target as every run entrypoint does: it answers 502 when
        # the named backend cannot be reached, instead of falling through to the SMS path's "not authenticated" or
        # "simulator_id is required", and it never asks that backend for a pinned or session build. A submit that
        # carries a simulator id is the SMS host's and does not wait on the named backend.
        # The operator named a backend that runs documents: compose-api (the composite runs in the environment its
        # registry lists for this workspace) or viva-core (serve --backend-base-url; the declaration authorizes the
        # dispatch). Either way there is no simulator id to ask for. A session build or pin selects SMS instead.
        # One decision (backends.active, which backends.resolve_run_target also reads): patching
        # backends.resolve_run_target does not steer this submit; patch backends.active / backends.select.
        chosen = backends.active(ws_root)
        if chosen is not None and chosen.unrouted is None:
            return chosen.runs.submit(ws_root, body)
    # A simulator id, or a backend with an ``unrouted`` facet (SMS: a session build or pin), or nothing named: the
    # unrouted backend answers, its login check first (401 before 400).
    ub = backends.unrouted()
    if ub is not None and ub.unrouted is not None:
        return ub.unrouted.submit(ws_root, body)
    if body.get("simulator_id"):  # providers off: no installed provider serves builds
        return _not_installed("", "a simulator id names a build, and no installed provider serves builds in this "
                                  "workbench")
    return {"error": "no remote backend was named (serve --backend-base-url)", "reason": "no-backend"}, 409


def _not_installed(rid: str, error: str) -> tuple[dict, int]:
    """Providers off: the request names something (a run no active backend owns, a build or an analysis) that only an
    installed provider could answer. Refused like a record whose provider is not active (terminal 409)."""
    from vivarium_workbench.lib.backends.records import refusal

    return refusal(rid, error, reason="backend-not-installed", phase="backend-not-installed")


def _unowned(rid: str) -> tuple[dict, int]:
    return _not_installed(rid, f"run {rid} belongs to no backend an installed provider serves in this workbench")


def _first_id(params: dict, keys: "tuple[str, ...]") -> str:
    """The first of ``keys`` whose value is a non-blank id, stripped; ``""`` when none is."""
    for key in keys:
        value = params.get(key)
        if not value:  # None, "", 0: no id (a falsy id must never reach a backend)
            continue
        text = str(value).strip()
        if text and not (text.isascii() and text.isdigit() and not text.strip("0")):  # "0", "00": no simulation, like the integer 0
            return text
    return ""


#: ``_sms_only`` answers memoised for the figure routes, which ask once per image of a gallery (seconds, not
#: minutes: a run's record changes only when it is dispatched or landed).
_SMS_ONLY_MEMO: "dict[tuple, tuple[float, tuple[dict, int] | None]]" = {}
_SMS_ONLY_TTL = 30.0


def _no_id(params: dict) -> dict:
    """``params`` without the run-id keys, for the SMS half's own "id required" answer when no usable id was given
    (a ``"0"`` must not slip past its ``if not sim_id`` check as a truthy string)."""
    return {k: v for k, v in params.items() if k not in ("run_id", "simulation_id")}


def _sms_only(ws_root: Path, sim_id: object, body: dict, *, memo: bool = False) -> "tuple[dict, int] | None":
    """For the SMS-only operations (re-run analyses, land artifacts, figures): refuse a run whose record names
    another backend (or another SMS deployment) before anything is asked; ``None`` lets the SMS path proceed.
    ``memo`` (the per-image figure route) reuses an answer for :data:`_SMS_ONLY_TTL`."""
    rid = _first_id({"simulation_id": sim_id}, ("simulation_id",))
    if not rid:  # a zero / blank id names no simulation (the callers' own checks see only the raw value)
        return {"error": "simulation_id is required"}, 400
    key = (str(ws_root), rid, body.get("deployment"), body.get("study"))
    if memo:
        hit = _SMS_ONLY_MEMO.get(key)
        if hit and time.monotonic() - hit[0] < _SMS_ONLY_TTL:
            return hit[1]
        answer = _sms_only(ws_root, sim_id, body)
        _SMS_ONLY_MEMO[key] = (time.monotonic(), answer)
        return answer
    chosen = backends.for_existing(ws_root, rid, deployment=body.get("deployment"), study=body.get("study"))
    if isinstance(chosen, tuple):
        return chosen
    if chosen is not None and chosen is not backends.SMS_LEGACY:
        from vivarium_workbench.lib.backends.records import refusal

        return refusal(rid, f"run {rid} ran on {chosen.name}; this operation exists only for SMS simulations",
                       reason="not-sms", phase="not-sms")
    if not (rid.isascii() and rid.isdigit()):
        # Opaque run ids: the record said SMS but the id is no SMS simulation id (the callers int() it next). Refused
        # like any other non-SMS run, rather than a 500 from int().
        from vivarium_workbench.lib.backends.records import refusal

        return refusal(rid, f"run {rid} is not an SMS simulation id; this operation exists only for SMS simulations",
                       reason="not-sms", phase="not-sms")
    return None


def opaque_run_id(value: object) -> str:
    """A run id from a query string, as the opaque string every route passes on (the run's record decides what it
    is, never its form). An integer spelling is normalised the way the old ``int`` query parameter parsed it
    (``" 12 "``, ``"+12"`` -> ``"12"``), so an SMS simulation id behaves exactly as before; ``0`` / blank -> ``""``
    (no id). Anything else (``"simulation-E9Ec819"``, ``"compose-7"``) is returned stripped, unchanged."""
    text = str(value if value is not None else "").strip()
    digits = text[1:] if text.startswith("+") else text
    if not (digits.isascii() and digits.isdigit()):  # not "1_2", "١٢" or "²", which int() would read or choke on
        return text
    number = int(digits)
    return str(number) if number else ""


def remote_run_land(ws_root: Path, body: dict) -> tuple[dict, int]:
    """Phase 3 (on demand): download a COMPLETED sim's store and land it as a
    study run. Returns ``({run_id}, 200)``.

    If the study has ``spec.analyses`` configured, also triggers standalone
    analysis on the same simulation before downloading, then polls its real
    status (GET /api/v1/analyses/{id}/status) so the download -- which streams
    everything under the experiment's S3 prefix -- has a real chance of
    picking up the analysis output too. If the job hasn't finished within the
    poll ceiling, the run still lands normally; the analysis just isn't
    folded in yet (landing again later will pick it up).
    """
    body = body or {}
    # Step 1c-2: the run's record picks the backend that ran it (backends.for_existing). For land, ``simulation_id``
    # names the remote run and ``run_id`` is only a fallback (in the land family ``run_id`` is the LOCAL landed run).
    rid = _first_id(body, ("simulation_id",))
    if not rid and remote_run_compose.is_run_id(body.get("run_id")):
        rid = str(body["run_id"]).strip()  # compose-api's land has always accepted its run id as run_id
    chosen = backends.for_existing(ws_root, rid, deployment=body.get("deployment"),
                                   study=body.get("study")) if rid else None
    if isinstance(chosen, tuple):
        return chosen  # the run belongs to another backend: refused, nothing asked of the configured one
    if chosen is None:
        ub = backends.unrouted()
        if ub is not None and ub.unrouted is not None:
            return ub.unrouted.land(ws_root, _no_id(body))  # no id: the backend's exact answers (401 before 400)
        return _unowned(rid) if rid else ({"error": "study and simulation_id are required"}, 400)
    return chosen.runs.land(ws_root, {**body, "simulation_id": rid})


def remote_run_land_artifacts(ws_root: Path, body: dict) -> tuple[dict, int]:
    """Land a remote run's analyses + PTools exports WITHOUT a study — the
    study-less counterpart of ``remote_run_land`` for a Runs-table remote row
    (a GovCloud run that isn't investigation-organized, so it has no study spec
    to land into). Downloads the sim's result tar and folds ``analyses.json`` +
    copies ``ptools/*.tsv`` into ``.pbg/runs/<run_id>/``, so the Analyses button
    resolves. The TSVs stay in the run folder, which the Pathway Tools viewer does
    not read (it globs study directories; the workbench never serves ``.pbg``): to
    view a run there, land it into a study (``remote_run_land``).

    This is the "land on demand" endpoint the Runs-table remote-row actions call.
    Body: ``{simulation_id, run_id}``. Idempotent.
    """
    body = body or {}
    if not sms_legacy._run_auth_ok():
        return {"error": "not authenticated"}, 401
    sim_id = body.get("simulation_id")
    run_id = (body.get("run_id") or "").strip()
    if not sim_id or not run_id:
        return {"error": "simulation_id and run_id are required"}, 400
    refused = _sms_only(ws_root, sim_id, body)  # step 1c-2: never another backend's run
    if refused is not None:
        return refused
    from vivarium_workbench.lib.remote_run_landing import land_remote_simulation_artifacts

    client = _sms_client()
    try:
        result = land_remote_simulation_artifacts(ws_root, int(sim_id), run_id, client=client)
    except sms_legacy.SmsApiError as e:
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
    if not sms_legacy._run_auth_ok():
        return {"error": "not authenticated"}, 401
    sim_id = body.get("simulation_id")
    if not sim_id:
        return {"error": "simulation_id is required"}, 400
    refused = _sms_only(ws_root, sim_id, body)  # step 1c-2: never another backend's run
    if refused is not None:
        return refused

    modules = body.get("modules") or {}
    study = (body.get("study") or "").strip()
    analysis_errors: list[dict] = []
    if not modules and study:
        spec_path = study_spec.study_spec_path(ws_root, study)
        if spec_path is None or not spec_path.is_file():
            return {"error": f"study {study!r} not found"}, 404
        from vivarium_workbench.lib.study_run_post import build_analysis_options

        modules, analysis_errors = build_analysis_options(sms_legacy.load_spec(spec_path).get("analyses") or [], ws_root)
        for err in analysis_errors:
            warnings.warn(f"remote_run_analysis: {study!r} analysis config: {err.get('error')}")

    client = _sms_client()
    try:
        triggered = client.run_analysis(int(sim_id), modules)
    except sms_legacy.SmsApiError as e:
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


def remote_run_status(params: dict, *, ws_root: Path) -> tuple[dict, int]:
    """On-demand status read from sms-api (NO in-process state). The JS panel
    polls this per phase. Pass ``simulation_id`` (run phase), ``simulator_id``
    (build phase) or ``analysis_id`` (analysis phase). Maps the raw sms-api
    status into a UI ``phase``."""
    params = params or {}
    # Step 1c-2: the run's id (``run_id``, else ``simulation_id``) is looked up in its record, which names the
    # backend that ran it. A run of a backend with an ``unrouted`` facet (an SMS simulation), or no run id, goes to
    # that facet with the build/analysis ids it may carry (their precedence there is unchanged: analysis, then
    # simulation, then simulator) -- ``runs.status(rid)`` would drop them.
    rid = _first_id(params, ("run_id", "simulation_id"))
    chosen = backends.for_existing(ws_root, rid, deployment=params.get("deployment"),
                                   study=params.get("study")) if rid else None
    if isinstance(chosen, tuple):
        return chosen
    if chosen is not None and chosen.unrouted is None:
        return chosen.runs.status(rid)
    ub = backends.unrouted()
    if ub is not None and ub.unrouted is not None:
        return ub.unrouted.status({**params, "simulation_id": rid} if rid else _no_id(params))
    # Providers off.
    if rid:
        return _unowned(rid)
    if params.get("analysis_id"):
        return _not_installed("", "an analysis id names an SMS analysis, and no installed provider serves analyses "
                                  "in this workbench")
    if params.get("simulator_id"):
        return _not_installed("", "a simulator id names a build, and no installed provider serves builds in this "
                                  "workbench")
    return {"error": "simulator_id, simulation_id or analysis_id required"}, 400


def remote_run_chain_progress(params: dict, *, ws_root: Path) -> tuple[dict, int]:
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
    rid = _first_id(params, ("run_id", "simulation_id"))  # step 1c-2: the run's record picks the backend
    chosen = backends.for_existing(ws_root, rid, deployment=params.get("deployment"),
                                   study=params.get("study")) if rid else None
    if isinstance(chosen, tuple):
        return chosen
    if chosen is None:
        ub = backends.unrouted()
        if ub is not None and ub.unrouted is not None:
            return ub.unrouted.progress(_no_id(params))  # no id: the backend's own 400
        return _unowned(rid) if rid else ({"error": "simulation_id required"}, 400)
    return chosen.runs.progress(rid)


def remote_run_cancel(body: dict, *, ws_root: Path) -> tuple[dict, int]:
    """Backlog item 53: cancel a real remote simulation/campaign, proxying
    viva-api's ``DELETE /api/v1/simulations/{id}/cancel`` (``SmsApiClient.
    cancel_simulation``) the same way ``remote_run_chain_progress`` proxies
    ``chain-progress`` — on-demand, no in-process state.

    For a chain-dispatch campaign row, viva-api's own handler walks every
    seed's own dependsOn chain and cancels/terminates whichever job is
    actually non-terminal per seed (see that handler's own docstring/item 53's
    backlog file for the full design) — this function does no walk-back logic
    itself, it is a thin proxy, same division of responsibility as every other
    function in this file.

    ``simulation_id`` required in ``body``. 404 (unknown simulation) passes
    through distinctly, matching ``remote_run_chain_progress``'s own
    convention of not collapsing every viva-api error into one generic
    message."""
    body = body or {}
    rid = _first_id(body, ("run_id", "simulation_id"))  # step 1c-2: the run's record picks the backend
    chosen = backends.for_existing(ws_root, rid, deployment=body.get("deployment"),
                                   study=body.get("study")) if rid else None
    if isinstance(chosen, tuple):
        return chosen
    if chosen is None:
        ub = backends.unrouted()
        if ub is not None and ub.unrouted is not None:
            return ub.unrouted.cancel(_no_id(body))  # no id: the backend's own 400
        return _unowned(rid) if rid else ({"error": "simulation_id required"}, 400)
    return chosen.runs.cancel(rid)
