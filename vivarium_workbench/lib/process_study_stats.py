"""Per-process cross-study track record for the Registry.

A process/step *participates* in a study when the study's composite contains it.
This aggregates, per process address: how many studies participate it (via the
composites that reference the class) and a pass / inconclusive / fail tally of
those studies' ``runs[].outcomes`` report-card verdicts — so the Registry can
show a studies count + percent success, mirroring the Composites page
(:mod:`composite_study_stats`).

Self-contained source/YAML scan (no composite builds, no heavy imports); safe to
call from the HTTP process. Best-effort — never raises.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from vivarium_workbench.lib.composite_study_stats import (
    _iter_study_yamls, tally_outcomes,
)
from vivarium_workbench.lib.workspace_walk import iter_workspace_files

_SKIP = ("/.venv/", "/node_modules/", "/.git/", "/out/", "/build-cache/",
         "/__pycache__/", "/.pbg/")

# `@composite_generator(name="foo")` — the registered short name a study may
# declare (studies also declare the file stem or an FQN; we map all three).
_GEN_NAME_RE = re.compile(
    r"@composite_generator\([^)]*?name\s*=\s*[\"']([^\"']+)[\"']", re.S)


def _composite_files(ws_root: Path) -> "dict[Path, str]":
    # Same path-shape the old glob patterns (`*/composites/*.py`,
    # `*/composites/**/*.py`, `**/composites/*.py`) approximated: any `.py`
    # file with a `composites` directory anywhere among its ancestor path
    # components. `iter_workspace_files` never follows symlinked dirs, so a
    # symlinked `.venv` is never entered (see registry.py::_annotate_use_counts
    # for the identical pattern used there).
    files: dict[Path, str] = {}
    try:
        for f in iter_workspace_files(ws_root, suffixes=(".py",)):
            sp = str(f)
            if any(s in sp for s in _SKIP):
                continue
            rel_parts = f.relative_to(ws_root).parts[:-1]
            if "composites" not in rel_parts:
                continue
            try:
                files[f] = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
    except Exception:  # noqa: BLE001
        pass
    return files


def process_study_stats(ws_root, procs) -> "dict[str, dict]":
    """``{proc_address: {studies, pass, inconclusive, fail, total}}``.

    ``procs`` is the registry's process/step entries (dicts with
    ``address``/``name``). A study is attributed to a process when the study's
    declared composite is defined in a composite file that references the
    process class (by full address or class name).
    """
    ws_root = Path(ws_root)
    files = _composite_files(ws_root)
    if not files:
        return {}

    # Map every way a study might name a composite -> that composite's file text:
    # the registered generator name(s) and the file stem.
    name_text: dict[str, str] = {}
    for f, txt in files.items():
        for m in _GEN_NAME_RE.finditer(txt):
            name_text.setdefault(m.group(1), txt)
        name_text.setdefault(f.stem, txt)

    # Per (address, class-name) matchers for the registry processes/steps.
    matchers = []
    for p in procs:
        addr = p.get("address") or ""
        cname = addr.rsplit(".", 1)[-1] if addr else (p.get("name") or "")
        matchers.append((addr, re.compile(r"\b" + re.escape(cname) + r"\b") if cname else None))

    # Which processes a given composite-file text references (memoized by text id).
    _cache: dict[int, "set[str]"] = {}

    def _procs_in(txt: str) -> "set[str]":
        key = id(txt)
        hit = _cache.get(key)
        if hit is not None:
            return hit
        found: set[str] = set()
        for addr, namere in matchers:
            if (addr and addr in txt) or (namere and namere.search(txt)):
                if addr:
                    found.add(addr)
        _cache[key] = found
        return found

    from vivarium_workbench.lib.simulations_index import _study_declared_composite

    acc: dict[str, dict] = {}
    for f in _iter_study_yamls(ws_root):
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(data, dict):
            continue
        runs = data.get("runs") if isinstance(data.get("runs"), list) else []
        declared = _study_declared_composite(data)
        run_comps = [r.get("composite") for r in runs
                     if isinstance(r, dict) and r.get("composite")]
        txt = None
        for ref in ([declared] + run_comps):
            if not ref:
                continue
            name = str(ref).rsplit(".", 1)[-1]
            txt = name_text.get(name) or name_text.get(str(ref))
            if txt:
                break
        if not txt:
            continue
        participants = _procs_in(txt)
        if not participants:
            continue
        # Tally this study's report-card outcomes once, then credit each
        # participating process with them.
        b = tally_outcomes(runs)
        for addr in participants:
            e = acc.setdefault(addr, {"_studies": set(), "_slugs": set(), "pass": 0, "inconclusive": 0, "fail": 0})
            e["_studies"].add(str(f.parent))
            e["_slugs"].add(f.parent.name)
            e["pass"] += b["pass"]
            e["inconclusive"] += b["inconclusive"]
            e["fail"] += b["fail"]

    out: dict[str, dict] = {}
    for addr, e in acc.items():
        p_, i_, fa_ = e["pass"], e["inconclusive"], e["fail"]
        out[addr] = {
            "studies": len(e["_studies"]),
            # Study slugs (dir names) participating this process, so the
            # Registry can list + link them (not just show a count).
            "study_slugs": sorted(e.get("_slugs", set())),
            "pass": p_, "inconclusive": i_, "fail": fa_,
            "total": p_ + i_ + fa_,
        }
    return out
