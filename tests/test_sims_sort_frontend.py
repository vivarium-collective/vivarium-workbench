from pathlib import Path

WALK = Path("vivarium_workbench/static/walkthrough.js").read_text()
TPL = Path("vivarium_workbench/templates/index.html.j2").read_text()


def test_sort_helper_and_header_handlers_present():
    assert "function _sortSimRows(" in WALK
    assert "_simSortState" in WALK           # {key, dir}
    # headers carry a sort hook + data-sort-key
    assert "data-sort-key=" in TPL
    assert "_onSimHeaderClick" in WALK
