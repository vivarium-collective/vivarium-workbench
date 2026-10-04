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
import re
from typing import Any

# A Composite Process built via ``type(name, bases, dict)`` (and some builtins)
# has no docstring of its own, so ``cls.__doc__`` falls through to an inherited
# builtin docstring — junk like "type(name, bases, dict, **kwds) -> a new type".
# Never surface those as a process description.
_JUNK_DOC = re.compile(
    r"^\s*(type\(name, bases|Create and return a new object|"
    r"The base class of the class hierarchy|str\(object=|int\(\[|"
    r"dict\(\)|list\(\)|tuple\(\)|object\(\)|Built-in)")


def _is_junk_doc(doc: str) -> bool:
    return bool(doc) and bool(_JUNK_DOC.match(doc))


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
    doc = doc.strip() if isinstance(doc, str) else ""
    return "" if _is_junk_doc(doc) else doc


def _contract_for_class(cls: Any) -> dict | None:
    """The structured ``.contract`` a process advertises (summary + governing
    equations + symbol key), expanded to the loom's ``_contract`` shape so the
    process CARD can render it. ``None`` when the class declares no contract."""
    c = getattr(cls, "contract", None)
    # A structured ProcessContract (bigraph_schema.contract) exposes a to_dict()
    # accessor with the same summary/description/math/symbols fields. Normalize it
    # to the dict shape below — duck-typed (no import) so this stays import-light
    # and back-compatible with a plain-dict contract.
    if c is not None and not isinstance(c, dict) and hasattr(c, "to_dict"):
        try:
            c = c.to_dict()
        except Exception:
            c = None
    if not isinstance(c, dict) or not c.get("summary"):
        return None
    # Forward the FULL contract to the loom's `_contract` shape. The Inspector
    # renders per-port semantics (contract.inputs[port]/outputs[port]),
    # assumptions and references, and uses `status` for draft detection — so a
    # ProcessContract that declares them must have them carried through, not
    # dropped. `config` is forwarded too (the Contract type carries it). Keys the
    # contract omits default to empty on both sides (contract.ts `blankContract`).
    return {
        "summary": c["summary"],
        "description": c.get("description") or c["summary"],
        "status": c.get("status", ""),
        "math": list(c.get("math", [])),
        "symbols": dict(c.get("symbols", {})),
        "inputs": dict(c.get("inputs", {})),
        "outputs": dict(c.get("outputs", {})),
        "config": dict(c.get("config", {})),
        "assumptions": list(c.get("assumptions", [])),
        "references": list(c.get("references", [])),
    }


def _resolve_class(address: str) -> Any:
    """Import the class for a ``local:<dotted.path>`` address, or None. Bare
    registry-name addresses (``local:DynamicFBA``) can't be imported here — the
    env-worker resolves those against the workspace core."""
    if not isinstance(address, str) or not address:
        return None
    addr = address.split(":", 1)[1] if ":" in address else address
    # Strip a leading "!" bigraph serialization marker (e.g.
    # "!v2ecoli.cell_shape.ShapeStep") so the dotted path imports.
    addr = addr.lstrip("!")
    if "." not in addr:
        return None
    module_path, _, cls_name = addr.rpartition(".")
    try:
        return getattr(importlib.import_module(module_path), cls_name, None)
    except Exception:
        return None


def _doc_for_address(address: str) -> str:
    """Return the formal description for a ``local:<dotted.path>`` address, or ''."""
    cls = _resolve_class(address)
    return _describe_class(cls) if cls is not None else ""


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
    _cache: dict[str, tuple[str, dict | None]] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("_type") in ("process", "step"):
                addr = node.get("address", "")
                if addr not in _cache:
                    cls = _resolve_class(addr)
                    _cache[addr] = (
                        _describe_class(cls) if cls is not None else "",
                        _contract_for_class(cls) if cls is not None else None,
                    )
                doc, contract = _cache[addr]
                if doc and "doc" not in node:
                    node["doc"] = doc
                # Surface the process's structured contract on the CARD (governing
                # equations), live — the same contract the static figures bake in.
                if contract and "_contract" not in node:
                    node["_contract"] = contract
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


def attach_process_docs_via_worker(ws_root: Any, doc: Any, spec_id: Any = None) -> Any:
    """Route :func:`attach_process_docs` decoration to the workspace's env worker,
    so the HTTP process imports no workspace Python to read process docstrings
    (env-worker method ``attach_process_docs{document}``; the workbench passes the
    already-resolved doc inline, §11). **Soft-degrade**: decoration is optional, so
    if the worker is unavailable, return the doc undecorated. ``summarize_large_values``
    stays a separate in-process call where needed — it is pure (no workspace import).

    ``spec_id`` (the generator ref) is optional; when given, the worker builds a
    core from that spec's ``core_extensions`` so bare registry-name addresses
    (``local:EcoliWCM``) resolve — needed to flag Composite Processes + read port
    types on a committed-artifact doc that was not live-built."""
    from vivarium_workbench.lib.env_worker_pool import get_pool
    params: dict = {"document": doc}
    if spec_id:
        params["ref"] = spec_id
    try:
        r = get_pool().call(ws_root, "attach_process_docs", params)
        return r["document"] if isinstance(r, dict) and "document" in r else doc
    except Exception:  # noqa: BLE001 — decoration is best-effort; never break the request
        return doc
