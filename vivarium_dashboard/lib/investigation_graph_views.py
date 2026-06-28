"""Build the typed Actionable Investigation Graph for one investigation
(RFC-0002 Phase B4): study nodes + pipeline_gate study->study edges, plus each
study's typed evidence-chain nodes/edges and validate_chain violations.
Read-only and tolerant — unknown investigation 404s, bad studies are skipped,
unresolved chain refs are dropped from edges (but still flagged by validate_chain)."""
from __future__ import annotations

from pathlib import Path

import yaml

from vivarium_dashboard.lib.workspace_paths import WorkspacePaths
from vivarium_dashboard.lib.node_store import load_study_nodes
from vivarium_dashboard.lib.investigations import normalize_dag_edges
from investigation_contracts import validate_chain


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
                          "lifecycle_state": n.get("lifecycle_state", "")})
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

    studies_out: list[dict] = []
    study_edges: list[dict] = []
    chains: dict[str, dict] = {}
    for slug in (spec.get("studies") or []):
        try:
            sp = wp.study_dir(slug) / "study.yaml"
        except FileNotFoundError:
            sp = wp.investigations / slug / "spec.yaml"
        if not sp.is_file():
            continue
        try:
            study_spec = yaml.safe_load(sp.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001 — skip invalid/unloadable study, never fatal
            continue
        studies_out.append({"id": f"study/{slug}", "slug": slug, "type": "study",
                            "label": study_spec.get("title") or study_spec.get("name") or slug,
                            "status": study_spec.get("status", "planned")})
        # normalize_dag_edges injects a "tests-passed" default condition; the
        # payload contract treats an unspecified gate as "" (no explicit gate),
        # so read explicit conditions from the raw prerequisites.
        pg = study_spec.get("pipeline_gate") or {}
        explicit = {pr["study"]: pr["condition"]
                    for pr in (pg.get("prerequisites") or [])
                    if isinstance(pr, dict) and pr.get("study") and "condition" in pr}
        for pre in normalize_dag_edges(study_spec):
            study_edges.append({"source": f"study/{pre['study']}", "target": f"study/{slug}",
                               "rel": "prerequisite",
                               "condition": explicit.get(pre["study"], "")})
        chains[slug] = _build_chain(slug, load_study_nodes(ws_root, slug))

    return {"investigation": inv_slug, "studies": studies_out,
            "study_edges": study_edges, "chains": chains}, 200
