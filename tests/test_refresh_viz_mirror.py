"""Drift guard: vendored vivarium_workbench/lib/refresh_viz.py must keep its
``refresh_study_viz`` body identical to the canonical
pbg_superpowers/refresh_viz.py.

Uses the file-read approach (pbg_superpowers is not installed in the dashboard
venv), extracting each function's source by scanning for `def <name>` blocks —
the same technique as tests/test_viz_freshness_mirror.py.
"""
import re
from pathlib import Path

CANONICAL = Path(__file__).parent.parent.parent / "pbg-superpowers" / "pbg_superpowers" / "refresh_viz.py"
VENDORED = Path(__file__).parent.parent / "vivarium_workbench" / "lib" / "refresh_viz.py"

FUNCS = ["refresh_study_viz"]


def _extract_functions(source: str) -> dict[str, str]:
    """Extract top-level function source blocks keyed by name."""
    result = {}
    lines = source.splitlines(keepends=True)
    starts = []
    for i, line in enumerate(lines):
        if re.match(r"^def \w+", line):
            m = re.match(r"^def (\w+)", line)
            starts.append((i, m.group(1)))
    for idx, (start, name) in enumerate(starts):
        end = starts[idx + 1][0] if idx + 1 < len(starts) else len(lines)
        result[name] = "".join(lines[start:end]).rstrip("\n")
    return result


def test_vendored_refresh_viz_matches_canonical():
    assert CANONICAL.is_file(), f"Canonical not found: {CANONICAL}"
    assert VENDORED.is_file(), f"Vendored not found: {VENDORED}"

    canon_funcs = _extract_functions(CANONICAL.read_text(encoding="utf-8"))
    vend_funcs = _extract_functions(VENDORED.read_text(encoding="utf-8"))

    for name in FUNCS:
        assert name in canon_funcs, f"{name} missing from canonical"
        assert name in vend_funcs, f"{name} missing from vendored"
        assert canon_funcs[name] == vend_funcs[name], (
            f"DRIFT in {name}: vendored copy differs from pbg_superpowers canonical"
        )
