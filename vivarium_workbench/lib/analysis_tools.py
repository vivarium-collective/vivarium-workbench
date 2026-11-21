# vivarium_workbench/lib/analysis_tools.py
"""Compose the Analysis Tools tab: external viewers + built-in tools, each
matched to the runs/studies whose capabilities satisfy the tool's ``requires``.
Match rule: set(requires) <= set(candidate.capabilities)."""
from __future__ import annotations

from pathlib import Path

import yaml

from vivarium_workbench.lib.analysis_viewers import viewers_public
from vivarium_workbench.lib.simulations_index import build_simulations_data


def _excluded_tool_ids(ws_root) -> set[str]:
    """Tool ids this workspace opts out of, from ``workspace.yaml``
    ``ui.analysis_tools_exclude`` (a list of tool ids).

    Lets a workspace suppress a built-in or contributed Analysis Tool by id
    without a code change — e.g. a workspace that keeps a hosted 3D pack but
    does not want the local 3D viewer card excludes that contributed viewer by
    its declared id. Read directly from workspace.yaml, mirroring
    ``analysis_tools_3d._hosted_viewer_urls``."""
    try:
        ws = yaml.safe_load(
            (Path(ws_root) / "workspace.yaml").read_text(encoding="utf-8")
        ) or {}
    except Exception:  # noqa: BLE001
        return set()
    ui = ws.get("ui") or {}
    excl = ui.get("analysis_tools_exclude") or []
    return {str(x) for x in excl} if isinstance(excl, (list, tuple)) else set()


def match(requires, candidates: list[dict]) -> list[dict]:
    req = set(requires or [])
    if not req:
        return []
    return [c for c in candidates if req <= set(c.get("capabilities") or [])]


def builtin_tools() -> list[dict]:
    # Domain-specific built-in tools. They are only surfaced in a workspace
    # that can actually use them (see build_analysis_tools: a built-in with no
    # matched candidate is skipped), so e.g. the Simularium Viewer appears only
    # in a workspace with a recorded trajectory.
    #
    # The 3D (parsimony) viewer is NOT built in: it is a CONTRIBUTED module
    # (a package's workbench_viewers.get_viewers() declaring kind "embed-3d",
    # requires ["3d_pack"] and its own bundle_package). The workbench core holds
    # no parsimony-specific knowledge; see build_analysis_tools.
    return [
        {"id": "simularium-viewer", "title": "Simularium Viewer",
         "description": "Play a run's particle trajectory in the Simularium "
                        "viewer (agent positions over time).",
         "kind": "embed-simularium", "requires": ["simularium"]},
    ]


def _run_label(r: dict) -> str:
    """A concise, unique-enough label for a run's result dropdown. A run's stored
    ``label`` is a verbose composite-param dump (``cache_dir=…, config_overrides=
    {}, …``) — unusable in a picker — so prefer the run_id with any dotted module
    prefix stripped (``v2ecoli.composites.baseline__…__b026df`` -> ``baseline__…__
    b026df``), which keeps the disambiguating hash. Falls back to sim_name."""
    rid = str(r.get("run_id") or "")
    short = rid.rsplit(".", 1)[-1] if rid else ""
    return short or str(r.get("sim_name") or "") or rid or "run"


def _run_candidates(ws_root) -> list[dict]:
    data = build_simulations_data(ws_root) or {}
    out = []
    for r in data.get("simulations", []):
        out.append({"ref": r.get("run_id"),
                    "label": _run_label(r),
                    "detail": r.get("emitter_type") or "",
                    "capabilities": r.get("capabilities") or []})
    return out


def _pack_candidates(ws_root) -> list[dict]:
    from vivarium_workbench.lib.analysis_tools_3d import studies_with_3d_pack
    out = []
    for s in studies_with_3d_pack(ws_root):
        views = ", ".join(p["name"] for p in s.get("packs", [])) or "3D pack"
        out.append({"ref": s["study"], "label": s["study"],
                    "detail": views, "capabilities": ["3d_pack"],
                    "viewer_url": s.get("viewer_url")})
    return out


def _simularium_candidates(ws_root) -> list[dict]:
    from vivarium_workbench.lib.analysis_tools_simularium import (
        studies_with_simularium)
    out = []
    for s in studies_with_simularium(ws_root):
        trajs = s.get("trajectories") or []
        names = ", ".join(t["name"] for t in trajs) or "trajectory"
        out.append({"ref": s["study"], "label": s["study"],
                    "detail": names, "capabilities": ["simularium"],
                    "trajectories": trajs})
    return out


def build_analysis_tools(ws_root) -> list[dict]:
    ws_root = Path(ws_root)
    runs = _run_candidates(ws_root)
    packs = _pack_candidates(ws_root)
    sims = _simularium_candidates(ws_root)
    excluded = _excluded_tool_ids(ws_root)
    tools: list[dict] = []

    def _candidates_for(requires: list[str]) -> list[dict]:
        # Map a tool's required capability to the candidate set advertising it.
        if "3d_pack" in requires:
            return packs
        if "simularium" in requires:
            return sims
        return runs

    # Contributed viewers (a package's workbench_viewers.get_viewers). A viewer
    # that declares a capability in ``requires`` (e.g. ["3d_pack"]) is matched
    # against the candidates advertising it, EXACTLY like a built-in — so a
    # contributed embed-3d viewer is surfaced only where a study has a 3D pack,
    # and skipped (no dead card) otherwise. This is how the 3D viewer is now a
    # contributed module rather than hardcoded in the workbench. A viewer with no
    # ``requires`` keeps its own ``targets`` (launcher semantics).
    for v in viewers_public(ws_root):
        v = dict(v)
        if v.get("id") in excluded:
            continue  # workspace opted this tool out (ui.analysis_tools_exclude)
        v.setdefault("requires", [])
        if v["requires"]:
            v["matched"] = match(v["requires"], _candidates_for(v["requires"]))
            if not v["matched"]:
                continue  # required capability absent in this workspace → skip
        else:
            v["matched"] = []
        tools.append(v)

    # Built-in tools — only surfaced where the workspace can actually use them:
    # a built-in whose required capability is absent (no matched run/pack) is a
    # domain-specific tool irrelevant to this workspace, so skip it rather than
    # showing a dead "No compatible runs" card.
    for t in builtin_tools():
        t = dict(t)
        if t.get("id") in excluded:
            continue  # workspace opted this tool out (ui.analysis_tools_exclude)
        t["matched"] = match(t["requires"], _candidates_for(t["requires"]))
        if not t["matched"]:
            continue
        t["unmatched_reason"] = ""
        tools.append(t)
    return tools
