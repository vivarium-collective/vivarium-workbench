"""vivarium_workbench.publish — narrative export / "publish" CLI.

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

from vivarium_workbench.lib.report import _normalize_asset_urls


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_json(path: Path, data) -> None:
    """Write *data* as JSON using the shared ``_json_default`` serializer.

    ``allow_nan=False`` keeps the bundle spec-compliant (the browser SPA parses
    it with ``JSON.parse``, which rejects the ``Infinity``/``NaN`` tokens
    ``allow_nan=True`` emits). This is STRICT on purpose: a non-finite float
    makes the write raise, which the composite-state loop catches per-composite
    to hide a broken composite from the loom Explorer (has_wiring=False) rather
    than ship a misleading null-patched state. Callers that legitimately carry
    non-finite values should sanitize via ``lib.json_serialize._json_sanitize`` first.
    """
    from vivarium_workbench.lib.json_serialize import _json_default
    path.write_text(
        json.dumps(data, default=_json_default, allow_nan=False),
        encoding="utf-8",
    )


def _snapshot_explorer(api_dir: Path, ws_root: Path,
                       snap_steps: int = 30, curated_per_class: int = 25) -> int:
    """Pre-render the Data Explorer endpoints to static JSON so the read-only
    (published) dashboard's Explorer card works without a live server.

    Bounded snapshot (the Explorer is otherwise an unbounded live-query tool):
      * per-step views (flux / pathways / allocation) are captured at <=``snap_steps``
        evenly-spaced row indices; the frontend snaps slider values to these.
      * timeseries is captured for every scalar (full time) + the top
        ``curated_per_class`` vector elements per class (the rest stay
        interactive-only).
      * scatter uses the last snapshot step's vectors.
    Only the first run is captured per-step (heavy); others get observables,
    validation, and last-step vectors (enough for scatter). Returns file count.
    """
    from vivarium_workbench.lib import explorer_data as E

    base = api_dir / "explorer"

    def _snap(s):
        return re.sub(r"[^A-Za-z0-9]+", "-", "" if s is None else str(s))

    def _w(rel, data):
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        _write_json(p, data)
        return 1

    try:
        runs = [r for r in E.list_runs(Path(ws_root)) if r.get("db_path")]
    except Exception:
        return 0
    if not runs:
        return 0

    idmap = E.load_flux_assets()[1]
    n = 0
    # richest run first (most emitted steps) so the published Explorer defaults to
    # it and it's the one that gets the full per-step snapshot.
    runs = sorted(runs, key=lambda r: -(int(r.get("n_steps") or 0)))
    plan = []
    for ri, r in enumerate(runs):
        nsteps = int(r.get("n_steps") or 0)
        idx = list(range(nsteps)) if nsteps > 0 else [0]
        if len(idx) > snap_steps:
            sel = sorted({round(i * (len(idx) - 1) / (snap_steps - 1)) for i in range(snap_steps)})
            idx = [idx[i] for i in sel]
        rr = dict(r); rr["snap_steps"] = idx
        plan.append((rr, idx, ri == 0))
    n += _w("runs.json", {"runs": [p[0] for p in plan]})

    for rr, steps, primary in plan:
        run, db = rr.get("run_id"), rr["db_path"]
        sr, nsteps = _snap(run), int(rr.get("n_steps") or 0)
        last = steps[-1] if steps else 0
        try:
            obs = E.list_observables(db, run, ws_root)
        except Exception:
            obs = {"categories": {}}
        n += _w(f"observables/{sr}.json", obs)
        for ds in ("schmidt", "wisniewski"):
            try:
                v = E.get_validation_scatter(db, ds, run, ws_root, n_steps=nsteps or None)
            except Exception:
                v = {"points": [], "n": 0, "pearson": None}
            n += _w(f"validation/{sr}/{_snap(ds)}.json", v)

        cats = obs.get("categories") or {}
        vecs = [o for leaves in cats.values() for o in leaves if o.get("kind") == "vector"]
        scalars = [o for leaves in cats.values() for o in leaves if o.get("kind") != "vector"]
        protein_path = next((o["path"] for o in vecs if o.get("mclass") == "Protein"), None)

        if primary:
            for s in steps:
                try: n += _w(f"flux/{sr}/{s}.json", E.get_flux_auto(db, s, idmap, run, ws_root))
                except Exception: pass
                try: n += _w(f"base-fluxes/{sr}/{s}.json", E.get_base_fluxes(db, s, run, ws_root))
                except Exception: pass
                if protein_path:
                    try: n += _w(f"protein-breakdown/{sr}/{_snap(protein_path)}/{s}.json",
                                 E.get_protein_breakdown(db, protein_path, s, run, ws_root))
                    except Exception: pass
            for o in vecs:  # step-0 ids for timeseries class expansion
                try: n += _w(f"vector/{sr}/{_snap(o['path'])}/0.json",
                             E.get_vector(db, o["path"], 0, run, ws_root))
                except Exception: pass
            for o in scalars:  # full-time series for every scalar
                try: n += _w(f"series/{sr}/{_snap(o['path'])}.json",
                             E.get_series(db, [(o["path"], None)], 400, run, ws_root))
                except Exception: pass
            for o in vecs:  # top curated vector elements per class
                vec = E.get_vector(db, o["path"], last, run, ws_root)
                vals = vec.get("values") or []
                for i in sorted(range(len(vals)), key=lambda k: -abs(vals[k]))[:curated_per_class]:
                    try: n += _w(f"series/{sr}/{_snap(o['path'] + '#' + str(i))}.json",
                                 E.get_series(db, [(o["path"], i)], 400, run, ws_root))
                    except Exception: pass

        # scatter vectors at the last snapshot step (both dotted + __ path forms,
        # since the scatter view hardcodes dotted while observables use __)
        for path in ("listeners.monomer_counts", "listeners.rna_counts.mRNA_counts",
                     "listeners.fba_results.base_reaction_fluxes",
                     "listeners__monomer_counts", "listeners__rna_counts__mRNA_counts",
                     "listeners__fba_results__base_reaction_fluxes"):
            try:
                vv = E.get_vector(db, path, last, run, ws_root)
                if vv.get("ids"):
                    n += _w(f"vector/{sr}/{_snap(path)}/{last}.json", vv)
            except Exception:
                pass
    return n


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


def _inputs_download_base(ws_root: Path) -> str:
    """Base URL for input-file (expert-doc / dataset) downloads in a *published*
    bundle.

    Published bundles do not stage input binaries — they can be large (a paper
    PDF is often several MB) and are already committed to the source repo. So
    the read-only dashboard links each input's workspace-relative ``path`` to the
    committed file in the GitHub source repo via the ``raw`` endpoint, rather
    than to a bundle-relative path that was never copied in (which 404s on
    GitHub Pages).

    Returns ``https://github.com/<owner>/<repo>/raw/<branch>[/<prefix>]`` where
    ``<prefix>`` is the workspace directory relative to the repo root (e.g.
    ``workspace``), so ``<base>/<workspace-relative path>`` is the file's
    canonical repo URL. Returns ``""`` when the workspace has no GitHub
    ``origin`` remote — the frontend then falls back to the live ``'/' + path``.
    """
    try:
        from vivarium_workbench.lib.report import _detect_github_repo
        repo = _detect_github_repo(ws_root)
    except Exception:
        repo = None
    if not repo:
        return ""

    def _git(*args) -> str:
        try:
            r = subprocess.run(
                ["git", "-C", str(ws_root), *args],
                capture_output=True, text=True, timeout=5,
            )
            return r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            return ""

    # Link to the branch the bundle is published FROM — that is the branch that
    # actually contains the committed input files (investigations are commonly
    # published from a per-investigation feature branch that is pushed to origin
    # but not yet merged to main, so a "main" link would 404). The published
    # snapshot is a point-in-time view of this branch.
    branch = _git("rev-parse", "--abbrev-ref", "HEAD") or "main"

    # Workspace directory relative to the repo root (e.g. "workspace").
    prefix = ""
    top = _git("rev-parse", "--show-toplevel")
    if top:
        try:
            rel = Path(ws_root).resolve().relative_to(Path(top).resolve())
            prefix = "" if str(rel) == "." else rel.as_posix()
        except Exception:
            prefix = ""

    base = f"https://github.com/{repo}/raw/{branch}"
    return base + ("/" + prefix if prefix else "")


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


def _stage_embed_visualizations(spec, ws_root: Path, out_dir: Path,
                                base_path: str) -> None:
    """Copy a study's ``embed_visualizations`` source files into the bundle and
    base-path-prefix their URLs (mutates *spec* in place).

    The study-detail panel renders each embed as an ``<iframe src=URL>`` the
    browser fetches at runtime (unlike the investigation REPORT, which inlines
    the HTML as ``srcdoc`` at generation time). The URLs are workspace-root-
    relative (e.g. ``/reports/figures/<study>/fig.html`` from
    ``_discover_viz_html_files``). In snapshot mode those files must (a) exist in
    the bundle and (b) carry the hosting base path — otherwise every embed 404s
    (the static build previously copied neither, so the study-detail "Embedded
    visualizations" panel was broken for every investigation that used them). We
    copy ``ws_root/<url>`` to ``out_dir/<url>`` (preserving the path) and rewrite
    the URL to ``<base_path><url>``.
    """
    embeds = spec.get("embed_visualizations")
    if not isinstance(embeds, list):
        return
    for embed in embeds:
        url = (embed or {}).get("url")
        # Only stage local, root-absolute workspace files (skip api/, externals).
        if not url or not url.startswith("/") or url.startswith(("/api/", "//")):
            continue
        rel = url.lstrip("/")
        src = ws_root / rel
        if not src.is_file():
            continue
        dst = out_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        if base_path:
            embed["url"] = base_path + url


def _stage_report_cards(spec, ws_root: Path, out_dir: Path,
                        base_path: str) -> None:
    """Copy a study's ``report_card_urls`` source HTML into the bundle and
    base-path-prefix their URLs (mutates *spec* in place).

    The study-detail panel renders each graded report card as an
    ``<iframe src=URL>`` the browser fetches at runtime. The URLs are
    workspace-root-absolute (e.g.
    ``/workspace/investigations/<inv>/studies/<name>/viz/report_card/standard.html``).
    In snapshot mode those files must (a) exist in the bundle and (b) carry the
    hosting base path — otherwise every comparison card 404s (the static build
    previously copied neither, so report-card studies showed no visualizations).
    Mirrors ``_stage_embed_visualizations``.
    """
    cards = spec.get("report_card_urls")
    if not isinstance(cards, dict):
        return
    for card in cards.values():
        url = (card or {}).get("url") if isinstance(card, dict) else None
        # Only stage local, root-absolute workspace files (skip api/, externals).
        if not url or not url.startswith("/") or url.startswith(("/api/", "//")):
            continue
        rel = url.lstrip("/")
        src = ws_root / rel
        if not src.is_file():
            continue
        dst = out_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        if base_path:
            card["url"] = base_path + url


def _artifact_dest(art, ws_root: Path):
    """``(src, out_name)`` for a study artifact with a local ``path``, else
    ``(None, None)``.

    Directories (e.g. a ``.zarr`` store) become ``<name>.zip``; files keep their
    suffix. The naming is the SINGLE source of truth shared by
    :func:`_stage_artifacts` (which writes the file) and
    :func:`_artifact_bundle_url` (which computes the link) so the runs-tab link
    and the staged file can never drift apart.
    """
    if not isinstance(art, dict):
        return None, None
    path = art.get("path")
    if not path or str(path).startswith(("/api/", "http://", "https://", "//")):
        return None, None
    src = (ws_root / str(path)).resolve()
    if not src.exists():
        return None, None
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(art.get("name") or src.name))
    return src, (safe + ".zip" if src.is_dir() else safe + src.suffix)


def _artifact_bundle_url(slug: str, art, ws_root: Path, base_path: str):
    """Deterministic bundle URL a staged artifact WILL live at (or ``None``).

    Order-independent: computable before the file is staged, so
    ``simulations.json`` (written before the per-study staging loop) can carry a
    working ``download_url`` for the runs tab.
    """
    _src, out_name = _artifact_dest(art, ws_root)
    if not out_name:
        return None
    rel = "/studies/%s/artifacts/%s" % (slug, out_name)
    return (base_path + rel) if base_path else rel


def _stage_artifacts(spec, ws_root: Path, out_dir: Path,
                     base_path: str, slug: str) -> None:
    """Copy a study's declared ``artifacts[]`` into the bundle as downloadable
    files and set a bundle-relative ``href`` (+ ``bytes``) on each (mutates
    *spec* in place).

    Files are copied verbatim; directories (e.g. a ``.zarr`` store) are zipped.
    Downloads land under ``studies/<slug>/artifacts/`` so both the read-only
    study page ("Download artifacts") AND the runs tab (via
    :func:`_attach_artifact_downloads`) can offer a working ``download`` with no
    live backend — the snapshot analogue of the live ``⬇ Results`` button.
    Artifacts with no local ``path`` are left untouched.
    """
    arts = spec.get("artifacts")
    if not isinstance(arts, list):
        return
    dest_root = out_dir / "studies" / slug / "artifacts"
    for art in arts:
        src, out_name = _artifact_dest(art, ws_root)
        if not src:
            continue
        dest_root.mkdir(parents=True, exist_ok=True)
        out_file = dest_root / out_name
        if src.is_dir():
            shutil.make_archive(str(out_file)[:-4], "zip", root_dir=str(src))
        else:
            shutil.copy2(src, out_file)
        art["href"] = _artifact_bundle_url(slug, art, ws_root, base_path)
        try:
            art["bytes"] = out_file.stat().st_size
        except OSError:
            pass


def _attach_artifact_downloads(sims, ws_root: Path, base_path: str) -> None:
    """Stamp a static ``download_url`` onto each run row in ``simulations.json``
    whose study declares a downloadable artifact (mutates *sims* in place).

    The runs-tab ``⬇ Results`` button is live-only (``/api/simulation-run-download``),
    so it is dead in a static bundle. Here we point it at the study's staged
    results artifact instead — deterministically, from ``study.yaml``'s
    ``artifacts[]`` — so the SAME zarr the "Download artifacts" panel serves is
    reachable from the Runs tab. The row's study is taken from ``study_slug`` or
    parsed out of its ``db_path``/``store_path`` (``studies/<slug>/…``).
    """
    rows = (sims or {}).get("simulations") or []
    cache: dict = {}

    def _study_results_artifact(slug: str):
        if slug in cache:
            return cache[slug]
        art = None
        try:
            from vivarium_workbench.lib import study_spec as _ss
            spec = _ss.load_study_detail_spec(ws_root, slug)
            arts = [a for a in (spec.get("artifacts") or [])
                    if isinstance(a, dict) and a.get("path")]
            art = next((a for a in arts if a.get("kind") == "zarr"), None) \
                or (arts[0] if arts else None)
        except Exception:
            art = None
        cache[slug] = art
        return art

    for row in rows:
        slug = row.get("study_slug")
        if not slug:
            loc = str(row.get("db_path") or row.get("store_path") or "").replace("\\", "/")
            m = re.search(r"studies/([^/]+)/", loc)
            if m:
                slug = m.group(1)
                row["study_slug"] = slug  # so the runs table associates the row
        if not slug:
            continue
        art = _study_results_artifact(slug)
        if art:
            url = _artifact_bundle_url(slug, art, ws_root, base_path)
            if url:
                row["download_url"] = url


def _stage_comparison_plotly(spec, ws_root: Path, out_dir: Path,
                             base_path: str) -> None:
    """Copy a study's ``comparison_plotly_url`` file into the bundle + base-path it.

    ``study_spec`` surfaces a single ``viz/comparison_plotly.html`` as the
    "Interactive comparison" panel ABOVE the scorecards, via the scalar
    ``comparison_plotly_url`` field (NOT ``embed_visualizations``). Like the
    embeds and report cards it's an ``<iframe src=URL>`` the browser fetches at
    runtime, so the file must exist in the bundle and carry the hosting base path
    — otherwise the panel 404s in the read-only dashboard. Mirrors
    ``_stage_embed_visualizations`` for that one field.
    """
    url = spec.get("comparison_plotly_url")
    if not isinstance(url, str) or not url.startswith("/") or url.startswith(("/api/", "//")):
        return
    rel = url.lstrip("/")
    src = ws_root / rel
    if not src.is_file():
        return
    dst = out_dir / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    if base_path:
        spec["comparison_plotly_url"] = base_path + url


def _stage_gif_visualizations(spec: dict, ws_root: Path, out_dir: Path, slug: str) -> None:
    """Stage a study's ``gif:`` visualization artifacts into the bundle.

    A study viz declared as ``address: gif:<file>`` references an animated GIF in
    the study's source dir. Copy it next to the study shell
    (``studies/<slug>/<file>``) so the shell renders it inline via a relative URL
    (no base-path needed — the shell sits in the same dir). Temporary local
    artifact hosting until sms-api serves these.
    """
    try:
        from vivarium_workbench.lib import study_spec as _ss
        sdir = _ss.study_dir(ws_root, slug)
    except Exception:
        return
    if not sdir or not Path(sdir).is_dir():
        return
    for v in (spec.get("visualizations") or []):
        addr = (v.get("address") or "") if isinstance(v, dict) else ""
        if not addr.startswith("gif:"):
            continue
        fname = addr[len("gif:"):].lstrip("/")
        if not fname or "/" in fname or ".." in fname:
            continue  # only a bare filename living in the study dir
        src = Path(sdir) / fname
        if src.is_file():
            dst = out_dir / "studies" / slug / fname
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


def _rewrite_pack_mesh_urls(obj, pack_dir_rel: str, base_no_slash: str) -> None:
    """Recursively rewrite mesh ``url`` strings in a parsimony pack (in place).

    The pack stores LOD mesh urls under ``ingredients[].shape.lods[].url`` as
    workspace-rooted-relative paths (e.g.
    ``studies/<name>/viz/3d/meshes/x.obj``). The viewer's ``resolveMeshUrl``
    prepends ``/`` to any non-absolute url, so for the bundle to resolve under a
    hosting base path the url must become ``<base>/studies/<name>/viz/3d/meshes/
    x.obj`` *without* a leading slash (``resolveMeshUrl`` adds it back). When
    *base_no_slash* is empty (root hosting) the url stays
    ``studies/<name>/viz/3d/meshes/x.obj`` → ``/studies/...`` which is correct
    for a root-served bundle.

    Args:
        pack_dir_rel: the pack's bundle-relative directory, e.g.
            ``studies/<name>/viz/3d`` (the meshes dir is ``<pack_dir_rel>/meshes``).
        base_no_slash: the hosting base path WITHOUT a leading slash
            (e.g. ``v2ecoli/dashboard``), or ``""`` for root hosting.
    """
    prefix = (base_no_slash + "/") if base_no_slash else ""
    mesh_base = prefix + pack_dir_rel + "/meshes/"

    def _walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "url" and isinstance(v, str) and v:
                    if "meshes/" in v:
                        tail = v.split("meshes/", 1)[1]
                    elif v.endswith(".obj"):
                        tail = v.rsplit("/", 1)[-1]
                    else:
                        continue
                    node[k] = mesh_base + tail
                else:
                    _walk(v)
        elif isinstance(node, list):
            for it in node:
                _walk(it)

    _walk(obj)


def _export_saved_visualizations(ws_root: Path, out_dir: Path,
                                 base_path: str) -> None:
    """Export the Analyses-tab saved 3D visualizations into the static bundle.

    Feature-detected on the optional ``pbg_parsimony`` package (mirrors the live
    ``/parsimony-viewer/*`` route + ``/api/saved-visualizations`` endpoint). When
    it's not installed this is a no-op, so the snapshot simply omits the gallery.

    Writes:
      - ``api/saved-visualizations.json`` — same payload as the live endpoint
        (``_build_saved_visualizations``). ``pack_url``/``meta_url`` stay
        workspace-rooted-absolute (``/studies/...``); the frontend prefixes the
        hosting base path at render time, identical to the live (empty-base) case.
      - ``parsimony-viewer/`` — the bundled viewer assets (index.html, viewer.js,
        obj-worker.js) copied from ``pbg_parsimony/viewer/``.
      - ``studies/<name>/viz/3d/`` — each saved pack + ``.meta.json`` sidecar +
        sibling ``meshes/`` dir, with the COPIED pack's mesh urls rewritten to be
        base-path-correct (see ``_rewrite_pack_mesh_urls``).
    """
    from vivarium_workbench.lib import saved_visualizations as _savedviz

    viewer_dir = _savedviz.parsimony_viewer_dir()
    if viewer_dir is None:
        return  # pbg_parsimony not installed → no parsimony assets in this bundle

    payload = _savedviz.build_saved_visualizations(ws_root)

    api_dir = out_dir / "api"
    api_dir.mkdir(parents=True, exist_ok=True)
    _write_json(api_dir / "saved-visualizations.json", payload)

    # Copy the viewer assets → bundle/parsimony-viewer/.
    viewer_dst = out_dir / "parsimony-viewer"
    if viewer_dst.exists():
        shutil.rmtree(viewer_dst)
    shutil.copytree(str(viewer_dir), str(viewer_dst))

    base_no_slash = (base_path or "").lstrip("/")

    # Copy each saved pack + sidecar + meshes, rewriting the copied pack's urls.
    for entry in payload.get("saved") or []:
        pack_url = entry.get("pack_url")
        if not pack_url:
            continue
        rel = pack_url.lstrip("/")                # studies/<name>/viz/3d/<pack>.json
        src_pack = ws_root / rel
        if not src_pack.is_file():
            continue
        pack_dir_rel = str(Path(rel).parent.as_posix())   # studies/<name>/viz/3d
        dst_pack = out_dir / rel
        dst_pack.parent.mkdir(parents=True, exist_ok=True)

        # Rewrite the copied pack's mesh urls (read → mutate → write).
        try:
            pack_data = json.loads(src_pack.read_text(encoding="utf-8"))
            _rewrite_pack_mesh_urls(pack_data, pack_dir_rel, base_no_slash)
            _write_json(dst_pack, pack_data)
        except Exception:
            # Fall back to a verbatim copy rather than dropping the pack entirely.
            shutil.copy2(src_pack, dst_pack)

        # Copy the .meta.json sidecar (no mesh urls → verbatim) next to the pack.
        src_meta = src_pack.with_name(src_pack.name.replace(".pack.json", ".meta.json"))
        if src_meta.is_file():
            shutil.copy2(src_meta, dst_pack.with_name(src_meta.name))

        # Copy the sibling meshes/ dir preserving the studies/<name>/viz/3d path.
        src_meshes = src_pack.parent / "meshes"
        if src_meshes.is_dir():
            dst_meshes = dst_pack.parent / "meshes"
            if dst_meshes.exists():
                shutil.rmtree(dst_meshes)
            shutil.copytree(str(src_meshes), str(dst_meshes))


def _export_analysis_viewers(ws_root: Path, out_dir: Path) -> None:
    """Snapshot the Analyses-page analysis viewers into the static bundle.

    Mirrors the live ``/api/analysis-viewers`` endpoint (repo-contributed
    ``workbench_viewers`` modules) so the read-only dashboard ADVERTISES which
    viewers exist instead of fetching a missing endpoint and throwing a JSON
    SyntaxError. The payload is the public, launch-callable-free viewer shape;
    viewers whose targets carry an external ``href`` (e.g. a publicly hosted 3D
    viewer) stay clickable in the snapshot, while launch-only viewers (PTools)
    render as "available in the live workbench". Best-effort: an unavailable
    worker yields an empty list, never a crash.
    """
    try:
        from vivarium_workbench.lib import analysis_viewers as _av
        viewers = _av.viewers_public(ws_root)
    except Exception:
        viewers = []
    api_dir = out_dir / "api"
    api_dir.mkdir(parents=True, exist_ok=True)
    _write_json(api_dir / "analysis-viewers.json", {"viewers": viewers})


def _export_analysis_tools(ws_root: Path, out_dir: Path) -> None:
    """Snapshot the tools-first Analysis Tools tab into the static bundle.

    Mirrors the live ``/api/analysis-tools`` endpoint (built-in tools +
    external viewers, each capability-matched to runs/studies) so the
    read-only dashboard advertises the same tools instead of fetching a
    missing endpoint. Best-effort: an unavailable worker yields an empty
    list, never a crash.
    """
    try:
        from vivarium_workbench.lib.analysis_tools import build_analysis_tools
        tools = build_analysis_tools(ws_root)
    except Exception:
        tools = []
    api_dir = out_dir / "api"
    api_dir.mkdir(parents=True, exist_ok=True)
    _write_json(api_dir / "analysis-tools.json", {"tools": tools})


def _set_snapshot_config(
    html: str,
    interactive_url: str = "",
    base_path: str = "",
    inputs_download_base: str = "",
    provenance: dict | None = None,
) -> str:
    """Swap the ``__DASH_CONFIG__`` mode from *local-server* to *snapshot*.

    Optionally injects:
    - ``interactiveUrl`` — so the snapshot banner can link to the interactive
      version (``--interactive-url`` CLI arg).
    - ``basePath`` — URL prefix for subpath hosting so ``data-source.js`` can
      resolve ``/api/*.json`` paths correctly when the bundle is served under a
      non-root path (``--base-path`` CLI arg).  Only injected when non-empty.
    - ``inputsDownloadBase`` — GitHub ``raw`` base URL for expert-doc / dataset
      downloads. Input binaries aren't staged in the bundle, so the frontend
      links them to the committed source-repo file instead of a bundle-relative
      path that 404s on GitHub Pages (see :func:`_inputs_download_base`). Only
      injected when non-empty.
    - ``provenance`` — the source repo/commit/branch/lockfile + build time, so
      the read-only Source panel can show a reproducibility card (GitHub link,
      commit sha, `sync` command) even with no live backend. Only injected when
      non-empty.
    """
    import json as _json
    config_js = 'window.__DASH_CONFIG__ = { mode: "snapshot"'
    if interactive_url:
        config_js += ', interactiveUrl: ' + _json.dumps(interactive_url)
    if base_path:
        config_js += ', basePath: ' + _json.dumps(base_path)
    if inputs_download_base:
        config_js += ', inputsDownloadBase: ' + _json.dumps(inputs_download_base)
    if provenance:
        prov = {k: v for k, v in provenance.items() if v}
        if prov:
            config_js += ', provenance: ' + _json.dumps(prov)
    config_js += ' };'
    return html.replace(
        'window.__DASH_CONFIG__ = { mode: "local-server" };',
        config_js,
    )


def _snapshot_provenance(ws_root: Path) -> dict:
    """Assemble the reproducibility facts surfaced in the published Source panel:
    repo slug + GitHub URLs, commit sha (+ commit page URL), branch, uv.lock
    hash, and the build timestamp. Tolerates non-git / partial workspaces —
    every field is best-effort and omitted when unknown (see
    :func:`_set_snapshot_config`, which drops empty values)."""
    from datetime import datetime, timezone

    commit, remote, branch = _git_info(ws_root)
    try:
        from vivarium_workbench.lib.report import _detect_github_repo
        slug = _detect_github_repo(ws_root) or ""
    except Exception:  # noqa: BLE001
        slug = ""
    repo_url = f"https://github.com/{slug}" if slug else (remote or "")
    commit = commit or ""
    commit_url = f"{repo_url}/commit/{commit}" if (repo_url and commit and slug) else ""
    try:
        from vivarium_workbench.lib.provenance_manifest import lockfile_hash
        lockfile = lockfile_hash(ws_root) or ""
    except Exception:  # noqa: BLE001
        lockfile = ""
    return {
        "repo_slug": slug,
        "repo_url": repo_url,
        "commit": commit,
        "commit_url": commit_url,
        "branch": branch or "",
        "lockfile": lockfile,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


def _render_home_html(ws_root: Path) -> str:
    """Render the home SPA shell from ``index.html.j2`` with a minimal context.

    All dynamic content (investigations list, registry, datasets …) is loaded
    by JS at runtime via ``DataSource.loadWorkspace()``; the template only
    needs scalar branding variables.
    """
    import yaml
    import jinja2
    from jinja2 import select_autoescape
    from vivarium_workbench.lib.static_serving import TEMPLATES_DIR

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
    # GitHub repository this workspace is associated with (from `git remote
    # origin`) — rendered as a link in the rail header (live + published).
    try:
        from vivarium_workbench.lib.report import _detect_github_repo
        _repo_slug = _detect_github_repo(ws_root)
    except Exception:
        _repo_slug = None

    # Current branch — shown in the rail repo+branch chip (published site).
    try:
        import subprocess
        _branch = subprocess.run(
            ["git", "-C", str(ws_root), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
    except Exception:
        _branch = ""

    tpl = env.get_template("index.html.j2")
    return tpl.render(
        workspace_name=ws.get("name", ws_root.name),
        workspace_branch=_branch,
        repo_url=(f"https://github.com/{_repo_slug}" if _repo_slug else ""),
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
    use ``lib.study_spec.load_study_detail_spec`` + ``lib.json_serialize._json_default``.

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
    from vivarium_workbench.lib._root import set_workspace_root

    ws_root = Path(ws_root)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    base_path = _normalize_base_path(base_path)

    # Point the global workspace root at ws_root so any lib fn that still reads
    # the global (rather than taking ws_root explicitly) resolves against the
    # right workspace.
    # Resolve symlinks: WorkspacePaths resolves its root internally, so viz
    # discovery's ``html_file.relative_to(ws_root)`` raises (and silently drops
    # that study's figures) if the root is left unresolved while the globbed
    # paths come back resolved — e.g. a ws_root under /tmp (-> /private/tmp on
    # macOS) or any symlinked parent.
    set_workspace_root(ws_root.resolve())
    return _do_build(
        ws_root, out_dir,
        interactive_url=interactive_url,
        base_path=base_path,
    )


def _do_build(
    ws_root: Path,
    out_dir: Path,
    *,
    interactive_url: str = "",
    base_path: str = "",
) -> dict:
    """Internal build routine — reads the workspace at ws_root via lib fns."""
    from vivarium_workbench.lib.static_serving import STATIC_DIR
    from vivarium_workbench.lib.study_spec import load_study_detail_spec as _study_detail_spec
    from vivarium_workbench.lib.study_charts import build_study_charts_payload
    from vivarium_workbench.lib.system_info import build_workspace_home
    from vivarium_workbench.lib.study_page import render_study_detail_html
    from vivarium_workbench.lib.investigation_status import (
        build_iset_summary, study_run_slugs,
    )
    from vivarium_workbench.lib.report_views import build_inputs, build_iset_detail
    from vivarium_workbench.lib.catalog import build_catalog
    from vivarium_workbench.lib.audit_views import build_audit as _build_audit
    from vivarium_workbench.lib.composite_lookup import composites_data
    from vivarium_workbench.lib.composite_resolve import resolve_composite
    from vivarium_workbench.lib.registry import build_registry
    from vivarium_workbench.lib.data_sources import enumerate_data_sources
    from vivarium_workbench.lib.investigations_index import build_investigations
    from vivarium_workbench.lib.simulations_index import build_simulations_data
    from vivarium_workbench.lib.visualization_classes import list_visualization_classes
    from vivarium_workbench.lib.workspace_paths import WorkspacePaths

    # runs-presence check for the investigation-summaries builder (mirrors the
    # retired server._build_iset_summary_for_test shim).
    _run_slugs = study_run_slugs(ws_root)

    def _study_has_runs(slug, spec):
        return slug in _run_slugs or bool((spec or {}).get("runs"))

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
    (api_dir / "investigation").mkdir(parents=True, exist_ok=True)
    (api_dir / "study").mkdir(parents=True, exist_ok=True)
    (api_dir / "inputs").mkdir(parents=True, exist_ok=True)

    # api/workspace.json
    _write_json(api_dir / "workspace.json", build_workspace_home(ws_root))

    # api/investigation-summaries.json — investigations list (GET /api/investigation-summaries)
    _write_json(api_dir / "investigation-summaries.json",
                {"investigations": build_iset_summary(ws_root, study_has_runs=_study_has_runs)})

    # api/inputs/_global.json — global/shared inputs (GET /api/inputs with no slug)
    try:
        global_inputs = build_inputs(ws_root, "")
    except Exception:
        global_inputs = {}
    _write_json(api_dir / "inputs" / "_global.json", global_inputs)

    # api/inputs/<inv>.json — per-investigation inputs (GET /api/inputs?investigation=<slug>)
    for inv_name in investigations:
        try:
            payload = build_inputs(ws_root, inv_name)
        except Exception:
            payload = {}
        _write_json(api_dir / "inputs" / f"{inv_name}.json", payload)

    # api/catalog.json — curated module catalog (GET /api/catalog)
    try:
        catalog = build_catalog(ws_root)
    except Exception:
        catalog = {"modules": []}
    # A static snapshot has no live venv, so the build-time install-sync probe
    # (which can even time out importing a heavy package) is meaningless and
    # misleading here — strip the out-of-sync flags from the published catalog.
    for _m in catalog.get("modules") or []:
        if isinstance(_m, dict):
            _m.pop("out_of_sync", None)
            _m.pop("out_of_sync_reason", None)
    _write_json(api_dir / "catalog.json", catalog)

    # api/marketplace.json — the FULL viva ecosystem (GET /api/marketplace,
    # build_catalog(full=True)): every module including the ones NOT installed
    # here, so the read-only Registry's Repositories facet can show what's
    # available to install (browse-only in a snapshot — Install is suppressed
    # with the other authoring controls). Best-effort; falls back to the
    # installed-only catalog so the facet still renders if the federation scan
    # is unavailable at publish time.
    try:
        marketplace = build_catalog(ws_root, full=True)
    except Exception:
        marketplace = catalog
    for _m in (marketplace.get("modules") or []):
        if isinstance(_m, dict):
            _m.pop("out_of_sync", None)
            _m.pop("out_of_sync_reason", None)
    _write_json(api_dir / "marketplace.json", marketplace)

    # api/ecosystem-index.json — the viva-marketplace aggregated artifact index
    # (per-repo processes/steps/composites/studies/investigations). Lets the
    # published Registry surface artifacts from repos not installed here. Sourced
    # from the installed viva_marketplace package; best-effort empty index if not.
    try:
        import viva_marketplace  # noqa: PLC0415
        eco_index = viva_marketplace.load_ecosystem_index()
    except Exception:  # noqa: BLE001
        eco_index = {"repos": []}
    _write_json(api_dir / "ecosystem-index.json", eco_index)

    # api/audit.json — read-only L0-L5 reproducibility audit (GET /api/audit).
    # Tolerant: build_audit never raises (returns a 200-shaped dict on error), so
    # the Audit tab works in the static bundle. Routed through the same
    # allow_nan=False writer; the audit emits only str/int/list so it's a no-op.
    try:
        audit_body, _ = _build_audit(ws_root)
    except Exception:
        audit_body = {"error": "audit unavailable", "studies": [], "investigations": []}
    _write_json(api_dir / "audit.json", audit_body)

    # api/explorer/* — pre-render the Data Explorer so its card works read-only
    try:
        n_exp = _snapshot_explorer(api_dir, ws_root)
        print(f"  explorer snapshot: {n_exp} files")
    except Exception as exc:
        print(f"  explorer snapshot skipped: {exc}")

    # api/composites.json — composite specs (GET /api/composites)
    # Written AFTER the composite-state loop so each entry can carry has_wiring.
    try:
        composites = composites_data(ws_root)
    except Exception:
        composites = {"composites": []}

    # api/composite-state/<id>.json — pre-resolved composite state for loom ?static=1
    composite_state_dir = api_dir / "composite-state"
    composite_state_dir.mkdir(parents=True, exist_ok=True)
    # Optional committed overrides: a workspace can PRE-RESOLVE a heavy composite
    # once (e.g. the full baseline, whose generator needs the on-disk ParCa cache
    # and so can't resolve at publish time) and commit the state JSON under
    # reports/composite-state/<id>.json. When present it's used verbatim and the
    # composite is marked navigable (has_wiring=True), even if live resolution
    # would fail. The filename must match the composite id.
    committed_state_dir = ws_root / "reports" / "composite-state"
    exported_wiring: set[str] = set()
    for comp in (composites.get("composites") or []):
        cid = comp.get("id")
        if not cid:
            continue
        committed = committed_state_dir / f"{cid}.json"
        if committed.is_file():
            try:
                (composite_state_dir / f"{cid}.json").write_bytes(committed.read_bytes())
                exported_wiring.add(cid)
                continue  # committed override wins; skip live resolution
            except Exception:
                pass
        try:
            data = resolve_composite(ws_root, cid)
            if data is not None:
                # The write itself can also fail (e.g. a resolved state that
                # carries non-finite floats like inf/nan, which strict JSON
                # rejects).  Treat that the same as an unresolvable composite:
                # degrade gracefully and let has_wiring=False hide Explore.
                _write_json(composite_state_dir / f"{cid}.json", data)
                exported_wiring.add(cid)
        except Exception:
            pass

    # Also publish any committed override whose filename is NOT a canonical
    # registry id — these are ALIAS forms a study.yaml references directly (e.g.
    # `...baseline.baseline.json` when discovery canonicalizes the id to
    # `...baseline`). The study-page loom pop-out builds its stateUrl from the
    # raw study ref, so the static file must exist under that exact name or it
    # 404s, even though the canonical state was already exported above.
    if committed_state_dir.is_dir():
        for override in sorted(committed_state_dir.glob("*.json")):
            alias = override.stem
            if alias in exported_wiring:
                continue
            try:
                (composite_state_dir / f"{alias}.json").write_bytes(override.read_bytes())
                exported_wiring.add(alias)
            except Exception:
                pass

    # api/composite-inner-state/<key>.json — pre-built inner-composite states so
    # the loom's drill-in mini-map works in ?static=1 (read-only) mode, where the
    # live /api/composite-inner-state endpoint is unavailable. Two sources, in
    # order: (1) committed reports/composite-inner-state/*.json (heavy composites
    # whose cells can't be instantiated at publish time — same rationale as the
    # committed composite-state overrides above); (2) a best-effort live build for
    # the light composites that DO resolve at publish time. Keys are computed by
    # composite_inner_states.inner_state_key and matched client-side in loom.
    inner_dir = api_dir / "composite-inner-state"
    committed_inner_dir = ws_root / "reports" / "composite-inner-state"
    _inner_made = False
    if committed_inner_dir.is_dir():
        inner_dir.mkdir(parents=True, exist_ok=True)
        _inner_made = True
        for f in sorted(committed_inner_dir.glob("*.json")):
            try:
                (inner_dir / f.name).write_bytes(f.read_bytes())
            except Exception:
                pass
    try:
        from vivarium_workbench.lib.composite_inner_states import build_inner_states_for
        for comp in (composites.get("composites") or []):
            cid = comp.get("id")
            if not cid:
                continue
            sf = composite_state_dir / f"{cid}.json"
            if not sf.is_file():
                continue
            try:
                payload = json.loads(sf.read_text(encoding="utf-8"))
            except Exception:
                continue
            st = payload.get("state") if isinstance(payload, dict) else None
            if isinstance(st, dict) and isinstance(st.get("state"), dict):
                st = st["state"]
            if not isinstance(st, dict):
                continue
            try:
                built = build_inner_states_for(ws_root, cid, st)
            except Exception:
                built = {}
            if built and not _inner_made:
                inner_dir.mkdir(parents=True, exist_ok=True)
                _inner_made = True
            for key, body in built.items():
                p = inner_dir / f"{key}.json"
                if p.exists():
                    continue  # committed override wins over a live rebuild
                try:
                    _write_json(p, body)
                except Exception:
                    pass
    except Exception:
        pass  # inner-state pre-build is optional; never break the bundle

    # Annotate each composite with has_wiring so the viewer can hide the
    # Explore button for composites whose state could not be exported.
    for comp in (composites.get("composites") or []):
        cid = comp.get("id")
        comp["has_wiring"] = bool(cid and cid in exported_wiring)
    _write_json(api_dir / "composites.json", composites)

    # api/simulations.json — pre-run simulations (GET /api/simulations)
    try:
        sims = build_simulations_data(ws_root)
    except Exception:
        sims = {"simulations": [], "current": None}
    # Point each run row's ⬇ Results at the study's staged results artifact so
    # the runs tab download works in the (backend-less) read-only bundle.
    try:
        _attach_artifact_downloads(sims, ws_root, base_path)
    except Exception:
        pass
    _write_json(api_dir / "simulations.json", sims)

    # api/visualization-classes.json — registered viz/analysis classes
    try:
        viz_classes = list_visualization_classes(ws_root)
    except Exception:
        viz_classes = {"classes": []}
    _write_json(api_dir / "visualization-classes.json", viz_classes)

    # api/registry.json — discovered process/type registry (GET /api/registry)
    try:
        registry = build_registry(ws_root, bypass_cache=True)
    except Exception:
        registry = {"processes": [], "types": []}
    _write_json(api_dir / "registry.json", registry)

    # api/data-sources.json — repo-wide data-source bundle (GET /api/data-sources)
    try:
        data_sources = enumerate_data_sources(ws_root, True)
    except Exception:
        data_sources = {"sources": []}
    _write_json(api_dir / "data-sources.json", data_sources)

    # api/references-bib.json — parsed papers.bib (GET /api/references-bib).
    # Without this the read-only References cards fetch /api/references-bib and
    # 404 in snapshot mode, so the published dashboard shows no papers at all.
    try:
        from vivarium_workbench.lib.report import _parse_bib_entries
        references_entries = _parse_bib_entries(ws_root)
        try:
            from vivarium_workbench.lib.references_fetch import (
                load_cache, enrich_entries,
            )
            references_entries = enrich_entries(
                references_entries, load_cache(ws_root))
        except Exception:
            pass  # enrichment cache is optional; raw bib entries still render
        references = {"entries": references_entries}
    except Exception:
        references = {"entries": []}
    _write_json(api_dir / "references-bib.json", references)

    # api/investigations.json — flat studies list with DAG (GET /api/investigations)
    try:
        investigations_flat = build_investigations(ws_root)
    except Exception:
        investigations_flat = {"investigations": []}
    _write_json(api_dir / "investigations.json", investigations_flat)

    # api/investigation/<id>.json  (+ per-investigation runnable notebook export)
    # Each investigation also ships a self-contained Jupyter notebook + .py under
    # bundle/investigation-notebooks/ — the coder-facing complement to the HTML
    # report. Deterministic; guarded per investigation so one failure never
    # aborts the publish (same pattern as the study/charts loops).
    from vivarium_workbench.lib.notebook_export import export_investigation_notebook
    nb_out_dir = out_dir / "investigation-notebooks"
    notebook_manifest: list[dict] = []
    # api/investigation-graph/<slug>.json — study nodes + typed evidence chains,
    # byte-parity with GET /api/investigation-graph?investigation=<slug>. Without
    # this, the read-only DAG cards can't render the evidence chain at near zoom.
    from vivarium_workbench.lib.investigation_graph_views import build_investigation_graph
    (api_dir / "investigation-graph").mkdir(parents=True, exist_ok=True)
    for inv_name in investigations:
        data = build_iset_detail(ws_root, inv_name)
        if data is None:
            continue
        # iset JSON stays byte-parity with the live builder; notebook urls live
        # in the separate manifest (the SPA derives the snapshot url from slug).
        _write_json(api_dir / "investigation" / f"{inv_name}.json", data)
        try:
            _graph, _ = build_investigation_graph(ws_root, inv_name)
            _write_json(api_dir / "investigation-graph" / f"{inv_name}.json", _graph)
        except Exception as exc:  # noqa: BLE001 — never abort a publish on one graph
            print(f"  warn: investigation-graph export failed for {inv_name!r}: {exc}")
        try:
            paths = export_investigation_notebook(ws_root, inv_name, out_dir=nb_out_dir)
            notebook_manifest.append({
                "slug": inv_name,
                "ipynb": f"investigation-notebooks/{paths['ipynb'].name}",
                "py": f"investigation-notebooks/{paths['py'].name}",
            })
        except Exception as exc:  # noqa: BLE001 — never abort a publish on one notebook
            print(f"  warn: notebook export failed for {inv_name!r}: {exc}")
    _write_json(api_dir / "investigation-notebooks.json", {"notebooks": notebook_manifest})

    # api/study/<slug>.json
    # Guard per study: a single malformed study.yaml (e.g. a stub study that
    # exists only to host saved viz assets and declares neither 'variants' nor
    # 'composite') must not abort the whole publish — skip it and continue, the
    # same way the charts/composites loops degrade gracefully below.
    for slug in studies:
        try:
            data = _study_detail_spec(ws_root, slug)
        except Exception as exc:  # noqa: BLE001 — never abort a publish on one study
            print(f"  warn: study-detail export failed for {slug!r}: {exc}")
            continue
        if data is not None:
            _stage_embed_visualizations(data, ws_root, out_dir, base_path)
            _stage_report_cards(data, ws_root, out_dir, base_path)
            _stage_comparison_plotly(data, ws_root, out_dir, base_path)
            _stage_gif_visualizations(data, ws_root, out_dir, slug)
            _stage_artifacts(data, ws_root, out_dir, base_path, slug)
            _write_json(api_dir / "study" / f"{slug}.json", data)

    # api/study-charts/<slug>.json — the Visualizations-tab charts payload,
    # byte-parity with GET /api/study-charts/<slug>. Without this the snapshot
    # SPA has no charts to render and the panel falls back to a placeholder.
    # Live charts depend on a runs.db that may be absent in CI; the static
    # charts (base64-embedded PNG/SVG under studies/<slug>/charts/) are the
    # snapshot-relevant ones and are always available. One study's chart-render
    # failure must not abort the whole publish, so guard per study.
    charts_api_dir = api_dir / "study-charts"
    charts_api_dir.mkdir(parents=True, exist_ok=True)
    for slug in studies:
        try:
            # Feedback-friction: the published per-investigation report shows
            # only current figures — hide charts from superseded runs (opt-in).
            payload = build_study_charts_payload(ws_root, slug, hide_superseded=True)
        except Exception as exc:  # noqa: BLE001 — never abort a publish on one study
            print(f"  warn: study-charts export failed for {slug!r}: {exc}")
            continue
        _write_json(charts_api_dir / f"{slug}.json", payload)

    # api/saved-visualizations.json + parsimony-viewer/ + copied packs/meshes —
    # the Analyses-tab gallery. Feature-detected on pbg_parsimony; no-op when the
    # viewer package isn't installed (mirrors the live /parsimony-viewer route).
    try:
        _export_saved_visualizations(ws_root, out_dir, base_path)
    except Exception as exc:  # noqa: BLE001 — never abort a publish on the gallery
        print(f"  warn: saved-visualizations export failed: {exc}")

    # api/analysis-viewers.json — the Analyses-page viewer tools (PTools, 3d-ecoli,
    # …). Snapshotting this is what stops the read-only dashboard from throwing
    # "Error loading analysis viewers: SyntaxError" (a missing endpoint returned
    # SPA HTML that JSON.parse rejected).
    try:
        _export_analysis_viewers(ws_root, out_dir)
    except Exception as exc:  # noqa: BLE001 — never abort a publish on the viewers
        print(f"  warn: analysis-viewers export failed: {exc}")

    # api/analysis-tools.json — the tools-first Analysis Tools tab (built-in
    # tools + external viewers, capability-matched to runs/studies).
    try:
        _export_analysis_tools(ws_root, out_dir)
    except Exception as exc:  # noqa: BLE001 — never abort a publish on the tools
        print(f"  warn: analysis-tools export failed: {exc}")

    # ------------------------------------------------------------------
    # 3. Copy bundled static assets → bundle/assets/
    # ------------------------------------------------------------------
    assets_dir = out_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    for src in STATIC_DIR.iterdir():
        if src.is_file():
            shutil.copy2(src, assets_dir / src.name)

    # Copy bigraph-loom dist → bundle/bigraph-loom/ (read-only loom ?static=1 mode).
    # Skipped gracefully when the vendored bundle hasn't been built in this environment.
    try:
        from vivarium_workbench.loom_assets import asset_dir as _loom_asset_dir
        loom_src = Path(_loom_asset_dir())
        loom_dst = out_dir / "bigraph-loom"
        # Check the source *before* clearing the destination: re-publishing
        # into an existing bundle when _dist is missing would otherwise delete
        # a previously-published (working) loom dir and then fall into the
        # warning branch, silently stripping the Explorer from that bundle.
        if not loom_src.is_dir():
            raise FileNotFoundError(loom_src)
        if loom_dst.exists():
            shutil.rmtree(loom_dst)
        shutil.copytree(str(loom_src), str(loom_dst))
    except Exception as exc:
        print(f"  warn: loom _dist not found — did you run scripts/build_loom.sh? ({exc})")

    # ------------------------------------------------------------------
    # 4. Render home SPA shell → bundle/index.html
    # ------------------------------------------------------------------
    # Input downloads (expert docs / datasets) aren't staged in the bundle;
    # link them to the committed file in the GitHub source repo.
    inputs_download_base = _inputs_download_base(ws_root)
    provenance = _snapshot_provenance(ws_root)
    home_html = _render_home_html(ws_root)
    home_html = _normalize_asset_urls(home_html)
    home_html = _apply_base_path(home_html, base_path)
    home_html = _set_snapshot_config(
        home_html, interactive_url=interactive_url, base_path=base_path,
        inputs_download_base=inputs_download_base, provenance=provenance,
    )
    (out_dir / "index.html").write_text(home_html, encoding="utf-8")

    # ------------------------------------------------------------------
    # 5. Render per-study shells → bundle/studies/<slug>/index.html
    # ------------------------------------------------------------------
    for slug in studies:
        try:
            spec = _study_detail_spec(ws_root, slug)
        except Exception as exc:  # noqa: BLE001 — one bad study must not abort
            print(f"  warn: study-shell export failed for {slug!r}: {exc}")
            continue
        if spec is None:
            continue
        # The shell template renders embed_visualizations as <iframe src="{{v.url}}">
        # server-side; this spec is re-fetched (not the one staged for the JSON
        # above), so stage it too or its URLs stay root-absolute (/reports/...)
        # and 404 under a hosting base path. (_apply_base_path only rewrites
        # /assets/ + /bigraph-loom/, not embed URLs.)
        try:
            _stage_embed_visualizations(spec, ws_root, out_dir, base_path)
            _stage_report_cards(spec, ws_root, out_dir, base_path)
            _stage_comparison_plotly(spec, ws_root, out_dir, base_path)
            _stage_artifacts(spec, ws_root, out_dir, base_path, slug)
            study_html = render_study_detail_html(ws_root, slug, spec)
            study_html = _normalize_asset_urls(study_html)
            study_html = _apply_base_path(study_html, base_path)
            study_html = _set_snapshot_config(
                study_html, interactive_url=interactive_url, base_path=base_path,
                inputs_download_base=inputs_download_base, provenance=provenance,
            )
        except Exception as exc:  # noqa: BLE001 — one bad study must not abort the whole publish
            print(f"  warn: study-shell render failed for {slug!r}: {exc}")
            continue
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
        prog="vivarium-workbench-publish",
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
