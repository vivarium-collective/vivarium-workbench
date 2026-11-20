"""SMS terms in the core's Python string constants may only shrink (docs/backend-adapters.md §2.5, step P3 C2).

The server of a workbench without the SMS plugin must not speak SMS: its start-up lines, refusals, run-log hints, route
summaries and error texts use neutral words ("remote backend"). Step P3 C2 made them neutral (Jim, 2026-11-20: wording
B); this ratchet stops new ones arriving meanwhile. Each (file, term) count may only go down (``<=``), so a PR that
removes terms need not edit this file; a file not listed here may have none. Step P3 Z turns it into a ban.

Counted case-insensitively over the string constants the AST holds (f-string parts included) in
``vivarium_workbench/**/*.py``. Not counted: docstrings and comments (not user-visible), ``lib/backends/`` (the
backend registry, which names its providers), and the alias names in ``backend_config.BACKEND_BASE_ENV_VARS``, which the
core keeps only to warn that a set alias is ignored while no provider is active. The browser's twin is
``tests/test_browser_sms_terms.py``.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "vivarium_workbench"

TERMS = ("sms-api", "GovCloud", "SMS_API_BASE", "smsvpctest")

#: Not counted: (file, name of the module-level assignment whose value holds the constants).
EXEMPT_ASSIGNMENTS = {("lib/backend_config.py", "BACKEND_BASE_ENV_VARS")}

#: The counts after step P3 C2's rewording (2026-11-20; the base had 15 across api/app.py, cli.py, backend_config.py,
#: preflight.py, run_runner.py and startup.py). Every file is clean now, so nothing is listed; keep it that way.
BASELINE: dict[str, dict[str, int]] = {}


def _docstring_ids(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                ids.add(id(body[0].value))
    return ids


def _exempt_ids(tree: ast.Module, rel: str) -> set[int]:
    ids = set()
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and (rel, t.id) in EXEMPT_ASSIGNMENTS for t in targets) and node.value:
                ids.update(id(n) for n in ast.walk(node.value))
    return ids


def counts_in(source: str, rel: str) -> dict[str, int]:
    tree = ast.parse(source)
    skip = _docstring_ids(tree) | _exempt_ids(tree, rel)
    found: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            for term in TERMS:
                n = len(re.findall(re.escape(term), node.value, re.IGNORECASE))
                if n:
                    found[term] = found.get(term, 0) + n
    return found


def _files() -> list[Path]:
    return sorted(p for p in ROOT.rglob("*.py") if "lib/backends/" not in p.relative_to(ROOT).as_posix())


def test_sms_terms_in_core_string_constants_only_shrink():
    grown = {}
    for path in _files():
        rel = path.relative_to(ROOT).as_posix()
        allowed = BASELINE.get(rel, {})
        for term, n in counts_in(path.read_text(encoding="utf-8"), rel).items():
            if n > allowed.get(term, 0):
                grown[f"{rel}: {term}"] = f"{n} > {allowed.get(term, 0)}"
    assert not grown, (
        "SMS terms grew in the core's string constants (use neutral words such as 'remote backend'; the SMS plugin "
        f"owns its own wording; docs/backend-adapters.md §2.5): {grown}")


def test_the_counter_skips_docstrings_and_the_exempt_aliases_only():
    src = ('"""sms-api module docstring."""\n'
           'BACKEND_BASE_ENV_VARS = ("X", "SMS_API_BASE")\n'
           'OTHER = ("SMS_API_BASE",)\n'
           'def f(e):\n'
           '    """GovCloud."""\n'
           '    return f"sms-api at {e}: GovCloud"  # smsvpctest\n')
    assert counts_in(src, "lib/backend_config.py") == {"SMS_API_BASE": 1, "sms-api": 1, "GovCloud": 1}
    assert counts_in(src, "lib/other.py") == {"SMS_API_BASE": 2, "sms-api": 1, "GovCloud": 1}
