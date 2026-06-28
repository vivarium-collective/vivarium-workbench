"""``GET /api/study-readouts`` worker — auto-lists a study's Readouts table from
the composite emit plan, overlaying authored ``study.yaml readouts:`` annotations.

The table is the union of (a) every emitter leaf path the composite exposes
(``available_observables(...).leaves`` — the paths the run actually saves) and
(b) authored readouts. An authored readout overlays its name/description/units/
notes onto the matching leaf (matched after stripping a leading ``agents.<n>.``).
Authored ``available`` readouts whose ``store_path`` is missing or not an emitted
leaf surface as ``not_in_emit_plan`` (never-fabricate); ``derived-needed`` /
``aspirational`` readouts (computed metrics, not raw emit paths) surface as
``derived`` and are exempt from that check.
"""

from __future__ import annotations

import re
import time as _time
from pathlib import Path
from typing import Any

import yaml

from . import active_workspace as _aw
from .observables_views import build_composite_state_for_observables

_READOUTS_CACHE: dict = {}
_READOUTS_CACHE_TTL_S = 300.0

_LINEAGE_RE = re.compile(r"^agents\.\d+\.")
_GENERIC_LEAF = {"count", "id", "value"}


def clear_cache() -> None:
    _READOUTS_CACHE.clear()


def _strip_lineage(path: str) -> str:
    """Strip a leading ``agents.<n>.`` so authored bare paths match emit leaves."""
    return _LINEAGE_RE.sub("", path or "")


def _short_name(leaf: str) -> str:
    """Readable default name from a dotted leaf path."""
    segs = [s for s in (leaf or "").split(".") if s]
    if not segs:
        return leaf or ""
    last = segs[-1]
    if last in _GENERIC_LEAF and len(segs) >= 2:
        return f"{segs[-2]}_{last}"
    return last


def _merge_readouts(spec: dict, available: dict) -> list[dict]:
    """Pure merge of emit-plan leaves + authored readouts → ordered row dicts.

    Headless-friendly (no composite build): pass ``available={"leaves": [...]}``.
    """
    leaves = list(available.get("leaves") or [])
    # Index authored readouts by lineage-stripped store_path for overlay match.
    authored = [r for r in (spec.get("readouts") or []) if isinstance(r, dict)]
    overlay: dict[str, dict] = {}
    for r in authored:
        sp = r.get("store_path")
        if isinstance(sp, str) and sp.strip():
            overlay[_strip_lineage(sp.strip())] = r

    rows: list[dict] = []
    matched_ids: set[int] = set()

    for leaf in sorted(leaves):
        key = _strip_lineage(leaf)
        ann = overlay.get(key)
        if ann is not None:
            matched_ids.add(id(ann))
        rows.append({
            "store_path": leaf,
            "name": (ann or {}).get("name") or _short_name(leaf),
            "description": (ann or {}).get("description", "") or "",
            "units": (ann or {}).get("units", "") or "",
            "index_by": (ann or {}).get("index_by"),
            "notes": (ann or {}).get("notes", "") or "",
            "annotated": ann is not None,
            "emit_status": "emitted",
        })

    # Authored readouts that did not match any emit leaf.
    for r in authored:
        if id(r) in matched_ids:
            continue
        status = (r.get("status") or "").strip()
        derived = status in ("derived-needed", "aspirational")
        rows.append({
            "store_path": (r.get("store_path") or "") if not derived else "",
            "name": r.get("name") or "readout",
            "description": r.get("description", "") or "",
            "units": r.get("units", "") or "",
            "index_by": r.get("index_by"),
            "notes": r.get("notes", "") or "",
            "annotated": True,
            "emit_status": "derived" if derived else "not_in_emit_plan",
        })

    return rows


def build_study_readouts(ws_root: Path, slug: str) -> tuple[dict, int]:
    """Worker for ``GET /api/study-readouts?study=<slug>`` → ``(payload, status)``.

    200 with ``{composite, rows, note}``. Resolution/ref errors → 4xx. If the
    composite cannot build, returns 422 with authored-only rows + an explanatory
    ``note`` (never a 500).
    """
    from .study_spec import SLUG_RE, study_spec_file
    from .spec_migration import migrate_v2_to_v3

    ws_root = Path(ws_root)
    if not SLUG_RE.match(slug or ""):
        return {"error": "invalid slug"}, 400

    study_dir = ws_root / "studies" / slug
    if not study_dir.is_dir():
        study_dir = ws_root / "investigations" / slug
    sf = study_spec_file(study_dir)
    if not sf.is_file():
        return {"error": f"study not found: {slug}"}, 404
    try:
        spec = migrate_v2_to_v3(yaml.safe_load(sf.read_text(encoding="utf-8")) or {})
    except Exception as e:  # noqa: BLE001
        return {"error": f"study spec parse failed: {e}"}, 400

    baseline = spec.get("baseline") or []
    if not (isinstance(baseline, list) and baseline and isinstance(baseline[0], dict)):
        return {"error": "study has no baseline composite", "rows": []}, 422
    ref = baseline[0].get("composite")
    if not ref:
        return {"error": "baseline entry has no composite ref", "rows": []}, 422

    ckey = ("readouts", str(ws_root), slug)
    hit = _READOUTS_CACHE.get(ckey)
    if hit is not None and (_time.time() - hit[0]) < _READOUTS_CACHE_TTL_S:
        return {**hit[1], "cached": True}, 200

    try:
        from pbg_superpowers.readout_validation import available_observables
    except Exception as e:  # noqa: BLE001
        return {"error": f"readout_validation unavailable: {e}"}, 501

    try:
        core, state, schema = build_composite_state_for_observables(ws_root, ref)
        available = available_observables(core, state, schema)
    except Exception as e:  # noqa: BLE001
        rows = _merge_readouts(spec, {"leaves": []})
        return {"composite": ref, "rows": rows,
                "note": f"composite {ref!r} could not be built — rows unverified: {e}"}, 422

    payload = {"composite": ref, "rows": _merge_readouts(spec, available), "note": ""}
    _READOUTS_CACHE[ckey] = (_time.time(), payload)
    if len(_READOUTS_CACHE) > 32:
        _READOUTS_CACHE.pop(next(iter(_READOUTS_CACHE)))
    return payload, 200


_aw.register_clear_cb(clear_cache)
