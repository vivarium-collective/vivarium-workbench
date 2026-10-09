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
* :func:`resolve_loom_asset` — ``vivarium_workbench.loom_assets.asset_dir()/rel``, raising
  :class:`AssetTraversal` on a ``..`` segment (the route maps it to 403).
* :func:`resolve_parsimony_asset` — the optional ``pbg_parsimony`` viewer dir
  (``None`` when the package is absent → the route 404s).
* :func:`index_html_path` — the SPA shell ``<ws>/reports/index.html`` (the route
  best-effort re-renders via ``lib.report.render_workspace_report`` first).

This module imports only ``lib`` (and the package itself for ``STATIC_DIR``),
never ``server``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import vivarium_workbench as _vd_pkg
from vivarium_workbench.lib.saved_visualizations import parsimony_viewer_dir
from vivarium_workbench.lib.workspace_paths import WorkspacePaths

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


# Databases, event logs and pid files: run data the API serves in a shaped form, never as raw files.
_UNSERVED_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".jsonl", ".pid")


def _within(path: Path, root: Path) -> bool:
    """True iff ``path`` really lives under ``root`` once symlinks are followed."""
    try:
        Path(os.path.realpath(path)).relative_to(os.path.realpath(root))
    except ValueError:
        return False
    return True


def is_servable(rel: str) -> bool:
    """True iff ``rel`` (a URL path, leading slash already stripped) may be served from the workspace.

    Refuses any dot-segment — ``.git``, ``.env``, ``.pbg`` (run databases, server state, the audit log),
    ``.venv``, and ``..`` itself — plus absolute and NUL-carrying paths. The dashboard's own pages and assets
    never live under a dot-name, so nothing legitimate is lost.
    """
    return (bool(rel) and "\x00" not in rel and not os.path.isabs(rel)
            and not any(seg.startswith(".") for seg in rel.split("/") if seg)
            and not rel.lower().endswith(_UNSERVED_SUFFIXES))


def resolve_asset(ws_root: Path, rel: str) -> Optional[Path]:
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

    ``rel`` must already be ``lstrip("/")``-ed by the caller.  Returns ``None``
    when ``rel`` is not :func:`is_servable`, or resolves (through a symlink) to a
    file outside the tree it is served from (the caller 404s), else the chosen
    path (which may not exist).
    """
    if not is_servable(rel):
        return None
    bundled = STATIC_DIR / rel
    if bundled.is_file():
        return bundled
    if rel.startswith("assets/"):
        bundled_alt = STATIC_DIR / rel[len("assets/"):]
        if bundled_alt.is_file():
            return bundled_alt
    primary = ws_root / rel
    if primary.is_file():
        return primary if _within(primary, ws_root) else None  # a symlink out of the tree is not served
    reports = WorkspacePaths.load(ws_root).reports
    final = reports / rel
    return final if (not final.is_file() or _within(final, reports)) else None


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
    from vivarium_workbench.loom_assets import asset_dir
    return asset_dir() / rel


def loom_bundle_present() -> bool:
    """True when the vendored bigraph-loom bundle (``_dist/index.html``) exists.

    False means the server is running against a package whose ``loom/_dist`` is
    absent — e.g. an editable install whose source worktree was deleted/moved out
    from under a long-lived server, or a build that never vendored the bundle. In
    that state ``/bigraph-loom/index.html`` 404s and every composite-card loom
    EMBED renders as a blank iframe pane with no error (see
    :func:`loom_bundle_missing_html`)."""
    from vivarium_workbench.loom_assets import asset_dir
    try:
        return (asset_dir() / "index.html").is_file()
    except Exception:  # noqa: BLE001 — a resolution failure is "not present"
        return False


def loom_bundle_missing_html() -> str:
    """A self-contained HTML page shown IN the loom iframe when the bundle is
    absent, instead of the browser's blank 404 body.

    The composite-card embed mounts ``/bigraph-loom/index.html`` in an iframe; a
    bare 404 there leaves the user staring at an empty pane with no clue what
    broke. This page names the cause (missing vendored ``_dist``) and the fix
    (reinstall the workbench / restart the server from a live checkout), so the
    failure is visible and actionable rather than silent. It carries its own
    styles and theme handling so it needs no sibling assets (which are also
    absent)."""
    from vivarium_workbench.loom_assets import asset_dir
    where = str(asset_dir())
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"UTF-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">"
        "<title>bigraph-loom unavailable</title><style>"
        ":root{color-scheme:light dark}"
        "body{margin:0;min-height:100vh;display:flex;align-items:center;"
        "justify-content:center;font-family:system-ui,sans-serif;"
        "background:#fff7ed;color:#7c2d12;padding:24px;box-sizing:border-box}"
        "@media (prefers-color-scheme:dark){body{background:#2a1a0e;color:#fcd9b6}}"
        ".box{max-width:520px}.box h1{font-size:15px;margin:0 0 8px}"
        ".box p{font-size:13px;line-height:1.5;margin:0 0 8px}"
        "code{background:rgba(124,45,18,.12);padding:1px 5px;border-radius:4px;"
        "font-size:12px;word-break:break-all}"
        "@media (prefers-color-scheme:dark){code{background:rgba(252,217,182,.12)}}"
        "</style></head><body><div class=\"box\">"
        "<h1>⚠ The bigraph-loom viewer bundle is missing</h1>"
        "<p>This server cannot find its vendored loom build, so the graph view "
        "cannot render. The bundle was expected at:</p>"
        f"<p><code>{where}</code></p>"
        "<p>This usually means the running server was launched from a "
        "<code>vivarium_workbench</code> install whose source directory was later "
        "moved or deleted (e.g. a git worktree removed out from under a long-lived "
        "server). Reinstall the workbench and restart the server from a live "
        "checkout — then reload this view.</p>"
        "</div></body></html>"
    )


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
