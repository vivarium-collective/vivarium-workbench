"""Emitter broker — the single locus for ``output_kind → reader / label / chart``.

Before this module the dashboard chose readers, emitter labels, and chart
sources with inline ``if kind == "xarray"/"parquet"/"sqlite"`` branches scattered
across ``study_charts``, ``simulations_index``, ``explorer_data`` and ``registry``.
This broker centralizes that dispatch: it resolves each emitter's CONTRACT from
``pbg-emitters`` (Task 1) and maps a store's ``output_kind`` to the EXISTING
reader / label / chart-source functions — never reimplementing a reader body or
changing any output.

Task 4 was zero-behavior-change. Task 6 then flipped the default emitter to
``"xarray"`` (made its runtime deps mandatory) — a workspace/study opts back to
sqlite via ``runtime.default_emitter: sqlite``. ``reader_for`` is the ONLY place
a ``kind → reader`` mapping may live.

All cross-``lib`` imports are lazy (inside functions) so this module can be
imported by the very modules it dispatches into without an import cycle.
"""
from __future__ import annotations

import copy
import traceback
from pathlib import Path
from typing import Callable

# The framework default. Task 6 flipped this from "sqlite" to "xarray": the
# dashboard now prefers the XArray/zarr emitter for new runs and read-source
# selection. A workspace/study opts back out with ``runtime.default_emitter:
# sqlite`` (still honored below), and the broker's empty-view auto-fallback to
# sqlite still fires when a composite declares no emit_paths. xarray's runtime
# deps (xarray + zarr) are mandatory as of Task 6 (see pyproject.toml).
DEFAULT_EMITTER = "xarray"

# The workspace/runtime emitter NAME is "xarray"; the store kind it writes is
# "zarr". Every other accepted name already equals its output_kind.
_OUTPUT_KIND_ALIASES = {"xarray": "zarr"}

# Emitter names a workspace may declare via ``runtime.default_emitter`` (ports
# study_charts._emitter_choice._ACCEPTED).
_ACCEPTED_EMITTERS = ("xarray", "sqlite", "parquet")


# ---------------------------------------------------------------------------
# Contract resolution + output_kind
# ---------------------------------------------------------------------------

def resolve_contract(name) -> "object":
    """Return the ``pbg_emitters.EmitterContract`` for an emitter name/class.

    Thin delegate to ``pbg_emitters.contract_for`` (Task 1). Raises whatever
    that raises (``KeyError`` for an unregistered name).
    """
    from pbg_emitters import contract_for
    return contract_for(name)


def output_kind(name: str) -> str:
    """Store kind a named emitter writes: ``sqlite`` / ``zarr`` / ``parquet`` / ``ram``.

    Resolves through the pbg-emitters contract when the emitter is registered
    (this is where ``xarray → zarr`` comes from canonically). For unknown /
    unregistered names — e.g. when the optional emitter extra isn't installed —
    fall back to the static alias map / lowercased name so callers still get a
    stable kind without importing heavy deps.
    """
    try:
        return resolve_contract(name).output_kind
    except Exception:  # noqa: BLE001 — unregistered/extra-not-installed → static fallback
        n = str(name or "").strip().lower()
        return _OUTPUT_KIND_ALIASES.get(n, n)


def normalize_emitter_name(name) -> str:
    """Lowercase + strip an emitter NAME (not its output_kind).

    Used where the raw declared name must be matched against class names
    (e.g. the Registry ``default_emitter`` badge) — deliberately does NOT apply
    the ``xarray → zarr`` output_kind alias, which would break that match.
    """
    return str(name or "").strip().lower()


# ---------------------------------------------------------------------------
# Source resolution + reader dispatch
# ---------------------------------------------------------------------------

def read_source(path, workspace=None) -> "tuple[str | None, Path | None]":
    """Resolve a run reference to ``(kind, store Path)``.

    Pure delegate to ``explorer_data._resolve_run_source`` (the canonical
    on-disk store detector); kept here so callers select the source through the
    broker rather than reaching into explorer_data directly.
    """
    from vivarium_dashboard.lib import explorer_data
    return explorer_data._resolve_run_source(path, workspace)


def reader_for(kind: str) -> Callable:
    """Return the EXISTING per-kind trace reader for ``kind``.

    The SINGLE allowed locus mapping a store kind to a trace-extraction
    function. Returns the existing functions unchanged (signatures preserved);
    callers invoke them with the kind-appropriate arguments. Raises ``KeyError``
    for kinds without a single trace reader (e.g. ``parquet``, which explorer
    reads column-by-column inline).
    """
    from vivarium_dashboard.lib import comparative_viz
    table = {
        "zarr": comparative_viz._extract_trace_from_zarr,
        "sqlite": comparative_viz._extract_trace,
    }
    return table[kind]


# ---------------------------------------------------------------------------
# Emitter-choice + label ports (behavior-identical to the originals)
# ---------------------------------------------------------------------------

def default_emitter(spec: "dict | None", runs_db: "Path | None") -> str:
    """Workspace's read-source emitter NAME — ``xarray`` / ``parquet`` / ``sqlite``.

    Ports ``study_charts._emitter_choice``. Resolves ``runtime.default_emitter``
    from (1) the study spec's runtime block, then (2) the nearest ancestor
    ``workspace.yaml``'s runtime block, defaulting to ``DEFAULT_EMITTER``.
    Deliberately does NOT probe disk state — declaring no emitter must not
    silently flip read sources (that hides drift).
    """
    spec_rt = (spec or {}).get("runtime") or {}
    if isinstance(spec_rt, dict):
        declared = normalize_emitter_name(spec_rt.get("default_emitter"))
        if declared in _ACCEPTED_EMITTERS:
            return declared
    if runs_db is not None:
        # Studies layouts vary (flat <ws>/studies/<slug>/runs.db or nested
        # <ws>/workspace/studies/<slug>/runs.db) — walk up to the nearest
        # workspace.yaml rather than assuming a fixed depth.
        for ancestor in Path(runs_db).parents:
            ws_yaml = ancestor / "workspace.yaml"
            if not ws_yaml.is_file():
                continue
            try:
                import yaml as _yaml
                ws = _yaml.safe_load(ws_yaml.read_text(encoding="utf-8")) or {}
                ws_rt = ws.get("runtime") or {}
                if isinstance(ws_rt, dict):
                    ws_declared = normalize_emitter_name(ws_rt.get("default_emitter"))
                    if ws_declared in _ACCEPTED_EMITTERS:
                        return ws_declared
            except (OSError, Exception):  # noqa: BLE001 — read-fail = default
                pass
            break  # nearest workspace.yaml is the workspace root; don't climb past it
    return DEFAULT_EMITTER


def label_for_run(row: dict, workspace) -> str:
    """Emitter that persisted a run row: ``parquet`` / ``xarray`` / ``sqlite`` / ``none``.

    Ports ``simulations_index._emitter_for_row`` (note: that helper takes
    ``(workspace, row)`` — this broker entry takes ``(row, workspace)``). For
    SQLite-table rows it still disk-probes ``.pbg/runs/<run_id>`` for a backfilled
    zarr store before defaulting to ``sqlite``.
    """
    # A remote run lands its native store next to runs.db, so the row may already
    # carry the derived emitter; honor it (the .pbg/runs probe below only covers
    # the LOCAL backfill layout).
    em0 = row.get("emitter")
    if isinstance(em0, str) and em0 in ("xarray", "parquet"):
        return em0
    src = row.get("source")
    if src == "parquet":
        return "parquet"
    if src == "xarray":
        return "xarray"
    if src == "study_yaml":
        # Surface the emitter the run DECLARES in study.yaml (plain string or a
        # structured {"kind": ...} dict); normalise to the kind string so the
        # downstream label mapping never sees a dict. Else 'none'.
        em = row.get("emitter")
        if isinstance(em, dict):
            em = em.get("kind")
        return em if isinstance(em, str) and em else "none"
    rid = row.get("run_id")
    if rid:
        run_dir = Path(workspace) / ".pbg" / "runs" / str(rid)
        try:
            if run_dir.is_dir() and (
                list(run_dir.glob("store.zarr"))
                or list(run_dir.glob("*/store.zarr"))
                or list(run_dir.glob("*/*/store.zarr"))
            ):
                return "xarray"
        except Exception:
            pass
    return "sqlite"


# ---------------------------------------------------------------------------
# Chart source-selection port (keyed on output_kind)
# ---------------------------------------------------------------------------

def chart_source(
    spec: "dict | None",
    runs_db: "Path | None",
    study_dir: "Path | None",
    path_specs: "list[tuple[str, int | None]]",
) -> list:
    """Alternate-store chart sources for a study, as ``[(label, {key: (xs, ys)})]``.

    Ports the ``study_charts`` source-selection: when the workspace's default
    emitter writes zarr / parquet, locate the latest such store under
    ``study_dir`` and single-pass-extract every requested observable path.
    Keyed on ``output_kind`` (``xarray → zarr``). Returns an empty list for the
    sqlite default (the sqlite chain is assembled by the caller as before).
    """
    from vivarium_dashboard.lib import study_charts

    kind = output_kind(default_emitter(spec, runs_db))
    sources: list[tuple[str, dict]] = []
    if study_dir is None:
        return sources
    if kind == "zarr":
        zarr_path = study_charts._latest_zarr_for_study(study_dir)
        if zarr_path is not None:
            sources.append(
                ("study-zarr", study_charts._extract_paths_from_zarr(zarr_path, path_specs))
            )
    elif kind == "parquet":
        hive_root = study_charts._latest_parquet_for_study(study_dir)
        if hive_root is not None:
            sources.append(
                ("study-parquet", study_charts._extract_paths_from_parquet(hive_root, path_specs))
            )
    return sources


# ---------------------------------------------------------------------------
# Uniform WRITE path — inject any emitter as a process-bigraph Step and run.
# ---------------------------------------------------------------------------
#
# ``run_with_emitter`` is the single write-side locus mirroring ``reader_for``
# on the read side: every emitter is injected as a Step, the Composite drives
# it via ``run(N)``, and the function returns a small provenance dict. It keeps
# the dashboard's TWO write paths (``run_runner.execute`` — the Composite
# Explorer "Run" tab — and, for non-v2ecoli emitters, the study-run subprocess)
# from each re-implementing per-emitter ``inject + run + flush`` branches.
#
# Dispatch is keyed on ``output_kind`` (so ``xarray → zarr``). The default
# emitter stays ``sqlite`` (Task 6 flips it), so default runs produce
# byte-identical sqlite/parquet/ram output to the pre-broker inline code: the
# sqlite branch reuses ``inject_emitter_for_paths`` + ``inject_sqlite_emitter``
# and a plain per-tick ``run(1)`` loop exactly as ``run_runner.execute`` did.


def _drive(composite, steps: int, progress_cb: "Callable | None") -> None:
    """Advance ``composite`` one tick at a time, calling ``progress_cb(step)``.

    Plain ``run(1)`` per tick (NOT division-aware) — byte-identical to the loop
    ``run_runner.execute`` ran before this broker existed. ``progress_cb`` may
    raise to abort the run (``run_runner`` uses this for its max-runtime guard);
    the exception propagates so the caller can record a ``failed`` status.
    """
    for step in range(1, int(steps) + 1):
        composite.run(1)
        if progress_cb is not None:
            progress_cb(step)


def _flush_step_emitters(composite) -> "tuple[int, list[tuple[object, BaseException]]]":
    """Close every buffering Step emitter in a composite so its trailing batch
    lands on disk. Returns ``(closed, errors)``.

    Generalises ``run_runner._flush_parquet_emitters``: targets any node
    ``instance`` exposing a callable ``close`` that buffers to a store — keyed
    off the emitter's own ``out_uri`` (ParquetEmitter, the tyssue
    DataFrameParquetEmitter, …) OR a ``writer.out_uri`` (XArrayEmitter, whose
    store handle lives on its writer). Skips already-closed emitters and never
    raises (a failed close must not sink the run), so callers needn't import
    workspace-specific emitter classes.

    A close that raises (notably the flat-Step XArrayEmitter's close-time
    AssertionError when its buffer never filled → an EMPTY store) is COLLECTED
    into ``errors`` and returned rather than swallowed via ``traceback`` — so the
    caller's guard can SEE the failure and act on it (see ``_run_xarray``). The
    traceback is still printed for diagnosability.
    """
    closed = 0
    errors: list[tuple[object, BaseException]] = []

    def _buffers(inst) -> bool:
        if not callable(getattr(inst, "close", None)):
            return False
        if getattr(inst, "_closed", False):
            return False
        if hasattr(inst, "out_uri"):
            return True
        return hasattr(getattr(inst, "writer", None), "out_uri")

    def _walk(node):
        nonlocal closed
        if isinstance(node, dict):
            inst = node.get("instance")
            if inst is not None and _buffers(inst):
                try:
                    inst.close(success=True)
                    closed += 1
                except Exception as exc:  # noqa: BLE001 — surface, don't sink the run
                    errors.append((inst, exc))
                    traceback.print_exc()
            for v in node.values():
                _walk(v)

    _walk(getattr(composite, "state", None) or {})
    return closed, errors


def _zarr_store_has_observable_data(store) -> bool:
    """True iff the zarr store at ``store`` holds at least one non-empty OBSERVABLE
    leaf array.

    The flat-Step XArrayEmitter's async writer only persists a complete buffer:
    a run shorter than the buffer (size 3) writes the hive PARTITION coordinates
    (``time_gen`` etc. under ``experiment_id=…/variant=…/lineage_seed=…``) but —
    deterministically for a 1-tick run, and NON-deterministically for a 2-tick
    run (an executor race) — omits the observable leaf groups entirely, leaving
    a store with no actual data. ``num_writes`` does NOT distinguish these (a
    2-tick run reports ``num_writes==1`` whether or not the leaf landed), so the
    only reliable empty-detection is to look for real observable data.

    An OBSERVABLE leaf group is one whose path has a non-hive segment (a segment
    without ``=`` — i.e. a store path like ``…/counter_store/value``, not a
    ``key=value`` partition segment). A real run always has such a group with a
    non-empty data array; an empty short-run store has only the partition root.
    Sizes are read from zarr metadata (lazy — no data load), and the scan
    short-circuits on the first hit, so this is cheap even on large stores.
    """
    p = Path(store)
    if not p.exists():
        return False
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            import xarray as xr
            dt = xr.open_datatree(str(p), engine="zarr")
        except Exception:  # noqa: BLE001 — unreadable/partial store counts as empty
            return False
        for group in dt.groups:
            segs = [s for s in group.strip("/").split("/") if s]
            if not any("=" not in s for s in segs):
                continue  # only hive partition segments → not an observable leaf
            ds = dt[group].ds
            for var in ds.data_vars:
                try:
                    if int(ds[var].size) > 0:
                        return True
                except Exception:  # noqa: BLE001
                    continue
    return False


def _normalize_emit_path(p: str) -> str:
    """A '/'-joined, dot-normalised, edge-stripped store path for matching."""
    return str(p or "").strip().strip("/").replace(".", "/")


def _xarray_emitter_config(store: str, view: list, emitter_config: "dict | None") -> dict:
    """Build the flat-Step XArrayEmitter config (the Task-2 wiring).

    ``strategy='flat'`` + ``emit_root=()`` consume the wired flat state directly
    (no colony/lineage envelope, no external driver loop). A colony
    ``emitter_config`` (e.g. ``{"strategy": "colony", "emit_root": [...]}``) is
    layered on top so a workspace can opt into lineage semantics.
    """
    config = {
        "out_uri": store,
        "strategy": "flat",
        "emit_root": [],
        "transducer": {
            "predicate": [[{"subsample": {"interval": 1}}]],
            # Small flush buffer (3 = the transducer minimum). The XArrayEmitter
            # only persists a buffer when it FILLS during the run; its final
            # close-flush asserts ``not include_static``, which holds only once
            # at least one full-buffer write has happened (num_writes > 0). With
            # the old size-100 buffer a sub-100-step run — the common Composite
            # Explorer "Run" case, and run_runner.execute is the ONLY caller of
            # run_with_emitter — never filled, so the close-flush AssertionError
            # was swallowed and the zarr store left EMPTY. Now that Task 6 makes
            # xarray the DEFAULT emitter, that empty-store path would be the
            # default for short runs; a minimal buffer makes every short run
            # flush real data. Trade-off: more, smaller zarr chunks (fine for
            # interactive Explorer runs). Runs of <~4 emit-ticks still under-fill.
            "buffer": {"size": 3},
        },
        "view": view,
        "writer": {
            "backend": "zarr",
            "store": store,
            "buffers_per_chunk": 1,
            "backend_config": {"format": 3},
        },
        "metadata": {"experiment_id": ""},
        "metadata_keys": [],
        "metadata_validators": {},
        "output_metadata": {},
        "debug": False,
    }
    if emitter_config:
        config.update(emitter_config)
    return config


def _inject_xarray_step(composite, core, config: dict, inputs: dict) -> None:
    """Inject XArrayEmitter into ``composite`` as a flat Step (Task-2 wiring).

    Mirrors ``process_bigraph.emitter.add_emitter_to_composite`` (merge an
    emitter step spec, register it in ``step_paths``, rebuild the step network)
    but supplies the rich XArrayEmitter ``config`` alongside the auto-derived
    ``emit``/``inputs`` wiring. The composite itself drives the emitter via
    ``run(N)`` — there is NO external driver loop.
    """
    from bigraph_schema import set_path

    emit = {port: "node" for port in inputs}
    emitter_state = {
        "_type": "step",
        "address": "local:XArrayEmitter",
        "config": {**config, "emit": emit},
        "inputs": dict(inputs),
    }
    path = ("emitter",)
    composite.merge({}, set_path({}, path, emitter_state))
    _, instance = core.traverse(composite.schema, composite.state, path)
    composite.step_paths[path] = instance
    composite.build_step_network()


def _run_xarray(*, state, run_id, emit_paths, out_dir, core, steps,
                progress_cb, emitter_config) -> "dict | None":
    """Inject XArrayEmitter as a flat Step, run, flush. Returns provenance.

    Returns ``None`` when the auto-derived view is empty (no ``emit_paths`` /
    none present), signalling the caller to fall back to sqlite. Returns a
    sentinel ``{"output_kind": None, "warning": ...}`` when the run DID emit but
    wrote an EMPTY store (the buffer never filled — see the guard below); the
    caller treats that as a fall-back too but propagates the warning.
    """
    if not emit_paths:
        return None  # no selection → empty view → fall back to sqlite

    from process_bigraph import Composite
    from process_bigraph.emitter import collect_input_ports
    from pbg_emitters.xarray_emitter import XArrayEmitter
    from pbg_emitters.xarray_emitter.view import view_from_emit_paths

    core.register_link("XArrayEmitter", XArrayEmitter)
    composite = Composite({"state": state}, core=core)

    # collect_input_ports gives the flat emit-port -> store-path wiring for
    # every store; select the ports the caller asked to emit (a port matches an
    # emit_path when it equals it or is nested under it).
    all_wires = collect_input_ports(composite.state)
    wanted = [_normalize_emit_path(p) for p in emit_paths]
    selected: dict = {}
    for port, wire in all_wires.items():
        if port == "global_time":
            continue
        for ep in wanted:
            if ep and (port == ep or port.startswith(ep + "/")):
                selected[port] = wire
                break
    emit_ports = sorted(selected)
    if not emit_ports:
        return None  # nothing present → empty view → fall back to sqlite

    store = str(Path(out_dir) / f"{run_id}.zarr")
    view = view_from_emit_paths(emit_ports, dtype="<f8")
    config = _xarray_emitter_config(store, view, emitter_config)
    config["metadata"] = {**(config.get("metadata") or {}), "experiment_id": run_id}

    # Wire the selected ports plus global_time (the transducer's time source).
    inputs = dict(selected)
    inputs["global_time"] = all_wires.get("global_time", ["global_time"])

    _inject_xarray_step(composite, core, config, inputs)
    _drive(composite, steps, progress_cb)
    _, close_errors = _flush_step_emitters(composite)

    # GUARD (Finding 1): the flat-Step XArrayEmitter's async writer only persists
    # a COMPLETE buffer; a run shorter than the buffer (size 3) writes the hive
    # partition coordinates but omits the observable leaf data — deterministically
    # for a 1-tick run (its close-time final flush asserts ``not include_static``,
    # an AssertionError that ``_flush_step_emitters`` now SURFACES via
    # ``close_errors`` instead of swallowing) and NON-deterministically for a
    # 2-tick run (an executor race). Either way the ``.zarr`` charts empty. Since
    # xarray is the DEFAULT emitter (Task 6) and ``run_runner.execute`` resolves
    # the Run tab through ``default_emitter``, that empty store would be the
    # DEFAULT outcome for short interactive runs. Detect the empty store by its
    # actual content (``num_writes`` does NOT distinguish the flaky 2-tick case)
    # and fall back to sqlite so the default path NEVER silently yields nothing.
    if close_errors or not _zarr_store_has_observable_data(store):
        warning = (
            f"xarray run {run_id!r}: the zarr store has no observable data after "
            f"{steps} emit-tick(s) (the flat-Step buffer of 3 under-filled) — "
            f"falling back to sqlite so the run yields readable data."
        )
        if close_errors:
            warning += " close error(s): " + "; ".join(
                repr(exc) for _i, exc in close_errors)
        print(warning, flush=True)
        # Best-effort: drop the empty/partial store so a later read-source probe
        # can't mis-resolve the run to it.
        try:
            import shutil
            shutil.rmtree(store, ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass
        return {"output_kind": None, "warning": warning}
    return {"output_kind": "zarr", "store_path": store, "steps": steps,
            "run_id": run_id, "composite": composite}


def run_with_emitter(name, *, state, run_id, emit_paths, out_dir, core, steps,
                     db_file=None, progress_cb=None, spec=None,
                     emitter_config=None) -> dict:
    """Inject the named emitter as a Step, build a Composite, run ``steps`` ticks
    (calling ``progress_cb(step)`` each tick), flush/close, and return provenance.

    Dispatch by ``output_kind`` (every emitter is a Step):

    - ``sqlite``  → ``inject_emitter_for_paths`` + ``inject_sqlite_emitter``;
      ``store_path`` is ``db_file``.
    - ``parquet`` → ``install_default_emitters`` (the composite's declared sink)
      then ``_flush_step_emitters``; ``store_path`` is ``<out_dir>/parquet``.
    - ``ram``     → process-bigraph RAMEmitter convention (in-memory; no store).
    - ``xarray``  → XArrayEmitter as a flat Step (``out_uri`` under ``out_dir``,
      ``emit_root=()``, a view auto-derived from ``emit_paths``). A colony
      ``emitter_config`` (``strategy``/``emit_root``) is passed through. An EMPTY
      view (no ``emit_paths`` / none present) auto-falls-back to sqlite.

    Returns ``{"output_kind", "store_path", "steps", "run_id"}`` plus the live
    ``"composite"`` (so callers can render visualizations off the finished run).
    """
    from process_bigraph import Composite

    kind = output_kind(name)
    fallback_warning = None
    # Did we fall back from the zarr path AFTER it already drove a Composite for
    # `steps` ticks? (MINOR 3) Only the empty-STORE sentinel implies a real drive
    # happened; the empty-VIEW fallback (`prov is None`) returns before any drive.
    zarr_drove_already = False

    if kind == "zarr":
        prov = _run_xarray(
            state=state, run_id=run_id, emit_paths=emit_paths, out_dir=out_dir,
            core=core, steps=steps, progress_cb=progress_cb,
            emitter_config=emitter_config)
        if prov is not None and prov.get("output_kind") == "zarr":
            return prov
        # Empty view (prov is None) OR empty store (sentinel with a warning) →
        # fall back to the default sqlite store. Carry any warning into the
        # fall-back provenance so the empty result is DIAGNOSABLE (not silent).
        if isinstance(prov, dict):
            fallback_warning = prov.get("warning")
            zarr_drove_already = True  # the empty-store path drove `steps` ticks
        kind = "sqlite"

    if kind == "sqlite":
        from vivarium_dashboard.lib import composite_runs as cr
        st = state
        # MINOR 3 guard: when we fall back from the zarr path that ALREADY drove
        # a Composite built from this same `state`, deep-copy so the sqlite re-run
        # starts from a PRISTINE state — independent of whether `Composite(...)`
        # (or the inject_* helpers) ever mutate the input tree in place. (The
        # current process-bigraph builds its own copy, so this is belt-and-braces,
        # but it makes the fall-back robust to that not holding.)
        if zarr_drove_already:
            st = copy.deepcopy(st)
        if emit_paths:
            st = cr.inject_emitter_for_paths(st, list(emit_paths))
        st = cr.inject_sqlite_emitter(st, run_id=run_id, db_file=db_file)
        try:
            from pbg_emitters.sqlite_emitter import SQLiteEmitter
        except ImportError:  # process-bigraph < 1.4.17 (legacy location)
            from process_bigraph.emitter import SQLiteEmitter
        core.register_link("SQLiteEmitter", SQLiteEmitter)
        composite = Composite({"state": st}, core=core)
        # MINOR 3: on a zarr→sqlite fall-back the empty-store path already called
        # `progress_cb(1..steps)` once, and this re-drive calls it again — so the
        # heartbeat re-counts 1..steps. That double-count is HARMLESS: run_runner's
        # `_progress` is a liveness heartbeat (latest step + a wall-clock-based
        # max-runtime guard), NOT a cumulative counter, so the timeout still
        # measures real elapsed time and the run finalises at `steps`. We keep
        # calling progress_cb (rather than suppressing it) precisely so the
        # max-runtime guard stays armed during the sqlite re-run.
        _drive(composite, steps, progress_cb)
        result = {"output_kind": "sqlite",
                  "store_path": str(db_file) if db_file is not None else None,
                  "steps": steps, "run_id": run_id, "composite": composite}
        if fallback_warning:
            result["warning"] = fallback_warning
        return result

    if kind == "parquet":
        from pbg_superpowers.composite_generator import install_default_emitters
        parquet_dir = str(Path(out_dir) / "parquet") if out_dir else None
        st = install_default_emitters(
            state, spec, run_id=run_id, out_dir=parquet_dir, core=core)
        composite = Composite({"state": st}, core=core)
        _drive(composite, steps, progress_cb)
        _flush_step_emitters(composite)  # parquet flush; close errors are non-fatal
        return {"output_kind": "parquet", "store_path": parquet_dir,
                "steps": steps, "run_id": run_id, "composite": composite}

    if kind == "ram":
        from vivarium_dashboard.lib import composite_runs as cr
        from process_bigraph.emitter import RAMEmitter
        st = state
        if emit_paths:
            st = cr.inject_emitter_for_paths(st, list(emit_paths))
        core.register_link("RAMEmitter", RAMEmitter)
        composite = Composite({"state": st}, core=core)
        _drive(composite, steps, progress_cb)
        return {"output_kind": "ram", "store_path": None, "steps": steps,
                "run_id": run_id, "composite": composite}

    raise ValueError(
        f"run_with_emitter: unsupported emitter {name!r} (output_kind={kind!r})")
