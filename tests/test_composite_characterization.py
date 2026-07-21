"""Phase 3: the Composites-tab characterization surfacing.

Static assertions that the composite-explore view wires the EXISTING
characterization endpoints (GET /api/observables for outputs; GET
/api/composite-runs for measured wall-time from runs_meta) into the page —
no new backend. The HTML carries the containers; walkthrough.js loads them
(base-path-routed) and computes wall-time from completed-run timing.
"""
from __future__ import annotations

from pathlib import Path

import vivarium_workbench

_ROOT = Path(vivarium_workbench.__file__).parent


def _txt(rel: str) -> str:
    return (_ROOT / rel).read_text(encoding="utf-8")


def test_characterization_panel_in_template():
    html = _txt("templates/index.html.j2")
    assert 'id="ce-outputs"' in html
    assert 'id="ce-walltime"' in html
    assert 'id="ce-characterization"' in html


def test_walkthrough_loads_characterization_from_existing_endpoints():
    js = _txt("static/walkthrough.js")
    assert "function _ceLoadCharacterization" in js
    # Called from the resolve success path.
    assert "_ceLoadCharacterization(data.id)" in js
    # Outputs come from /api/observables; wall-time from /api/composite-runs — both
    # base-path routed via _api(...).
    assert "_api('/api/observables?ref=' + encodeURIComponent(id))" in js
    assert "_api('/api/composite-runs?spec_id=' + encodeURIComponent(id))" in js
    # Wall-time is derived from completed-run timing columns, not a new field.
    assert "completed_at" in js and "started_at" in js
