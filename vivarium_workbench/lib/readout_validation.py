"""readout_validation.py — structure introspection + author-time readout validation.

Sub-project #4 of the Readout-coordination design
(``docs/specs/2026-06-09-readout-coordination-design.md`` §5.4).

Two public functions:

    available_observables(core, state, schema=None) -> dict
        Enumerate the emittable leaf paths a built composite exposes (via
        process-bigraph's ``collect_input_ports``) plus the id/name catalogs
        recoverable from the structure (``_labels`` on array / ``LabeledArray``
        ports).  Returns ``{"leaves": [...], "catalogs": {observable: [...]}}``.

    validate_readouts(spec, core=None, state=None, *, schema=None,
                      available=None) -> list[dict]
        For each readout in ``spec["readouts"]``, resolve it via
        ``readout_resolver`` and check its selector against the available
        structure.  Returns one ``{name, status, detail}`` per readout, where
        ``status`` is one of ``ok | unresolved | not_in_structure | aspirational``.

NEVER-FABRICATE policy: a selector that references something the structure does
not expose is *flagged* (``not_in_structure``), never silently accepted; a
selector that cannot be checked from the static structure alone (e.g. ``bulk``
ids, which are resolved at run time) is flagged ``aspirational`` — not invented
into existence.
"""
from __future__ import annotations

from typing import Any

from viva_superpowers.readout_resolver import (
    ResolvedReadout,
    UnresolvedReadout,
    resolve_readout,
)

# Default emit observable for catalog-backed index_by types, mirroring
# viva_emitters.RunReader._SELECT_TYPE_MAP so validation and resolution agree
# on where a given id type lives.
_TYPE_OBSERVABLE: dict[str, str] = {
    "bulk_id": "bulk",
    "monomer_id": "listeners.monomer_counts",
}

# Id types whose catalog is only materialised at run time (not recoverable from
# the static composite structure).  bulk ids live in the run-time ``bulk__id``
# column, never in the schema.
_RUNTIME_CATALOG_TYPES: frozenset[str] = frozenset({"bulk_id"})


# ---------------------------------------------------------------------------
# Structure introspection
# ---------------------------------------------------------------------------

def _leaf_paths(state: dict) -> list[str]:
    """Return dotted emittable leaf paths from a built composite ``state``.

    Reuses process-bigraph's ``collect_input_ports`` (which skips Process/Step
    instances and schema keys, treating lists/scalars as leaves), then joins
    each path with ``.`` to match the dotted convention used by study readouts
    (``listeners.monomer_counts``, ``listeners.mass.cell_mass``).
    """
    from process_bigraph.emitter import collect_input_ports

    ports = collect_input_ports(state)
    return sorted(".".join(path) for path in ports.values())


def _labels_of(core: Any, node: Any) -> list | None:
    """Best-effort: return the ``_labels`` catalog for a schema ``node``.

    A ``LabeledArray``-typed port carries its element name catalog as
    ``_labels``.  We look for it three ways, in order:

      1. ``node["_labels"]`` directly on the schema dict (inline labels).
      2. ``core.access(node["_type"])._labels`` — a registered labeled type.
      3. ``core.access(node)._labels`` — schema dict accepted by ``access``.

    Returns the labels list, or ``None`` when the node is not a labeled array.
    """
    if isinstance(node, dict) and isinstance(node.get("_labels"), list):
        return list(node["_labels"])

    if core is None:
        return None

    candidates = []
    if isinstance(node, dict) and isinstance(node.get("_type"), str):
        candidates.append(node["_type"])
    if isinstance(node, (str, dict)):
        candidates.append(node)

    for cand in candidates:
        try:
            accessed = core.access(cand)
        except Exception:
            continue
        labels = getattr(accessed, "_labels", None)
        if labels is None and isinstance(accessed, dict):
            labels = accessed.get("_labels")
        if isinstance(labels, list):
            return list(labels)
    return None


def _walk_catalogs(core: Any, schema: Any, path: tuple[str, ...], out: dict) -> None:
    """Recursively collect ``{dotted_path: labels}`` for labeled ports in ``schema``."""
    if not isinstance(schema, dict):
        return

    labels = _labels_of(core, schema)
    if labels is not None and path:
        out[".".join(path)] = labels
        return  # a labeled leaf — do not descend into its type params

    for key, value in schema.items():
        if key.startswith("_"):
            continue
        _walk_catalogs(core, value, path + (key,), out)


def available_observables(
    core: Any,
    state: dict,
    schema: Any | None = None,
) -> dict:
    """Enumerate what a built composite exposes.

    Args:
        core:   the process-bigraph type core (for resolving ``_labels`` on
                registered labeled-array types).  May be ``None`` when the
                schema carries inline ``_labels``.
        state:  the built composite state tree (nested dict of values).
        schema: optional composition schema mirroring ``state``; walked for
                ``_labels`` catalogs.  When omitted, ``catalogs`` is empty
                (only run-time resolution remains available).

    Returns:
        ``{"leaves": [dotted paths...], "catalogs": {observable: [labels...]}}``.
        ``leaves`` are the emittable scalar/array leaf paths; ``catalogs`` maps
        an array observable's dotted path to its static element-name catalog.
        ``bulk`` is intentionally absent from ``catalogs`` — ``bulk__id`` is a
        run-time column, not part of the static structure.
    """
    leaves = _leaf_paths(state)
    catalogs: dict[str, list] = {}
    if schema is not None:
        _walk_catalogs(core, schema, (), catalogs)
    return {"leaves": leaves, "catalogs": catalogs}


# ---------------------------------------------------------------------------
# Readout validation against structure
# ---------------------------------------------------------------------------

def _result(name: str, status: str, detail: str) -> dict:
    return {"name": name, "status": status, "detail": detail}


def _in_structure(path: str, leaves: list[str]) -> bool:
    """True if ``path`` is an emittable leaf OR an existing branch above one."""
    if path in leaves:
        return True
    prefix = path + "."
    return any(l.startswith(prefix) for l in leaves)


def _parent_in_structure(path: str, leaves: list[str]) -> bool:
    """True if ``path``'s immediate parent exists in the structure.

    This is what separates a *fabricated* selector from one merely pointing at a
    store that is **empty at build time**.  ``available_observables`` walks the
    BUILT state, so a store written only in a Process's ``next_update`` (e.g. a
    coupler writing ``environment.external_concentrations`` each tick) has no
    leaves yet and would otherwise look identical to a typo.  Its PARENT node,
    however, is present in both cases -- whereas a fabricated subtree
    (``agents.0.metabolism.*`` where the process key is ``ecoli-metabolism``) has
    no parent either.  Build-time absence is not fabrication; absence all the way
    up the path is.
    """
    parent = path.rpartition(".")[0]
    if not parent:
        return True  # a bare top-level name has no parent to check
    return _in_structure(parent, leaves)


def _validate_element(
    name: str,
    index_by: dict,
    observable: str | None,
    available: dict,
) -> dict:
    """Validate one element selector ``{type, value}`` against ``available``."""
    leaves: list[str] = available.get("leaves", [])
    catalogs: dict[str, list] = available.get("catalogs", {})

    itype = index_by.get("type")
    value = index_by.get("value")

    # --- literal_index: observable must be a known leaf, index in range ----
    if itype == "literal_index":
        obs = observable
        if obs is None:
            return _result(
                name, "not_in_structure",
                "literal_index selector has no observable to index into",
            )
        if obs not in leaves:
            return _result(
                name, "not_in_structure",
                f"observable {obs!r} is not an emittable leaf path",
            )
        cat = catalogs.get(obs)
        if cat is None:
            return _result(
                name, "aspirational",
                f"index {value} into {obs!r}: range unverifiable (no static "
                "catalog persisted for this observable)",
            )
        if not isinstance(value, int) or not (0 <= value < len(cat)):
            return _result(
                name, "not_in_structure",
                f"index {value} out of range for {obs!r} (catalog length {len(cat)})",
            )
        return _result(
            name, "ok",
            f"index {value} → {cat[value]!r} in {obs!r}",
        )

    # --- run-time catalog types (bulk_id): not in static structure ---------
    if itype in _RUNTIME_CATALOG_TYPES:
        obs = _TYPE_OBSERVABLE.get(itype, observable)
        if obs in catalogs:  # someone persisted the catalog statically
            if value in catalogs[obs]:
                return _result(name, "ok", f"{value!r} in static {obs!r} catalog")
            return _result(
                name, "not_in_structure",
                f"{value!r} not in static {obs!r} catalog",
            )
        # never-fabricate: the run-time catalog cannot verify the VALUE, but the
        # readout's declared store_path is ordinary structure and must exist.
        # Without this, a readout carrying `index_by: {type: bulk_id}` gets a free
        # pass on a fabricated store_path -- the catalog-backed branch below
        # already guards this via `if obs in leaves`. Only flag when the path is
        # absent AND its parent is too, so a store that is legitimately empty at
        # build time is not mistaken for a typo (see _parent_in_structure).
        if (
            observable
            and not _in_structure(observable, leaves)
            and not _parent_in_structure(observable, leaves)
        ):
            return _result(
                name, "not_in_structure",
                f"{itype} {value!r}: observable {observable!r} is not exposed by "
                "the composite (neither it nor its parent is in the structure)",
            )
        return _result(
            name, "aspirational",
            f"{itype} {value!r}: resolved from the run-time {obs!r} catalog, "
            "not verifiable against static structure",
        )

    # --- catalog-backed id types (monomer_id, rna_id, tf_id, listener_id) --
    obs = _TYPE_OBSERVABLE.get(itype, observable)
    if obs is None:
        return _result(
            name, "aspirational",
            f"{itype} {value!r}: no observable to locate its catalog",
        )
    cat = catalogs.get(obs)
    if cat is not None:
        if value in cat:
            return _result(name, "ok", f"{value!r} in {obs!r} catalog")
        return _result(
            name, "not_in_structure",
            f"{value!r} not in {obs!r} catalog (length {len(cat)})",
        )
    if obs in leaves:
        return _result(
            name, "aspirational",
            f"{itype} {value!r}: {obs!r} is exposed but its id catalog is not "
            "in the static structure (run-time resolution required)",
        )
    return _result(
        name, "not_in_structure",
        f"{itype} {value!r}: observable {obs!r} is not exposed by the composite",
    )


def _validate_resolved(r: ResolvedReadout, available: dict) -> dict:
    """Validate a ResolvedReadout against the available structure."""
    leaves: list[str] = available.get("leaves", [])

    if r.kind == "scalar":
        if r.observable in leaves:
            return _result(r.name, "ok", f"scalar {r.observable!r} is an emittable leaf")
        return _result(
            r.name, "not_in_structure",
            f"scalar {r.observable!r} is not an emittable leaf path",
        )

    if r.kind == "element" and r.index_by is not None:
        return _validate_element(r.name, r.index_by, r.observable, available)

    if r.kind == "expression":
        operands = r.operand_ids or []
        sub = []
        for op in operands:
            ib = op.get("index_by", {})
            itype = ib.get("type")
            if itype == "scalar":
                # dotted-path operand: validate as a leaf
                tok = ib.get("value")
                ok = tok in leaves
                sub.append(_result(
                    r.name, "ok" if ok else "not_in_structure", f"operand {tok!r}"
                ))
            else:
                sub.append(_validate_element(r.name, ib, r.observable, available))
        statuses = {s["status"] for s in sub}
        details = "; ".join(f"{op.get('token')}: {s['status']}"
                            for op, s in zip(operands, sub))
        if "not_in_structure" in statuses:
            return _result(r.name, "not_in_structure",
                           f"expression operand(s) not in structure — {details}")
        if "aspirational" in statuses:
            return _result(r.name, "aspirational",
                           f"expression resolved but run-time verified — {details}")
        return _result(r.name, "ok", f"all operands in structure — {details}")

    # Resolved but an unexpected shape — surface, never fabricate.
    return _result(
        r.name, "aspirational",
        f"resolved as kind={r.kind!r} with no structural selector to verify",
    )


def validate_readouts(
    spec: dict,
    core: Any = None,
    state: dict | None = None,
    *,
    schema: Any | None = None,
    available: dict | None = None,
) -> list[dict]:
    """Validate every readout in ``spec`` against the composite structure.

    Args:
        spec:      parsed study.yaml dict; reads ``spec["readouts"]``.
        core:      process-bigraph type core (for ``_labels`` resolution).
        state:     built composite state tree.
        schema:    optional composition schema (for ``_labels`` catalogs).
        available: a pre-computed ``available_observables`` dict; when given,
                   ``core``/``state``/``schema`` are ignored.  This is the
                   headless-friendly path used by unit tests.

    Returns:
        One ``{name, status, detail}`` per readout.  ``status`` ∈
        ``{ok, unresolved, not_in_structure, aspirational}``.
    """
    if available is None:
        if state is None:
            raise ValueError(
                "validate_readouts requires either `available=` or `state=`"
            )
        available = available_observables(core, state, schema)

    readouts: list[dict] = spec.get("readouts") or []
    results: list[dict] = []
    for i, ro in enumerate(readouts):
        name = ro.get("name", f"readout_{i}")
        resolved = resolve_readout(ro)
        if isinstance(resolved, UnresolvedReadout):
            results.append(_result(name, "unresolved", resolved.reason))
        else:
            results.append(_validate_resolved(resolved, available))
    return results
