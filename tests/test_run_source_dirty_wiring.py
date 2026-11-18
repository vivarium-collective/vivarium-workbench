"""Wiring: the Runs table's Source cell marks a run whose workspace tree was
dirty at launch, so the recorded commit isn't read as fully capturing what ran
(issue #1618, bug 10). Vanilla-JS frontend with no JS runner, so — like the
other *_wiring tests — assert the shipped asset.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _source_cell() -> str:
    js = (ROOT / "vivarium_workbench/static/sim-table.js").read_text()
    i = js.index("function sourceCell")
    return js[i: js.index("\n  function ", i + 1)]


def test_source_cell_reads_dirty_flag():
    fn = _source_cell()
    assert "s.dirty === true" in fn


def test_source_cell_shows_uncommitted_marker():
    fn = _source_cell()
    assert "+uncommitted" in fn
    assert "uncommitted changes present at run time" in fn
