import re
from pathlib import Path

TPL = Path("vivarium_workbench/templates/study-detail.html").read_text(encoding="utf-8")


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


def test_panel_sections_no_premature_close():
    """Fix 5: each of the three key panels has exactly one </section> before the
    next study-tab-panel (guards the premature-</section> regression class).
    """
    panel_ids = ["panel-observables", "panel-runs", "panel-visualizations"]
    for pid in panel_ids:
        start = TPL.find(f'id="{pid}"')
        assert start != -1, f"Panel {pid!r} not found in template"
        # Find the start of the next study-tab-panel after this one's opening tag.
        next_panel = TPL.find('class="study-tab-panel"', start + len(pid))
        end = next_panel if next_panel != -1 else len(TPL)
        segment = TPL[start:end]
        count = segment.count("</section>")
        assert count == 1, (
            f"Panel {pid!r}: expected exactly 1 </section> before next panel, got {count}"
        )
