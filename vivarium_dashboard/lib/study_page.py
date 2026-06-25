"""Study-detail page builder — Jinja2 render + page builder.

Extracted from ``vivarium_dashboard.server`` so both the FastAPI seam
(``api/app.py``) and ``server.py``'s handler can share one implementation.
``server.py`` re-exports ``_render_study_detail_html`` as a thin shim (2-arg
``(name, spec)``) so ``publish.py`` and existing call-sites keep working
unchanged.

Public API
----------
render_study_detail_html(ws_root, name, spec)  → str
    Render the study-detail Jinja2 template for *name* against *spec*.

build_study_detail_page(ws_root, slug)  → (html, status_code)
    Full page builder: slug-validate → 404; spec-load → 404; render → 200.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import vivarium_dashboard as _vd_pkg

_TEMPLATES_DIR: Path = Path(_vd_pkg.__file__).parent / "templates"

from vivarium_dashboard.lib.study_spec import SLUG_RE as _SLUG_RE  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers (moved from server.py)
# ---------------------------------------------------------------------------

def _enrich_runs_with_meta(study_dir: Path, runs: list) -> list:
    """Merge per-run metadata from studies/<name>/runs.db into study.runs[].

    study.yaml's runs[] carries only the slim authoritative fields (run_id,
    variant, composite, label, status, n_steps). The runs_meta table in
    runs.db carries the rich per-run record (spec_id, params, started_at,
    completed_at, log_path). The Runs tab needs both. We copy the rich
    fields onto each entry under namespaced keys (``meta_*``) so the
    template doesn't have to know which DB they came from.

    Tolerant: if runs.db is absent, has no row for a run_id, or fails to
    open, the run entry is returned unchanged.
    """
    if not runs:
        return runs
    db = study_dir / "runs.db"
    rows: list = []
    if db.is_file():
        import sqlite3 as _sql
        try:
            conn = _sql.connect(str(db))
            conn.row_factory = _sql.Row
            rows = conn.execute(
                "SELECT run_id, spec_id, params_json, started_at, completed_at, "
                "n_steps, status, log_path FROM runs_meta"
            ).fetchall()
            conn.close()
        except _sql.Error:
            rows = []
    import json as _json
    by_id = {r["run_id"]: r for r in rows}
    enriched = []
    for r in runs:
        out = dict(r)
        # Always set meta_* keys so the Jinja template can call filters
        # against them unconditionally (None → empty cell).
        out.setdefault("meta_spec_id", None)
        out.setdefault("meta_started_at", None)
        out.setdefault("meta_completed_at", None)
        out.setdefault("meta_duration_sec", None)
        out.setdefault("meta_params", {})
        out.setdefault("meta_log_path", None)
        m = by_id.get(r.get("run_id"))
        if m is not None:
            try:
                params = _json.loads(m["params_json"] or "{}")
            except (ValueError, TypeError):
                params = {}
            started = m["started_at"]
            completed = m["completed_at"]
            duration = (completed - started) if (started and completed) else None
            out["meta_spec_id"] = m["spec_id"]
            out["meta_started_at"] = started
            out["meta_completed_at"] = completed
            out["meta_duration_sec"] = duration
            out["meta_params"] = params
            out["meta_log_path"] = m["log_path"]
        enriched.append(out)
    return enriched


def _humanize_study_name(slug: str) -> dict:
    """Mirror of JS _humanizeStudyName: peel a leading '<prefix>-NN[a-z]?-' into
    a chip and humanize the remainder. Keeps dashboard + report names identical."""
    m = re.match(r"^([a-z]+-\d+[a-z]*)-(.+)$", slug or "")
    if not m:
        return {"chip": "", "title": (slug or "").replace("-", " ")}
    rest = m.group(2).replace("-", " ")
    rest = rest[:1].upper() + rest[1:]
    if len(rest) > 60:
        rest = rest[:57] + "…"
    return {"chip": m.group(1), "title": rest}


def _jinja_fmt_ts(ts) -> str:
    """Format a unix timestamp as 'YYYY-MM-DD HH:MM' UTC, or '' if missing.

    Returns '' for None, empty values, AND undefined (Jinja's Undefined
    sentinel — e.g. when the template walks ``r.meta_started_at or
    r.started_at`` against a dict that has neither key). The previous
    ``(TypeError, ValueError)`` excludes Jinja's UndefinedError, which
    escaped here as a template-render failure for every <tr> in the
    Runs table whenever the merged run dict was missing both fields.
    """
    try:
        ts = float(ts)
    except Exception:
        return ""
    if not ts:
        return ""
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _jinja_fmt_duration(seconds) -> str:
    """Format a duration in seconds as '12s', '1m 30s', '2h 15m', or '' if missing.

    Same Undefined-tolerance contract as _jinja_fmt_ts above.
    """
    try:
        seconds = float(seconds)
    except Exception:
        return ""
    if seconds < 0:
        return ""
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s}s" if s else f"{m}m"
    h, m = divmod(m, 60)
    return f"{h}h {m}m" if m else f"{h}h"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_study_detail_html(ws_root: Path, name: str, spec: dict) -> str:
    """Render study-detail.html via Jinja2.

    This is the implementation extracted from ``server._render_study_detail_html``.
    ``server.py`` provides a 2-arg shim ``_render_study_detail_html(name, spec)``
    that injects the module-level WORKSPACE as ``ws_root`` so ``publish.py``
    (which calls ``_render_study_detail_html(slug, spec)``) keeps working.
    """
    import yaml
    import jinja2
    from vivarium_dashboard.lib.investigations import effective_status
    from vivarium_dashboard.lib.study_spec import study_dir

    spec = dict(spec)
    spec["runs"] = _enrich_runs_with_meta(study_dir(ws_root, name), spec.get("runs") or [])
    # Normalize implementation_requirements / gaps so the template iterates a
    # list of dicts — never a prose STRING.
    from vivarium_dashboard.lib.spec_norm import normalize_requirements as _normalize_requirements
    if spec.get("implementation_requirements") is not None:
        spec["implementation_requirements"] = _normalize_requirements(
            spec.get("implementation_requirements"))
    if spec.get("gaps") is not None:
        spec["gaps"] = _normalize_requirements(spec.get("gaps"))
    # F1: compute a single headline status from the multi-axis fields.
    spec["_effective_status"] = effective_status(spec)
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=True,
    )
    env.filters["fmt_ts"] = _jinja_fmt_ts
    env.filters["fmt_duration"] = _jinja_fmt_duration
    tpl = env.get_template("study-detail.html")
    _hn = _humanize_study_name(name)
    # PTools (Pathway Tools Omics Viewer) is a v2ecoli-style feature; only offer
    # the "Launch ptools" action when the workspace configures ui.ptools_server_url.
    _ptools_enabled = False
    try:
        _ws = yaml.safe_load((ws_root / "workspace.yaml").read_text(encoding="utf-8")) or {}
        _ptools_enabled = bool((_ws.get("ui") or {}).get("ptools_server_url"))
    except Exception:
        _ptools_enabled = False
    # W15 — open epistemic debts, computed server-side via the deterministic
    # pbg_superpowers collector. Defensive: degrade to no panel if not importable.
    epistemic_debts: list = []
    try:
        from pbg_superpowers.needs_attention import open_epistemic_debts
        epistemic_debts = open_epistemic_debts(spec) or []
    except Exception:
        epistemic_debts = []
    # Composite-resolution lint: flag declared composite refs that don't resolve.
    unresolved_composites: list = []
    try:
        from vivarium_dashboard.lib.composite_lookup import (
            known_composite_ids, unresolved_study_composite_refs,
        )
        unresolved_composites = unresolved_study_composite_refs(
            spec, known_composite_ids(ws_root)) or []
    except Exception:
        unresolved_composites = []
    return tpl.render(study=spec, name=name,
                      display_name=spec.get("title") or _hn["title"],
                      name_chip=_hn["chip"], ptools_enabled=_ptools_enabled,
                      epistemic_debts=epistemic_debts,
                      unresolved_composites=unresolved_composites)


def build_study_detail_page(ws_root: Path, slug: str) -> tuple[str, int]:
    """Full study-detail page builder: validate → load spec → render.

    Returns ``(html, status_code)`` where status_code is 200 on success
    or 404 for an invalid/unknown slug.  The 404 bodies are byte-identical
    to the legacy handler's ``_send_html`` responses.
    """
    from vivarium_dashboard.lib.study_spec import load_study_detail_spec

    if not _SLUG_RE.match(slug):
        return "<h1>Not found</h1>", 404
    spec: Optional[dict] = load_study_detail_spec(ws_root, slug)
    if spec is None:
        return (
            f"<h1>Study not found</h1><p><code>{slug}</code> does not exist.</p>",
            404,
        )
    html = render_study_detail_html(ws_root, slug, spec)
    return html, 200
