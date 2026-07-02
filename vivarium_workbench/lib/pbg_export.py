"""PBG export utilities.

Provides helpers for serializing a workspace composite to a portable
``.pbg`` JSON document, rewriting short ``local:<Name>`` process addresses
to full import-path form (``local:!<module>.<qualname>``).

Full-path form is required so the sms-api container runner can resolve
process classes via ``importlib`` without needing the workspace's
``build_core()`` registry.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# C1: Address rewriter
# ---------------------------------------------------------------------------

def rewrite_local_addresses(document: dict, core) -> dict:
    """Return a deep-copy of *document* with short ``local:<Name>`` addresses
    rewritten to ``local:!<module>.<qualname>``.

    Rules
    -----
    - ``local:!...`` (already full-path) — left untouched.
    - Non-``local:`` protocols (e.g. ``pkg:mod.Z``) — left untouched.
    - ``local:<Name>`` — look up *Name* in ``core.link_registry`` (a plain
      dict), compute ``f"local:!{cls.__module__}.{cls.__qualname__}"``.

    Raises :exc:`ValueError` (listing all problems) if any short name is
    unresolvable, lives in ``__main__``, or has ``<locals>`` in its
    ``__qualname__`` (such classes cannot be imported by dotted path).

    The original *document* is never mutated.
    """
    doc = copy.deepcopy(document)
    errors: list[str] = []
    _walk(doc, core, errors)
    if errors:
        raise ValueError(
            "Cannot export non-importable process addresses:\n" + "\n".join(errors)
        )
    return doc


def _walk(node: object, core, errors: list[str]) -> None:
    """Recursively walk *node*; rewrite any ``address`` key in-place."""
    if not isinstance(node, dict):
        return
    if "address" in node:
        _rewrite_address(node, core, errors)
    for v in node.values():
        if isinstance(v, dict):
            _walk(v, core, errors)
        elif isinstance(v, list):
            for item in v:
                _walk(item, core, errors)


def _rewrite_address(node: dict, core, errors: list[str]) -> None:
    """Rewrite ``node["address"]`` in-place if it is a short local address."""
    addr = node["address"]
    if not isinstance(addr, str):
        return
    if not addr.startswith("local:"):
        return  # non-local protocol — untouched
    rest = addr[len("local:"):]
    if rest.startswith("!"):
        return  # already full-path form — untouched

    # Short name: look up in registry
    cls = core.link_registry.get(rest)
    if cls is None:
        errors.append(f"'{rest}' not found in core.link_registry")
        return

    module = getattr(cls, "__module__", None)
    qualname = getattr(cls, "__qualname__", None)

    if module is None or qualname is None:
        errors.append(f"'{rest}' -> {cls!r} has no __module__/__qualname__")
        return
    if module == "__main__":
        errors.append(
            f"'{rest}' -> {cls!r} lives in __main__ (not importable by dotted path)"
        )
        return
    if "<locals>" in qualname:
        errors.append(
            f"'{rest}' -> qualname '{qualname}' contains <locals> (not importable)"
        )
        return

    node["address"] = f"local:!{module}.{qualname}"


# ---------------------------------------------------------------------------
# C2: Composite → .pbg export
# ---------------------------------------------------------------------------

def export_composite_pbg(
    ws_root: "Path | str",
    composite_id: str,
    out_path: "Path | str",
    core=None,
) -> Path:
    """Build a named composite, rewrite its process addresses, and write JSON.

    Parameters
    ----------
    ws_root:
        Workspace root directory (must contain ``workspace.yaml``).
    composite_id:
        Composite identifier — either a generator ref (``<module>.<name>``)
        or a static spec id (``<pkg>.composites.<stem>``).
    out_path:
        Destination path for the ``.pbg`` JSON file.
    core:
        Optional pre-built process-bigraph core.  If *None*, the workspace's
        own ``build_core()`` is called after ensuring *ws_root* is on
        ``sys.path``.

    Returns
    -------
    Path
        *out_path* (resolved).
    """
    import yaml  # already a dep
    from process_bigraph.composite_spec import CompositeSpec
    from process_bigraph.composite_spec import get as _get_spec

    ws_root = Path(ws_root)
    out_path = Path(out_path)

    # Ensure workspace package is importable
    ws_str = str(ws_root)
    if ws_str not in sys.path:
        sys.path.insert(0, ws_str)

    # Build core from workspace if not supplied
    if core is None:
        core = _build_core_for_workspace(ws_root)

    # Resolve spec: generator branch first, then static-file branch
    spec = _get_spec(composite_id)
    if spec is None:
        from vivarium_workbench.lib.composite_lookup import find_composite_path

        ws_yaml = ws_root / "workspace.yaml"
        ws_data = (
            yaml.safe_load(ws_yaml.read_text(encoding="utf-8"))
            if ws_yaml.is_file()
            else {}
        )
        pkg = ws_data.get("package_path") or (
            "pbg_" + str(ws_data.get("name", "")).replace("-", "_")
        )
        path = find_composite_path(ws_root, pkg, composite_id)
        if path is None:
            raise ValueError(f"Composite {composite_id!r} not found in workspace {ws_root}")
        spec = CompositeSpec.from_file(path)

    document = spec.to_document(core=core)
    document = rewrite_local_addresses(document, core)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(document, default=str), encoding="utf-8")
    return out_path


def _build_core_for_workspace(ws_root: Path):
    """Import and call the workspace's own ``build_core()``."""
    import yaml

    ws_yaml = ws_root / "workspace.yaml"
    ws_data = (
        yaml.safe_load(ws_yaml.read_text(encoding="utf-8")) if ws_yaml.is_file() else {}
    )
    pkg = ws_data.get("package_path") or (
        "pbg_" + str(ws_data.get("name", "")).replace("-", "_")
    )
    # Import the workspace package's core module
    import importlib
    core_mod = importlib.import_module(f"{pkg}.core")
    return core_mod.build_core()
