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


def list_observables(db_path: str, run_id: str | None = None) -> dict:
    state = _first_state(db_path, run_id)
    if not state:
        return {"categories": {}}
    inner = _unwrap_agent(state)
    leaves: list[dict] = []
    for top_key, sub in inner.items():
        _walk(sub, top_key, top_key, leaves)
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


def get_series(db_path, paths, subsample=400, run_id=None):
    """Aligned (time, values-per-path). Time comes from the first non-empty path.
    `paths` is a list of (path, index|None). Reuses comparative_viz._extract_trace,
    which already handles the agents/0/ fallback and json_extract subsampling.
    Bulk paths of the form ``bulk[<id>]`` are dispatched to _extract_bulk_trace."""
    series, time = {}, []
    for path, index in paths:
        if path.startswith("bulk[") and path.endswith("]"):
            mol_id = path[len("bulk["):-1]
            t, v = _extract_bulk_trace(db_path, mol_id, subsample, run_id)
        else:
            t, v = comparative_viz._extract_trace(
                Path(db_path), path, index, subsample, sim_name=None)
        series[_series_key(path, index)] = v
        if not time and t:
            time = t
    return {"time": time, "series": series}


def list_runs(workspace: Path) -> list[dict]:
    """Runs for the explorer's run-picker, projected to a small public shape."""
    out = []
    for r in simulations_index.list_simulations(Path(workspace)):
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
