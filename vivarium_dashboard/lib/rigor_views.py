"""Rigor scorecard view builders extracted from server.py.

The ``ws_root``-parameterised builder for the rigor GET route ported in
Phase A, Batch 3.  It reads the **run-merged** per-study spec via
``lib.study_spec.load_study_detail_spec`` — this is the whole reason the loader
was extracted first: ``pbg_superpowers.rigor`` reads ``spec["runs"]`` for the
replication (dim 1) and run-persistence (dim 13) dimensions, so the runs.db
merge must already be applied or the scores drift from the legacy handler.

Builders
--------
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

from vivarium_dashboard.lib.study_spec import load_study_detail_spec
from vivarium_dashboard.lib.workspace_paths import WorkspacePaths


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

def build_investigation_rigor(ws_root: Path, investigation: Optional[str]) -> dict:
    """Build the GET /api/investigation-rigor payload for *ws_root*.

    Returns the rigor roll-up across the investigation's member studies plus the
    investigation-level dimensions (adversarial coverage, traceable methodology),
    from ``pbg_superpowers.rigor.investigation_rigor``.  Each member study's spec
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
    for s in (inv_spec.get("studies") or []):
        slug_s = s if isinstance(s, str) else (
            (s.get("slug") or s.get("study")) if isinstance(s, dict) else None)
        if not slug_s:
            continue
        sp = load_study_detail_spec(ws_root, slug_s)
        if sp:
            member_specs.append(sp)
    try:
        from pbg_superpowers.rigor import investigation_rigor
        return investigation_rigor(inv_spec, member_specs)
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}",
                "dimensions": [], "per_study": {}, "score": {}, "summary": ""}
