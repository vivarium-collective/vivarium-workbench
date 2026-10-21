"""Read-only L0-L5 study-reproducibility audit view.

Wraps :func:`viva_superpowers.study_audit.audit_workspace` behind the same
``(body, status)`` contract used by :mod:`investigation_graph_views`, so the
``GET /api/audit`` route is a thin ``JSONResponse`` passthrough. This is the
*visible* companion to the CI reproducibility gate (which lives in v2ecoli CI);
it is purely informational — it runs no gate and applies no allowlist.

Tolerant by design: a workspace with no studies, an unimportable audit module,
or any ``audit_workspace`` error yields ``({"error": ..., "studies": [],
"investigations": []}, 200)`` — never a 500 — so the UI degrades gracefully.

``build_study_audit`` (Fable G6) is the single-study companion: it backs
``GET /api/study-audit?study=<slug>``, the Tests-tab "Reproducibility" check
group, mirroring :func:`vivarium_workbench.lib.rigor_views.build_study_rigor`'s
400/404/``unavailable`` contract exactly.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from vivarium_workbench.lib.workspace_paths import WorkspacePaths


class StudyAuditViewError(Exception):
    """Raised by :func:`build_study_audit` to signal a non-200 HTTP response.

    Mirrors :class:`vivarium_workbench.lib.rigor_views.RigorViewError` exactly:
    ``body`` is the complete JSON-serialisable error dict, ``status`` is 400 or
    404. A failed *computation* (unimportable ``study_audit``, or any exception
    raised while auditing) is NOT this error — it degrades to a 200
    ``{"unavailable": True, "reason": ...}`` body instead (spec §2 R2, "absent
    != empty").
    """

    def __init__(self, body: dict, status: int) -> None:
        super().__init__(body.get("error", ""))
        self.body = body
        self.status = status


def _workspace_packages(ws_root: Path) -> list[str]:
    """Best-effort import names for the workspace's own composite generators.

    The workspace Python package registers its ``@composite_generator``
    decorators only when imported, so include it and its ``.composites``
    subpackage as ``extra_packages`` — the same lever the CLI ``--package`` flag
    provides — so workspace-local composites resolve during the audit.
    """
    try:
        pkg = WorkspacePaths.load(ws_root).package.name
    except Exception:  # noqa: BLE001 — best-effort
        return []
    if not pkg:
        return []
    return [pkg, f"{pkg}.composites"]


def build_audit(ws_root) -> tuple[dict, int]:
    """Run the audit and return ``(report, 200)``.

    Two layers, each best-effort:
      - the L0-L5 **reproducibility grade** (:mod:`audit_grade`, self-contained —
        works with or without the upstream package), attached as ``grade`` on
        each study/investigation block + ``summary.grade_distribution``;
      - the upstream **structural** audit (``viva_superpowers.study_audit``),
        whose per-check levels/verdicts fill each block's ``checks`` when present.

    When ``study_audit`` is unavailable the report is built from the grade alone,
    so the Audit tab (and published snapshot) still show reproducibility scores.
    Tolerant: any failure -> a 200 with an ``error`` note, never a 500.
    """
    ws_root = Path(ws_root)

    try:
        from vivarium_workbench.lib.audit_grade import grade_workspace
        grades = grade_workspace(ws_root)
    except Exception as exc:  # noqa: BLE001 — grade is best-effort
        grades = {"studies": {}, "investigations": {}, "distribution": {}, "error": str(exc)}

    report = None
    err = grades.get("error") or ""
    try:
        from viva_superpowers import study_audit
        report = study_audit.audit_workspace(
            ws_root, extra_packages=_workspace_packages(ws_root) or None
        ).as_dict()
    except Exception as exc:  # noqa: BLE001 — dependency missing/broken: degrade, don't 500
        err = err or f"study_audit unavailable: {exc}"

    if report is None:
        report = {
            "error": err,
            "studies": [{"slug": s, "worst": "pass", "checks": []} for s in grades["studies"]],
            "investigations": [{"slug": s, "worst": "pass", "checks": []}
                               for s in grades["investigations"]],
            "summary": {"n_studies": len(grades["studies"]),
                        "n_investigations": len(grades["investigations"]),
                        "hard_failures": 0},
        }

    for blk in report.get("studies", []):
        g = grades["studies"].get(blk.get("slug"))
        if g:
            blk["grade"] = g
    for blk in report.get("investigations", []):
        g = grades["investigations"].get(blk.get("slug"))
        if g:
            blk["grade"] = g

    # Fold the observable-contract check (readouts ⊆ composite observables) into
    # every study block, so the workspace-wide Audit tab grades readout coverage
    # exactly as the single-study Tests tab does (via build_study_audit). The
    # check is tier "soft" and self-skips when a study declares no readouts or
    # its baseline composite won't resolve, so this never 500s and never fabricates.
    for blk in report.get("studies", []):
        _augment_with_observable_contract(blk, ws_root, blk.get("slug"))

    # The ladder's top rung: grade each investigation's closed-check (every
    # acceptance criterion met by a supporting study verdict) as an L5 check,
    # computed fresh. Soft-tier + self-skipping (no criteria / unloadable), so
    # it never 500s and never fabricates.
    for blk in report.get("investigations", []):
        _augment_with_acceptance_closed(blk, ws_root, blk.get("slug"))

    report.setdefault("summary", {})["grade_distribution"] = grades.get("distribution", {})
    return report, 200


_SEVERITY = {"pass": 0, "warn": 1, "fail": 2}


def _worst_status(checks: list) -> str:
    """The worst status across a block's checks (fail > warn > pass)."""
    worst = 0
    for c in checks:
        worst = max(worst, _SEVERITY.get(c.get("status", "pass"), 0))
    return {0: "pass", 1: "warn", 2: "fail"}[worst]


def _observable_contract_check(ws_root: Path, slug: str) -> "dict | None":
    """A graded *observable-contract* check: a study's declared readouts must
    resolve to real observables of its baseline composite(s) -- the readout-level
    analogue of ``contract-face-wellformed``, one layer up.

    Reuses :func:`observables_views.build_study_observable_check` (the SAME
    resolver the readouts picker and needs-attention use), so this adds no new
    resolution logic. Maps each per-readout status to a verdict:
    ``not_in_structure``/``unresolved`` (a selector no baseline composite
    exposes) -> ``fail``; ``aspirational`` (declared, intentionally not yet
    available) -> ``warn``; all ``ok`` -> ``pass``.

    Returns the check dict, or ``None`` to skip -- the study declares no readouts
    (nothing to grade), or the resolver could not run (non-200, e.g. no baseline
    composite builds; that is a composite-resolution problem graded elsewhere).
    Degrade, never fabricate.
    """
    try:
        from vivarium_workbench.lib.observables_views import (
            build_study_observable_check,
        )
        payload, code = build_study_observable_check(ws_root, slug)
    except Exception:  # noqa: BLE001 — degrade; the audit must never 500
        return None
    if code != 200 or not isinstance(payload, dict):
        return None
    readouts = payload.get("readouts") or []
    if not readouts:
        return None
    failed = [r for r in readouts
              if r.get("status") in ("not_in_structure", "unresolved")]
    warned = [r for r in readouts if r.get("status") == "aspirational"]
    base = {"level": "L3", "name": "observable-contract", "tier": "soft"}
    if failed:
        names = ", ".join(str(r.get("name")) for r in failed[:5])
        return {**base, "status": "fail",
                "detail": f"{len(failed)} declared readout(s) resolve to no "
                          f"composite observable: {names}"}
    if warned:
        names = ", ".join(str(r.get("name")) for r in warned[:5])
        return {**base, "status": "warn",
                "detail": f"{len(warned)} readout(s) aspirational (declared, not "
                          f"yet available): {names}"}
    return {**base, "status": "pass",
            "detail": f"all {len(readouts)} declared readouts resolve to a "
                      f"composite observable"}


def _augment_with_observable_contract(block: dict, ws_root: Path, slug: str) -> dict:
    """Fold the observable-contract check into a study-audit ``block`` in place
    (append to ``checks``, recompute ``worst``), then return it. A no-op when
    :func:`_observable_contract_check` skips."""
    chk = _observable_contract_check(ws_root, slug)
    if chk is not None:
        block.setdefault("checks", []).append(chk)
        block["worst"] = _worst_status(block.get("checks") or [])
    return block


def _grade_acceptance(rolled: "dict | None") -> "dict | None":
    """Map a :func:`roll_up_acceptance` result to an L5 ``acceptance-closed``
    check — the inquiry-level analogue of the ground-check: an investigation is
    *closed* only when every acceptance criterion is met by a supporting study
    verdict. ``failing`` -> ``fail`` (a criterion is refuted/unmet),
    ``in-progress``/``passing-with-caveats`` -> ``warn`` (not yet closed),
    ``passing`` -> ``pass`` (closed). Returns ``None`` to skip when the
    investigation declares no acceptance criteria (nothing to grade)."""
    if not isinstance(rolled, dict):
        return None
    criteria = rolled.get("criteria") or []
    if not criteria:
        return None
    unmet = rolled.get("unmet") or []
    vs = rolled.get("verdict_status")
    base = {"level": "L5", "name": "acceptance-closed", "tier": "soft"}
    if vs == "failing":
        names = ", ".join(f"{c.get('study')}·{c.get('behavior')}" for c in unmet[:5])
        return {**base, "status": "fail",
                "detail": f"{len(unmet)} of {len(criteria)} acceptance criteria "
                          f"refuted or unmet: {names}"}
    if vs in ("in-progress", "passing-with-caveats"):
        return {**base, "status": "warn",
                "detail": f"investigation not yet closed ({vs}): {len(unmet)} of "
                          f"{len(criteria)} criteria not passing"}
    if vs == "passing":
        return {**base, "status": "pass",
                "detail": f"all {len(criteria)} acceptance criteria met — "
                          f"investigation closed"}
    return None


def _acceptance_closed_check(ws_root: Path, slug: str) -> "dict | None":
    """Compute the investigation closed-check fresh via the canonical
    :func:`viva_superpowers.investigation_status.roll_up_acceptance` evaluator
    (the same one the report's conclusion and the linkage index use), then grade
    it with :func:`_grade_acceptance`. Loads the investigation spec + member
    study specs read-only. Degrades to ``None`` (skip) on any failure — a missing
    investigation.yaml, no criteria, or an unimportable evaluator — so the audit
    never 500s and never fabricates a verdict."""
    try:
        from viva_superpowers import study_io as _sio
        from viva_superpowers.investigation_status import roll_up_acceptance

        wp = WorkspacePaths.load(ws_root)
        inv_yaml = wp.dir("investigations") / slug / "investigation.yaml"
        if not inv_yaml.is_file():
            return None
        inv_spec = _sio.load_yaml_mapping(inv_yaml) or {}
        if not (inv_spec.get("acceptance_criteria") or []):
            return None
        studies_by_name: dict = {}
        studies_dir = wp.dir("studies")
        if studies_dir.is_dir():
            for sd in studies_dir.iterdir():
                syp = sd / "study.yaml"
                if syp.is_file():
                    try:
                        studies_by_name[sd.name] = _sio.load_yaml_mapping(syp)
                    except Exception:  # noqa: BLE001 — one bad study must not sink the audit
                        pass
        rolled = roll_up_acceptance(inv_spec, studies_by_name)
    except Exception:  # noqa: BLE001 — degrade, never 500
        return None
    return _grade_acceptance(rolled)


def _augment_with_acceptance_closed(block: dict, ws_root: Path, slug: str) -> dict:
    """Fold the acceptance-closed check into an investigation-audit ``block`` in
    place (append to ``checks``, recompute ``worst``), then return it. A no-op
    when :func:`_acceptance_closed_check` skips."""
    chk = _acceptance_closed_check(ws_root, slug)
    if chk is not None:
        block.setdefault("checks", []).append(chk)
        block["worst"] = _worst_status(block.get("checks") or [])
    return block


def build_study_audit(ws_root: Path, study: Optional[str]) -> dict:
    """Build the GET /api/study-audit payload for one study (Fable G6).

    Backs the Tests tab's Reproducibility check group. Runs the SAME
    ``viva_superpowers.study_audit.audit_workspace`` evaluator ``build_audit``
    uses for the workspace-wide Audit tab -- there is no single-study entry
    point upstream, so this audits the whole workspace and returns just the
    block for ``study`` -- then returns that study's block verbatim:
    ``{"slug": ..., "worst": "pass"|"warn"|"fail", "checks": [{"level": "L0".."L5",
    "name": ..., "status": "pass"|"warn"|"fail", "tier": "hard"|"soft",
    "detail": ...}, ...]}``.

    Raises ``StudyAuditViewError``:
    - 400 when ``study`` is empty/None (``{"error": "missing ?study="}``).
    - 404 when no ``study.yaml``/``spec.yaml`` exists for the slug
      (``{"error": "study not found"}``).

    Never a 500: an unimportable ``viva_superpowers.study_audit``, any
    exception raised while auditing, or the audited workspace simply not
    reporting a block for this slug (e.g. a nested/invalid study.yaml the
    evaluator skips) all degrade to a 200-shaped
    ``{"unavailable": True, "reason": "..."}`` payload -- never a fabricated
    empty check list (spec §2 R2, "absent != empty").
    """
    if not study:
        raise StudyAuditViewError({"error": "missing ?study="}, 400)
    ws_root = Path(ws_root)
    from vivarium_workbench.lib.study_spec import load_study_detail_spec

    spec = load_study_detail_spec(ws_root, study)
    if spec is None:
        raise StudyAuditViewError({"error": "study not found"}, 404)

    try:
        from viva_superpowers import study_audit
        report = study_audit.audit_workspace(
            ws_root, extra_packages=_workspace_packages(ws_root) or None
        )
    except Exception as e:  # noqa: BLE001 — degrade, never 500
        return {"unavailable": True, "reason": f"{type(e).__name__}: {e}"}

    for blk in report.studies:
        if blk.slug == study:
            return _augment_with_observable_contract(blk.as_dict(), ws_root, study)
    return {"unavailable": True,
            "reason": f"study_audit reported no block for {study!r}"}
