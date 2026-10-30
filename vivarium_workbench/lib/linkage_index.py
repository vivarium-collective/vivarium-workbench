"""Derived, EPHEMERAL linkage index over the workspace YAML — the explicit
knowledge graph (studies ↔ composites ↔ observables ↔ sources ↔ findings ↔
acceptance ↔ study-DAG) — plus the cheap reverse queries that today require
grep.

SP4a, Active Investigation Framework / Layer 2 (Navigate).

Design notes
------------
- **Pure derive.** ``build_index`` and every reverse-query helper only *read*
  the workspace YAML (via :class:`WorkspacePaths` + :mod:`study_io`). Nothing
  is ever written back to disk; the index is rebuilt on demand and never
  persisted into any ``*.yaml``.
- **Reuse, don't reimplement.** AC results come from
  :func:`investigation_status.roll_up_acceptance` /
  :func:`study_outcomes.canonical_outcomes`; the input pool from
  :func:`investigation_inputs.investigation_inputs`. We do not re-derive AC
  roll-up here.
- **Best-effort per study.** A study whose YAML fails to parse is skipped, not
  fatal — the rest of the graph still builds.
- **SP4b — composite→observable edges (INJECTED build).** The only expensive
  edge — what a composite actually emits — is added by
  :func:`enrich_observable_edges`, which takes an injected
  ``observables_for_ref(ref) -> {"leaves": [...], "catalogs": {...}}`` callable
  (the dashboard's ``_observables_for_ref``). ``build_index`` and the pure YAML
  core never depend on or pay for a build; only the registry queries that opt in
  (by supplying the callable) trigger one. Emitted leaves are lineage-prefix
  normalized (strip a leading ``agents.<n>.``) so they MATCH the bare tokens
  studies declare.

Public API
----------
``build_index(ws_root) -> {"nodes": [...], "edges": [...]}``
    The full typed graph. PURE — never builds a composite.
``enrich_observable_edges(index, observables_for_ref) -> index``
    Mutate ``index`` in place, adding ``composite --emits--> observable`` edges
    from an INJECTED build callable (tolerant: a composite that fails to build
    is skipped). In-memory only — never writes YAML.
``studies_for_observable(ws_root, token, *, observables_for_ref) -> {"studies": [...], "composites": [...]}``
    Cross-study registry: which studies/composites emit an observable token.
``composite_emits(ws_root, composite_id, *, observables_for_ref) -> {"emits": [...], "used_by_studies": [...]}``
    What a composite emits + which studies use it.
``ac_gating_matrix(ws_root, inv) -> {...}``
    Per-criterion study→result matrix; ``gap: True`` when an acceptance
    criterion carries no ``study:`` link (the live chromosome-cycle gap).
``studies_for_source(ws_root, key) -> [slug, ...]``
    Which studies cite a bib_key (study-level ``cites`` + per-band cites).
``findings_for_observable(ws_root, token) -> [{finding, study, observable}]``
    Findings whose linked test measures an observable token.
``study_dag(ws_root, inv) -> {"nodes": [...], "edges": [...]}``
    Study nodes + prerequisite/enables edges from ``pipeline_gate``.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable, Iterable

from discovery_engine import study_io
from discovery_engine.workspace_paths import WorkspacePaths

from vivarium_workbench.lib.investigation_members import investigation_member_slugs


# ---------------------------------------------------------------------------
# Node / edge typed-id helpers
# ---------------------------------------------------------------------------

def _node(nid: str, ntype: str, **attrs: Any) -> dict:
    return {"id": nid, "type": ntype, **attrs}


def _edge(src: str, dst: str, etype: str, **attrs: Any) -> dict:
    return {"from": src, "to": dst, "type": etype, **attrs}


def _id(kind: str, *parts: str) -> str:
    return ":".join([kind, *(str(p) for p in parts)])


# ---------------------------------------------------------------------------
# Tolerant loaders
# ---------------------------------------------------------------------------

def _load(path: Path) -> dict | None:
    """Best-effort YAML mapping load; None on any error / non-mapping."""
    try:
        data = study_io.load_yaml(path)
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) else None


def _iter_studies(wp: WorkspacePaths) -> Iterable[tuple[str, Path, dict]]:
    """Yield (slug, study_dir, spec) for each parseable study in the workspace."""
    for sdir in wp.iter_study_dirs():
        spec = _load(sdir / "study.yaml")
        if spec is not None:
            yield sdir.name, sdir, spec


def _iter_investigations(wp: WorkspacePaths) -> Iterable[tuple[str, dict]]:
    inv_root = wp.dir("investigations")
    if not inv_root.is_dir():
        return
    for inv in sorted(p for p in inv_root.iterdir() if p.is_dir()):
        spec = _load(inv / "investigation.yaml")
        if spec is not None:
            yield inv.name, spec


# ---------------------------------------------------------------------------
# Shape extractors (the edge anchors, normalized)
# ---------------------------------------------------------------------------

def _composites_of_study(spec: dict) -> list[str]:
    """study → composite. Reads ``conditions.baseline[].composite`` and
    ``conditions.variants[].base_composite`` (resolved to the baseline name →
    composite). Tolerant of both the nested ``conditions:`` shape and a
    top-level ``baseline``/``variants`` (dict OR list)."""
    out: list[str] = []
    baselines_by_name: dict[str, str] = {}

    def _add_baseline(entry: Any) -> None:
        if not isinstance(entry, dict):
            return
        comp = entry.get("composite")
        if isinstance(comp, str) and comp:
            out.append(comp)
            name = entry.get("name")
            if isinstance(name, str) and name:
                baselines_by_name[name] = comp

    for container in (spec, spec.get("conditions") or {}):
        if not isinstance(container, dict):
            continue
        baseline = container.get("baseline")
        if isinstance(baseline, dict):
            _add_baseline(baseline)
        elif isinstance(baseline, list):
            for b in baseline:
                _add_baseline(b)

    # variants reference a base_composite → resolve to its composite (or use the
    # base_composite name directly when it names a composite).
    for container in (spec, spec.get("conditions") or {}):
        if not isinstance(container, dict):
            continue
        for v in (container.get("variants") or []):
            if not isinstance(v, dict):
                continue
            base = v.get("base_composite")
            if isinstance(base, str) and base:
                out.append(baselines_by_name.get(base, base))

    # de-dup, preserve order
    seen: set[str] = set()
    return [c for c in out if not (c in seen or seen.add(c))]


def _observables_of_study(spec: dict) -> list[str]:
    """study → observables. The ``_collect_study_observables`` shape:
    ``readouts[].identifier``/``store_path`` + ``behavior_tests[]``/``tests[]``
    ``measure.{field,path}``."""
    out: list[str] = []
    for ro in (spec.get("readouts") or []):
        if isinstance(ro, dict):
            for key in ("identifier", "store_path", "path"):
                tok = ro.get(key)
                if isinstance(tok, str) and tok:
                    out.append(tok)
    for section in ("behavior_tests", "tests"):
        for t in (spec.get(section) or []):
            if not isinstance(t, dict):
                continue
            for tok in _test_observables(t):
                out.append(tok)
    seen: set[str] = set()
    return [o for o in out if not (o in seen or seen.add(o))]


def _test_observables(test: dict) -> list[str]:
    measure = test.get("measure")
    out: list[str] = []
    if isinstance(measure, dict):
        for key in ("field", "path"):
            tok = measure.get(key)
            if isinstance(tok, str) and tok:
                out.append(tok)
    return out


def _cites_of_study(spec: dict) -> list[str]:
    """study → sources: study-level ``cites[]`` + per-band cites on
    ``behavior_tests[]``/``tests[]``/``readouts[]``."""
    out: list[str] = []
    for key in (spec.get("cites") or []):
        if isinstance(key, str) and key:
            out.append(key)
    for section in ("behavior_tests", "tests", "readouts"):
        for entry in (spec.get(section) or []):
            if not isinstance(entry, dict):
                continue
            for key in (entry.get("cites") or []):
                if isinstance(key, str) and key:
                    out.append(key)
    seen: set[str] = set()
    return [c for c in out if not (c in seen or seen.add(c))]


def _sources_of_investigation(ws_root: Path, slug: str, spec: dict) -> list[str]:
    """investigation → source, NORMALIZING the split-shape: both
    ``inputs.{references,datasets,expert_docs}`` AND the top-level
    ``references``/``expert_docs`` lists."""
    from discovery_engine.investigation_inputs import investigation_inputs

    out: list[str] = []

    # Canonical inputs pool (inputs.* block).
    try:
        pool = investigation_inputs(ws_root, slug)
    except Exception:  # noqa: BLE001
        pool = {"references": [], "datasets": [], "expert_docs": []}
    for group in ("references", "datasets", "expert_docs"):
        for entry in (pool.get(group) or []):
            out.append(_source_key(entry))

    # Top-level split-shape (legacy / parallel) — references / expert_docs.
    for group in ("references", "expert_docs", "datasets"):
        for entry in (spec.get(group) or []):
            out.append(_source_key(entry))

    seen: set[str] = set()
    return [s for s in out if s and not (s in seen or seen.add(s))]


def _source_key(entry: Any) -> str:
    """Normalize a source reference (a bib_key string, or a dict with
    name/path/key) to a stable token."""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        for key in ("key", "name", "bib_key", "path"):
            v = entry.get(key)
            if isinstance(v, str) and v:
                return v
    return ""


def _pipeline_gate_edges(slug: str, spec: dict) -> list[tuple[str, str]]:
    """Return (from_slug, to_slug) prerequisite-direction edges for a study,
    from ``pipeline_gate.{prerequisites,enables}`` (each a list of study slugs
    OR dicts carrying ``study:``)."""
    gate = spec.get("pipeline_gate")
    if not isinstance(gate, dict):
        return []
    edges: list[tuple[str, str]] = []
    for pre in _gate_slugs(gate.get("prerequisites")):
        edges.append((pre, slug))          # prerequisite → this study
    for nxt in _gate_slugs(gate.get("enables")):
        edges.append((slug, nxt))          # this study → enabled study
    return edges


def _gate_slugs(value: Any) -> list[str]:
    out: list[str] = []
    for item in (value or []):
        if isinstance(item, str) and item:
            out.append(item)
        elif isinstance(item, dict):
            s = item.get("study")
            if isinstance(s, str) and s:
                out.append(s)
    return out


def _findings_of_study(spec: dict) -> list[dict]:
    out: list[dict] = []
    for f in (spec.get("findings") or []):
        if isinstance(f, dict) and f.get("id"):
            out.append(f)
    return out


def _finding_evidence(finding: dict) -> dict:
    """Normalize a finding's evidence/provenance pointers:
    ``evidence.{from_test,from_run}`` + ``provenance.run_ids``."""
    ev = finding.get("evidence") if isinstance(finding.get("evidence"), dict) else {}
    prov = finding.get("provenance") if isinstance(finding.get("provenance"), dict) else {}
    run_ids = list(prov.get("run_ids") or [])
    from_run = ev.get("from_run")
    if isinstance(from_run, str) and from_run and from_run not in run_ids:
        run_ids.append(from_run)
    return {
        "from_test": ev.get("from_test") if isinstance(ev.get("from_test"), str) else None,
        "run_ids": run_ids,
    }


# ---------------------------------------------------------------------------
# build_index — the full typed graph (PURE)
# ---------------------------------------------------------------------------

def build_index(ws_root: Path | str) -> dict:
    """Derive the workspace linkage graph. PURE — reads YAML only, never writes.

    Returns ``{"nodes": [...], "edges": [...]}`` where each node is
    ``{id, type, ...}`` and each edge is ``{from, to, type, ...}``.
    """
    ws_root = Path(ws_root)
    wp = WorkspacePaths.load(ws_root)

    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    def add_node(node: dict) -> str:
        nodes.setdefault(node["id"], node)
        return node["id"]

    # --- investigations + their sources + membership + acceptance ----------
    inv_specs: dict[str, dict] = {}
    for inv_slug, inv_spec in _iter_investigations(wp):
        inv_specs[inv_slug] = inv_spec
        inv_id = add_node(_node(_id("investigation", inv_slug), "investigation",
                                name=inv_slug, title=inv_spec.get("title")))

        for member in investigation_member_slugs(inv_spec):
            if isinstance(member, str) and member:
                edges.append(_edge(inv_id, _id("study", member), "contains"))

        for key in _sources_of_investigation(ws_root, inv_slug, inv_spec):
            sid = add_node(_node(_id("source", key), "source", key=key))
            edges.append(_edge(inv_id, sid, "references"))

        # acceptance criteria → study (gap-flagged when no study: link)
        for i, crit in enumerate(inv_spec.get("acceptance_criteria") or []):
            if not isinstance(crit, dict):
                continue
            behavior = crit.get("behavior", "")
            study = crit.get("study")
            ac_id = add_node(_node(_id("ac", inv_slug, str(i)), "acceptance_criterion",
                                   investigation=inv_slug, behavior=behavior,
                                   study=study, gap=not bool(study),
                                   status=crit.get("status")))
            if study:
                edges.append(_edge(ac_id, _id("study", study), "criterion_of"))

    # --- studies + composites + observables + sources + findings + DAG -----
    for slug, _sdir, spec in _iter_studies(wp):
        study_id = add_node(_node(_id("study", slug), "study", slug=slug,
                                  title=spec.get("title"),
                                  investigation=spec.get("investigation")))

        for comp in _composites_of_study(spec):
            cid = add_node(_node(_id("composite", comp), "composite", name=comp))
            edges.append(_edge(study_id, cid, "uses_composite"))

        for obs in _observables_of_study(spec):
            oid = add_node(_node(_id("observable", obs), "observable", token=obs))
            edges.append(_edge(study_id, oid, "measures"))

        for key in _cites_of_study(spec):
            sid = add_node(_node(_id("source", key), "source", key=key))
            edges.append(_edge(study_id, sid, "cites"))

        for finding in _findings_of_study(spec):
            fid = add_node(_node(_id("finding", slug, finding["id"]), "finding",
                                 finding=finding["id"], study=slug,
                                 status=finding.get("status")))
            edges.append(_edge(study_id, fid, "has_finding"))
            ev = _finding_evidence(finding)
            if ev["from_test"]:
                edges.append(_edge(fid, _id("test", slug, ev["from_test"]),
                                   "evidence_test"))
            for run_id in ev["run_ids"]:
                edges.append(_edge(fid, _id("run", slug, run_id), "evidence_run"))

        for from_slug, to_slug in _pipeline_gate_edges(slug, spec):
            edges.append(_edge(_id("study", from_slug), _id("study", to_slug),
                               "prerequisite"))

    return {"nodes": list(nodes.values()), "edges": edges}


# ---------------------------------------------------------------------------
# Reverse query: AC → study gating matrix (+ GAPS)
# ---------------------------------------------------------------------------

def ac_gating_matrix(ws_root: Path | str, inv: str) -> dict:
    """Per-criterion study→result matrix for one investigation.

    Each criterion row: ``{behavior, study, result, covered_by, gap, status}``.
    ``gap`` is True (and ``covered_by`` empty) when the criterion carries no
    ``study:`` link — the exact chromosome-cycle-calibration gap.

    Reuses :func:`investigation_status.roll_up_acceptance` for the coded results
    (no reimplementation).
    """
    from discovery_engine.investigation_status import roll_up_acceptance

    ws_root = Path(ws_root)
    wp = WorkspacePaths.load(ws_root)

    inv_spec = _load(wp.dir("investigations") / inv / "investigation.yaml") or {}
    studies_by_name: dict[str, dict] = {
        slug: spec for slug, _d, spec in _iter_studies(wp)
    }

    rolled = roll_up_acceptance(inv_spec, studies_by_name)
    # roll_up preserves criterion order → zip back onto the authored criteria.
    rolled_results = rolled.get("criteria") or []

    criteria: list[dict] = []
    gaps: list[dict] = []
    raw = inv_spec.get("acceptance_criteria") or []
    for i, crit in enumerate(raw):
        if not isinstance(crit, dict):
            continue
        study = crit.get("study")
        behavior = crit.get("behavior", "")
        result = rolled_results[i]["result"] if i < len(rolled_results) else None
        row = {
            "behavior": behavior,
            "study": study or None,
            "result": result,
            "status": crit.get("status"),
            "covered_by": [study] if study else [],
            "gap": not bool(study),
        }
        criteria.append(row)
        if row["gap"]:
            gaps.append(row)

    return {
        "investigation": inv,
        "verdict_status": rolled.get("verdict_status"),
        "criteria": criteria,
        "gaps": gaps,
    }


# ---------------------------------------------------------------------------
# Reverse query: source → studies
# ---------------------------------------------------------------------------

def studies_for_source(ws_root: Path | str, key: str) -> list[str]:
    """Study slugs that cite ``key`` (study-level ``cites`` + per-band cites)."""
    wp = WorkspacePaths.load(Path(ws_root))
    out: list[str] = []
    for slug, _d, spec in _iter_studies(wp):
        if key in _cites_of_study(spec):
            out.append(slug)
    return out


# ---------------------------------------------------------------------------
# Reverse query: observable → findings
# ---------------------------------------------------------------------------

def findings_for_observable(ws_root: Path | str, token: str) -> list[dict]:
    """Findings whose linked test measures ``token``.

    Resolution: ``finding.evidence.from_test`` → the test's
    ``measure.{path,field}`` observable tokens. Exact match first, substring
    tolerance second.
    """
    wp = WorkspacePaths.load(Path(ws_root))
    out: list[dict] = []
    for slug, _d, spec in _iter_studies(wp):
        tests_by_name: dict[str, dict] = {}
        for section in ("behavior_tests", "tests"):
            for t in (spec.get(section) or []):
                if isinstance(t, dict) and isinstance(t.get("name"), str):
                    tests_by_name[t["name"]] = t
        for finding in _findings_of_study(spec):
            ev = _finding_evidence(finding)
            from_test = ev["from_test"]
            if not from_test:
                continue
            test = tests_by_name.get(from_test)
            if test is None:
                continue
            obs = _test_observables(test)
            if _token_matches(token, obs):
                out.append({"finding": finding["id"], "study": slug,
                            "observable": token})
    return out


def _token_matches(token: str, observables: list[str]) -> bool:
    if token in observables:
        return True
    return any(token in o or o in token for o in observables)


# ---------------------------------------------------------------------------
# Reverse query: study DAG
# ---------------------------------------------------------------------------

def study_dag(ws_root: Path | str, inv: str) -> dict:
    """Study nodes + prerequisite/enables edges for one investigation, from
    each member study's ``pipeline_gate``."""
    wp = WorkspacePaths.load(Path(ws_root))
    inv_spec = _load(wp.dir("investigations") / inv / "investigation.yaml") or {}
    members = [m for m in investigation_member_slugs(inv_spec) if isinstance(m, str)]
    member_set = set(members)

    specs: dict[str, dict] = {
        slug: spec for slug, _d, spec in _iter_studies(wp)
    }

    nodes = [{"id": m, "study": m} for m in members]
    edges: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for slug in members:
        spec = specs.get(slug)
        if spec is None:
            continue
        for from_slug, to_slug in _pipeline_gate_edges(slug, spec):
            # keep DAG within the investigation's membership when known
            if member_set and (from_slug not in member_set or to_slug not in member_set):
                continue
            pair = (from_slug, to_slug)
            if pair in seen:
                continue
            seen.add(pair)
            edges.append({"from": from_slug, "to": to_slug, "type": "prerequisite"})

    return {"investigation": inv, "nodes": nodes, "edges": edges}


# ---------------------------------------------------------------------------
# SP4b: composite → observable edge enrichment (INJECTED build)
# ---------------------------------------------------------------------------

_LINEAGE_PREFIX = re.compile(r"^agents\.\d+\.")


def _strip_lineage_prefix(leaf: str) -> str:
    """Strip a leading ``agents.<n>.`` lineage prefix so a composite-emitted
    leaf (``agents.0.listeners.mass.cell_mass``) matches the BARE token a study
    declares (``listeners.mass.cell_mass``). See the SP2b-i lesson."""
    return _LINEAGE_PREFIX.sub("", leaf)


# A bulk-pool atom: ``MOLECULE[compartment]`` (e.g. ``ATP[c]``). Reconciles the
# bulk dialects — a composite emits ``bulk[ATP[c]]`` while a study declares
# ``bulk.ATP[c]`` or ``bulk__count[ATP[c], ADP[c]]``; all three name the same
# observable and must MATCH (else the registry is silently empty for the bulk
# class, which dominates v2ecoli). The SP2b-i lesson, bracket edition.
_BULK_ATOM_RE = re.compile(r"[A-Za-z0-9_\-+]+\[[a-z0-9]+\]")


def _observable_match_keys(token: str) -> frozenset[str]:
    """Canonical comparison keys for an observable token.

    Non-bulk tokens compare by their exact (lineage-stripped) string. A bulk
    token reconciles to the SET of its ``MOLECULE[compartment]`` atoms, so
    ``bulk[ATP[c]]`` (a composite leaf), ``bulk.ATP[c]`` and
    ``bulk__count[ATP[c], ADP[c]]`` (study declarations) all share the key
    ``ATP[c]`` and therefore match. Two tokens MATCH iff their key sets
    intersect. The ``"bulk"`` guard keeps non-bulk bracketed paths (e.g. a
    listener array ``listeners.x[gene]``) on exact-string matching — no false
    positives."""
    t = _strip_lineage_prefix(token)
    if "bulk" in t.lower():
        atoms = _BULK_ATOM_RE.findall(t)
        if atoms:
            return frozenset(atoms)
    return frozenset({t})


def _observable_key_map(nodes: list[dict]) -> dict[str, set[str]]:
    """Map each match-key to the set of existing ``observable`` node ids that
    carry it — so a composite leaf can SNAP onto the study-declared observable
    node it matches (rather than minting a parallel, never-matching node)."""
    out: dict[str, set[str]] = {}
    for n in nodes:
        if n.get("type") == "observable":
            for k in _observable_match_keys(n.get("token", "")):
                out.setdefault(k, set()).add(n["id"])
    return out


def enrich_observable_edges(
    index: dict,
    observables_for_ref: Callable[[str], dict],
) -> dict:
    """Add ``composite --emits--> observable`` edges from an INJECTED build.

    For each ``composite`` node, call ``observables_for_ref(node["name"])``
    (the dashboard's ``_observables_for_ref``) — the ONLY expensive edge, kept
    out of the pure :func:`build_index` core. Each returned leaf is
    lineage-prefix-normalized so it lands on the SAME ``observable:<token>``
    node a study ``measures``. Tolerant: a composite whose build raises is
    skipped (no edges for it). Mutates and returns the SAME ``index`` dict —
    in-memory only, never writes YAML.
    """
    nodes: list[dict] = index["nodes"]
    edges: list[dict] = index["edges"]
    node_ids = {n["id"] for n in nodes}
    existing_emits = {
        (e["from"], e["to"]) for e in edges if e.get("type") == "emits"
    }
    # match-key → existing observable node ids (study `measures` nodes); a leaf
    # SNAPS onto the node(s) it matches by key, reconciling the bulk dialects.
    key_to_oids = _observable_key_map(nodes)

    for node in list(nodes):
        if node.get("type") != "composite":
            continue
        comp = node["name"]
        cid = node["id"]
        try:
            result = observables_for_ref(comp)
        except Exception:  # noqa: BLE001 — tolerant: skip composites that can't build
            continue
        if not isinstance(result, dict):
            continue
        for leaf in (result.get("leaves") or []):
            if not isinstance(leaf, str) or not leaf:
                continue
            keys = _observable_match_keys(leaf)
            target_oids: set[str] = set()
            for k in keys:
                target_oids |= key_to_oids.get(k, set())
            if not target_oids:
                # No study declares this observable — mint a node for the leaf
                # so the composite's emission is still discoverable.
                tok = _strip_lineage_prefix(leaf)
                oid = _id("observable", tok)
                if oid not in node_ids:
                    nodes.append(_node(oid, "observable", token=tok))
                    node_ids.add(oid)
                    for k in keys:
                        key_to_oids.setdefault(k, set()).add(oid)
                target_oids = {oid}
            for oid in target_oids:
                pair = (cid, oid)
                if pair in existing_emits:
                    continue
                existing_emits.add(pair)
                edges.append(_edge(cid, oid, "emits"))

    return index


def studies_for_observable(
    ws_root: Path | str,
    token: str,
    *,
    observables_for_ref: Callable[[str], dict],
) -> dict:
    """Cross-study registry for an observable token.

    Builds + enriches the index (the enrich step triggers the injected build),
    then returns the composites that ``emits`` an observable MATCHING ``token``
    and the studies that ``uses_composite`` any of them. Matching is key-based
    (:func:`_observable_match_keys`), so a query for ``bulk.ATP[c]`` finds a
    composite leaf ``bulk[ATP[c]]`` and a study node ``bulk__count[ATP[c], …]``.
    Output lists carry BARE slugs/composite-ids (the ``study:``/``composite:``
    id prefixes stripped). Tolerates ``token`` already being bare.
    """
    query_keys = _observable_match_keys(token)

    index = build_index(ws_root)
    enrich_observable_edges(index, observables_for_ref)

    target_oids = {
        n["id"] for n in index["nodes"]
        if n.get("type") == "observable"
        and _observable_match_keys(n.get("token", "")) & query_keys
    }
    comp_ids = {
        e["from"] for e in index["edges"]
        if e.get("type") == "emits" and e["to"] in target_oids
    }
    studies: set[str] = set()
    for e in index["edges"]:
        if e.get("type") == "uses_composite" and e["to"] in comp_ids:
            studies.add(_strip_id_prefix(e["from"]))

    return {
        "studies": sorted(studies),
        "composites": sorted(_strip_id_prefix(c) for c in comp_ids),
    }


def composite_emits(
    ws_root: Path | str,
    composite_id: str,
    *,
    observables_for_ref: Callable[[str], dict],
) -> dict:
    """What a composite emits + which studies use it.

    Builds + enriches the index, then collects the bare observable tokens this
    composite ``emits`` and the study slugs that ``uses_composite`` it.
    """
    cid = _id("composite", composite_id)

    index = build_index(ws_root)
    enrich_observable_edges(index, observables_for_ref)

    emits: list[str] = []
    for e in index["edges"]:
        if e.get("type") == "emits" and e["from"] == cid:
            emits.append(_strip_id_prefix(e["to"]))
    used_by: set[str] = set()
    for e in index["edges"]:
        if e.get("type") == "uses_composite" and e["to"] == cid:
            used_by.add(_strip_id_prefix(e["from"]))

    return {
        "emits": sorted(set(emits)),
        "used_by_studies": sorted(used_by),
    }


def _strip_id_prefix(nid: str) -> str:
    """``composite:v2ecoli.x`` → ``v2ecoli.x``; ``study:s1`` → ``s1``.

    Splits off only the leading ``kind:`` prefix (one split) so composite ids
    that themselves contain ``:`` survive intact."""
    return nid.split(":", 1)[1] if ":" in nid else nid
