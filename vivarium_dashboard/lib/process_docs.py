"""Attach process docstrings to a composite-state document.

The Composite Explorer's inspector shows a process "Description" sourced from the
process class docstring. A composite-state document carries each process's
``address`` (e.g. ``local:v2ecoli.steps.listeners.mass_listener.PostDivisionMassListener``)
but not its docstring — the docstring lives on the Python class. This module
walks a composite doc and, for each process/step node, resolves the class from
its address and sets ``node['doc']`` to the class docstring so the frontend
(convert.ts → InspectorPanel) can display it.

Resolution is best-effort: a full dotted ``local:<module>.<Class>`` address is
imported via importlib; bare registry-name addresses (e.g. ``local:SQLiteEmitter``)
or unresolvable ones are skipped (no ``doc`` set). All failures are swallowed so
this can never break the composite-state response.
"""
from __future__ import annotations

import importlib
from typing import Any


def _doc_for_address(address: str) -> str:
    """Return the (stripped) class docstring for a ``local:<dotted.path>`` address, or ''."""
    if not isinstance(address, str) or not address:
        return ""
    addr = address.split(":", 1)[1] if ":" in address else address
    if "." not in addr:
        return ""  # bare registry name — can't import a dotted path
    module_path, _, cls_name = addr.rpartition(".")
    try:
        mod = importlib.import_module(module_path)
        cls = getattr(mod, cls_name, None)
        doc = getattr(cls, "__doc__", None)
        return doc.strip() if isinstance(doc, str) else ""
    except Exception:
        return ""


def attach_process_docs(doc: Any) -> Any:
    """Walk a composite-state document in place, attaching ``doc`` to each process.

    Returns the same object for convenience. Safe to call on any JSON-ish value.
    """
    _cache: dict[str, str] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("_type") in ("process", "step") and "doc" not in node:
                addr = node.get("address", "")
                if addr not in _cache:
                    _cache[addr] = _doc_for_address(addr)
                d = _cache[addr]
                if d:
                    node["doc"] = d
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    try:
        walk(doc)
    except Exception:
        pass
    return doc
