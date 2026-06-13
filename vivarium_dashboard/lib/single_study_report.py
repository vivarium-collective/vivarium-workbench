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


# ---------------------------------------------------------------------------
# Workflow typing chips (Wave 3a — critiques #10 / #7 / #18)
# ---------------------------------------------------------------------------

# critique #10 — study_type enum (default unset → standard; `kind`/`study_kind`
# == "adversarial" stay valid aliases). Kept in sync with rigor._study_type and
# study-detail.js _studyType.
_STUDY_TYPES = (
    "exploratory", "confirmatory", "diagnostic", "adversarial", "standard",
)
_STUDY_TYPE_COLORS = {
    "exploratory":  ("#e0e7ff", "#3730a3"),
    "confirmatory": ("#dcfce7", "#166534"),
    "diagnostic":   ("#fef3c7", "#92400e"),
    "adversarial":  ("#fee2e2", "#991b1b"),
    "standard":     ("#f1f5f9", "#475569"),
}

# critique #7 — next_action_type enum (free-text next_action stays the rationale).
_NEXT_ACTION_TYPES = (
    "replicate", "calibrate", "ablate", "adversarially_probe",
    "refine_representation", "split_hypothesis", "retire_hypothesis",
    "escalate_model",
)


def _study_type(spec: dict) -> str:
    """Return the study's typed workflow role (critique #10).

    Reads ``study_type`` first, falls back to the legacy ``kind`` / ``study_kind``
    aliases, defaults to ``standard``. Mirrors ``rigor._study_type`` so the
    badge matches the rigor credit.
    """
    for key in ("study_type", "kind", "study_kind"):
        val = spec.get(key)
        if isinstance(val, str) and val.strip():
            v = val.strip().lower()
            if v in _STUDY_TYPES:
                return v
    return "standard"


def _render_study_type_badge(spec: dict) -> str:
    """Render the study_type pill for the report header. Omitted for the
    implicit ``standard`` default (only an explicit type is worth a chip)."""
    explicit = any(
        isinstance(spec.get(k), str) and spec.get(k).strip()
        for k in ("study_type", "kind", "study_kind")
    )
    st = _study_type(spec)
    if not explicit or st == "standard":
        return ""
    bg, fg = _STUDY_TYPE_COLORS.get(st, ("#f1f5f9", "#475569"))
    return (
        f'<span class="study-type-badge" title="study type (critique #10)" '
        f'style="display:inline-block;padding:2px 10px;border-radius:9999px;'
        f'font-weight:600;font-size:0.78em;background:{bg};color:{fg};'
        f'margin-left:6px;vertical-align:middle">{_h(st)}</span>'
    )


def _next_action_type_chip(finding: dict) -> str:
    """Render a finding's ``next_action_type`` pill (critique #7). Returns ''
    when absent; renders unknown values too (the linter flags them, the render
    stays faithful to the model)."""
    if not isinstance(finding, dict):
        return ""
    nat = finding.get("next_action_type")
    if not isinstance(nat, str) or not nat.strip():
        return ""
    v = nat.strip()
    known = v in _NEXT_ACTION_TYPES
    bg, fg = ("#dbeafe", "#1e40af") if known else ("#fef9c3", "#854d0e")
    return (
        f'<span class="next-action-type" title="next action type (critique #7)" '
        f'style="display:inline-block;padding:1px 8px;border-radius:9999px;'
        f'background:{bg};color:{fg};font-weight:600;font-size:0.72em;'
        f'margin-left:6px;vertical-align:middle">{_h(v)}</span>'
    )


def _preregistration_status(spec: dict) -> dict | None:
    """critique #18 — defensive bridge to
    ``study_verdict.preregistration_status``. Returns None when pbg-superpowers
    (or the function) isn't importable so the chip simply doesn't render."""
    try:
        from pbg_superpowers.study_verdict import preregistration_status
    except Exception:
        return None
    try:
        return preregistration_status(spec)
    except Exception:
        return None


def _render_preregistration_chip(spec: dict) -> str:
    """Render the "pre-registered ✓ / post-hoc ⚠" chip (critique #18) for the
    verdict area, driven by ``study_verdict.preregistration_status``. Omitted
    when no ``preregistered`` block is declared (or the bridge is unavailable)."""
    status = _preregistration_status(spec)
    if not isinstance(status, dict) or not status.get("preregistered"):
        return ""
    before = status.get("registered_before_run")
    if before is True:
        bg, fg, label, title = (
            "#dcfce7", "#166534", "pre-registered ✓",
            "criteria registered before the canonical run",
        )
    elif before is False:
        bg, fg, label, title = (
            "#fef3c7", "#92400e", "post-hoc ⚠",
            "criteria registered AFTER the run started",
        )
    else:
        bg, fg, label, title = (
            "#e2e8f0", "#475569", "pre-registered (timing unknown)",
            "registered_at or run start time missing — timing could not be checked",
        )
    cm = status.get("criteria_match")
    if cm is False:
        label += " · thresholds drifted"
        title += "; pre-registered thresholds differ from the current behavior tests"
    return (
        f'<span class="prereg-chip" title="{_h(title)}" '
        f'style="display:inline-block;padding:2px 10px;border-radius:9999px;'
        f'font-weight:600;font-size:0.78em;background:{bg};color:{fg};'
        f'margin-left:6px;vertical-align:middle">{_h(label)}</span>'
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
                    nat_chip = _next_action_type_chip(f)   # critique #7
                    wave3b = _finding_chips(spec, f)       # #21/#22/#25
                    derived.append(
                        f'<div class="narrative-row" style="margin:10px 0">'
                        f'<strong>Finding{(" " + _h(fid)) if fid else ""}:</strong> '
                        f'{_multiline(stmt)}{chip}{nat_chip}{wave3b}</div>'
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
        nat_chip = _next_action_type_chip(f)   # critique #7
        wave3b = _finding_chips(spec, f)       # #21/#22/#25
        claim_lis.append(f'<li>{_multiline(str(text))}{chip}{nat_chip}{wave3b}</li>')
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
# Wave 3b — per-finding claim_scope (#21) / generality (#22) / lifecycle (#25)
# chips, rendered beside each finding's tier/weight badges. All consume
# authored/computed fields; absent → no chip. Enums match the cross-repo
# contract exactly (kept in sync with static/walkthrough.js).
# ---------------------------------------------------------------------------

# critique #21 — claim_scope (DISTINCT from tier; the reach of the claim).
_CLAIM_SCOPES = (
    "local-implementation", "mechanism", "behavioral", "theoretical", "generality",
)
_CLAIM_SCOPE_COLORS = {
    "local-implementation": ("#f1f5f9", "#475569"),
    "mechanism":            ("#dbeafe", "#1e40af"),
    "behavioral":           ("#dcfce7", "#166534"),
    "theoretical":          ("#ede9fe", "#6d28d9"),
    "generality":           ("#fef9c3", "#854d0e"),
}


def _claim_scope_chip(finding: dict) -> str:
    """Render a finding's ``claim_scope`` pill (critique #21). '' when absent.
    Unknown values still render (faithful to the model; the linter flags them)."""
    if not isinstance(finding, dict):
        return ""
    cs = finding.get("claim_scope")
    if not isinstance(cs, str) or not cs.strip():
        return ""
    v = cs.strip()
    bg, fg = _CLAIM_SCOPE_COLORS.get(v, ("#fef9c3", "#854d0e"))
    return (
        f'<span class="claim-scope" title="claim scope (critique #21)" '
        f'style="display:inline-block;padding:1px 8px;border-radius:9999px;'
        f'background:{bg};color:{fg};font-weight:600;font-size:0.72em;'
        f'margin-left:6px;vertical-align:middle">scope: {_h(v)}</span>'
    )


# critique #22 — generality (axes tested + level).
_GENERALITY_AXES = (
    "parameter_regime", "initial_conditions", "discretization",
    "geometry", "alt_implementation", "independent_authoring",
)
_GENERALITY_LEVELS = ("instance_specific", "mechanism", "framework")
_GENERALITY_LEVEL_COLORS = {
    "instance_specific": ("#fee2e2", "#991b1b"),
    "mechanism":         ("#fef9c3", "#854d0e"),
    "framework":         ("#dcfce7", "#166534"),
}


def _generality_chip(finding: dict) -> str:
    """Render a finding's ``generality`` pill (critique #22): the level coloured
    by reach, with the tested axes in the tooltip. '' when absent."""
    if not isinstance(finding, dict):
        return ""
    g = finding.get("generality")
    if not isinstance(g, dict) or not g:
        return ""
    level = str(g.get("level") or "").strip()
    axes = g.get("axes_tested") or []
    if isinstance(axes, str):
        axes = [axes]
    axes = [str(a) for a in axes if a]
    if not level and not axes:
        return ""
    bg, fg = _GENERALITY_LEVEL_COLORS.get(level, ("#f1f5f9", "#475569"))
    label = "generality" + (f": {level}" if level else "")
    n = len(axes)
    if n:
        label += f" · {n} ax{'es' if n != 1 else 'is'}"
    title = "generality (critique #22) — axes tested: " + (", ".join(axes) or "none")
    return (
        f'<span class="generality" title="{_h(title)}" '
        f'style="display:inline-block;padding:1px 8px;border-radius:9999px;'
        f'background:{bg};color:{fg};font-weight:600;font-size:0.72em;'
        f'margin-left:6px;vertical-align:middle">{_h(label)}</span>'
    )


# critique #25 — lifecycle_state (DISTINCT from tier/claim-class).
_LIFECYCLE_STATES = (
    "observation", "candidate-explanation", "tested-vs-alternatives",
    "provisional-claim", "generalized", "retired", "superseded",
)
_LIFECYCLE_COLORS = {
    "observation":            ("#f1f5f9", "#475569"),
    "candidate-explanation":  ("#e0e7ff", "#3730a3"),
    "tested-vs-alternatives": ("#dbeafe", "#1e40af"),
    "provisional-claim":      ("#fef9c3", "#854d0e"),
    "generalized":            ("#dcfce7", "#166534"),
    "retired":                ("#fee2e2", "#991b1b"),
    "superseded":             ("#fee2e2", "#991b1b"),
}


def _lifecycle_floor(spec: dict, finding: dict) -> str | None:
    """critique #25 — defensive bridge to ``study_verdict.lifecycle_floor``.
    Returns None when pbg-superpowers (or the function) isn't importable, so the
    chip falls back to the authored value (or nothing)."""
    try:
        from pbg_superpowers.study_verdict import lifecycle_floor
    except Exception:
        return None
    try:
        val = lifecycle_floor(finding, spec)
    except Exception:
        return None
    return val if isinstance(val, str) and val.strip() else None


def _lifecycle_chip(spec: dict, finding: dict) -> str:
    """Render a finding's ``lifecycle_state`` pill (critique #25), beside the
    tier/weight badges. Shows the authored state when present; otherwise the
    DERIVED floor from ``study_verdict.lifecycle_floor`` (marked "floor"). '' when
    neither is available."""
    if not isinstance(finding, dict):
        return ""
    authored = finding.get("lifecycle_state")
    authored = authored.strip() if isinstance(authored, str) and authored.strip() else None
    floor = _lifecycle_floor(spec, finding)
    state = authored or floor
    if not state:
        return ""
    bg, fg = _LIFECYCLE_COLORS.get(state, ("#f1f5f9", "#475569"))
    derived = (authored is None) and bool(floor)
    label = state + (" · floor" if derived else "")
    title = "lifecycle state (critique #25)"
    if derived:
        title += " — derived floor (no authored state)"
    elif authored and floor and authored != floor:
        title += f" — authored '{authored}' (floor: {floor})"
    return (
        f'<span class="lifecycle-state" title="{_h(title)}" '
        f'style="display:inline-block;padding:1px 8px;border-radius:9999px;'
        f'background:{bg};color:{fg};font-weight:600;font-size:0.72em;'
        f'margin-left:6px;vertical-align:middle">{_h(label)}</span>'
    )


def _finding_chips(spec: dict, finding: dict) -> str:
    """The Wave 3b chip cluster appended after a finding's existing chips:
    claim_scope (#21) · generality (#22) · lifecycle_state (#25)."""
    return (
        _claim_scope_chip(finding)
        + _generality_chip(finding)
        + _lifecycle_chip(spec, finding)
    )


# ---------------------------------------------------------------------------
# Wave 3b — measurement integrity: threshold provenance + sensitivity (#9) and
# the per-metric calibration ladder (#20). Consumes behavior_tests[].pass_if
# .provenance, rigor.threshold_sensitivity (defensive), and calibration_ladder.
# ---------------------------------------------------------------------------

# critique #9 — threshold provenance.kind enum (DISTINCT from cites/anchor).
_THRESHOLD_PROVENANCE_KINDS = (
    "theory", "calibration", "literature", "expert", "exploratory", "post_hoc",
)
_THRESHOLD_PROV_COLORS = {
    "theory":      ("#dbeafe", "#1e40af"),
    "calibration": ("#dcfce7", "#166534"),
    "literature":  ("#e0e7ff", "#3730a3"),
    "expert":      ("#fef9c3", "#854d0e"),
    "exploratory": ("#f1f5f9", "#475569"),
    "post_hoc":    ("#fee2e2", "#991b1b"),
}


def _pass_if_text(p) -> str:
    """Render a pass_if band as compact text (mirrors walkthrough.js _passIfText)."""
    if not isinstance(p, dict):
        return str(p) if p is not None else ""
    op = str(p.get("op", "")).strip()
    low, high, val = p.get("low"), p.get("high"), p.get("value")
    if op == "in_range":
        return f"in [{low}, {high}]"
    if op == "at_least":
        return f"≥ {low if low is not None else val}"
    if op == "at_most":
        return f"≤ {high if high is not None else val}"
    if op == "equals":
        return f"= {val}"
    return op or ""


def _threshold_provenance_chip(prov: dict) -> str:
    """Render the provenance.kind pill (+ note in the tooltip) for a band."""
    if not isinstance(prov, dict):
        return ""
    kind = prov.get("kind")
    if not isinstance(kind, str) or not kind.strip():
        return ""
    v = kind.strip()
    bg, fg = _THRESHOLD_PROV_COLORS.get(v, ("#fef9c3", "#854d0e"))
    note = str(prov.get("note") or "").strip()
    title = "threshold provenance (critique #9)" + (f" — {note}" if note else "")
    return (
        f'<span class="threshold-provenance" title="{_h(title)}" '
        f'style="display:inline-block;padding:1px 8px;border-radius:9999px;'
        f'background:{bg};color:{fg};font-weight:600;font-size:0.72em;'
        f'margin-left:6px;vertical-align:middle">provenance: {_h(v)}</span>'
    )


def _threshold_sensitivity(spec: dict, test_name: str):
    """critique #9 — defensive bridge to ``rigor.threshold_sensitivity``.
    Returns a list of ``{cutoff, result}`` or None when unavailable/guarded."""
    try:
        from pbg_superpowers.rigor import threshold_sensitivity
    except Exception:
        return None
    try:
        return threshold_sensitivity(spec, test_name)
    except Exception:
        return None


def _render_threshold_sensitivity(rows) -> str:
    """Render the "passes across ±20%" mini-view from a threshold_sensitivity
    list of ``{cutoff, result}``. '' when empty/guarded."""
    if not isinstance(rows, (list, tuple)) or not rows:
        return ""
    cells = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        cutoff = r.get("cutoff")
        res = str(r.get("result", "")).upper()
        bg, fg = _TRACK_COLORS.get(res, ("#f1f5f9", "#475569"))
        glyph = "✓" if res == "PASS" else ("✗" if res == "FAIL" else "·")
        cells.append(
            f'<span style="display:inline-block;padding:1px 7px;border-radius:9999px;'
            f'background:{bg};color:{fg};font-size:0.72em;margin:1px">'
            f'{_h(str(cutoff))} {glyph}</span>'
        )
    if not cells:
        return ""
    return (
        '<div class="threshold-sensitivity" style="margin-top:3px">'
        '<span style="color:#475569;font-size:0.78em">sensitivity (passes across ±20%):</span> '
        + "".join(cells) + '</div>'
    )


def _render_calibration_ladder(spec: dict) -> str:
    """critique #20 — per-metric calibration-ladder table (fail / pass /
    borderline / stress rungs, each a controls[].name or "—" when unbuilt).
    '' when no ``calibration_ladder`` is declared."""
    ladders = [l for l in (spec.get("calibration_ladder") or []) if isinstance(l, dict)]
    if not ladders:
        return ""
    head = (
        '<tr style="text-align:left;color:#475569;font-size:0.82em">'
        '<th style="padding:4px 8px">Metric</th>'
        '<th style="padding:4px 8px">known_fail</th>'
        '<th style="padding:4px 8px">known_pass</th>'
        '<th style="padding:4px 8px">borderline</th>'
        '<th style="padding:4px 8px">stress</th>'
        '<th style="padding:4px 8px">rungs</th></tr>'
    )
    rows = []
    for l in ladders:
        rungs = [l.get("known_fail"), l.get("known_pass"),
                 l.get("borderline"), l.get("stress")]
        filled = sum(1 for r in rungs if r)
        if filled >= 3:
            bg, fg = "#dcfce7", "#166534"
        elif l.get("known_fail") and l.get("known_pass"):
            bg, fg = "#fef9c3", "#854d0e"
        else:
            bg, fg = "#fee2e2", "#991b1b"
        def _cell(v):
            return (f'<code style="font-size:0.82em">{_h(str(v))}</code>'
                    if v else '<span style="color:#cbd5e1">—</span>')
        rows.append(
            '<tr style="border-top:1px solid #f1f5f9;font-size:0.9em">'
            f'<td style="padding:4px 8px"><strong>{_h(str(l.get("metric", "")))}</strong></td>'
            f'<td style="padding:4px 8px">{_cell(l.get("known_fail"))}</td>'
            f'<td style="padding:4px 8px">{_cell(l.get("known_pass"))}</td>'
            f'<td style="padding:4px 8px">{_cell(l.get("borderline"))}</td>'
            f'<td style="padding:4px 8px">{_cell(l.get("stress"))}</td>'
            f'<td style="padding:4px 8px"><span style="padding:1px 8px;border-radius:9999px;'
            f'background:{bg};color:{fg};font-weight:600;font-size:0.82em">{filled}/4</span></td>'
            '</tr>'
        )
    return (
        '<div id="calibration-ladder" style="margin:10px 0">'
        '<strong style="color:#1e293b">Calibration ladder</strong>'
        '<p style="color:#475569;font-size:0.88em;margin:2px 0 4px">Per metric, which '
        'controls anchor the fail / pass / borderline / stress rungs (≥3 filled = '
        'calibrated; an empty rung is the gap to close).</p>'
        '<table style="border-collapse:collapse;width:100%">'
        + head + "".join(rows) + '</table></div>'
    )


def _render_measurement_integrity(spec: dict) -> str:
    """Wave 3b — "Measurement integrity" section: per-band threshold provenance
    + sensitivity (#9) and the per-metric calibration ladder (#20). Omitted when
    neither a band carries provenance/sensitivity nor a calibration_ladder is
    declared."""
    tests = [t for t in (spec.get("behavior_tests") or spec.get("expected_behavior") or [])
             if isinstance(t, dict)]
    band_rows = []
    for t in tests:
        pass_if = t.get("pass_if") if isinstance(t.get("pass_if"), dict) else {}
        prov = pass_if.get("provenance") if isinstance(pass_if, dict) else None
        prov_chip = _threshold_provenance_chip(prov) if isinstance(prov, dict) else ""
        sens = _render_threshold_sensitivity(
            _threshold_sensitivity(spec, t.get("name", "")))
        if not prov_chip and not sens:
            continue
        name = t.get("name") or "(unnamed)"
        band = _pass_if_text(pass_if)
        note = ""
        if isinstance(prov, dict) and str(prov.get("note") or "").strip():
            note = (f'<div style="color:#475569;font-size:0.82em;margin-top:1px">'
                    f'{_h(str(prov["note"]).strip())}</div>')
        band_rows.append(
            '<div style="padding:7px 0;border-top:1px solid #f1f5f9">'
            f'<code style="font-size:0.85em">{_h(name)}</code>'
            + (f' <span style="color:#475569;font-size:0.85em">passes if '
               f'<strong>{_h(band)}</strong></span>' if band else "")
            + prov_chip + note + sens
            + '</div>'
        )
    ladder_html = _render_calibration_ladder(spec)
    if not band_rows and not ladder_html:
        return ""
    bands_block = ""
    if band_rows:
        bands_block = (
            '<div style="margin:6px 0"><strong style="color:#1e293b">Threshold provenance '
            '&amp; sensitivity</strong>' + "".join(band_rows) + '</div>'
        )
    return (
        '<section id="measurement-integrity"><h2>Measurement integrity</h2>'
        '<p style="color:#475569;font-size:0.92em;margin:0 0 8px">Where each pass/fail '
        'threshold comes from (theory / calibration / literature / expert / exploratory / '
        'post-hoc), how robust the verdict is to moving the cutoff ±20%, and which controls '
        'anchor each metric\'s calibration ladder.</p>'
        + bands_block + ladder_html +
        '</section>'
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


# ---------------------------------------------------------------------------
# Wave 2 — compositional causal discovery + semantic closure renderers.
# All consume data the model WRITES into study.yaml (composition_commitment,
# invariant_check, ablations, model_representation). Each renders defensively:
# a missing/empty field returns '' so the section is omitted entirely.
# ---------------------------------------------------------------------------

def _chip_list(items, *, bg: str = "#f1f5f9", fg: str = "#0f172a") -> str:
    """Render a list of strings as inline pill chips. Returns '' when empty."""
    chips = []
    for it in items or []:
        if it is None or it == "":
            continue
        chips.append(
            f'<span style="display:inline-block;padding:2px 9px;border-radius:9999px;'
            f'background:{bg};color:{fg};margin:2px;font-size:0.82em">{_h(str(it))}</span>'
        )
    return "".join(chips)


def _render_composition_commitment(spec: dict) -> str:
    """C-COMMIT — "Theoretical commitment" panel.

    Sourced from the authored ``composition_commitment`` block: what process(es)
    this study adds vs its prerequisite, the deficit that closes (+ closure-gap
    chips), the new behavior it unlocks, the invariants it must preserve (links to
    earlier studies), and the alternatives it excludes. Omitted when absent.
    """
    cc = spec.get("composition_commitment")
    if not isinstance(cc, dict) or not cc:
        return ""
    rows = []

    added = cc.get("component_added") or []
    if isinstance(added, str):
        added = [added]
    if added:
        rows.append(
            '<div style="margin:8px 0"><strong style="color:#1e293b">Component added</strong> '
            f'{_chip_list(added, bg="#e0e7ff", fg="#3730a3")}</div>'
        )

    deficit = cc.get("deficit_addressed")
    if isinstance(deficit, dict) and deficit:
        note = deficit.get("note") or ""
        gap_items = deficit.get("closure_gap_item") or []
        if isinstance(gap_items, str):
            gap_items = [gap_items]
        gap_html = (
            ' <span style="color:#475569;font-size:0.85em">closes:</span> '
            + _chip_list(gap_items, bg="#fee2e2", fg="#991b1b")
        ) if gap_items else ""
        if note or gap_html:
            rows.append(
                '<div style="margin:8px 0"><strong style="color:#1e293b">Deficit addressed</strong> '
                f'{_multiline(str(note)) if note else ""}{gap_html}</div>'
            )
    elif isinstance(deficit, str) and deficit:
        rows.append(
            '<div style="margin:8px 0"><strong style="color:#1e293b">Deficit addressed</strong> '
            f'{_multiline(deficit)}</div>'
        )

    new_behavior = cc.get("new_behavior") or []
    if isinstance(new_behavior, str):
        new_behavior = [new_behavior]
    if new_behavior:
        rows.append(
            '<div style="margin:8px 0"><strong style="color:#1e293b">New behavior</strong> '
            f'{_chip_list(new_behavior, bg="#dcfce7", fg="#166534")}</div>'
        )

    invariants = cc.get("invariants_required") or []
    inv_bits = []
    for iv in invariants:
        if isinstance(iv, dict):
            study = iv.get("study") or ""
            test = iv.get("test") or ""
            txt = study + ((" · " + test) if test else "")
        else:
            txt = str(iv)
        if txt:
            inv_bits.append(
                f'<li><code>{_h(txt)}</code></li>'
            )
    if inv_bits:
        rows.append(
            '<div style="margin:8px 0"><strong style="color:#1e293b">Invariants required</strong>'
            '<ul style="margin:4px 0 0;padding-left:20px;color:#334155;font-size:0.92em">'
            + "".join(inv_bits) + '</ul></div>'
        )

    excluded = cc.get("alternatives_excluded") or []
    if isinstance(excluded, str):
        excluded = [excluded]
    if excluded:
        rows.append(
            '<div style="margin:8px 0"><strong style="color:#1e293b">Alternatives excluded</strong> '
            f'{_chip_list(excluded, bg="#fef9c3", fg="#854d0e")}</div>'
        )

    if not rows:
        return ""
    return (
        '<section id="commitment"><h2>Theoretical commitment</h2>'
        '<p style="color:#475569;font-size:0.92em;margin:0 0 8px">What this study adds to its '
        'prerequisite — the component introduced, the deficit it closes, the new behavior it '
        'unlocks, the earlier invariants it must preserve, and the alternatives it excludes.</p>'
        + "".join(rows) +
        '</section>'
    )


_INVAR_STATUS_COLORS = {
    "invalidated":  ("#fee2e2", "#991b1b"),
    "weakened":     ("#fef9c3", "#854d0e"),
    "preserved":    ("#dcfce7", "#166534"),
    "strengthened": ("#dbeafe", "#1e40af"),
}
# Order so gap statuses (invalidated/weakened) render first.
_INVAR_STATUS_RANK = {"invalidated": 0, "weakened": 1, "preserved": 2, "strengthened": 3}


def _render_invariant_checks(spec: dict) -> str:
    """C-INVAR render — "Invariant checks" sub-section.

    Renders ``study.invariant_check[]`` (study · test · prior→now · status chip),
    invalidated/weakened first. Omitted when absent.
    """
    checks = [c for c in (spec.get("invariant_check") or []) if isinstance(c, dict)]
    if not checks:
        return ""
    checks = sorted(checks, key=lambda c: _INVAR_STATUS_RANK.get(
        str(c.get("status", "")).lower(), 9))
    head = (
        '<tr style="text-align:left;color:#475569;font-size:0.82em">'
        '<th style="padding:4px 8px">Study</th><th style="padding:4px 8px">Test</th>'
        '<th style="padding:4px 8px">Prior</th><th style="padding:4px 8px">Now</th>'
        '<th style="padding:4px 8px">Status</th></tr>'
    )
    rows = []
    for c in checks:
        status = str(c.get("status", "")).lower()
        bg, fg = _INVAR_STATUS_COLORS.get(status, ("#f1f5f9", "#475569"))
        chip = (
            f'<span style="padding:1px 8px;border-radius:9999px;background:{bg};color:{fg};'
            f'font-weight:600;font-size:0.82em">{_h(status or "—")}</span>'
        )
        rows.append(
            '<tr style="border-top:1px solid #f1f5f9;font-size:0.9em">'
            f'<td style="padding:4px 8px"><code>{_h(c.get("study", ""))}</code></td>'
            f'<td style="padding:4px 8px">{_h(c.get("test", ""))}</td>'
            f'<td style="padding:4px 8px">{_h(c.get("prior", ""))}</td>'
            f'<td style="padding:4px 8px">{_h(c.get("now", ""))}</td>'
            f'<td style="padding:4px 8px">{chip}</td>'
            '</tr>'
        )
    return (
        '<section id="invariants"><h2>Invariant checks</h2>'
        '<p style="color:#475569;font-size:0.92em;margin:0 0 8px">Earlier guarantees re-checked in '
        'the current code state — each invariant required by this study, its prior vs current value, '
        'and whether it was preserved. Invalidated / weakened invariants are listed first.</p>'
        '<table style="border-collapse:collapse;width:100%">'
        + head + "".join(rows) + '</table>'
        '</section>'
    )


def _render_causal_necessity(spec: dict) -> str:
    """C-CF — "Causal necessity" table from ``study.ablations[]``.

    The causal READ of the ablation suite: per process/target · mode ·
    behavior_test · baseline→ablated · role · causally necessary. Omitted when
    absent.
    """
    ablations = [a for a in (spec.get("ablations") or []) if isinstance(a, dict)]
    if not ablations:
        return ""
    head = (
        '<tr style="text-align:left;color:#475569;font-size:0.82em">'
        '<th style="padding:4px 8px">Process / target</th><th style="padding:4px 8px">Mode</th>'
        '<th style="padding:4px 8px">Behavior test</th><th style="padding:4px 8px">Baseline → ablated</th>'
        '<th style="padding:4px 8px">Role</th><th style="padding:4px 8px">Necessary</th></tr>'
    )
    role_colors = {
        "necessary":  ("#fee2e2", "#991b1b"),
        "modulatory": ("#fef9c3", "#854d0e"),
        "redundant":  ("#f1f5f9", "#475569"),
    }
    rows = []
    for a in ablations:
        target = a.get("target")
        if isinstance(target, (list, tuple)):
            target = ".".join(str(t) for t in target)
        proc = a.get("process", "")
        proc_target = _h(str(proc)) + (f' <code style="font-size:0.82em">{_h(str(target))}</code>'
                                       if target else "")
        role = str(a.get("role", "")).lower()
        rbg, rfg = role_colors.get(role, ("#f1f5f9", "#475569"))
        role_html = (
            f'<span style="padding:1px 8px;border-radius:9999px;background:{rbg};color:{rfg};'
            f'font-weight:600;font-size:0.82em">{_h(role or "—")}</span>'
        )
        nec = a.get("causally_necessary")
        nec_html = ("✓" if nec is True else ("✗" if nec is False else "—"))
        baseline = a.get("baseline_result")
        ablated = a.get("ablated_result")
        rows.append(
            '<tr style="border-top:1px solid #f1f5f9;font-size:0.9em">'
            f'<td style="padding:4px 8px">{proc_target}</td>'
            f'<td style="padding:4px 8px"><code>{_h(a.get("mode", ""))}</code></td>'
            f'<td style="padding:4px 8px">{_h(a.get("behavior_test", ""))}</td>'
            f'<td style="padding:4px 8px">{_h(str(baseline))} → {_h(str(ablated))}</td>'
            f'<td style="padding:4px 8px">{role_html}</td>'
            f'<td style="padding:4px 8px;text-align:center;font-weight:700">{nec_html}</td>'
            '</tr>'
        )
    return (
        '<section id="causal-necessity"><h2>Causal necessity</h2>'
        '<p style="color:#475569;font-size:0.92em;margin:0 0 8px">Counterfactual read of the ablation '
        'suite — each process/store removed or perturbed, whether a behavior test flipped, and so '
        'whether that component is causally necessary for the behavior (vs redundant or merely '
        'modulatory).</p>'
        '<table style="border-collapse:collapse;width:100%">'
        + head + "".join(rows) + '</table>'
        '</section>'
    )


def _wiring_summary(wiring) -> str:
    """Render an inputs/outputs wiring map ({port: [store_path]}) as port→store."""
    if not isinstance(wiring, dict) or not wiring:
        return '<span style="color:#94a3b8">—</span>'
    bits = []
    for port, path in wiring.items():
        if isinstance(path, (list, tuple)):
            tgt = ".".join(str(p) for p in path)
        else:
            tgt = str(path)
        bits.append(
            f'<code style="font-size:0.82em">{_h(str(port))}'
            f'<span style="color:#94a3b8">→</span>{_h(tgt)}</code>'
        )
    return " ".join(bits)


def _render_model_card(composite_doc: Optional[dict],
                       *, model_representation: Optional[dict] = None,
                       readouts: Optional[list] = None,
                       behavior_tests: Optional[list] = None,
                       variants: Optional[list] = None,
                       interventions: Optional[list] = None) -> str:
    """C-MODELCARD — static, reader-independent model card.

    Built from the (light) composite-state doc — the same ``summarize_large_values``
    + ``process_docs`` doc the explorer uses, NOT the heavy raw composite. Renders:
    per process (address · inputs port→store · outputs · config); stores + initial
    values; boundary (from ``model_representation``); observables (readouts /
    behavior-test measures); perturbations (interventions / variants).

    Rendered server-side so it survives the static read-only bundle. Returns ''
    when no composite doc is available.
    """
    if not isinstance(composite_doc, dict) or not composite_doc:
        return ""
    # The doc may be wrapped as {"state": {...}} or be the bare state mapping.
    state = composite_doc.get("state") if isinstance(composite_doc.get("state"), dict) else composite_doc

    processes = []
    stores = []
    for key, node in state.items():
        if isinstance(node, dict) and node.get("_type") in ("process", "step"):
            processes.append((key, node))
        elif key not in ("_type",) and not (isinstance(node, dict) and node.get("_type")):
            # Treat non-process top-level entries as stores (scalars / containers).
            stores.append((key, node))

    sections = []

    if processes:
        prows = []
        for name, node in processes:
            addr = node.get("address", "")
            cfg = node.get("config") or {}
            cfg_html = ""
            if isinstance(cfg, dict) and cfg:
                cfg_html = (
                    '<div style="color:#475569;font-size:0.85em;margin-top:2px">config: '
                    + _chip_list([f"{k}={v}" for k, v in cfg.items()]) + '</div>'
                )
            desc = node.get("doc") or ""
            desc_html = (
                f'<div style="color:#64748b;font-size:0.85em;margin-top:2px">{_h(desc[:300])}</div>'
                if desc else ""
            )
            prows.append(
                '<div style="padding:8px 0;border-top:1px solid #f1f5f9">'
                f'<div><strong style="color:#1e293b">{_h(name)}</strong> '
                f'<code style="font-size:0.82em">{_h(addr)}</code></div>'
                f'<div style="font-size:0.88em;margin-top:2px"><span style="color:#475569">in:</span> '
                f'{_wiring_summary(node.get("inputs"))}</div>'
                f'<div style="font-size:0.88em;margin-top:2px"><span style="color:#475569">out:</span> '
                f'{_wiring_summary(node.get("outputs"))}</div>'
                f'{cfg_html}{desc_html}'
                '</div>'
            )
        sections.append(
            '<div style="margin:10px 0"><strong style="color:#1e293b">Processes</strong>'
            + "".join(prows) + '</div>'
        )

    if stores:
        boundary = set()
        if isinstance(model_representation, dict):
            for b in (model_representation.get("boundary") or []):
                boundary.add(str(b))
            for b in (model_representation.get("requires") or []):
                boundary.add(str(b))
        srows = []
        for name, val in stores:
            val_disp = val
            if isinstance(val, (dict, list)):
                val_disp = f"⟨{type(val).__name__}⟩"
            badge = (
                ' <span style="padding:0 6px;border-radius:9999px;background:#dbeafe;color:#1e40af;'
                'font-size:0.72em">boundary</span>' if name in boundary else ""
            )
            srows.append(
                '<tr style="border-top:1px solid #f1f5f9;font-size:0.9em">'
                f'<td style="padding:4px 8px"><code>{_h(name)}</code>{badge}</td>'
                f'<td style="padding:4px 8px">{_h(str(val_disp))}</td></tr>'
            )
        sections.append(
            '<div style="margin:10px 0"><strong style="color:#1e293b">Stores &amp; initial values</strong>'
            '<table style="border-collapse:collapse;width:100%;margin-top:4px">'
            '<tr style="text-align:left;color:#475569;font-size:0.82em">'
            '<th style="padding:4px 8px">Store</th><th style="padding:4px 8px">Initial</th></tr>'
            + "".join(srows) + '</table></div>'
        )

    # Observables — readouts + behavior-test measures.
    obs_bits = []
    for r in (readouts or []):
        if isinstance(r, dict):
            nm = r.get("name") or r.get("store_path") or ""
            if nm:
                obs_bits.append(str(nm))
        elif r:
            obs_bits.append(str(r))
    for bt in (behavior_tests or []):
        if isinstance(bt, dict):
            measure = bt.get("measure")
            if isinstance(measure, dict):
                m = measure.get("path") or measure.get("field") or measure.get("kind")
                if m:
                    obs_bits.append(str(m))
    if obs_bits:
        sections.append(
            '<div style="margin:10px 0"><strong style="color:#1e293b">Observables</strong> '
            + _chip_list(obs_bits) + '</div>'
        )

    # Perturbations — interventions + variants.
    pert_bits = []
    for v in (variants or []):
        if isinstance(v, dict):
            nm = v.get("name") or ""
            if nm:
                pert_bits.append(str(nm))
        elif v:
            pert_bits.append(str(v))
    for iv in (interventions or []):
        if isinstance(iv, dict):
            nm = iv.get("name") or iv.get("mode") or ""
            if nm:
                pert_bits.append(str(nm))
        elif iv:
            pert_bits.append(str(iv))
    if pert_bits:
        sections.append(
            '<div style="margin:10px 0"><strong style="color:#1e293b">Perturbations</strong> '
            + _chip_list(pert_bits, bg="#fef9c3", fg="#854d0e") + '</div>'
        )

    if not sections:
        return ""
    return (
        '<section id="model-card"><h2>Model card</h2>'
        '<p style="color:#475569;font-size:0.92em;margin:0 0 8px">A static, reader-independent '
        'description of the model — its processes, wiring, stores, observables, and perturbations — '
        'rendered from the composite state so it reads the same for everyone.</p>'
        + "".join(sections) +
        '</section>'
    )


_REPR_ROLE_COLORS = {
    "inside":            ("#f1f5f9", "#475569"),
    "boundary-crossing": ("#dbeafe", "#1e40af"),
    "derived":           ("#ede9fe", "#6d28d9"),
    "self-produced":     ("#dcfce7", "#166534"),
}


def _render_representation(model_representation: Optional[dict]) -> str:
    """C-MODELCARD — "Representation claims" table.

    Labels each store inside / boundary-crossing / derived / self-produced (from
    the persisted ``model_representation``) and reports interface-vs-semantic
    closure status. Omitted when absent.
    """
    mr = model_representation
    if not isinstance(mr, dict) or not mr:
        return ""

    # Classify each store by priority. The model writes the category lists; we
    # render a row per store with its highest-priority label.
    categories = [
        ("self-produced", mr.get("self_produced")),
        ("derived", mr.get("derived")),
        ("boundary-crossing", mr.get("boundary")),
        ("boundary-crossing", mr.get("requires")),
        ("inside", mr.get("provides")),
        ("inside", mr.get("inside")),
    ]
    store_role: dict[str, str] = {}
    for role, lst in categories:
        if isinstance(lst, str):
            lst = [lst]
        for s in (lst or []):
            store_role.setdefault(str(s), role)

    gap = mr.get("gap") or []
    if isinstance(gap, str):
        gap = [gap]
    gap_set = {str(g) for g in gap}

    rows = []
    for store, role in sorted(store_role.items()):
        bg, fg = _REPR_ROLE_COLORS.get(role, ("#f1f5f9", "#475569"))
        gap_badge = (
            ' <span style="padding:0 6px;border-radius:9999px;background:#fee2e2;color:#991b1b;'
            'font-size:0.72em">unclosed gap</span>' if store in gap_set else ""
        )
        rows.append(
            '<tr style="border-top:1px solid #f1f5f9;font-size:0.9em">'
            f'<td style="padding:4px 8px"><code>{_h(store)}</code>{gap_badge}</td>'
            f'<td style="padding:4px 8px"><span style="padding:1px 8px;border-radius:9999px;'
            f'background:{bg};color:{fg};font-weight:600;font-size:0.82em">{_h(role)}</span></td>'
            '</tr>'
        )

    table_html = ""
    if rows:
        table_html = (
            '<table style="border-collapse:collapse;width:100%;margin-top:4px">'
            '<tr style="text-align:left;color:#475569;font-size:0.82em">'
            '<th style="padding:4px 8px">Store</th><th style="padding:4px 8px">Representation</th></tr>'
            + "".join(rows) + '</table>'
        )

    # Closure status strip: interface vs semantic.
    def _closure_chip(label: str, closed) -> str:
        if closed is True:
            bg, fg, txt = "#dcfce7", "#166534", "CLOSED"
        elif closed is False:
            bg, fg, txt = "#fee2e2", "#991b1b", "OPEN"
        else:
            bg, fg, txt = "#f1f5f9", "#475569", "—"
        return (
            f'<span style="margin-right:12px">{_h(label)}: '
            f'<span style="padding:1px 8px;border-radius:9999px;background:{bg};color:{fg};'
            f'font-weight:700;font-size:0.82em">{txt}</span></span>'
        )

    semantic = mr.get("semantic") if isinstance(mr.get("semantic"), dict) else {}
    interface_closed = mr.get("interface_closed")
    semantically_closed = semantic.get("semantically_closed")
    closure_html = (
        '<div style="margin:10px 0">'
        + _closure_chip("Interface closure", interface_closed)
        + _closure_chip("Semantic closure", semantically_closed)
        + '</div>'
    )

    if not rows and interface_closed is None and semantically_closed is None:
        return ""
    return (
        '<section id="representation"><h2>Representation claims</h2>'
        '<p style="color:#475569;font-size:0.92em;margin:0 0 8px">How each store is represented '
        '(inside / boundary-crossing / derived / self-produced) and whether the model achieves '
        'interface closure (no missing inputs) and semantic closure (every self-produced store '
        'actually fluxes).</p>'
        + closure_html + table_html +
        '</section>'
    )


def _resolve_composite_doc(ws_root: Path, spec: dict) -> Optional[dict]:
    """Best-effort resolve the study's baseline composite to a LIGHT state doc.

    Mirrors the server's ``_get_composite_state`` resolution (generator registry
    → workspace file) but standalone, so the single-study report renders the
    model card from the same ``summarize_large_values`` + ``process_docs`` doc the
    explorer uses. Returns None on any failure (network-free / import-light:
    degrades to no model card).
    """
    # Find the baseline composite ref (v4 conditions.baseline.composite or the
    # legacy top-level baseline[].composite).
    ref = None
    conds = spec.get("conditions")
    if isinstance(conds, dict):
        bl = conds.get("baseline")
        if isinstance(bl, dict):
            ref = bl.get("composite")
    if not ref:
        for b in (spec.get("baseline") or []):
            if isinstance(b, dict) and b.get("composite"):
                ref = b["composite"]
                break
    if not ref:
        return None

    doc = None
    # 1) generator registry (built composites)
    try:
        from pbg_superpowers.composite_generator import (
            _REGISTRY, build_generator, discover_generators,
        )
        if not _REGISTRY:
            discover_generators()
        entry = _REGISTRY.get(ref)
        if entry is not None:
            doc = build_generator(entry)
    except Exception:
        doc = None

    # 2) workspace file (dotted spec id or relative path)
    if doc is None:
        try:
            from vivarium_dashboard.lib.composite_lookup import find_composite_path
            ws_data = yaml.safe_load(
                (Path(ws_root) / "workspace.yaml").read_text(encoding="utf-8")) or {}
            pkg = ws_data.get("package_path") or (
                "pbg_" + str(ws_data.get("name", "")).replace("-", "_"))
            found = find_composite_path(Path(ws_root), pkg, ref)
            if found is None:
                cand = Path(ws_root) / ref
                found = cand if cand.is_file() else None
            if found is not None and found.is_file():
                text = found.read_text(encoding="utf-8")
                doc = (json.loads(text) if found.suffix.lower() == ".json"
                       else (yaml.safe_load(text) or {}))
        except Exception:
            doc = None

    if not isinstance(doc, dict) or not doc:
        return None
    try:
        from vivarium_dashboard.lib.process_docs import (
            attach_process_docs, summarize_large_values,
        )
        doc = summarize_large_values(doc)
        attach_process_docs(doc)
    except Exception:
        pass
    # A composite file usually nests the wiring under a `state:` key; the
    # generator path returns the bare state. _render_model_card handles both.
    return doc.get("state") if isinstance(doc.get("state"), dict) else doc


def _render_html(study_spec: dict, viz_entries: list[dict],
                 *, investigation_slug: Optional[str], generated_at: str,
                 composite_doc: Optional[dict] = None,
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
    study_type_badge = _render_study_type_badge(study_spec)   # critique #10
    prereg_chip = _render_preregistration_chip(study_spec)    # critique #18
    metrics_html = _render_key_metrics(key_metrics)
    verdicts_html = _render_conclusion_verdicts(study_spec)
    synthesis_html = _render_conclusion_synthesis(study_spec)
    biology_html = _render_biological_summary(study_spec)
    alternatives_html = _render_alternatives(study_spec)
    viz_html = _render_viz_embeds(viz_entries)
    rigor_html = _render_rigor(study_spec, skeptic=skeptic)
    measurement_html = _render_measurement_integrity(study_spec)   # Wave 3b #9/#20
    debts_html = _render_epistemic_debts(study_spec)          # W15
    # Wave 2 — compositional causal discovery + semantic closure.
    commitment_html = _render_composition_commitment(study_spec)   # C-COMMIT
    invariants_html = _render_invariant_checks(study_spec)         # C-INVAR
    causal_html = _render_causal_necessity(study_spec)             # C-CF
    model_card_html = _render_model_card(                          # C-MODELCARD
        composite_doc,
        model_representation=study_spec.get("model_representation"),
        readouts=study_spec.get("readouts"),
        behavior_tests=study_spec.get("behavior_tests"),
        variants=study_spec.get("variants"),
        interventions=study_spec.get("interventions"),
    )
    representation_html = _render_representation(                  # C-MODELCARD
        study_spec.get("model_representation"))
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
            ("commitment", "Commitment", commitment_html, bool(commitment_html)),
            ("invariants", "Invariants", invariants_html, bool(invariants_html)),
            ("audit-trail", "Audit trail", audit_html, bool(audit_html)),
            ("rigor", "Rigor", rigor_html, bool(rigor_html)),
            ("measurement-integrity", "Measurement integrity", measurement_html, bool(measurement_html)),
            ("rigor-detail", "Controls", controls_section_html, bool(controls_section_html)),
            ("causal-necessity", "Causal necessity", causal_html, bool(causal_html)),
            ("alternatives", "Alternatives", alternatives_html, bool(alternatives_html)),
            ("limitations", "Limitations", limitations_html, bool(limitations_html)),
            ("epistemic-debts", "Open debts", debts_html, bool(debts_html)),
            ("verdicts", "Verdicts", verdicts_html, bool(verdicts_html)),
            ("synthesis", "Synthesis", synthesis_html, bool(synthesis_html)),
            ("biology", "Biology", biology_section, bool(biology_html)),
            ("model-card", "Model card", model_card_html, bool(model_card_html)),
            ("representation", "Representation", representation_html, bool(representation_html)),
            ("viz", "Visualisations", viz_section, bool(viz_html)),
        ]
        body_main = "\n\n".join(html for (_a, _l, html, _show) in seq if html)
        nav_chips = [_chip(a, l) for (a, l, _html, show) in seq if show]
    else:
        nav_chips = []
        if head_blocks or metrics_html:
            nav_chips.append(_chip("overview", "Overview"))
        if commitment_html:
            nav_chips.append(_chip("commitment", "Commitment"))
        if invariants_html:
            nav_chips.append(_chip("invariants", "Invariants"))
        if verdicts_html:
            nav_chips.append(_chip("verdicts", "Verdicts"))
        if synthesis_html:
            nav_chips.append(_chip("synthesis", "Synthesis"))
        if biology_html:
            nav_chips.append(_chip("biology", "Biology"))
        if alternatives_html:
            nav_chips.append(_chip("alternatives", "Alternatives"))
        if causal_html:
            nav_chips.append(_chip("causal-necessity", "Causal necessity"))
        if model_card_html:
            nav_chips.append(_chip("model-card", "Model card"))
        if representation_html:
            nav_chips.append(_chip("representation", "Representation"))
        if measurement_html:
            nav_chips.append(_chip("measurement-integrity", "Measurement integrity"))
        if viz_html:
            nav_chips.append(_chip("viz", "Visualisations"))
        # W15 — the open-debts panel renders right after rigor in normal mode.
        # Wave 2 — commitment + invariants lead the framing; the causal-necessity
        # table sits in the evidence area (after rigor); model card + representation
        # render with the build/model detail near the end.
        body_main = "\n\n".join([
            overview_section,
            commitment_html,
            invariants_html,
            verdicts_html,
            synthesis_html,
            biology_section,
            alternatives_html,
            rigor_html,
            measurement_html,
            causal_html,
            debts_html,
            model_card_html,
            representation_html,
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
    <code>{_h(study_spec.get("name", ""))}</code> {badge}{study_type_badge}{prereg_chip}
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
    # C-MODELCARD — resolve the baseline composite to a light state doc so the
    # model card renders from the same doc the explorer uses. Best-effort; the
    # card is omitted when resolution fails (no composite / import-light env).
    composite_doc = _resolve_composite_doc(ws_root, study_spec)
    html = _render_html(
        study_spec, viz_entries,
        investigation_slug=investigation_slug,
        generated_at=generated_at,
        composite_doc=composite_doc,
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
