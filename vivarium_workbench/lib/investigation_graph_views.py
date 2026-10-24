"""Build the typed Actionable Investigation Graph for one investigation
(RFC-0002 Phase B4): study nodes + pipeline_gate study->study edges, plus each
study's typed evidence-chain nodes/edges and validate_chain violations.
Read-only and tolerant — unknown investigation 404s, bad studies are skipped,
unresolved chain refs are dropped from edges (but still flagged by validate_chain)."""
from __future__ import annotations

from pathlib import Path

import yaml

from vivarium_workbench.lib.workspace_paths import WorkspacePaths
from vivarium_workbench.lib.node_store import load_study_nodes
from vivarium_workbench.lib.investigations import normalize_dag_edges, InvestigationSpecError
from vivarium_workbench.lib.study_spec import study_interface
from vivarium_workbench.lib.chain_derivation import derive_chain_nodes, lift_report_card_findings
from investigation_contracts import validate_chain

# Pre-evaluation statuses promoted to "evaluated" when a study carries committed
# report-card verdicts (mirrors report_views._PRE_EVAL_STATUSES).
_PRE_EVAL_STATUSES = frozenset({"build", "planned", "implementation", "design", "pending", ""})

# Two-axis ontology derivation (execution_status × verdict) + edge-relation
# typing live in the shared study_ontology module so this graph builder and the
# study-card detail serializer (report_views) can never drift.
from vivarium_workbench.lib.study_ontology import (  # noqa: E402
    canon_relation as _canon_relation,
    derive_execution_status as _derive_execution_status,
    derive_verdict as _derive_verdict,
)


def _label(node: dict) -> str:
    s = (node.get("statement") or "").strip()
    if not s:
        return node.get("id", "")
    return s if len(s) <= 80 else s[:77] + "..."


def _build_chain(slug: str, nodes: dict[str, dict]) -> dict:
    """Typed chain nodes + edges for one study. Edge targets that don't resolve
    in ``nodes`` are dropped here, but validate_chain still reports them."""
    out_nodes: list[dict] = []
    out_edges: list[dict] = []
    for nid, n in nodes.items():
        t = n.get("type")
        out_nodes.append({"id": nid, "type": t, "label": _label(n),
                          "lifecycle_state": n.get("lifecycle_state", ""),
                          "statement": str(n.get("statement", "")),
                          "outcome": n.get("outcome"),
                          "source": (n.get("provenance") or {}).get("justification", "")})
        if t == "finding":
            out_edges.append({"source": f"study/{slug}", "target": nid, "rel": "contains"})
        elif t == "evidence":
            for f in n.get("findings", []) or []:
                if f in nodes:
                    out_edges.append({"source": nid, "target": f, "rel": "cites"})
        elif t == "decision":
            for e in n.get("evidence", []) or []:
                if e in nodes:
                    out_edges.append({"source": nid, "target": e, "rel": "decides"})
        elif t == "conclusion":
            for e in n.get("evidence", []) or []:
                if e in nodes:
                    out_edges.append({"source": nid, "target": e, "rel": "concludes"})
            for d in n.get("decisions", []) or []:
                if d in nodes:
                    out_edges.append({"source": nid, "target": d, "rel": "via"})
    return {"nodes": out_nodes, "edges": out_edges, "violations": validate_chain(nodes)}


def build_investigation_graph(ws_root: Path, inv_slug: str) -> tuple[dict, int]:
    ws_root = Path(ws_root)
    wp = WorkspacePaths.load(ws_root)
    spec_path = wp.investigations / inv_slug / "investigation.yaml"
    if not spec_path.is_file():
        return {"error": f"no investigation.yaml for {inv_slug!r}"}, 404
    try:
        spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return {"error": f"unreadable investigation.yaml for {inv_slug!r}"}, 404

    use_members = spec.get("members") is not None
    member_slugs = spec.get("members") if use_members else (spec.get("studies") or [])
    member_set = set(member_slugs)

    studies_out: list[dict] = []
    study_edges: list[dict] = []
    chains: dict[str, dict] = {}
    for slug in member_slugs:
        try:
            sp = wp.study_dir(slug, must_exist=True) / "study.yaml"
        except FileNotFoundError:
            sp = wp.investigations / slug / "spec.yaml"
        if not sp.is_file():
            continue
        try:
            study_spec = yaml.safe_load(sp.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001 — skip invalid/unloadable study, never fatal
            continue
        # Safeguard (mirrors report_views): a study left at a pre-eval status but
        # carrying committed report-card verdicts (viz/report_card/*.verdict.json)
        # has a completed, graded analysis — surface the node as evaluated so its
        # cards show, instead of stalling as "investigating".
        _node_status = study_spec.get("status", "planned")
        if _node_status in _PRE_EVAL_STATUSES and study_spec.get("report_cards"):
            try:
                from vivarium_workbench.lib.study_spec import (  # noqa: PLC0415
                    has_graded_report_cards as _hgrc,
                )
                if _hgrc(ws_root, slug):
                    _node_status = "evaluated"
            except Exception:  # noqa: BLE001
                pass
        # Authored edge relations (parent_relations): parent-slug -> relation,
        # consulted below so edges carry the real leads-to/alternative-to/refutes/
        # precedes typing instead of only the synthesized input/prerequisite rel.
        _rel_by_parent = {r["study"]: r["relation"]
                          for r in (study_spec.get("parent_relations") or [])
                          if isinstance(r, dict) and r.get("study") and r.get("relation")}
        _node = {"id": f"study/{slug}", "slug": slug, "type": "study",
                 "label": study_spec.get("title") or study_spec.get("name") or slug,
                 "status": _node_status}
        # Canonical display confidence (Accepted/Investigating/Refuted/Planned)
        # derived from the same verdict the rail dot reads, so the graph node and
        # the rail never disagree. The frontend prefers this over its own
        # status-fallback derivation (`s.confidence || …`). Derive-only; optional.
        try:
            from viva_superpowers.study_verdict import derive_confidence as _dc
            _node["confidence"] = _dc(study_spec)
        except Exception:  # noqa: BLE001 — viva_superpowers optional; frontend falls back
            pass
        # Two-axis ontology (execution_status × verdict) + evidence_tier. The
        # execution axis (what happened) is always derived so every node has a
        # Progress pill; the verdict (what the evidence supports) is only set when
        # authored or derivable, so an unmarked study shows "pending". Authored
        # fields win over derivation.
        _node["execution_status"] = _derive_execution_status(study_spec, _node_status)
        _verdict = _derive_verdict(study_spec)
        if _verdict:
            _node["verdict"] = _verdict
        if study_spec.get("evidence_tier"):
            _node["evidence_tier"] = study_spec["evidence_tier"]
        studies_out.append(_node)
        if use_members:
            # New reference model: edges are derived from this member's
            # declared interface inputs, restricted to other members.
            try:
                interface = study_interface(study_spec)
            except InvestigationSpecError:
                interface = None
            if interface is not None:
                for inp in interface["inputs"]:
                    src = inp["from"]
                    if src in member_set:
                        _e = {"source": f"study/{src}", "target": f"study/{slug}",
                              "rel": "input", "artifact": inp["artifact"]}
                        _canon = _canon_relation(_rel_by_parent.get(src))
                        if _canon:
                            _e["relation"] = _canon
                        study_edges.append(_e)
        else:
            # Legacy path: edges from pipeline_gate.prerequisites.
            # normalize_dag_edges injects a "tests-passed" default condition; the
            # payload contract treats an unspecified gate as "" (no explicit gate),
            # so read explicit conditions from the raw prerequisites.
            pg = study_spec.get("pipeline_gate")
            prereqs = pg.get("prerequisites") if isinstance(pg, dict) else None
            explicit = {pr["study"]: pr["condition"]
                        for pr in (prereqs or [])
                        if isinstance(pr, dict) and pr.get("study") and "condition" in pr}
            for pre in normalize_dag_edges(study_spec):
                _e = {"source": f"study/{pre['study']}", "target": f"study/{slug}",
                      "rel": "prerequisite", "condition": explicit.get(pre["study"], "")}
                _canon = _canon_relation(_rel_by_parent.get(pre["study"]))
                if _canon:
                    _e["relation"] = _canon
                study_edges.append(_e)
        nodes = load_study_nodes(ws_root, slug)
        derived = False
        if not nodes:
            nodes = derive_chain_nodes(study_spec, slug)
            derived = bool(nodes)
        if not nodes:
            # Phase 2d: no persisted (human/API) nodes and nothing authored to
            # derive from — fall back to the study's COMPUTED report-card
            # verdicts (verdict.json artifacts) lifted into the same typed
            # evidence chain, so evidence surfaces from computed workflow
            # artifacts, not just read-time authored fields.
            from vivarium_workbench.lib.study_spec import report_card_findings_for_study
            rc_findings, _ = report_card_findings_for_study(ws_root, slug)
            nodes = lift_report_card_findings(rc_findings, slug)
            derived = derived or bool(nodes)
        chain = _build_chain(slug, nodes)
        chain["derived"] = derived
        chains[slug] = chain

    return {"investigation": inv_slug, "studies": studies_out,
            "study_edges": study_edges, "chains": chains}, 200


def _member_slug(entry):
    """Normalize one member-list entry (bare slug or {study|slug|name: ...})."""
    if isinstance(entry, str):
        return entry or None
    if isinstance(entry, dict):
        return entry.get("study") or entry.get("slug") or entry.get("name")
    return None


def _empty_argument_overlay(inv_slug: str) -> tuple[dict, int]:
    return {"investigation": inv_slug, "claims": {}, "edges": [],
            "narrative": {"units": [], "limitations": [], "open_questions": []},
            "gaps": [], "available": False}, 200


def build_argument_overlay(ws_root, inv_slug, *, derive_fn=None, narrate_fn=None,
                           audit_fn=None):
    """Server-side Argument/Narrative projection for one investigation (RFC-0005).

    Loads the investigation + its member study specs and projects the argument
    graph (`derive_argument`), the ordered narrative (`narrate`), and the audit
    gaps (`audit`) from the ``viva_superpowers`` knowledge layer. Read-only and
    tolerant: unknown investigation -> 404; a missing/raising derivation layer ->
    an empty ``available: False`` payload with 200 (the UI degrades gracefully).
    The derivation functions are injectable for testing without the dependency.
    """
    ws_root = Path(ws_root)
    wp = WorkspacePaths.load(ws_root)
    spec_path = wp.investigations / inv_slug / "investigation.yaml"
    if not spec_path.is_file():
        return {"error": f"no investigation.yaml for {inv_slug!r}"}, 404
    try:
        spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return {"error": f"unreadable investigation.yaml for {inv_slug!r}"}, 404

    if derive_fn is None or narrate_fn is None or audit_fn is None:
        try:
            from viva_superpowers.argument_store import derive_argument
            from viva_superpowers.narrative import audit as _audit, narrate as _narrate
        except Exception:  # noqa: BLE001 — derivation layer absent: degrade, never 500
            return _empty_argument_overlay(inv_slug)
        derive_fn = derive_fn or derive_argument
        narrate_fn = narrate_fn or _narrate
        audit_fn = audit_fn or _audit

    members = spec.get("members") if spec.get("members") is not None else (spec.get("studies") or [])
    studies_by_slug: dict[str, dict] = {}
    for entry in members:
        slug = _member_slug(entry)
        if not slug:
            continue
        try:
            sp = wp.study_dir(slug, must_exist=True) / "study.yaml"
        except FileNotFoundError:
            continue
        if not sp.is_file():
            continue
        try:
            studies_by_slug[slug] = yaml.safe_load(sp.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001 — skip an unloadable study, never fatal
            continue

    try:
        graph = derive_fn(spec, studies_by_slug)
        narrative = narrate_fn(graph, question=spec.get("question"))
        gaps = audit_fn(narrative, graph)
    except Exception:  # noqa: BLE001 — a derivation failure degrades, never 500s
        return _empty_argument_overlay(inv_slug)

    return {
        "investigation": inv_slug,
        "claims": graph.get("claims", {}),
        "edges": graph.get("edges", []),
        "narrative": narrative,
        "gaps": gaps,
        "available": True,
    }, 200
