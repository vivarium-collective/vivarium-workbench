"""One-shot: lift a readout's emit ``store_path`` out of ``notes`` prose into a
structured field, so authored readouts attach to the emit-plan table and the
store_path lint can validate them. Idempotent; leaves ``notes`` text intact.
"""

from __future__ import annotations

import re

# A leading dotted path like ``listeners.mass.instantaneous_growth_rate`` —
# 2+ dot-separated identifier segments at the start of the notes string.
_LEADING_PATH = re.compile(r"^\s*([A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)+)")
_DERIVED = {"derived-needed", "aspirational"}


def lift_store_paths(spec: dict) -> tuple[dict, int]:
    changed = 0
    for r in spec.get("readouts", []) or []:
        if not isinstance(r, dict):
            continue
        if r.get("store_path"):
            continue
        if (r.get("status") or "").strip() in _DERIVED:
            continue
        m = _LEADING_PATH.match(str(r.get("notes") or ""))
        if not m:
            continue
        r["store_path"] = m.group(1)
        changed += 1
    return spec, changed
