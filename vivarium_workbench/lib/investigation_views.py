"""Investigation-detail view builders extracted from server.py.

These are the ``ws_root``-parameterised public builders for the 4
investigation-detail read-only routes ported in Phase A, Batch 2.  The legacy
``server.py`` handlers now delegate to these functions (thin shims), so there
is one implementation shared by both the stdlib server and the FastAPI seam.

``investigation-rigor`` is intentionally NOT ported here: it depends on the
per-study run-merging loader (``server._study_detail_spec`` merges runs.db +
reconciles simulation_set), which ``pbg_superpowers.rigor`` reads via
``spec["runs"]`` — extracting that loader belongs with Batch 3.

Builders
--------
build_investigation_viz_html      → GET /api/investigation-viz-html
build_investigation_composites    → GET /api/investigation-composites
build_investigation_composite_doc → GET /api/investigation-composite-doc
build_investigation_state_tree    → GET /api/investigation-state-tree
build_investigation_hypotheses    → GET /api/investigation-hypotheses
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from vivarium_workbench.lib.workspace_paths import WorkspacePaths


# ---------------------------------------------------------------------------
# Error signal
# ---------------------------------------------------------------------------

class InvViewError(Exception):
    """Raised by builders to signal a non-200 HTTP response.

    ``body`` is the complete JSON-serialisable error dict (e.g.
    ``{"error": "..."}``, possibly with extra fields like ``viz_files: []``).
    ``status`` is the HTTP status code (400, 404, or 500).  Both the stdlib
    shim and the FastAPI route catch this and return the body verbatim so the
    error contract is defined once, in the builder.
    """

    def __init__(self, body: dict, status: int) -> None:
        super().__init__(body.get("error", ""))
        self.body = body
        self.status = status


# ---------------------------------------------------------------------------
# Private path helpers (ws_root-parameterised mirrors of server.py helpers)
# ---------------------------------------------------------------------------

def _study_dir(ws_root: Path, name: str) -> Path:
    """Resolve a study directory — nested-first, then flat, then legacy.

    Mirrors ``server._study_dir`` parameterised on ``ws_root`` instead of the
    module-level ``WORKSPACE`` global.
    """
    wp = WorkspacePaths.load(ws_root)
    try:
        return wp.study_dir(name)
    except FileNotFoundError:
        pass
    flat = wp.studies / name
    if flat.is_dir():
        return flat
    return wp.investigations / name


def _study_spec_path(ws_root: Path, name: str) -> Path:
    """Resolve the study spec file (``study.yaml`` → ``spec.yaml`` fallback).

    Mirrors ``server._study_spec_path`` parameterised on ``ws_root``.
    """
    study_dir = _study_dir(ws_root, name)
    for fname in ("study.yaml", "spec.yaml"):
        p = study_dir / fname
        if p.is_file():
            return p
    return study_dir / "study.yaml"   # not-found default


# ---------------------------------------------------------------------------
# Public builders
# ---------------------------------------------------------------------------

def build_investigation_viz_html(
    ws_root: Path,
    investigation: str,
    run_id: str,
) -> dict:
    """Build the GET /api/investigation-viz-html payload for *ws_root*.

    Returns ``{viz_files: [{name, html_path}]}`` where ``html_path`` is the
    workspace-relative path the static-file handler can serve.  Returns an
    empty ``viz_files`` list when the viz directory does not exist.

    Raises ``InvViewError`` (400) when ``investigation`` or ``run_id`` is empty
    — body is ``{error, viz_files: []}`` (matching the legacy handler).

    Mirrors ``server.Handler._get_investigation_viz_html``.
    """
    if not investigation or not run_id:
        raise InvViewError(
            {"error": "investigation and run_id are required", "viz_files": []},
            400,
        )
    study_dir = _study_dir(ws_root, investigation)
    viz_dir = study_dir / "viz" / run_id
    if not viz_dir.is_dir():
        return {"viz_files": []}
    out = []
    for html_file in sorted(viz_dir.glob("*.html")):
        out.append({
            "name": html_file.stem,
            "html_path": str(html_file.relative_to(ws_root)),
        })
    return {"viz_files": out}


def build_investigation_composites(ws_root: Path, investigation: str) -> dict:
    """Build the GET /api/investigation-composites payload for *ws_root*.

    Returns ``{composites: [{name, source, params}]}``.  Reads the v3
    ``baseline`` list from the investigation's spec; each entry is projected
    to ``{name, source (was composite), params}``.

    Raises ``InvViewError``:
    - 400 when ``investigation`` is empty or the spec is malformed.
    - 404 when no spec file exists for the given name.

    Mirrors ``server.Handler._get_investigation_composites``.
    """
    from vivarium_workbench.lib.investigations import load_spec, InvestigationSpecError

    if not investigation:
        raise InvViewError({"error": "investigation is required"}, 400)
    spec_path = _study_spec_path(ws_root, investigation)
    if not spec_path.is_file():
        raise InvViewError({"error": f"investigation '{investigation}' not found"}, 404)
    try:
        spec = load_spec(spec_path)
    except InvestigationSpecError as e:
        raise InvViewError({"error": f"spec error: {e}"}, 400)
    items = [
        {
            "name":   b.get("name", ""),
            "source": b.get("composite", ""),
            "params": b.get("params") or {},
        }
        for b in (spec.get("baseline") or [])
        if isinstance(b, dict)
    ]
    return {"composites": items}


def build_investigation_composite_doc(
    ws_root: Path,
    investigation: str,
    composite: str,
) -> dict:
    """Build the GET /api/investigation-composite-doc payload for *ws_root*.

    Returns ``{state: <parsed composite YAML>}`` as a JSON-serialisable dict.

    Raises ``InvViewError``:
    - 400 when ``investigation`` or ``composite`` is empty.
    - 404 when the composite YAML file does not exist.
    - 500 on YAML parse failure.

    Mirrors ``server.Handler._get_investigation_composite_doc``.
    """
    if not investigation or not composite:
        raise InvViewError({"error": "investigation + composite required"}, 400)
    study_dir = _study_dir(ws_root, investigation)
    composite_path = study_dir / "composites" / f"{composite}.yaml"
    if not composite_path.is_file():
        raise InvViewError({"error": "composite document not found"}, 404)
    try:
        doc = yaml.safe_load(composite_path.read_text(encoding="utf-8")) or {}
    except Exception as e:  # noqa: BLE001
        raise InvViewError({"error": f"parse failed: {e}"}, 500)
    return {"state": doc}


def build_investigation_state_tree(
    ws_root: Path,
    investigation: str,
    composite: str,
) -> dict:
    """Build the GET /api/investigation-state-tree payload for *ws_root*.

    Reads ``study_dir(investigation)/composites/<composite>.yaml`` and flattens
    its ``state`` into a list of node records via
    ``lib.composite_recipes.walk_state_tree``.  Returns ``{nodes: [...]}``.

    Raises ``InvViewError``:
    - 400 when ``investigation`` or ``composite`` is empty.
    - 404 when the composite YAML file does not exist (body carries the
      resolved path, matching the legacy handler).
    - 500 on YAML parse failure.

    Mirrors ``server.Handler._get_investigation_state_tree``.
    """
    from vivarium_workbench.lib.composite_recipes import walk_state_tree

    if not investigation or not composite:
        raise InvViewError({"error": "investigation + composite required"}, 400)
    composite_path = _study_dir(ws_root, investigation) / "composites" / f"{composite}.yaml"
    if not composite_path.is_file():
        raise InvViewError(
            {"error": f"composite document not found: {composite_path}"}, 404
        )
    try:
        doc = yaml.safe_load(composite_path.read_text(encoding="utf-8")) or {}
    except Exception as e:  # noqa: BLE001
        raise InvViewError({"error": f"failed to parse composite: {e}"}, 500)
    return {"nodes": walk_state_tree(doc)}


def build_investigation_hypotheses(ws_root: Path, name: str) -> dict:
    """Build the GET /api/investigation-hypotheses payload for *ws_root*.

    Returns ``{hypotheses: [...], investigation: name}`` always (never raises).
    The ``hypotheses`` list carries computed ``support_log`` fields (via
    ``pbg_superpowers.hypotheses.rollup_support`` / ``score_support``).  An
    absent ``pbg_superpowers``, missing investigation, or compute failure
    returns the authored hypotheses unchanged (or an empty list) rather than
    a 500.

    Extracted from ``server._investigation_hypotheses`` (module-level function).
    Mirrors ``server.Handler._investigation_hypotheses_test``.
    """
    wp = WorkspacePaths.load(ws_root)
    base: dict = {"hypotheses": [], "investigation": name}

    inv_path = wp.investigations / name / "investigation.yaml"
    if not inv_path.is_file():
        return base
    try:
        inv_spec: dict = yaml.safe_load(inv_path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return base
    if not isinstance(inv_spec, dict):
        return base

    authored = inv_spec.get("hypotheses")
    authored = authored if isinstance(authored, list) else []
    base["hypotheses"] = authored
    if not authored:
        return base

    # Member study specs for support-log computation.
    study_specs = []
    for s in (inv_spec.get("studies") or []):
        slug: Optional[str] = s.get("name") if isinstance(s, dict) else s
        if not slug:
            continue
        f = wp.studies / str(slug) / "study.yaml"
        if not f.is_file():
            continue
        try:
            sp = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            continue
        if isinstance(sp, dict):
            study_specs.append(sp)

    # 1) Preferred: rollup_support returns the enriched inv_spec (or list).
    try:
        from pbg_superpowers.hypotheses import rollup_support  # type: ignore[import]
    except Exception:  # noqa: BLE001
        rollup_support = None
    if rollup_support is not None:
        try:
            enriched = rollup_support(inv_spec, study_specs)
            if isinstance(enriched, dict):
                hyps = enriched.get("hypotheses")
                if isinstance(hyps, list):
                    base["hypotheses"] = hyps
                    return base
            elif isinstance(enriched, list):
                base["hypotheses"] = enriched
                return base
        except Exception:  # noqa: BLE001
            pass

    # 2) Fallback: score_support per hypothesis.
    try:
        from pbg_superpowers.hypotheses import score_support  # type: ignore[import]
    except Exception:  # noqa: BLE001
        return base
    out = []
    for h in authored:
        if not isinstance(h, dict):
            continue
        h2 = dict(h)
        try:
            log = score_support(h, study_specs)
            if isinstance(log, list):
                h2["support_log"] = log
        except Exception:  # noqa: BLE001
            pass
        out.append(h2)
    base["hypotheses"] = out
    return base
