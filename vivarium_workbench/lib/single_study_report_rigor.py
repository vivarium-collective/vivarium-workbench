"""Rigor / measurement-integrity report sections (Wave 3b, #285).

Extracted from ``single_study_report.py``: the threshold-provenance + sensitivity
(#9) and per-metric calibration-ladder (#20) measurement-integrity renderers, and
the verdict-count gate ledger (viva-superpowers #285). All pure
``spec: dict -> HTML str`` with no shared mutable state; ``single_study_report``
re-imports them so existing ``single_study_report.<name>`` references keep working.
"""
from __future__ import annotations

from vivarium_workbench.lib.conclusion_card import _TRACK_COLORS
from vivarium_workbench.lib.html_render import esc as _h


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
        from viva_superpowers.rigor import threshold_sensitivity
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
# Rigor split (viva-superpowers #285) — the verdict-count ledger, the
# units_and_time table, and the provenance/environment block. Each renders
# defensively: a missing/empty field returns '' so the section is omitted.
# ---------------------------------------------------------------------------

def _render_gate_ledger(spec: dict) -> str:
    """The verdict-count LEDGER — pins vs acceptance vs expected-fail counted
    separately (e.g. "pins 2/2 · acceptance 1/2 · expected-fail behaved 1/1")
    instead of a single conflated "gate: passed". Driven by
    ``gate_rigor.verdict_count_split`` (delegates to viva_superpowers when
    #285 is installed, local mirror otherwise). '' when the study declares no
    behavior tests.
    """
    try:
        from vivarium_workbench.lib.gate_rigor import verdict_count_split
        split = verdict_count_split(spec)
    except Exception:
        return ""
    if not isinstance(split, dict):
        return ""
    pins = split.get("regression_pins") or {}
    acc = split.get("acceptance_criteria") or {}
    ef = split.get("expected_fail") or {}
    n_unclassified = split.get("unclassified") or 0
    n_narrated = split.get("narrated") or 0
    if not (pins.get("total") or acc.get("total") or ef.get("total")
            or n_unclassified or n_narrated):
        return ""

    def _seg(label: str, ok: int, total: int, *, hint: str) -> str:
        if total <= 0:
            return ""
        if ok >= total:
            bg, fg = "#dcfce7", "#166534"
        elif ok > 0:
            bg, fg = "#fef3c7", "#92400e"
        else:
            bg, fg = "#fee2e2", "#991b1b"
        return (
            f'<span title="{_h(hint)}" style="display:inline-block;'
            f'padding:3px 12px;border-radius:9999px;background:{bg};color:{fg};'
            f'font-weight:700;font-size:0.9em;margin:2px">'
            f'{_h(label)} {ok}/{total}</span>'
        )

    segs = [
        _seg("pins", int(pins.get("pass") or 0), int(pins.get("total") or 0),
             hint="regression pins — freeze known-good behavior"),
        _seg("acceptance", int(acc.get("pass") or 0), int(acc.get("total") or 0),
             hint="acceptance criteria — the forward-looking scientific gates"),
        _seg("expected-fail behaved", int(ef.get("behaved") or 0),
             int(ef.get("total") or 0),
             hint="expected-fail controls — behaved = failed as designed"),
    ]
    extras = []
    if n_unclassified:
        extras.append(
            f'<span style="display:inline-block;padding:3px 12px;border-radius:9999px;'
            f'background:#f1f5f9;color:#475569;font-size:0.9em;margin:2px" '
            f'title="graded tests with no gate_class — classify them as '
            f'regression_pin or acceptance_criterion">'
            f'{n_unclassified} unclassified</span>')
    if n_narrated:
        extras.append(
            f'<span style="display:inline-block;padding:3px 12px;border-radius:9999px;'
            f'background:#f1f5f9;color:#475569;font-size:0.9em;margin:2px" '
            f'title="narrative-only expectations (no machine-checkable measure)">'
            f'{n_narrated} narrated</span>')
    label = str(split.get("label") or "")
    rerun = split.get("committed_rerunnable")
    rerun_html = ""
    if rerun is not None:
        # #285: committed_rerunnable is a COUNT of tests carrying an
        # executable check / machine predicate (not a bool) — render the
        # number when we have one, but stay tolerant of a bool from an
        # older/offline local mirror.
        rerun_text = ("yes" if rerun else "no") if isinstance(rerun, bool) else str(rerun)
        rerun_html = (
            '<div style="color:#64748b;font-size:0.85em;margin-top:4px">'
            'committed rerunnable evidence: '
            + rerun_text + '</div>'
        )
    return (
        '<section id="gate-ledger"><h2>Gate ledger</h2>'
        '<p style="color:#475569;font-size:0.92em;margin:0 0 8px">Regression pins, '
        'acceptance criteria, and expected-fail controls counted <strong>separately</strong> '
        '— a behaved negative control is never a green acceptance pass, and a pin '
        'regression is never hidden inside an overall "gate: passed".</p>'
        f'<div title="{_h(label)}">' + "".join(s for s in segs if s) + "".join(extras)
        + '</div>' + rerun_html +
        '</section>'
    )
