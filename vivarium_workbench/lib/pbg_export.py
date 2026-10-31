"""PBG export utilities.

Provides helpers for serializing a workspace composite to a portable
``.pbg`` JSON document, rewriting short ``local:<Name>`` process addresses
to full import-path form (``local:!<module>.<qualname>``).

Full-path form is required so the sms-api container runner can resolve
process classes via ``importlib`` without needing the workspace's
``build_core()`` registry.

The ``registry`` address form (``local:<module>.<qualname>``, no ``!``) is for a
runner that resolves classes through its own core's link registry by their dotted
names and refuses arbitrary imports -- compose-api's (``docs/backend-compose-api.md``).
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Callable


# ---------------------------------------------------------------------------
# C1: Address rewriter
# ---------------------------------------------------------------------------

#: How a class is named in an exported address: ``import`` is ``local:!<module>.<qualname>``
#: (the runner imports it), ``registry`` is ``local:<module>.<qualname>`` (the runner's core
#: resolves it by that dotted name in its link registry; compose-api accepts only this form).
ADDRESS_FORMS = {"import": "!", "registry": ""}


def rewrite_local_addresses(document: dict, core, *, address_form: str = "import") -> dict:
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

    With ``address_form="registry"`` every class is written ``local:<module>.<qualname>``
    instead, and an address already in ``local:!`` form loses its ``!``.

    The original *document* is never mutated.
    """
    if address_form not in ADDRESS_FORMS:
        raise ValueError(f"address_form must be one of {sorted(ADDRESS_FORMS)}, got {address_form!r}")
    marker = ADDRESS_FORMS[address_form]
    doc = copy.deepcopy(document)
    errors: list[str] = []
    _walk(doc, core, errors, marker)
    if errors:
        raise ValueError(
            "Cannot export non-importable process addresses:\n" + "\n".join(errors)
        )
    return doc


# Runtime-only fields that ``to_document`` leaves on a REALIZED edge node. They
# are rebuilt from ``address`` + ``config`` (and the process/step class) when the
# document is re-instantiated, so a portable ``.pbg`` spec must not carry them:
#   - ``instance``  — the live Process/Step object (serializes to its repr under
#                     ``json.dumps(default=str)``; on reload it's a str and
#                     ``realize_link`` calls ``.interface()`` on it -> AttributeError)
#   - ``_inputs`` / ``_outputs`` — the realized port schema objects (serialize to a
#                     type repr like ``"InPlaceDict(_default=None, ...)"`` that the
#                     bigraph-schema parser can't reparse -> IncompleteParseError)
# Stripping them yields a clean, reparseable spec that ``Composite(document)``
# rebuilds identically. Only these three reserved keys are removed; wiring
# (``inputs``/``outputs``), ``_type``, ``address`` and ``config`` are preserved.
_REALIZED_EDGE_FIELDS = ("instance", "_inputs", "_outputs")


def strip_realized_edge_fields(node: object) -> object:
    """Recursively drop realized-edge runtime fields so the document is a portable spec."""
    if isinstance(node, dict):
        for field in _REALIZED_EDGE_FIELDS:
            node.pop(field, None)
        for value in node.values():
            strip_realized_edge_fields(value)
    elif isinstance(node, list):
        for value in node:
            strip_realized_edge_fields(value)
    return node


def _walk(node: object, core, errors: list[str], marker: str = "!") -> None:
    """Recursively walk *node*; rewrite any ``address`` key in-place."""
    if not isinstance(node, dict):
        return
    if "address" in node:
        _rewrite_address(node, core, errors, marker)
    for v in node.values():
        if isinstance(v, dict):
            _walk(v, core, errors, marker)
        elif isinstance(v, list):
            for item in v:
                _walk(item, core, errors, marker)


def _rewrite_address(node: dict, core, errors: list[str], marker: str = "!") -> None:
    """Rewrite ``node["address"]`` in-place if it is a short local address."""
    addr = node["address"]
    if not isinstance(addr, str):
        return
    if not addr.startswith("local:"):
        return  # non-local protocol — untouched
    rest = addr[len("local:"):]
    if rest.startswith("!"):
        if marker != "!":
            node["address"] = f"local:{marker}{rest[1:]}"  # already a full path: only the form changes
        return  # already full-path form — untouched

    # Dotted full path (``local:<module>.<Class>``): the importable full address
    # missing only the ``!`` marker. A whole-cell composite (v2ecoli.ecoli_baseline
    # builds 30+ processes/steps addressed this way) wires its edges by full dotted
    # path, NOT by the short registry name — and the registry is keyed by short
    # class name ("Metabolism"), so treating the dotted path as a short name always
    # misses. Resolve it by import instead: if ``<module>.<Class>`` imports to a
    # clean, importable class, canonicalize to ``local:!<module>.<qualname>`` (the
    # same form the registry branch below produces). Fall through to the registry
    # lookup only when it is NOT an importable dotted path.
    if "." in rest:
        mod_name, _, cls_name = rest.rpartition(".")
        imported = None
        try:
            import importlib
            imported = getattr(importlib.import_module(mod_name), cls_name, None)
        except Exception:  # noqa: BLE001 — any import failure → not a dotted path, try registry
            imported = None
        if imported is not None:
            module = getattr(imported, "__module__", None)
            qualname = getattr(imported, "__qualname__", None)
            if (module and qualname and module != "__main__"
                    and "<locals>" not in qualname):
                node["address"] = f"local:{marker}{module}.{qualname}"
                return

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

    node["address"] = f"local:{marker}{module}.{qualname}"


# ---------------------------------------------------------------------------
# C2: Composite → .pbg export
# ---------------------------------------------------------------------------

def export_composite_pbg(
    ws_root: "Path | str",
    composite_id: str,
    out_path: "Path | str",
    core=None,
    overrides: "dict | None" = None,
    address_form: str = "import",
    document_hook: "Callable[[dict], dict] | None" = None,
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
    address_form:
        ``import`` (``local:!<module>.<qualname>``) or ``registry``
        (``local:<module>.<qualname>``); see :data:`ADDRESS_FORMS`.
    document_hook:
        Applied to the built document before its addresses are rewritten
        (e.g. to add an emitter by its short registry name).

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
    ws_yaml = ws_root / "workspace.yaml"
    ws_data = (
        yaml.safe_load(ws_yaml.read_text(encoding="utf-8"))
        if ws_yaml.is_file()
        else {}
    )
    pkg = ws_data.get("package_path") or (
        "pbg_" + str(ws_data.get("name", "")).replace("-", "_")
    )
    spec = _get_spec(composite_id)
    if spec is None:
        # A generator registers when its module is imported, and nothing above imports the workspace's
        # composites (build_core need not). Discover them as every other spec-id lookup does
        # (run_runner._generator_entry), the workspace's own package included.
        from process_bigraph.composite_generator import discover_generators

        discover_generators([pkg])
        spec = _get_spec(composite_id)
    if spec is None:
        from vivarium_workbench.lib.composite_lookup import find_composite_path

        path = find_composite_path(ws_root, pkg, composite_id)
        if path is None:
            raise ValueError(f"Composite {composite_id!r} not found in workspace {ws_root}")
        spec = CompositeSpec.from_file(path)

    # Apply the run's parameter overrides (e.g. the Composites-tab run form's
    # n_seeds / duration) so a REMOTE run honors them just like the local path
    # (run_runner build_generator(overrides=…)). CompositeSpec.to_document merges
    # them over the generator's parameter defaults and validates unknown keys.
    document = spec.to_document(overrides=overrides, core=core)
    # Drop realized-edge runtime fields (instance / _inputs / _outputs) FIRST —
    # BEFORE rewrite_local_addresses, whose ``copy.deepcopy(document)`` would
    # otherwise try to deep-copy the live Process/Step ``instance`` objects that
    # ``to_document()`` embeds on realized edges. Some of those instances aren't
    # deep-copyable: v2ecoli's generator-declared default ParquetEmitter (built
    # eagerly inside ``_build_declared_emitter``) holds a ``ThreadPoolExecutor``
    # whose ``_queue.SimpleQueue`` raises ``TypeError: cannot pickle
    # '_queue.SimpleQueue' object`` under deepcopy — crashing the export before
    # any ``.pbg`` is written. Since rewrite is about to throw those fields away
    # anyway, strip them up front so the deepcopy only ever copies plain spec
    # data. See ``strip_realized_edge_fields`` / ``_REALIZED_EDGE_FIELDS``.
    document = strip_realized_edge_fields(document)
    if document_hook is not None:
        document = document_hook(document)
    document = rewrite_local_addresses(document, core, address_form=address_form)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Only a registry-form document is strict: the import form's runners (viva-v1, SMS) have always read the
    # ``str`` of a value JSON has no type for, and their exports must not start failing.
    default = _document_value if address_form == "registry" else str
    out_path.write_text(json.dumps(document, default=default), encoding="utf-8")
    return out_path


def _document_value(o: object) -> object:
    """A document value JSON has no type for, as the value a runner rebuilds it from.

    A numpy array becomes its nested list (bigraph-schema realizes an ``array``
    with ``np.array`` of it) and a numpy scalar its Python scalar. Anything else
    is refused: writing its ``str`` would export a document that no longer runs
    (an array's repr is not an array).
    """
    if isinstance(o, Path):
        return str(o)
    tolist = getattr(o, "tolist", None)
    if callable(tolist) and getattr(getattr(o, "dtype", None), "names", None) is None:
        return tolist()
    raise TypeError(f"cannot export a {type(o).__module__}.{type(o).__qualname__} value into a composite document")


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
