"""Static-asset + SPA-shell serving resolvers extracted from server.py.

These are the path-resolving helpers behind the Phase-C, Batch-16 **static /
SPA-shell** routes — the ``do_GET`` page/static branches (the bundled assets,
the workspace tree, the rendered ``reports/`` output, plus the standalone
``bigraph-loom`` and ``pbg_parsimony`` viewer bundles).  Unlike the JSON view
builders, the routes serve raw files (``FileResponse``), so these helpers return
a resolved :class:`~pathlib.Path` (or ``None`` / a traversal signal) plus the
mime guess — a single implementation driving both the legacy stdlib
``server.py`` handlers and the FastAPI seam.

Resolution contract (mirrors the legacy ``do_GET`` static branch EXACTLY):

* :func:`resolve_asset` — the generic 4-step priority: bundled ``STATIC_DIR`` →
  ``assets/`` prefix-strip retry against ``STATIC_DIR`` → the workspace tree →
  the rendered ``reports/`` dir (served unconditionally, so the caller 404s when
  the returned path is not a file).
* :func:`resolve_loom_asset` — ``bigraph_loom.asset_dir()/rel``, raising
  :class:`AssetTraversal` on a ``..`` segment (the route maps it to 403).
* :func:`resolve_parsimony_asset` — the optional ``pbg_parsimony`` viewer dir
  (``None`` when the package is absent → the route 404s).
* :func:`index_html_path` — the SPA shell ``<ws>/reports/index.html`` (the route
  best-effort re-renders via ``lib.report.render_workspace_report`` first).

This module imports only ``lib`` (and the package itself for ``STATIC_DIR``),
never ``server``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import vivarium_dashboard as _vd_pkg
from vivarium_dashboard.lib.saved_visualizations import parsimony_viewer_dir
from vivarium_dashboard.lib.workspace_paths import WorkspacePaths

# Package-bundled static dir (style.css, walkthrough.js, vivarium-logo.png,
# render-helpers.js, client.js, ...).  Derived from the package, NOT from
# server.py — matches ``server.STATIC_DIR`` (``PACKAGE_ROOT / "static"``).
STATIC_DIR: Path = Path(_vd_pkg.__file__).parent / "static"

# Package-bundled Jinja templates dir (index.html.j2, study-detail shells, ...).
# Matches the retired ``server.TEMPLATES_DIR`` (``PACKAGE_ROOT / "templates"``).
TEMPLATES_DIR: Path = Path(_vd_pkg.__file__).parent / "templates"


class AssetTraversal(Exception):
    """Raised by :func:`resolve_loom_asset` (and used by the catch-all guard) to
    signal a path-traversal attempt (a ``..`` path segment).  The caller maps it
    to an HTTP 403 — mirroring the legacy ``send_response(403)`` branches."""


def guess_mime(rel: str) -> str:
    """Guess a bare mime type from a relative path's suffix.

    Moved verbatim from ``server.Handler._guess_mime`` (a staticmethod).  Returns
    the bare value with NO ``; charset=...`` suffix so the route can set it via a
    headers dict and keep the header byte-identical to ``_serve_file``.
    """
    if rel.endswith(".css"): return "text/css"
    if rel.endswith(".js"): return "application/javascript"
    if rel.endswith(".json"): return "application/json"
    if rel.endswith(".png"): return "image/png"
    if rel.endswith(".svg"): return "image/svg+xml"
    if rel.endswith(".html"): return "text/html"
    if rel.endswith(".tsv"): return "text/tab-separated-values"
    return "text/plain"


def index_html_path(ws_root: Path) -> Path:
    """The SPA shell file ``<ws>/reports/index.html``.

    The route best-effort re-renders it via ``lib.report.render_workspace_report``
    BEFORE serving (mirrors the legacy ``/`` branch), then serves this path —
    404ing when it is absent.
    """
    return WorkspacePaths.load(ws_root).reports / "index.html"


def resolve_asset(ws_root: Path, rel: str) -> Path:
    """Resolve a generic static asset for the catch-all route.

    Reproduces the legacy ``do_GET`` static branch priority EXACTLY:

    1. ``STATIC_DIR/rel`` if it is a file (package-bundled assets first);
    2. if ``rel`` starts with ``assets/``: ``STATIC_DIR/<rel-without-assets/>``
       if it is a file (the live HTML references bundled assets at ``/assets/*``
       but they live at the package root — strip + retry before the workspace,
       so a stale ``reports/assets/*`` copy can't shadow the live source);
    3. ``WORKSPACE/rel`` if it is a file (workspace tree);
    4. else ``reports/rel`` — returned UNCONDITIONALLY (served as-is, so the
       caller 404s when this final path is not a file).

    ``rel`` must already be ``lstrip("/")``-ed and traversal-checked by the
    caller (the catch-all route refuses ``..`` segments first).  Returns the
    chosen path (which may not exist).
    """
    bundled = STATIC_DIR / rel
    if bundled.is_file():
        return bundled
    if rel.startswith("assets/"):
        bundled_alt = STATIC_DIR / rel[len("assets/"):]
        if bundled_alt.is_file():
            return bundled_alt
    primary = ws_root / rel
    if primary.is_file():
        return primary
    return WorkspacePaths.load(ws_root).reports / rel


def resolve_loom_asset(rel: str) -> Path:
    """Resolve a ``bigraph-loom`` viewer asset (``bigraph_loom.asset_dir()/rel``).

    ``rel`` is the path AFTER the ``/bigraph-loom`` prefix (already stripped of a
    query string and leading ``/``); ``""`` resolves to ``index.html``.  Raises
    :class:`AssetTraversal` when ``rel`` contains a ``..`` segment (the route maps
    it to 403); otherwise returns the target path (the route 404s when absent).
    Mirrors the legacy ``/bigraph-loom`` branch.
    """
    rel = rel or "index.html"
    if ".." in rel.split("/"):
        raise AssetTraversal(rel)
    from bigraph_loom import asset_dir
    return asset_dir() / rel


def resolve_parsimony_asset(rel: str) -> Optional[Path]:
    """Resolve a ``parsimony-viewer`` asset, or ``None`` when unavailable.

    Returns ``None`` when the optional ``pbg_parsimony`` package is not installed
    (the route 404s — the Analyses gallery hides its 3D cards).  ``rel`` is the
    path AFTER the ``/parsimony-viewer`` prefix; ``""`` resolves to
    ``index.html``.  Raises :class:`AssetTraversal` on a ``..`` segment.  Mirrors
    the legacy ``/parsimony-viewer`` branch.
    """
    pv_dir = parsimony_viewer_dir()
    if pv_dir is None:
        return None
    rel = rel or "index.html"
    if ".." in rel.split("/"):
        raise AssetTraversal(rel)
    return pv_dir / rel
