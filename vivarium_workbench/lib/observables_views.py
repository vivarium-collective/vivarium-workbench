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
``vivarium_workbench.lib.readout_validation.available_observables``.

Pure ``ws_root``-parameterised functions: NO ``import server`` (the stdlib
``vivarium_workbench.server`` keeps thin shims that delegate here, passing the
``WORKSPACE`` global).  The FastAPI app imports this module directly.

Caching: this module owns :data:`_OBS_CACHE`, DISJOINT from
``server._COMPOSITE_STATE_CACHE`` (the subprocess composite-state build keeps
its own cache + keys).  :func:`clear_cache` is wired into
``server._invalidate_workspace_caches`` so a workspace switch clears it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

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


def _call_obs_worker(ws_root: Path, method: str, params: dict) -> "dict | None":
    """Route an observables method to the session's env worker; ``None`` when the
    worker is unavailable (crash / can't spawn)."""
    from vivarium_workbench.lib.env_worker_client import EnvWorkerUnavailable
    from vivarium_workbench.lib.env_worker_pool import get_pool
    try:
        return get_pool().call(ws_root, method, params)
    except EnvWorkerUnavailable:
        return None


def _resolve_spec_params(ws_root: Path, ref: str) -> dict:
    """``__not_registered__`` fallback: resolve ``ref`` to a spec file, parse +
    ``substitute_parameters`` → ``{'state', 'schema'}`` for an inline worker
    build. Mirrors ``build_composite_state_for_observables``'s spec-parse branch.
    Raises ``LookupError`` (→404) if unresolved, other exceptions (→400) on parse
    failure. The file I/O stays workbench-side (science record; §11)."""
    ws_data = yaml.safe_load((ws_root / "workspace.yaml").read_text(encoding="utf-8")) or {}
    pkg = ws_data.get("package_path") or ("pbg_" + str(ws_data.get("name", "")).replace("-", "_"))
    from vivarium_workbench.lib.composite_lookup import find_composite_path, substitute_parameters
    path = find_composite_path(ws_root, pkg, ref)
    if path is None or not path.is_file():
        raise LookupError(f"composite not found: {ref}")
    text = path.read_text(encoding="utf-8")
    spec = json.loads(text) if path.suffix.lower() == ".json" else (yaml.safe_load(text) or {})
    state = substitute_parameters(spec.get("state") or {}, spec.get("parameters") or {}, {})
    return {"state": state, "schema": spec.get("schema") or spec.get("composition")}


def build_observables(ws_root: Path, ref: str) -> tuple[dict, int]:
    """GET /api/observables?ref=<id> worker — returns ``(payload_dict, status)``.

    The composite build + ``available_observables`` introspection runs in the
    session's env worker (it needs the live core + polars, which the HTTP process
    must not import); this routes ``observables{ref}`` there, falling back to an
    inline ``observables{state, schema}`` for a spec-file composite the worker's
    registry doesn't know. Payload:
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

    r = _call_obs_worker(ws_root, "observables", {"ref": ref})
    if r is None:
        return {"error": "environment worker unavailable"}, 500
    if "__not_registered__" in r:
        # Not a registered generator → resolve the spec file (workbench-side) and
        # build from the inline document.
        try:
            params = _resolve_spec_params(ws_root, ref)
        except LookupError as e:
            return {"error": str(e)}, 404
        except Exception as e:  # noqa: BLE001
            return {"error": f"composite build failed: {e}"}, 400
        r = _call_obs_worker(ws_root, "observables", params)
        if r is None:
            return {"error": "environment worker unavailable"}, 500

    if "__no_validator__" in r:
        return {"error": f"readout_validation unavailable: {r['__no_validator__']}"}, 501
    if "__build_error__" in r:
        return {"error": f"composite build failed: {r['__build_error__']}"}, 400
    if "__introspect_error__" in r:
        return {"error": r["__introspect_error__"]}, 500

    payload = {
        "ref": ref,
        "leaves": r.get("leaves", []),
        "catalogs": r.get("catalogs", {}),
    }
    cache[ckey] = (_time.time(), payload)
    if len(cache) > 32:  # cap memory; drop the oldest entry
        cache.pop(next(iter(cache)))
    return payload, 200


def build_study_observable_check(ws_root: Path, slug: str) -> tuple[dict, int]:
    """GET /api/study-observable-check?study=<slug> worker — ``(payload_dict, status)``.

    Validates every readout in a study against its baseline composites' real
    structure (the never-fabricate guard): ``{"composite": ref, "composites":
    [refs], "readouts": [{name, status, detail, composite}]}`` with ``status``
    ∈ ``ok|unresolved|not_in_structure|aspirational``. ``not_in_structure`` is
    the never-fabricate flag — a selector pointing at an observable no baseline
    composite exposes.

    A study may declare more than one baseline composite (e.g. a sweep over
    several analytic models); each readout belongs to whichever baseline
    exposes it, so a readout is validated against EVERY baseline composite and
    reported with its best status across them — ``ok`` if any baseline exposes
    it — rather than against ``baseline[0]`` alone (#1306). ``composite`` in the
    payload stays the first baseline (back-compat); ``composites`` lists all
    baselines checked, and each readout carries the ``composite`` that produced
    its reported status. If no baseline composite can build, returns a clear
    non-500 (422 + all readouts marked aspirational with a note), never a crash.
    """
    from vivarium_workbench.lib.study_spec import SLUG_RE, study_spec_path

    ws_root = Path(ws_root)
    if not SLUG_RE.match(slug or ""):
        return {"error": "invalid slug"}, 400

    # The shared layout-aware resolver (the one GET /api/study/{slug} uses):
    # honors workspace.yaml `layout:` and nested investigations/<inv>/studies/.
    sf = study_spec_path(ws_root, slug)
    if not sf.is_file():
        return {"error": f"study not found: {slug}"}, 404

    try:
        # Project legacy v2 shape (baseline: <str>) into the v3 baseline list.
        from vivarium_workbench.lib.spec_migration import migrate_v2_to_v3
        spec = migrate_v2_to_v3(yaml.safe_load(sf.read_text(encoding="utf-8")) or {})
        # v4 studies carry the baseline composite under conditions.baseline;
        # project it onto the legacy v3 baseline-list shape this worker reads
        # (mirrors readouts_views.py / study_runs.py / investigations.py).
        if spec.get("schema_version") == 4 and isinstance(spec.get("conditions"), dict):
            from vivarium_workbench.lib.investigations import (
                _project_v4_redesign_to_legacy_view,
            )
            spec = _project_v4_redesign_to_legacy_view(spec)
    except Exception as e:  # noqa: BLE001
        return {"error": f"study spec parse failed: {e}"}, 400

    baseline = spec.get("baseline") or []
    if not (isinstance(baseline, list) and baseline and isinstance(baseline[0], dict)):
        return {"error": "study has no baseline composite", "readouts": []}, 422
    # Every baseline composite the study declares (not just baseline[0]):
    # order-preserving, de-duplicated. A readout belongs to whichever baseline
    # exposes it, so each is validated against ALL of these (#1306).
    refs: list[str] = []
    for b in baseline:
        if isinstance(b, dict):
            c = b.get("composite")
            if isinstance(c, str) and c.strip() and c.strip() not in refs:
                refs.append(c.strip())
    if not refs:
        return {"error": "baseline entry has no composite ref", "readouts": []}, 422
    ref = refs[0]  # back-compat: the payload's top-level "composite"

    readouts = spec.get("readouts") or []

    def _aspirational_results(cref: str) -> list[dict]:
        # One composite can't build → surface its readouts as aspirational
        # (unverifiable) rather than crashing; they lose to an ``ok`` from
        # another baseline in the merge below.
        return [
            {"name": r.get("name", f"readout_{i}"), "status": "aspirational",
             "detail": f"composite {cref!r} could not be built — readout unverified",
             "composite": cref}
            for i, r in enumerate(readouts)
        ]

    def _check_ref(cref: str):
        """Validate every readout against one composite. Returns
        ``("readouts", [...])`` on a real build, ``("buildfail", reason)`` when
        that composite can't build, or ``("hard", (body, status))`` for a
        workspace-level error (501/500) that should abort the whole check.

        The build + available_observables + augment + validate_readouts all run
        in the env worker (live core + polars). The lineage-alias augmentation
        is the dashboard's agent-structure convention, applied worker-side
        before the general validator (never-fabricate: only a leading
        ``agents.<n>.`` stripped).
        """
        r = _call_obs_worker(ws_root, "study_readout_check", {"ref": cref, "spec": spec})
        if r is None:
            return "buildfail", "environment worker unavailable"
        if "__not_registered__" in r:
            try:
                params = _resolve_spec_params(ws_root, cref)
            except Exception as e:  # noqa: BLE001 (LookupError / parse → can't build)
                return "buildfail", str(e)
            r = _call_obs_worker(ws_root, "study_readout_check", {**params, "spec": spec})
            if r is None:
                return "buildfail", "environment worker unavailable"
        if "__no_validator__" in r:
            return "hard", ({"error": f"readout_validation unavailable: {r['__no_validator__']}"}, 501)
        if "__build_error__" in r:
            return "buildfail", r["__build_error__"]
        if "__introspect_error__" in r or "__validate_error__" in r:
            return "hard", ({"error": r.get("__introspect_error__") or r.get("__validate_error__"),
                             "composite": cref}, 500)
        results = r.get("readouts", [])
        for res in results:
            res.setdefault("composite", cref)
        return "readouts", results

    # Merge each composite's per-readout result, keeping the best status across
    # baselines (ok < aspirational < unresolved < not_in_structure). A readout
    # that any baseline exposes is ``ok``; one no baseline exposes stays flagged.
    _RANK = {"ok": 0, "aspirational": 1, "unresolved": 2, "not_in_structure": 3}

    def _rank(entry: dict) -> int:
        s = entry.get("status")
        return _RANK.get(s, 2) if isinstance(s, str) else 2

    best: dict[int, dict] = {}
    any_built = False
    build_notes: list[str] = []
    for cref in refs:
        kind, payload = _check_ref(cref)
        if kind == "hard":
            return payload  # 501/500 — workspace-level, abort
        if kind == "buildfail":
            build_notes.append(f"{cref}: {payload}")
            results = _aspirational_results(cref)
        else:
            any_built = True
            results = payload
        for i, res in enumerate(results):
            cur = best.get(i)
            if cur is None or _rank(res) < _rank(cur):
                best[i] = res

    merged = [best[i] for i in sorted(best)]

    if not any_built:
        # No baseline composite could be built → clear non-500 (as before),
        # every readout aspirational with a note.
        return {"composite": ref, "composites": refs, "readouts": merged,
                "note": "no baseline composite could be built: "
                        + "; ".join(build_notes)}, 422

    return {"composite": ref, "composites": refs, "readouts": merged}, 200


def observables_for_ref_payload(ws_root: Path, ref: str) -> dict:
    """Adapter for the SP4b linkage paths: ``ref -> {"leaves", "catalogs"}``.

    The ``vivarium_workbench.lib.linkage_index`` enrich callable wants the plain
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


def _observables_for_ref(ws_root: Path, ref: str):
    """GET /api/observables?ref=<id> worker — returns ``(json_bytes, status)``.

    Encodes :func:`build_observables`'s payload dict via ``_json_body``.
    Relocated from the retired ``server._observables_for_ref`` and retained for
    external consumers that import it by that name (the dashboard's FastAPI seam
    uses ``build_observables`` directly).
    """
    from vivarium_workbench.lib.json_serialize import _json_body
    body, status = build_observables(ws_root, ref)
    return _json_body(body), status


# Register this module's cache-clear with the active-workspace registry so a
# workspace switch invalidates it via active_workspace.invalidate().
from . import active_workspace as _aw  # noqa: E402
_aw.register_clear_cb(clear_cache)
