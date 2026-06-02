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


def _describe_class(cls: Any) -> str:
    """Formal description for a process/step class, via ``Edge.describe()``.

    The inspector shows each process's standardized formal description. As of
    ``bigraph_schema`` 1.4.x, ``Edge.describe()`` returns the class-level
    ``description`` (a markdown/LaTeX string) and falls back to the docstring.
    We call the *real* ``describe()`` so any subclass override is honored, but
    on an UNINITIALIZED instance (``cls.__new__`` — no ``core``/config needed),
    since ``describe()`` only reads class-level data.

    Graceful fallbacks keep this working on older ``bigraph_schema`` (no
    ``describe()``): prefer the ``description`` attribute, then the docstring.
    """
    try:
        inst = cls.__new__(cls)  # uninitialized — skips __init__/core requirement
        describe = getattr(inst, "describe", None)
        if callable(describe):
            text = describe()
            if isinstance(text, str) and text.strip():
                return text.strip()
    except Exception:
        pass
    desc = getattr(cls, "description", "")
    if isinstance(desc, str) and desc.strip():
        return desc.strip()
    doc = getattr(cls, "__doc__", None)
    return doc.strip() if isinstance(doc, str) else ""


def _doc_for_address(address: str) -> str:
    """Return the formal description for a ``local:<dotted.path>`` address, or ''."""
    if not isinstance(address, str) or not address:
        return ""
    addr = address.split(":", 1)[1] if ":" in address else address
    if "." not in addr:
        return ""  # bare registry name — can't import a dotted path
    module_path, _, cls_name = addr.rpartition(".")
    try:
        mod = importlib.import_module(module_path)
        cls = getattr(mod, cls_name, None)
        if cls is None:
            return ""
        return _describe_class(cls)
    except Exception:
        return ""


def summarize_large_values(node: Any, max_list: int = 40, max_str: int = 2000) -> Any:
    """Return a copy of a composite-state doc with large leaf VALUES summarized.

    A whole-cell `bulk` store is a multi-MB array of thousands of molecules; the
    Composite Explorer only renders structure (it shows `Array(N)` anyway), so
    sending the raw values makes the response ~5 MB and ~1s. Replace any list
    longer than `max_list` with a short ``⟨N items⟩`` string and truncate very
    long strings. Process wiring (port→path lists, all short) and docstrings are
    left intact. Pure — does not mutate its input.
    """
    if isinstance(node, dict):
        return {k: summarize_large_values(v, max_list, max_str) for k, v in node.items()}
    if isinstance(node, (list, tuple)):
        if len(node) > max_list:
            return f"⟨{len(node)} items⟩"
        return [summarize_large_values(v, max_list, max_str) for v in node]
    if isinstance(node, str):
        return node[:max_str] + "…" if len(node) > max_str else node
    if isinstance(node, (bytes, bytearray)):
        return f"⟨{len(node)} bytes⟩"
    # Array-like that isn't a list/tuple/str — e.g. a numpy (structured) array,
    # which is how vEcoli's `bulk` store arrives BEFORE JSON-encoding. Summarize
    # by length; leave small ones for the JSON encoder.
    try:
        n = len(node)
    except TypeError:
        return node
    if n > max_list:
        return f"⟨{n} items⟩"
    return node


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
