"""The frozen SMS ``/api/v1`` run path: integer simulation ids (docs/backend-adapters.md §2.5).

Moved verbatim from ``remote_run_views`` in step 1a: the legacy tails of ``remote_run_land``,
``remote_run_status``, ``remote_run_chain_progress`` and ``remote_run_cancel``, plus their two helpers;
step 1b added the SMS half of ``remote_run_submit``. Each
view keeps its own routing and calls the matching ``*_tail`` here once it has decided the request is SMS's
(through ``runs``, or ``unrouted`` for a request that names no run: :class:`SmsLegacyUnrouted`).

Step P2 (Runs a1) moved the SMS constants and the login check (``_run_auth_ok``) here from ``remote_run_views``. The
seams tests patch (``SmsApiClient``, ``SmsApiError``, ``_sms_api_base``, ``load_spec``, ``_run_auth_ok``,
``land_remote_run``) are this module's own globals, read at call time; the SMS code still in ``remote_run_views``
reads them through this module too, so each has exactly one patch target. It is deleted only if the plugin itself
leaves ``/api/v1`` (out of scope, Appendix A).
"""

from __future__ import annotations

import tempfile
import time
import warnings
from pathlib import Path

from vivarium_workbench.lib import github_auth, study_spec
from vivarium_workbench.lib.investigations import load_spec
from vivarium_workbench.lib.remote_run_landing import land_remote_run
from vivarium_workbench_sms import remote_pinned, sms_api_client
from vivarium_workbench.lib.backends.records import remote_record as _remote_record
from vivarium_workbench_sms.sms_api_client import DOWNLOAD_TIMEOUT, SmsApiClient, SmsApiError

# sms-api JobStatus terminal sets (relocated here from remote_run_jobs, which R5
# deletes). The thin client maps a raw sms-api status into a UI phase.
#
# These mirror sms-api's ACTUAL JobStatus vocabulary, which is
# COMPLETED / FAILED / CANCELLED plus the non-terminal states. A "partial"
# entry was added defensively in #1045 for viva-api#609's then-planned PARTIAL
# status and kept in #1046 on the grounds that it cost nothing; it is removed
# here because viva-api dropped that status before merging, so the value can
# never arrive, and a set that lists states the API cannot produce invites the
# reader to believe it can. If a partial-run status ever genuinely lands, add
# it back together with the sms-api change that produces it.
# "ready" is an environment's terminal-OK status (/viva/v1/environments, W2):
# without it a build poll against a W2 server never ends.
_TERMINAL_OK = {"completed", "done", "succeeded", "ready"}
_TERMINAL_BAD = {"failed", "cancelled", "error"}

# Real completion signal now exists (GET /api/v1/analyses/{id}/status, S3-exists
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


def _sms_api_base() -> str:
    """Base URL of the viva-api (nee sms-api): :func:`sms_api_client.sms_api_base`, read through its module at call
    time (tests patch it there, or this function here)."""
    return sms_api_client.sms_api_base()


def _run_auth_ok() -> bool:
    """Gate for submit/land. A GitHub session satisfies it (stock build-first
    flow). Pinned mode ALSO satisfies it: those calls push nothing to GitHub, so
    requiring a human token would be neither production-grade nor reproducible —
    the operator authorizes remote runs declaratively by enabling pinned mode."""
    return (
        github_auth.current_session() is not None or remote_pinned.is_pinned_enabled()
    )


def _poll_analysis_until_terminal(client: SmsApiClient, database_id: int) -> None:
    """Poll GET /api/v1/analyses/{id}/status until COMPLETED/FAILED or the attempt
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


def land_tail(ws_root: Path, body: dict) -> tuple[dict, int]:
    """The SMS half of ``remote_run_views.remote_run_land``, moved verbatim."""
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
            base_url=getattr(client, "base_url", None),
        )
    response: dict = {"run_id": run_id}
    if analysis_errors:
        # backlog item 39: surface which requested analyses couldn't be
        # resolved (e.g. a stale workspace PVC missing a newer v2ecoli
        # analysis) instead of silently dropping them -- landing itself
        # still succeeds, this is informational.
        response["analysis_errors"] = analysis_errors
    return response, 200


def status_tail(params: dict) -> tuple[dict, int]:
    """The SMS half of ``remote_run_views.status``, moved verbatim."""
    sim_id = params.get("simulation_id")
    sm_id = params.get("simulator_id")
    an_id = params.get("analysis_id")
    if not sim_id and not sm_id and not an_id:
        return {"error": "simulator_id, simulation_id or analysis_id required"}, 400
    client = SmsApiClient(_sms_api_base())
    try:
        if an_id:
            # GET /api/v1/analyses/{id}/status resolves against the job's own S3
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


#: viva-api ``ComposeJobStatus`` values that mean the run is over (moved verbatim from ``run_jobs``, step P2 Runs c2).
#: Anything not listed — waiting/queued/running/pending/suspended, or an absent
#: status — leaves the item ``submitted``: an unknown state is not a finished one.
_UPSTREAM_DONE = frozenset({"completed"})
_UPSTREAM_FAILED = frozenset({"failed", "cancelled", "out_of_memory", "timeout"})


def status_batch(run_ids: "list[str]") -> "dict[str, dict]":
    """The SMS half of ``run_jobs.refresh_submitted`` (moved from its ``_refresh_sms``, step P2 Runs c2): the status
    of several integer simulations in ONE ``compose_status_batch`` call, as ``runs.status``-shaped bodies keyed by
    the id's text (:class:`vivarium_workbench.lib.backends.BatchStatus`). An id viva-api did not answer is absent.
    Raises when viva-api does not answer, or answers a status that is not text (the caller keeps the items
    ``submitted``)."""
    rows = SmsApiClient(_sms_api_base()).compose_status_batch([int(r) for r in run_ids])
    out: "dict[str, dict]" = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        upstream = (row.get("status") or "").lower()
        phase = "done" if upstream in _UPSTREAM_DONE else "failed" if upstream in _UPSTREAM_FAILED else "running"
        out[str(row.get("sim_id"))] = {"phase": phase, "raw_status": upstream, "error": row.get("error_message")}
    return out


def chain_progress_tail(params: dict) -> tuple[dict, int]:
    """The SMS half of ``remote_run_views.chain_progress``, moved verbatim."""
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


def cancel_tail(body: dict) -> tuple[dict, int]:
    """The SMS half of ``remote_run_views.cancel``, moved verbatim."""
    sim_id = body.get("simulation_id")
    if not sim_id:
        return {"error": "simulation_id required"}, 400
    client = SmsApiClient(_sms_api_base())
    try:
        result = client.cancel_simulation(int(sim_id))
        return {"kind": "cancel", "simulation_id": int(sim_id), **result}, 200
    except SmsApiError as e:
        msg = str(e)
        if msg.endswith("-> 404"):
            return {"kind": "cancel", "error": msg, "simulation_id": int(sim_id)}, 404
        return {
            "kind": "cancel", "reachable": False,
            "reason": "sms-api unreachable (is the tunnel up?)", "error": msg,
            "simulation_id": int(sim_id),
        }, 502


def submit_tail(ws_root: Path, body: dict) -> tuple[dict, int]:
    """The SMS half of ``remote_run_views.remote_run_submit``, moved verbatim (step 1b)."""
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
    # sms-launch.js:_dispatchRemotePinned, which blocks the dispatch before
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
            config_filename=(body.get("config_filename") or "").strip() or None,
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
                    # step 1c: where this run went, for every later operation on it (lib/backends/records.py)
                    "remote": _remote_record("sms-legacy", sim["database_id"],
                                             getattr(client, "base_url", None) or _sms_api_base()),
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


class SmsLegacyRuns:
    """The run facet over the tails above. Only integer ids are SMS's."""

    name = "sms-legacy"

    def owns(self, run_id: object) -> bool:
        text = str(run_id or "").strip()
        return text.isascii() and text.isdigit()  # ASCII only: int() accepts "١٢", and "²".isdigit() is True; falsy ids are refused by every tail

    def submit(self, ws_root: Path, body: dict) -> tuple[dict, int]:
        return submit_tail(ws_root, body)

    def land(self, ws_root: Path, body: dict) -> tuple[dict, int]:
        return land_tail(ws_root, body)

    def status(self, run_id: str) -> tuple[dict, int]:
        return status_tail({"simulation_id": run_id})

    def status_many(self, run_ids: "list[str]") -> "dict[str, dict]":
        """The optional batch status (:class:`vivarium_workbench.lib.backends.BatchStatus`): one viva-api call."""
        return status_batch(run_ids)

    def progress(self, run_id: str) -> tuple[dict, int]:
        return chain_progress_tail({"simulation_id": run_id})

    def cancel(self, run_id: str) -> tuple[dict, int]:
        return cancel_tail({"simulation_id": run_id})

    def trace(self, run_id: str) -> "tuple[bytes | dict, int, str]":
        raise NotImplementedError("SMS simulation traces are served by remote_trace.fetch_trace")

    def run_composite(self, ws_root: Path, composite_id: str, *, dest: Path, n_steps: float,
                      overrides: "dict | None", emit_paths: "list[str] | None",
                      analysis_options: "dict | None", build_ref: "dict | None",
                      poll_interval: float, poll_timeout: float) -> Path:
        """The SMS deployment run on ``/compose/v1`` (:func:`deployment_run.run`; step P2, Runs b)."""
        from vivarium_workbench_sms import deployment_run  # lazy: loading the registry (env workers) skips it

        return deployment_run.run(ws_root, composite_id, dest=dest, n_steps=n_steps, overrides=overrides,
                                  poll_interval=poll_interval, poll_timeout=poll_timeout,
                                  analysis_options=analysis_options, build_ref=build_ref, emit_paths=emit_paths)


class SmsLegacyUnrouted:
    """The ``unrouted`` facet (step P2, Runs a1): the tails above for the lifecycle requests the core cannot route by
    a run's record (no run id, or a build / analysis id riding along). Their own login check runs first."""

    def submit(self, ws_root: Path, body: dict) -> tuple[dict, int]:
        return submit_tail(ws_root, body)

    def land(self, ws_root: Path, body: dict) -> tuple[dict, int]:
        return land_tail(ws_root, body)

    def status(self, params: dict) -> tuple[dict, int]:
        return status_tail(params)

    def progress(self, params: dict) -> tuple[dict, int]:
        return chain_progress_tail(params)

    def cancel(self, params: dict) -> tuple[dict, int]:
        return cancel_tail(params)

    def run_composite(self, ws_root: Path, composite_id: str, *, dest: Path, n_steps: float,
                      overrides: "dict | None", emit_paths: "list[str] | None",
                      analysis_options: "dict | None", build_ref: "dict | None",
                      poll_interval: float, poll_timeout: float) -> Path:
        """A deployment run no backend selected (the opt-in compose path): SMS's ``/compose/v1``, as before
        step P2 (Runs b)."""
        from vivarium_workbench_sms import deployment_run  # lazy: loading the registry (env workers) skips it

        return deployment_run.run(ws_root, composite_id, dest=dest, n_steps=n_steps, overrides=overrides,
                                  poll_interval=poll_interval, poll_timeout=poll_timeout,
                                  analysis_options=analysis_options, build_ref=build_ref, emit_paths=emit_paths)
