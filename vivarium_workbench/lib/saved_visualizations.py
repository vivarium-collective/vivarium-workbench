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
    """Return the bundled parsimony viewer asset dir, or None when the optional
    parsimony package is not installed (feature-detect seam).

    The package was renamed ``pbg_parsimony`` -> ``viva_parsimony`` (the new dist
    ships ``viewer/`` as ``viva_parsimony`` package-data; ``pbg_parsimony`` remains
    only as a Python import shim WITHOUT the viewer assets). Probe both names and
    return whichever module actually carries a ``viewer/`` dir, so both the old
    pinned installs and current ``viva-parsimony`` resolve."""
    try:
        import importlib.util
        for pkg in ("viva_parsimony", "pbg_parsimony"):
            spec = importlib.util.find_spec(pkg)
            if spec is None or not spec.origin:
                continue
            d = Path(spec.origin).parent / "viewer"
            if d.is_dir():
                return d
        return None
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
        # Independent of the relative_to append below, so it holds even if a
        # path can't be made workspace-relative.
        has_bt_artifact = (rc_dir / "behavior-tests.html").is_file()
        if rc_dir.is_dir():
            for rep in sorted(rc_dir.glob("*.html")):
                try:
                    rel = rep.relative_to(ws_root).as_posix()
                except ValueError:
                    continue
                name = rep.name[: -len(".html")]
                verdict = None
                vfile = rep.with_name(name + ".verdict.json")
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
                    "name": name,
                    "url": "/" + rel,
                    "verdict": verdict,
                    "created": created,
                })
        # The behavior-tests card is the DEFAULT every study gets (#98 Stage 3b).
        # Before a run produces the on-disk artifact, synthesize a pending entry
        # (no file write — the workspace is git-tracked) pointing at the
        # read-only render endpoint, so a study in Design still surfaces it.
        if not has_bt_artifact:
            try:
                from vivarium_workbench.lib import behavior_test_card, study_spec
                sf = study_spec.study_spec_file(study_dir)
                if sf.is_file():
                    spec = yaml.safe_load(sf.read_text(encoding="utf-8")) or {}
                    bts = (spec.get("behavior_tests")
                           or spec.get("expected_behavior") or [])
                    if isinstance(bts, list) and bts:
                        verdict = behavior_test_card.build_behavior_tests_verdict(spec)
                        report_cards.append({
                            "study": study,
                            "name": "behavior-tests",
                            "url": f"/api/study-behavior-card/{study}",
                            "verdict": verdict.get("overall"),
                            "created": None,
                            "synthesized": True,
                        })
            except Exception:
                pass
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
