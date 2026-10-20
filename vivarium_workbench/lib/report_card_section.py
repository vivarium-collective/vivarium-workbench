"""Render a study's report cards into the static per-study report.

A **report card** is the compiled *output* of a study's Tests — the rendered
result at ``<study>/viz/report_card/<card>.html`` with its structured verdict at
``<card>.verdict.json`` (``overall`` + per-group/per-axis ``label/verdict/meter``).
This is the SAME artifact ``study_spec.report_card_urls`` feeds the live SPA
"Tests" tab; the static report reuses it so a published report shows the same
report cards a reviewer sees in the workbench, plus the since-last-run change from
``test_diff.json``.

Reads workspace files only (no live backend), so the section survives in a
published static bundle; returns ``""`` when a study has no report cards.
"""
from __future__ import annotations

import json
from pathlib import Path
from vivarium_workbench.lib.html_render import esc as _e

_COLOR = {"within_tol": "#16a34a", "drift": "#d97706",
          "mismatch": "#dc2626", "ungraded": "#6b7280"}
_GLYPH = {"within_tol": "✓", "drift": "≈", "mismatch": "✗", "ungraded": "–"}
_ALIAS = {"pass": "within_tol", "passing": "within_tol", "met": "within_tol",
          "fail": "mismatch", "failing": "mismatch", "not met": "mismatch",
          "warn": "drift", "partial": "drift", "conditional-pass": "drift",
          "skip": "ungraded", "pending": "ungraded", "gap": "ungraded"}
# since-last-run change badges (test_diff/v1 `change`).
_CHANGE = {"fixed": ("#16a34a", "✓ fixed"), "broke": ("#dc2626", "✗ broke"),
           "improved": ("#16a34a", "↑ improved"), "regressed": ("#d97706", "↓ regressed"),
           "new": ("#2563eb", "＋ new"), "gone": ("#6b7280", "－ gone")}




def _norm(v) -> str:
    if not v:
        return "ungraded"
    v = str(v).strip().lower()
    return v if v in _COLOR else _ALIAS.get(v, "ungraded")


def _latest_run_json(ws_root: Path, slug: str, filename: str):
    """Read ``<latest run_dir>/<filename>`` for a study, or ``None``. Best-effort."""
    try:
        from vivarium_workbench.lib.study_charts import latest_run_row
        from vivarium_workbench.lib.study_spec import study_dir
        from vivarium_workbench.lib.workspace_paths import WorkspacePaths
        latest = latest_run_row(study_dir(ws_root, slug) / "runs.db")
        if latest and latest.get("run_id"):
            f = WorkspacePaths.load(ws_root).pbg / "runs" / latest["run_id"] / filename
            if f.is_file():
                return json.loads(f.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return None


def _report_cards(ws_root: Path, slug: str) -> list[dict]:
    """The study's report cards from ``<study>/viz/report_card/<card>.html`` +
    ``<card>.verdict.json`` — the same source study_spec.report_card_urls scans."""
    try:
        from vivarium_workbench.lib.study_spec import study_dir
    except Exception:  # noqa: BLE001
        return []
    rc_dir = study_dir(ws_root, slug) / "viz" / "report_card"
    if not rc_dir.is_dir():
        return []
    cards = []
    for html_path in sorted(rc_dir.glob("*.html")):
        card = html_path.name[: -len(".html")]
        verdict = None
        groups = None
        vf = html_path.with_name(card + ".verdict.json")
        if vf.is_file():
            try:
                vj = json.loads(vf.read_text(encoding="utf-8"))
                verdict = vj.get("overall")
                groups = vj.get("groups")
            except Exception:  # noqa: BLE001
                pass
        try:
            html_text = html_path.read_text(encoding="utf-8")
        except OSError:
            html_text = ""
        # Stub: a verdict makes the card real regardless of HTML size; else a
        # tiny HTML file is a placeholder we render from the verdict table.
        stub = verdict is None and len(html_text.strip()) < 64
        cards.append({"card": card, "verdict": verdict, "groups": groups,
                      "html": html_text, "stub": stub})
    return cards


def _chip(verdict: str) -> str:
    v = _norm(verdict)
    return (f'<span style="display:inline-block;padding:1px 7px;border-radius:9px;'
            f'background:{_COLOR[v]};color:#fff;font-size:0.8em;white-space:nowrap">'
            f'{_GLYPH[v]} {_e(v.replace("_", " "))}</span>')


def _change_badge(change, margin_delta) -> str:
    entry = _CHANGE.get(change)
    if not entry:
        return ""
    color, label = entry
    md = f' ({margin_delta:+.3g})' if isinstance(margin_delta, (int, float)) else ""
    return (f'<span style="display:inline-block;padding:0 6px;border-radius:8px;'
            f'background:{color};color:#fff;font-size:0.74em;white-space:nowrap">'
            f'{_e(label)}{_e(md)}</span>')


def _verdict_table(card: str, groups: dict, change_of: dict) -> str:
    rows = []
    for gslug, grp in (groups or {}).items():
        axes = (grp or {}).get("axes") or []
        if not axes:
            continue
        rows.append(
            '<tr><td colspan="4" style="padding:7px 8px 3px;font-size:0.76em;'
            'text-transform:uppercase;letter-spacing:.04em;color:#64748b">'
            f'{_e(gslug.replace("_", " "))} &nbsp; {_chip((grp or {}).get("verdict", "ungraded"))}</td></tr>')
        for i, ax in enumerate(axes):
            bg = "#ffffff" if i % 2 == 0 else "#f8fafc"
            change, md = change_of.get((card, ax.get("id")), (None, None))
            rows.append(
                f'<tr style="background:{bg};border-top:1px solid #eef2f7">'
                f'<td style="padding:6px 8px">{_e(ax.get("label", ax.get("id")))}</td>'
                f'<td style="padding:6px 8px">{_chip(ax.get("verdict", "ungraded"))}</td>'
                f'<td style="padding:6px 8px;color:#475569;font-size:0.85em">{_e(ax.get("meter") or "")}</td>'
                f'<td style="padding:6px 8px">{_change_badge(change, md)}</td>'
                '</tr>')
    if not rows:
        return ""
    return ('<table style="width:100%;border-collapse:collapse;font-size:0.9em">'
            '<thead><tr style="text-align:left;color:#94a3b8;font-size:0.72em;'
            'text-transform:uppercase;letter-spacing:.04em">'
            '<th style="padding:5px 8px">Test</th><th style="padding:5px 8px">Verdict</th>'
            '<th style="padding:5px 8px">Meter</th><th style="padding:5px 8px">Since last run</th>'
            f'</tr></thead><tbody>{"".join(rows)}</tbody></table>')


def _embed_iframe(html_text: str) -> str:
    return (f'<iframe scrolling="no" srcdoc="{_e(html_text)}" class="rc-iframe" '
            'style="width:100%;min-height:420px;border:0;display:block;overflow:hidden" '
            'loading="lazy"></iframe>')


_FIT_SCRIPT = (
    '<script>(function(){function fit(f){try{var d=f.contentDocument;if(!d)return;'
    'var h=Math.max(d.documentElement?d.documentElement.scrollHeight:0,'
    'd.body?d.body.scrollHeight:0);if(h>0)f.style.height=h+"px";}catch(e){}}'
    'document.querySelectorAll(".rc-iframe").forEach(function(f){'
    'f.addEventListener("load",function(){fit(f);[300,1200,2500].forEach(function(t){'
    'setTimeout(function(){fit(f);},t);});});});})();</script>')


def render_report_cards_section(ws_root: Path, slug: str) -> str:
    """Render the study's report cards (compiled Test output) as an HTML
    ``<section>``, or ``""`` when the study has none."""
    ws_root = Path(ws_root)
    cards = _report_cards(ws_root, slug)
    if not cards:
        return ""
    diff = _latest_run_json(ws_root, slug, "test_diff.json")
    change_of: dict = {}
    diff_counts: dict = {}
    for p in ((diff or {}).get("per") or []):
        change_of[(p.get("card"), p.get("id"))] = (p.get("change"), p.get("margin_delta"))
        c = p.get("change")
        if c in _CHANGE:
            diff_counts[c] = diff_counts.get(c, 0) + 1

    diff_line = ""
    if diff_counts:
        bits = [f'<span style="color:{_CHANGE[c][0]}">{n} {_CHANGE[c][1].split(" ", 1)[1]}</span>'
                for c, n in diff_counts.items()]
        diff_line = ('<div style="font-size:0.85em;color:#475569;margin:2px 0 12px">'
                     'Since last run: ' + " &nbsp; ".join(bits) + '</div>')

    has_embed = False
    blocks = []
    for c in cards:
        table = _verdict_table(c["card"], c["groups"], change_of)
        body = table
        if not c["stub"] and c["html"].strip():
            has_embed = True
            body = (table + _embed_iframe(c["html"])) if table else _embed_iframe(c["html"])
        if not body:
            body = ('<p style="color:#6b7280;font-size:0.85em;margin:8px">'
                    'card rendered but carries no structured verdict.</p>')
        blocks.append(
            '<div style="border:1px solid #e2e8f0;border-radius:8px;overflow:hidden;margin:0 0 12px">'
            '<div style="padding:7px 10px;background:#f1f5f9;border-bottom:1px solid #e2e8f0;'
            f'font-weight:600;color:#0f172a">{_e(c["card"])} &nbsp; {_chip(c["verdict"] or "ungraded")}</div>'
            f'{body}</div>')

    return ('<section id="report-cards"><h2>Report cards</h2>'
            '<p style="color:#475569;font-size:0.92em;margin:0 0 8px">'
            'The compiled output of this study’s Tests — per-test verdict and the '
            'change since the previous run.</p>'
            + diff_line + "".join(blocks)
            + (_FIT_SCRIPT if has_embed else "") + '</section>')
