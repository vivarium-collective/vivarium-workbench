"""Resolve an investigation's figures — for the Figures tab + the ``↓ figures``
download.

Two categories per member study:

* **composites** — the post-study stitched ``figure_<N>.{svg,png}`` (the "final
  figures", one per figure study). These carry a number/title/caption and drive
  the Figures tab + per-figure downloads.
* **panels** — every *other* declared image visualization on the study (loom
  SVGs, sim PNGs, gifs). These are the raw study figures.

The ``↓ figures`` zip is the **full** archive: panels *and* composites across
all member studies, organized ``<study>/<filename>``.

Figures are auto-discovered from each study's ``visualizations[]``; an optional
``figures:`` block on the investigation overrides per-figure
number/title/caption/order/inclusion:

    figures:
      - study: fig-07
        number: 7          # optional (derived from figure_<N> / slug)
        title: "…"         # optional (derived from study.title)
        caption: "…"       # optional (derived from study.claim / purpose.mechanism)
        order: 1           # optional (derived from number)
        include: true      # optional (default true; false hides an auto figure)

The resolver never raises: a missing file, unreadable study, or malformed
``figures:`` entry is skipped so the rest still resolve.
"""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path
from typing import Optional

import yaml

from vivarium_workbench.lib.investigation_members import investigation_member_slugs
from vivarium_workbench.lib.study_charts import (
    _resolve_figure_path,
    resolve_declared_figure_ref,
)
from vivarium_workbench.lib.workspace_paths import WorkspacePaths, study_dir

# ``figure_7`` / ``figure-7`` → 7. The stitcher writes ``figure_<N>.svg``.
_COMPOSITE_RE = re.compile(r"figure[_-](\d+)$", re.IGNORECASE)
# ``fig-07`` / ``fig07`` → 7 (fallback figure number from the study slug).
_SLUG_NUM_RE = re.compile(r"fig[-_]?0*(\d+)", re.IGNORECASE)


def _load_investigation(ws_root: Path, name: str) -> Optional[dict]:
    f = WorkspacePaths.load(ws_root).investigations / name / "investigation.yaml"
    if not f.is_file():
        return None
    try:
        return yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    except Exception:
        return None


def _load_study(ws_root: Path, slug: str) -> tuple[Optional[Path], dict]:
    try:
        sdir = study_dir(ws_root, slug, must_exist=True)
    except Exception:
        return None, {}
    try:
        spec = yaml.safe_load((sdir / "study.yaml").read_text(encoding="utf-8")) or {}
    except Exception:
        spec = {}
    return sdir, spec


def _image_files(study_dir_path: Path, spec: dict) -> list[Path]:
    """Every declared downloadable-figure file on a study, in the order the
    study declares them. Deduped, existing files only. Resolution is shared with
    the render path via :func:`resolve_declared_figure_ref`, so it covers both
    the canonical ``address: <scheme>:<file>`` form and a bare file-pointer field
    (``path``/``file``/``chart``/``src``), for static images (svg/png/gif/jpg)
    *and* interactive HTML — an investigation of HTML charts still reports a
    figure count and its ``↓ figures`` zip carries them."""
    out: list[Path] = []
    seen: set[Path] = set()
    for entry in (spec.get("visualizations") or []):
        # Shared resolver with the render path: accepts both the canonical
        # ``address: <scheme>:<file>`` form and a bare file-pointer field
        # (``path``/``file``/``chart``/``src``) — images and self-contained HTML
        # — so a declared figure that renders is always also downloadable.
        ref, _is_iframe, _media = resolve_declared_figure_ref(entry)
        if not ref:
            continue
        p = _resolve_figure_path(study_dir_path, ref)
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _caption_for(spec: dict) -> str:
    claim = str(spec.get("claim") or "").strip()
    if claim:
        return claim
    mech = ((spec.get("purpose") or {}).get("mechanism")) if isinstance(spec.get("purpose"), dict) else ""
    return " ".join(str(mech or "").split())


def figures_staleness(ws_root) -> dict:
    """Per-study figure staleness, read from the figure-build manifest
    (``.pbg/figures/manifest.json``, written by ``scripts/build_all_figures.py``).

    A study is STALE when any of its figure nodes' declared input files (a saved
    loom view, a composite spec, a sim script) hash differently than when the
    figure was last built — i.e. the downloadable figure is out of date and the
    incremental builder would rebuild it. Returns ``{study: reason}``; empty when
    no manifest exists (the incremental pipeline hasn't run). Never raises — a
    freshness signal must never break the figures response."""
    import hashlib
    import json as _json

    ws_root = Path(ws_root).resolve()
    try:
        manifest = _json.loads((ws_root / ".pbg" / "figures" / "manifest.json").read_text())
    except (OSError, ValueError):
        return {}

    def _hash(rel: str) -> Optional[str]:
        try:
            return hashlib.sha256((ws_root / rel).read_bytes()).hexdigest()[:16]
        except OSError:
            return None

    stale: dict[str, str] = {}
    for key, rec in (manifest.items() if isinstance(manifest, dict) else []):
        study = str(key).split("/", 1)[0]
        if study in stale:
            continue
        for rel, recorded in (rec.get("inputs") or {}).items():
            if _hash(rel) != recorded:
                stale[study] = f"{rel} changed since last build"
                break
    return stale


def kick_figure_build(ws_root, inv_dir) -> bool:
    """Launch the investigation's declared incremental figure build in the
    BACKGROUND, so a saved loom view is reflected in the download with no manual
    rebuild — transparently. The build (investigation.yaml ``figures_build:``) is
    single-flight + self-coalescing (it re-checks for saves made while it runs),
    so this is safe to fire on every save. Returns True if launched. Never raises
    — a background figure rebuild must never break the save that triggered it."""
    import shlex
    import subprocess
    import sys as _sys

    import yaml as _yaml
    try:
        inv = _yaml.safe_load((Path(inv_dir) / "investigation.yaml").read_text()) or {}
        fb = inv.get("figures_build")
        if not fb:
            return False
        cmd = fb if isinstance(fb, str) else (fb.get("command")
                                              or f"python {fb.get('script', 'scripts/build_all_figures.py')}")
        parts = shlex.split(cmd)
        if parts and parts[0] == "python":  # resolve to the workspace's own python
            base = Path(ws_root).name.split("--")[0]
            for c in (Path(ws_root) / ".venv" / "bin" / "python",
                      Path(ws_root).parent / base / ".venv" / "bin" / "python"):
                if c.exists():
                    parts[0] = str(c)
                    break
            else:
                parts[0] = _sys.executable
        subprocess.Popen(parts, cwd=str(ws_root),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)  # detached: outlives the request
        return True
    except Exception:  # noqa: BLE001
        return False


def build_investigation_figures(ws_root, name: str) -> dict:
    """Resolve ``name``'s figures.

    Returns::

        {
          "composites": [ {study, number, title, caption, order,
                           svg_rel, png_rel|None, stale, stale_reason?}, … ],
          "files":      [ {study, arcname, rel_path}, … ], # EVERY figure file
          "n_composites": int,
          "stale":      [ study, … ],   # studies whose figures are out of date
          "n_stale":    int,
        }

    ``*_rel`` paths are workspace-root-relative POSIX strings; the API/publish
    layers turn them into live vs snapshot URLs. ``stale`` reflects whether a
    figure's declared inputs changed since it was last built (see
    :func:`figures_staleness`) — so the download can say so instead of silently
    serving stale bytes.
    """
    ws_root = Path(ws_root).resolve()  # absolute → relative_to(ws_root) is safe when callers pass '.'
    inv = _load_investigation(ws_root, name) or {}
    slugs = investigation_member_slugs(inv)

    overrides: dict[str, dict] = {}
    for f in (inv.get("figures") or []):
        if isinstance(f, dict) and f.get("study"):
            overrides[str(f["study"])] = f

    composites: list[dict] = []
    files: list[dict] = []
    for slug in slugs:
        ov = overrides.get(slug, {})
        if ov.get("include") is False:
            continue
        sdir, spec = _load_study(ws_root, slug)
        if sdir is None:
            continue
        imgs = _image_files(sdir, spec)
        for p in imgs:
            try:
                rel = p.relative_to(ws_root).as_posix()
            except ValueError:
                continue
            files.append({"study": slug, "arcname": f"{slug}/{p.name}", "rel_path": rel})

        # Composite(s): figure_<N>.svg (the post-study stitched figure).
        slug_num = _SLUG_NUM_RE.match(slug)
        for csvg in imgs:
            m = _COMPOSITE_RE.match(csvg.stem)
            if not m or csvg.suffix.lower() != ".svg":
                continue
            number = ov.get("number") or int(m.group(1) if m else (slug_num.group(1) if slug_num else 0))
            png = csvg.with_suffix(".png")
            try:
                svg_rel = csvg.relative_to(ws_root).as_posix()
                png_rel = png.relative_to(ws_root).as_posix() if png.exists() else None
            except ValueError:
                continue
            composites.append({
                "study": slug,
                "number": number,
                "title": str(ov.get("title") or spec.get("title") or slug),
                "caption": str(ov.get("caption") or _caption_for(spec)),
                "order": ov.get("order", number),
                "svg_rel": svg_rel,
                "png_rel": png_rel,
            })

    composites.sort(key=lambda c: (c["order"], c["number"]))
    stale = figures_staleness(ws_root)
    for c in composites:
        c["stale"] = c["study"] in stale
        if c["stale"]:
            c["stale_reason"] = stale[c["study"]]
    return {"composites": composites, "files": files, "n_composites": len(composites),
            "stale": sorted(stale.keys()), "n_stale": len(stale)}


_EXT_MIME = {"svg": "image/svg+xml", "png": "image/png"}


def resolve_figure_file(ws_root, name: str, number: int, ext: str) -> Optional[Path]:
    """The composite figure file for ``Figure <number>`` in ``.<ext>`` (svg|png),
    or ``None``. Backs ``GET /api/investigation/<slug>/figure/<n>.<ext>``."""
    ext = ext.lower().lstrip(".")
    if ext not in _EXT_MIME:
        return None
    ws_root = Path(ws_root).resolve()  # absolute → relative_to(ws_root) is safe when callers pass '.'
    for c in build_investigation_figures(ws_root, name)["composites"]:
        if int(c["number"]) != int(number):
            continue
        rel = c["svg_rel"] if ext == "svg" else c.get("png_rel")
        if not rel:
            return None
        p = ws_root / rel
        return p if p.is_file() else None
    return None


def build_figures_zip(ws_root, name: str) -> Optional[bytes]:
    """Zip EVERY figure file across the investigation's member studies. The
    per-study PANELS are arranged ``<study>/<filename>``; the post-study stitched
    figures (``figure_<N>.{svg,png}``) are ALSO collected together into a single
    top-level ``final/`` folder (``final/figure_1.svg`` …) so the finished figures
    sit side by side, not buried one-per-study. Returns ``None`` when the
    investigation has no figure files. Backs
    ``GET /api/investigation/<slug>/figures.zip``."""
    ws_root = Path(ws_root).resolve()  # absolute → relative_to(ws_root) is safe when callers pass '.'
    figs = build_investigation_figures(ws_root, name)
    entries = figs["files"]
    composites = figs["composites"]
    if not entries and not composites:
        return None
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        seen: set[str] = set()

        def _write(src: Path, arc: str) -> None:
            if arc in seen or not src.is_file():
                return
            seen.add(arc)
            try:
                zf.write(src, arc)
            except OSError:
                pass

        # Panels, per study — but NOT the stitched figure_<N> (those go to final/).
        for e in entries:
            if _COMPOSITE_RE.match(Path(e["rel_path"]).stem):
                continue
            _write(ws_root / e["rel_path"], e["arcname"])

        # final/: every stitched figure together, figure_<N>.{svg,png}.
        for c in composites:
            for rel in (c.get("svg_rel"), c.get("png_rel")):
                if rel:
                    _write(ws_root / rel, f"final/{Path(rel).name}")
    return buf.getvalue()


def study_figure_files(ws_root, slug: str) -> list[Path]:
    """Every declared image-figure file on a single study (panels + its own
    composite). Backs the study-scoped ``↓ figures``."""
    ws_root = Path(ws_root).resolve()  # absolute → relative_to(ws_root) is safe when callers pass '.'
    sdir, spec = _load_study(ws_root, slug)
    if sdir is None:
        return []
    return _image_files(sdir, spec)


def build_study_figures_zip(ws_root, slug: str) -> Optional[bytes]:
    """Zip a single study's figures (flat, ``<filename>``), or ``None`` when the
    study has none. Backs ``GET /api/study/<slug>/figures.zip``."""
    imgs = study_figure_files(ws_root, slug)
    if not imgs:
        return None
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        seen: set[str] = set()
        for p in imgs:
            if p.name in seen or not p.is_file():
                continue
            seen.add(p.name)
            try:
                zf.write(p, p.name)
            except OSError:
                continue
    return buf.getvalue()


def study_output_files(ws_root, slug: str) -> list[tuple[Path, str]]:
    """``(abs_path, arcname)`` for a study's downloadable OUTPUTS: image figures
    (under ``figures/``) plus its embedded HTML report pages and the sibling
    assets in their ``viz/`` directory (under ``viz/…``). Backs the study-scoped
    ``↓ outputs`` — a superset of ``study_figure_files``.

    HTML outputs come from the study's ``embed_visualizations[].url`` entries
    that point at this study's own ``/studies/<slug>/viz/…`` (a self-contained
    dashboard is just its ``index.html``; an asset-referencing viewer travels
    with the other files in its ``viz/`` directory). Never raises."""
    ws_root = Path(ws_root).resolve()
    sdir, spec = _load_study(ws_root, slug)
    if sdir is None:
        return []
    out: list[tuple[Path, str]] = []
    seen: set[str] = set()
    for p in _image_files(sdir, spec):
        arc = f"figures/{p.name}"
        if arc not in seen and p.is_file():
            seen.add(arc)
            out.append((p, arc))
    prefix = f"/studies/{slug}/"
    for entry in (spec.get("embed_visualizations") or []):
        if not isinstance(entry, dict):
            continue
        url = str(entry.get("url") or "").strip()
        if not url.startswith(prefix):
            continue  # only this study's local viz outputs (skip remote/other-study URLs)
        html_path = (ws_root / url[1:]).resolve()
        try:
            html_path.relative_to(sdir)  # containment guard: never escape the study dir
        except ValueError:
            continue
        if not html_path.is_file():
            continue
        for f in sorted(html_path.parent.iterdir()):
            if not f.is_file():
                continue
            arc = str(f.relative_to(sdir))
            if arc not in seen:
                seen.add(arc)
                out.append((f, arc))
    return out


def build_study_outputs_zip(ws_root, slug: str) -> Optional[bytes]:
    """Zip a single study's downloadable outputs (image figures under
    ``figures/`` + embedded HTML reports and their ``viz/`` assets), or ``None``
    when the study has none. Backs ``GET /api/study/<slug>/outputs.zip``."""
    items = study_output_files(ws_root, slug)
    if not items:
        return None
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p, arc in items:
            if not p.is_file():
                continue
            try:
                zf.write(p, arc)
            except OSError:
                continue
    return buf.getvalue()
