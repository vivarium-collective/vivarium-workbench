"""Pure builders for the remote-run lifecycle routes (submit, poll, land, cancel, chain progress) and the analysis-output
and re-run routers (land-artifacts, re-run, figures, the study gallery). The pin ``/api/remote-run-config`` reports is
the selected backend's ``builds.pin`` (step P2, Builds B3).

Each view returns ``(body, status)``; the FastAPI route wraps every path in ``JSONResponse``. This module imports no
plugin module: every SMS answer comes through a backend's facets (step P2: Runs, Analyses, Builds B3). The lifecycle requests no run record routes go to the
backend's ``unrouted`` facet (``backends.unrouted()``); with providers off the core answers them itself.

The SMS-only views (the legacy pipeline submit and the thin client's build phase) and their routes moved to the
plugin in step P2 Runs a2 (``vivarium_workbench_sms.remote_pipeline_views``, served by ``Provider.router``). The
analysis-output and re-run requests go to the run's backend's optional ``artifacts`` / ``analyses`` facets (step P2,
Analyses and figures; SMS's are ``vivarium_workbench_sms.analyses``); a backend without the facet refuses them.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Union

from vivarium_workbench.lib import backends, path_safety, remote_run_compose
from vivarium_workbench.lib.backends import config as backend_cfg
import vivarium_workbench.lib.backends.records as records

if TYPE_CHECKING:
    from vivarium_workbench.lib.backends import AnalysisFacet, ArtifactFacet, Backend


def remote_run_config(ws_root: Path) -> tuple[dict, int]:
    """Report pinned-run config for the client to relabel the run card.

    ``{"pinned": false}`` when off; ``{"pinned": true, "repo_url", "branch",
    "commit"?, "simulator_id"?}`` when on. Both carry ``deployment`` — the
    config-derived Origin name (``VIVARIUM_WORKBENCH_REMOTE_DEPLOYMENT``) so the
    run form's origin selector labels "Remote:<deployment>" truthfully instead of
    a hardcoded "smsvpctest". Resolving the build is best-effort — a missing build
    or unreachable sms-api degrades to ``build_error`` rather than failing the card.

    The pin is the selected backend's (``builds.pin``, step P2 Builds B3: the session's own build wins over the
    deployment's static pin there); a backend without builds, or none selected, reports ``{"pinned": false}`` with
    the configured deployment name and asks nothing."""
    # The profile first: reading it is how a named compose-api becomes known as one, and the pinned build below is
    # looked up on the SMS host only once it is.
    backend = _backend_info()
    chosen = backends.select(ws_root)
    if chosen is not None and chosen.builds is not None:
        body = chosen.builds.pin(ws_root)
    else:
        body = {"pinned": False, "deployment": backend_cfg.deployment_name()}
    if backend is not None:
        body["backend"] = backend
    return body, 200


def _backend_info() -> "dict | None":
    """The explicitly named backend's negotiated profile for the UI; ``None`` (no key at all,
    no network call) when the operator named none, so the payload is byte-identical to before."""
    from vivarium_workbench.lib.server_capabilities import explicit_backend_profile

    return explicit_backend_profile()


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
        return records.not_installed("", "a simulator id names a build, and no installed provider serves builds in this "
                                         "workbench")
    return {"error": "no remote backend was named (serve --backend-base-url)", "reason": "no-backend"}, 409


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


#: ``_artifacts_of`` answers memoised for the per-image figure route, which asks once per image of a gallery (seconds,
#: not minutes: a run's record changes only when it is dispatched or landed).
_FACET_MEMO: "dict[tuple, tuple[float, Union[ArtifactFacet, tuple[dict, int]]]]" = {}
_FACET_TTL = 30.0


def _no_id(params: dict) -> dict:
    """``params`` without the run-id keys, for the SMS half's own "id required" answer when no usable id was given
    (a ``"0"`` must not slip past its ``if not sim_id`` check as a truthy string)."""
    return {k: v for k, v in params.items() if k not in ("run_id", "simulation_id")}


def _owner(ws_root: Path, rid: str, where: dict) -> "Union[Backend, tuple[dict, int]]":
    """The backend whose record (or, with none, whose id form) owns ``rid`` (``backends.for_existing``), or the
    refusal: another backend's or deployment's run, or (providers off) one no installed provider serves."""
    chosen = backends.for_existing(ws_root, rid, deployment=where.get("deployment"), study=where.get("study"))
    if isinstance(chosen, tuple):
        return chosen
    return records.unowned(rid) if chosen is None else chosen


def _not_offered(rid: str, backend: "Backend", what: str) -> tuple[dict, int]:
    from vivarium_workbench.lib.backends.records import refusal

    # "not-offered" (P3 I1; "not-sms" before it): callers read it only as "refused, terminal".
    return refusal(rid, f"run {rid} ran on {backend.name}, which does not offer {what}",
                   reason="not-offered", phase="not-offered")


def _artifacts_of(ws_root: Path, rid: str, where: dict, *,
                  memo: bool = False) -> "Union[ArtifactFacet, tuple[dict, int]]":
    """The ``artifacts`` facet of the backend that ran ``rid``, or the refusal (``_owner``'s, or 409 ``not-offered`` when
    that backend offers none). ``memo`` (the per-image figure route) reuses an answer for :data:`_FACET_TTL`."""
    key = (str(ws_root), rid, where.get("deployment"), where.get("study"))
    if memo:
        hit = _FACET_MEMO.get(key)
        if hit and time.monotonic() - hit[0] < _FACET_TTL:
            return hit[1]
    chosen = _owner(ws_root, rid, where)
    answer: "Union[ArtifactFacet, tuple[dict, int]]"
    if isinstance(chosen, tuple):
        answer = chosen
    elif chosen.artifacts is None:
        answer = _not_offered(rid, chosen, "analysis outputs (land, figures)")
    else:
        answer = chosen.artifacts
    if memo:
        _FACET_MEMO[key] = (time.monotonic(), answer)
    return answer


def _analyses_of(ws_root: Path, rid: str, where: dict) -> "Union[AnalysisFacet, tuple[dict, int]]":
    """The ``analyses`` facet of the backend that ran ``rid``, or the refusal (as :func:`_artifacts_of`)."""
    chosen = _owner(ws_root, rid, where)
    if isinstance(chosen, tuple):
        return chosen
    return _not_offered(rid, chosen, "analysis re-runs") if chosen.analyses is None else chosen.analyses


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
        return records.unowned(rid) if rid else ({"error": "study and simulation_id are required"}, 400)
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

    Step P2 (Analyses and figures): the run's record picks its backend, whose ``artifacts`` facet lands it (a backend
    without one refuses, 409 ``not-offered``). A request that names no run goes to the ``unrouted`` backend's facet, so
    its own answers come first (SMS: 401 before 400); with providers off the core answers the 400 itself.
    """
    body = body or {}
    rid = _first_id(body, ("simulation_id",))
    run_id = str(body.get("run_id") or "").strip()
    if not rid or not run_id:
        ub = backends.unrouted()
        if ub is not None and ub.artifacts is not None:
            # Only a dead simulation_id ("0", blank) is dropped, so the facet's own "required" check sees it missing;
            # not _no_id, which would also drop run_id -- here the LOCAL landing folder, not a run id.
            return ub.artifacts.land_outputs(ws_root, body if rid else {k: v for k, v in body.items()
                                                                        if k != "simulation_id"})
        return {"error": "simulation_id and run_id are required"}, 400
    if not path_safety.is_plain_name(run_id):  # it becomes .pbg/runs/<run_id> (the app-wide guard covers routes only)
        return {"error": "invalid run_id: it must be a plain name, not a path"}, 400
    facet = _artifacts_of(ws_root, rid, body)
    if isinstance(facet, tuple):
        return facet
    return facet.land_outputs(ws_root, {**body, "simulation_id": rid, "run_id": run_id})


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

    Step P2 (Analyses and figures): the run's record picks its backend, whose ``analyses`` facet runs it (SMS:
    ``vivarium_workbench_sms.analyses.rerun_tail``); a backend without one refuses (409 ``not-offered``). A request that
    names no run goes to the ``unrouted`` backend's facet (its 401 before its 400); providers off, the core's 400.
    """
    body = body or {}
    rid = _first_id(body, ("simulation_id",))
    if not rid:
        ub = backends.unrouted()
        if ub is not None and ub.analyses is not None:
            return ub.analyses.rerun(ws_root, {k: v for k, v in body.items() if k != "simulation_id"})
        return {"error": "simulation_id is required"}, 400
    facet = _analyses_of(ws_root, rid, body)
    if isinstance(facet, tuple):
        return facet
    return facet.rerun(ws_root, {**body, "simulation_id": rid})


def analysis_figures(ws_root: Path, simulation_id: object, *, deployment: "str | None" = None,
                     study: "str | None" = None) -> tuple[dict, int]:
    """``GET /api/remote-analysis-figures``: the run's figures and PTools tables, from its backend's ``artifacts``
    facet (``{available, reason, via, analyses}``). The id is opaque (:func:`opaque_run_id`); its record decides."""
    rid = opaque_run_id(simulation_id)
    if not rid:
        return {"error": "simulation_id required"}, 400
    facet = _artifacts_of(ws_root, rid, {"deployment": deployment, "study": study})
    if isinstance(facet, tuple):
        return facet
    return facet.list_figures(rid)


def analysis_figure(ws_root: Path, simulation_id: object, analysis: str, path: str, *,
                    deployment: "str | None" = None,
                    study: "str | None" = None) -> "tuple[Union[bytes, dict], int, str]":
    """``GET /api/remote-analysis-figure``: one figure or PTools file, ``(bytes | dict, status, content_type)``.
    Asked once per image of a gallery, so the routing answer is memoised (:data:`_FACET_TTL`)."""
    rid = opaque_run_id(simulation_id)
    if not rid or not analysis or not path:
        return b'{"error":"simulation_id, analysis, path required"}', 400, "application/json"
    facet = _artifacts_of(ws_root, rid, {"deployment": deployment, "study": study}, memo=True)
    if isinstance(facet, tuple):
        return facet[0], facet[1], "application/json"
    return facet.read_figure(rid, analysis, path)


def study_figures(ws_root: Path, study: str, limit: "int | None") -> tuple[dict, int]:
    """``GET /api/study-remote-figures``: a study's gallery of remote figures over at most ``limit`` runs (1..30,
    default 10). It names no run, so every backend that offers ``artifacts`` is asked (``backends.offering``): the
    first ``available`` answer wins, else the first answer. Providers off: an empty gallery (``reason:
    "no-backend"``)."""
    if not study:
        return {"error": "study required"}, 400
    max_runs = max(1, min(int(limit or 10), 30))
    first: "tuple[dict, int] | None" = None
    for backend in backends.offering("artifacts"):
        if backend.artifacts is None:  # offering() returns only backends that declare it
            continue
        body, status = backend.artifacts.study_figures(ws_root, study, max_runs)
        if status == 200 and body.get("available"):
            return body, status
        first = first or (body, status)
    return first or ({"available": False, "reason": "no-backend", "study": study, "sims": []}, 200)


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
        return records.unowned(rid)
    if params.get("analysis_id"):
        return records.not_installed("", "an analysis id names an SMS analysis, and no installed provider serves analyses "
                                         "in this workbench")
    if params.get("simulator_id"):
        return records.not_installed("", "a simulator id names a build, and no installed provider serves builds in this "
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
        return records.unowned(rid) if rid else ({"error": "simulation_id required"}, 400)
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
        return records.unowned(rid) if rid else ({"error": "simulation_id required"}, 400)
    return chosen.runs.cancel(rid)
