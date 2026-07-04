"""Discovery of saved interactive visualizations, as library code.

Extracted from server.py so the FastAPI ``/api/saved-visualizations`` route can
build the payload without reaching into the stdlib server. A pure filesystem
scan of the workspace's study dirs for packed 3D scenes (``viz/3d/*.pack.json``),
and comparison report cards (``viz/report_card/*.html``).
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from vivarium_workbench.lib.workspace_paths import WorkspacePaths


def parsimony_viewer_dir() -> Path | None:
    """Return the bundled ``pbg_parsimony`` viewer asset dir, or None when the
    optional ``pbg_parsimony`` package is not installed (feature-detect seam)."""
    try:
        import importlib.util
        spec = importlib.util.find_spec("pbg_parsimony")
        if spec is None or not spec.origin:
            return None
        d = Path(spec.origin).parent / "viewer"
        return d if d.is_dir() else None
    except Exception:
        return None


def build_saved_visualizations(ws_root) -> dict:
    """Discover saved, interactive visualizations in the workspace.

    Returns ``{parsimony_available, saved: [...], report_cards: [...]}``.
    Pure (no socket I/O) — call with an explicit ``ws_root``.
    """
    ws_root = Path(ws_root)
    wp = WorkspacePaths.load(ws_root)
    saved: list[dict] = []
    report_cards: list[dict] = []
    for study_dir in wp.iter_study_dirs():
        study = study_dir.name
        rc_dir = study_dir / "viz" / "report_card"
        if rc_dir.is_dir():
            for rep in sorted(rc_dir.glob("*.html")):
                try:
                    rel = rep.relative_to(ws_root).as_posix()
                except ValueError:
                    continue
                verdict = None
                vfile = rep.with_name(rep.name[: -len(".html")] + ".verdict.json")
                if vfile.is_file():
                    try:
                        verdict = json.loads(
                            vfile.read_text(encoding="utf-8")).get("overall")
                    except Exception:
                        verdict = None
                try:
                    created = int(rep.stat().st_mtime)
                except Exception:
                    created = None
                report_cards.append({
                    "study": study,
                    "name": rep.name[: -len(".html")],
                    "url": "/" + rel,
                    "verdict": verdict,
                    "created": created,
                })
        viz3d = study_dir / "viz" / "3d"
        if viz3d.is_dir():
            for pack in sorted(viz3d.glob("*.pack.json")):
                try:
                    rel = pack.relative_to(ws_root).as_posix()
                except ValueError:
                    continue
                meta = pack.with_name(pack.name.replace(".pack.json", ".meta.json"))
                meta_url = None
                n_placed = None
                if meta.is_file():
                    try:
                        meta_url = "/" + meta.relative_to(ws_root).as_posix()
                    except ValueError:
                        meta_url = None
                    try:
                        md = json.loads(meta.read_text(encoding="utf-8"))
                        ing = md.get("ingredients") or {}
                        total = sum(
                            int(v.get("count", 0))
                            for v in ing.values() if isinstance(v, dict)
                        )
                        n_placed = total or None
                    except Exception:
                        n_placed = None
                try:
                    created = int(pack.stat().st_mtime)
                except Exception:
                    created = None
                saved.append({
                    "study": study,
                    "name": pack.name[: -len(".pack.json")],
                    "pack_url": "/" + rel,
                    "meta_url": meta_url,
                    "n_placed": n_placed,
                    "created": created,
                })
    try:
        ws = yaml.safe_load((ws_root / "workspace.yaml").read_text(encoding="utf-8")) or {}
    except Exception:
        ws = {}
    ui = ws.get("ui") or {}

    # Optional per-pack external viewer URL (ui.viz_viewer_urls: {<pack-name>: url}).
    viewer_urls = ui.get("viz_viewer_urls") or {}
    if isinstance(viewer_urls, dict):
        for entry in saved:
            url = viewer_urls.get(entry["name"])
            if url:
                entry["viewer_url"] = str(url)

    return {
        "parsimony_available": parsimony_viewer_dir() is not None,
        "saved": saved,
        "report_cards": report_cards,
    }
