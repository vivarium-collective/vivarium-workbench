"""Per-composite cross-study track record for the Composites page.

A composite can be used by many studies; each study's ``runs[].outcomes`` carry
report-card verdicts. This aggregates, per composite id: how many studies use
it, and a tally of outcomes bucketed into pass / inconclusive / fail — so the
Composites page can rank composites by how well they've held up across studies.

Pure ``ws_root``-parameterised file scan (YAML only, no heavy imports); safe to
call from the HTTP process.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from vivarium_workbench.lib.workspace_walk import iter_workspace_files

# Directories never scanned for studies (vendored/worktree/venv copies would
# double-count and are not the live workspace). ``.venv``/``.pbg``/``.claude``/
# ``.git``/``node_modules`` are already pruned by ``iter_workspace_files``
# (hidden dirs + the fixed skip set, and never followed if symlinked -- the
# NFS ``.venv`` case); ``out`` is kept here as the one extra exclusion it
# doesn't know about.
_SKIP_DIRS = {".venv", ".pbg", ".claude", ".git", "node_modules", "out"}


def _bucket(result: object) -> str:
    """Bucket a report-card outcome value into pass / fail / inconclusive.

    Observed values: PASS, PASS-NARROW, WITHIN_TOL (→ pass); FAIL (→ fail);
    PARTIAL, SKIP, INCONCLUSIVE, anything else (→ inconclusive)."""
    r = str(result or "").strip().upper()
    if not r:
        return "inconclusive"
    if "FAIL" in r:
        return "fail"
    if r.startswith("PASS") or r == "WITHIN_TOL" or r == "OK":
        return "pass"
    return "inconclusive"


def tally_outcomes(runs: "list") -> "dict[str, int]":
    """Bucket a study's ``runs[].outcomes`` report-card verdicts into a
    pass/inconclusive/fail tally (see :func:`_bucket`).

    Shared by this module (per-composite stats) and ``process_study_stats``
    (per-process stats) — both attribute the same per-study outcome tally,
    just to a different accumulator key.
    """
    tally = {"pass": 0, "inconclusive": 0, "fail": 0}
    for r in runs or []:
        oc = r.get("outcomes") if isinstance(r, dict) else None
        if not isinstance(oc, dict):
            continue
        for v in oc.values():
            res = v.get("result") if isinstance(v, dict) else v
            tally[_bucket(res)] += 1
    return tally


def _iter_study_yamls(ws_root: Path):
    for p in iter_workspace_files(Path(ws_root), names=("study.yaml",)):
        if any(part in _SKIP_DIRS or part.endswith(".worktrees") for part in p.parts):
            continue
        yield p


def composite_study_stats(ws_root: Path, known_ids: "list[str]") -> "dict[str, dict]":
    """Aggregate per-composite study usage + outcome tallies.

    Maps each study's declared composite to one of ``known_ids`` (alias-tolerant,
    via ``composite_lookup._ref_resolves``) and tallies its runs' outcomes.
    Returns ``{composite_id: {studies, pass, inconclusive, fail, total}}`` for
    every known id that at least one study uses (absent ⇒ unused)."""
    ws_root = Path(ws_root)
    from vivarium_workbench.lib.simulations_index import _study_declared_composite
    from vivarium_workbench.lib.composite_lookup import _ref_resolves

    known = list(known_ids or [])
    known_set = set(known)
    acc: dict[str, dict] = {}

    def _resolve(ref: str) -> "str | None":
        if not ref:
            return None
        if ref in known_set:
            return ref
        for kid in known:
            try:
                if _ref_resolves(ref, {kid}):
                    return kid
            except Exception:  # noqa: BLE001
                continue
        return None

    for f in _iter_study_yamls(ws_root):
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(data, dict):
            continue
        # A study declares one composite (top-level, condition, or per-run).
        declared = _study_declared_composite(data)
        runs = data.get("runs") if isinstance(data.get("runs"), list) else []
        # Per-run spec can override the study-level one.
        run_comps = {r.get("composite") for r in runs
                     if isinstance(r, dict) and r.get("composite")}
        candidates = [c for c in ([declared] + list(run_comps)) if c]
        cid = None
        for c in candidates:
            cid = _resolve(c)
            if cid:
                break
        if not cid:
            continue
        e = acc.setdefault(
            cid, {"_studies": set(), "pass": 0, "inconclusive": 0, "fail": 0})
        e["_studies"].add(str(f.parent))
        for k, v in tally_outcomes(runs).items():
            e[k] += v

    out: dict[str, dict] = {}
    for cid, e in acc.items():
        p, i, fa = e["pass"], e["inconclusive"], e["fail"]
        out[cid] = {
            "studies": len(e["_studies"]),
            "pass": p, "inconclusive": i, "fail": fa,
            "total": p + i + fa,
        }
    return out
