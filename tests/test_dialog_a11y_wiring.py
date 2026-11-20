"""Wiring checks for the dialog + inline-edit accessibility fixes (issue #1618).

Vanilla-JS frontend with no JS test runner, so — like the other *_wiring tests —
these assert the shipped static assets contain the behavior. The interactive
result (focus/Escape/visible pencils) still warrants a manual pass.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _walkthrough() -> str:
    return (ROOT / "vivarium_workbench/static/walkthrough.js").read_text()


def _inline_edit() -> str:
    return (ROOT / "vivarium_workbench/static/inline-edit.js").read_text()


def test_open_modal_sets_dialog_role_and_focuses_first_field():
    js = _walkthrough()
    # role="dialog" + aria-modal on the dialog box, labelled from its heading.
    assert 'setAttribute("role", "dialog")' in js
    assert 'setAttribute("aria-modal", "true")' in js
    assert 'setAttribute("aria-labelledby"' in js
    # Focus moves into the dialog on open (first text field preferred).
    assert "_focusFirstInModal" in js
    assert "input:not([type=hidden])" in js


def test_escape_closes_the_modal():
    js = _walkthrough()
    # A keydown handler that closes the top-most visible overlay on Escape.
    assert 'e.key !== "Escape"' in js
    assert ".modal-overlay" in js
    assert "_dismissModalEl" in js


def test_close_restores_focus():
    js = _walkthrough()
    # Focus returns to the element that had it before the dialog opened.
    assert "_modalReturnFocus" in js


def test_edit_pencil_is_visible_by_default():
    js = _inline_edit()
    # The ✎ affordance must not be opacity:0 at rest (it was undiscoverable).
    assert "opacity:.5;transform:none" in js
    assert "opacity:0;transform:translateY(-2px)" not in js
    # Still emphasized on hover/focus.
    assert ".ie-editable:hover>.ie-pencil,.ie-pencil:focus{opacity:.95" in js
