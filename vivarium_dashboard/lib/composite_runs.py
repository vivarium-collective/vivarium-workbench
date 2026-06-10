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


# Nullable columns added to runs_meta after the original 8-column schema.
# `connect()` ALTERs in any that a pre-existing DB is missing. `sim_name`
# predates the detached-runs rework but is migrated through the same path.
_NEW_COLUMNS = {
    "sim_name": "TEXT",
    "pid": "INTEGER",
    "progress_step": "INTEGER",
    "log_path": "TEXT",
    "heartbeat_at": "REAL",
    # Coordinated-generation provenance (expert-feedback A.2). Links this run
    # to one (git_sha, param_set, composite_versions) snapshot so the report
    # can flag panels from an older generation as stale. See
    # pbg_superpowers.generation. Nullable: runs predating the model have NULL
    # and are treated as stale once any generation exists.
    "generation_id": "TEXT",
}


def _migrate_runs_meta(conn: sqlite3.Connection) -> None:
    """Add any missing nullable columns to an existing runs_meta table."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(runs_meta)")}
    for name, sqltype in _NEW_COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE runs_meta ADD COLUMN {name} {sqltype}")
    conn.commit()


def connect(db_file: str | Path) -> sqlite3.Connection:
    """Open the runs DB, ensure schema + migrations, enable WAL."""
    db_file = Path(db_file)
    db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(_SCHEMA_RUNS_META)
    conn.execute(_INDEX_RUNS_META)
    _migrate_runs_meta(conn)
    conn.commit()
    return conn


def run_with_division(composite, steps: int, chunk: int = 100) -> int:
    """Run ``composite`` up to ``steps`` ticks, stopping cleanly at division.

    v2ecoli single-cell composites signal cell division in one of two ways:
    ``composite.run()`` raises, or ``agents['0']`` is removed from the state.
    The dashboard runs each study as a single generation, so either signal
    means the cell cycle finished — we stop and let the caller gather whatever
    the emitter captured up to that point.

    Running ``steps`` in one ``composite.run(steps)`` call (the old behaviour)
    instead crashed the whole run at division, so any run length that crossed
    the division point failed with a 502. Mirrors the chunked, division-aware
    loop in ``scripts/run_default_baseline.py``. Returns ticks actually run.
    """
    steps = int(steps)
    done = 0
    while done < steps:
        n = min(chunk, steps - done)
        try:
            composite.run(n)
        except Exception:
            break  # division — composite raised
        done += n
        agents = (getattr(composite, "state", None) or {}).get("agents") or {}
        if agents.get("0") is None:
            break  # division — parent agent removed
    return done


def generate_run_id(spec_id: str, params: dict | None = None,
                    now: float | None = None) -> str:
    """Build a deterministic-shape run id: `<spec_id>__<ts>__<hash6>`."""
    ts = int(now if now is not None else time.time())
    payload = json.dumps({"spec_id": spec_id, "params": params or {},
                          "ts": ts}, sort_keys=True)
    short = hashlib.sha1(payload.encode()).hexdigest()[:6]
    return f"{spec_id}__{ts}__{short}"


def save_metadata(conn: sqlite3.Connection, *, spec_id: str, run_id: str,
                  params: dict | None, label: str, started_at: float,
                  n_steps: int, log_path: str | None = None,
                  generation_id: str | None = None) -> None:
    """Insert a new run row with status='running'.

    ``n_steps`` is the *requested* step total — stored up front so the UI
    progress bar always has a denominator. ``complete_metadata`` may later
    overwrite it with the actual count.

    ``generation_id`` stamps the run with the workspace's current coordinated
    generation (expert-feedback A.2) so stale panels can be flagged.
    """
    conn.execute(
        "INSERT INTO runs_meta "
        "(run_id, spec_id, label, params_json, started_at, status, "
        " n_steps, log_path, progress_step, generation_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
        (run_id, spec_id, label, json.dumps(params or {}),
         started_at, "running", n_steps, log_path, generation_id),
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


def query_run_meta(conn: sqlite3.Connection, *, run_id: str) -> dict | None:
    """Return the runs_meta row for one run as a dict, or None if absent."""
    row = conn.execute(
        "SELECT run_id, spec_id, label, params_json, started_at, completed_at, "
        "n_steps, status, pid, progress_step, log_path, heartbeat_at, "
        "generation_id "
        "FROM runs_meta WHERE run_id=?",
        (run_id,),
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    try:
        d["params"] = json.loads(d.pop("params_json") or "{}")
    except json.JSONDecodeError:
        d["params"] = {}
    return d


def update_progress(conn: sqlite3.Connection, *, run_id: str,
                    progress_step: int, heartbeat_at: float) -> None:
    """Advance the live progress counter + heartbeat for a running run."""
    conn.execute(
        "UPDATE runs_meta SET progress_step=?, heartbeat_at=? WHERE run_id=?",
        (progress_step, heartbeat_at, run_id),
    )
    conn.commit()


def set_pid(conn: sqlite3.Connection, *, run_id: str, pid: int) -> None:
    """Record the detached child PID once it has been spawned."""
    conn.execute("UPDATE runs_meta SET pid=? WHERE run_id=?", (pid, run_id))
    conn.commit()


def mark_orphaned(conn: sqlite3.Connection, *, run_id: str) -> None:
    """Mark a run whose process died without writing a terminal status."""
    conn.execute(
        "UPDATE runs_meta SET status='orphaned', completed_at=? WHERE run_id=?",
        (time.time(), run_id),
    )
    conn.commit()


PRUNE_KEEP = 20


def prune_runs(conn: sqlite3.Connection, *, spec_id: str,
               keep: int = PRUNE_KEEP) -> int:
    """Delete all but the newest ``keep`` runs for ``spec_id``.

    Removes both the runs_meta rows and their history rows. Returns the
    number of runs deleted.
    """
    rows = conn.execute(
        "SELECT run_id FROM runs_meta WHERE spec_id=? "
        "ORDER BY started_at DESC", (spec_id,),
    ).fetchall()
    stale = [r[0] for r in rows[keep:]]
    if not stale:
        return 0
    placeholders = ",".join("?" * len(stale))
    has_history = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='history'"
    ).fetchone()
    if has_history:
        conn.execute(
            f"DELETE FROM history WHERE simulation_id IN ({placeholders})",
            stale,
        )
    conn.execute(
        f"DELETE FROM runs_meta WHERE run_id IN ({placeholders})", stale,
    )
    conn.commit()
    return len(stale)


def query_runs(conn: sqlite3.Connection, *, spec_id: str) -> list[dict]:
    """List runs for one spec_id, newest first."""
    rows = conn.execute(
        "SELECT run_id, spec_id, label, params_json, started_at, "
        "completed_at, n_steps, status, generation_id FROM runs_meta "
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
    ``_type='step'`` entry whose ``address`` ends with ``Emitter`` (case-
    insensitive) — so the SQLiteEmitter captures the same observables the
    spec's primary emitter already declared.

    **2026-05-19 — empty-inputs fix (v2ecoli friction #1, deeper finding).**
    Spec-level ``_type`` does NOT control Step-vs-Process scheduling;
    ``find_instance_paths`` uses Python ``isinstance`` against the loaded
    class, and ``SQLiteEmitter`` extends ``Step``. A Step only re-fires when
    ``trigger_steps`` sees overlap between just-updated paths and the step's
    wired ``inputs``. With ``inputs={}`` the SQLiteEmitter fired exactly
    once at construction and never again, leaving ``runs.db`` with 1–2
    history rows per run no matter how long the sim ran (this broke every
    comparative visualization downstream).

    Fix: when the candidate-scan finds no spec emitter to mirror, default
    ``inputs`` to ``{"global_time": ["global_time"]}``. Every Process
    ``update`` advances ``global_time``, so that path lands in
    ``update_paths`` and ``trigger_steps`` re-enqueues the SQLiteEmitter
    once per composite apply (cadence ≈ composite tick rate). The state
    payload is whatever ``config.emit`` declares — empty by default, which
    still gives one history row per tick so callers can verify cadence.

    A future iteration may walk the composite recursively and inject the
    SQLiteEmitter as a sibling of every nested spec emitter — the higher-
    fidelity fix. Or upstream may grow ``SQLiteEmitterProcess(Process)`` so
    a periodic-interval emitter is expressible as a Process and the
    impedance mismatch goes away.

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
    # The user_emitter (added by inject_emitter_for_paths) carries the explicit
    # emit selection — prefer it over a composite's own emitter, which would
    # otherwise win simply by appearing earlier in iteration order.
    candidates = []
    if isinstance(state.get("user_emitter"), dict):
        candidates.append(state["user_emitter"])
    candidates.extend(v for k, v in state.items() if k != "user_emitter")
    for node in candidates:
        if not isinstance(node, dict):
            continue
        if node.get("_type") != "step":
            continue
        addr = node.get("address", "")
        # Match case-insensitively so kebab-case addresses register too —
        # `local:ram-emitter` should be picked up the same as
        # `local:RAMEmitter` (mem3dg-readdy friction #24). Case-sensitive
        # `endswith("Emitter")` silently skipped the workspace's RAM
        # emitter, the SQLiteEmitter then installed with empty emit:/inputs:,
        # and runs.db filled up with state={} rows that broke every viz.
        if not addr.lower().endswith("emitter"):
            continue
        emit_schema = dict((node.get("config") or {}).get("emit") or {})
        inputs = dict(node.get("inputs") or {})
        break

    # SQLiteEmitter joins file_path (directory) + db_file (filename) via
    # os.path.join, so we must split the absolute path accordingly.
    # (db_file is already a Path from the top of this function.)
    # v2ecoli friction #1 (deeper finding): a Step with empty `inputs`
    # never re-fires — `trigger_steps` has nothing to match against. When
    # the scan above found no spec emitter to mirror, fall back to
    # wiring `global_time` so every Process apply re-enqueues us.
    if not inputs:
        inputs = {"global_time": ["global_time"]}

    new_state = dict(state)
    new_state["sqlite_emitter"] = {
        # _type: "step" is the truth — SQLiteEmitter extends Step in
        # process-bigraph. Spec-level _type does not influence scheduling;
        # see the docstring for why `inputs` is the actual lever.
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


def _readout_observables(rr) -> list[str]:
    """Underlying observable path(s) a resolved readout needs emitted.

    Adds the *parent* array/scalar for each readout kind, not the selected
    element — the whole vector is emitted (self-describing via #1's id-coord)
    and ``RunReader.select`` picks the element at read time:

      - ``scalar``      → the dotted observable path.
      - ``element``     → the parent array observable (``bulk`` for bulk_id,
                          ``listeners.monomer_counts`` for literal_index /
                          monomer_id / …); the index_by value is resolved later.
      - ``expression``  → each operand's observable: ``bulk`` for bulk_id
                          operands, the dotted path for scalar operands.

    ``rr`` is a ``ResolvedReadout``; only its public dataclass fields are read.
    """
    out: list[str] = []
    if rr.kind in ("scalar", "element"):
        if rr.observable:
            out.append(rr.observable)
    elif rr.kind == "expression":
        for op in (rr.operand_ids or []):
            ib = op.get("index_by") or {}
            if ib.get("type") == "bulk_id":
                out.append("bulk")
            else:  # scalar operand → the dotted path is the value/token
                out.append(ib.get("value") or op.get("token"))
    return out


def collect_emit_paths_from_spec(spec: dict) -> list[str]:
    """Collect observable paths declared by a v4 study yaml, for emitter setup.

    Threaded into ``inject_emitter_for_declared_paths`` so the injected emitter
    captures the study's biology, not just ``_tick``. Sources:
      - ``readouts[].store_path``                       — v2ecoli explicit
                                                          per-readout paths
      - ``readouts[]`` resolved via ``readout_resolver`` — canonical/legacy
                                                          ``identifier:`` /
                                                          ``index_by:`` readouts
                                                          (the real dnaa studies)
      - ``tests[].measure.path``                        — per-test observables
      - ``behavior_tests[].measure.path``               — legacy v3 fallback
      - ``visualizations[].inputs_map.*`` / ``.config.inputs_map.*``
      - ``comparative_visualizations[].observable_path`` — multi-run overlays

    Dotted paths are normalised to slash form. Each path is ALSO emitted in its
    per-agent form (``agents/0/<path>``): v2ecoli single-cell composites scope
    listener stores under ``agents.0.``, so the agent-scoped variant is the one
    that actually carries data; the literal variant is kept too for non-agent
    composites. Returns a sorted, deduped list.
    """
    def _norm(p):
        if isinstance(p, str):
            return p.replace(".", "/") if p else None
        if isinstance(p, (list, tuple)):
            joined = "/".join(str(x) for x in p if x is not None)
            return joined or None
        return None

    paths: set[str] = set()
    for r in (spec.get("readouts") or []):
        if not isinstance(r, dict):
            continue
        p = _norm(r.get("store_path"))
        if p:
            paths.add(p)
    # Canonical/legacy readouts (identifier: / index_by:) carry no usable
    # store_path, so the loop above misses every real dnaa study. Resolve them
    # to their underlying observables and add the array/scalar that must be
    # emitted so RunReader.select can pick the element at read time. Imported
    # defensively: an older pbg_superpowers without the resolver simply yields
    # no readout-driven additions (the dashboard still works).
    try:
        from pbg_superpowers.readout_resolver import (
            resolve_study_readouts, ResolvedReadout,
        )
    except ImportError:
        resolve_study_readouts = None
    if resolve_study_readouts is not None:
        for rr in resolve_study_readouts(spec).values():
            if not isinstance(rr, ResolvedReadout):
                continue  # UnresolvedReadout → never fabricate a path
            for obs in _readout_observables(rr):
                p = _norm(obs)
                if p:
                    paths.add(p)
    for t in (spec.get("tests") or []) + (spec.get("behavior_tests") or []):
        if not isinstance(t, dict):
            continue
        m = t.get("measure") or {}
        p = _norm(m.get("path"))
        if p:
            paths.add(p)
    for v in (spec.get("visualizations") or []):
        if not isinstance(v, dict):
            continue
        for im_loc in (v.get("inputs_map"),
                       (v.get("config") or {}).get("inputs_map")):
            if isinstance(im_loc, dict):
                for val in im_loc.values():
                    p = _norm(val)
                    if p:
                        paths.add(p)
    for cv in (spec.get("comparative_visualizations") or []):
        if not isinstance(cv, dict):
            continue
        p = _norm(cv.get("observable_path") or cv.get("path"))
        if p:
            paths.add(p)

    expanded = set(paths)
    for p in list(paths):
        if not p.startswith("agents/"):
            expanded.add(f"agents/0/{p}")
    return sorted(expanded)


def inject_emitter_for_declared_paths(state: dict,
                                      declared_paths: list[str]) -> dict:
    """Like :func:`inject_emitter_for_paths` but does NOT pre-validate paths
    against the initial state tree, and writes the captured state as a NESTED
    tree (mirroring the wire structure) rather than flat underscore keys.

    Why bypass validation:
      Many observable stores are created at composite-build/run time by process
      ``outputs`` wires and aren't present in the spec-time state. v2ecoli's
      listener Steps materialise ``agents/0/listeners/<...>`` only after the
      composite runs, so the walk-existing-state approach (_collect_emit_leaves)
      skips them. This variant trusts the declared paths.

    Why nested vs flat:
      Flat ``"_".join(path)`` port names produce flat JSON keys that
      ``json_extract(state, '$.<dotted>.<path>')`` (comparative_viz, study_charts)
      can't navigate. The nested form mirrors the path hierarchy so the readers
      resolve it.

    Always also wires ``global_time``: it advances every composite apply, so
    wiring it guarantees the emitter Step re-fires every tick (an emitter wired
    only to rarely-mutating listener paths — or to paths absent at init — fires
    just once, collapsing history to ~1-2 rows). It also supplies the
    history.global_time x-axis column.

    Idempotent on re-call with the same declared paths.
    """
    if not declared_paths:
        return state
    paths = list(declared_paths)
    if "global_time" not in paths:
        paths.append("global_time")

    wires: dict = {}
    for raw in paths:
        parts = [p for p in raw.split("/") if p]
        if not parts:
            continue
        node = wires
        for p in parts[:-1]:
            existing = node.get(p)
            if not isinstance(existing, dict):
                existing = {}
                node[p] = existing
            node = existing
        node[parts[-1]] = list(parts)
    if not wires:
        return state

    def _to_schema(node):
        if isinstance(node, dict):
            return {k: _to_schema(v) for k, v in node.items()}
        return "node"
    emit_schema = _to_schema(wires)

    new_state = dict(state)
    existing = state.get("user_emitter")
    if (isinstance(existing, dict)
            and (existing.get("config") or {}).get("emit") == emit_schema
            and existing.get("inputs") == wires):
        return new_state
    new_state["user_emitter"] = {
        "_type": "step",
        "address": "local:RAMEmitter",
        "config": {"emit": emit_schema},
        "inputs": wires,
    }
    return new_state


def all_store_paths(state: dict) -> list[str]:
    """Return every top-level store key in ``state``, skipping step/process
    nodes.

    Used as the Composite Explorer Run tab's default emit selection: when the
    user hasn't hand-picked stores in the wiring view, the run emits every
    store. The returned keys feed ``inject_emitter_for_paths``, which walks
    each into its leaf stores.
    """
    return [
        key for key, node in state.items()
        if not (isinstance(node, dict)
                and node.get("_type") in ("process", "step"))
    ]


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
        # v2ecoli single-cell composites scope every listener store under
        # agents/0/...; study observables are declared at the biology path
        # (e.g. listeners/dnaA_cycle/atp_fraction). If the literal path
        # doesn't resolve, retry under agents/0/.
        if node is None and parts[:1] != ["agents"]:
            ag_parts = ["agents", "0"] + parts
            ag_node = _resolve_path(state, ag_parts)
            if ag_node is not None:
                parts, node = ag_parts, ag_node
        if node is None:
            # Path resolves nowhere (neither literal nor agents/0/ scoped).
            # This happens for listener outputs materialised only during the
            # run (e.g. listeners/replication_data/number_of_oric is absent at
            # init). We deliberately do NOT wire it: the SQLiteEmitter is a
            # Step that fires on input triggers, and an input path with no
            # store to trigger on leaves the step without a per-tick trigger —
            # it then emits ~once and the whole history collapses to 2 rows.
            # Dropping the unresolved path keeps per-tick capture of the rest.
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
