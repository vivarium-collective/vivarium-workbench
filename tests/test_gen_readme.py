"""Tests for `vivarium-workbench gen-readme` (vivarium_workbench.gen_readme).

Builds a tiny hermetic workspace in tmp_path — one .composite.yaml (so composite
discovery works without an importable package) + one investigation.yaml + a
README with markers — and exercises generate / --check / idempotency.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from vivarium_workbench import gen_readme


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


@pytest.fixture()
def ws(tmp_path: Path) -> Path:
    root = tmp_path / "demo-ws"
    # Mirror the real v2ecoli layout: investigations live under workspace/.
    _write(root / "workspace.yaml", """\
        name: demo-ws
        package_path: pbg_demo
        layout:
          investigations: workspace/investigations
          studies: workspace/studies
    """)
    _write(root / "pbg_demo" / "composites" / "widget.composite.yaml", """\
        name: widget
        description: |
          A demo widget composite for E. coli. Second sentence is dropped.
        state: {}
    """)
    _write(root / "workspace" / "investigations" / "alpha" / "investigation.yaml", """\
        name: alpha
        title: Alpha Investigation
        status: active
        question: |
          Does the widget behave? Extra detail we truncate away.
    """)
    _write(root / "README.md", """\
        # demo

        <!-- BEGIN:composites -->
        <!-- END:composites -->

        <!-- BEGIN:investigations -->
        <!-- END:investigations -->
    """)
    return root


def test_generate_fills_both_tables(ws: Path):
    rc = gen_readme.generate(ws)
    assert rc == 0
    text = (ws / "README.md").read_text()
    # Composite row (from the .composite.yaml), first sentence only.
    assert "| `widget` | A demo widget composite for E. coli. |" in text
    assert "Second sentence is dropped" not in text
    # Investigation row: title + status; no Pages link (tmp workspace has no remote).
    assert "Alpha Investigation _(active)_" in text
    assert "Does the widget behave?" in text
    assert "Extra detail we truncate away" not in text
    assert gen_readme.GEN_NOTE in text


def test_idempotent_and_check(ws: Path):
    assert gen_readme.generate(ws) == 0
    first = (ws / "README.md").read_text()
    # --check on a fresh README passes and does not rewrite.
    assert gen_readme.generate(ws, check=True) == 0
    assert (ws / "README.md").read_text() == first
    # Re-running generate is a no-op.
    assert gen_readme.generate(ws) == 0
    assert (ws / "README.md").read_text() == first


def test_check_detects_drift(ws: Path):
    assert gen_readme.generate(ws) == 0
    # Add a second investigation → README is now stale.
    _write(ws / "workspace" / "investigations" / "beta" / "investigation.yaml", """\
        name: beta
        title: Beta Investigation
        question: Another question?
    """)
    assert gen_readme.generate(ws, check=True) == 1
    # Regenerate → fresh again.
    assert gen_readme.generate(ws) == 0
    assert gen_readme.generate(ws, check=True) == 0
    assert "Beta Investigation" in (ws / "README.md").read_text()


def test_dashboard_block_local_fallback(ws: Path):
    # A README with only the dashboard marker + no git remote → the link block
    # falls back to the local-serve instruction (no Pages URL to link).
    _write(ws / "README.md", """\
        # demo

        <!-- BEGIN:dashboard -->
        <!-- END:dashboard -->
    """)
    assert gen_readme.generate(ws) == 0
    text = (ws / "README.md").read_text()
    assert "workbench" in text.lower()
    assert "vivarium-workbench serve" in text  # no remote → local-serve instruction
    assert gen_readme.GEN_NOTE in text


def test_render_dashboard_with_base():
    out = gen_readme.render_dashboard("https://owner.github.io/repo")
    assert "https://owner.github.io/repo/workbench/" in out
    assert "Open the read-only workbench" in out
    assert "🔬" not in out  # plain, not the old slop callout


def test_render_badges_with_remote(tmp_path: Path):
    out = gen_readme.render_badges(tmp_path, "owner/repo", "https://owner.github.io/repo")
    assert "actions/workflows/workspace-ci.yml/badge.svg" in out
    assert "https://owner.github.io/repo/workbench/" in out
    # No LICENSE file in tmp_path → no license badge.
    assert "github/license" not in out


def test_render_badges_includes_license_when_present(tmp_path: Path):
    (tmp_path / "LICENSE").write_text("MIT\n")
    out = gen_readme.render_badges(tmp_path, "owner/repo", "https://owner.github.io/repo")
    assert "img.shields.io/github/license/owner/repo" in out


def test_render_badges_no_remote_is_empty(tmp_path: Path):
    out = gen_readme.render_badges(tmp_path, None, None)
    assert "badge" not in out  # nothing to point at → just the gen-note, no broken badges


def test_only_present_markers_are_generated(ws: Path):
    # A README with only the composites marker gets only that block.
    _write(ws / "README.md", """\
        # demo
        <!-- BEGIN:composites -->
        <!-- END:composites -->
    """)
    assert gen_readme.generate(ws) == 0
    text = (ws / "README.md").read_text()
    assert "| `widget` |" in text
    assert "BEGIN:investigations" not in text


def test_no_markers_returns_2(ws: Path):
    _write(ws / "README.md", "# demo\n\nno markers here\n")
    assert gen_readme.generate(ws) == 2


def test_missing_readme_returns_2(ws: Path):
    (ws / "README.md").unlink()
    assert gen_readme.generate(ws) == 2


@pytest.mark.parametrize("text,expected", [
    ("Whole-cell E. coli model. And more.", "Whole-cell E. coli model."),
    ("e.g. a thing. Next sentence here.", "e.g. a thing."),
    ("One sentence only", "One sentence only"),
    ("First. Second. Third.", "First."),
])
def test_first_sentence_abbreviations(text, expected):
    assert gen_readme._first_sentence(text) == expected


def test_md_cell_escapes_pipes_and_flattens():
    assert gen_readme._md_cell("a | b\nc") == "a \\| b c"
