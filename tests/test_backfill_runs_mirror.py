"""Drift guard: vendored vivarium_workbench/lib/backfill_runs.py must keep
``backfill_study_runs`` byte-identical to the canonical
pbg_superpowers/backfill_runs.py.

Only ``backfill_study_runs`` is compared — the dashboard vendors that single
function (not the workspace-wide ``backfill``/CLI which pull extra deps).

Uses the file-read approach (pbg_superpowers is not installed in the dashboard
venv), extracting the function's source by scanning for `def <name>` blocks.
"""
import re
from pathlib import Path

CANONICAL = Path(__file__).parent.parent.parent / "pbg-superpowers" / "pbg_superpowers" / "backfill_runs.py"
VENDORED = Path(__file__).parent.parent / "vivarium_workbench" / "lib" / "backfill_runs.py"

FUNCS = ["backfill_study_runs"]


def _extract_functions(source: str) -> dict[str, str]:
    """Extract top-level function source blocks keyed by name."""
    lines = source.splitlines(keepends=True)
    starts = []
    for i, line in enumerate(lines):
        m = re.match(r"^def (\w+)", line)
        if m:
            starts.append((i, m.group(1)))
    result = {}
    for idx, (start, name) in enumerate(starts):
        end = starts[idx + 1][0] if idx + 1 < len(starts) else len(lines)
        result[name] = "".join(lines[start:end]).rstrip("\n")
    return result


def test_vendored_backfill_runs_matches_canonical():
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
