"""Render a standalone single-study HTML report.

Use case: an investigation declares ``focus_study: <slug>`` so the domain
expert (Haochen) can review one study at a time — verdict, key metrics,
biological summary, viz embeds — without wading through the full
investigation walkthrough.

This is intentionally a small server-side Python renderer, NOT a port of
the JS-side ``_buildInvestigationReportHtml``. The full investigation
report is assembled in the browser from the iset bundle; this single-
study path runs entirely server-side so it can be triggered from CLI /
PR review hooks without spinning up a browser.

Public API:
    render_single_study_report(ws_root, study_slug, *, investigation_slug=None,
                               out_dir=None) -> Path
    resolve_focus_study(ws_root, investigation_slug) -> str | None
"""
from __future__ import annotations

import html as _htmllib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from vivarium_dashboard.lib.workspace_paths import WorkspacePaths


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def resolve_focus_study(ws_root: Path, investigation_slug: str) -> Optional[str]:
    """Read ``investigations/<slug>/investigation.yaml`` and return its
    ``focus_study`` field. Returns None if the file is missing or the
    field is absent.
    """
    inv_path = WorkspacePaths.load(ws_root).investigations / investigation_slug / "investigation.yaml"
    if not inv_path.is_file():
        return None
    try:
        spec = yaml.safe_load(inv_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    val = spec.get("focus_study")
    return val.strip() if isinstance(val, str) and val.strip() else None


def _load_study_spec(ws_root: Path, study_slug: str) -> dict:
    """Return the parsed ``studies/<slug>/study.yaml``. Raises FileNotFoundError
    if missing, ValueError on parse error.
    """
    wp = WorkspacePaths.load(ws_root)
    p = wp.studies / study_slug / "study.yaml"
    if not p.is_file():
        # Legacy fallback: investigations/<slug>/spec.yaml (pre-studies layout)
        p = wp.investigations / study_slug / "spec.yaml"
    if not p.is_file():
        raise FileNotFoundError(
            f"study.yaml not found for {study_slug!r} (looked under studies/ and investigations/)"
        )
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"failed to parse {p}: {e}") from e


def _collect_viz_html(ws_root: Path, study_slug: str) -> list[dict]:
    """Return a list of ``{name, html}`` entries for every per-study viz file
    under ``studies/<slug>/viz/*.html``. Empty list if dir is missing.

    The HTML is inlined verbatim so the report opens standalone in a browser
    without needing the dashboard server to be running.
    """
    viz_dir = WorkspacePaths.load(ws_root).studies / study_slug / "viz"
    if not viz_dir.is_dir():
        return []
    entries = []
    for p in sorted(viz_dir.glob("*.html")):
        try:
            entries.append({"name": p.stem, "html": p.read_text(encoding="utf-8")})
        except OSError:
            continue
    return entries


# ---------------------------------------------------------------------------
# HTML rendering (no templates — small enough to build inline)
# ---------------------------------------------------------------------------

_VERDICT_MAP = {
    # Mirrors VERDICT_MAP in walkthrough.js so the badge looks familiar to
    # reviewers who know the full investigation report.
    "passing":              {"label": "Passing",                       "color": "#16a34a", "bg": "#dcfce7"},
    "passing-with-caveats": {"label": "Passing with caveats",          "color": "#92400e", "bg": "#fef3c7"},
    "blocked":              {"label": "Blocked",                       "color": "#991b1b", "bg": "#fee2e2"},
    "preliminary":          {"label": "Preliminary",                   "color": "#3730a3", "bg": "#e0e7ff"},
    "failing-bio":          {"label": "Failing biological validation", "color": "#991b1b", "bg": "#fee2e2"},
    "calibrating":          {"label": "Calibration in progress",       "color": "#155e75", "bg": "#cffafe"},
    "not-started":          {"label": "Not started",                   "color": "#475569", "bg": "#e2e8f0"},
}


def _h(s) -> str:
    if s is None:
        return ""
    return _htmllib.escape(str(s), quote=True)


def _multiline(s) -> str:
    """Render a YAML block-scalar string: double newlines become paragraph
    breaks, single newlines collapse to a space (mirrors walkthrough.js)."""
    if not s:
        return ""
    escaped = _h(s)
    # Collapse paragraphs first to avoid the single-newline pass eating them.
    parts = escaped.split("\n\n")
    return "<br><br>".join(p.replace("\n", " ").strip() for p in parts)


def _render_verdict_badge(verdict_key: str | None) -> str:
    if not verdict_key:
        return ""
    key = verdict_key.strip().lower()
    v = _VERDICT_MAP.get(key)
    if v is None:
        v = {"label": verdict_key, "color": "#0f172a", "bg": "#f1f5f9"}
    return (
        f'<span class="verdict-badge" style="display:inline-block;'
        f'padding:4px 12px;border-radius:9999px;font-weight:700;'
        f'background:{v["bg"]};color:{v["color"]};font-size:0.9em">'
        f'{_h(v["label"])}</span>'
    )


def _render_key_metrics(metrics: list) -> str:
    if not metrics:
        return ""
    chips = []
    for m in metrics:
        if isinstance(m, str):
            chips.append(
                f'<span class="metric-chip" style="display:inline-block;'
                f'padding:4px 10px;border-radius:6px;background:#f1f5f9;'
                f'color:#0f172a;margin:2px;font-size:0.88em">{_h(m)}</span>'
            )
        elif isinstance(m, dict):
            st = str(m.get("status", "")).lower()
            colors = {
                "pass": ("#dcfce7", "#166534"),
                "warn": ("#fef3c7", "#92400e"),
                "fail": ("#fee2e2", "#991b1b"),
            }
            bg, fg = colors.get(st, ("#f1f5f9", "#0f172a"))
            icon = {"pass": "✅ ", "warn": "⚠️ ", "fail": "❌ "}.get(st, "")
            label = m.get("label", "")
            value = m.get("value")
            text = label + (f": {value}" if value is not None else "")
            chips.append(
                f'<span class="metric-chip" style="display:inline-block;'
                f'padding:4px 10px;border-radius:6px;background:{bg};'
                f'color:{fg};margin:2px;font-size:0.88em">{_h(icon)}{_h(text)}</span>'
            )
    return '<div class="metrics-strip" style="margin:12px 0">' + "".join(chips) + "</div>"


def _render_biological_summary(spec: dict) -> str:
    """Pull whatever biological/narrative text the study declares."""
    bits = []
    bio = spec.get("biological_summary")
    if bio:
        bits.append(
            f'<section class="biology"><h2>Biological summary</h2>'
            f'<p class="biology-prose" style="line-height:1.55">{_multiline(bio)}</p></section>'
        )
    rep = spec.get("report") or {}
    # The compact-report block has its own narrative slots; render any that
    # are present as labelled paragraphs so the reviewer sees the same prose
    # the investigation report would show in the per-study card.
    narrative_slots = [
        ("purpose", "Purpose"),
        ("setup", "Setup"),
        ("result", "Result"),
        ("interpretation", "Interpretation"),
        ("decision", "Decision"),
        ("next_action", "Next action"),
    ]
    rows = []
    for key, label in narrative_slots:
        val = rep.get(key)
        if val:
            rows.append(
                f'<div class="narrative-row" style="margin:10px 0">'
                f'<strong>{_h(label)}:</strong> {_multiline(val)}'
                f'</div>'
            )
    if rows:
        bits.append(
            '<section class="report-narrative"><h2>Study narrative</h2>'
            + "".join(rows)
            + '</section>'
        )
    # MINIMAL fallback: no authored biology/narrative → derive the narrative from
    # the study's objective + findings so the section still renders meaningfully.
    if not bits:
        derived = []
        obj = spec.get("objective")
        if obj:
            derived.append(
                f'<div class="narrative-row" style="margin:10px 0">'
                f'<strong>Objective:</strong> {_multiline(obj)}</div>'
            )
        for f in (spec.get("findings") or []):
            if isinstance(f, dict):
                stmt = f.get("statement") or f.get("summary")
                if stmt:
                    fid = f.get("id", "")
                    chip = _finding_weight_chip(_finding_weight(spec, f))
                    derived.append(
                        f'<div class="narrative-row" style="margin:10px 0">'
                        f'<strong>Finding{(" " + _h(fid)) if fid else ""}:</strong> '
                        f'{_multiline(stmt)}{chip}</div>'
                    )
        if derived:
            bits.append(
                '<section class="report-narrative"><h2>Study narrative</h2>'
                + "".join(derived) + '</section>'
            )
    return "".join(bits)


def _render_viz_embeds(viz_entries: list[dict]) -> str:
    if not viz_entries:
        return ""
    blocks = []
    for entry in viz_entries:
        blocks.append(
            f'<section class="viz-embed" style="margin:24px 0;'
            f'border:1px solid #e2e8f0;border-radius:8px;padding:16px">'
            f'<h3 style="margin-top:0">{_h(entry["name"])}</h3>'
            # Each viz HTML may contain its own <html>/<body>; we wrap in
            # an iframe srcdoc so it stays sandboxed and CSS doesn't bleed
            # into the surrounding report.
            # scrolling="no" + min-height + the _fitEmbed script below
            # match the walkthrough.js infrastructural guarantee (PR #121):
            # iframes never get inner scrollbars; their height grows to
            # match the rendered content (including Plotly legends that
            # overflow the chart container).
            f'<iframe scrolling="no" srcdoc="{_h(entry["html"])}" '
            f'class="viz-iframe" '
            f'style="width:100%;min-height:520px;border:0;'
            f'display:block;overflow:hidden" loading="lazy"></iframe>'
            f'</section>'
        )
    # Walk every .viz-iframe and size it to its content's scrollHeight,
    # measuring chart-div children too (Plotly's legend overflow is not in
    # body.scrollHeight). Re-runs on resize + after a few delays so
    # Plotly's async render lands. Copied/condensed from walkthrough.js.
    fit_script = (
        '<script>'
        '(function(){'
        'function fit(f){try{var d=f.contentDocument||(f.contentWindow&&f.contentWindow.document);'
        'if(!d)return;var e=d.documentElement,b=d.body,plotlyMax=0;'
        'try{var charts=d.querySelectorAll(".plotly-graph-div, [data-plotly], div[id]");'
        'for(var i=0;i<charts.length;i++){var c=charts[i];'
        'var ch=Math.max(c.scrollHeight||0,c.offsetHeight||0,c.clientHeight||0);'
        'if(ch>plotlyMax)plotlyMax=ch+c.offsetTop;}}catch(e){}'
        'var h=Math.max(e?e.scrollHeight:0,b?b.scrollHeight:0,plotlyMax);'
        'if(h>0)f.style.height=h+"px";}catch(err){}}'
        'function wireAll(){var frames=document.querySelectorAll(".viz-iframe");'
        'frames.forEach(function(f){f.addEventListener("load",function(){fit(f);'
        '[150,500,1200,2500,4000].forEach(function(t){setTimeout(function(){fit(f);},t);});'
        'if(window.ResizeObserver){try{var d=f.contentDocument;if(d){'
        'var ro=new ResizeObserver(function(){fit(f);});ro.observe(d.documentElement);}}catch(e){}}});'
        'if(f.contentDocument&&f.contentDocument.readyState==="complete")fit(f);});}'
        'if(document.readyState==="loading"){document.addEventListener("DOMContentLoaded",wireAll);}'
        'else{wireAll();}'
        '})();'
        '</script>'
    )
    return (
        '<section class="visualizations"><h2>Visualizations</h2>'
        + "".join(blocks)
        + fit_script
        + '</section>'
    )


# ---------------------------------------------------------------------------
# Derivation — when a study has no authored ``report:`` block, build the
# standard report fields from the study's REAL content (gate evaluator, run
# outcomes, findings, objective). A MINIMAL but valid study then still renders
# the standard report structure instead of a near-empty page.
# ---------------------------------------------------------------------------

_GATE_TO_VERDICT = {
    "passed": "passing", "failed": "failing-bio",
    "needs_calibration": "calibrating", "blocked": "blocked",
    "not_started": "not-started",
}


def _derive_verdict(spec: dict) -> str:
    ge = (spec.get("pipeline_gate") or {}).get("gate_evaluator") or {}
    return _GATE_TO_VERDICT.get(ge.get("result") or spec.get("gate_status"), "")


def _latest_outcomes(spec: dict) -> dict:
    for r in reversed(spec.get("runs") or []):
        if isinstance(r, dict) and r.get("outcomes"):
            return r["outcomes"]
    return {}


def _derive_key_metrics(spec: dict) -> list[dict]:
    """Behavior-test outcomes as metric chips (PASS/FAIL + the observed value)."""
    metrics = []
    for name, o in _latest_outcomes(spec).items():
        if not isinstance(o, dict):
            continue
        res = str(o.get("result", "")).upper()
        observed = o.get("observed")
        metrics.append({
            "label": name,
            "value": observed if observed is not None else res,
            "status": "pass" if res == "PASS" else ("fail" if res == "FAIL" else "warn"),
        })
    return metrics


def _derive_insight(spec: dict) -> str:
    """Headline insight: the first finding's statement/summary."""
    for f in (spec.get("findings") or []):
        if isinstance(f, dict):
            s = f.get("statement") or f.get("summary")
            if s:
                return s
    return ""


# ---------------------------------------------------------------------------
# C2 — derived 3-track conclusion verdicts.
# The `result` of each track is COMPUTED (read-only) from canonical fields;
# the `basis` free-text is author/agent-supplied. These three rules are kept
# IDENTICAL in static/walkthrough.js (_deriveConclusionVerdicts) and
# static/study-detail.js so every surface shows the same badge.
# ---------------------------------------------------------------------------

_GATE_RESULT_NORM = {
    "pass": "PASS", "passed": "PASS", "ok": "PASS",
    "fail": "FAIL", "failed": "FAIL",
    "partial": "PARTIAL", "mixed": "PARTIAL", "needs_calibration": "PARTIAL",
}

_RUN_ERRORED = {"error", "errored", "failed", "crashed", "fail"}
_RUN_COMPLETED = {"completed", "complete", "success", "succeeded", "ok", "done", "finished"}


def _norm_gate_result(val) -> str:
    return _GATE_RESULT_NORM.get(str(val or "").strip().lower(), "PENDING")


def _derive_conclusion_verdicts(spec: dict) -> dict:
    """Compute the three verdict-track results from canonical fields.

    Rules (canonical — mirrored in walkthrough.js + study-detail.js):
      * ``biological_validation``   ← ``pipeline_gate.gate_evaluator.result``
      * ``regression_compatibility``← PASS if all runs completed without error,
        FAIL if any errored, PARTIAL if mixed/unknown, PENDING if no runs.
      * ``explanatory_gain``        ← PASS if >=1 finding has
        ``tier=='interpretation'`` (or any ``mechanism_origin`` set);
        PARTIAL if findings but none qualify; GAP if there are no findings.
    The authored ``basis`` free-text is carried through per track.
    """
    authored = spec.get("conclusion_verdicts") or {}

    ge = (spec.get("pipeline_gate") or {}).get("gate_evaluator") or {}
    bio = _norm_gate_result(ge.get("result") or spec.get("gate_status"))

    runs = [r for r in (spec.get("runs") or []) if isinstance(r, dict)]
    if not runs:
        reg = "PENDING"
    else:
        statuses = [str(r.get("status", "")).strip().lower() for r in runs]
        if any(s in _RUN_ERRORED for s in statuses):
            reg = "FAIL"
        elif all(s in _RUN_COMPLETED for s in statuses):
            reg = "PASS"
        else:
            reg = "PARTIAL"

    findings = [f for f in (spec.get("findings") or []) if isinstance(f, dict)]
    if not findings:
        exp = "GAP"
    elif any((f.get("tier") == "interpretation") or f.get("mechanism_origin") for f in findings):
        exp = "PASS"
    else:
        exp = "PARTIAL"

    def _basis(track):
        t = authored.get(track)
        return (t.get("basis", "") if isinstance(t, dict) else "")

    return {
        "biological_validation":    {"result": bio, "basis": _basis("biological_validation")},
        "regression_compatibility": {"result": reg, "basis": _basis("regression_compatibility")},
        "explanatory_gain":         {"result": exp, "basis": _basis("explanatory_gain")},
    }


_TRACK_COLORS = {
    "PASS": ("#dcfce7", "#166534"),
    "PARTIAL": ("#fef3c7", "#92400e"),
    "FAIL": ("#fee2e2", "#991b1b"),
    "GAP": ("#f1f5f9", "#475569"),
    "PENDING": ("#f1f5f9", "#475569"),
}


def _render_conclusion_verdicts(spec: dict) -> str:
    """Render the derived 3-track verdict block (read-only computed badges)."""
    cv = _derive_conclusion_verdicts(spec)
    tracks = [
        ("biological_validation", "Biological validation", "from gate evaluator"),
        ("regression_compatibility", "Regression compatibility", "from run status"),
        ("explanatory_gain", "Explanatory gain", "from interpretation-tier findings"),
    ]
    rows = []
    for key, label, hint in tracks:
        t = cv[key]
        res = t["result"]
        bg, fg = _TRACK_COLORS.get(res, ("#f1f5f9", "#475569"))
        basis = t.get("basis") or ""
        basis_html = (
            f'<div style="color:#475569;font-size:0.9em;margin-top:2px">{_multiline(basis)}</div>'
            if basis else ""
        )
        rows.append(
            '<div style="padding:8px 0;border-top:1px solid #f1f5f9">'
            '<div style="display:flex;gap:10px;align-items:baseline;flex-wrap:wrap">'
            f'<span style="display:inline-block;min-width:11em;font-weight:600;color:#1e293b">{_h(label)}</span>'
            f'<span style="display:inline-block;padding:2px 10px;border-radius:9999px;'
            f'background:{bg};color:{fg};font-weight:700;font-size:0.85em">{_h(res)}</span>'
            f'<span style="color:#94a3b8;font-size:0.82em">{_h(hint)} · computed</span>'
            '</div>'
            f'{basis_html}'
            '</div>'
        )
    return (
        '<section id="verdicts"><h2>Conclusion verdicts</h2>'
        '<p style="color:#475569;font-size:0.92em;margin:0 0 8px">Three-track verdict — '
        'each result is <strong>computed</strong> from canonical fields (gate evaluator, run '
        'status, finding tiers). The basis is the author\'s rationale.</p>'
        + "".join(rows) +
        '</section>'
    )


def _render_conclusion_synthesis(spec: dict) -> str:
    """C3 — read-only four-section synthesis sourced from canonical fields:
    Claims←findings[].statement, Evidence←findings[].evidence,
    Limitations←limitations, Next steps←discovery_implications.followup_study_proposals.
    """
    findings = [f for f in (spec.get("findings") or []) if isinstance(f, dict)]
    # Claims — one per finding, each tagged with its W8 evidential-weight chip.
    claim_lis = []
    for f in findings:
        text = f.get("statement") or f.get("summary")
        if not text:
            continue
        chip = _finding_weight_chip(_finding_weight(spec, f))
        claim_lis.append(f'<li>{_multiline(str(text))}{chip}</li>')
    evidence = []
    for f in findings:
        ev = f.get("evidence")
        if isinstance(ev, dict):
            ev = ev.get("observed") or ev.get("summary") or ev.get("detail")
        if ev is not None and ev != "":
            evidence.append(ev)

    limitations = spec.get("limitations") or []
    if isinstance(limitations, str):
        limitations = [limitations]

    di = spec.get("discovery_implications") or {}
    next_steps = []
    for p in (di.get("followup_study_proposals") or []):
        if isinstance(p, dict):
            t = p.get("title") or p.get("id")
            if t:
                next_steps.append(t)
        elif p:
            next_steps.append(str(p))

    sections = [
        ("Evidence", evidence),
        ("Limitations", limitations),
        ("Next steps", next_steps),
    ]
    blocks = []
    # Claims first — rendered separately because each <li> carries a weight chip.
    if claim_lis:
        blocks.append(
            '<div style="margin:10px 0"><strong style="color:#1e293b">Claims</strong>'
            f'<ul style="margin:4px 0 0;padding-left:20px;color:#334155">{"".join(claim_lis)}</ul></div>'
        )
    for label, items in sections:
        items = [i for i in (items or []) if i]
        if not items:
            continue
        lis = "".join(f'<li>{_multiline(str(i))}</li>' for i in items)
        blocks.append(
            f'<div style="margin:10px 0"><strong style="color:#1e293b">{_h(label)}</strong>'
            f'<ul style="margin:4px 0 0;padding-left:20px;color:#334155">{lis}</ul></div>'
        )
    if not blocks:
        return ""
    return (
        '<section id="synthesis"><h2>Conclusion synthesis</h2>'
        '<p style="color:#475569;font-size:0.92em;margin:0 0 8px">Read-only synthesis derived '
        'from the study\'s canonical fields (findings, limitations, follow-up proposals).</p>'
        + "".join(blocks) +
        '</section>'
    )


def _render_alternatives(spec: dict) -> str:
    """C5 — alternative hypotheses. Canonical source is
    ``discovery_implications.alternate_hypotheses``; fall back to top-level
    ``alternative_hypotheses`` so authored prose anywhere still surfaces.
    """
    di = spec.get("discovery_implications") or {}
    alts = di.get("alternate_hypotheses") or spec.get("alternative_hypotheses")
    if not alts:
        return ""
    items = []
    for a in alts:
        if isinstance(a, dict):
            claim = a.get("claim") or a.get("hypothesis") or ""
            extra = []
            if a.get("discriminated_by"):
                extra.append(f'discriminated by: {_h(a["discriminated_by"])}')
            if a.get("status"):
                extra.append(f'status: {_h(a["status"])}')
            extra_html = (
                f' <span style="color:#94a3b8;font-size:0.85em">({" · ".join(extra)})</span>'
                if extra else ""
            )
            if claim or extra_html:
                items.append(f'<li>{_h(claim)}{extra_html}</li>')
        elif a:
            items.append(f'<li>{_h(str(a))}</li>')
    if not items:
        return ""
    return (
        '<section id="alternatives"><h2>Alternative hypotheses</h2>'
        f'<ul style="padding-left:20px;color:#334155;line-height:1.6">{"".join(items)}</ul>'
        '</section>'
    )


def _render_controls_and_falsifiability(spec: dict) -> str:
    """Item 13 — surface the scored-but-hidden ``controls[]`` table and the
    ``falsifiability`` statement verbatim (the rigor scorecard only emits a dot
    for these). Returns '' when neither is present.
    """
    controls = [c for c in (spec.get("controls") or []) if isinstance(c, dict)]
    falsifiability = spec.get("falsifiability")
    bits = []
    if controls:
        head = (
            '<tr style="text-align:left;color:#475569;font-size:0.82em">'
            '<th style="padding:4px 8px">Name</th><th style="padding:4px 8px">Kind</th>'
            '<th style="padding:4px 8px">Hypothesis</th><th style="padding:4px 8px">Expected</th>'
            '<th style="padding:4px 8px">Observed</th><th style="padding:4px 8px">Result</th></tr>'
        )
        trows = []
        for c in controls:
            res = str(c.get("result", "")).upper()
            bg, fg = _TRACK_COLORS.get(res, ("#f1f5f9", "#475569"))
            res_html = (
                f'<span style="padding:1px 8px;border-radius:9999px;background:{bg};color:{fg};'
                f'font-weight:600;font-size:0.82em">{_h(res)}</span>' if res else ""
            )
            trows.append(
                '<tr style="border-top:1px solid #f1f5f9;font-size:0.9em">'
                f'<td style="padding:4px 8px">{_h(c.get("name", ""))}</td>'
                f'<td style="padding:4px 8px">{_h(c.get("kind", ""))}</td>'
                f'<td style="padding:4px 8px">{_h(c.get("hypothesis", ""))}</td>'
                f'<td style="padding:4px 8px">{_h(c.get("expected", ""))}</td>'
                f'<td style="padding:4px 8px">{_h(c.get("observed", ""))}</td>'
                f'<td style="padding:4px 8px">{res_html}</td>'
                '</tr>'
            )
        bits.append(
            '<div id="rigor-controls" style="margin:10px 0">'
            '<strong style="color:#1e293b">Controls</strong>'
            '<table style="border-collapse:collapse;width:100%;margin-top:4px">'
            + head + "".join(trows) + '</table></div>'
        )
    if falsifiability:
        bits.append(
            '<div id="rigor-falsifiability" style="margin:10px 0;padding:8px 12px;'
            'background:#f8fafc;border-left:4px solid #64748b;border-radius:4px">'
            f'<strong style="color:#1e293b">Falsifiability:</strong> '
            f'{_multiline(str(falsifiability))}</div>'
        )
    return "".join(bits)


def _render_rigor(study_spec: dict, *, skeptic: bool = False) -> str:
    """Evidence & rigor scorecard section — deterministic skeptic-feedback
    (replication, negative controls, alternative hypotheses, claim discipline,
    falsifiability, engineered-vs-emergent) computed by pbg_superpowers.rigor.

    Returns '' if pbg-superpowers isn't importable, so the report degrades
    gracefully.

    In ``skeptic`` mode (W24) the dimension rows are sorted by severity
    (gap → warn → ok) so unmet rigor demands surface first, and the embedded
    controls/falsifiability detail is omitted because the skeptic layout
    renders it as its own ordered section immediately below.
    """
    try:
        from pbg_superpowers.rigor import study_rigor
    except Exception:
        return ""
    sc = study_rigor(study_spec)
    dims = sc.get("dimensions") or []
    if not dims:
        return ""
    if skeptic:
        _sev_rank = {"gap": 0, "warn": 1, "ok": 2}
        dims = sorted(dims, key=lambda d: _sev_rank.get(d.get("severity", "gap"), 0))
    color = {"ok": "#16a34a", "warn": "#d97706", "gap": "#dc2626"}
    glyph = {"ok": "✓", "warn": "⚠", "gap": "✗"}
    # Item 13 — link the controls / falsifiability dimension dots to the
    # verbatim detail blocks we now emit below the scorecard.
    has_controls = bool([c for c in (study_spec.get("controls") or []) if isinstance(c, dict)])
    has_falsifiability = bool(study_spec.get("falsifiability"))
    rows = []
    for d in dims:
        sev = d.get("severity", "gap")
        c = color.get(sev, "#64748b")
        comments = " ".join(d.get("comments") or [])
        comment_html = (f' <span style="color:#94a3b8;font-size:0.82em">{_h(comments)}</span>'
                        if comments else "")
        label = d.get("label", "")
        ll = label.lower()
        link = ""
        if has_controls and ("control" in ll):
            link = ' <a href="#rigor-controls" style="font-size:0.82em">(see controls ↓)</a>'
        elif has_falsifiability and ("falsifi" in ll):
            link = ' <a href="#rigor-falsifiability" style="font-size:0.82em">(see statement ↓)</a>'
        rows.append(
            '<div style="display:flex;gap:10px;align-items:flex-start;padding:7px 0;'
            'border-top:1px solid #f1f5f9">'
            f'<span style="color:{c};font-weight:700;min-width:1.2em">{glyph.get(sev, "•")}</span>'
            f'<div><strong style="color:#1e293b">{_h(label)}</strong>{comment_html}{link}'
            f'<div style="color:#475569;font-size:0.9em;margin-top:1px">{_h(d.get("detail", ""))}</div>'
            '</div></div>'
        )
    # In skeptic mode the controls/falsifiability detail is rendered as its
    # own ordered section below, so don't embed it here (avoid duplication).
    controls_html = "" if skeptic else _render_controls_and_falsifiability(study_spec)
    return (
        '<section id="rigor"><h2>Evidence &amp; rigor</h2>'
        '<p style="color:#475569;font-size:0.92em;margin:0 0 8px">Deterministic feedback '
        'on how well this study defends its claims against a skeptical reader — computed '
        'from declared fields. Gaps prompt the next iteration to add controls, replicates, '
        'alternatives, or a falsifiability note.</p>'
        f'<div style="font-weight:600;color:#1e293b;margin-bottom:2px">{_h(sc.get("summary", ""))}</div>'
        + "".join(rows)
        + controls_html +
        '</section>'
    )


# ---------------------------------------------------------------------------
# W8 — per-finding evidential-weight chip
# ---------------------------------------------------------------------------

_WEIGHT_CHIP_COLORS = {
    "strong":   ("#dcfce7", "#166534"),
    "moderate": ("#fef9c3", "#854d0e"),
    "weak":     ("#fee2e2", "#991b1b"),
}


def _finding_weight(spec: dict, finding: dict) -> dict | None:
    """W8 — per-finding evidential weight via pbg_superpowers.rigor.

    Defensive: returns None when pbg-superpowers (or the new function) isn't
    importable, so the chip simply doesn't render and the report degrades.
    """
    try:
        from pbg_superpowers.rigor import finding_evidential_weight
    except Exception:
        return None
    try:
        return finding_evidential_weight(spec, finding)
    except Exception:
        return None


def _finding_weight_chip(weight_info: dict | None) -> str:
    """Render the strong/moderate/weak pill for a finding's evidential weight."""
    if not weight_info or not weight_info.get("weight"):
        return ""
    w = str(weight_info["weight"])
    bg, fg = _WEIGHT_CHIP_COLORS.get(w, ("#f1f5f9", "#475569"))
    n = weight_info.get("n_supporting")
    label = _h(w) + (f" · {n}/5" if isinstance(n, int) else "")
    dims = weight_info.get("dims") or {}
    title = ""
    if dims:
        supported = [k for k, v in dims.items() if v]
        title = ' title="evidence dims: ' + _h(", ".join(supported) or "none") + '"'
    return (
        f'<span class="finding-weight"{title} style="display:inline-block;'
        f'padding:1px 8px;border-radius:9999px;background:{bg};color:{fg};'
        f'font-weight:600;font-size:0.72em;margin-left:6px;vertical-align:middle">'
        f'{label}</span>'
    )


# ---------------------------------------------------------------------------
# W15 — open epistemic debts panel
# ---------------------------------------------------------------------------

_DEBT_SEV_COLORS = {
    "high":   ("#fee2e2", "#991b1b"),
    "medium": ("#fef9c3", "#854d0e"),
    "low":    ("#f1f5f9", "#475569"),
}


def _render_epistemic_debts(spec: dict) -> str:
    """W15 — "Open epistemic debts" panel, driven by the deterministic
    ``pbg_superpowers.needs_attention.open_epistemic_debts`` collector (which
    derives from rigor + viz-freshness so it can't drift). Returns '' when the
    collector isn't importable or there are no debts.
    """
    try:
        from pbg_superpowers.needs_attention import open_epistemic_debts
    except Exception:
        return ""
    try:
        debts = open_epistemic_debts(spec) or []
    except Exception:
        return ""
    if not debts:
        return ""
    rows = []
    for d in debts:
        if not isinstance(d, dict):
            continue
        sev = str(d.get("severity") or "low").lower()
        bg, fg = _DEBT_SEV_COLORS.get(sev, ("#f1f5f9", "#475569"))
        kind = _h(str(d.get("kind") or ""))
        ref = _h(str(d.get("ref") or ""))
        note = _multiline(str(d.get("note") or ""))
        rows.append(
            '<div style="display:flex;gap:10px;align-items:flex-start;padding:7px 0;'
            'border-top:1px solid #f1f5f9">'
            f'<span style="padding:1px 8px;border-radius:9999px;background:{bg};color:{fg};'
            f'font-weight:600;font-size:0.72em;white-space:nowrap">{_h(sev)}</span>'
            f'<div><strong style="color:#1e293b">{kind}</strong>'
            + (f' <code style="font-size:0.82em">{ref}</code>' if ref else "")
            + f'<div style="color:#475569;font-size:0.9em;margin-top:1px">{note}</div>'
            '</div></div>'
        )
    if not rows:
        return ""
    return (
        '<section id="epistemic-debts"><h2>Open epistemic debts</h2>'
        '<p style="color:#475569;font-size:0.92em;margin:0 0 8px">Negative knowledge — '
        'what this study has <em>not</em> yet established (untested claims, absent '
        'controls, uncalibrated metrics, un-excluded alternatives, unexplored regions, '
        'stale visuals). Derived from the rigor scorecard + freshness signals.</p>'
        + "".join(rows) +
        '</section>'
    )


# ---------------------------------------------------------------------------
# W24 — skeptical-reader audit trail + limitations strip
# ---------------------------------------------------------------------------

def _render_audit_trail(spec: dict) -> str:
    """W24 — one compressed audit strip built from existing canonical fields:
    claim · evidence · assumptions (falsifiability) · controls summary ·
    limitations/remaining-uncertainties · next discriminating test · threshold
    provenance. Shows "threshold provenance: none" (rather than omitting) when
    no behavior-test band carries ``cites``/``calibration_anchor`` — the
    absence is itself the signal a skeptic wants to see.
    """
    findings = [f for f in (spec.get("findings") or []) if isinstance(f, dict)]
    primary = findings[0] if findings else {}
    claim = primary.get("statement") or primary.get("summary") or spec.get("claim") or "—"

    ev = primary.get("evidence")
    if isinstance(ev, dict):
        ev = (ev.get("observed") or ev.get("summary") or ev.get("detail")
              or ev.get("from_test"))
    evidence = ev or "—"

    assumptions = spec.get("falsifiability") or "—"

    controls = [c for c in (spec.get("controls") or []) if isinstance(c, dict)]
    if controls:
        n_disc = sum(1 for c in controls
                     if str(c.get("result", "")).upper() == "PASS"
                     and str(c.get("observed") or "").strip())
        controls_summary = f"{len(controls)} declared · {n_disc} discriminating"
    else:
        controls_summary = "none"

    lims = spec.get("limitations") or []
    if isinstance(lims, str):
        lims = [lims]
    di = spec.get("discovery_implications") or {}
    rem = di.get("remaining_uncertainties") or []
    if isinstance(rem, str):
        rem = [rem]
    limits_text = "; ".join(str(x) for x in (list(lims) + list(rem)) if x) or "—"

    next_test = None
    for f in findings:
        if f.get("next_action"):
            next_test = f["next_action"]
            break
    if not next_test:
        for p in (di.get("followup_study_proposals") or []):
            if isinstance(p, dict) and (p.get("title") or p.get("motivation")):
                next_test = p.get("title") or p.get("motivation")
                break
    next_test = next_test or "—"

    bands = spec.get("behavior_tests") or spec.get("expected_behavior") or []
    has_prov = any(
        isinstance(b, dict) and (b.get("cites") or b.get("calibration_anchor"))
        for b in bands
    )
    threshold = "present" if has_prov else "none"

    pairs = [
        ("Claim", claim),
        ("Evidence", evidence),
        ("Assumptions (falsifiability)", assumptions),
        ("Controls", controls_summary),
        ("Limitations / remaining uncertainties", limits_text),
        ("Next discriminating test", next_test),
        ("Threshold provenance", threshold),
    ]
    rows = "".join(
        '<div style="display:flex;gap:10px;padding:6px 0;border-top:1px solid #e2e8f0">'
        f'<span style="flex:0 0 16em;font-weight:600;color:#475569">{_h(label)}</span>'
        f'<span style="color:#1e293b">{_multiline(str(val))}</span>'
        '</div>'
        for label, val in pairs
    )
    return (
        '<section id="audit-trail"><h2>Audit trail</h2>'
        '<p style="color:#475569;font-size:0.92em;margin:0 0 8px">The skeptic\'s '
        'one-glance ledger: the claim and exactly what backs it, what it assumes, '
        'what could overturn it, and what it has not yet settled.</p>'
        '<div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;'
        'padding:4px 14px 10px">' + rows + '</div>'
        '</section>'
    )


def _render_limitations(spec: dict) -> str:
    """W24 — dedicated limitations / remaining-uncertainties section (skeptic
    layout renders this as its own ordered step). Returns '' when empty.
    """
    lims = spec.get("limitations") or []
    if isinstance(lims, str):
        lims = [lims]
    di = spec.get("discovery_implications") or {}
    rem = di.get("remaining_uncertainties") or []
    if isinstance(rem, str):
        rem = [rem]
    blocks = []
    if [x for x in lims if x]:
        lis = "".join(f'<li>{_multiline(str(x))}</li>' for x in lims if x)
        blocks.append(
            '<div style="margin:10px 0"><strong style="color:#1e293b">Limitations</strong>'
            f'<ul style="margin:4px 0 0;padding-left:20px;color:#334155">{lis}</ul></div>'
        )
    if [x for x in rem if x]:
        lis = "".join(f'<li>{_multiline(str(x))}</li>' for x in rem if x)
        blocks.append(
            '<div style="margin:10px 0"><strong style="color:#1e293b">Remaining '
            'uncertainties</strong>'
            f'<ul style="margin:4px 0 0;padding-left:20px;color:#334155">{lis}</ul></div>'
        )
    if not blocks:
        return ""
    return (
        '<section id="limitations"><h2>Limitations &amp; remaining uncertainties</h2>'
        + "".join(blocks) +
        '</section>'
    )


def _render_html(study_spec: dict, viz_entries: list[dict],
                 *, investigation_slug: Optional[str], generated_at: str,
                 skeptic: bool = False) -> str:
    rep = study_spec.get("report") or {}
    # Authored ``report:`` fields win; absent ones are DERIVED from real study
    # content so minimal studies still render the standard structure.
    title = rep.get("title") or study_spec.get("title") or study_spec.get("name", "study")
    verdict = rep.get("verdict") or _derive_verdict(study_spec)
    confidence = rep.get("confidence") or ""
    evidence_quality = rep.get("evidence_quality") or ""
    objective = rep.get("objective") or study_spec.get("objective") or ""
    conclusion = rep.get("conclusion") or ""
    main_insight = rep.get("main_insight") or _derive_insight(study_spec)
    caveat = rep.get("caveat") or ""
    lit_match = rep.get("lit_match") or ""
    key_metrics = rep.get("key_metrics") or _derive_key_metrics(study_spec)

    badge = _render_verdict_badge(verdict)
    metrics_html = _render_key_metrics(key_metrics)
    verdicts_html = _render_conclusion_verdicts(study_spec)
    synthesis_html = _render_conclusion_synthesis(study_spec)
    biology_html = _render_biological_summary(study_spec)
    alternatives_html = _render_alternatives(study_spec)
    viz_html = _render_viz_embeds(viz_entries)
    rigor_html = _render_rigor(study_spec, skeptic=skeptic)
    debts_html = _render_epistemic_debts(study_spec)          # W15
    audit_html = _render_audit_trail(study_spec) if skeptic else ""   # W24
    limitations_html = _render_limitations(study_spec) if skeptic else ""  # W24
    # W24 — in skeptic mode controls/falsifiability is its own ordered section
    # (the rigor scorecard omits its embedded copy in skeptic mode).
    controls_section_html = ""
    if skeptic:
        _cf = _render_controls_and_falsifiability(study_spec)
        if _cf:
            controls_section_html = (
                '<section id="rigor-detail"><h2>Controls &amp; falsifiability</h2>'
                + _cf + '</section>'
            )

    inv_chip = ""
    if investigation_slug:
        inv_chip = (
            f'<div class="inv-chip" style="color:#475569;font-size:0.9em;'
            f'margin-bottom:4px">Investigation: '
            f'<code>{_h(investigation_slug)}</code> · '
            f'<strong>focus study</strong></div>'
        )

    quality_bits = []
    if confidence:
        quality_bits.append(f'<span>Confidence: <strong>{_h(confidence)}</strong></span>')
    if evidence_quality:
        quality_bits.append(f'<span>Evidence: <strong>{_h(evidence_quality)}</strong></span>')
    if lit_match:
        quality_bits.append(f'<span>Literature match: <strong>{_h(lit_match)}</strong></span>')
    quality_html = (
        '<div class="quality" style="display:flex;gap:16px;color:#334155;'
        'font-size:0.92em;margin:8px 0">'
        + " · ".join(quality_bits)
        + '</div>'
    ) if quality_bits else ""

    head_blocks = []
    if objective:
        head_blocks.append(
            f'<div class="objective" style="margin:12px 0;font-size:1.05em">'
            f'<strong>Objective:</strong> {_multiline(objective)}</div>'
        )
    if conclusion:
        head_blocks.append(
            f'<div class="conclusion" style="margin:12px 0;padding:10px 14px;'
            f'background:#f8fafc;border-left:4px solid #0ea5e9;border-radius:4px">'
            f'<strong>Conclusion:</strong> {_multiline(conclusion)}</div>'
        )
    if main_insight:
        head_blocks.append(
            f'<div class="insight" style="margin:12px 0;padding:10px 14px;'
            f'background:#fefce8;border-left:4px solid #eab308;border-radius:4px">'
            f'<strong>Main insight:</strong> {_multiline(main_insight)}</div>'
        )
    if caveat:
        head_blocks.append(
            f'<div class="caveat" style="margin:12px 0;padding:10px 14px;'
            f'background:#fef2f2;border-left:4px solid #dc2626;border-radius:4px">'
            f'<strong>Caveat:</strong> {_multiline(caveat)}</div>'
        )

    # Build the section-nav jump chips. Each chip targets an in-page
    # anchor (#overview / #biology / #viz). Only show the chip when the
    # corresponding section will actually render — empty-section chips
    # are dead-ends that confuse navigation. Mirrors the same pattern
    # the investigation report's sticky panel uses (`sp-section-nav`)
    # so single-study + investigation reports feel consistent.
    def _chip(anchor: str, label: str) -> str:
        return f'<a href="#{anchor}">{_h(label)}</a>'

    # Sections that were historically always emitted (even when empty) so the
    # DOM is stable; their nav chips are still gated on real content.
    overview_section = (
        '<section class="overview" id="overview">\n'
        f'  {"".join(head_blocks)}\n'
        f'  {metrics_html}\n'
        '</section>'
    )
    biology_section = f'<section id="biology">\n{biology_html}\n</section>'
    viz_section = f'<section id="viz">\n{viz_html}\n</section>'

    if skeptic:
        # W24 skeptic order: audit trail → rigor (gap→warn→ok) → controls &
        # falsifiability → alternatives (not-excluded first, from the shared
        # renderer) → limitations/remaining-uncertainties → open epistemic
        # debts → THEN the usual verdicts / synthesis / biology / viz.
        seq = [
            ("overview", "Overview", overview_section, bool(head_blocks or metrics_html)),
            ("audit-trail", "Audit trail", audit_html, bool(audit_html)),
            ("rigor", "Rigor", rigor_html, bool(rigor_html)),
            ("rigor-detail", "Controls", controls_section_html, bool(controls_section_html)),
            ("alternatives", "Alternatives", alternatives_html, bool(alternatives_html)),
            ("limitations", "Limitations", limitations_html, bool(limitations_html)),
            ("epistemic-debts", "Open debts", debts_html, bool(debts_html)),
            ("verdicts", "Verdicts", verdicts_html, bool(verdicts_html)),
            ("synthesis", "Synthesis", synthesis_html, bool(synthesis_html)),
            ("biology", "Biology", biology_section, bool(biology_html)),
            ("viz", "Visualisations", viz_section, bool(viz_html)),
        ]
        body_main = "\n\n".join(html for (_a, _l, html, _show) in seq if html)
        nav_chips = [_chip(a, l) for (a, l, _html, show) in seq if show]
    else:
        nav_chips = []
        if head_blocks or metrics_html:
            nav_chips.append(_chip("overview", "Overview"))
        if verdicts_html:
            nav_chips.append(_chip("verdicts", "Verdicts"))
        if synthesis_html:
            nav_chips.append(_chip("synthesis", "Synthesis"))
        if biology_html:
            nav_chips.append(_chip("biology", "Biology"))
        if alternatives_html:
            nav_chips.append(_chip("alternatives", "Alternatives"))
        if viz_html:
            nav_chips.append(_chip("viz", "Visualisations"))
        # W15 — the open-debts panel renders right after rigor in normal mode.
        body_main = "\n\n".join([
            overview_section,
            verdicts_html,
            synthesis_html,
            biology_section,
            alternatives_html,
            rigor_html,
            debts_html,
            viz_section,
        ])
    nav_html = (
        '<nav class="ssr-section-nav">' + "".join(nav_chips) + '</nav>'
    ) if nav_chips else ""

    # Sticky strip rendered as a sibling of the header so it stays
    # pinned at the top of the viewport as the user scrolls past the
    # overview into long viz sections (the scroll problem the user hit
    # in 2026-05-25 viz-heavy reports). Layout: one line — title +
    # verdict-badge on the left, section-nav chips on the right.
    sticky_html = (
        '<div class="ssr-sticky-strip">'
        f'  <span class="ssr-sticky-title">{_h(title)}</span>'
        f'  {badge}'
        f'  {nav_html}'
        '</div>'
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{_h(title)} — single-study report</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
         color:#0f172a; max-width:980px; margin:0 auto; padding:0 32px 32px; line-height:1.5; }}
  h1 {{ margin:0 0 4px; font-size:1.9em }}
  h2 {{ border-bottom:1px solid #e2e8f0; padding-bottom:6px; margin-top:32px; font-size:1.35em }}
  h3 {{ font-size:1.05em }}
  code {{ background:#f1f5f9; padding:1px 6px; border-radius:4px; font-size:0.9em }}
  iframe {{ background:#fff }}
  /* Sticky strip — pinned at the top of the viewport once the user
     scrolls past the page header. Keeps the title + verdict + jump
     nav visible regardless of scroll depth. The negative left/right
     margin + page-width padding extends the strip's background
     edge-to-edge while the inner content respects the max-width body. */
  .ssr-sticky-strip {{
    position: sticky; top: 0; z-index: 50;
    margin: 0 -32px 0 -32px; padding: 10px 32px;
    background: rgba(248, 250, 252, 0.96);
    backdrop-filter: blur(4px);
    border-bottom: 1px solid #e2e8f0;
    display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
    font-size: 0.92em;
  }}
  .ssr-sticky-title {{ font-weight: 600; color: #0f172a;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    max-width: 48ch;
  }}
  .ssr-section-nav {{ display: inline-flex; gap: 4px; flex-wrap: wrap;
    margin-left: auto;
  }}
  .ssr-section-nav a {{
    color: #2563eb; text-decoration: none;
    padding: 3px 10px; border-radius: 9999px;
    background: rgba(37, 99, 235, 0.08);
    font-size: 0.9em;
  }}
  .ssr-section-nav a:hover {{ background: rgba(37, 99, 235, 0.16); }}
  /* Page sections under the sticky strip — scroll-margin-top so
     hash-anchors land below the sticky header, not under it. */
  section[id], h1, h2 {{ scroll-margin-top: 56px; }}
  body > header {{ padding-top: 32px; }}
  .footer {{ margin-top:48px; padding-top:16px; border-top:1px solid #e2e8f0;
            color:#64748b; font-size:0.85em }}
</style>
</head>
<body>
{sticky_html}

<header>
  {inv_chip}
  <h1>{_h(title)}</h1>
  <div class="meta" style="color:#475569;font-size:0.95em">
    <code>{_h(study_spec.get("name", ""))}</code> {badge}
  </div>
  {quality_html}
</header>

{body_main}

<div class="footer">
  Generated {_h(generated_at)} by vivarium-dashboard single-study report.
  This report intentionally omits investigation-level overview, comparative,
  and cross-study sections — see the full investigation report for those.
</div>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def render_single_study_report(
    ws_root: Path,
    study_slug: str,
    *,
    investigation_slug: Optional[str] = None,
    out_dir: Optional[Path] = None,
    skeptic: bool = False,
) -> Path:
    """Build a self-contained HTML report for ONE study.

    The output lives at ``<ws_root>/reports/single-study-<slug>.html`` by
    default; pass ``out_dir`` to override the parent directory.

    The renderer reads:
      - ``studies/<slug>/study.yaml`` (or legacy ``investigations/<slug>/spec.yaml``)
      - ``studies/<slug>/viz/*.html`` (inlined via iframe srcdoc)

    Raises FileNotFoundError when the study spec doesn't exist.
    """
    ws_root = Path(ws_root)
    study_spec = _load_study_spec(ws_root, study_slug)
    viz_entries = _collect_viz_html(ws_root, study_slug)
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    html = _render_html(
        study_spec, viz_entries,
        investigation_slug=investigation_slug,
        generated_at=generated_at,
        skeptic=skeptic,
    )

    out_dir = Path(out_dir) if out_dir is not None else WorkspacePaths.load(ws_root).reports
    out_dir.mkdir(parents=True, exist_ok=True)
    # The skeptic view is written to a distinct file so it never clobbers the
    # default report (W24).
    suffix = "-skeptic" if skeptic else ""
    out_path = out_dir / f"single-study-{study_slug}{suffix}.html"
    out_path.write_text(html, encoding="utf-8")
    return out_path


def build_single_study_report_for_test(
    ws_root: Path, body: dict,
) -> tuple[dict, int]:
    """Pure handler backing ``POST /api/study-report-single``.

    Body shape (accepts either, prefers ``study`` when both are set):
        {"study": "<slug>"}                  # explicit study override
        {"investigation": "<slug>"}          # resolves focus_study from yaml

    Returns ``({html_path, size_bytes, study, investigation?}, 200)`` on
    success; ``({error}, 4xx)`` on bad input / missing files.
    """
    body = body or {}
    study_slug = (body.get("study") or "").strip()
    inv_slug = (body.get("investigation") or "").strip()
    skeptic = bool(body.get("skeptic"))   # W24 — skeptical-reader view

    if not study_slug and not inv_slug:
        return {"error": "either 'study' or 'investigation' is required"}, 400

    if not study_slug:
        study_slug = resolve_focus_study(ws_root, inv_slug) or ""
        if not study_slug:
            return ({"error": f"investigation '{inv_slug}' has no focus_study "
                              f"(or investigation.yaml missing)"}, 404)

    try:
        out_path = render_single_study_report(
            ws_root, study_slug,
            investigation_slug=inv_slug or None,
            skeptic=skeptic,
        )
    except FileNotFoundError as e:
        return {"error": str(e)}, 404
    except ValueError as e:
        return {"error": str(e)}, 400

    rel = str(out_path.relative_to(Path(ws_root))) \
        if str(out_path).startswith(str(Path(ws_root))) else str(out_path)
    resp = {
        "html_path": rel,
        "size_bytes": out_path.stat().st_size,
        "study": study_slug,
        "skeptic": skeptic,
    }
    if inv_slug:
        resp["investigation"] = inv_slug
    return resp, 200
