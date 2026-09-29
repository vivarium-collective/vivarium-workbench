"""Workspace investigation -> process-bigraph investigation *document*.

Layer-4 "run this study / continue from here" is built on process-bigraph's
``trigger`` (pull-or-compute), which operates on a **pbg investigation
document**: a flat ``{member_name: region}`` mapping where each region carries
``_study`` metadata ``{id, config, inputs: [{artifact, from}], kind}`` plus a
placeholder ``sim`` compute node. ``trigger`` reads that metadata to compute
each member's content address, decide pull-vs-compute-vs-prune, and return the
report the UI renders.

A workspace, though, stores its investigation as ``investigation.yaml``
(``members:``) + one ``studies/<slug>/study.yaml`` per member, each declaring
its execution interface (``composite``/``config``/``inputs[].from``) via
:func:`vivarium_workbench.lib.study_spec.study_interface`. This module is the
**generic, workspace-driven** converter between the two — the generalization of
v2ecoli's bespoke ``library/comparison_composite.py`` (no ecoli/ParCa/vEcoli
assumptions; everything comes from the workspace's own study specs).

Addressing lock-step (critical): the ``_study`` metadata is built so that
``process_bigraph.templates.study_address`` computes the **same** 16-char
``artifact_id`` that :func:`vivarium_workbench.lib.artifacts.pipeline.resolve_study`
already writes into ``.pbg/artifacts/<id>/``. That is what lets ``trigger`` (and
:func:`node_cache_status`) find artifacts the workbench pipeline produced. It
holds because both sides feed the *same* ``artifact_id`` formula the *same*
inputs:

  * ``_study.id``     == ``study_interface(spec)["composite"] or slug``
  * ``_study.config`` == ``study_interface(spec)["config"]``
  * every ``inputs[].from`` producer is included as a member (so ``study_address``
    recurses over the same input-id set ``resolve_study`` does), and
  * the same workspace git ``commit`` is passed to ``trigger``/``study_address``.

Change either side's formula and both must change together (same lock-step note
process-bigraph's ``artifacts`` module carries).
"""
from __future__ import annotations

from pathlib import Path

import yaml

from vivarium_workbench.lib.investigation_members import (
    investigation_member_slugs,
    member_slug,
)
from vivarium_workbench.lib.study_spec import study_interface, study_dir
from vivarium_workbench.lib.workspace_paths import WorkspacePaths

# Matches ``process_bigraph.templates.STUDY_META`` — the per-member metadata key
# ``trigger``/``study_members`` look for. Duplicated (not imported) so a reader
# sees the contract here; kept in lock-step with pbg.
STUDY_META = "_study"


class InvestigationCompositeError(Exception):
    """Raised when a workspace investigation cannot be converted to a document."""


def workspace_commit(ws_root: Path | str) -> str:
    """Workspace git HEAD (or "") — the ``commit`` component of every address.

    Delegates to the pipeline's resolver so the value passed to ``trigger`` is
    byte-identical to the one :func:`resolve_study` already hashed into the
    artifact ids on disk.
    """
    from vivarium_workbench.lib.artifacts.pipeline import _workspace_commit
    return _workspace_commit(Path(ws_root))


def artifacts_root(ws_root: Path | str) -> str:
    """Absolute ``.pbg/artifacts`` dir to pass as ``trigger(..., root=...)``.

    ``process_bigraph.artifacts.ARTIFACT_ROOT`` is the *relative* ``.pbg/artifacts``;
    the server's CWD is not the workspace, so the absolute, layout-aware path
    (via ``WorkspacePaths``) must be passed explicitly or every cache lookup
    misses.
    """
    return str(WorkspacePaths.load(Path(ws_root)).pbg / "artifacts")


def _load_study_spec(ws_root: Path, slug: str) -> dict | None:
    """Load ``studies/<slug>/study.yaml`` (nested-first). ``None`` if absent."""
    try:
        sdir = study_dir(ws_root, slug)
    except Exception:  # noqa: BLE001
        return None
    for name in ("study.yaml", "spec.yaml"):
        p = sdir / name
        if p.is_file():
            try:
                return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            except Exception:  # noqa: BLE001 — a malformed member is unresolvable
                return None
    return None


def _compute_node(address: str, name: str, config: dict) -> dict:
    """A placeholder pbg ``step`` node for a member's ``sim`` site.

    ``trigger`` swaps this for a ``CachedResults`` node when the member's
    artifact is already in the store (pull); it stays put for the target
    (compute). The workbench never constructs a ``Composite`` from this
    document — it reads ``_study`` metadata for the report and delegates actual
    execution to its own run subsystem — so the address only has to be a
    well-formed reference, not a registered Step.
    """
    return {
        "_type": "step",
        "address": address,
        "config": {"name": name, **(config or {})},
        "inputs": {},
        "outputs": {"results": ["results"]},
    }


def _load_investigation_spec(ws_root: Path, inv_slug: str) -> dict:
    wp = WorkspacePaths.load(ws_root)
    spec_path = wp.investigations / inv_slug / "investigation.yaml"
    if not spec_path.is_file():
        raise InvestigationCompositeError(
            f"no investigation.yaml for {inv_slug!r}")
    try:
        return yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        raise InvestigationCompositeError(
            f"unreadable investigation.yaml for {inv_slug!r}: {exc}") from exc


def build_investigation_document(ws_root: Path | str, inv_slug: str) -> dict:
    """Build the pbg investigation document for ``inv_slug``.

    Discovers every member of ``investigations/<inv_slug>/investigation.yaml``
    plus every transitive ``inputs[].from`` producer (even one that is not
    itself a declared member — the same node-discovery ``resolve_investigation``
    does), and emits one region per resolvable study::

        {name: {"_study": {"id", "config", "inputs": [{artifact, from, into?}],
                           "kind"},
                "results": {},
                "sim": <step node>}}

    An ``inputs[].from`` that names an unresolvable study (no ``study.yaml``) is
    dropped from that member's ``_study.inputs`` (and never added as a member),
    so the produced document is always internally consistent — every ``from``
    resolves to a sibling region, which is what ``study_ancestors`` requires.

    Raises :class:`InvestigationCompositeError` if the investigation itself is
    missing/unreadable or declares no resolvable members.
    """
    from process_bigraph.artifacts import TRAJECTORY  # local: keep this module
    # importable without requiring process_bigraph, matching
    # workspace_commit/node_cache_status's local-import pattern in this file.

    ws_root = Path(ws_root)
    inv_spec = _load_investigation_spec(ws_root, inv_slug)

    # BFS discovery: members + transitive producers, loading each study's spec
    # once. Mirrors ``resolve_investigation``'s node discovery.
    specs: dict[str, dict] = {}
    queue = [s for s in (member_slug(m) for m in investigation_member_slugs(inv_spec)) if s]
    seen: set[str] = set()
    while queue:
        slug = queue.pop()
        if slug in seen:
            continue
        seen.add(slug)
        spec = _load_study_spec(ws_root, slug)
        if spec is None:
            continue  # unresolvable producer — dropped (edges into it pruned below)
        specs[slug] = spec
        try:
            for inp in study_interface(spec)["inputs"]:
                queue.append(inp["from"])
        except Exception:  # noqa: BLE001 — a malformed interface contributes no edges
            pass

    if not specs:
        raise InvestigationCompositeError(
            f"investigation {inv_slug!r} has no resolvable member studies")

    # Pass 1: build each region's _study metadata (id/config/inputs), keeping
    # only edges whose producer resolved to a member.
    document: dict = {}
    consumed_kind: dict[str, str] = {}
    for slug, spec in specs.items():
        iface = study_interface(spec)
        inputs = [
            {k: v for k, v in
             (("artifact", e["artifact"]), ("from", e["from"]),
              ("into", e.get("into"))) if v is not None}
            for e in iface["inputs"] if e["from"] in specs
        ]
        # Remember what kind each producer is consumed AS (used to type a
        # producer that doesn't declare its own outputs).
        for e in inputs:
            consumed_kind.setdefault(e["from"], e["artifact"])
        composite_id = iface["composite"] or slug
        document[slug] = {
            STUDY_META: {
                "id": composite_id,
                "config": iface["config"],
                "inputs": inputs,
                "kind": None,  # filled in pass 2
                "outputs": iface["outputs"],
            },
            "results": {},
            "sim": _compute_node(
                f"local:{composite_id}", slug, iface["config"]),
        }

    # Pass 2: resolve each member's own artifact kind. Prefer its declared
    # output; else the kind its consumers read it as; else trajectory.
    for slug, region in document.items():
        meta = region[STUDY_META]
        outputs = meta.pop("outputs", []) or []
        meta["kind"] = (outputs[0] if outputs
                        else consumed_kind.get(slug, TRAJECTORY))

    return document


def node_cache_status(ws_root: Path | str, inv_slug: str) -> dict:
    """Per-member cached-vs-compute status for the investigation graph badges.

    Builds the document and, for each member, computes its content address
    (:func:`process_bigraph.templates.study_address`, same formula the pipeline
    stored under) and probes the store
    (:func:`process_bigraph.artifacts.artifact_exists`). Returns::

        {"investigation": inv_slug,
         "commit": <workspace git HEAD or "">,
         "nodes": [{"slug", "id", "kind", "cached", "artifact_id",
                    "ancestors": [slug, ...]}, ...]}

    Read-only and tolerant: a member whose address cannot be computed (e.g. a
    cyclic ``inputs`` chain) is reported ``cached: False`` with
    ``artifact_id: None`` rather than raising, so one bad member never blanks
    the whole graph.
    """
    from process_bigraph.templates import (
        study_members, study_ancestors, study_address)
    from process_bigraph.artifacts import artifact_exists
    from vivarium_workbench.lib.investigation_figures import study_output_files

    ws_root = Path(ws_root)
    document = build_investigation_document(ws_root, inv_slug)
    commit = workspace_commit(ws_root)
    root = artifacts_root(ws_root)

    nodes = []
    for slug, region in study_members(document).items():
        meta = region.get(STUDY_META) or {}
        try:
            address = study_address(document, slug, commit=commit)
            cached = artifact_exists(address, root)
        except Exception:  # noqa: BLE001 — a bad member is "not cached", not fatal
            address, cached = None, False
        try:
            ancestors = study_ancestors(document, slug)
        except Exception:  # noqa: BLE001
            ancestors = []
        # Whether this study has downloadable figure outputs, so the graph card
        # can gate its "↓ figures" link (a run whose analyses produced nothing —
        # e.g. failed viz — has none). Same predicate as the outputs.zip the link
        # downloads; tolerant so a bad member never blanks the graph.
        try:
            has_figures = bool(study_output_files(ws_root, slug))
        except Exception:  # noqa: BLE001
            has_figures = False
        nodes.append({
            "slug": slug,
            "id": meta.get("id"),
            "kind": meta.get("kind"),
            "cached": bool(cached),
            "artifact_id": address,
            "ancestors": ancestors,
            "has_figures": has_figures,
        })

    return {"investigation": inv_slug, "commit": commit, "nodes": nodes}
