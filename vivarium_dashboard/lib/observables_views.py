"""In-process composite-build + observable-introspection workers (library seam).

These are the HTTP-free builders behind three dashboard routes:

  * ``GET /api/observables?ref=<composite>``        → :func:`build_observables`
  * ``GET /api/study-observable-check?study=<slug>`` → :func:`build_study_observable_check`
  * (the SP4b ``observable_registry``/``composite`` linkage paths) →
    :func:`observables_for_ref_payload`

They run the SAME in-process composite build the Composite Explorer uses
(``_get_composite_state`` / ``_get_composite_resolve``): a ``@composite_generator``
entry via ``build_generator``, else a spec file parsed + ``substitute_parameters``-
resolved, with a best-effort workspace ``build_core()`` threaded through for
``LabeledArray`` catalog resolution.  Emittable observables are reported via
``pbg_superpowers.readout_validation.available_observables``.

Pure ``ws_root``-parameterised functions: NO ``import server`` (the stdlib
``vivarium_dashboard.server`` keeps thin shims that delegate here, passing the
``WORKSPACE`` global).  The FastAPI app imports this module directly.

Caching: this module owns :data:`_OBS_CACHE`, DISJOINT from
``server._COMPOSITE_STATE_CACHE`` (the subprocess composite-state build keeps
its own cache + keys).  :func:`clear_cache` is wired into
``server._invalidate_workspace_caches`` so a workspace switch clears it.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

# Observables build cache — keyed ("observables", str(ws_root), ref) →
# (built_at_epoch, payload_dict).  Building a whole-cell composite is ~1s+, so
# repeat opens + pop-outs are cached.  Short TTL so code edits are picked up.
# DISJOINT from server._COMPOSITE_STATE_CACHE (which the subprocess
# composite-state build owns); the keys never collide but keep them separate.
_OBS_CACHE: dict = {}
_OBS_CACHE_TTL_S = 300.0  # seconds (mirrors server._COMPOSITE_STATE_TTL_S)


def clear_cache() -> None:
    """Clear the observables build cache (called on workspace switch)."""
    _OBS_CACHE.clear()


def _resolve_registry_ref(ref: str, keys) -> str | None:
    """Resolve a (possibly short) composite ``ref`` to a canonical registry key.

    The generator registry is keyed by FQN (``v2ecoli.composites.baseline``) but
    studies author short refs (``baseline``). Mirror the alias rule the rest of
    the dashboard uses (``composite_lookup._ref_resolves``): match on the
    trailing ``.composites.<slug>`` segment, else the last dotted segment. When
    several keys match, prefer the shortest (the canonical module-path id, e.g.
    ``…composites.baseline`` over ``…composites.baseline.baseline``). Returns the
    canonical key, or ``None`` if nothing matches.
    """
    keys = list(keys)
    if ref in keys:
        return ref
    tail = ref.rsplit(".composites.", 1)[-1]
    matches = [k for k in keys if k.rsplit(".composites.", 1)[-1] == tail]
    if not matches:
        matches = [k for k in keys if k.rsplit(".", 1)[-1] == ref]
    if not matches:
        return None
    return min(matches, key=lambda k: (len(k), k))


def build_composite_state_for_observables(ws_root: Path, ref: str) -> tuple[Any, Any, Any]:
    """Build a composite by ``ref`` and return ``(core, state, schema)``.

    Reuses the SAME build path the Composite Explorer uses
    (``_get_composite_state`` / ``_get_composite_resolve``): a
    ``@composite_generator`` entry via ``build_generator``, else a spec file
    parsed + ``substitute_parameters``-resolved. A best-effort workspace
    ``build_core()`` is threaded through so registered ``LabeledArray`` types
    resolve their ``_labels`` catalogs (tolerated if it fails — ``core`` may be
    ``None``, in which case only inline ``_labels`` are recoverable).

    Raises ``LookupError`` for an unknown ref and ``RuntimeError`` for a build
    failure; the caller maps those to clear 4xx statuses.
    """
    ws_root = Path(ws_root)
    ws_str = str(ws_root)
    if ws_str not in sys.path:
        sys.path.insert(0, ws_str)

    ws_data = yaml.safe_load((ws_root / "workspace.yaml").read_text(encoding="utf-8")) or {}
    pkg = ws_data.get("package_path") or ("pbg_" + str(ws_data.get("name", "")).replace("-", "_"))

    # Best-effort core for labeled-array catalog resolution. Absence is fine —
    # leaves come from the state tree alone; only static catalogs degrade.
    core = None
    try:
        core_module = __import__(f"{pkg}.core", fromlist=["build_core"])
        core = core_module.build_core()
    except Exception:
        core = None

    # Generator branch (mirrors _get_composite_state): resolve via the live
    # pbg-superpowers registry.
    entry: Any = None
    apply_core_extensions: Any = None
    build_generator: Any = None
    try:
        from pbg_superpowers.composite_generator import (
            _REGISTRY,
            build_generator as _build_generator,
            discover_generators,
            apply_core_extensions as _apply_core_extensions,
        )
        build_generator = _build_generator
        apply_core_extensions = _apply_core_extensions
        if not _REGISTRY:
            try:
                discover_generators()
            except Exception:
                pass
        entry = _REGISTRY.get(ref)
        if entry is None:
            # Short study refs (``baseline``) miss the FQN-keyed registry;
            # resolve via the canonical-alias rule before falling through.
            canon = _resolve_registry_ref(ref, _REGISTRY.keys())
            if canon is not None:
                entry = _REGISTRY.get(canon)
    except ImportError:
        entry = None

    if entry is not None:
        if core is not None and apply_core_extensions is not None:
            try:
                core = apply_core_extensions(entry, core)
            except Exception:
                pass
        try:
            doc = build_generator(entry, core=core)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"generator build failed: {e}") from e
        if isinstance(doc, dict) and isinstance(doc.get("state"), dict):
            return core, doc["state"], doc.get("schema")
        return core, doc, None

    # Spec-parse branch (mirrors _get_composite_resolve): read the file +
    # substitute parameter defaults to get the live state tree.
    from vivarium_dashboard.lib.composite_lookup import find_composite_path, substitute_parameters
    path = find_composite_path(ws_root, pkg, ref)
    if path is None or not path.is_file():
        raise LookupError(f"composite not found: {ref}")
    try:
        text = path.read_text(encoding="utf-8")
        spec = json.loads(text) if path.suffix.lower() == ".json" else (yaml.safe_load(text) or {})
        state = substitute_parameters(spec.get("state") or {}, spec.get("parameters") or {}, {})
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"spec parse failed: {e}") from e
    return core, state, spec.get("schema") or spec.get("composition")


_LINEAGE_AGENT_RE = re.compile(r"^agents\.\d+\.(.+)$")


def augment_lineage_aliases(available: dict) -> dict:
    """Augment an ``available_observables`` dict with lineage-prefix-stripped aliases.

    The whole-cell composite runs as a LINEAGE: the cell is nested under
    ``agents.<n>.*`` (nearly every leaf is ``agents.0.<rest>``).  Studies,
    however, author *bare* single-cell readout paths (``listeners.mass.cell_mass``,
    ``unique.active_replisome``).  Without normalization the never-fabricate
    guard flags those real readouts as ``not_in_structure`` purely on a prefix
    mismatch (confirmed across all v2e-invest studies: 4/4 such flags, 0 genuine
    phantoms).

    For the ``available`` set used in VALIDATION only, this strips a leading
    ``agents.<n>.`` from every leaf (and catalog key) and adds the captured
    ``<rest>`` as an alias.  The raw emitted paths are preserved.  Crucially it
    strips ONLY a leading ``agents.<n>.`` — never an arbitrary suffix — so a
    genuinely-absent observable (``listeners.totally_fabricated``) still fails
    to match and is correctly flagged ``not_in_structure``.

    This lineage/``agents.<n>.`` convention lives in the dashboard worker; the
    general ``readout_validation`` validator stays free of agent-structure
    knowledge.
    """
    leaves = list(available.get("leaves", []) or [])
    catalogs = dict(available.get("catalogs", {}) or {})

    seen = set(leaves)
    extra_leaves = []
    for leaf in leaves:
        m = _LINEAGE_AGENT_RE.match(leaf)
        if m:
            rest = m.group(1)
            if rest not in seen:
                extra_leaves.append(rest)
                seen.add(rest)

    for key, val in list(catalogs.items()):
        m = _LINEAGE_AGENT_RE.match(key)
        if m:
            catalogs.setdefault(m.group(1), val)

    return {"leaves": leaves + extra_leaves, "catalogs": catalogs}


def build_observables(ws_root: Path, ref: str) -> tuple[dict, int]:
    """GET /api/observables?ref=<id> worker — returns ``(payload_dict, status)``.

    Builds the composite (shared TTL cache, since a whole-cell build is ~3s)
    and reports its emittable observables via ``available_observables``:
    ``{"ref", "leaves": [dotted paths], "catalogs": {observable: [labels]}}``.
    Unknown ref → 404; build failure → 400; validator absent → 501.
    """
    ref = (ref or "").strip()
    if not ref:
        return {"error": "ref required"}, 400

    import time as _time
    cache = _OBS_CACHE
    ckey = ("observables", str(ws_root), ref)
    hit = cache.get(ckey)
    if hit is not None and (_time.time() - hit[0]) < _OBS_CACHE_TTL_S:
        return {**hit[1], "cached": True}, 200

    # Lazy import — tolerant if pbg_superpowers predates readout_validation.
    try:
        from pbg_superpowers.readout_validation import available_observables
    except Exception as e:  # noqa: BLE001
        return {"error": f"readout_validation unavailable: {e}"}, 501

    try:
        core, state, schema = build_composite_state_for_observables(ws_root, ref)
    except LookupError as e:
        return {"error": str(e)}, 404
    except Exception as e:  # noqa: BLE001
        return {"error": f"composite build failed: {e}"}, 400

    try:
        available = available_observables(core, state, schema)
    except Exception as e:  # noqa: BLE001
        return {"error": f"observable introspection failed: {e}"}, 500

    payload = {
        "ref": ref,
        "leaves": available.get("leaves", []),
        "catalogs": available.get("catalogs", {}),
    }
    cache[ckey] = (_time.time(), payload)
    if len(cache) > 32:  # cap memory; drop the oldest entry
        cache.pop(next(iter(cache)))
    return payload, 200


def build_study_observable_check(ws_root: Path, slug: str) -> tuple[dict, int]:
    """GET /api/study-observable-check?study=<slug> worker — ``(payload_dict, status)``.

    Validates every readout in a study against its baseline composite's real
    structure (the never-fabricate guard): ``{"composite": ref, "readouts":
    [{name, status, detail}]}`` with ``status`` ∈
    ``ok|unresolved|not_in_structure|aspirational``. ``not_in_structure`` is the
    never-fabricate flag — a selector pointing at an observable the composite
    does not expose. If the composite can't build, returns a clear non-500
    (422 + all readouts marked aspirational with a note), never a crash.
    """
    from vivarium_dashboard.lib.study_spec import SLUG_RE, study_spec_file

    ws_root = Path(ws_root)
    if not SLUG_RE.match(slug or ""):
        return {"error": "invalid slug"}, 400

    study_dir = ws_root / "studies" / slug
    if not study_dir.is_dir():
        study_dir = ws_root / "investigations" / slug
    sf = study_spec_file(study_dir)
    if not sf.is_file():
        return {"error": f"study not found: {slug}"}, 404

    try:
        spec = yaml.safe_load(sf.read_text(encoding="utf-8")) or {}
    except Exception as e:  # noqa: BLE001
        return {"error": f"study spec parse failed: {e}"}, 400

    # Project legacy v2 shape (baseline: <str>) into the v3 baseline list.
    from vivarium_dashboard.lib.spec_migration import migrate_v2_to_v3
    spec = migrate_v2_to_v3(spec)

    baseline = spec.get("baseline") or []
    if not (isinstance(baseline, list) and baseline and isinstance(baseline[0], dict)):
        return {"error": "study has no baseline composite", "readouts": []}, 422
    ref = baseline[0].get("composite")
    if not ref:
        return {"error": "baseline entry has no composite ref", "readouts": []}, 422

    try:
        from pbg_superpowers.readout_validation import available_observables, validate_readouts
    except Exception as e:  # noqa: BLE001
        return {"error": f"readout_validation unavailable: {e}"}, 501

    readouts = spec.get("readouts") or []
    try:
        core, state, schema = build_composite_state_for_observables(ws_root, ref)
    except Exception as e:  # noqa: BLE001 (LookupError / RuntimeError both land here)
        # Composite can't build → clear non-500: surface every readout as
        # aspirational (unverifiable) with a note, rather than crashing.
        out = [
            {"name": r.get("name", f"readout_{i}"), "status": "aspirational",
             "detail": f"composite {ref!r} could not be built — readout unverified"}
            for i, r in enumerate(readouts)
        ]
        return {
            "composite": ref,
            "readouts": out,
            "note": f"composite {ref!r} could not be built: {e}",
        }, 422

    try:
        # Normalize the lineage prefix: the whole-cell composite nests the cell
        # under ``agents.<n>.`` but studies author bare single-cell paths, so
        # augment the VALIDATION set with prefix-stripped aliases (never-fabricate
        # preserved — only a leading ``agents.<n>.`` is stripped).
        available = augment_lineage_aliases(available_observables(core, state, schema))
        results = validate_readouts(spec, available=available)
    except Exception as e:  # noqa: BLE001
        return {"error": f"readout validation failed: {e}", "composite": ref}, 500

    return {"composite": ref, "readouts": results}, 200


def observables_for_ref_payload(ws_root: Path, ref: str) -> dict:
    """Adapter for the SP4b linkage paths: ``ref -> {"leaves", "catalogs"}``.

    The ``pbg_superpowers.linkage_index`` enrich callable wants the plain
    ``{"leaves": [...], "catalogs": {...}}`` dict (it reads ``leaves`` to map a
    composite's emissions onto observable nodes).  This mirrors the shape the
    legacy ``server._linkage_index`` fed in via its ``_obs_for_ref`` wrapper —
    now sourced from :func:`build_observables` (lib), so the dashboard and the
    FastAPI route produce identical linkage data.  Returns ``{}`` on any
    failure (the consumer is tolerant and skips unbuildable composites).
    """
    try:
        payload, _status = build_observables(ws_root, ref)
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(payload, dict):
        return {}
    return {"leaves": payload.get("leaves", []), "catalogs": payload.get("catalogs", {})}


# Register this module's cache-clear with the active-workspace registry so a
# workspace switch invalidates it via active_workspace.invalidate().
from . import active_workspace as _aw  # noqa: E402
_aw.register_clear_cb(clear_cache)
