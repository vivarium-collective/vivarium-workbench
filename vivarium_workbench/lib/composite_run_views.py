"""HTTP-free builders for the composite-run read routes.

These are the library seam behind five dashboard GET routes:

  * ``GET /api/composite-runs?spec_id=X``            → :func:`build_composite_runs`
  * ``GET /api/composite-run/{run_id}``              → :func:`build_composite_run`
  * ``GET /api/composite-run/{run_id}/state?step=N`` → :func:`build_composite_run_state`
  * ``GET /api/composite-run/{run_id}/status``       → :func:`build_composite_run_status`
  * ``GET /api/composite-run/{run_id}/download``     → :func:`build_composite_run_zip`

All read from the workspace's ``.pbg/composite-runs.db`` via ``lib.composite_runs``.
Pure ``ws_root``-parameterised functions: NO ``import server`` (docstring reference only).

The FastAPI app (``api/app.py``) imports these builders directly; ``server.py``'s
``_get_composite_run*`` shims delegate here rather than duplicating the logic.
"""
from __future__ import annotations

import base64
import io
import json
import zipfile
from pathlib import Path

from vivarium_workbench.lib import composite_runs as cr
from vivarium_workbench.lib.workspace_paths import WorkspacePaths

_TERMINAL_STATUSES = {"completed", "failed", "orphaned"}


def _db_file(ws_root: Path) -> Path:
    """Resolve the composite-runs database path for a workspace."""
    return WorkspacePaths.load(ws_root).pbg / "composite-runs.db"


def build_composite_runs(
    ws_root: Path, spec_id: str | None
) -> tuple[dict, int]:
    """Worker for ``GET /api/composite-runs?spec_id=X``.

    Returns ``(payload_dict, http_status)``:

    * HTTP 400 ``{"runs": [], "error": "missing spec_id"}`` — when ``spec_id``
      is absent or empty (mirrors ``_get_composite_runs`` line 8092).
    * HTTP 200 ``{"runs": []}`` — when the ``.pbg/composite-runs.db`` file is
      absent (first-launch, no runs yet).
    * HTTP 200 ``{"runs": [...]}`` — run list for the given spec, newest first.
    """
    if not spec_id:
        return {"runs": [], "error": "missing spec_id"}, 400
    db = _db_file(ws_root)
    if not db.is_file():
        return {"runs": []}, 200
    conn = cr.connect(db)
    try:
        runs = cr.query_runs(conn, spec_id=spec_id)
    finally:
        conn.close()
    return {"runs": runs}, 200


def build_composite_run(ws_root: Path, run_id: str) -> tuple[dict, int]:
    """Worker for ``GET /api/composite-run/{run_id}``.

    Returns ``(payload_dict, http_status)``:

    * HTTP 404 ``{"error": "no run database"}`` — db absent.
    * HTTP 404 ``{"error": "run not found"}`` — trajectory is empty (no history
      rows for ``run_id``).
    * HTTP 200 ``{"run_id": ..., "trajectory": [...]}`` — success.
    """
    # Plan B (image-backed Cloud run): a "remote-sim-<id>" run has no local
    # trajectory — its data lives on the cloud and surfaces in the Simulations/
    # Runs tab. Return an empty trajectory so the loom degrades gracefully (no
    # inline graph) instead of throwing on a 404.
    if run_id.startswith("remote-sim-"):
        return {"run_id": run_id, "trajectory": [], "remote": True}, 200
    db = _db_file(ws_root)
    if not db.is_file():
        return {"error": "no run database"}, 404
    conn = cr.connect(db)
    try:
        trajectory = cr.query_run(conn, run_id=run_id)
    finally:
        conn.close()
    if not trajectory:
        return {"error": "run not found"}, 404
    return {"run_id": run_id, "trajectory": trajectory}, 200


def build_composite_run_state(
    ws_root: Path, run_id: str, step: int
) -> tuple[dict, int]:
    """Worker for ``GET /api/composite-run/{run_id}/state?step=N``.

    The **caller** is responsible for parsing ``step`` from the query string
    and returning HTTP 400 on ``ValueError`` — this function only handles the
    database lookup (``step`` is already an ``int`` here).

    Returns ``(payload_dict, http_status)``:

    * HTTP 404 ``{"error": "no run database"}`` — db absent.
    * HTTP 404 ``{"error": "state not found for run+step"}`` — step not in history.
    * HTTP 200 ``{"run_id": ..., "step": ..., "state": {...}}`` — success.
    """
    db = _db_file(ws_root)
    if not db.is_file():
        return {"error": "no run database"}, 404
    conn = cr.connect(db)
    try:
        state = cr.query_run_state(conn, run_id=run_id, step=step)
    finally:
        conn.close()
    if state is None:
        return {"error": "state not found for run+step"}, 404
    return {"run_id": run_id, "step": step, "state": state}, 200


def build_composite_run_status(ws_root: Path, run_id: str) -> tuple[dict, int]:
    """Worker for ``GET /api/composite-run/{run_id}/status``.

    Returns ``(payload_dict, http_status)``:

    * HTTP 404 ``{"error": "no run database"}`` — db absent.
    * HTTP 404 ``{"error": "run not found"}`` — ``run_id`` not in ``runs_meta``.
    * HTTP 200 ``{run_id, status, progress_step, n_steps, heartbeat_at,
      [log_path, error] | [viz_html]}`` — success.

    Terminal-state enrichment (mirrors ``_get_composite_run_status`` lines
    8198–8212):

    * ``failed`` / ``orphaned``: if ``log_path`` is in meta, adds ``log_path``
      to the response and (when the file exists) reads the last 2 000 chars as
      the ``error`` excerpt.
    * ``completed``: reads ``viz_html`` from
      ``.pbg/runs/<run_id>/viz.json`` when the file exists; skipped on
      ``JSONDecodeError``.
    """
    ws_root = Path(ws_root)
    # Plan B (image-backed Cloud run): a composite-card Cloud run dispatched via
    # run_simulation has no local run row — its state lives on sms-api. The loom
    # polls this endpoint with the synthetic "remote-sim-<id>" run_id; map it to
    # the sms-api simulation status so the run bar shows running -> completed. The
    # run itself surfaces in the Simulations/Runs tab via remote_simulations.
    if run_id.startswith("remote-sim-"):
        try:
            sim_id = int(run_id[len("remote-sim-"):])
        except ValueError:
            return {"error": "run not found"}, 404
        from vivarium_workbench.lib import remote_run_views as _rrv
        st, code = _rrv.remote_run_status({"simulation_id": sim_id})
        if code != 200:
            return st, code
        phase = str(st.get("phase") or "")
        mapped = {"done": "completed", "failed": "failed"}.get(phase, "running")
        return {
            "run_id": run_id,
            "status": mapped,
            "progress_step": None,
            "n_steps": None,
            "heartbeat_at": None,
            "remote": True,
            "simulation_id": sim_id,
            "raw_status": st.get("raw_status"),
        }, 200
    db = _db_file(ws_root)
    if not db.is_file():
        return {"error": "no run database"}, 404
    conn = cr.connect(db)
    try:
        meta = cr.query_run_meta(conn, run_id=run_id)
    finally:
        conn.close()
    if meta is None:
        return {"error": "run not found"}, 404

    resp: dict = {
        "run_id": run_id,
        "status": meta["status"],
        "progress_step": meta.get("progress_step") or 0,
        "n_steps": meta.get("n_steps"),
        "heartbeat_at": meta.get("heartbeat_at"),
        # Sub-status while status=='running' (simulate / rendering visualizations
        # / analysis flush) so the UI can announce the current stage.
        "phase": meta.get("phase") if meta["status"] == "running" else None,
    }
    resp["downloadable"] = False
    if meta["status"] in ("failed", "orphaned"):
        log_rel = meta.get("log_path")
        if log_rel:
            resp["log_path"] = log_rel
            log_full = ws_root / log_rel
            if log_full.is_file():
                resp["error"] = log_full.read_text(encoding="utf-8")[-2000:]
    elif meta["status"] == "completed":
        wp = WorkspacePaths.load(ws_root)
        run_dir = wp.pbg / "runs" / run_id
        viz_file = run_dir / "viz.json"
        if viz_file.is_file():
            try:
                resp["viz_html"] = json.loads(viz_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        analyses_file = run_dir / "analyses.json"
        has_analyses = False
        if analyses_file.is_file():
            content = analyses_file.read_text(encoding="utf-8").strip()
            has_analyses = content not in ("", "[]")
        resp["has_analyses"] = has_analyses
        resp["has_report"] = (run_dir / "report.html").is_file()
        resp["downloadable"] = True
    else:
        resp["downloadable"] = False
    return resp, 200


def _run_is_terminal(ws_root, run_id: str) -> bool:
    """Return True iff the run's status is terminal (completed/failed/orphaned).

    Reuses the same ``connect`` + ``query_run_meta`` path as
    :func:`build_composite_run_status` — no parallel DB read.
    Returns False when the DB is absent or ``run_id`` is unknown.
    """
    db = _db_file(Path(ws_root))
    if not db.is_file():
        return False
    conn = cr.connect(db)
    try:
        meta = cr.query_run_meta(conn, run_id=run_id)
    finally:
        conn.close()
    if meta is None:
        return False
    return meta["status"] in _TERMINAL_STATUSES


def build_composite_run_zip(ws_root, run_id: str) -> tuple[bytes, str, int]:
    """Zip the run directory for ``GET /api/composite-run/{run_id}/download``.

    Mirrors ``lib.analysis_outputs.build_analysis_outputs_zip`` using
    ``io.BytesIO`` + ``zipfile.ZIP_DEFLATED``.

    Returns ``(zip_bytes, filename, http_status)``:

    * HTTP 404 — run directory is absent (run never wrote results).
    * HTTP 409 — run exists but is not in a terminal state; download not yet safe.
    * HTTP 200 — success; ``zip_bytes`` is a valid ZIP archive of every file in
      the run directory except ``request.json`` (implementation detail).
    """
    run_dir = WorkspacePaths.load(ws_root).pbg / "runs" / run_id
    fname = f"run_{run_id}.zip"
    if not run_dir.is_dir():
        return b"", fname, 404
    if not _run_is_terminal(ws_root, run_id):
        return b"", fname, 409
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for src in sorted(run_dir.rglob("*")):
            if src.is_file() and src.name != "request.json":
                zf.write(src, str(src.relative_to(run_dir)))
    return buf.getvalue(), fname, 200


# Per-run retrievable artifacts (Runs-tab "Visualizations / Analyses / Report
# cards / Results" buttons). Each run dir under .pbg/runs/<run_id>/ writes these
# on completion; this serves them by name — including for the ad-hoc
# composite-test-runs (ecoli_colony, ecoli_baseline), which have no study.
#
#   name    → source file            served as
#   ------    -------------------      ----------------------------------------
#   viz     → viz.json               a rendered HTML page (GIF + plots inline)
#   report  → report.html            HTML (view)
#   analyses→ analyses.json          JSON (download)
#   verdict → verdict.json           JSON (view/download)
_RUN_ARTIFACTS = {
    "viz":      ("viz.json",      "text/html"),
    "report":   ("report.html",   "text/html"),
    "analyses": ("analyses.json", "application/json"),
    "verdict":  ("verdict.json",  "application/json"),
}


def _zarr_fallback_viz(ws_root, run_id: str, name: str) -> "str | None":
    """Best-effort zarr-native render for a run with no local viz.json/report.html.

    Returns the rendered HTML, or ``None`` when the run isn't found, its store
    doesn't resolve at all, or it resolves to something other than zarr
    (parquet/sqlite runs already have viz.json written by a local execution —
    this fallback only exists for the never-executed-locally, zarr-only case).
    Never raises.
    """
    from vivarium_workbench.lib import emitters
    from vivarium_workbench.lib import simulations_index
    from vivarium_workbench.lib import zarr_default_viz

    ws_root = Path(ws_root)
    row = next(
        (r for r in simulations_index.list_simulations(ws_root) if r.get("run_id") == run_id),
        None,
    )
    if row is None:
        return None

    store, tmp = simulations_index.resolve_or_fetch_store(ws_root, row)
    if store is None:
        return None
    try:
        kind, resolved = emitters.read_source(str(store))
        if kind != "zarr" or resolved is None:
            return None
        import html as _html
        body = zarr_default_viz.render_default_viz(resolved, run_id)
        if name == "report":
            title, heading = "Run report", "Report"
            note = (
                f'<p style="color:#6b7280;font-size:13px"><code>{_html.escape(run_id)}</code> — '
                f'no formal report card was generated (this run was never executed locally); '
                f'showing the default observables rendered directly from its native store.</p>'
            )
        else:
            title, heading = "Run visualizations", "Visualizations"
            note = f'<p style="color:#6b7280;font-size:13px"><code>{_html.escape(run_id)}</code></p>'
        return (
            f'<!doctype html><meta charset="utf-8"><title>{title} — {_html.escape(run_id)}</title>'
            f'<body style="max-width:1100px;margin:24px auto;padding:0 16px;font-family:system-ui">'
            f'<h2 style="color:#111827">{heading}</h2>{note}{body}</body>'
        )
    finally:
        if tmp is not None:
            tmp.cleanup()


def build_run_artifact(ws_root, run_id: str, name: str) -> tuple[bytes, str, "str | None", int]:
    """Serve one named run artifact. Returns ``(content, media_type, download_name, status)``.

    ``viz`` is rendered into a standalone HTML page embedding each declared
    visualization's html (so the colony GIF / plots open directly in a tab);
    ``report`` is the run's report.html; ``analyses``/``verdict`` are the raw
    JSON (downloadable). 400 on an unknown name, 404 when the file is absent.

    ``viz``/``report`` fall back to a zarr-native default render
    (:mod:`lib.zarr_default_viz`) when the local file is absent AND the run's
    native store resolves (locally, or fetched from sms-api via
    :func:`lib.simulations_index.resolve_or_fetch_store`) to a zarr/XArray
    store — the shape every GovCloud/Ray-dispatched run actually uses, for
    which no local composite execution ever wrote a viz.json/report.html.
    """
    spec = _RUN_ARTIFACTS.get(name)
    if spec is None:
        return b"", "application/json", None, 400
    filename, media_type = spec
    run_dir = WorkspacePaths.load(ws_root).pbg / "runs" / run_id
    path = run_dir / filename
    if not path.is_file():
        if name in ("viz", "report"):
            rendered = _zarr_fallback_viz(ws_root, run_id, name)
            if rendered is not None:
                return rendered.encode("utf-8"), "text/html", None, 200
        return b"", media_type, None, 404
    raw = path.read_bytes()
    if name == "viz":
        try:
            viz = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            viz = {}
        items = list(viz.items()) if isinstance(viz, dict) else []

        # When every visualization is a standalone image (an SVG document or a
        # PNG data-URI — the paper-figure / bigraph-loom case), serve the file(s)
        # as a FILE DOWNLOAD rather than an inline HTML viewer page: a single file
        # directly, or a .zip when the run has several (e.g. an SVG + PNG of each
        # panel). Detected by content, so plot/GIF/HTML viz (the normal
        # simulation case) still open inline via the page below.
        def _is_svg(s: object) -> bool:
            return isinstance(s, str) and s.lstrip()[:200].lstrip().startswith(("<?xml", "<svg"))

        def _is_png(s: object) -> bool:
            return isinstance(s, str) and s.startswith("data:image/png")

        def _as_file(vn: str, vh: str):
            """(filename, bytes) for one image viz entry."""
            vn = str(vn)
            if _is_png(vh):
                fn = vn if vn.lower().endswith(".png") else f"{vn}.png"
                return fn, base64.b64decode(vh[vh.index(",") + 1:])
            fn = vn if vn.lower().endswith(".svg") else f"{vn}.svg"
            return fn, vh.encode("utf-8")

        images = [(vn, vh) for vn, vh in items if _is_svg(vh) or _is_png(vh)]
        if images and len(images) == len(items):
            if len(images) == 1:
                fn, data = _as_file(*images[0])
                media = "image/png" if fn.lower().endswith(".png") else "image/svg+xml"
                return data, media, fn, 200
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for vn, vh in images:
                    fn, data = _as_file(vn, vh)
                    zf.writestr(fn, data)
            # Clean zip name: drop the "<pkg>.composites." prefix and the
            # "__<ts>__<hash>" run-id suffix, leaving e.g. "fig07-…_viz.zip".
            base = str(run_id).rsplit(".", 1)[-1].split("__", 1)[0] or "run"
            safe = base.replace("/", "_")
            return buf.getvalue(), "application/zip", f"{safe}_viz.zip", 200

        # Otherwise: viz.json is {viz_name: html_string}; wrap into a viewable page.
        import html as _html
        sections = []
        for vname, vhtml in items:
            sections.append(
                f'<section style="margin:18px 0"><h3 style="font-family:system-ui;'
                f'color:#111827">{_html.escape(str(vname))}</h3>{vhtml}</section>'
            )
        body = "".join(sections) or '<p style="color:#6b7280">No visualizations were rendered for this run.</p>'
        page = (
            f'<!doctype html><meta charset="utf-8"><title>Run visualizations — {_html.escape(run_id)}</title>'
            f'<body style="max-width:1100px;margin:24px auto;padding:0 16px;font-family:system-ui">'
            f'<h2 style="color:#111827">Visualizations</h2>'
            f'<p style="color:#6b7280;font-size:13px"><code>{_html.escape(run_id)}</code></p>{body}</body>'
        )
        return page.encode("utf-8"), "text/html", None, 200
    if name in ("analyses", "verdict"):
        return raw, media_type, f"{name}_{run_id}.json", 200
    return raw, media_type, None, 200
