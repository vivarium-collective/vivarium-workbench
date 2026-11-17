"""Wiring: the needs-attention chip/popover don't claim "✓ All clear" for an
empty or never-run investigation (issue #1618, bug 3). Vanilla-JS frontend with
no JS runner, so — like the other *_wiring tests — assert the shipped assets.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _walkthrough() -> str:
    return (ROOT / "vivarium_workbench/static/walkthrough.js").read_text()


def test_quiet_state_helper_distinguishes_no_studies_and_no_runs():
    js = _walkthrough()
    assert "function _naQuietState(" in js
    assert "No studies yet" in js
    assert "No runs yet" in js
    # Green "All clear" is still the genuine-clear branch.
    assert "aig-na-clear" in js and "aig-na-quiet" in js


def test_chip_and_popover_route_through_quiet_state():
    js = _walkthrough()
    # Chip picks its label/class from the helper rather than hard-coding "All clear".
    assert "_naQuietState(d.summary)" in js
    # Popover header uses the helper too.
    assert "_naQuietState(summary)" in js


def test_quiet_chip_has_neutral_style():
    css = (ROOT / "vivarium_workbench/static/style.css").read_text()
    assert ".aig-na-chip.aig-na-quiet" in css
