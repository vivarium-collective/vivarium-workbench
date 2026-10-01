"""The study-run remote routes on ``/viva/v1/composites`` (contract: docs/backend-viva-v1.md).

Used only when the operator named a backend (``serve --backend-base-url``) that advertises the
document run surface and neither a session build nor the pinned config chose the simulator-keyed
path (:func:`remote_pinned.uses_viva_v1_dispatch`). Every function mirrors the shape the legacy
``remote_run_views`` route answers, so the existing panel/poll code is unchanged; ids are the
backend's opaque run ids (strings), carried as ``run_id``.
"""

from __future__ import annotations

import json
import tempfile
import time
import warnings
from pathlib import Path

from vivarium_workbench.lib import remote_pinned, study_spec
from vivarium_workbench.lib.investigations import load_spec
from vivarium_workbench.lib.sms_api_client import SmsApiClient, SmsApiError
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
        return {"error": str(e), "reachable": False}, 502
    _record_pending(ws_root, study, composite_id, run["id"])
    return {"run_id": run["id"], "phase": "running", "backend": "viva-v1"}, 202


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
