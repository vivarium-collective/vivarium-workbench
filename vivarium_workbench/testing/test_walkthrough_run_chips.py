"""Task 9: the investigation-report SPA wires run-command chips.

These are STRUCTURAL wiring assertions (not "the substring exists somewhere"):
the report builder must define a `_runChip` helper, read `run_commands` off the
per-study payload, and reference `.run_commands.baseline` for the Reproduce /
"what we ran" chips and `.run_commands.variants` for the per-variant chips.
"""
from pathlib import Path

_PKG = Path(__file__).parent.parent
JS = (_PKG / "static" / "walkthrough.js").read_text()


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
    # real wiring: per-variant commands are looked up off s.run_commands.variants
    assert "s.run_commands && s.run_commands.variants" in JS
    # matched by variant name, then the chip renders the resolved cmd
    assert "_runChip(_vc && _vc.cmd)" in JS


def test_reproduce_line_present():
    assert "reproduce-line" in JS
    assert "Reproduce:" in JS


def test_degrades_without_run_commands():
    # _runChip returns '' for a falsy command (older payloads / static bundle)
    assert "if (!cmd) return '';" in JS
