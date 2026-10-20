"""``GET /api/study-results`` worker (spec §3.3, plan Task 4) — a per-store
PREVIEW of the study's LATEST run: sparkline + first/last/min/max for every
scalar leaf the run emitted. Full arrays stay in the download
(``/api/simulation-run-download``) — this worker only ever returns a bounded,
downsampled slice, never a raw un-downsampled series.

Reuses the SAME store readers the Composite Explorer / Readouts panels use:
``lib.simulations_index.list_simulations`` to resolve the study's latest run
(the same rows the Simulations panel's ``/api/simulations?study=`` renders),
then ``lib.explorer_data.list_observables``/``get_series`` (kind-dispatched via
the emitter broker, ``lib.emitters``) to read it — no new store-reading code.

Graceful-empty contract (never a 500, always HTTP 200): no runs yet, the
latest run has no on-disk store, or the store can't be read all degrade to
``{"present": False, "reason": "..."}``.
"""
from __future__ import annotations

from pathlib import Path

# Rendered sparkline length. ``explorer_data.get_series``'s own ``subsample``
# already bounds the READ (each reader strides down large histories before
# returning), so this is a defensive second downsample of whatever comes back
# — never blows up the payload even if a reader ever returned more.
_SPARKLINE_POINTS = 30

# Declared numeric dtype for an emitted leaf's preview — the same CONTRACT
# dtype ``readouts_views._LEAF_DTYPE`` declares for the write path
# (``view_from_emit_paths(..., dtype="<f8")``). The dashboard doesn't
# introspect per-column runtime types here either; this is a preview, not a
# type-checked read.
_LEAF_DTYPE = "float64"


def _pick_latest_run(ws_root: Path, slug: str) -> "dict | None":
    """The study's most recent run row, or ``None``.

    ``list_simulations`` already returns every run newest-first (preferring
    ``completed_at`` over ``started_at``); filtering preserves that order, so
    the first match is the latest.
    """
    from .simulations_index import list_simulations

    rows = list_simulations(ws_root)
    for row in rows:
        if row.get("study_slug") == slug or slug in (row.get("studies") or []):
            return row
    return None


def _scalar_leaf_paths(categories: dict) -> list[str]:
    """Flatten ``list_observables``'s ``{category: [{path, kind, ...}]}`` to
    the sorted, deduped set of SCALAR leaf paths — a preview needs one number
    per step per store, so vector/bulk leaves (which need an explicit index)
    are left out; they're still fully covered by the run's raw-data download.
    """
    paths: set[str] = set()
    for leaves in (categories or {}).values():
        for leaf in leaves or []:
            if isinstance(leaf, dict) and leaf.get("kind") == "scalar" and leaf.get("path"):
                paths.add(leaf["path"])
    return sorted(paths)


def _downsample(values: list, n: int) -> list:
    """Stride ``values`` down to at most ``n`` points, preserving order and
    (when non-empty) the first/last sample."""
    if len(values) <= n:
        return list(values)
    stride = len(values) / n
    return [values[int(i * stride)] for i in range(n)]


def _store_stats(values: list) -> "dict | None":
    """``{first, last, min, max, sparkline}`` for one store's series, or
    ``None`` when the series has no usable numeric samples (excluded from the
    payload entirely rather than fabricated)."""
    nums = [v for v in (values or []) if isinstance(v, (int, float)) and v == v]  # v==v: drop NaN
    if not nums:
        return None
    return {
        "first": nums[0],
        "last": nums[-1],
        "min": min(nums),
        "max": max(nums),
        "sparkline": _downsample(nums, _SPARKLINE_POINTS),
    }


def _global_run_id(row: dict) -> str:
    """The run's workspace-GLOBAL id — the one the on-disk store is keyed by,
    and the one the SQLite reader matches against ``simulation_id``.

    ``simulations_index._study_run_uid`` prefixes a store-less ``study.yaml``
    run's id with ``<slug>:`` (to keep name-runs unique across the workspace),
    but the run's store on disk — e.g. the XArray ``<study>/runs.<run_id>.zarr``
    — is named with the UN-prefixed global id. Strip only that exact ``<slug>:``
    prefix; every other row's ``run_id`` is already the global id (and must be
    left verbatim — NOT replaced by the display ``label``, which is the sim
    name, not the store key).
    """
    rid = str(row.get("run_id") or "")
    slug = row.get("study_slug")
    if slug and rid.startswith(f"{slug}:"):
        return rid[len(str(slug)) + 1:]
    return rid


def _study_dir_for_run(ws_root: Path, row: dict) -> "Path | None":
    """The study directory (root OR nested-investigation layout) for a run row,
    or ``None`` when the run isn't study-associated / the dir can't be found.

    Reuses ``simulations_index._iter_all_study_dirs`` so a ``layout:`` remap and
    the nested ``investigations/<inv>/studies/<slug>`` layout both resolve.
    """
    slug = row.get("study_slug") or next(iter(row.get("studies") or []), None)
    if not slug:
        return None
    try:
        from .simulations_index import _iter_all_study_dirs

        for sdir, _inv, _rel in _iter_all_study_dirs(ws_root):
            if sdir.name == slug:
                return sdir
    except Exception:  # noqa: BLE001 — best-effort; absence just means no zarr fallback
        return None
    return None


def _candidate_stores(ws_root: Path, row: dict) -> "list[Path]":
    """Ordered, on-disk stores that MAY hold this run's emitted observables.

    The preview reads whichever one FIRST yields scalar leaves, so a run that
    emitted to an XArray/zarr store is previewed from that zarr even when the
    simulations-index row's ``store_path`` points at an empty — or entirely
    absent — SQLite ``runs.db`` (the bug this resolves). Emitter-agnostic and
    per-study-config-free; it only ever returns stores that actually exist on
    disk, so a genuinely scalar-less run still previews empty (never fabricated).

    Priority:
      1. the row's recorded ``store_path`` / ``db_path``, resolved against BOTH
         the workspace root and the study dir (a ``study.yaml`` run records a
         study-relative path like ``canonical-runs/<id>/run.db`` that does NOT
         resolve against the workspace root);
      2. the canonical per-run XArray store ``<study>/runs.<run_id>.zarr``;
      3. any other ``<study>/runs.*.zarr`` (newest first);
      4. the study's ``runs.db``.
    """
    from . import run_store

    ws_root = Path(ws_root)
    study_dir = _study_dir_for_run(ws_root, row)
    run_id = _global_run_id(row)
    seen: set[str] = set()
    out: list[Path] = []

    def _add(p) -> None:
        if not p:
            return
        p = Path(p)
        key = str(p)
        if key in seen:
            return
        seen.add(key)
        if p.exists():
            out.append(p)

    for recorded in (row.get("store_path"), row.get("db_path")):
        if not recorded:
            continue
        p = Path(recorded)
        if p.is_absolute():
            _add(p)
        else:
            _add(ws_root / p)
            if study_dir is not None:
                _add(study_dir / p)

    if study_dir is not None:
        if run_id:
            _add(run_store.zarr_store_path(study_dir, run_id))
        try:
            for z in sorted(run_store.iter_zarr_stores(study_dir),
                            key=lambda d: d.stat().st_mtime, reverse=True):
                _add(z)
        except OSError:
            pass
        _add(study_dir / "runs.db")

    return out


def build_study_results(ws_root: Path, slug: "str | None") -> tuple[dict, int]:
    """Worker for ``GET /api/study-results?study=<slug>`` -> ``(payload, 200)``.

    Always HTTP 200 (graceful-empty on every failure path — never a 500):

    * ``{"present": False, "reason": "..."}`` [+ ``"run_id"`` when a run was
      found but its store couldn't be resolved/read] — no runs yet, invalid
      slug, the latest run has no on-disk store, or the store read failed.
    * ``{"present": True, "run_id", "run_label", "started_at", "completed_at",
      "status", "stores": [{"path", "dtype", "first", "last", "min", "max",
      "sparkline": [...]}]}`` — per-store preview of the latest run. ``stores``
      is ``[]`` when the run emitted no scalar leaves (still ``present: True``
      — the run exists, there's just nothing scalar to preview). When the
      run's state carries cross-engine ``comparisons`` the payload instead has
      ``"comparison"`` (a ``models.StudyComparison``) and ``stores: []`` — see
      ``lib/study_comparison.py``.
    """
    from .study_spec import SLUG_RE

    ws_root = Path(ws_root)
    slug = (slug or "").strip()
    if not SLUG_RE.match(slug):
        return {"present": False, "reason": "invalid slug"}, 200

    try:
        row = _pick_latest_run(ws_root, slug)
    except Exception as e:  # noqa: BLE001 — the index must never break the panel
        return {"present": False, "reason": f"could not read the simulations index: {e}"}, 200
    if row is None:
        return {"present": False, "reason": "no runs yet"}, 200

    run_id = row.get("run_id")
    db_path = row.get("store_path") or row.get("db_path")
    # The GLOBAL run id (un-prefixed) is what the on-disk store is keyed by, so
    # the store readers are addressed with it, not the possibly ``<slug>:``-
    # prefixed row id.
    store_run_id = _global_run_id(row)
    # Every store this run might have emitted to, resolved from what's actually
    # on disk — NOT just the one path the index row recorded. The scalar preview
    # reads whichever first yields scalar leaves (see ``_candidate_stores``).
    candidates = _candidate_stores(ws_root, row)
    if not db_path and not candidates:
        return {"present": False, "run_id": run_id,
                "reason": "this run has no on-disk store"}, 200

    common = {
        "run_id": run_id,
        "run_label": row.get("sim_name") or row.get("label") or run_id,
        "started_at": row.get("started_at"),
        "completed_at": row.get("completed_at"),
        "status": row.get("status"),
    }

    # A remote batch run's snapshot carries cross-engine comparisons: show
    # those (a heatmap per model/job) instead of hundreds of scalar rows.
    # ``None`` for every other run -> the scalar preview below, unchanged.
    try:
        from .study_comparison import build_comparison

        # Index rows carry workspace-relative paths (the explorer readers
        # below take ``workspace=`` for the same reason).
        comparison = build_comparison(str(ws_root / db_path), run_id)
    except Exception:  # noqa: BLE001 — a malformed state must degrade to the scalar preview
        comparison = None
    if comparison is not None:
        return {"present": True, "stores": [], "comparison": comparison, **common}, 200

    try:
        from . import explorer_data

        # Read whichever store ACTUALLY holds this run's scalar observables.
        # A study.yaml run may record a SQLite ``store_path`` while its scalars
        # were emitted to the sibling ``<study>/runs.<run_id>.zarr`` — so we try
        # each candidate and use the first that yields scalar leaves rather than
        # trusting the one recorded path (which may be empty or missing).
        chosen = None
        leaf_paths: list[str] = []
        for store in candidates:
            obs = explorer_data.list_observables(
                str(store), run_id=store_run_id, workspace=ws_root)
            found = _scalar_leaf_paths(obs.get("categories") or {})
            if found:
                chosen, leaf_paths = store, found
                break
        if chosen is None:
            # Every resolvable store read clean but none carried a scalar leaf —
            # a genuinely scalar-less run. Present, with nothing to preview
            # (never fabricated).
            return {"present": True, "stores": [], **common}, 200
        series = explorer_data.get_series(
            str(chosen), [(p, None) for p in leaf_paths],
            subsample=_SPARKLINE_POINTS, run_id=store_run_id, workspace=ws_root,
        )
    except Exception as e:  # noqa: BLE001 — an unreadable store must never 500 the panel
        return {"present": False, "run_id": run_id,
                "reason": f"could not read this run's store: {e}"}, 200

    by_path = series.get("series") or {}
    stores = []
    for path in leaf_paths:
        stats = _store_stats(by_path.get(path))
        if stats is None:
            continue
        stores.append({"path": path, "dtype": _LEAF_DTYPE, **stats})

    return {"present": True, "stores": stores, **common}, 200
