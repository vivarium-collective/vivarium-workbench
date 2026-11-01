"""Task V4 — the 'visualization_gap' readiness finding + its wiring into
GET /api/report-lint (feeding the existing "N readiness gaps" pill).
Recalibrated by Task Vcal into three outcomes (warning / info / silent),
driven by ``lib.viz_gate.study_visualization_status``'s ``gap_severity``.

Mirrors ``tests/test_report_lint_question.py``'s style for the lib-level
findings builder, plus a live-server test (via the ``inproc_client``
factory) confirming the finding actually rides ``/api/report-lint`` and is
counted the same way the readiness panel counts gaps: ``severity in
{"warning", "error"}`` (see ``static/study-detail.js``'s
``_renderReadinessPanel``, which sums ``sev.error + sev.warning`` into the
"N readiness gaps" pill; "info" findings are notes, not gaps).
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from vivarium_workbench.lib.report_views import (
    _visualization_gap_findings,
    build_report_lint,
)


def _study_with_no_figures(ws_root: Path, slug: str = "no-viz") -> None:
    sd = ws_root / "studies" / slug
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(
        yaml.safe_dump({"name": slug, "purpose": {"question": "Does X hold?"}}),
        encoding="utf-8",
    )


def test_non_qualifying_study_yields_visualization_gap_finding(tmp_path):
    ws = tmp_path / "ws"
    _study_with_no_figures(ws)
    findings = _visualization_gap_findings(ws)
    checks = [f["check"] for f in findings]
    assert "visualization_gap" in checks
    f = next(f for f in findings if f["check"] == "visualization_gap")
    assert f["study"] == "no-viz"
    assert f["severity"] in ("warning", "warn")
    assert f["field_path"] == "visualizations"
    # actionable — names what to do, not just that something's wrong
    assert "add" in f["message"].lower() or "link" in f["message"].lower()


def test_qualifying_study_yields_no_finding(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    _study_with_no_figures(ws, slug="has-viz")

    from vivarium_workbench.lib import viz_gate as _viz_gate
    monkeypatch.setattr(
        _viz_gate, "study_visualization_status",
        lambda ws_root, slug: {
            "has_interactive": True, "has_run_linked": True, "has_runs": True,
            "qualifies": True, "n_figures": 1, "reason": None, "gap_severity": None,
        },
    )
    findings = _visualization_gap_findings(ws)
    assert [f for f in findings if f["check"] == "visualization_gap"] == []


def test_visualization_gap_findings_never_raises_on_unreadable_study(tmp_path, monkeypatch):
    """A study whose viz-status probe blows up must not 500 the lint — it
    reads as a (soft) gap, not a crash."""
    ws = tmp_path / "ws"
    _study_with_no_figures(ws, slug="broken")

    from vivarium_workbench.lib import viz_gate as _viz_gate

    def _boom(ws_root, slug):
        raise RuntimeError("composite cannot be resolved")

    monkeypatch.setattr(_viz_gate, "study_visualization_status", _boom)
    findings = _visualization_gap_findings(ws)  # must not raise
    checks = [f["check"] for f in findings]
    assert "visualization_gap" in checks


def test_build_report_lint_includes_visualization_gap(tmp_path):
    ws = tmp_path / "ws"
    _study_with_no_figures(ws)
    body, code = build_report_lint(ws)
    assert code == 200
    checks = [f["check"] for f in body["findings"]]
    assert "visualization_gap" in checks


def test_visualization_gap_is_soft_never_error(tmp_path):
    """The finding must never be a hard error/block — warn/info only."""
    ws = tmp_path / "ws"
    _study_with_no_figures(ws)
    findings = _visualization_gap_findings(ws)
    for f in findings:
        assert f["severity"] != "error"


# ---------------------------------------------------------------------------
# Task Vcal — recalibrate into three outcomes (warning / info / silent),
# driven by `gap_severity`. These replace the OLD flat "always warning when
# not fully qualifying" behavior: a not-run-linked study that previously
# always warned now emits `info` (if it has a recorded run) or NOTHING (if
# it doesn't) — never weakened back to a blanket warning.
# ---------------------------------------------------------------------------

def _study_with_interactive_embed(
    ws_root: Path, slug: str, *, has_recorded_run: bool
) -> None:
    """A study with ONE interactive figure (embed, no run_id) — real spec,
    no mocking of `study_visualization_status` — so `has_runs` is derived
    for real via `read_runs_db_for_study`'s study.yaml `runs:` merge."""
    sd = ws_root / "studies" / slug
    sd.mkdir(parents=True)
    spec: dict = {
        "name": slug,
        "purpose": {"question": "Does X hold?"},
        "embed_visualizations": [
            {"name": "plot", "url": f"/studies/{slug}/viz/plot.html", "run_id": None},
        ],
    }
    if has_recorded_run:
        spec["runs"] = [{"name": "r1", "status": "completed"}]
    (sd / "study.yaml").write_text(yaml.safe_dump(spec), encoding="utf-8")


def test_vcal_case_b_interactive_not_run_linked_with_runs_yields_info(tmp_path):
    """(b) has_interactive + !has_run_linked + HAS a recorded run ->
    `_visualization_gap_findings` emits ONE finding, severity 'info' (a soft
    nudge, not the old blanket 'warning')."""
    ws = tmp_path / "ws"
    _study_with_interactive_embed(ws, "info-nudge", has_recorded_run=True)
    findings = _visualization_gap_findings(ws)
    viz = [f for f in findings if f["check"] == "visualization_gap" and f["study"] == "info-nudge"]
    assert len(viz) == 1, viz
    assert viz[0]["severity"] == "info", viz[0]
    assert "link" in viz[0]["message"].lower()


def test_vcal_case_c_interactive_not_run_linked_no_runs_yields_nothing(tmp_path):
    """(c) has_interactive + !has_run_linked + NO recorded run at all ->
    `_visualization_gap_findings` emits NOTHING for this study — an unrun
    study isn't a visualization problem (this is the 37-noise-case fix; the
    OLD behavior warned here, which would be a wrongly-weakened assertion to
    keep)."""
    ws = tmp_path / "ws"
    _study_with_interactive_embed(ws, "unrun-silent", has_recorded_run=False)
    findings = _visualization_gap_findings(ws)
    viz = [f for f in findings if f["study"] == "unrun-silent"]
    assert viz == [], viz


def test_vcal_qualifying_and_empty_still_behave(tmp_path):
    """(d)/(a)+(e) sanity: a fully-qualifying study still yields nothing, an
    empty study still warns — Vcal only recalibrates the not-run-linked
    branch, not the other two."""
    ws = tmp_path / "ws"
    _study_with_no_figures(ws, slug="still-warns")
    findings = _visualization_gap_findings(ws)
    warn = [f for f in findings if f["study"] == "still-warns"]
    assert len(warn) == 1
    assert warn[0]["severity"] == "warning"


# ---------------------------------------------------------------------------
# Live-server: the finding rides /api/report-lint and is counted the same
# way the readiness panel counts "N readiness gaps" (severity warning/error).
# ---------------------------------------------------------------------------

def test_rides_report_lint_endpoint_and_counts_as_a_readiness_gap(inproc_client, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: viz-gap-ws\n", encoding="utf-8")
    _study_with_no_figures(ws, slug="no-viz")

    client = inproc_client(ws)
    r = client.get("/api/report-lint")
    assert r.status_code == 200
    findings = r.json()["findings"]
    study_findings = [f for f in findings if f.get("study") == "no-viz"]
    viz_findings = [f for f in study_findings if f.get("check") == "visualization_gap"]
    assert viz_findings, f"expected a visualization_gap finding; got: {study_findings}"

    # Same arithmetic the readiness panel uses (_renderReadinessPanel in
    # static/study-detail.js): gaps = count(severity in {error, warning}).
    gaps = sum(1 for f in study_findings if f.get("severity") in ("error", "warning"))
    assert gaps >= 1, "visualization_gap finding should be counted in N readiness gaps"


def test_vcal_info_findings_do_not_count_toward_readiness_gap_pill(inproc_client, tmp_path):
    """Task Vcal: a study with an 'info' visualization_gap finding must NOT
    inflate the readiness-panel gap count — only warning/error do (per
    _renderReadinessPanel's `sev.error + sev.warning` arithmetic)."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: viz-gap-ws\n", encoding="utf-8")
    _study_with_interactive_embed(ws, "info-nudge", has_recorded_run=True)

    client = inproc_client(ws)
    r = client.get("/api/report-lint")
    assert r.status_code == 200
    findings = r.json()["findings"]
    study_findings = [f for f in findings if f.get("study") == "info-nudge"]
    viz_findings = [f for f in study_findings if f.get("check") == "visualization_gap"]
    assert viz_findings and viz_findings[0]["severity"] == "info", viz_findings

    # Scope the gap-count arithmetic to the visualization_gap check itself —
    # other unrelated checks (e.g. missing_question) may independently warn
    # on this minimal fixture study; that's out of scope for this test.
    gaps = sum(1 for f in viz_findings if f.get("severity") in ("error", "warning"))
    assert gaps == 0, "an info-severity nudge must not count as a readiness gap"
