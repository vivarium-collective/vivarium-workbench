"""Server-side data prep for the Analyses Data Explorer card.

Thin, pure-Python functions over a workspace's simulation runs. Reuses the
existing run-discovery and trace-extraction readers; adds no new deps.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from vivarium_dashboard.lib import simulations_index
from vivarium_dashboard.lib import comparative_viz


# Top-level store key -> friendly category. Order defines display order.
_CATEGORY_MAP = [
    ("mass", "Mass"),
    ("bulk", "Bulk molecules"),
    ("fba_results", "Fluxes"),
    ("listeners", "Listeners"),
    ("growth", "Growth & division"),
]

_NUM = (int, float)


def _unwrap_agent(state: dict) -> dict:
    """v2ecoli single-cell composites nest everything under agents/0/."""
    ag = state.get("agents")
    if isinstance(ag, dict) and "0" in ag and isinstance(ag["0"], dict):
        return ag["0"]
    return state


def _category_for(top_key: str) -> str:
    for key, friendly in _CATEGORY_MAP:
        if top_key == key or top_key.startswith(key + "."):
            return friendly
    return "Other"


def _unit_for(path: str) -> str:
    """Physical unit for an observable, inferred from its path (units live in
    listener port schemas, not in the emitted payload)."""
    p = path.lower()
    if "fraction" in p or "ratio" in p or "growth_rate" in p:
        return ""
    if "_mass" in p or p.endswith("mass"):
        return "fg"
    if "fba_results" in p or "flux" in p:
        return "mmol·s⁻¹"
    if "rna_counts" in p or "monomer_counts" in p or p.startswith("bulk["):
        return "counts"
    return ""


def _mol_class(path: str) -> str:
    """Molecule class for an observable, inferred from its path."""
    p = path.lower()
    if "rna_counts" in p:
        return "RNA"
    if "monomer_counts" in p:
        return "Protein"
    if p.startswith("bulk["):
        return "Metabolite"
    if "fba_results" in p or "flux" in p:
        return "Flux"
    if "mass" in p:
        return "Mass"
    return "Other"


def _annotate(leaf: dict) -> dict:
    leaf["unit"] = _unit_for(leaf["path"])
    leaf["mclass"] = _mol_class(leaf["path"])
    return leaf


def _walk(node, prefix, top_key, out):
    """Collect numeric scalar leaves and numeric vectors as observable dicts."""
    if isinstance(node, dict):
        for k, v in node.items():
            _walk(v, f"{prefix}.{k}" if prefix else k, top_key or k, out)
    elif isinstance(node, list) and node:
        # list-of-[name, number] pairs (e.g. bulk molecules: [["GLC", 10], ...])
        if all(isinstance(x, list) and len(x) == 2
               and isinstance(x[0], str) and isinstance(x[1], _NUM)
               for x in node):
            for name, _val in node:
                out.append({"path": f"{prefix}[{name}]", "index": None,
                            "label": name, "kind": "bulk"})
        # pure numeric vector
        elif all(isinstance(x, _NUM) for x in node):
            out.append({"path": prefix, "index": 0, "label": prefix.split(".")[-1],
                        "kind": "vector", "length": len(node)})
    elif isinstance(node, _NUM) and not isinstance(node, bool):
        out.append({"path": prefix, "index": None, "label": prefix.split(".")[-1],
                    "kind": "scalar"})


def _first_state(db_path: str, run_id: str | None) -> dict | None:
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            tbls = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            if "history" not in tbls:
                return None
            if run_id:
                row = conn.execute(
                    "SELECT state FROM history WHERE simulation_id=? ORDER BY step LIMIT 1",
                    (run_id,)).fetchone()
            else:
                row = conn.execute(
                    "SELECT state FROM history ORDER BY step LIMIT 1").fetchone()
            return json.loads(row[0]) if row else None
        except Exception:
            return None
        finally:
            conn.close()
    except Exception:
        return None


def _resolve_run_source(db, workspace=None):
    """('sqlite', Path) | ('zarr', store.zarr Path) | ('parquet', dir Path) | (None, None)."""
    p = Path(db)
    if workspace and not p.is_absolute():
        p = Path(workspace) / p
    if str(p).endswith(".zarr") and p.exists():
        return "zarr", p
    if p.is_dir():
        for cand in [p / "store.zarr", *sorted(p.glob("*/store.zarr")),
                     *sorted(p.glob("*/*/store.zarr"))]:
            if cand.exists():
                return "zarr", cand
        # Parquet: directory with *.pq files (not a zarr store)
        if next(p.rglob("*.pq"), None) is not None:
            return "parquet", p
        return None, None
    if p.exists():
        return "sqlite", p
    return None, None


# ---------------------------------------------------------------------------
# Parquet helpers (lazy-import pyarrow; degrade to empty/None if absent)
# ---------------------------------------------------------------------------

def _parquet_files(part_dir):
    """Sorted list of .pq paths under a partition directory."""
    return sorted(Path(part_dir).rglob("*.pq"))


def _parquet_table(part_dir, columns=None):
    """Read all .pq files in part_dir, concat, sort by global_time.

    Uses ParquetFile (single-file reader) to avoid the hive-partition
    ArrowTypeError that occurs when pyarrow tries to merge schemas across
    a partitioned directory.  Returns None on ImportError or any read error.
    """
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:
        return None
    files = _parquet_files(part_dir)
    if not files:
        return None
    try:
        tables = [pq.ParquetFile(str(f)).read(columns=columns) for f in files]
        tbl = pa.concat_tables(tables)
        return tbl.sort_by("global_time")
    except Exception:
        return None


def _parquet_config_meta(part_dir):
    """Return {observable_col: id_list} from the sibling configuration/config.pq.

    The config file lives at the parallel path with /history/ replaced by
    /configuration/, containing output_metadata__<col> columns.
    """
    try:
        import pyarrow.parquet as pq
    except ImportError:
        return {}
    try:
        # Anchor the swap at the partition boundary so a workspace path that
        # happens to contain "/history/" isn't corrupted.
        config_root = Path(str(part_dir).replace(
            "/history/experiment_id=", "/configuration/experiment_id=", 1))
        config_files = list(config_root.rglob("config.pq"))
        if not config_files:
            return {}
        t = pq.ParquetFile(str(config_files[0])).read()
        result = {}
        for col in t.column_names:
            if col.startswith("output_metadata__"):
                key = col[len("output_metadata__"):]
                val = t.column(col)[0].as_py()
                if val is not None:
                    result[key] = val
        return result
    except Exception:
        return {}


def _parquet_observables(part_dir):
    """Build the {categories: {...}} observable map from a parquet partition."""
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:
        return {"categories": {}}
    files = _parquet_files(part_dir)
    if not files:
        return {"categories": {}}
    try:
        schema = pq.read_schema(str(files[0]))
    except Exception:
        return {"categories": {}}

    # Columns that are hive-partition metadata or otherwise not observables
    SKIP = {"global_time", "bulk__id", "experiment_id", "variant",
            "lineage_seed", "generation", "agent_id"}
    leaves = []
    for field in schema:
        col = field.name
        if col in SKIP:
            continue
        is_large_list = pa.types.is_large_list(field.type)
        is_list = pa.types.is_list(field.type)
        if col == "bulk__count":
            # Special-case: bulk molecule counts as Metabolite vector
            leaves.append({"path": col, "index": 0, "kind": "vector",
                           "label": "bulk", "unit": "counts", "mclass": "Metabolite"})
        elif is_large_list or is_list:
            leaves.append(_annotate({"path": col, "index": 0, "kind": "vector",
                                     "label": col.split("__")[-1]}))
        elif pa.types.is_floating(field.type) or pa.types.is_integer(field.type):
            leaves.append(_annotate({"path": col, "index": None, "kind": "scalar",
                                     "label": col.split("__")[-1]}))
    return {"categories": _categorize_leaves(leaves)}


def _categorize_leaves(leaves):
    """Group zarr leaf names (no nested store key) by a name heuristic."""
    def cat(name):
        n = name.lower()
        if "mass" in n:
            return "Mass"
        if "flux" in n or "fba" in n:
            return "Fluxes"
        if n.startswith("bulk"):
            return "Bulk molecules"
        if "growth" in n or "division" in n:
            return "Growth & division"
        return "Listeners"
    out = {}
    for leaf in leaves:
        out.setdefault(cat(leaf["path"]), []).append(leaf)
    order = ["Mass", "Bulk molecules", "Fluxes", "Listeners", "Growth & division"]
    return {c: sorted(out[c], key=lambda o: o["path"]) for c in order if c in out}


def _zarr_observables(store):
    try:
        import xarray as xr
    except ImportError:
        return {"categories": {}}
    try:
        dt = xr.open_datatree(str(store), engine="zarr")
    except Exception:
        return {"categories": {}}
    leaves = []
    for node in dt.subtree:
        gen_vars = [v for v in (node.data_vars or {}) if str(v).startswith("generation=")]
        if not gen_vars:
            continue
        leaf = node.name
        is_vec = any(("id_" + leaf) in node[v].dims for v in gen_vars)
        leaves.append(_annotate({"path": leaf, "index": 0 if is_vec else None,
                       "label": leaf, "kind": "vector" if is_vec else "scalar"}))
    return {"categories": _categorize_leaves(leaves)}


def list_observables(db_path: str, run_id: str | None = None, workspace=None) -> dict:
    kind, resolved = _resolve_run_source(db_path, workspace)
    if kind == "zarr":
        return _zarr_observables(resolved)
    if kind == "parquet":
        return _parquet_observables(resolved)
    if kind != "sqlite":
        return {"categories": {}}
    state = _first_state(resolved, run_id)
    if not state:
        return {"categories": {}}
    inner = _unwrap_agent(state)
    leaves: list[dict] = []
    for top_key, sub in inner.items():
        _walk(sub, top_key, top_key, leaves)
    for _leaf in leaves:
        _annotate(_leaf)
    categories: dict[str, list] = {}
    for leaf in leaves:
        top = re.split(r'[\[.]', leaf["path"])[0]
        cat = _category_for(top)
        categories.setdefault(cat, []).append(leaf)
    # stable ordering: by _CATEGORY_MAP order, then Other
    order = [f for _, f in _CATEGORY_MAP] + ["Other"]
    return {"categories": {c: sorted(categories[c], key=lambda o: o["path"])
                           for c in order if c in categories}}


def _series_key(path: str, index: int | None) -> str:
    return f"{path}#{index}" if index is not None else path


def _extract_bulk_trace(db_path, mol_id, subsample=400, run_id=None):
    """(times, values) for one bulk molecule id, from the array-of-pairs store."""
    try:
        conn = sqlite3.connect(str(db_path))
    except sqlite3.OperationalError:
        return [], []
    try:
        tbls = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "simulations" not in tbls or "history" not in tbls:
            return [], []
        if run_id:
            row = conn.execute(
                "SELECT simulation_id FROM simulations WHERE simulation_id=? LIMIT 1",
                (run_id,)).fetchone() or (run_id,)
            sim_id = row[0]
        else:
            row = conn.execute(
                "SELECT simulation_id FROM simulations ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            if row is None:
                return [], []
            sim_id = row[0]
        n_rows = conn.execute(
            "SELECT COUNT(*) FROM history WHERE simulation_id=?", (sim_id,)
        ).fetchone()[0] or 0
        if n_rows == 0:
            return [], []
        stride = max(1, n_rows // subsample)
        # One row per step; json_each over the bulk array, match the id pair.
        # Try top-level bulk, then agents/0 bulk; whichever yields a value wins.
        sql = (
            "SELECT h.global_time, "
            "  (SELECT json_extract(j.value,'$[1]') FROM json_each(h.state,'$.bulk') j "
            "     WHERE json_extract(j.value,'$[0]')=?), "
            "  (SELECT json_extract(j.value,'$[1]') FROM json_each(h.state,'$.agents.0.bulk') j "
            "     WHERE json_extract(j.value,'$[0]')=?) "
            "FROM history h WHERE h.simulation_id=? AND (h.step % ?)=0 ORDER BY h.step ASC"
        )
        times, values = [], []
        for tm, v_top, v_ag in conn.execute(sql, (mol_id, mol_id, sim_id, stride)):
            val = v_top if v_top is not None else v_ag
            if val is None:
                continue
            try:
                values.append(float(val)); times.append(float(tm))
            except (TypeError, ValueError):
                continue
        return times, values
    finally:
        conn.close()


def get_series(db_path, paths, subsample=400, run_id=None, workspace=None):
    """Aligned (time, values-per-path). Time comes from the first non-empty path.
    `paths` is a list of (path, index|None). Reuses comparative_viz._extract_trace,
    which already handles the agents/0/ fallback and json_extract subsampling.
    Bulk paths of the form ``bulk[<id>]`` are dispatched to _extract_bulk_trace.
    XArrayEmitter (zarr) runs are dispatched via _extract_trace_from_zarr; bulk[id]
    remains SQLite-only for v1 (zarr bulk would need its own leaf handling).
    ParquetEmitter runs read column-by-column via _parquet_table."""
    kind, resolved = _resolve_run_source(db_path, workspace)
    series, time = {}, []

    if kind == "parquet":
        for path, index in paths:
            try:
                tbl = _parquet_table(resolved, columns=["global_time", path])
                if tbl is None:
                    series[_series_key(path, index)] = []
                    continue
                times_col = tbl.column("global_time").to_pylist()
                vals_col = tbl.column(path).to_pylist()
                if index is None:
                    vals = [float(v) if v is not None else None for v in vals_col]
                else:
                    vals = []
                    for row in vals_col:
                        if row is not None and len(row) > index:
                            vals.append(float(row[index]))
                        else:
                            vals.append(None)
                series[_series_key(path, index)] = vals
                if not time and times_col:
                    time = times_col
            except Exception:
                series[_series_key(path, index)] = []
        return {"time": time, "series": series}

    for path, index in paths:
        if kind == "zarr":
            t, v = comparative_viz._extract_trace_from_zarr(
                resolved, path, subsample, index)
        elif path.startswith("bulk[") and path.endswith("]"):
            mol_id = path[len("bulk["):-1]
            t, v = _extract_bulk_trace(resolved if kind == "sqlite" else db_path,
                                       mol_id, subsample, run_id)
        elif kind == "sqlite":
            t, v = comparative_viz._extract_trace(
                resolved, path, index, subsample, sim_name=None, sim_id=run_id)
        else:
            t, v = [], []
        series[_series_key(path, index)] = v
        if not time and t:
            time = t
    return {"time": time, "series": series}


_FLUX_PATH = "listeners.fba_results.base_reaction_fluxes"

_ASSET_DIR = Path(__file__).resolve().parents[1] / "static" / "explorer"
_flux_assets_cache = None


def load_flux_assets():
    """(base_reaction_ids, reaction_id_map), cached. Returns ([], {}) if absent."""
    global _flux_assets_cache
    if _flux_assets_cache is None:
        try:
            base = json.loads((_ASSET_DIR / "base_reaction_ids.json").read_text())
        except (OSError, ValueError):
            base = []
        try:
            idmap = json.loads((_ASSET_DIR / "reaction_id_map.json").read_text())
        except (OSError, ValueError):
            idmap = {}
        _flux_assets_cache = (base, idmap)
    return _flux_assets_cache


def _state_at_step(db_path: str, step: int, run_id: str | None):
    try:
        conn = sqlite3.connect(str(db_path))
    except sqlite3.OperationalError:
        return None, None
    try:
        if run_id:
            row = conn.execute(
                "SELECT global_time, state FROM history WHERE simulation_id=? AND step=?",
                (run_id, step)).fetchone()
        else:
            row = conn.execute(
                "SELECT global_time, state FROM history WHERE step=? ORDER BY simulation_id LIMIT 1",
                (step,)).fetchone()
        if not row:
            return None, None
        return row[0], json.loads(row[1])
    finally:
        conn.close()


def _dig(node, dotted):
    for k in dotted.split("."):
        if not isinstance(node, dict) or k not in node:
            return None
        node = node[k]
    return node


def get_flux(db_path: str, step: int, base_ids, id_map, run_id: str | None = None) -> dict:
    time, state = _state_at_step(db_path, step, run_id)
    fluxes: dict[str, float] = {}
    total = len(base_ids)
    if state is not None:
        inner = _unwrap_agent(state)
        vec = _dig(inner, _FLUX_PATH)
        if isinstance(vec, list):
            for i, val in enumerate(vec):
                if i >= len(base_ids):
                    break
                bigg = id_map.get(base_ids[i])
                if bigg is not None and isinstance(val, _NUM):
                    fluxes[bigg] = float(val)
    return {"step": step, "time": time, "fluxes": fluxes,
            "coverage": {"mapped": len(fluxes), "total": total}}


def base_ids_from_run(db_path, run_id=None):
    """Ordered base_reaction_ids emitted in the run state, or [] if absent."""
    state = _first_state(db_path, run_id)
    if not state:
        return []
    found = []
    def _search(node):
        if found:
            return
        if isinstance(node, dict):
            v = node.get("base_reaction_ids")
            if isinstance(v, list) and v and all(isinstance(x, str) for x in v):
                found.extend(v); return
            for sub in node.values():
                _search(sub)
        elif isinstance(node, list):
            for sub in node:
                _search(sub)
    _search(_unwrap_agent(state))
    return found


def _zarr_flux(store, step):
    """(base_ids, flux_values) at one emit step from a zarr store, or ([], [])."""
    return _zarr_vector(store, "base_reaction_fluxes", step)


def _zarr_vector(store, leaf, step):
    """(ids, values) for one vector leaf at one emit step, ids from id_<leaf>."""
    try:
        import xarray as xr
    except ImportError:
        return [], []
    try:
        dt = xr.open_datatree(str(store), engine="zarr")
    except Exception:
        return [], []
    for node in dt.subtree:
        if node.name != leaf:
            continue
        gen_vars = sorted((v for v in (node.data_vars or {})
                           if str(v).startswith("generation=")),
                          key=lambda s: int(str(s).split("=")[1]))
        if not gen_vars:
            return [], []
        arr = node[gen_vars[0]]
        idcoord = "id_" + leaf
        if idcoord not in arr.dims:
            return [], []
        ids = ([str(x) for x in node[idcoord].values]
               if idcoord in node.coords
               else [str(i) for i in range(arr.sizes[idcoord])])
        emitdim = [d for d in arr.dims if d != idcoord]
        if not emitdim:
            return [], []
        nstep = arr.sizes[emitdim[0]]
        si = min(max(0, step), nstep - 1)
        vals = arr.isel({emitdim[0]: si}).values.tolist()
        return ids, [float(x) for x in vals]
    return [], []


def get_vector(db_path, path, step, run_id=None, workspace=None):
    """One vector observable's per-entity (ids, values) at a timepoint.
    zarr: ids from the id_<leaf> coord. sqlite: positional index ids.
    parquet: ids from config_meta (output_metadata__<col>), or bulk__id for bulk."""
    kind, resolved = _resolve_run_source(db_path, workspace)
    if kind == "zarr":
        leaf = path.split(".")[-1].split("[")[0]
        ids, vals = _zarr_vector(resolved, leaf, step)
        return {"ids": ids, "values": vals, "step": step, "time": None}
    if kind == "parquet":
        try:
            tbl = _parquet_table(resolved, columns=["global_time", path])
            if tbl is None:
                return {"ids": [], "values": [], "step": step, "time": None}
            nrows = len(tbl)
            si = min(max(0, step), nrows - 1)
            time_val = tbl.column("global_time")[si].as_py()
            row_val = tbl.column(path)[si].as_py()
            if row_val is None:
                return {"ids": [], "values": [], "step": step, "time": time_val}
            values = [float(v) for v in row_val]
            if path == "bulk__count":
                bulk_tbl = _parquet_table(resolved, columns=["global_time", "bulk__id"])
                if bulk_tbl is not None:
                    ids = bulk_tbl.column("bulk__id")[si].as_py() or []
                else:
                    ids = [str(i) for i in range(len(values))]
            else:
                meta = _parquet_config_meta(resolved)
                ids = meta.get(path, [str(i) for i in range(len(values))])
            return {"ids": ids, "values": values, "step": step, "time": time_val}
        except Exception:
            return {"ids": [], "values": [], "step": step, "time": None}
    if kind == "sqlite":
        time, state = _state_at_step(resolved, step, run_id)
        vec = _dig(_unwrap_agent(state), path) if state is not None else None
        if isinstance(vec, list) and all(isinstance(x, _NUM) for x in vec):
            return {"ids": [str(i) for i in range(len(vec))],
                    "values": [float(x) for x in vec], "step": step, "time": time}
        return {"ids": [], "values": [], "step": step, "time": time}
    return {"ids": [], "values": [], "step": step, "time": None}


def get_flux_auto(db_path, step, id_map, run_id=None, workspace=None):
    """Emitter-aware flux: zarr reads ids+vector from the store; sqlite uses
    get_flux with asset/run base_ids; parquet reads the FBA vector + config ids."""
    kind, resolved = _resolve_run_source(db_path, workspace)
    if kind == "zarr":
        ids, vals = _zarr_flux(resolved, step)
        fluxes = {}
        for i, rid in enumerate(ids):
            if i >= len(vals):
                break
            bigg = id_map.get(rid)
            if bigg is not None:
                fluxes[bigg] = float(vals[i])
        return {"step": step, "time": None, "fluxes": fluxes,
                "coverage": {"mapped": len(fluxes), "total": len(ids)}}
    if kind == "parquet":
        flux_col = "listeners__fba_results__base_reaction_fluxes"
        try:
            tbl = _parquet_table(resolved, columns=["global_time", flux_col])
            if tbl is None:
                return {"step": step, "time": None, "fluxes": {},
                        "coverage": {"mapped": 0, "total": 0}}
            nrows = len(tbl)
            si = min(max(0, step), nrows - 1)
            time_val = tbl.column("global_time")[si].as_py()
            vals = tbl.column(flux_col)[si].as_py() or []
            meta = _parquet_config_meta(resolved)
            ids = meta.get(flux_col, [])
            fluxes = {}
            for i, rid in enumerate(ids):
                if i >= len(vals):
                    break
                bigg = id_map.get(rid)
                if bigg is not None:
                    fluxes[bigg] = float(vals[i])
            return {"step": step, "time": time_val, "fluxes": fluxes,
                    "coverage": {"mapped": len(fluxes), "total": len(ids)}}
        except Exception:
            return {"step": step, "time": None, "fluxes": {},
                    "coverage": {"mapped": 0, "total": 0}}
    # sqlite path
    base_ids, _ = load_flux_assets()
    if not base_ids:
        base_ids = base_ids_from_run(resolved if kind == "sqlite" else db_path, run_id)
    return get_flux(resolved if kind == "sqlite" else db_path, step, base_ids, id_map, run_id)


def _run_has_data(kind, resolved) -> bool:
    """True iff the resolved store actually holds emitted history to explore.
    Drops metadata-only records and empty/headerless DBs from the picker."""
    try:
        if kind == "sqlite":
            conn = sqlite3.connect(str(resolved))
            try:
                tbls = {x[0] for x in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                if "history" not in tbls:
                    return False
                return conn.execute(
                    "SELECT 1 FROM history LIMIT 1").fetchone() is not None
            finally:
                conn.close()
        if kind == "zarr":
            import xarray as xr
            dt = xr.open_datatree(str(resolved), engine="zarr")
            for node in dt.subtree:
                if any(str(v).startswith("generation=")
                       for v in (node.data_vars or {})):
                    return True
            return False
    except Exception:
        return False
    return False


def list_runs(workspace: Path) -> list[dict]:
    """Runs for the explorer's run-picker, projected to a small public shape.

    Returns SQLite/zarr runs from list_simulations (non-empty stores only), plus
    parquet runs discovered by scanning .pbg/runs/ for hive partition directories.
    Parquet runs are de-duplicated against any that list_simulations already surfaced.
    """
    ws = Path(workspace)
    out = []
    existing_resolved: set[str] = set()

    for r in simulations_index.list_simulations(ws):
        db = r.get("db_path")
        if not db:
            continue
        kind, resolved = _resolve_run_source(db, ws)
        if kind not in ("sqlite", "zarr") or not _run_has_data(kind, resolved):
            continue
        existing_resolved.add(str(resolved))
        studies = r.get("studies") or []
        out.append({
            "run_id": r.get("run_id"),
            "label": r.get("label") or r.get("sim_name") or r.get("run_id"),
            "study": r.get("study_slug") or (studies[0] if studies else None),
            "investigation": r.get("investigation_slug"),
            "n_steps": r.get("n_steps"),
            "status": r.get("status"),
            "db_path": r.get("db_path"),
            "source": r.get("source"),
        })

    # Discover parquet runs from .pbg/runs/
    try:
        import pyarrow.parquet as pq
        _pq_available = True
    except ImportError:
        _pq_available = False

    if _pq_available:
        pbg_runs = ws / ".pbg" / "runs"
        if pbg_runs.is_dir():
            pattern = "*/*/history/experiment_id=*/variant=*/lineage_seed=*"
            for lineage_dir in sorted(pbg_runs.glob(pattern)):
                if not lineage_dir.is_dir():
                    continue
                if next(lineage_dir.rglob("*.pq"), None) is None:
                    continue
                if str(lineage_dir) in existing_resolved:
                    continue
                variant = lineage_dir.parent.name.split("=", 1)[1]
                seed = lineage_dir.name.split("=", 1)[1]
                # runfolder is the first segment under .pbg/runs
                runfolder = lineage_dir.parts[len(pbg_runs.parts)]
                run_id = f"{runfolder}:v{variant}:s{seed}"
                label = f"{runfolder} · variant {variant} · seed {seed}"
                n_steps = 0
                for f in sorted(lineage_dir.rglob("*.pq")):
                    try:
                        n_steps += pq.read_metadata(str(f)).num_rows
                    except Exception:
                        pass
                db_path_rel = str(lineage_dir.relative_to(ws))
                out.append({
                    "run_id": run_id,
                    "label": label,
                    "study": None,
                    "investigation": None,
                    "n_steps": n_steps,
                    "status": None,
                    "db_path": db_path_rel,
                    "source": "parquet",
                })

    # Parquet runs carry the richest observable set (molecules + fluxes + mass),
    # so surface them first — the picker defaults to one and scatter defaults to
    # the first two (e.g. baseline vs a variant of the same run).
    return ([r for r in out if r["source"] == "parquet"] +
            [r for r in out if r["source"] != "parquet"])
