"""No SMS terms in the core's Python string constants (docs/backend-adapters.md §2.5, steps P3 C2 and Z).

The server of a workbench without the SMS plugin must not speak SMS: its start-up lines, refusals, run-log hints, route
summaries and error texts use neutral words ("remote backend"). Step P3 C2 made them neutral (Jim, 2026-11-20: wording
B) behind a ``<=`` ratchet; step Z made it a ban.

Counted case-insensitively over the string constants the AST holds (f-string parts included) in
``vivarium_workbench/**/*.py``. Not counted: docstrings and comments, ``lib/backends/`` (the provider seam, which may
name what it adapts), and the alias names in ``backend_config.BACKEND_BASE_ENV_VARS``, which the core keeps only to warn
that a set alias is ignored while no provider is active. Docstrings are not all invisible, though: a route function's
or a pydantic model's docstring becomes an OpenAPI description, so ``test_the_core_openapi_schema_names_no_sms_terms``
checks the core's whole OpenAPI schema (providers off) as well. The browser's twin is ``tests/test_browser_sms_terms.py``.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "vivarium_workbench"

TERMS = ("sms-api", "GovCloud", "SMS_API_BASE", "smsvpctest")

#: Not counted: (file, name of the module-level assignment whose value holds the constants).
EXEMPT_ASSIGNMENTS = {("lib/backend_config.py", "BACKEND_BASE_ENV_VARS")}

#: Not counted: the provider seam (path prefix relative to ``vivarium_workbench/``).
EXEMPT_DIRS = ("lib/backends/",)


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
    return sorted(p for p in ROOT.rglob("*.py")
                  if not p.relative_to(ROOT).as_posix().startswith(EXEMPT_DIRS))


def test_no_sms_terms_in_core_string_constants():
    found = {}
    for path in _files():
        rel = path.relative_to(ROOT).as_posix()
        for term, n in counts_in(path.read_text(encoding="utf-8"), rel).items():
            found[f"{rel}: {term}"] = n
    assert not found, (
        "SMS terms in the core's string constants (use neutral words such as 'remote backend'; the SMS plugin "
        f"owns its own wording; docs/backend-adapters.md §2.5): {found}")


def test_the_scan_covers_the_core_but_not_the_seam():
    rels = {p.relative_to(ROOT).as_posix() for p in _files()}
    assert "lib/backend_config.py" in rels and "api/app.py" in rels
    assert not any(r.startswith("lib/backends/") for r in rels)


def test_the_counter_skips_docstrings_and_the_exempt_aliases_only():
    src = ('"""sms-api module docstring."""\n'
           'BACKEND_BASE_ENV_VARS = ("X", "SMS_API_BASE")\n'
           'OTHER = ("SMS_API_BASE",)\n'
           'def f(e):\n'
           '    """GovCloud."""\n'
           '    return f"sms-api at {e}: GovCloud"  # smsvpctest\n')
    assert counts_in(src, "lib/backend_config.py") == {"SMS_API_BASE": 1, "sms-api": 1, "GovCloud": 1}
    assert counts_in(src, "lib/other.py") == {"SMS_API_BASE": 2, "sms-api": 1, "GovCloud": 1}


def test_the_core_openapi_schema_names_no_sms_terms(monkeypatch):
    """Route and model docstrings become OpenAPI descriptions, which the counter above skips: the core's schema (no
    provider active, so no provider routes) must be clean of SMS terms too. Imports no plugin (core-only safe)."""
    import json

    from vivarium_workbench.lib import backends
    from vivarium_workbench.lib.backends import provider as provider_mod

    monkeypatch.setenv(provider_mod.PROVIDERS_ENV, "none")
    backends.reset_registry()
    try:
        from vivarium_workbench.api.app import create_app

        text = json.dumps(create_app().openapi())
    finally:
        monkeypatch.delenv(provider_mod.PROVIDERS_ENV, raising=False)
        backends.reset_registry()
    found = {t: len(re.findall(re.escape(t), text, re.IGNORECASE)) for t in TERMS}
    assert not {t: n for t, n in found.items() if n}, f"SMS terms in the core's OpenAPI schema: {found}"
