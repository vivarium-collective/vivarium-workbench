"""SQLite-backed persistence for Composite Explorer runs.

Owns `.pbg/composite-runs.db`. Bootstraps the `runs_meta` table (run-level
metadata) alongside the `history` table that `process_bigraph.emitter.SQLiteEmitter`
owns (per-step state rows, partitioned by `simulation_id`).

A run's `simulation_id` and our `run_id` are the same string by convention:
    `<spec_id>__<unix-epoch-int>__<6-hex-chars>`
"""
from __future__ import annotations
import hashlib
import json
import sqlite3
import time
from pathlib import Path


_SCHEMA_RUNS_META = """
CREATE TABLE IF NOT EXISTS runs_meta (
    run_id        TEXT PRIMARY KEY,
    spec_id       TEXT NOT NULL,
    label         TEXT,
    params_json   TEXT,
    started_at    REAL NOT NULL,
    completed_at  REAL,
    n_steps       INTEGER,
    status        TEXT NOT NULL,
    sim_name      TEXT
);
"""

_INDEX_RUNS_META = """
CREATE INDEX IF NOT EXISTS idx_runs_meta_spec ON runs_meta(spec_id);
"""


def connect(db_file: str | Path) -> sqlite3.Connection:
    """Open the runs DB and ensure the metadata schema exists."""
    db_file = Path(db_file)
    db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    conn.execute(_SCHEMA_RUNS_META)
    conn.execute(_INDEX_RUNS_META)
    # Legacy DBs created before sim_name was in the base schema: ALTER it in.
    cols = {row[1] for row in conn.execute("PRAGMA table_info(runs_meta)")}
    if "sim_name" not in cols:
        conn.execute("ALTER TABLE runs_meta ADD COLUMN sim_name TEXT")
    conn.commit()
    return conn


def generate_run_id(spec_id: str, params: dict | None = None,
                    now: float | None = None) -> str:
    """Build a deterministic-shape run id: `<spec_id>__<ts>__<hash6>`."""
    ts = int(now if now is not None else time.time())
    payload = json.dumps({"spec_id": spec_id, "params": params or {},
                          "ts": ts}, sort_keys=True)
    short = hashlib.sha1(payload.encode()).hexdigest()[:6]
    return f"{spec_id}__{ts}__{short}"


def save_metadata(conn: sqlite3.Connection, *, spec_id: str, run_id: str,
                  params: dict | None, label: str, started_at: float) -> None:
    """Insert a new run row with status='running'."""
    conn.execute(
        "INSERT INTO runs_meta "
        "(run_id, spec_id, label, params_json, started_at, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (run_id, spec_id, label, json.dumps(params or {}),
         started_at, "running"),
    )
    conn.commit()


def complete_metadata(conn: sqlite3.Connection, *, run_id: str,
                      n_steps: int, status: str) -> None:
    """Mark an existing run as completed (or failed)."""
    conn.execute(
        "UPDATE runs_meta "
        "SET completed_at=?, n_steps=?, status=? WHERE run_id=?",
        (time.time(), n_steps, status, run_id),
    )
    conn.commit()


def query_runs(conn: sqlite3.Connection, *, spec_id: str) -> list[dict]:
    """List runs for one spec_id, newest first."""
    rows = conn.execute(
        "SELECT run_id, spec_id, label, params_json, started_at, "
        "completed_at, n_steps, status FROM runs_meta "
        "WHERE spec_id=? ORDER BY started_at DESC",
        (spec_id,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["params"] = json.loads(d.pop("params_json") or "{}")
        except json.JSONDecodeError:
            d["params"] = {}
        out.append(d)
    return out


def query_run(conn: sqlite3.Connection, *, run_id: str) -> list[dict]:
    """Return the trajectory `[{step, time, state}, ...]` for one run.

    Reads from the `history` table owned by process_bigraph.emitter.SQLiteEmitter.
    If that table doesn't exist yet (no SQLiteEmitter has ever written to this
    DB), returns an empty list.
    """
    has_history = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='history'"
    ).fetchone()
    if not has_history:
        return []
    rows = conn.execute(
        "SELECT step, global_time AS time, state FROM history WHERE simulation_id=? "
        "ORDER BY step ASC",
        (run_id,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["state"] = json.loads(d["state"]) if d["state"] else {}
        except json.JSONDecodeError:
            d["state"] = {}
        out.append(d)
    return out


def query_run_state(conn: sqlite3.Connection, *, run_id: str,
                    step: int) -> dict | None:
    """Return the single state dict at one step, or None if missing."""
    has_history = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='history'"
    ).fetchone()
    if not has_history:
        return None
    row = conn.execute(
        "SELECT state FROM history WHERE simulation_id=? AND step=?",
        (run_id, step),
    ).fetchone()
    if not row or not row["state"]:
        return None
    try:
        return json.loads(row["state"])
    except json.JSONDecodeError:
        return None


def auto_label(overrides: dict) -> str:
    """Build a short human-readable label from non-default override values.

    Returns ``'defaults'`` when *overrides* is empty, otherwise a
    comma-separated ``key=value`` string of the sorted items, truncated to 80
    characters so it fits neatly in the dashboard.
    """
    if not overrides:
        return "defaults"
    parts = [f"{k}={v}" for k, v in sorted(overrides.items())]
    return ", ".join(parts)[:80]


def inject_sqlite_emitter(state: dict, *, run_id: str,
                          db_file: str | Path) -> dict:
    """Return a copy of `state` with a SQLiteEmitter step appended.

    The injected step consumes the same input ports declared by the first
    `_type='step'` entry whose `address` ends with `Emitter` — so the
    SQLiteEmitter captures the same observables the spec's primary emitter
    already declared. When no such step exists, the SQLiteEmitter is added
    with an empty `emit` schema and no inputs (step counts persist anyway).

    Idempotent: a second call with the same run_id is a no-op.
    """
    db_file = Path(db_file)
    if "sqlite_emitter" in state:
        existing = state["sqlite_emitter"]
        cfg = existing.get("config", {})
        if (cfg.get("simulation_id") == run_id
                and cfg.get("file_path") == str(db_file.parent)
                and cfg.get("db_file") == db_file.name):
            return dict(state)

    emit_schema: dict = {}
    inputs: dict = {}
    for key, node in state.items():
        if not isinstance(node, dict):
            continue
        if node.get("_type") != "step":
            continue
        addr = node.get("address", "")
        if not addr.endswith("Emitter"):
            continue
        emit_schema = dict((node.get("config") or {}).get("emit") or {})
        inputs = dict(node.get("inputs") or {})
        break

    # SQLiteEmitter joins file_path (directory) + db_file (filename) via
    # os.path.join, so we must split the absolute path accordingly.
    # (db_file is already a Path from the top of this function.)
    new_state = dict(state)
    new_state["sqlite_emitter"] = {
        "_type": "step",
        "address": "local:SQLiteEmitter",
        "config": {
            "emit": emit_schema,
            "file_path": str(db_file.parent),
            "db_file": db_file.name,
            "simulation_id": run_id,
        },
        "inputs": inputs,
    }
    return new_state


def inject_emitter_for_paths(state: dict, explicit_paths: list[str]) -> dict:
    """Inject a RAMEmitter step that captures the user-selected store paths.

    ``explicit_paths`` is a list of '/'-joined path strings (e.g.
    ``['stores/level', 'stores/fields']``). For each explicit path, this
    function walks the state tree under that path and collects every
    leaf-ish store node (anything that isn't a dict with
    ``_type='process'`` or ``_type='step'``). The resulting set is used to
    build the emitter's ``config.emit`` schema and ``inputs`` wiring.

    The injected emitter is named ``user_emitter``; idempotent on re-call —
    a second call with the same path set is a no-op.

    Subsequent ``inject_sqlite_emitter()`` will then copy this emitter's
    schema + inputs onto the SQLiteEmitter for persistence.
    """
    if not explicit_paths:
        return state

    leaves = _collect_emit_leaves(state, explicit_paths)
    if not leaves:
        return state

    emit_schema: dict = {}
    inputs: dict = {}
    for path_parts in sorted(leaves, key=lambda p: tuple(p)):
        # Slug-safe port name from the path
        key = "_".join(path_parts) if path_parts else "root"
        # process-bigraph's emitter convention uses "node" as the permissive
        # leaf type (see emitter.anyize_paths). "any" trips a bigraph-schema
        # bug in append_link_path that assumes the schema is a dict.
        emit_schema[key] = "node"
        inputs[key] = list(path_parts)

    new_state = dict(state)
    new_state["user_emitter"] = {
        "_type": "step",
        "address": "local:RAMEmitter",
        "config": {"emit": emit_schema},
        "inputs": inputs,
    }
    return new_state


def _collect_emit_leaves(state: dict,
                          explicit_paths: list[str]) -> list[list[str]]:
    """For each explicit_path (slash-joined), walk the state tree and return
    every leaf store path (path that doesn't lead to a dict with
    ``_type`` of ``process``/``step``).

    A path resolves into the state tree by indexing top-level keys
    recursively. If the path points to a leaf (non-dict or dict without
    ``_type``), the path itself is a leaf. If the path points to a
    subtree, walk it.
    """
    leaves: list[list[str]] = []
    for raw in explicit_paths:
        parts = [p for p in raw.split("/") if p]
        node = _resolve_path(state, parts)
        if node is None:
            continue
        _walk_collect(node, parts, leaves)
    # Dedup while preserving order
    seen: set[tuple[str, ...]] = set()
    out: list[list[str]] = []
    for p in leaves:
        t = tuple(p)
        if t in seen:
            continue
        seen.add(t)
        out.append(p)
    return out


def _resolve_path(state: dict, parts: list[str]):
    node = state
    for p in parts:
        if not isinstance(node, dict) or p not in node:
            return None
        node = node[p]
    return node


def _walk_collect(node, path: list[str], out: list[list[str]]) -> None:
    # If node is a process or step, skip — we only emit store values.
    if isinstance(node, dict) and node.get("_type") in ("process", "step"):
        return
    # If node is a dict, treat its non-meta keys as child store paths and
    # recurse into each. Each key becomes its own leaf or sub-walk.
    if isinstance(node, dict):
        children = {k: v for k, v in node.items() if not k.startswith("_")}
        if children:
            for k, v in children.items():
                _walk_collect(v, path + [k], out)
            return
    # Otherwise it's a leaf store
    out.append(path)


def copy_run_to_new_db(src_db: Path, dst_db: Path, run_id: str) -> int:
    """Copy one run's metadata + history rows from src_db to dst_db.

    Both DBs use the same schema (runs_meta + history). Bootstraps dst_db's
    schema if missing. Returns the count of history rows copied.

    Raises KeyError if run_id is not found in src_db.
    """
    src = sqlite3.connect(str(src_db))
    src.row_factory = sqlite3.Row
    dst = connect(dst_db)  # bootstraps runs_meta + index
    try:
        # SQLiteEmitter creates the history table lazily on first write; do it eagerly here.
        dst.executescript("""
            CREATE TABLE IF NOT EXISTS history (
                simulation_id TEXT NOT NULL,
                step INTEGER NOT NULL,
                global_time REAL,
                state TEXT NOT NULL,
                PRIMARY KEY (simulation_id, step)
            );
        """)

        meta = src.execute(
            "SELECT * FROM runs_meta WHERE run_id = ?", (run_id,)
        ).fetchone()
        if meta is None:
            raise KeyError(run_id)

        dst.execute(
            "INSERT INTO runs_meta (run_id, spec_id, label, params_json, "
            "started_at, completed_at, n_steps, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (meta["run_id"], meta["spec_id"], meta["label"], meta["params_json"],
             meta["started_at"], meta["completed_at"], meta["n_steps"], meta["status"]),
        )

        rows = src.execute(
            "SELECT step, global_time, state FROM history WHERE simulation_id = ?",
            (run_id,),
        ).fetchall()
        dst.executemany(
            "INSERT INTO history (simulation_id, step, global_time, state) "
            "VALUES (?, ?, ?, ?)",
            [(run_id, r["step"], r["global_time"], r["state"]) for r in rows],
        )
        dst.commit()
        return len(rows)
    finally:
        src.close()
        dst.close()
