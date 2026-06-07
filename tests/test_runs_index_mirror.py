"""Drift guard: vendored vivarium_dashboard/lib/runs_index.py must stay
identical to the canonical pbg_superpowers/runs_index.py for the functions
the dashboard relies on.

``emitter_type_of`` + ``_all_runs`` are compared byte-for-byte. ``list_all_runs``
is EXCLUDED: the dashboard's ``lib/`` has no ``backfill_runs`` module, so the
vendored copy wraps that import in try/except (see runs_index.py header).

Uses the file-read approach (pbg_superpowers is not installed in the dashboard
venv), extracting each function's source by scanning for `def <name>` blocks.
"""
import re
from pathlib import Path

CANONICAL = Path(__file__).parent.parent.parent / "pbg-superpowers" / "pbg_superpowers" / "runs_index.py"
VENDORED = Path(__file__).parent.parent / "vivarium_dashboard" / "lib" / "runs_index.py"

# list_all_runs intentionally excluded — see module docstring.
FUNCS = ["emitter_type_of", "_all_runs"]


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


def test_vendored_runs_index_matches_canonical():
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
