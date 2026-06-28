import re
from pathlib import Path

TPL = Path("vivarium_dashboard/templates/study-detail.html").read_text(encoding="utf-8")


def test_readouts_panel_has_async_shell_not_authored_loop():
    # New shell present, old authored {% for o in _obs %} table gone.
    assert 'id="readouts-table"' in TPL
    assert "{% for o in _obs %}" not in TPL


def test_add_observable_picker_removed():
    assert "Add observable from bigraph state" not in TPL
    assert "bigraph-picker-details" not in TPL


def test_registered_viz_modules_removed():
    assert "Registered visualization modules" not in TPL
    assert "btn-add-viz" not in TPL


def test_runs_compare_and_clear_buttons_removed():
    assert "btn-compare-selected" not in TPL
    assert "btn-clear-runs" not in TPL
    assert "Compare selected" not in TPL
    assert "Clear all runs" not in TPL
