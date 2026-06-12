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


def _normalize_base_path(base_path: str) -> str:
    """Normalize a *base_path* value: strip trailing slashes, ensure a leading
    slash when the value is non-empty.  Empty string (root hosting) is returned
    as-is.

    >>> _normalize_base_path("/v2ecoli/dashboard/")
    '/v2ecoli/dashboard'
    >>> _normalize_base_path("v2ecoli/dashboard")
    '/v2ecoli/dashboard'
    >>> _normalize_base_path("")
    ''
    """
    if not base_path:
        return ""
    bp = base_path.rstrip("/")
    if not bp.startswith("/"):
        bp = "/" + bp
    return bp


def _apply_base_path(html: str, base_path: str) -> str:
    """Prefix root-absolute ``/assets/`` and ``/bigraph-loom/`` URLs in *html*
    with *base_path*.

    Called AFTER ``_normalize_asset_urls()`` so all JS/CSS refs are already in
    ``/assets/<name>`` form.  Does **not** touch external URLs (``https://``)
    or ``/api/`` paths (those are prefixed at runtime by ``data-source.js``
    via the ``basePath`` config key).
    """
    if not base_path:
        return html

    def _prefix(m: re.Match) -> str:
        attr = m.group(1)
        url = m.group(2)
        if url.startswith(("/assets/", "/bigraph-loom/")):
            return f'{attr}="{base_path}{url}"'
        return m.group(0)

    return re.sub(r'\b(src|href)="(/[^"]+)"', _prefix, html)


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


def _set_snapshot_config(
    html: str,
    interactive_url: str = "",
    base_path: str = "",
) -> str:
    """Swap the ``__DASH_CONFIG__`` mode from *local-server* to *snapshot*.

    Optionally injects:
    - ``interactiveUrl`` — so the snapshot banner can link to the interactive
      version (``--interactive-url`` CLI arg).
    - ``basePath`` — URL prefix for subpath hosting so ``data-source.js`` can
      resolve ``/api/*.json`` paths correctly when the bundle is served under a
      non-root path (``--base-path`` CLI arg).  Only injected when non-empty.
    """
    import json as _json
    config_js = 'window.__DASH_CONFIG__ = { mode: "snapshot"'
    if interactive_url:
        config_js += ', interactiveUrl: ' + _json.dumps(interactive_url)
    if base_path:
        config_js += ', basePath: ' + _json.dumps(base_path)
    config_js += ' };'
    return html.replace(
        'window.__DASH_CONFIG__ = { mode: "local-server" };',
        config_js,
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

def build_bundle(
    ws_root,
    out_dir,
    *,
    interactive_url: str = "",
    base_path: str = "",
) -> dict:
    """Export the workspace at *ws_root* into a static bundle at *out_dir*.

    Returns a summary dict::

        {"investigations": [...], "studies": [...], "out": "<out_dir>"}

    JSON parity guarantee: each ``api/study/<slug>.json`` file is byte-for-byte
    identical to ``GET /api/study/<slug>`` (modulo key ordering), because both
    use ``server._study_detail_spec`` + ``server._json_default``.

    Args:
        interactive_url: Optional URL injected into the snapshot banner's
            "Open interactive version" link.  Pass via ``--interactive-url`` CLI.
        base_path: URL prefix for subpath hosting (e.g. ``/v2ecoli/dashboard``).
            When set, every root-absolute ``/assets/`` and ``/bigraph-loom/``
            URL in the rendered shells is prefixed with this value, and
            ``basePath`` is injected into ``__DASH_CONFIG__`` so that
            ``data-source.js`` resolves ``/api/*.json`` URLs correctly.
            Pass via ``--base-path`` CLI.  Default ``""`` keeps root-absolute
            (domain-root) behavior unchanged.
    """
    import vivarium_dashboard.server as srv

    ws_root = Path(ws_root)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    base_path = _normalize_base_path(base_path)

    # Temporarily point the server module at ws_root so all lookups
    # (_study_detail_spec, _iset_detail_data, workspace_paths …) use the right
    # workspace.  Restore afterwards, even on exceptions.
    orig_ws = srv.WORKSPACE
    srv.WORKSPACE = ws_root
    srv._WP_CACHE.clear()
    try:
        return _do_build(
            ws_root, out_dir, srv,
            interactive_url=interactive_url,
            base_path=base_path,
        )
    finally:
        srv.WORKSPACE = orig_ws
        srv._WP_CACHE.clear()


def _do_build(
    ws_root: Path,
    out_dir: Path,
    srv,
    *,
    interactive_url: str = "",
    base_path: str = "",
) -> dict:
    """Internal build routine — called with WORKSPACE already set to ws_root."""
    from vivarium_dashboard.server import (
        STATIC_DIR,
        _study_detail_spec,
        _workspace_home_data,
        _render_study_detail_html,
        _build_iset_summary_for_test,
        _inputs_payload,
        _catalog_data,
        _composites_data,
        _composite_resolve_data,
        _get_registry_data,
        _enumerate_data_sources,
        _investigations_data,
        _simulations_data,
        _visualization_classes_data,
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
    (api_dir / "inputs").mkdir(parents=True, exist_ok=True)

    # api/workspace.json
    _write_json(api_dir / "workspace.json", _workspace_home_data(ws_root))

    # api/iset-list.json — investigations list (GET /api/iset-list)
    _write_json(api_dir / "iset-list.json",
                {"investigations": _build_iset_summary_for_test(ws_root)})

    # api/inputs/_global.json — global/shared inputs (GET /api/inputs with no slug)
    try:
        global_inputs = _inputs_payload(ws_root, "")
    except Exception:
        global_inputs = {}
    _write_json(api_dir / "inputs" / "_global.json", global_inputs)

    # api/inputs/<inv>.json — per-investigation inputs (GET /api/inputs?investigation=<slug>)
    for inv_name in investigations:
        try:
            payload = _inputs_payload(ws_root, inv_name)
        except Exception:
            payload = {}
        _write_json(api_dir / "inputs" / f"{inv_name}.json", payload)

    # api/catalog.json — curated module catalog (GET /api/catalog)
    try:
        catalog = _catalog_data(ws_root)
    except Exception:
        catalog = {"modules": []}
    _write_json(api_dir / "catalog.json", catalog)

    # api/composites.json — composite specs (GET /api/composites)
    # Written AFTER the composite-state loop so each entry can carry has_wiring.
    try:
        composites = _composites_data(ws_root)
    except Exception:
        composites = {"composites": []}

    # api/composite-state/<id>.json — pre-resolved composite state for loom ?static=1
    composite_state_dir = api_dir / "composite-state"
    composite_state_dir.mkdir(parents=True, exist_ok=True)
    exported_wiring: set[str] = set()
    for comp in (composites.get("composites") or []):
        cid = comp.get("id")
        if not cid:
            continue
        try:
            data = _composite_resolve_data(cid)
            if data is not None:
                # The write itself can also fail (e.g. a resolved state that
                # carries non-finite floats like inf/nan, which strict JSON
                # rejects).  Treat that the same as an unresolvable composite:
                # degrade gracefully and let has_wiring=False hide Explore.
                _write_json(composite_state_dir / f"{cid}.json", data)
                exported_wiring.add(cid)
        except Exception:
            pass

    # Annotate each composite with has_wiring so the viewer can hide the
    # Explore button for composites whose state could not be exported.
    for comp in (composites.get("composites") or []):
        cid = comp.get("id")
        comp["has_wiring"] = bool(cid and cid in exported_wiring)
    _write_json(api_dir / "composites.json", composites)

    # api/simulations.json — pre-run simulations (GET /api/simulations)
    try:
        sims = _simulations_data(ws_root)
    except Exception:
        sims = {"simulations": [], "current": None}
    _write_json(api_dir / "simulations.json", sims)

    # api/visualization-classes.json — registered viz/analysis classes
    try:
        viz_classes = _visualization_classes_data(ws_root)
    except Exception:
        viz_classes = {"classes": []}
    _write_json(api_dir / "visualization-classes.json", viz_classes)

    # api/registry.json — discovered process/type registry (GET /api/registry)
    try:
        registry = _get_registry_data(bypass_cache=True)
    except Exception:
        registry = {"processes": [], "types": []}
    _write_json(api_dir / "registry.json", registry)

    # api/data-sources.json — repo-wide data-source bundle (GET /api/data-sources)
    try:
        data_sources = _enumerate_data_sources(bypass_cache=True)
    except Exception:
        data_sources = {"sources": []}
    _write_json(api_dir / "data-sources.json", data_sources)

    # api/investigations.json — flat studies list with DAG (GET /api/investigations)
    try:
        investigations_flat = _investigations_data(ws_root)
    except Exception:
        investigations_flat = {"investigations": []}
    _write_json(api_dir / "investigations.json", investigations_flat)

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

    # Copy bigraph-loom dist → bundle/bigraph-loom/ (read-only loom ?static=1 mode).
    # Skipped gracefully when bigraph_loom is not installed in this environment.
    try:
        import bigraph_loom as _bl
        loom_src = Path(_bl.asset_dir())
        loom_dst = out_dir / "bigraph-loom"
        if loom_dst.exists():
            shutil.rmtree(loom_dst)
        shutil.copytree(str(loom_src), str(loom_dst))
    except Exception:
        pass

    # ------------------------------------------------------------------
    # 4. Render home SPA shell → bundle/index.html
    # ------------------------------------------------------------------
    home_html = _render_home_html(ws_root)
    home_html = _normalize_asset_urls(home_html)
    home_html = _apply_base_path(home_html, base_path)
    home_html = _set_snapshot_config(
        home_html, interactive_url=interactive_url, base_path=base_path,
    )
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
        study_html = _apply_base_path(study_html, base_path)
        study_html = _set_snapshot_config(
            study_html, interactive_url=interactive_url, base_path=base_path,
        )
        shell_dir = out_dir / "studies" / slug
        shell_dir.mkdir(parents=True, exist_ok=True)
        (shell_dir / "index.html").write_text(study_html, encoding="utf-8")

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
    parser.add_argument(
        "--interactive-url", default="",
        dest="interactive_url",
        help="URL of the interactive vivarium-dashboard version (injected into the snapshot banner).",
    )
    parser.add_argument(
        "--base-path", default="",
        dest="base_path",
        help=(
            "URL prefix for subpath hosting (e.g. /v2ecoli/dashboard). "
            "When set, every /assets/ and /bigraph-loom/ URL in the rendered "
            "shells is prefixed with this value, and basePath is injected into "
            "__DASH_CONFIG__ so data-source.js resolves /api/*.json URLs "
            "correctly.  Default '' keeps root-absolute (domain-root) behavior."
        ),
    )
    args = parser.parse_args(argv)
    summary = build_bundle(
        Path(args.workspace), Path(args.out),
        interactive_url=args.interactive_url,
        base_path=args.base_path,
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
