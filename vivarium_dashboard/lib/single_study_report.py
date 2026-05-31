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


def _render_html(study_spec: dict, viz_entries: list[dict],
                 *, investigation_slug: Optional[str], generated_at: str) -> str:
    rep = study_spec.get("report") or {}
    title = rep.get("title") or study_spec.get("name", "study")
    verdict = rep.get("verdict") or ""
    confidence = rep.get("confidence") or ""
    evidence_quality = rep.get("evidence_quality") or ""
    objective = rep.get("objective") or ""
    conclusion = rep.get("conclusion") or ""
    main_insight = rep.get("main_insight") or ""
    caveat = rep.get("caveat") or ""
    lit_match = rep.get("lit_match") or ""
    key_metrics = rep.get("key_metrics") or []

    badge = _render_verdict_badge(verdict)
    metrics_html = _render_key_metrics(key_metrics)
    biology_html = _render_biological_summary(study_spec)
    viz_html = _render_viz_embeds(viz_entries)

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
    nav_chips = []
    if head_blocks or metrics_html:
        nav_chips.append('<a href="#overview">Overview</a>')
    if biology_html:
        nav_chips.append('<a href="#biology">Biology</a>')
    if viz_html:
        nav_chips.append('<a href="#viz">Visualisations</a>')
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

<section class="overview" id="overview">
  {"".join(head_blocks)}
  {metrics_html}
</section>

<section id="biology">
{biology_html}
</section>

<section id="viz">
{viz_html}
</section>

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
    )

    out_dir = Path(out_dir) if out_dir is not None else WorkspacePaths.load(ws_root).reports
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"single-study-{study_slug}.html"
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
    }
    if inv_slug:
        resp["investigation"] = inv_slug
    return resp, 200
