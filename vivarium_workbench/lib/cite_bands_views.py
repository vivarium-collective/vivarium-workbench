"""View builders backing the ``/viva-cite-bands`` skill (Phase 2.1e).

Phase 2.1e (rewire-first, ``docs/superpowers/plans/2026-08-04-phase2.1-rewire-
first.md`` §2.1e): the workbench keeps importing the plugin's compute to BACK
these endpoints — no module move yet (that's 2.1k). This lets
``skills/viva-cite-bands/SKILL.md`` stop importing ``viva_superpowers.
band_provenance`` / ``citation_gaps`` / ``expert_search`` directly and call
the dashboard API instead, matching the pattern already used by
``/api/report-lint`` and ``/api/study-readout-migrate``.

Four builders, each ``(dict, int)``:

- ``build_band_provenance_missing`` — ``GET /api/band-provenance?study=``
  wraps ``viva_superpowers.band_provenance.bands_missing_provenance`` (READ).
- ``write_band_provenance`` — ``POST /api/band-provenance``
  wraps ``viva_superpowers.band_provenance.set_band_provenance`` (WRITE —
  the skill's ONLY sanctioned write path for citation provenance).
- ``build_citation_gaps`` — ``GET /api/citation-gaps?investigation=``
  wraps ``vivarium_workbench.lib.citation_gaps.investigation_citation_gaps`` (READ).
- ``build_expert_search`` — ``GET /api/expert-search?q=``
  wraps ``vivarium_workbench.lib.expert_search.search_expert_docs`` (READ).

The skill's fourth import, ``study_io.load_yaml_mapping``, is a trivial YAML
reader (``yaml.safe_load(path.read_text())``) with no endpoint of its own —
these builders inline the equivalent read directly rather than adding a
generic "read any YAML" route (see the plan's explicit note not to add one).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from vivarium_workbench.lib.study_spec import study_dir as _resolve_study_dir
from vivarium_workbench.lib.study_spec import study_spec_file as _study_spec_file
from vivarium_workbench.lib.workspace_paths import WorkspacePaths


def _load_raw_study_spec(sdir: Path) -> dict:
    """Raw (not run-merged) study.yaml/spec.yaml read — mirrors the skill's
    own ``study_io.load_yaml_mapping`` call, which ``band_provenance`` /
    ``set_band_provenance`` are written against (both operate on the
    hand-authored bands, not the runs.db-merged detail spec)."""
    spec_file = _study_spec_file(sdir)
    if not spec_file.is_file():
        return {}
    data = yaml.safe_load(spec_file.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# GET /api/band-provenance?study=<slug>
# ---------------------------------------------------------------------------

def build_band_provenance_missing(ws_root: Path, study: Optional[str]) -> "tuple[dict, int]":
    """GET /api/band-provenance builder — the uncited-bands read.

    Wraps ``viva_superpowers.band_provenance.bands_missing_provenance``.

    Returns ``({"study": slug, "missing": [...]}, 200)`` where ``missing`` is
    exactly the plugin function's ``[{name, kind, band, field_path}]`` list
    (empty list when every band is already cited).

    - 400 ``{"error": "study slug required"}`` — missing/blank ``study``.
    - 404 ``{"error": "study not found: <slug>"}`` — no ``study.yaml``/
      ``spec.yaml`` for the slug.
    - 500 ``{"error": "band provenance requires viva_superpowers: <e>"}`` —
      the plugin (or its ``band_provenance`` module) isn't installed.
    """
    if not study or not str(study).strip():
        return {"error": "study slug required"}, 400
    slug = str(study).strip()

    sdir = _resolve_study_dir(Path(ws_root), slug)
    if not _study_spec_file(sdir).is_file():
        return {"error": f"study not found: {slug}"}, 404

    try:
        from discovery_engine.band_provenance import bands_missing_provenance
    except ImportError as e:  # noqa: BLE001
        return {"error": f"band provenance requires viva_superpowers: {e}"}, 500

    spec = _load_raw_study_spec(sdir)
    try:
        missing = bands_missing_provenance(spec)
    except Exception as e:  # noqa: BLE001 — never a bare 500 with no message
        return {"error": f"band provenance scan failed: {e}"}, 500

    return {"study": slug, "missing": missing}, 200


# ---------------------------------------------------------------------------
# POST /api/band-provenance {study, test_name, cites, calibration_anchor?}
# ---------------------------------------------------------------------------

def write_band_provenance(ws_root: Path, body: dict) -> "tuple[dict, int]":
    """POST /api/band-provenance builder — the ONLY sanctioned write path.

    Wraps ``viva_superpowers.band_provenance.set_band_provenance``.

    Body: ``{study, test_name, cites: [bib_key, ...], calibration_anchor?}``.
    ``cites`` must be a non-empty list of strings — this is the ``/viva-
    cite-bands`` skill's one write op (Step 4), so an empty/missing ``cites``
    is treated as a caller error rather than silently writing nothing.

    Returns ``({"study": slug, "test_name": ..., "written": bool}, 200)`` on
    success. ``written`` is ``False`` when the entry was not found (never
    fabricates — matches the plugin's contract) or the write was a no-op
    (already identical — idempotent).

    - 400 ``{"error": "study slug required"}`` / ``{"error": "test_name required"}``
      / ``{"error": "cites must be a non-empty list of bib_keys"}``.
    - 404 ``{"error": "study not found: <slug>"}``.
    - 500 ``{"error": "band provenance requires viva_superpowers: <e>"}``.
    - 500 ``{"error": "band provenance write failed: <e>"}`` — any other
      failure raised while writing.
    """
    body = body or {}
    slug = body.get("study")
    if not slug or not str(slug).strip():
        return {"error": "study slug required"}, 400
    slug = str(slug).strip()

    test_name = body.get("test_name")
    if not test_name or not str(test_name).strip():
        return {"error": "test_name required"}, 400
    test_name = str(test_name).strip()

    cites = body.get("cites")
    if not isinstance(cites, list) or not cites or not all(isinstance(c, str) and c for c in cites):
        return {"error": "cites must be a non-empty list of bib_keys"}, 400

    calibration_anchor = body.get("calibration_anchor")
    if calibration_anchor is not None and not isinstance(calibration_anchor, dict):
        return {"error": "calibration_anchor must be an object"}, 400

    sdir = _resolve_study_dir(Path(ws_root), slug)
    if not _study_spec_file(sdir).is_file():
        return {"error": f"study not found: {slug}"}, 404

    try:
        from discovery_engine.band_provenance import set_band_provenance
    except ImportError as e:  # noqa: BLE001
        return {"error": f"band provenance requires viva_superpowers: {e}"}, 500

    try:
        written = set_band_provenance(
            sdir, test_name=test_name, cites=cites, calibration_anchor=calibration_anchor
        )
    except Exception as e:  # noqa: BLE001
        return {"error": f"band provenance write failed: {e}"}, 500

    return {"study": slug, "test_name": test_name, "written": bool(written)}, 200


# ---------------------------------------------------------------------------
# GET /api/citation-gaps?investigation=<slug>
# ---------------------------------------------------------------------------

def build_citation_gaps(ws_root: Path, investigation: Optional[str]) -> "tuple[dict, int]":
    """GET /api/citation-gaps builder.

    Wraps ``vivarium_workbench.lib.citation_gaps.investigation_citation_gaps``.

    Returns ``({"investigation": slug, "gaps": {study_slug: {uncited_bands,
    available_references}}}, 200)`` — ``gaps`` is exactly the plugin
    function's return value (empty dict when the investigation has no
    resolvable member studies).

    - 400 ``{"error": "investigation slug required"}`` — missing/blank
      ``investigation``.
    - 404 ``{"error": "investigation not found: <slug>"}`` — no
      ``investigation.yaml`` for the slug.
    - 500 ``{"error": "citation gaps requires viva_superpowers: <e>"}``.
    """
    if not investigation or not str(investigation).strip():
        return {"error": "investigation slug required"}, 400
    slug = str(investigation).strip()

    inv_path = WorkspacePaths.load(ws_root).investigations / slug / "investigation.yaml"
    if not inv_path.is_file():
        return {"error": f"investigation not found: {slug}"}, 404

    try:
        from vivarium_workbench.lib.citation_gaps import investigation_citation_gaps
    except ImportError as e:  # noqa: BLE001
        return {"error": f"citation gaps requires viva_superpowers: {e}"}, 500

    try:
        gaps = investigation_citation_gaps(ws_root, slug)
    except Exception as e:  # noqa: BLE001
        return {"error": f"citation gaps scan failed: {e}"}, 500

    return {"investigation": slug, "gaps": gaps}, 200


# ---------------------------------------------------------------------------
# GET /api/expert-search?q=<term1,term2,...>&max_hits=5
# ---------------------------------------------------------------------------

def build_expert_search(
    ws_root: Path, q: Optional[str], max_hits: int = 5
) -> "tuple[dict, int]":
    """GET /api/expert-search builder.

    Wraps ``vivarium_workbench.lib.expert_search.search_expert_docs``.

    ``q`` is a comma-separated list of search terms (the plugin function
    takes ``terms: Iterable[str]`` — a GET query string flattens that to one
    param). Terms are split on ``,``, stripped, and empties dropped.

    Returns ``({"terms": [...], "hits": [...]}, 200)`` — ``hits`` is exactly
    the plugin function's ``[{doc, page, snippet, term}]`` list, ranked
    per-term by hit count then doc/page for determinism.

    - 400 ``{"error": "q required (comma-separated search terms)"}`` —
      missing/blank ``q`` or no non-empty terms after splitting.
    - 500 ``{"error": "expert search requires viva_superpowers: <e>"}``.
    """
    terms = [t.strip() for t in (q or "").split(",") if t.strip()]
    if not terms:
        return {"error": "q required (comma-separated search terms)"}, 400

    try:
        from vivarium_workbench.lib.expert_search import search_expert_docs
    except ImportError as e:  # noqa: BLE001
        return {"error": f"expert search requires viva_superpowers: {e}"}, 500

    try:
        hits = search_expert_docs(Path(ws_root), terms=terms, max_hits=max_hits)
    except Exception as e:  # noqa: BLE001
        return {"error": f"expert search failed: {e}"}, 500

    return {"terms": terms, "hits": hits}, 200
