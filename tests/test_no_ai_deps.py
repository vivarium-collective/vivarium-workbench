"""Architecture gate: the dashboard imports NO AI/LLM SDK.

vivarium-dashboard is the AI-free UI+data layer — pure rendering of
deterministic functions. ALL AI assistance lives in the pbg-superpowers
``/pbg-*`` skills, never in the dashboard. This test statically scans every
module under ``vivarium_workbench/`` and fails if any of them imports a known
LLM/AI SDK, so the boundary can't erode silently.

Note: importing ``pbg_superpowers`` (its DETERMINISTIC modules) is allowed —
the dashboard may call pbg-superpowers' deterministic functions. What is
forbidden is pulling an LLM provider SDK into the dashboard process.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Top-level import roots that would pull an LLM/AI provider into the dashboard.
# Matched against the FIRST dotted component of every import (plus a prefix
# match for the langchain_* family).
_FORBIDDEN_ROOTS = {
    "anthropic",
    "openai",
    "cohere",
    "mistralai",
    "ollama",
    "litellm",
    "llama_index",
    "replicate",
    "vertexai",
    "transformers",
    "google_generativeai",  # `import google.generativeai` → see the dotted check below
}
_FORBIDDEN_PREFIXES = ("langchain",)  # langchain, langchain_core, langchain_openai, …
# Dotted module paths that are forbidden even though their root is innocuous.
_FORBIDDEN_DOTTED = ("google.generativeai",)

_PKG_ROOT = Path(__file__).resolve().parent.parent / "vivarium_workbench"


def _module_files() -> list[Path]:
    return sorted(_PKG_ROOT.rglob("*.py"))


def _imported_names(tree: ast.AST) -> set[str]:
    """Every fully-dotted module name referenced by an import in ``tree``."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:  # absolute imports only
                names.add(node.module)
    return names


def _violation(dotted: str) -> bool:
    root = dotted.split(".", 1)[0]
    if root in _FORBIDDEN_ROOTS:
        return True
    if any(root == p or root.startswith(p + "_") for p in _FORBIDDEN_PREFIXES):
        return True
    if any(dotted == d or dotted.startswith(d + ".") for d in _FORBIDDEN_DOTTED):
        return True
    return False


def test_dashboard_imports_no_ai_sdk():
    offenders: list[str] = []
    for path in _module_files():
        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except SyntaxError as e:  # pragma: no cover — a parse error is its own failure
            pytest.fail(f"{path} failed to parse: {e}")
        for dotted in _imported_names(tree):
            if _violation(dotted):
                offenders.append(f"{path.relative_to(_PKG_ROOT.parent)} imports {dotted!r}")
    assert not offenders, (
        "vivarium-dashboard must stay AI-free (all AI assistance lives in the "
        "pbg-superpowers skills). Forbidden LLM/AI SDK imports found:\n  "
        + "\n  ".join(offenders)
    )


def test_gate_detects_a_planted_violation():
    """The gate actually fires — a sanity check on the detector itself."""
    assert _violation("anthropic")
    assert _violation("anthropic.types")
    assert _violation("langchain_openai")
    assert _violation("google.generativeai")
    assert not _violation("pbg_superpowers")
    assert not _violation("pbg_superpowers.linkage_index")
    assert not _violation("yaml")
