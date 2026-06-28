"""Thread-B / Task 4 (readouts async table).

Structural tests (no JS harness): the Readouts tab now renders an async shell
and fetches /api/study-readouts (emit plan + authored annotations). The old
/api/study-observable-check validation badges have been superseded.
"""
from __future__ import annotations

from pathlib import Path

_PKG = Path(__file__).parent.parent / "vivarium_dashboard"


def test_study_detail_fetches_readouts_and_renders_table():
    js = (_PKG / "static" / "study-detail.js").read_text(encoding="utf-8")
    assert "/api/study-readouts" in js
    assert "_loadReadouts" in js
    assert "_emitStatusBadge" in js
    assert "_renderReadoutsTable" in js
    # All three emit_status values are handled.
    for status in ("emitted", "not_in_emit_plan", "derived"):
        assert status in js
    # Failure tolerated — no throw, guarded on shape.
    assert "catch" in js


def test_readouts_panel_has_async_shell():
    html = (_PKG / "templates" / "study-detail.html").read_text(encoding="utf-8")
    assert 'id="readouts-table"' in html
    assert "data-study=" in html
    # Old authored-loop table is gone.
    assert "{% for o in _obs %}" not in html
    assert "Validated against composite" not in html
