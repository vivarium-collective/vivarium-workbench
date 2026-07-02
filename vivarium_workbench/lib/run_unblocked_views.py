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

from vivarium_dashboard.lib import comparative_runs
from vivarium_dashboard.lib import study_runs
from vivarium_dashboard.lib.run_jobs import enumerate_unblocked, manager
from vivarium_dashboard.lib.workspace_paths import WorkspacePaths


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
    for member in (iset.get("studies") or []):
        member_name = member if isinstance(member, str) else member.get("study")
        if not member_name:
            continue
        if studies_filter and member_name not in studies_filter:
            continue
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
    def _worker(job):
        for idx, item in enumerate(list(job.items)):
            if item.get("status") != "queued":
                continue
            job.update_item(idx, status="running")
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
