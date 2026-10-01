"""The study-run remote routes on ``/viva/v1/composites`` (contract: docs/backend-viva-v1.md).

Used only when the operator named a backend (``serve --backend-base-url``) that advertises the
document run surface and neither a session build nor the pinned config chose the simulator-keyed
path (:func:`remote_pinned.uses_viva_v1_dispatch`). Every function mirrors the shape the legacy
``remote_run_views`` route answers, so the existing panel/poll code is unchanged; ids are the
backend's opaque run ids (strings), carried as ``run_id``.
"""

from __future__ import annotations

import json
import re
import tempfile
import time
import warnings
import zipfile
from pathlib import Path

from vivarium_workbench.lib import remote_pinned, study_spec
from vivarium_workbench.lib.investigations import load_spec
from vivarium_workbench.lib.sms_api_client import DOWNLOAD_TIMEOUT, SmsApiClient, SmsApiError
from vivarium_workbench.lib.workspace_deps_views import _sms_api_base

_TERMINAL_OK = {"completed"}
_TERMINAL_BAD = {"failed", "cancelled"}


def is_run_id(value: object) -> bool:
    """A /viva/v1 run id (``simulation-D55FF7B``) as opposed to a legacy integer id."""
    return bool(str(value or "").strip()) and not str(value).strip().isdigit()


def _client() -> SmsApiClient:
    return SmsApiClient(_sms_api_base())


def submit(ws_root: Path, body: dict) -> tuple[dict, int]:
    """``POST /viva/v1/composites`` for a study's composite. ``({run_id, phase: "running",
    backend: "viva-v1"}, 202)``; 409 when the workspace cannot be shipped (dirty / unpushed),
    502 when the backend call itself fails (never retried: a retry could double-spend)."""
    from vivarium_workbench.lib import remote_run
    from vivarium_workbench.lib.pbg_export import export_composite_pbg

    study = (body.get("study") or "").strip()
    if not study:
        return {"error": "study is required"}, 400
    spec_path = study_spec.study_spec_path(ws_root, study)
    if spec_path is None or not spec_path.is_file():
        return {"error": f"study {study!r} not found"}, 404
    spec = load_spec(spec_path)
    baseline = (spec.get("baseline") or [{}])[0]
    composite_id = (body.get("composite") or baseline.get("composite") or "").strip()
    if not composite_id:
        return {"error": f"study {study!r} declares no baseline composite"}, 400
    params = dict(body["params"] if body.get("params") is not None else (baseline.get("params") or {}))
    n_steps = int(body.get("n_steps") or params.get("n_steps") or 1)
    steps = max(0, min(n_steps, 1000))

    environment = remote_run.backend_environment()
    extra_pip_deps = None
    if "name" in environment:
        # A named environment is one container: the workspace arrives as a pip dependency.
        pf = remote_run.remote_dispatch_preflight(ws_root)
        if not pf.get("ok"):
            return {"error": pf.get("message"), "reason": pf.get("reason"), "preflight": pf}, 409
        extra_pip_deps = [remote_run.git_pip_url(ws_root), *remote_run.workspace_pinned_deps(ws_root)]

    analysis_options = None
    if "id" in environment:
        from vivarium_workbench.lib.study_run_post import build_analysis_options

        analysis_options, errors = build_analysis_options(spec.get("analyses") or [], ws_root)
        for err in errors:
            warnings.warn(f"remote_run_submit: {study!r} analysis config: {err.get('error')}")

    try:
        with tempfile.TemporaryDirectory() as td:
            pbg = Path(td) / "composite.pbg"
            export_composite_pbg(ws_root, composite_id, pbg, overrides=params)
            document = json.loads(pbg.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 - the export's own reason (an unregistered process, a missing dep) IS the answer
        return {"error": f"could not export composite {composite_id!r} as a document: {e}"}, 422
    try:
        run = _client().create_composite_run(
            environment=environment,
            document=document,
            execution={"options": remote_run.viva_v1_document_options(
                environment, steps, extra_pip_deps, analysis_options or None)},
            label=f"workbench: {study}",
        )
    except SmsApiError as e:
        refused = _not_allow_listed(e)
        if refused is not None:
            return refused
        return {"error": str(e), "reachable": False}, 502
    _record_pending(ws_root, study, composite_id, run["id"])
    return {"run_id": run["id"], "phase": "running", "backend": "viva-v1"}, 202


def land(ws_root: Path, body: dict) -> tuple[dict, int]:
    """Land a finished run's results as a study run: ``({run_id}, 200)``, the shape the legacy route answers.

    The run's record (``GET /viva/v1/composites/{id}``) says whether it finished and which compose job it
    became; the archive comes from the compose simulation that job is (:func:`remote_run.compose_simulation_id`
    -- this deployment has no dataset store, which is where a run's outputs would otherwise be read), and its
    emitter history lands in the study's run store (:func:`remote_run_landing.land_composite_results`).
    Provenance is the backend's record of the run -- what it installed, which simulation, which job -- never
    the local checkout. 409 for a run that has not completed (nothing is downloaded), 404 when the backend
    does not know the run or lists no simulation for it, 422 for an archive that is not a run's emitter
    history, 502 when the backend cannot be reached.
    """
    from vivarium_workbench.lib import composite_runs as cr
    from vivarium_workbench.lib import remote_run
    from vivarium_workbench.lib.remote_run_landing import land_composite_results

    study = (body.get("study") or "").strip()
    run_id = str(body.get("simulation_id") or "").strip()
    if not study or not run_id:
        return {"error": "study and simulation_id are required"}, 400
    spec_path = study_spec.study_spec_path(ws_root, study)
    if spec_path is None or not spec_path.is_file():
        return {"error": f"study {study!r} not found"}, 404
    baseline = (load_spec(spec_path).get("baseline") or [{}])[0]
    spec_id = baseline.get("composite") or study
    client = _client()
    try:
        run = client.composite_run(run_id)
    except SmsApiError as e:
        if e.status == 404:
            return {"error": str(e), "run_id": run_id}, 404
        return _unreachable(e)
    raw = str(run.get("status", "")).lower()
    if raw not in _TERMINAL_OK:
        return {"error": f"run {run_id!r} is {raw or 'in an unknown state'}; only a completed run has results to land",
                "phase": _phase(raw), "run_id": run_id}, 409
    try:
        simulation_id = remote_run.compose_simulation_id(client, run)
    except SmsApiError as e:
        return _unreachable(e)
    except RuntimeError as e:
        return {"error": str(e), "run_id": run_id}, 404

    deployment = remote_pinned.remote_deployment_name()
    execution = run.get("execution") or {}
    # The backend's record of the run, under one key: its data is landed HERE (runs.db), so the row must not
    # read as a remote-store run (that is what a top-level ``simulation_id`` marks, see simulations_index).
    provenance = {
        "source": deployment, "backend": "viva-v1",
        "viva_v1": {"run_id": run_id, "simulation_id": simulation_id, "job_id": run.get("job_id"),
                    "document_address": run.get("document_address"), "environment": run.get("environment"),
                    "execution": execution},
    }
    study_dir = study_spec.study_dir(ws_root, study)
    db_file = study_dir / "runs.db"
    if db_file.is_file():
        # Landing is idempotent: this run is landed once per study, and asking again answers that landing.
        conn = cr.connect(db_file)
        try:
            found = conn.execute(
                "SELECT run_id FROM runs_meta WHERE json_extract(params_json, '$.viva_v1.run_id') = ?",
                (run_id,)).fetchone()
        finally:
            conn.close()
        if found is not None:
            return {"run_id": found[0], "already_landed": True}, 200
    landed = cr.generate_run_id(spec_id, params=provenance)
    try:
        with tempfile.TemporaryDirectory() as td:
            archive = client.download_compose_results(simulation_id, Path(td), timeout=DOWNLOAD_TIMEOUT)
            if not zipfile.is_zipfile(archive):
                return {"error": f"simulation {simulation_id} served a {archive.name}, not a document run's results "
                                 "archive (a zip); there is nothing here to land as a study run", "run_id": run_id}, 422
            n_steps = land_composite_results(archive, db_file, landed)
    except SmsApiError as e:
        return _unreachable(e)
    except ValueError as e:
        return {"error": f"simulation {simulation_id}'s results cannot be landed: {e}", "run_id": run_id}, 422

    # What the backend installed is its record, not this checkout: the commit is the one its git requirement named.
    installed = next((d for d in (execution.get("options") or {}).get("extra_pip_deps") or []
                      if str(d).startswith("git+")), None)
    url, _, sha = str(installed or "").removeprefix("git+").rpartition("@")
    manifest = cr.build_run_manifest(
        spec_id=spec_id, params=provenance, n_steps=n_steps, emitter="sqlite", emit_paths=[], runtime={},
        origin="remote", study=None, generation_id=None, ws_root=None)
    manifest["code_version"] = {"git_sha": sha or None, "package": None, "repo": None,
                                "remote_url": url or None, "image": None}
    conn = cr.connect(db_file)
    try:
        try:
            cr.save_metadata(conn, spec_id=spec_id, run_id=landed, params=provenance,
                             label=f"Remote run ({deployment})", started_at=time.time(), n_steps=n_steps,
                             workspace=ws_root, manifest=manifest)
            cr.complete_metadata(conn, run_id=landed, n_steps=n_steps, status="completed")
        except Exception:
            # No history rows without the run they belong to.
            with conn:
                conn.execute("DELETE FROM history WHERE simulation_id = ?", (landed,))
            raise
        cr.delete_run(conn, run_id=f"remote-pending-{run_id}", workspace=ws_root)
    finally:
        conn.close()
    return {"run_id": landed}, 200


def _record_pending(ws_root: Path, study: str, spec_id: str, run_id: str) -> None:
    """A Runs-tab placeholder from the moment the backend accepted the run (best-effort: a
    visibility row must never fail an already-dispatched run). Same id scheme as the legacy
    route -- ``remote-pending-<id>`` -- so landing can find and replace it."""
    try:
        from vivarium_workbench.lib import composite_runs as cr

        deployment = remote_pinned.remote_deployment_name()
        conn = cr.connect(study_spec.study_dir(ws_root, study) / "runs.db")
        try:
            cr.save_metadata(
                conn, spec_id=spec_id, run_id=f"remote-pending-{run_id}",
                params={"source": deployment, "simulation_id": run_id, "backend": "viva-v1"},
                label=f"Remote dispatch ({deployment}) — in progress",
                started_at=time.time(), n_steps=0, workspace=ws_root,
            )
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        pass


def _phase(raw: str) -> str:
    return "done" if raw in _TERMINAL_OK else "failed" if raw in _TERMINAL_BAD else (
        "queued" if raw in ("queued", "waiting", "pending") else "running")


_NOT_ALLOW_LISTED = re.compile(r"'(?P<dep>[^']+)' is not in the compose allow list")


def _not_allow_listed(e: SmsApiError) -> "tuple[dict, int] | None":
    """The backend refused to install a dependency because it is not on its compose allow list.

    That is a policy decision by a reachable server, not a connectivity failure, and retrying cannot fix it; say which
    dependency and what to do. ``None`` for any other failure, which keeps its existing 502 report."""
    m = _NOT_ALLOW_LISTED.search(str(e))
    if m is None:
        return None
    dep = m.group("dep")
    repo = re.sub(r"^git\+https?://github\.com/|\.git(@.*)?$|@.*$", "", dep)
    return {
        "error": f"This backend does not allow installing {repo or dep} yet. Ask the backend's operator to add it to the "
                 f"compose allow list (or to register the repository), then run again.",
        "reason": "not-allow-listed", "dependency": dep, "reachable": True, "backend_error": str(e),
    }, 403


def _unreachable(e: SmsApiError) -> tuple[dict, int]:
    return {"phase": "unreachable", "reachable": False,
            "reason": "backend unreachable", "status": e.status, "error": str(e)}, 502


def status(run_id: str) -> tuple[dict, int]:
    """``GET /viva/v1/composites/{id}/status`` in the ``remote_run_status`` shape."""
    try:
        st = _client().composite_run_status(run_id)
    except SmsApiError as e:
        return _unreachable(e)
    raw = str(st.get("status", "")).lower()
    return {"kind": "run", "phase": _phase(raw), "raw_status": raw,
            "error": st.get("message"), "run_id": run_id}, 200


def progress(run_id: str) -> tuple[dict, int]:
    """``GET /viva/v1/composites/{id}/progress`` in the ``remote_run_chain_progress`` shape:
    the run's ``simulation`` jobs are the seeds."""
    try:
        pr = _client().composite_run_progress(run_id)
    except SmsApiError as e:
        if e.status == 404:
            return {"kind": "chain_progress", "phase": "not_found", "error": str(e), "simulation_id": run_id}, 404
        body, code = _unreachable(e)
        return {"kind": "chain_progress", **body, "simulation_id": run_id}, code
    raw = str(pr.get("status", "")).lower()
    sims = (pr.get("by_kind") or {}).get("simulation") or {}
    total = sum(sims.values()) if sims else None
    done = sims.get("completed", 0)
    failed = sims.get("failed", 0) + sims.get("cancelled", 0)
    return {
        "kind": "chain_progress", "phase": _phase(raw),
        "terminal": raw in _TERMINAL_OK | _TERMINAL_BAD,
        "seeds_total": total, "seeds_succeeded": done if total is not None else None,
        "seeds_failed": failed if total is not None else None,
        "seeds_in_progress": (total - done - failed) if total is not None else None,
        "simulation_id": run_id, "run_id": run_id,
    }, 200


def cancel(run_id: str) -> tuple[dict, int]:
    """``DELETE /viva/v1/composites/{id}``. 401/403/409/501 pass through by name."""
    try:
        out = _client().cancel_composite_run(run_id)
    except SmsApiError as e:
        code = e.status if e.status in (401, 403, 404, 409, 501) else 502
        return {"kind": "cancel", "error": str(e), "simulation_id": run_id, "run_id": run_id,
                **({} if code != 502 else {"reachable": False})}, code
    return {"kind": "cancel", "simulation_id": run_id, "run_id": run_id, **out}, 200
