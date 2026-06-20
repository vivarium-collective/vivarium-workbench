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
    """('sqlite', Path) | ('zarr', store.zarr Path) | (None, None)."""
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
        return None, None
    if p.exists():
        return "sqlite", p
    return None, None


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
    remains SQLite-only for v1 (zarr bulk would need its own leaf handling)."""
    kind, resolved = _resolve_run_source(db_path, workspace)
    series, time = {}, []
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
    try:
        import xarray as xr
    except ImportError:
        return [], []
    try:
        dt = xr.open_datatree(str(store), engine="zarr")
    except Exception:
        return [], []
    leaf = "base_reaction_fluxes"
    for node in dt.subtree:
        if node.name != leaf:
            continue
        gen_vars = sorted((v for v in (node.data_vars or {})
                           if str(v).startswith("generation=")),
                          key=lambda s: int(str(s).split("=")[1]))
        if not gen_vars:
            return [], []
        arr = node[gen_vars[0]]  # first generation
        idcoord = "id_" + leaf
        if idcoord not in arr.dims:
            return [], []
        ids = ([str(x) for x in node[idcoord].values] if idcoord in node.coords
               else [str(i) for i in range(arr.sizes[idcoord])])
        emitdim = [d for d in arr.dims if d != idcoord]
        if not emitdim:
            return [], []
        nstep = arr.sizes[emitdim[0]]
        si = min(max(0, step), nstep - 1)
        vals = arr.isel({emitdim[0]: si}).values.tolist()
        return ids, [float(x) for x in vals]
    return [], []


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
    zarr: ids from the id_<leaf> coord. sqlite: positional index ids."""
    kind, resolved = _resolve_run_source(db_path, workspace)
    if kind == "zarr":
        leaf = path.split(".")[-1].split("[")[0]
        ids, vals = _zarr_vector(resolved, leaf, step)
        return {"ids": ids, "values": vals, "step": step, "time": None}
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
    get_flux with asset/run base_ids."""
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

    Only runs backed by a real, non-empty emitter store (SQLite history or a
    zarr datatree) are returned — metadata-only ``study_yaml`` records and empty
    DBs have nothing to explore and would otherwise clutter the picker with
    dead entries.
    """
    ws = Path(workspace)
    out = []
    for r in simulations_index.list_simulations(ws):
        db = r.get("db_path")
        if not db:
            continue
        kind, resolved = _resolve_run_source(db, ws)
        if kind not in ("sqlite", "zarr") or not _run_has_data(kind, resolved):
            continue
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
    return out
