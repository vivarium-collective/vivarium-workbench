"""Drift guard: vendored vivarium_workbench/lib/viz_freshness.py must stay
identical to the canonical pbg_superpowers/viz_freshness.py.

Uses the file-read approach (pbg_superpowers is not installed in the dashboard
venv), extracting each function's source by scanning for `def <name>` blocks.
"""
import re
import pytest
from pathlib import Path

CANONICAL = Path(__file__).parent.parent.parent / "pbg-superpowers" / "pbg_superpowers" / "viz_freshness.py"
VENDORED = Path(__file__).parent.parent / "vivarium_workbench" / "lib" / "viz_freshness.py"

FUNCS = ["stamp_meta", "read_meta", "chart_freshness", "manifest_diff", "_meta_path", "_hash"]
CONSTANTS = ["FRESH", "STALE", "UNRENDERED", "UNTRACKED"]


def _extract_functions(source: str) -> dict[str, str]:
    """Extract top-level function source blocks keyed by name."""
    pattern = re.compile(r"^(def \w+)", re.MULTILINE)
    result = {}
    lines = source.splitlines(keepends=True)
    # find start indices for each def
    starts = []
    for i, line in enumerate(lines):
        if re.match(r"^def \w+", line):
            m = re.match(r"^def (\w+)", line)
            starts.append((i, m.group(1)))
    for idx, (start, name) in enumerate(starts):
        end = starts[idx + 1][0] if idx + 1 < len(starts) else len(lines)
        result[name] = "".join(lines[start:end]).rstrip("\n")
    return result


def _extract_constants(source: str) -> dict[str, str]:
    """Extract constant values, handling both single and tuple assignments.

    Handles:
      FRESH = "fresh"
      FRESH, STALE, ... = "fresh", "stale", ...
    """
    result = {}
    for line in source.splitlines():
        # Single: NAME = value
        m = re.match(r"^([A-Z_]+)\s*=\s*(.+)", line)
        if m:
            result[m.group(1)] = m.group(2).strip()
            continue
        # Tuple: NAME1, NAME2, ... = val1, val2, ...
        m = re.match(r"^([A-Z_]+(?:\s*,\s*[A-Z_]+)+)\s*=\s*(.+)", line)
        if m:
            names = [n.strip() for n in m.group(1).split(",")]
            vals = [v.strip() for v in m.group(2).split(",")]
            for name, val in zip(names, vals):
                result[name] = val
    return result


def test_vendored_viz_freshness_matches_canonical():
    if not CANONICAL.is_file():
        pytest.skip(
            f"canonical pbg-superpowers checkout not present at {CANONICAL} — "
            "this drift guard only runs when the sibling repo is checked out "
            "alongside (it is not, in CI)")
    assert VENDORED.is_file(), f"Vendored not found: {VENDORED}"

    canonical_src = CANONICAL.read_text(encoding="utf-8")
    vendored_src = VENDORED.read_text(encoding="utf-8")

    canon_funcs = _extract_functions(canonical_src)
    vend_funcs = _extract_functions(vendored_src)

    for name in FUNCS:
        assert name in canon_funcs, f"{name} missing from canonical"
        assert name in vend_funcs, f"{name} missing from vendored"
        assert canon_funcs[name] == vend_funcs[name], (
            f"DRIFT in {name}: vendored copy differs from pbg_superpowers canonical"
        )


def test_vendored_constants_match():
    if not CANONICAL.is_file():
        pytest.skip(
            f"canonical pbg-superpowers checkout not present at {CANONICAL} — "
            "this drift guard only runs when the sibling repo is checked out "
            "alongside (it is not, in CI)")
    canonical_src = CANONICAL.read_text(encoding="utf-8")
    vendored_src = VENDORED.read_text(encoding="utf-8")

    canon_consts = _extract_constants(canonical_src)
    vend_consts = _extract_constants(vendored_src)

    for k in CONSTANTS:
        assert k in canon_consts, f"{k} missing from canonical"
        assert k in vend_consts, f"{k} missing from vendored"
        assert canon_consts[k] == vend_consts[k], (
            f"DRIFT in constant {k}: vendored={vend_consts[k]!r} canonical={canon_consts[k]!r}"
        )
