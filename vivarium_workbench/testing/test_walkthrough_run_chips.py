"""Task 9: the investigation report wires run-command chips.

These are STRUCTURAL wiring assertions (not "the substring exists somewhere"):
the report (lib.investigation_report + its template) defines a `_runChip` helper,
reads `run_commands` off the per-study payload, and references
`.run_commands.baseline` for the Reproduce / "what we ran" chips and
`.run_commands.variants` for the per-variant chips. (The report moved server-side;
these assert its template render source.)
"""
from pathlib import Path

_PKG = Path(__file__).parent.parent
JS = (_PKG / "templates" / "investigation-report.html").read_text()


def test_run_chip_helper_defined():
    # the helper that renders a copy-to-run chip
    assert "function _runChip(cmd)" in JS
    assert 'class="run-chip"' in JS


def test_reads_run_commands_payload():
    # the SPA reads the precomputed commands off each study payload
    assert "run_commands" in JS


def test_baseline_chip_wired():
    # real wiring: the baseline command is read off s.run_commands.baseline
    assert "s.run_commands && s.run_commands.baseline" in JS


def test_variant_chip_wired():
    # real wiring: the per-variant entries are looked up off s.run_commands.variants
    assert "s.run_commands && s.run_commands.variants" in JS
    # variants are listed cleanly as "<id> — <description>" via _vrow, with the base
    # reproduce command shown ONCE (_runChip(_base)). The old per-variant command chip
    # (_runChip(_vc && _vc.cmd)) was dropped: the variant name already carries the
    # description, so the per-row command repeated it and read as unreadable noise.
    assert "_rcVariants.map(_vrow)" in JS
    assert "--variant" in JS


def test_reproduce_line_present():
    assert "reproduce-line" in JS
    assert "Reproduce:" in JS


def test_degrades_without_run_commands():
    # _runChip returns '' for a falsy command (older payloads / static bundle)
    assert "if (!cmd) return '';" in JS
