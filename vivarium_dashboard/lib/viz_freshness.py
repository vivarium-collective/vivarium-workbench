# VENDORED COPY — canonical source is pbg_superpowers/viz_freshness.py.
# Keep identical to the canonical; drift is caught by tests/test_viz_freshness_mirror.py.
"""Pure run->chart freshness core (single source of truth; vendored into the
dashboard). A chart's <chart>.meta.json records which run produced it; freshness
compares that against the study's latest run."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

FRESH, STALE, UNRENDERED, UNTRACKED = "fresh", "stale", "unrendered", "untracked"


def _meta_path(chart: Path) -> Path:
    return chart.with_suffix(chart.suffix + ".meta.json")


def _hash(chart: Path) -> str:
    h = hashlib.sha256(chart.read_bytes()).hexdigest()
    return f"sha256:{h}"


def stamp_meta(chart: Path, *, source_run_id: str | None, generation_id: str | None,
               rendered_at: float, command: str) -> None:
    """Write/overwrite <chart>.meta.json with provenance for `chart`."""
    meta = {
        "source_run_id": source_run_id,
        "generation_id": generation_id,
        "rendered_at": float(rendered_at),
        "command": command,
        "content_hash": _hash(chart) if chart.is_file() else None,
    }
    _meta_path(chart).write_text(json.dumps(meta, indent=2), encoding="utf-8")


def read_meta(chart: Path) -> dict | None:
    p = _meta_path(chart)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def chart_freshness(study_dir: Path, entry: dict, latest: dict | None) -> str:
    """fresh | stale | unrendered (for a declared visualizations[] entry)."""
    chart = Path(study_dir) / entry.get("chart", "")
    if not chart.is_file():
        return UNRENDERED
    meta = read_meta(chart)
    if meta is None:
        return STALE
    if not latest:
        return STALE
    if meta.get("source_run_id") != latest.get("run_id"):
        return STALE
    completed = latest.get("completed_at")
    if completed is not None and (meta.get("rendered_at") or 0) < float(completed):
        return STALE
    return FRESH


def manifest_diff(study_dir: Path, entries: list[dict]) -> dict:
    """Compare declared entries against charts/*.svg|png on disk.

    Returns {"declared": [...], "untracked": [...]} where untracked are chart
    files present on disk but absent from visualizations[]."""
    declared = {e.get("chart") for e in entries if e.get("chart")}
    charts_dir = Path(study_dir) / "charts"
    on_disk = set()
    if charts_dir.is_dir():
        for p in charts_dir.glob("*.svg"):
            on_disk.add(f"charts/{p.name}")
        for p in charts_dir.glob("*.png"):
            on_disk.add(f"charts/{p.name}")
    return {"declared": sorted(declared), "untracked": sorted(on_disk - declared)}
