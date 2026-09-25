"""Pure builder for the investigation-wide "run unblocked" SUBMIT route.

Behaviour-preserving port of the stdlib handler
``server.Handler._post_investigation_run_unblocked`` — enumerates every member
study's unblocked variants and submits one background job to the SAME in-process
``lib.run_jobs.manager`` singleton the already-ported
``GET /api/investigation-run-unblocked-status`` reads, so a FastAPI submit is
visible to the status GET.  No ``import server`` here.

``investigation_run_unblocked(ws_root, body)`` returns ``(body, status)`` — the
FastAPI route wraps every path (incl. the 202 success) in ``JSONResponse``.

The externals are referenced at MODULE level so tests monkeypatch them with
fakes and never run a real sim:

  * ``manager`` / ``enumerate_unblocked`` — the in-process run-job manager
    singleton + the per-study planner (``lib.run_jobs``);
  * ``study_runs`` — the E4 study-run orchestrators
    (``run_study_baseline`` / ``run_study_variant``);
  * ``comparative_runs`` — the E5 comparative-viz renderer.

The ``_worker`` closure captures ``ws_root`` / ``inv_slug`` / ``iset`` so the
daemon thread has them, and calls the lib orchestrators + lib renderer directly
(replacing the live handler's ``_post_study_run_*_for_test(WORKSPACE, …)`` and
``self._render_investigation_comparative_visualisations(…)`` — those are now lib
E4/E5).  ``WORKSPACE`` / ``workspace_paths()`` become ``ws_root`` /
``WorkspacePaths.load(ws_root)``.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import yaml as _yaml

from vivarium_workbench.lib import comparative_runs
from vivarium_workbench.lib import study_runs
from vivarium_workbench.lib.run_jobs import (
    WAITING, enumerate_unblocked, manager, order_items_by_prereqs, study_prereqs,
)
from vivarium_workbench.lib.workspace_paths import WorkspacePaths
from vivarium_workbench.lib.investigation_members import investigation_member_slugs


def investigation_run_unblocked(ws_root: Path, body: dict) -> tuple[dict, int]:
    """Submit an investigation-wide multi-variant run job. Returns ``(body, status)``.

    Behaviour-preserving port of ``_post_investigation_run_unblocked``
    (byte-identical messages + status order):

      * missing investigation   → ``({"error": "investigation is required"}, 400)``
      * investigation not found → ``({"error": f"investigation not found: {inv_slug}"}, 404)``
      * yaml parse failure       → ``({"error": f"yaml parse failed: {e}"}, 500)``
      * no variants to queue     → the breakdown ``400`` (Counter over statuses)
                                   with ``"items": items``
      * happy path               → ``({"job_id": job.job_id, "items": items}, 202)``

    Submits to the SAME ``run_jobs.manager`` singleton; the ``_worker`` closure
    fires each queued item through the lib study-run orchestrators
    (``study_runs.run_study_baseline`` / ``run_study_variant``) and then renders
    the comparative visualisations via
    ``comparative_runs.render_investigation_comparative_visualisations``.
    """
    inv_slug = ((body or {}).get("investigation") or "").strip()
    if not inv_slug:
        return {"error": "investigation is required"}, 400
    inv_yaml = WorkspacePaths.load(ws_root).investigations / inv_slug / "investigation.yaml"
    if not inv_yaml.is_file():
        return {"error": f"investigation not found: {inv_slug}"}, 404
    try:
        iset = _yaml.safe_load(inv_yaml.read_text(encoding="utf-8")) or {}
    except _yaml.YAMLError as e:
        return {"error": f"yaml parse failed: {e}"}, 500

    # Optional studies filter: ``{"investigation": "...", "studies":
    # ["dnaa-05-itv2-comparison", ...]}`` runs only those member
    # studies. Default (no filter) is "all studies in the investigation".
    studies_filter_raw = (body or {}).get("studies")
    studies_filter: set[str] | None = None
    if studies_filter_raw:
        if isinstance(studies_filter_raw, str):
            studies_filter = {studies_filter_raw}
        elif isinstance(studies_filter_raw, list):
            studies_filter = {str(s) for s in studies_filter_raw if s}

    # Collect runnable items across every member study (or just the
    # requested subset).
    items: list[dict] = []
    skipped: list[dict] = []
    for member in investigation_member_slugs(iset):
        member_name = member if isinstance(member, str) else member.get("study")
        if not member_name:
            continue
        if studies_filter and member_name not in studies_filter:
            continue
        # A federated member (study shipped inside an installed module) has no
        # host study.yaml; copy it into the host workspace so it isn't skipped and
        # its run can write outputs there.
        study_runs._materialize_federated_study(ws_root, member_name)
        spec_path = WorkspacePaths.load(ws_root).studies / member_name / "study.yaml"
        if not spec_path.is_file():
            # legacy: investigations/<name>/spec.yaml
            spec_path = WorkspacePaths.load(ws_root).investigations / member_name / "spec.yaml"
        if not spec_path.is_file():
            skipped.append({"study": member_name, "variant": "?",
                            "status": "skipped",
                            "error": "study.yaml not found"})
            continue
        try:
            spec = _yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
        except _yaml.YAMLError as e:
            skipped.append({"study": member_name, "variant": "?",
                            "status": "skipped", "error": f"yaml: {e}"})
            continue
        runnable, blocked = enumerate_unblocked(spec)
        items.extend(runnable)
        items.extend(blocked)
    # Plan §A3′: order by declared prerequisites before the worker walks them.
    # Until now this loop emitted items in investigation-member order and the
    # worker ran them in that order, so a study declaring
    # `pipeline_gate.prerequisites` could run BEFORE the study it depends on —
    # while the pbg composite path, building the same investigation, ordered it
    # correctly. Same investigation, two answers.
    #
    # Stable, so the 7-of-9 investigations that declare no prerequisites keep
    # exactly the declared order they have today (which is also what the
    # composite path's synthetic serial edges produce).
    #
    # `skipped` is appended AFTER ordering on purpose: those items have no
    # study.yaml to read prerequisites from, and they never run.
    items = order_items_by_prereqs(items, WorkspacePaths.load(ws_root))
    items.extend(skipped)

    if not any(it.get("status") == "queued" for it in items):
        # mem3dg-readdy friction #34: a bare "no unblocked variants"
        # error was unactionable. Compute a per-status breakdown
        # *and* surface the items[] in the response body so the UI
        # can render per-item reasons.
        status_counts = Counter(it.get("status") or "?" for it in items)
        parts = []
        for label, key in (
            ("blocked",   "blocked"),
            ("skipped",   "skipped"),
            ("completed", "done"),
        ):
            if status_counts.get(key):
                parts.append(f"{status_counts[key]} {label}")
        breakdown = ", ".join(parts) if parts else "no items enumerated"
        return {
            "error": (
                f"no variants to queue ({breakdown}). Each item's reason "
                "is in `items[].error` — see the per-item panel."
            ),
            "items": items,
        }, 400

    # Worker: walk through queued items in order, fire each via the
    # lib study-run orchestrators (E4); then render comparative viz (E5).
    # §A3′ option (c): the gate that ORDERING alone cannot provide. Ordering is
    # enough on a local target, where every run blocks until it finishes; on a
    # deployment target A2′ made dispatch return `submitted` at once, so without
    # this a dependent starts while its prerequisite is still running on Batch.
    _paths = WorkspacePaths.load(ws_root)
    _present = {it.get("study") for it in items if it.get("study")}
    _prereqs = {
        s: [p for p in study_prereqs(_paths, s) if p in _present and p != s]
        for s in _present
    }

    def _gate(job, study):
        """``(None, None)`` to run; else ``(status, reason)`` for this item.

        A prerequisite is satisfied only when EVERY item of that study is
        ``done`` — a study is its baseline plus its variants, and a dependent
        that reads its outputs needs all of them, not the first.

        A prerequisite that ``failed`` or was ``skipped`` can never become done,
        so the dependent is ``skipped`` rather than left ``waiting`` forever.
        Waiting on something that will never arrive is indistinguishable from a
        hang, and it is the redrive loop that would spin on it.
        """
        by_study: dict = {}
        for it in job.items:
            by_study.setdefault(it.get("study"), []).append(it.get("status"))
        dead, pending = [], []
        for pre in _prereqs.get(study, ()):
            statuses = by_study.get(pre) or []
            if any(st in ("failed", "skipped") for st in statuses):
                dead.append(pre)
            elif not all(st == "done" for st in statuses):
                pending.append(pre)
        if dead:
            return "skipped", f"prerequisite did not complete: {', '.join(sorted(dead))}"
        if pending:
            return WAITING, f"waiting on: {', '.join(sorted(pending))}"
        return None, None

    def _worker(job):
        # `waiting` as well as `queued`: a redrive re-runs this same closure, and
        # the items it exists to release are the waiting ones.
        for idx, item in enumerate(list(job.items)):
            if item.get("status") not in ("queued", WAITING):
                continue
            gated, why = _gate(job, item.get("study"))
            if gated is not None:
                job.update_item(idx, status=gated, error=why)
                continue
            # Clear a stale gate reason ONLY when there is one. A redriven item
            # still carries its "waiting on: a" text and would keep showing it
            # after succeeding; but writing `error: None` unconditionally adds
            # the key to every item, and a successful dispatch is specified to
            # carry no `error` at all (test_worker_records_a_202_as_submitted_
            # not_failed asserts absence, not None).
            job.update_item(idx, status="running",
                            **({"error": None} if item.get("error") else {}))
            study_slug = item["study"]
            variant_name = item["variant"]
            try:
                if item["kind"] == "baseline":
                    resp, code = study_runs.run_study_baseline(
                        ws_root, {"study": study_slug}
                    )
                else:
                    resp, code = study_runs.run_study_variant(
                        ws_root, {"study": study_slug, "variant": variant_name}
                    )
                if code == 200:
                    job.update_item(idx, status="done",
                                    run_id=resp.get("run_id", ""))
                elif code == 202:
                    # A 202 is a SUCCESSFUL async dispatch, not a failure. On a
                    # deployment target `study_runs.run_study_baseline` returns
                    # `remote_run_views.remote_run_submit` verbatim — 202 with a
                    # `simulation_id`, no polling — so the run is on Batch and
                    # still going. Accepting only 200 recorded every such
                    # dispatch as failed with the error text "HTTP 202", and
                    # discarded the simulation_id, leaving nothing to poll.
                    job.update_item(idx, status="submitted",
                                    simulation_id=resp.get("simulation_id"),
                                    phase=resp.get("phase", "running"))
                else:
                    job.update_item(idx, status="failed",
                                    error=resp.get("error", f"HTTP {code}"))
            except BaseException as e:  # noqa: BLE001
                job.update_item(idx, status="failed", error=str(e))
        # Optional: render investigation-level comparative visualisations.
        comparative_runs.render_investigation_comparative_visualisations(
            ws_root, inv_slug, iset, job,
        )

    job = manager.submit(inv_slug, items, _worker)
    return {"job_id": job.job_id, "items": items}, 202
