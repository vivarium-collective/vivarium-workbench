"""vivarium_dashboard.publish — narrative export / "publish" CLI.

Sub-project #2: exports a workspace's investigations and studies into a
self-contained static bundle (per-resource JSON + per-study shells + assets +
snapshot config) that can be served with any static HTTP server.

Bundle layout::

    bundle/
    ├── index.html                  (home SPA shell)
    ├── studies/<slug>/index.html   (per-study shell, one per study)
    ├── assets/  (data-source.js, study-detail.js, style.css, ...)
    ├── api/
    │   ├── workspace.json
    │   ├── iset/<id>.json
    │   └── study/<slug>.json
    └── config.json

Usage::

    vivarium-dashboard-publish --workspace /path/to/workspace --out /tmp/bundle
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_json(path: Path, data) -> None:
    """Write *data* as JSON using the server's ``_json_default`` serializer."""
    from vivarium_dashboard.server import _json_default
    path.write_text(
        json.dumps(data, default=_json_default, allow_nan=False),
        encoding="utf-8",
    )


def _git_info(ws_root: Path) -> tuple:
    """Return ``(commit_sha, remote_url, branch_ref)``.  Tolerates non-git dirs
    (all three values become ``None``).
    """
    def _git(*args):
        try:
            r = subprocess.run(
                ["git", "-C", str(ws_root), *args],
                capture_output=True, text=True, timeout=5,
            )
            return r.stdout.strip() if r.returncode == 0 else None
        except Exception:
            return None

    commit = _git("rev-parse", "HEAD")
    remote = _git("remote", "get-url", "origin") or _git("config", "--get", "remote.origin.url")
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    return commit, remote, branch


def _normalize_asset_urls(html: str) -> str:
    """Rewrite ``src``/``href`` JS/CSS asset URLs to root-absolute
    ``/assets/<basename>`` so both template conventions are normalised in the
    bundle.

    Rules:
    - ``src="assets/foo.js"`` (relative, home template) → ``src="/assets/foo.js"``
    - ``src="/foo.js"`` (root-relative, study-detail template) → ``src="/assets/foo.js"``
    - External CDN URLs (``https://...``), ``/api/...``, and already-correct
      ``/assets/...`` URLs are **left untouched**.
    - The plotly CDN ``<script src="https://cdn.plot.ly/...">`` has an absolute
      URL → skipped automatically.
    """
    def _replace(m: re.Match) -> str:
        attr = m.group(1)   # "src" or "href"
        url  = m.group(2)   # full URL value

        # Skip externals and already-correct bundle URLs
        if url.startswith(("https://", "http://", "/api/", "/assets/")):
            return m.group(0)

        # Strip query string to get the bare filename, then rebuild
        url_no_qs = url.split("?", 1)[0]
        basename  = url_no_qs.rsplit("/", 1)[-1]
        return f'{attr}="/assets/{basename}"'

    return re.sub(
        r'\b(src|href)="([^"]+\.(?:js|css)[^"]*)"',
        _replace,
        html,
    )


def _set_snapshot_config(html: str) -> str:
    """Swap the ``__DASH_CONFIG__`` mode from *local-server* to *snapshot*."""
    return html.replace(
        'window.__DASH_CONFIG__ = { mode: "local-server" };',
        'window.__DASH_CONFIG__ = { mode: "snapshot" };',
    )


def _render_home_html(ws_root: Path) -> str:
    """Render the home SPA shell from ``index.html.j2`` with a minimal context.

    All dynamic content (investigations list, registry, datasets …) is loaded
    by JS at runtime via ``DataSource.loadWorkspace()``; the template only
    needs scalar branding variables.
    """
    import yaml
    import jinja2
    from jinja2 import select_autoescape
    from vivarium_dashboard.server import TEMPLATES_DIR

    ws: dict = {}
    wf = ws_root / "workspace.yaml"
    if wf.exists():
        try:
            ws = yaml.safe_load(wf.read_text(encoding="utf-8")) or {}
        except Exception:
            ws = {}

    dash_cfg = ws.get("dashboard") or {}
    if not isinstance(dash_cfg, dict):
        dash_cfg = {}

    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
        keep_trailing_newline=True,
    )
    tpl = env.get_template("index.html.j2")
    return tpl.render(
        workspace_name=ws.get("name", ws_root.name),
        dashboard_name=dash_cfg.get("name", ""),
        dashboard_logo="assets/vivarium-logo.png",
        active_investigation_name="",
        asset_version="",
        owner_login="",
        owner_name="",
        owner_email="",
        owner_avatar_url="",
        owner_html_url="",
        owner_initials="",
        owner_source="",
        upstream_repo="",
    )


# ---------------------------------------------------------------------------
# Core builder
# ---------------------------------------------------------------------------

def build_bundle(ws_root, out_dir) -> dict:
    """Export the workspace at *ws_root* into a static bundle at *out_dir*.

    Returns a summary dict::

        {"investigations": [...], "studies": [...], "out": "<out_dir>"}

    JSON parity guarantee: each ``api/study/<slug>.json`` file is byte-for-byte
    identical to ``GET /api/study/<slug>`` (modulo key ordering), because both
    use ``server._study_detail_spec`` + ``server._json_default``.
    """
    import vivarium_dashboard.server as srv

    ws_root = Path(ws_root)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Temporarily point the server module at ws_root so all lookups
    # (_study_detail_spec, _iset_detail_data, workspace_paths …) use the right
    # workspace.  Restore afterwards, even on exceptions.
    orig_ws = srv.WORKSPACE
    srv.WORKSPACE = ws_root
    srv._WP_CACHE.clear()
    try:
        return _do_build(ws_root, out_dir, srv)
    finally:
        srv.WORKSPACE = orig_ws
        srv._WP_CACHE.clear()


def _stub_missing_assets(out_dir: Path) -> None:
    """After all shells are rendered, scan every HTML file in the bundle for
    ``/assets/*.{js,css}`` URLs and create an empty stub for any that are absent.

    This ensures that assets referenced in templates but not present in
    STATIC_DIR (e.g. ``investigations.js`` which is dynamically generated in
    some workspaces) don't produce 404s when the bundle is served statically.
    """
    assets_dir = out_dir / "assets"
    for shell in out_dir.glob("**/*.html"):
        html = shell.read_text(encoding="utf-8")
        for m in re.finditer(r'(?:src|href)="(/assets/[^"]+\.(?:js|css))"', html):
            url = m.group(1)
            basename = url.split("/")[-1].split("?")[0]
            stub = assets_dir / basename
            if not stub.exists():
                stub.write_text(
                    f"/* {basename} — placeholder for static bundle (file not present in source) */\n",
                    encoding="utf-8",
                )


def _do_build(ws_root: Path, out_dir: Path, srv) -> dict:
    """Internal build routine — called with WORKSPACE already set to ws_root."""
    from vivarium_dashboard.server import (
        STATIC_DIR,
        _study_detail_spec,
        _workspace_home_data,
        _render_study_detail_html,
    )
    from vivarium_dashboard.lib.workspace_paths import WorkspacePaths

    wp = WorkspacePaths.load(ws_root)

    # ------------------------------------------------------------------
    # 1. Enumerate investigations and studies
    # ------------------------------------------------------------------
    investigations: list[str] = []
    inv_root = wp.investigations
    if inv_root.is_dir():
        for inv_dir in sorted(
            d for d in inv_root.iterdir()
            if d.is_dir() and (d / "investigation.yaml").is_file()
        ):
            investigations.append(inv_dir.name)

    studies: list[str] = [s.name for s in wp.iter_study_dirs()]

    # ------------------------------------------------------------------
    # 2. Write per-resource API JSON files
    # ------------------------------------------------------------------
    api_dir = out_dir / "api"
    (api_dir / "iset").mkdir(parents=True, exist_ok=True)
    (api_dir / "study").mkdir(parents=True, exist_ok=True)

    # api/workspace.json
    _write_json(api_dir / "workspace.json", _workspace_home_data(ws_root))

    # api/iset/<id>.json
    for inv_name in investigations:
        data = srv.Handler._iset_detail_data(inv_name)
        if data is not None:
            _write_json(api_dir / "iset" / f"{inv_name}.json", data)

    # api/study/<slug>.json
    for slug in studies:
        data = _study_detail_spec(slug)
        if data is not None:
            _write_json(api_dir / "study" / f"{slug}.json", data)

    # ------------------------------------------------------------------
    # 3. Copy bundled static assets → bundle/assets/
    # ------------------------------------------------------------------
    assets_dir = out_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    for src in STATIC_DIR.iterdir():
        if src.is_file():
            shutil.copy2(src, assets_dir / src.name)

    # ------------------------------------------------------------------
    # 4. Render home SPA shell → bundle/index.html
    # ------------------------------------------------------------------
    home_html = _render_home_html(ws_root)
    home_html = _normalize_asset_urls(home_html)
    home_html = _set_snapshot_config(home_html)
    (out_dir / "index.html").write_text(home_html, encoding="utf-8")

    # ------------------------------------------------------------------
    # 5. Render per-study shells → bundle/studies/<slug>/index.html
    # ------------------------------------------------------------------
    for slug in studies:
        spec = _study_detail_spec(slug)
        if spec is None:
            continue
        study_html = _render_study_detail_html(slug, spec)
        study_html = _normalize_asset_urls(study_html)
        study_html = _set_snapshot_config(study_html)
        shell_dir = out_dir / "studies" / slug
        shell_dir.mkdir(parents=True, exist_ok=True)
        (shell_dir / "index.html").write_text(study_html, encoding="utf-8")

    # ------------------------------------------------------------------
    # 5b. Stub any referenced assets missing from the bundle
    # ------------------------------------------------------------------
    _stub_missing_assets(out_dir)

    # ------------------------------------------------------------------
    # 6. Write config.json
    # ------------------------------------------------------------------
    commit, remote, branch = _git_info(ws_root)
    config = {
        "mode":               "snapshot",
        "smsApiBase":         "",
        "repo":               remote or ws_root.name,
        "commit":             commit,
        "generated_from_ref": branch,
    }
    (out_dir / "config.json").write_text(
        json.dumps(config, indent=2), encoding="utf-8"
    )

    return {
        "investigations": investigations,
        "studies":        studies,
        "out":            str(out_dir),
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv=None):
    """CLI entry point for ``vivarium-dashboard-publish``."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="vivarium-dashboard-publish",
        description=(
            "Export a vivarium-dashboard workspace into a self-contained "
            "static bundle (investigations + studies + assets + config)."
        ),
    )
    parser.add_argument(
        "--workspace", default=".",
        help="Path to the workspace root (default: current directory).",
    )
    parser.add_argument(
        "--out", required=True,
        help="Output directory for the bundle (created if absent).",
    )
    args = parser.parse_args(argv)
    summary = build_bundle(Path(args.workspace), Path(args.out))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
