"""``GET /api/study-readouts`` worker — auto-lists a study's Readouts table from
the composite emit plan, overlaying authored ``study.yaml readouts:`` annotations.

The table is the union of (a) every emitter leaf path the composite exposes
(``available_observables(...).leaves`` — the paths the run actually saves) and
(b) authored readouts. An authored readout overlays its name/description/units/
notes onto the matching leaf (matched after stripping a leading ``agents.<n>.``).
Authored ``available`` readouts whose ``store_path`` is missing or not an emitted
leaf surface as ``not_in_emit_plan`` (never-fabricate); ``derived-needed`` /
``aspirational`` readouts (computed metrics, not raw emit paths) surface as
``derived`` and are exempt from that check.
"""

from __future__ import annotations

import re
import time as _time
from pathlib import Path

import yaml

from . import active_workspace as _aw

_READOUTS_CACHE: dict = {}
_READOUTS_CACHE_TTL_S = 300.0


class _ValidatorUnavailable(Exception):
    """readout_validation not importable in the worker's environment (→ 501)."""


def _available_observables_for_ref(ws_root: Path, ref: str) -> dict:
    """Worker-backed ``available_observables`` for a composite ``ref`` →
    ``{leaves, catalogs}``.

    Routes through the session's env worker (``observables{ref}``), falling back
    to an inline ``{state, schema}`` for a spec-file composite the registry
    doesn't know — the same plumbing ``observables_views.build_observables`` uses,
    so readouts never build the composite (or import polars) in the HTTP process.
    Raises ``_ValidatorUnavailable`` (→501) when the validator is absent, and
    ``RuntimeError`` on a build failure so the caller's soft-degrade path fires."""
    from .observables_views import _call_obs_worker, _resolve_spec_params
    r = _call_obs_worker(ws_root, "observables", {"ref": ref})
    if r is not None and "__not_registered__" in r:
        params = _resolve_spec_params(ws_root, ref)  # LookupError → build failure
        r = _call_obs_worker(ws_root, "observables", params)
    if r is None:
        raise RuntimeError("environment worker unavailable")
    if "__no_validator__" in r:
        raise _ValidatorUnavailable(r["__no_validator__"])
    if "__build_error__" in r or "__introspect_error__" in r:
        raise RuntimeError(r.get("__build_error__") or r.get("__introspect_error__"))
    return r  # {leaves, catalogs}

_LINEAGE_RE = re.compile(r"^agents\.\d+\.")
_GENERIC_LEAF = {"count", "id", "value"}

# ---------------------------------------------------------------------------
# Emitter & config block (spec §3.1 block 1)
# ---------------------------------------------------------------------------
#
# Static class/module map mirroring the ACTUAL imports in ``lib/emitters.py``
# (``_run_xarray`` / ``run_with_emitter``) — never fabricated, just named
# after where the dashboard's own write path imports each emitter from.
_EMITTER_CLASS_MODULE = {
    "xarray": ("XArrayEmitter", "pbg_emitters.xarray_emitter"),
    "sqlite": ("SQLiteEmitter", "pbg_emitters.sqlite_emitter"),
    "parquet": ("ParquetEmitter", "pbg_emitters.parquet_emitter"),
    "ram": ("RAMEmitter", "process_bigraph.emitter"),
}

# Where each output_kind's store lands, as the dashboard's own naming
# convention (``lib/emitters.py``: ``_run_xarray``'s ``<out_dir>/<run_id>.zarr``
# / study-convention ``runs.<run_id>.zarr``; ``composite_runs.inject_sqlite_emitter``'s
# ``runs.db``; the parquet hive root). Not a filesystem probe — a declared
# convention, consistent with this block describing the PLAN, not an observed run.
_EMITTER_OUTPUT_DIR_TEMPLATE = {
    "zarr": "<study>/runs.<run_id>.zarr",
    "sqlite": "<study>/runs.db",
    "parquet": "<study>/parquet/",
    "ram": "(in-memory — no on-disk store)",
}

# The flat-Step XArrayEmitter's transducer flush buffer (``lib/emitters.py``
# ``_xarray_emitter_config``: ``transducer.buffer.size``). Other emitters in
# this dashboard's write path don't buffer the same way, so they carry no
# declared buffer size (``None`` — the JS renders that as "—", never a
# fabricated number).
_EMITTER_BUFFER = {"xarray": 3}

# Declared numeric dtype for an emitted leaf. This is the CONTRACT dtype the
# xarray write path commits to (``view_from_emit_paths(..., dtype="<f8")``,
# ``lib/emitters.py``) — a best-effort default applied to every emitted leaf
# regardless of the workspace's chosen emitter, since the dashboard doesn't
# introspect per-column runtime types at design time (that's Results, not
# Readouts).
_LEAF_DTYPE = "float64"
_LEAF_ITEMSIZE = 8  # bytes per float64 element


def clear_cache() -> None:
    _READOUTS_CACHE.clear()


def _emitter_block(spec: dict, study_dir: Path) -> dict:
    """Design-time emitter & config block (spec §3.1 block 1): ``{name,
    class_name, module, output_kind, interval, buffer, output_dir, scope}``.

    Sourced entirely from ``lib/emitters.py`` (``default_emitter``/
    ``output_kind``) plus the study/workspace ``runtime.default_emitter``
    override ``default_emitter`` already resolves — never builds the
    composite or touches a run store, so this never blocks on a slow build.
    Always computable from a parsed study spec, so callers can attach it to
    every payload (including soft-degrade / error paths).
    """
    from . import emitters as em

    runs_db = Path(study_dir) / "runs.db"
    name = em.default_emitter(spec, runs_db)
    kind = em.output_kind(name)
    class_name, module = _EMITTER_CLASS_MODULE.get(name, (None, None))
    return {
        "name": name,
        "class_name": class_name,
        "module": module,
        "output_kind": kind,
        "interval": 1,
        "buffer": _EMITTER_BUFFER.get(name),
        "output_dir": _EMITTER_OUTPUT_DIR_TEMPLATE.get(kind, str(study_dir)),
        # The emit surface this panel describes is the DECLARED store-leaf
        # set (``observables_views.build_observables`` leaves), never an
        # incidental full-state dump — spec §3.1 "emit scope (declared)".
        "scope": "declared",
    }


def _declared_n_steps(spec: dict) -> "int | None":
    """The study's declared step count, from ``baseline[0].params.n_steps``
    (the same field ``study_runs.run_study_baseline`` pops before launch).
    ``None`` when not declared — callers render a symbolic ``n_steps`` dim
    rather than fabricate a number."""
    baseline = spec.get("baseline") or []
    if isinstance(baseline, list) and baseline and isinstance(baseline[0], dict):
        params = baseline[0].get("params")
        if isinstance(params, dict):
            n = params.get("n_steps")
            if isinstance(n, (int, float)) and not isinstance(n, bool):
                return int(n)
    return None


def _leaf_shape(leaf: str, catalogs: dict, n_steps: "int | None") -> list:
    """``[n_steps_or_symbol]`` for a scalar leaf, ``[n_steps_or_symbol,
    catalog_len]`` for a leaf backed by a static ``_labels`` catalog
    (``available_observables``'s ``catalogs`` — e.g. a per-monomer vector)."""
    shape: list = [n_steps if isinstance(n_steps, int) else "n_steps"]
    cat = catalogs.get(_strip_lineage(leaf))
    if cat is None:
        cat = catalogs.get(leaf)
    if isinstance(cat, list):
        shape.append(len(cat))
    return shape


def _shape_bytes(shape: list) -> "int | None":
    """Best-effort total byte count for ``shape`` at ``_LEAF_DTYPE`` width, or
    ``None`` when a dimension is symbolic (unknown ``n_steps``) — never
    fabricate a byte count for an unresolved dimension."""
    if any(not isinstance(d, int) for d in shape):
        return None
    count = 1
    for d in shape:
        count *= d
    return count * _LEAF_ITEMSIZE


def _strip_lineage(path: str) -> str:
    """Strip a leading ``agents.<n>.`` so authored bare paths match emit leaves."""
    return _LINEAGE_RE.sub("", path or "")


def _short_name(leaf: str) -> str:
    """Readable default name from a dotted leaf path."""
    segs = [s for s in (leaf or "").split(".") if s]
    if not segs:
        return leaf or ""
    last = segs[-1]
    if last in _GENERIC_LEAF and len(segs) >= 2:
        return f"{segs[-2]}_{last}"
    return last


def _split_saved_excluded(emitted_leaves, available_leaves):
    """Partition the observable surface into saved (emitted) and excluded rows.

    Both args are lists of dotted store paths. Comparison is on lineage-stripped
    keys so `_strip_lineage`-equivalent paths match. Returns (saved, excluded)
    where each is a list of {store_path, name} dicts.

    Correction (Fable Increment A #5, see
    ``docs/superpowers/specs/2026-08-01-study-design-fable-pass.md`` §5.1):
    an earlier take assumed ``readouts_views._available_observables_for_ref``
    and the per-run emit plan were the SAME leaf set (both route to the
    ``observables{ref}`` env-worker RPC → ``collect_input_ports(state)``, a
    structural walk of the entire built state tree) — so "available" and
    "emitted" always compared equal and ``excluded`` was structurally always
    ``[]``. That is wrong: ``composite_runs.collect_emit_paths_from_spec(spec)``
    already selects a strict SUBSET of that surface on every study launch
    (unions ``readouts[]``/``tests[]``/``visualizations[]`` paths), and is now
    the ``emitted_leaves`` this helper is called with (see
    ``_compute_excluded``) — so ``excluded`` is a real, usually non-empty set
    for any study that declares readouts/tests/figures.
    """
    emitted_keys = {_strip_lineage(l) for l in emitted_leaves}
    saved = [{"store_path": l, "name": _short_name(l)} for l in sorted(emitted_leaves)]
    excluded = [
        {"store_path": l, "name": _short_name(l)}
        for l in sorted(available_leaves)
        if _strip_lineage(l) not in emitted_keys
    ]
    return saved, excluded


def _compute_excluded(spec: dict, available: dict) -> dict:
    """Real saved/excluded split (Fable Increment A #5) + the R2 three-state
    marker, given an already-resolved ``available`` (``{leaves, catalogs}``).

    ``saved`` is the spec-selected emit set — ``composite_runs
    .collect_emit_paths_from_spec(spec)``, the same function ``study_runs
    .run_study_baseline`` calls before every launch (``composite_runs.py:888``).
    That function returns slash-form paths, each also expanded into its
    ``agents/0/…`` form; normalise to the dotted, lineage-stripped key
    ``_split_saved_excluded`` already compares on (spec §5.2's "Normalisation"
    note).

    Three states, never conflated (R2 "absent ≠ empty"):
      - the spec declares nothing → ``run_runner._emit_paths_for`` falls back
        to saving *everything* (`req.emit_paths or cr.all_store_paths(state)`,
        ``lib/run_runner.py:210-217``) — a real, total selection, not an
        unset one. ``excluded_state="empty"``, ``emit_selection="total"``.
      - the spec declares a subset → ``excluded`` is available − saved (may
        legitimately be ``[]`` if the subset happens to cover the whole
        surface; the state stays ``"computed"`` because it WAS computed).
        ``excluded_state="computed"``, ``emit_selection="subset"``.
    Callers must wrap this in try/except: a computation failure (e.g. an
    incompatible ``composite_runs`` import) must degrade to
    ``excluded_state="unavailable"`` + ``reason``, never fabricate ``[]``.
    """
    from . import composite_runs as cr

    declared = cr.collect_emit_paths_from_spec(spec)
    available_leaves = list(available.get("leaves") or [])
    if not declared:
        return {"excluded": [], "excluded_state": "empty", "emit_selection": "total",
                "emit_is_total": True}
    dotted_declared = sorted({_strip_lineage(p.replace("/", ".")) for p in declared if p})
    _saved_ignored, excluded = _split_saved_excluded(dotted_declared, available_leaves)
    return {"excluded": excluded, "excluded_state": "computed", "emit_selection": "subset",
            "emit_is_total": False}


def _merge_readouts(spec: dict, available: dict, *, plan_available: bool = True,
                     n_steps: "int | None" = None) -> list[dict]:
    """Pure merge of emit-plan leaves + authored readouts → ordered row dicts.

    Headless-friendly (no composite build): pass ``available={"leaves": [...]}``.

    ``plan_available`` distinguishes "the emit plan was built and this authored
    path is genuinely absent" (→ ``not_in_emit_plan``) from "we could not build
    the emit plan, so nothing is verified" (→ ``unverified``). The latter happens
    on a remote build (no local ParCa cache); flagging those rows
    ``not_in_emit_plan`` would falsely imply they were checked and found missing.

    ``n_steps`` (spec §3.1 block 3, "Outputs & shapes") is the study's declared
    step count (``_declared_n_steps``); emitted-leaf rows get a ``dtype``/
    ``shape``/(best-effort)``bytes`` triple. Rows that aren't a confirmed emit
    leaf (derived / not_in_emit_plan / unverified) carry no shape — there's no
    verified structure to describe.
    """
    leaves = list(available.get("leaves") or [])
    catalogs = dict(available.get("catalogs") or {})
    # Index authored readouts by lineage-stripped store_path for overlay match.
    # Fix 3: honour the legacy ``observables:`` key as an annotation source.
    authored = [r for r in (spec.get("readouts") or spec.get("observables") or []) if isinstance(r, dict)]
    overlay: dict[str, dict] = {}
    for r in authored:
        sp = r.get("store_path")
        if isinstance(sp, str) and sp.strip():
            overlay[_strip_lineage(sp.strip())] = r

    rows: list[dict] = []
    matched_ids: set[int] = set()

    for leaf in sorted(leaves):
        key = _strip_lineage(leaf)
        ann = overlay.get(key)
        if ann is not None:
            matched_ids.add(id(ann))
        shape = _leaf_shape(leaf, catalogs, n_steps)
        row = {
            "store_path": leaf,
            "name": (ann or {}).get("name") or _short_name(leaf),
            "description": (ann or {}).get("description", "") or "",
            "units": (ann or {}).get("units", "") or "",
            "index_by": (ann or {}).get("index_by"),
            "notes": (ann or {}).get("notes", "") or "",
            "annotated": ann is not None,
            "emit_status": "emitted",
            "dtype": _LEAF_DTYPE,
            "shape": shape,
        }
        nbytes = _shape_bytes(shape)
        if nbytes is not None:
            row["bytes"] = nbytes
        rows.append(row)

    # Fix 4: for orphan detection, build a set of all stripped leaf keys so that
    # a duplicate authored store_path (last-wins in overlay) doesn't false-flag
    # the earlier authored entry as not_in_emit_plan — it IS covered by a leaf row.
    leaf_keys: set[str] = {_strip_lineage(l) for l in leaves}

    # Authored readouts that did not match any emit leaf.
    for r in authored:
        if id(r) in matched_ids:
            continue
        sp_raw = r.get("store_path")
        if isinstance(sp_raw, str) and sp_raw.strip() and _strip_lineage(sp_raw.strip()) in leaf_keys:
            continue  # covered by a leaf row (duplicate store_path — overlay last-wins)
        status = (r.get("status") or "").strip()
        derived = status in ("derived-needed", "aspirational")
        rows.append({
            "store_path": (r.get("store_path") or "") if not derived else "",
            "name": r.get("name") or "readout",
            "description": r.get("description", "") or "",
            "units": r.get("units", "") or "",
            "index_by": r.get("index_by"),
            "notes": r.get("notes", "") or "",
            "annotated": True,
            "emit_status": (
                "derived" if derived
                else ("not_in_emit_plan" if plan_available else "unverified")
            ),
        })

    return rows


def _run_store_available(ws_root: Path, slug: str) -> "dict | None":
    """Fallback emit surface: the scalar leaf paths the study's LATEST persisted
    run actually saved, shaped like ``_available_observables_for_ref``'s
    ``{leaves, catalogs}`` so ``_merge_readouts`` can consume it directly.

    Used when the design-time emit plan is empty or the composite can't be built
    (e.g. a snapshot publish, or a composite that declares no in-document emitter
    but whose runs persist observables). Reuses the SAME store readers the
    Results tab uses (``results_views._pick_latest_run`` +
    ``explorer_data.list_observables``). Returns ``None`` (never raises) when
    there is no run, no on-disk store, or the store can't be read — the caller
    then keeps the empty plan.
    """
    try:
        from . import results_views as _rv
        from . import explorer_data as _ed
        row = _rv._pick_latest_run(ws_root, slug)
        if not row:
            return None
        db_path = row.get("store_path") or row.get("db_path")
        if not db_path:
            return None
        obs = _ed.list_observables(str(db_path), run_id=row.get("run_id"), workspace=ws_root)
        # list_observables returns {"categories": {cat: [leaf, ...]}} — unwrap it
        # the same way results_views does before flattening to scalar leaves.
        leaves = _rv._scalar_leaf_paths(obs.get("categories") or {})
        if not leaves:
            return None
        return {"leaves": leaves, "catalogs": {},
                "run_id": row.get("run_id"),
                "run_label": row.get("sim_name") or row.get("label") or row.get("run_id")}
    except Exception:  # noqa: BLE001 — a fallback must never break the panel
        return None


def build_study_readouts(ws_root: Path, slug: str) -> tuple[dict, int]:
    """Worker for ``GET /api/study-readouts?study=<slug>`` → ``(payload, status)``.

    200 with ``{composite, rows, note}``. Resolution/ref errors → 4xx. If the
    composite cannot build, returns 422 with authored-only rows + an explanatory
    ``note`` (never a 500).
    """
    from .study_spec import SLUG_RE, study_spec_file
    from .spec_migration import migrate_v2_to_v3

    ws_root = Path(ws_root)
    if not SLUG_RE.match(slug or ""):
        return {"error": "invalid slug"}, 400

    # Resolve the study dir honoring the workspace layout (workspace.yaml may
    # nest studies under e.g. workspace/studies — v2ecoli). Fall back to the
    # flat root layout if WorkspacePaths can't load.
    try:
        from .workspace_paths import WorkspacePaths
        wp = WorkspacePaths.load(ws_root)
        study_dir = wp.studies / slug
        if not study_dir.is_dir():
            study_dir = wp.investigations / slug
    except Exception:  # noqa: BLE001
        study_dir = ws_root / "studies" / slug
        if not study_dir.is_dir():
            study_dir = ws_root / "investigations" / slug
    sf = study_spec_file(study_dir)
    if not sf.is_file():
        return {"error": f"study not found: {slug}"}, 404
    try:
        spec = migrate_v2_to_v3(yaml.safe_load(sf.read_text(encoding="utf-8")) or {})
        # v4 studies carry the baseline composite under conditions.baseline;
        # project it onto the legacy v3 baseline-list shape this worker reads
        # (mirrors study_runs.py / investigations.py).
        if spec.get("schema_version") == 4 and isinstance(spec.get("conditions"), dict):
            from vivarium_workbench.lib.investigations import (
                _project_v4_redesign_to_legacy_view,
            )
            spec = _project_v4_redesign_to_legacy_view(spec)
    except Exception as e:  # noqa: BLE001
        return {"error": f"study spec parse failed: {e}"}, 400

    # Emitter identity/config is computable from the parsed spec alone (never
    # builds the composite) — attach it to EVERY payload from here on,
    # including the soft-degrade / error paths below, per spec §3.1.
    try:
        emitter_block = _emitter_block(spec, study_dir)
    except Exception as e:  # noqa: BLE001 — emitter identity must never 500 the panel
        emitter_block = {
            "name": None, "class_name": None, "module": None, "output_kind": None,
            "interval": None, "buffer": None, "output_dir": None, "scope": "declared",
            "error": f"emitter block unavailable: {e}",
        }

    baseline = spec.get("baseline") or []
    if not (isinstance(baseline, list) and baseline and isinstance(baseline[0], dict)):
        return {"error": "study has no baseline composite", "rows": [], "emitter": emitter_block}, 422
    ref = baseline[0].get("composite")
    if not ref:
        return {"error": "baseline entry has no composite ref", "rows": [], "emitter": emitter_block}, 422

    ckey = ("readouts", str(ws_root), slug)
    hit = _READOUTS_CACHE.get(ckey)
    if hit is not None and (_time.time() - hit[0]) < _READOUTS_CACHE_TTL_S:
        return {**hit[1], "cached": True}, 200

    try:
        available = _available_observables_for_ref(ws_root, ref)
    except _ValidatorUnavailable as e:
        return {"error": f"readout_validation unavailable: {e}", "emitter": emitter_block}, 501
    except Exception as e:  # noqa: BLE001
        # The design-time emit plan couldn't be built (e.g. a snapshot publish
        # with no live env worker). Fall back to what the study's latest run
        # actually persisted, so the Readouts table still shows real emitted
        # paths + shapes instead of going empty.
        fb = _run_store_available(ws_root, slug)
        if fb:
            n_steps = _declared_n_steps(spec)
            rows = _merge_readouts(spec, fb, n_steps=n_steps)
            note = ("Emit plan not built; showing the store-leaf paths from this "
                    "study's latest persisted run "
                    f"({fb.get('run_label') or fb.get('run_id')}).")
            return {"composite": ref, "rows": rows, "excluded": [],
                    "excluded_state": "unavailable", "emitter": emitter_block,
                    "readouts_source": "run-store", "note": note, "degraded": True}, 200
        rows = _merge_readouts(spec, {"leaves": []}, plan_available=False)
        reason = f"composite {ref!r} could not be built: {e}"
        # On a remote build (a materialized repo@commit, marked by .viv-build.json)
        # the composite CANNOT build — the workspace has no local ParCa cache, by
        # design. That's expected, not an error: degrade softly (200) with a clear
        # note instead of a 422 + a raw filesystem traceback.
        if (ws_root / ".viv-build.json").is_file():
            return {"composite": ref, "rows": rows, "excluded": [], "excluded_state": "unavailable",
                    "emitter": emitter_block,
                    "reason": "Emit-plan verification is unavailable on a remote build "
                              "(no local ParCa cache); showing authored readouts.",
                    "degraded": True, "remote_build": True,
                    "note": "Emit-plan verification is unavailable on a remote build "
                            "(no local ParCa cache); showing authored readouts."}, 200
        return {"composite": ref, "rows": rows, "excluded": [], "excluded_state": "unavailable",
                "emitter": emitter_block, "reason": reason, "degraded": True,
                "note": f"composite {ref!r} could not be built — rows unverified: {e}"}, 422

    n_steps = _declared_n_steps(spec)
    # The composite built but declared no emit leaves (e.g. a composite whose
    # runs persist observables via an out-of-document emitter). Fall back to the
    # latest run's store-leaf paths so the table isn't empty.
    readouts_source = None
    if not (available.get("leaves")):
        fb = _run_store_available(ws_root, slug)
        if fb:
            available = fb
            readouts_source = "run-store"
    payload = {"composite": ref, "rows": _merge_readouts(spec, available, n_steps=n_steps),
               "excluded": [], "note": "", "emitter": emitter_block}
    if readouts_source:
        payload["readouts_source"] = readouts_source
        payload["note"] = ("Composite declares no in-document emit plan; showing "
                           "the store-leaf paths from this study's latest persisted run "
                           f"({available.get('run_label') or available.get('run_id')}).")
    try:
        payload.update(_compute_excluded(spec, available))
    except Exception as e:  # noqa: BLE001 — degrade, never 500
        payload["excluded"] = []
        payload["excluded_state"] = "unavailable"
        payload["reason"] = f"could not compute the excluded set: {e}"
    _READOUTS_CACHE[ckey] = (_time.time(), payload)
    if len(_READOUTS_CACHE) > 32:
        _READOUTS_CACHE.pop(next(iter(_READOUTS_CACHE)))
    return payload, 200


_aw.register_clear_cb(clear_cache)
