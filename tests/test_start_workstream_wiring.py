"""Wiring checks for the Start-workstream button (issue #1618, blocker C).

`_startWork()` (POST /api/work-start) already existed, but nothing in the GUI
called it, so a new user could not begin an investigation branch — and the
Catalog install dialog claimed it "Requires an active investigation branch",
which the server never enforced. Vanilla-JS frontend with no JS test runner, so
— like the other *_wiring tests — these assert the shipped static asset contains
the behavior. The interactive result still warrants a manual pass.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _walkthrough() -> str:
    return (ROOT / "vivarium_workbench/static/walkthrough.js").read_text()


def test_start_workstream_button_is_wired_to_start_work():
    js = _walkthrough()
    # The git-status action strip surfaces a Start-workstream CTA that calls the
    # (previously orphaned) _startWork handler when no workstream is active.
    assert 'onclick="_startWork()"' in js
    assert ">Start workstream<" in js
    # Gated on the absence of an active workstream so it's mutually exclusive
    # with the End button.
    assert "if (!s.has_active_workstream) {" in js
    # The handler itself still posts to the work-start endpoint.
    assert "'/api/work-start'" in js
    assert "window._startWork = _startWork;" in js


def test_catalog_install_copy_drops_stale_active_branch_requirement():
    js = _walkthrough()
    # The server does not require an active investigation branch to install
    # (catalog_install defers the commit), so the dialog must not claim it does.
    assert "Requires an active investigation branch" not in js
    assert "on the active investigation branch?" not in js
