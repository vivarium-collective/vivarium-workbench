"""Ban: no core module outside ``lib/backends/`` names a provider's backend (docs/backend-adapters.md §2.4, end of
step P2).

The import graph (import-linter) and the route ban (``test_sms_route_ratchet.py``) cannot see a core module that
compares ``chosen is backends.SMS_LEGACY`` or tests ``kind == "viva-core"``: it imports nothing from the plugin and
spells no SMS route, yet branches on which provider's backend it got. The core asks a backend for a facet
(``chosen.builds``, ``backends.offering("listing")``) or routes by record instead. The registry vocabulary that names
the kinds (``backends._ATTRS``, ``named()``'s viva-core mapping, ``records.KINDS``) lives in ``lib/backends/`` and is
exempt until P3/P4.

Found as hits (any is a regression):

* an attribute ``SMS_LEGACY`` / ``VIVA_CORE`` (``backends.SMS_LEGACY``) or a name imported as one;
* ``getattr(backends, "SMS_LEGACY")`` / ``"VIVA_CORE"`` (or ``hasattr``);
* ``backends._ATTRS`` / ``backends._SENTINELS`` (the registry's private tables);
* a string constant ``"sms-legacy"`` / ``"viva-core"`` (docstrings skipped: prose may name a backend).
"""
from __future__ import annotations

import ast
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "vivarium_workbench"
EXEMPT = PKG / "lib" / "backends"

BANNED_ATTRS = frozenset({"SMS_LEGACY", "VIVA_CORE", "_ATTRS", "_SENTINELS"})
BANNED_KINDS = frozenset({"sms-legacy", "viva-core"})


def _docstrings(tree: ast.AST) -> set[int]:
    """The ids of the docstring constants (module, class, function), which may name a backend in prose."""
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                out.add(id(first.value))
    return out


def _hits(path: Path, root: Path = PKG.parent) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    skip = _docstrings(tree)
    rel = path.relative_to(root)
    out: list[str] = []
    for node in ast.walk(tree):
        where = f"{rel}:{getattr(node, 'lineno', '?')}"
        if isinstance(node, ast.Attribute) and node.attr in BANNED_ATTRS:
            out.append(f"{where} .{node.attr}")
        elif isinstance(node, ast.alias) and node.name in BANNED_ATTRS:
            out.append(f"{where} import {node.name}")
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("getattr", "hasattr")
                and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant)
                and node.args[1].value in BANNED_ATTRS):
            out.append(f"{where} {node.func.id}(..., {node.args[1].value!r})")
        elif (isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in BANNED_KINDS
                and id(node) not in skip):
            out.append(f"{where} {node.value!r}")
    return out


def _core_hits() -> list[str]:
    hits: list[str] = []
    for path in sorted(PKG.rglob("*.py")):
        if EXEMPT in path.parents:
            continue
        hits.extend(_hits(path))
    return hits


def test_the_core_names_no_provider_backend():
    hits = _core_hits()
    assert not hits, (
        "core modules naming a provider's backend (docs/backend-adapters.md §2.4): ask the backend for a facet "
        "(chosen.builds, backends.offering(...)) or route by record instead:\n  " + "\n  ".join(hits))


def test_the_scan_sees_each_form(tmp_path):
    """The scan is only as good as its forms: each banned spelling is a hit, and a docstring is not."""
    src = tmp_path / "m.py"
    src.write_text(
        '"""Mentions "sms-legacy" in prose."""\n'
        "from vivarium_workbench.lib import backends\n"
        "from vivarium_workbench.lib.backends import VIVA_CORE\n"
        "a = backends.SMS_LEGACY\n"
        "b = getattr(backends, 'VIVA_CORE')\n"
        "c = backends._ATTRS\n"
        "d = backends._SENTINELS\n"
        "e = 'viva-core'\n"
        "def f():\n"
        "    '''says viva-core'''\n"
        "    return 'sms-legacy'\n",
        encoding="utf-8")
    found = sorted(_hits(src, tmp_path), key=lambda h: int(h.split(" ", 1)[0].rsplit(":", 1)[1]))
    hits = [h.split(" ", 1)[1] for h in found]
    assert hits == ["import VIVA_CORE", ".SMS_LEGACY", "getattr(..., 'VIVA_CORE')", "._ATTRS", "._SENTINELS",
                    "'viva-core'", "'sms-legacy'"], hits

