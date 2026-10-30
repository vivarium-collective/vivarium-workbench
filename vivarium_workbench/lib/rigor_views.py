"""Rigor scorecard view builders extracted from server.py.

The ``ws_root``-parameterised builder for the rigor GET route ported in
Phase A, Batch 3.  It reads the **run-merged** per-study spec via
``lib.study_spec.load_study_detail_spec`` — this is the whole reason the loader
was extracted first: ``discovery_engine.rigor`` reads ``spec["runs"]`` for the
replication (dim 1) and run-persistence (dim 13) dimensions, so the runs.db
merge must already be applied or the scores drift from the legacy handler.

Builders
--------
build_study_rigor          → GET /api/study-rigor?study= (Fable G5)
build_investigation_rigor  → GET /api/investigation-rigor?investigation=

Returns a JSON-serialisable dict on every 200 path (including the
"200 with error" fallbacks the legacy handler emits when rigor computation or
an investigation.yaml read fails).  Raises :class:`RigorViewError` only for
the genuine non-200 paths (400 missing param, 404 not found).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from vivarium_workbench.lib.study_spec import load_study_detail_spec
from vivarium_workbench.lib.workspace_paths import WorkspacePaths
from vivarium_workbench.lib.investigation_members import investigation_member_slugs


# ---------------------------------------------------------------------------
# Error signal
# ---------------------------------------------------------------------------

class RigorViewError(Exception):
    """Raised by builders to signal a non-200 HTTP response.

    ``body`` is the complete JSON-serialisable error dict (e.g.
    ``{"error": "..."}``); ``status`` is the HTTP status code (400 or 404).
    Both the stdlib shim and the FastAPI route catch this and return the body
    verbatim so the error contract is defined once, in the builder.

    Note: the rigor handlers' "unreadable investigation.yaml" and "rigor
    compute failed" cases are NOT errors here — the legacy handlers emit those
    as HTTP 200 bodies, so the builders return them as ordinary dicts.
    """

    def __init__(self, body: dict, status: int) -> None:
        super().__init__(body.get("error", ""))
        self.body = body
        self.status = status


# ---------------------------------------------------------------------------
# Public builders
# ---------------------------------------------------------------------------

def build_study_rigor(ws_root: Path, study: Optional[str]) -> dict:
    """Build the GET /api/study-rigor payload for *ws_root* (Fable G5).

    Returns the per-study evidence & rigor scorecard (``dimensions`` / ``score``
    / ``summary``, plus ``study_type`` / ``mode`` / ``descriptive``) from
    ``discovery_engine.rigor.study_rigor``, computed over the study's
    run-merged spec loaded via :func:`vivarium_workbench.lib.study_spec.
    load_study_detail_spec` — the same loader :func:`build_investigation_rigor`
    uses for its member studies, so replication (dim 1) and run-persistence
    (dim 13) reflect actual ``runs.db`` rows, not just what's authored in
    ``study.yaml``.

    Raises ``RigorViewError``:
    - 400 when ``study`` is empty/None (``{"error": "missing ?study="}``).
    - 404 when no ``study.yaml`` / ``spec.yaml`` exists for the slug
      (``{"error": "study not found"}``).

    Never a 500: an unimportable ``discovery_engine.rigor`` or any exception
    raised while scoring degrades to a 200-shaped payload
    ``{"unavailable": True, "reason": "<ExceptionType>: <message>"}`` — no
    fabricated empty scorecard (spec §2 R2, "absent != empty").
    """
    if not study:
        raise RigorViewError({"error": "missing ?study="}, 400)
    spec = load_study_detail_spec(ws_root, study)
    if spec is None:
        raise RigorViewError({"error": "study not found"}, 404)
    try:
        from discovery_engine.rigor import study_rigor
        return study_rigor(spec)
    except Exception as e:  # noqa: BLE001 — degrade, never 500
        return {"unavailable": True, "reason": f"{type(e).__name__}: {e}"}


def build_investigation_rigor(ws_root: Path, investigation: Optional[str]) -> dict:
    """Build the GET /api/investigation-rigor payload for *ws_root*.

    Returns the rigor roll-up across the investigation's member studies plus the
    investigation-level dimensions (adversarial coverage, traceable methodology),
    from ``discovery_engine.rigor.investigation_rigor``.  Each member study's spec
    is loaded run-merged via :func:`load_study_detail_spec`.

    Raises ``RigorViewError``:
    - 400 when ``investigation`` is empty/None (``{"error": "missing ?investigation="}``).
    - 404 when ``investigations/<slug>/investigation.yaml`` does not exist
      (``{"error": "investigation not found"}``).

    On an unreadable investigation.yaml returns a 200-shaped error dict
    ``{"error": "unreadable investigation.yaml: ..."}``; on a rigor-computation /
    import failure returns ``{"error": "...", "dimensions": [], "per_study": {},
    "score": {}, "summary": ""}`` — both matching the legacy
    ``server._get_investigation_rigor``.
    """
    if not investigation:
        raise RigorViewError({"error": "missing ?investigation="}, 400)
    inv_path = WorkspacePaths.load(ws_root).investigations / investigation / "investigation.yaml"
    if not inv_path.is_file():
        raise RigorViewError({"error": "investigation not found"}, 404)
    try:
        inv_spec = yaml.safe_load(inv_path.read_text(encoding="utf-8")) or {}
    except Exception as e:  # noqa: BLE001
        return {"error": f"unreadable investigation.yaml: {e}"}
    member_specs = []
    for s in investigation_member_slugs(inv_spec):
        slug_s = s if isinstance(s, str) else (
            (s.get("slug") or s.get("study")) if isinstance(s, dict) else None)
        if not slug_s:
            continue
        sp = load_study_detail_spec(ws_root, slug_s)
        if sp:
            member_specs.append(sp)
    try:
        from discovery_engine.rigor import investigation_rigor
        return investigation_rigor(inv_spec, member_specs)
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}",
                "dimensions": [], "per_study": {}, "score": {}, "summary": ""}
