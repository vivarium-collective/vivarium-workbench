"""Emitter broker — the single locus for ``output_kind → reader / label / chart``.

Before this module the dashboard chose readers, emitter labels, and chart
sources with inline ``if kind == "xarray"/"parquet"/"sqlite"`` branches scattered
across ``study_charts``, ``simulations_index``, ``explorer_data`` and ``registry``.
This broker centralizes that dispatch: it resolves each emitter's CONTRACT from
``pbg-emitters`` (Task 1) and maps a store's ``output_kind`` to the EXISTING
reader / label / chart-source functions — never reimplementing a reader body or
changing any output.

ZERO behavior change is the contract of Task 4. The default emitter STAYS
``"sqlite"`` here; the flip to ``"xarray"`` (and its runtime deps) is Task 6.
``reader_for`` is the ONLY place a ``kind → reader`` mapping may live.

All cross-``lib`` imports are lazy (inside functions) so this module can be
imported by the very modules it dispatches into without an import cycle.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

# The framework default. Task 6 flips this to "xarray" — keep "sqlite" here so
# the read/label/chart paths produce byte-identical output to pre-broker code.
DEFAULT_EMITTER = "sqlite"

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
    if isinstance(spec_rt, dict) and spec_rt.get("default_emitter") in _ACCEPTED_EMITTERS:
        return spec_rt["default_emitter"]
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
                if isinstance(ws_rt, dict) and ws_rt.get("default_emitter") in _ACCEPTED_EMITTERS:
                    return ws_rt["default_emitter"]
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
