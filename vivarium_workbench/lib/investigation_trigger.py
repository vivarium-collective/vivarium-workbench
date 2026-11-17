"""Layer-4 endpoint logic: pull-or-compute ``trigger`` over an investigation.

Wraps ``process_bigraph.templates.trigger`` for the workbench:

  1. build the pbg investigation document from the workspace
     (:func:`vivarium_workbench.lib.investigation_composite.build_investigation_document`);
  2. call ``trigger(document, target, on_missing=..., commit=<ws HEAD>,
     root=<abs .pbg/artifacts>)`` — which fills each cached prerequisite with a
     ``CachedResults`` reference (pull), leaves the target open (compute), and
     prunes non-ancestors — returning the ``{pulled, computed, pruned}`` report;
  3. (optionally) launch the *target* study through the **existing** run
     subsystem (:func:`vivarium_workbench.lib.composite_test_run_views.composite_test_run`),
     wiring each *pulled* upstream artifact's store path into the target run's
     overrides (the same ``into`` mechanism ``artifacts.pipeline._default_compute``
     uses) — so "continue from here" reuses the cached upstream without
     recomputing it. No new runner is introduced; the pbg sub-document's single
     open (compute) node — the target — is what the run subsystem executes.

"Run this study" and "continue from here reusing cached upstream" are the same
call with different ``target_study`` values.
"""
from __future__ import annotations

from pathlib import Path

from vivarium_workbench.lib.errors import APIError
from vivarium_workbench.lib.investigation_composite import (
    STUDY_META,
    InvestigationCompositeError,
    artifacts_root,
    build_investigation_document,
    workspace_commit,
)
from vivarium_workbench.lib.study_spec import SLUG_RE

# Config keys that are run-control (carried by the RunRequest / the run
# subsystem), not generator parameters — kept out of the generator overrides.
# Mirrors ``artifacts.pipeline._RUN_CONTROL_KEYS``.
_RUN_CONTROL_KEYS = ("n_steps",)


def _pulled_upstream_overrides(document: dict, target: str, report: dict,
                               ws_root: Path, commit: str) -> dict:
    """Overrides wiring each *pulled* upstream's store path into the target run.

    For every ``inputs[].from`` edge of the target whose producer was pulled
    (its artifact is in the store), resolve the producer's on-disk payload path
    and expose it to the target's generator as ``<artifact>_path`` (always) and
    under the edge's declared ``into`` key (when present, or the documented
    ``sim_data -> cache_dir`` back-compat default). Identical to the wiring
    ``artifacts.pipeline._default_compute`` performs, so a study runnable via the
    pipeline is runnable via a trigger the same way.
    """
    from process_bigraph.templates import study_address
    from vivarium_workbench.lib.artifacts.store import ArtifactStore

    pulled = set(report.get("pulled") or [])
    meta = (document.get(target) or {}).get(STUDY_META) or {}
    store = ArtifactStore(ws_root)
    overrides: dict = {}
    for edge in meta.get("inputs") or []:
        frm = edge.get("from")
        artifact = edge.get("artifact")
        if not frm or frm not in pulled:
            continue
        try:
            address = study_address(document, frm, commit=commit)
        except Exception:  # noqa: BLE001 — an unresolvable edge just isn't wired
            continue
        path = str(store.path(address))
        if artifact:
            overrides[f"{artifact}_path"] = path
        into = edge.get("into") or ("cache_dir" if artifact == "sim_data" else None)
        if into:
            overrides[into] = path
    return overrides


def _launch_target(ws_root: Path, document: dict, target: str, report: dict,
                   commit: str) -> tuple[dict | None, int]:
    """Launch the target study via the existing detached composite-run subsystem.

    Returns ``(run_body, status)`` from
    :func:`composite_test_run_views.composite_test_run` (e.g.
    ``({"run_id", "status"}, 202)``), or ``({"error": ...}, status)`` on a
    launch failure — never raises, so a failed launch still lets the caller
    return the (successful) trigger report.
    """
    meta = (document.get(target) or {}).get(STUDY_META) or {}
    config = dict(meta.get("config") or {})
    n_steps = config.pop("n_steps", None)
    for key in _RUN_CONTROL_KEYS:
        config.pop(key, None)

    overrides = dict(config)
    overrides.update(
        _pulled_upstream_overrides(document, target, report, ws_root, commit))

    body = {
        "id": meta.get("id") or target,
        "overrides": overrides,
        "label": f"trigger:{target}",
    }
    if n_steps is not None:
        try:
            body["steps"] = float(n_steps)
        except (TypeError, ValueError):
            pass

    try:
        from vivarium_workbench.lib import composite_test_run_views
        return composite_test_run_views.composite_test_run(ws_root, body)
    except APIError as exc:  # a refusal with its own status and body (502: the backend named for this workbench is down)
        return exc.to_body(), exc.status_code
    except Exception as exc:  # noqa: BLE001 — a launch failure must not sink the report
        return {"error": f"launch failed: {exc}"}, 500


def investigation_trigger(ws_root: Path | str, body: dict) -> tuple[dict, int]:
    """POST /api/investigation-trigger handler logic.

    Body: ``{investigation, target_study, on_missing?, launch?}``.
      * ``on_missing`` — ``"error"`` (default: refuse to silently recompute an
        uncached prerequisite, naming it) or ``"compute"``.
      * ``launch`` — default ``True``; ``False`` returns the plan/report only
        (no run spawned). The published/read-only bundle has no live backend, so
        the frontend never calls this with ``launch`` there.

    Returns ``(payload, status)``:
      * 200 ``{investigation, target, on_missing, commit, report,
               run?, launch_status?}`` on success (``report`` =
        ``{target, pulled, computed, pruned}``).
      * 400 invalid body; 404 unknown investigation/target; 409 an uncached
        prerequisite under ``on_missing="error"`` (message names it).
    """
    from process_bigraph.templates import trigger as pbg_trigger

    ws_root = Path(ws_root)
    inv = (body or {}).get("investigation")
    target = (body or {}).get("target_study")
    on_missing = (body or {}).get("on_missing") or "error"
    launch = (body or {}).get("launch", True)

    if not inv or not isinstance(inv, str) or not SLUG_RE.match(inv):
        return {"error": "missing or invalid 'investigation'"}, 400
    if not target or not isinstance(target, str) or not SLUG_RE.match(target):
        return {"error": "missing or invalid 'target_study'"}, 400
    if on_missing not in ("error", "compute"):
        return {"error": "on_missing must be 'error' or 'compute'"}, 400

    try:
        document = build_investigation_document(ws_root, inv)
    except InvestigationCompositeError as exc:
        return {"error": str(exc)}, 404

    commit = workspace_commit(ws_root)
    root = artifacts_root(ws_root)

    try:
        _subdoc, report = pbg_trigger(
            document, target, on_missing=on_missing, commit=commit, root=root)
    except KeyError as exc:
        # study_ancestors raises KeyError naming the unknown target.
        return {"error": f"no study {target!r} in investigation {inv!r}: {exc}"}, 404
    except FileNotFoundError as exc:
        # on_missing='error' with an uncached prerequisite — names it.
        return {"error": str(exc), "on_missing": on_missing,
                "investigation": inv, "target": target}, 409

    payload: dict = {
        "investigation": inv,
        "target": target,
        "on_missing": on_missing,
        "commit": commit,
        "report": report,
    }

    if launch:
        run_body, run_status = _launch_target(
            ws_root, document, target, report, commit)
        payload["run"] = run_body
        payload["launch_status"] = run_status

    return payload, 200
