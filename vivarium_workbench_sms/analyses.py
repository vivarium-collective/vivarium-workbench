"""SMS's ``artifacts`` and ``analyses`` facets (docs/backend-adapters.md §2.1, step P2 "Analyses and figures").

The core keeps the routes and routes each request (``remote_run_views``): by the run's record when it names a run,
to the ``unrouted`` backend's facet when a land-artifacts or re-run request names none, and to every backend that
offers ``artifacts`` for a study's gallery. What is SMS's is here:

* the login check, the "required" 400s and the integer-id check, which used to run in the core views;
* the re-run's tail (``remote_run_views.remote_run_analysis``'s SMS half, moved verbatim);
* the figure reads over :mod:`remote_analysis_figures` and the study gallery;
* land-artifacts calls :func:`sms_legacy.land_artifacts_tail` (the store landing is ``sms_landing``'s, Runs c1);
* the store fetch (``fetch_store``, Remote tab R2) calls :func:`sms_legacy.fetch_store_tail`.

The seams tests patch (``SmsApiClient``, ``_sms_api_base``, ``_run_auth_ok``, ``SmsApiError``, ``load_spec``, and
``remote_analysis_figures``' functions) are read through their modules at call time and bound nowhere here, so each
has one patch target.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Optional, Union

from vivarium_workbench.lib import study_spec
from vivarium_workbench.lib.backends.records import refusal
from vivarium_workbench_sms import remote_analysis_figures, sms_legacy


def _client() -> "sms_legacy.SmsApiClient":
    return sms_legacy.SmsApiClient(sms_legacy._sms_api_base())


def _not_an_sms_id(rid: object) -> "Optional[tuple[dict, int]]":
    """Opaque run ids: a record (or, with none, the id's form) said SMS, but the id is no SMS simulation id (the calls
    below ``int()`` it). Refused like any other non-SMS run, rather than a 500 from ``int()``. Moved verbatim from the
    core's ``_sms_only``."""
    text = str(rid).strip()
    if not (text.isascii() and text.isdigit()):
        return refusal(text, f"run {text} is not an SMS simulation id; this operation exists only for SMS simulations",
                       reason="not-sms", phase="not-sms")
    return None


def land_outputs_tail(ws_root: Path, body: dict) -> tuple[dict, int]:
    """``remote_run_views.remote_run_land_artifacts``'s SMS checks, moved verbatim (login, then the 400), then the
    integer-id check and :func:`sms_legacy.land_artifacts_tail`."""
    if not sms_legacy._run_auth_ok():
        return {"error": "not authenticated"}, 401
    sim_id = body.get("simulation_id")
    run_id = str(body.get("run_id") or "").strip()
    if not sim_id or not run_id:
        return {"error": "simulation_id and run_id are required"}, 400
    refused = _not_an_sms_id(sim_id)
    if refused is not None:
        return refused
    return sms_legacy.land_artifacts_tail(ws_root, sim_id, run_id)


def rerun_tail(ws_root: Path, body: dict) -> tuple[dict, int]:
    """The SMS half of ``remote_run_views.remote_run_analysis``, moved verbatim (its docstring stays with the core
    view): fire the analysis phase on an existing, completed simulation. ``modules`` comes from the body, else the
    named study's ``spec.analyses`` (``study_run_post.build_analysis_options``), else nothing (viva-api resolves the
    simulation's own). Returns ``({analysis_id, analysis_name, simulation_id, phase}, 202)``."""
    if not sms_legacy._run_auth_ok():
        return {"error": "not authenticated"}, 401
    sim_id = body.get("simulation_id")
    if not sim_id:
        return {"error": "simulation_id is required"}, 400
    refused = _not_an_sms_id(sim_id)
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

    client = _client()
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


class SmsArtifacts:
    """The ``artifacts`` facet (:class:`vivarium_workbench.lib.backends.ArtifactFacet`) of ``sms-legacy``."""

    def land_outputs(self, ws_root: Path, body: dict) -> tuple[dict, int]:
        return land_outputs_tail(ws_root, body)

    def list_figures(self, run_id: str) -> tuple[dict, int]:
        refused = _not_an_sms_id(run_id)
        if refused is not None:
            return refused
        return remote_analysis_figures.list_remote_analysis_figures(_client(), int(run_id)), 200

    def read_figure(self, run_id: str, analysis: str, path: str) -> "tuple[Union[bytes, dict], int, str]":
        refused = _not_an_sms_id(run_id)
        if refused is not None:
            return refused[0], refused[1], "application/json"
        got = remote_analysis_figures.fetch_by_analysis(_client(), int(run_id), analysis, path)
        if not got:
            return {"error": "not found"}, 404, "application/json"
        content, content_type = got
        return content, 200, content_type

    def study_figures(self, ws_root: Path, study: str, max_runs: int) -> tuple[dict, int]:
        return remote_analysis_figures.study_remote_figures(ws_root, _client(), study, max_sims=max_runs), 200

    def fetch_store(self, run_id: str, dest: Path) -> Optional[Path]:
        return sms_legacy.fetch_store_tail(run_id, dest)


class SmsAnalyses:
    """The ``analyses`` facet (:class:`vivarium_workbench.lib.backends.AnalysisFacet`) of ``sms-legacy``."""

    def rerun(self, ws_root: Path, body: dict) -> tuple[dict, int]:
        return rerun_tail(ws_root, body)
