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
    without a code change — e.g. sms-ecoli descoped the 3D/structural layer
    (moved to a separate repo) but keeps a hosted 3D pack, so it excludes
    ``parsimony-viewer`` while still advertising the pack. Read directly from
    workspace.yaml, mirroring ``analysis_tools_3d._hosted_viewer_urls``."""
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
    # matched candidate is skipped), so e.g. the Parsimony Viewer appears in a
    # v2ecoli workspace with a 3D pack but not in an HRA/atlas workspace.
    return [
        {"id": "parsimony-viewer", "title": "Parsimony Viewer",
         "description": "3D molecular packing of a cell — saved views at "
                        "declared times.",
         "kind": "embed-3d", "requires": ["3d_pack"]},
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

    pack_ids = {c["ref"] for c in packs}

    # external contributed viewers (may or may not declare requires). The
    # built-in Parsimony Viewer natively renders every 3D pack — it resolves the
    # hosted/bundled viewer itself and always opens — so a legacy contributed
    # viewer whose targets are ALL 3D-pack studies is a duplicate of it (and, in
    # practice, the fragile one: its launch goes through the env worker and can
    # 404). Drop such a viewer so there is exactly one, always-working 3D card.
    native_3d = bool(packs)
    for v in viewers_public(ws_root):
        v = dict(v)
        if v.get("id") in excluded:
            continue  # workspace opted this tool out (ui.analysis_tools_exclude)
        v.setdefault("requires", [])
        tgts = v.get("targets") or []
        if (native_3d and not v["requires"] and tgts
                and all(isinstance(t, dict) and t.get("study") in pack_ids
                        for t in tgts)):
            continue  # duplicate of the built-in Parsimony Viewer
        v["matched"] = match(v["requires"], runs) if v["requires"] else []
        tools.append(v)

    # built-in tools — only surfaced where the workspace can actually use them:
    # a built-in whose required capability is absent (no matched run/pack) is
    # a domain-specific tool irrelevant to this workspace, so skip it rather
    # than showing a dead "No compatible runs" card.
    for t in builtin_tools():
        t = dict(t)
        if t.get("id") in excluded:
            continue  # workspace opted this tool out (ui.analysis_tools_exclude)
        if "3d_pack" in t["requires"]:
            cands = packs
        elif "simularium" in t["requires"]:
            cands = sims
        else:
            cands = runs
        t["matched"] = match(t["requires"], cands)
        if not t["matched"]:
            continue
        t["unmatched_reason"] = ""
        tools.append(t)

    # Molecule Explorer (v2ecoli.browser) — a WORKSPACE-level tool, not matched to a
    # single run: it indexes the whole model (every molecule / reaction / process and
    # what changes them in a run) and has its own run picker. Surfaced whenever the
    # workspace has a ParCa cache it can index; opens full-window via the
    # launch-on-demand redirect (GET /api/explorer-open), like the other viewers.
    if ("molecule-explorer" not in excluded
            and (ws_root / "out" / "cache" / "simData.cPickle").is_file()):
        _target = {"href": "/api/explorer-open", "label": "model + recorded runs"}
        tools.append({
            "id": "molecule-explorer",
            "title": "Molecule Explorer",
            "description": (
                "Browse every molecule, reaction and process in the model — and what "
                "changes them in a run. Pick the sim in the Explorer's own run picker."
            ),
            "kind": "launcher",
            "requires": [],
            "targets": [_target],
            "matched": [_target],
            "unmatched_reason": "",
        })
    return tools
