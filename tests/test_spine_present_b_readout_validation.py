"""Thread-B / Task 3 (B2): readout validation badges.

Structural tests (no JS harness): the Readouts tab fetches the SP2b-i
never-fabricate guard /api/study-observable-check and badges each readout row
with the COMPUTED validation status (ok / unresolved / not_in_structure /
aspirational) BESIDE the authored status. Failure is tolerated (no badge).
"""
from __future__ import annotations

from pathlib import Path

_PKG = Path(__file__).parent.parent / "vivarium_dashboard"


def test_study_detail_fetches_observable_check_and_badges():
    js = (_PKG / "static" / "study-detail.js").read_text(encoding="utf-8")
    assert "/api/study-observable-check" in js
    assert "_loadReadoutValidation" in js
    assert "_readoutValidationBadge" in js
    # All four computed statuses are handled.
    for status in ("ok", "unresolved", "not_in_structure", "aspirational"):
        assert status in js
    # not_in_structure links to re-author guidance.
    assert "re-author" in js
    # Failure tolerated — no throw, guarded on shape.
    assert "tolerate" in js or "catch" in js


def test_readouts_table_has_validation_column():
    html = (_PKG / "templates" / "study-detail.html").read_text(encoding="utf-8")
    assert "Validated against composite" in html
    assert "readout-validation" in html
    assert 'data-readout="{{ o.name }}"' in html
