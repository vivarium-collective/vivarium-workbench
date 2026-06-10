"""Unit tests for vivarium_dashboard.lib.composite_runs."""
from vivarium_dashboard.lib.composite_runs import (
    connect, save_metadata, complete_metadata, query_runs, query_run,
    query_run_meta, update_progress, set_pid, mark_orphaned, prune_runs,
    inject_sqlite_emitter, auto_label, inject_emitter_for_paths,
    all_store_paths, collect_emit_paths_from_spec,
)


def test_schema_bootstrap(tmp_path):
    db_file = tmp_path / "runs.db"
    conn = connect(db_file)
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "runs_meta" in tables


def test_save_and_query_metadata(tmp_path):
    db_file = tmp_path / "runs.db"
    conn = connect(db_file)
    save_metadata(
        conn,
        spec_id="pkg.composites.demo",
        run_id="pkg.composites.demo__1715470512__abc123",
        params={"rate": 0.5},
        label="rate=0.5",
        started_at=1715470512.0,
        n_steps=10,
    )
    runs = query_runs(conn, spec_id="pkg.composites.demo")
    assert len(runs) == 1
    assert runs[0]["run_id"] == "pkg.composites.demo__1715470512__abc123"
    assert runs[0]["label"] == "rate=0.5"
    assert runs[0]["status"] == "running"


def test_complete_metadata_updates_status(tmp_path):
    db_file = tmp_path / "runs.db"
    conn = connect(db_file)
    save_metadata(conn, spec_id="s", run_id="r1", params={}, label="",
                  started_at=0.0, n_steps=10)
    complete_metadata(conn, run_id="r1", n_steps=10, status="completed")
    runs = query_runs(conn, spec_id="s")
    assert runs[0]["status"] == "completed"
    assert runs[0]["n_steps"] == 10
    assert runs[0]["completed_at"] is not None


def test_query_runs_filtered_by_spec_id(tmp_path):
    db_file = tmp_path / "runs.db"
    conn = connect(db_file)
    save_metadata(conn, spec_id="A", run_id="r1", params={}, label="",
                  started_at=1.0, n_steps=10)
    save_metadata(conn, spec_id="B", run_id="r2", params={}, label="",
                  started_at=2.0, n_steps=10)
    save_metadata(conn, spec_id="A", run_id="r3", params={}, label="",
                  started_at=3.0, n_steps=10)
    runs_a = query_runs(conn, spec_id="A")
    assert sorted(r["run_id"] for r in runs_a) == ["r1", "r3"]


def test_query_runs_returns_newest_first(tmp_path):
    db_file = tmp_path / "runs.db"
    conn = connect(db_file)
    save_metadata(conn, spec_id="A", run_id="r_old", params={}, label="",
                  started_at=1.0, n_steps=10)
    save_metadata(conn, spec_id="A", run_id="r_new", params={}, label="",
                  started_at=10.0, n_steps=10)
    runs = query_runs(conn, spec_id="A")
    assert runs[0]["run_id"] == "r_new"


def test_query_run_returns_empty_when_no_history(tmp_path):
    db_file = tmp_path / "runs.db"
    conn = connect(db_file)
    # No SQLiteEmitter ran against this DB yet → history table empty.
    save_metadata(conn, spec_id="s", run_id="r1", params={}, label="",
                  started_at=0.0, n_steps=10)
    trajectory = query_run(conn, run_id="r1")
    assert trajectory == []


def _example_state_with_emitter():
    return {
        "increase": {
            "_type": "process",
            "address": "local:IncreaseProcess",
            "config": {"rate": 2.0},
            "inputs": {"level": ["stores", "level"]},
            "outputs": {"level": ["stores", "level"]},
            "interval": 1.0,
        },
        "stores": {"level": 1.0},
        "emitter": {
            "_type": "step",
            "address": "local:RAMEmitter",
            "config": {"emit": {"level": "float"}},
            "inputs": {"level": ["stores", "level"]},
        },
    }


def test_inject_sqlite_emitter_adds_step():
    """SQLiteEmitter is wired as a Step (matching its Python class). The
    `_type` field is a spec-level annotation; process-bigraph's scheduler
    routes via isinstance, not via the string. See the docstring for the
    `inputs`-empty bug that motivated the fallback wiring."""
    state = _example_state_with_emitter()
    out = inject_sqlite_emitter(state, run_id="r1", db_file="/tmp/x.db")
    # Original state unchanged
    assert "sqlite_emitter" not in state
    # New emitter present in returned state
    assert "sqlite_emitter" in out
    sql_em = out["sqlite_emitter"]
    assert sql_em["_type"] == "step"
    assert "interval" not in sql_em  # Steps don't take interval
    assert sql_em["address"] == "local:SQLiteEmitter"
    assert sql_em["config"]["simulation_id"] == "r1"
    assert sql_em["config"]["file_path"] == "/tmp"
    assert sql_em["config"]["db_file"] == "x.db"


def test_inject_sqlite_emitter_copies_existing_emitter_inputs():
    """When the spec already declares an emitter, the SQLite emitter should
    consume the same input ports so persistence captures the same observables."""
    state = _example_state_with_emitter()
    out = inject_sqlite_emitter(state, run_id="r1", db_file="/tmp/x.db")
    assert out["sqlite_emitter"]["inputs"] == {"level": ["stores", "level"]}
    assert out["sqlite_emitter"]["config"]["emit"] == {"level": "float"}


def test_inject_sqlite_emitter_matches_lowercase_emitter_address():
    """mem3dg-readdy friction #24: a workspace registering its emitter as
    `local:ram-emitter` (kebab-case) used to silently fall through the
    `addr.endswith("Emitter")` check; the SQLiteEmitter then got an empty
    emit:/inputs: map and runs.db filled up with state={} rows. Fix is to
    match case-insensitively so kebab-case addresses register too."""
    state = {
        "increase": {
            "_type": "process",
            "address": "local:IncreaseProcess",
            "config": {"rate": 2.0},
            "inputs": {"level": ["stores", "level"]},
            "outputs": {"level": ["stores", "level"]},
            "interval": 1.0,
        },
        "stores": {"level": 1.0},
        # lowercase + hyphenated — historically would have been skipped
        "emitter": {
            "_type": "step",
            "address": "local:ram-emitter",
            "config": {"emit": {"level": "float"}},
            "inputs": {"level": ["stores", "level"]},
        },
    }
    out = inject_sqlite_emitter(state, run_id="r1", db_file="/tmp/x.db")
    # The SQLite emitter must have picked up the schema + inputs from the
    # kebab-case emitter, NOT defaulted to empty.
    assert out["sqlite_emitter"]["config"]["emit"] == {"level": "float"}
    assert out["sqlite_emitter"]["inputs"] == {"level": ["stores", "level"]}


def test_inject_sqlite_emitter_no_emitter_in_spec_falls_back_to_global_time():
    """When the spec has no emitter to mirror, wire `inputs` to
    `global_time` so `trigger_steps` re-fires us every composite apply.
    Without this fallback the SQLiteEmitter would have empty `inputs`,
    fire exactly once at construction, and leave runs.db with 1 row.
    See v2ecoli friction #1 (deeper finding, 2026-05-19)."""
    state = {
        "p": {"_type": "process", "address": "local:Foo",
              "outputs": {}, "interval": 1.0},
        "stores": {},
    }
    out = inject_sqlite_emitter(state, run_id="r1", db_file="/tmp/x.db")
    assert "sqlite_emitter" in out
    assert out["sqlite_emitter"]["config"]["emit"] == {}
    assert out["sqlite_emitter"]["inputs"] == {"global_time": ["global_time"]}


def test_inject_sqlite_emitter_prefers_user_emitter():
    """When inject_emitter_for_paths has added a user_emitter, the SQLite
    emitter mirrors THAT (the explicit emit selection) rather than the
    composite's own emitter — even though the latter appears earlier in the
    state dict's iteration order."""
    state = {
        "stores": {"level": 1.0, "extra": 2.0},
        # Composite's own emitter — narrow, appears first in iteration order.
        "emitter": {
            "_type": "step", "address": "local:RAMEmitter",
            "config": {"emit": {"level": "float"}},
            "inputs": {"level": ["stores", "level"]},
        },
        # Injected by inject_emitter_for_paths — the user's emit selection.
        "user_emitter": {
            "_type": "step", "address": "local:RAMEmitter",
            "config": {"emit": {"stores_level": "node", "stores_extra": "node"}},
            "inputs": {"stores_level": ["stores", "level"],
                       "stores_extra": ["stores", "extra"]},
        },
    }
    out = inject_sqlite_emitter(state, run_id="r1", db_file="/tmp/x.db")
    assert out["sqlite_emitter"]["config"]["emit"] == {
        "stores_level": "node", "stores_extra": "node",
    }
    assert out["sqlite_emitter"]["inputs"] == {
        "stores_level": ["stores", "level"],
        "stores_extra": ["stores", "extra"],
    }


def test_inject_sqlite_emitter_idempotent():
    state = _example_state_with_emitter()
    once = inject_sqlite_emitter(state, run_id="r1", db_file="/tmp/x.db")
    twice = inject_sqlite_emitter(once, run_id="r1", db_file="/tmp/x.db")
    assert once == twice


def test_inject_sqlite_emitter_accepts_path(tmp_path):
    state = _example_state_with_emitter()
    out = inject_sqlite_emitter(state, run_id="r1", db_file=tmp_path / "x.db")
    assert isinstance(out["sqlite_emitter"]["config"]["db_file"], str)


def test_auto_label_empty():
    assert auto_label({}) == "defaults"


def test_auto_label_sorted_concat():
    assert auto_label({"b": 2, "a": 1}) == "a=1, b=2"


def test_auto_label_truncated_to_80():
    overrides = {f"k{i}": i for i in range(50)}
    out = auto_label(overrides)
    assert len(out) <= 80


# -- inject_emitter_for_paths ------------------------------------------------

def test_inject_emitter_for_paths_leaf():
    """Single-leaf path: user_emitter wires that leaf via a slug port."""
    state = {"stores": {"level": 1.0}}
    out = inject_emitter_for_paths(state, ["stores/level"])
    assert "user_emitter" in out
    em = out["user_emitter"]
    assert em["_type"] == "step"
    assert em["address"] == "local:RAMEmitter"
    assert em["config"]["emit"] == {"stores_level": "node"}
    assert em["inputs"] == {"stores_level": ["stores", "level"]}


def test_inject_emitter_for_paths_subtree_cascades():
    """Subtree path: every leaf under the subtree becomes its own port."""
    state = {"stores": {"level": 1.0, "fields": {"glucose": 5.0}}}
    out = inject_emitter_for_paths(state, ["stores"])
    em = out["user_emitter"]
    # Two leaves: stores/level and stores/fields/glucose
    assert em["config"]["emit"] == {
        "stores_fields_glucose": "node",
        "stores_level": "node",
    }
    assert em["inputs"] == {
        "stores_fields_glucose": ["stores", "fields", "glucose"],
        "stores_level": ["stores", "level"],
    }


def test_inject_emitter_for_paths_skips_processes():
    """Walk should skip process/step nodes so only stores are emitted."""
    state = {
        "root": {
            "proc": {"_type": "process", "address": "local:Foo",
                      "outputs": {}, "interval": 1.0},
            "store": 1.0,
        },
    }
    out = inject_emitter_for_paths(state, ["root"])
    em = out["user_emitter"]
    assert em["config"]["emit"] == {"root_store": "node"}
    assert em["inputs"] == {"root_store": ["root", "store"]}


def test_inject_emitter_for_paths_empty_list_noop():
    """Empty path list returns state unchanged."""
    state = {"stores": {"level": 1.0}}
    out = inject_emitter_for_paths(state, [])
    assert out is state
    assert "user_emitter" not in out


# -- all_store_paths ---------------------------------------------------------

def test_all_store_paths_returns_store_keys_skipping_steps_and_processes():
    """all_store_paths lists top-level store keys, omitting step/process nodes
    so an empty wiring-view selection can default to emitting every store."""
    state = {
        "biomodel_id": "BIOMD0000000001",
        "results": {"copasi": {}, "tellurium": {}},
        "comparison": {},
        "load": {"_type": "step", "address": "local:LoadBiomodelStep"},
        "sim_proc": {"_type": "process", "address": "local:Sim"},
    }
    assert all_store_paths(state) == ["biomodel_id", "results", "comparison"]


def test_all_store_paths_empty_state():
    """No stores (only steps) yields an empty list."""
    state = {"load": {"_type": "step", "address": "local:LoadBiomodelStep"}}
    assert all_store_paths(state) == []


def test_connect_runs_meta_has_sim_name(tmp_path):
    """Fresh runs.db must have sim_name — _get_investigation_detail SELECTs it."""
    from vivarium_dashboard.lib.composite_runs import connect
    conn = connect(tmp_path / "fresh.db")
    cols = {row[1] for row in conn.execute("PRAGMA table_info(runs_meta)")}
    conn.close()
    assert "sim_name" in cols


def test_connect_adds_sim_name_to_legacy_db(tmp_path):
    """connect() ALTERs sim_name into a pre-existing runs_meta that lacks it."""
    import sqlite3
    db = tmp_path / "legacy.db"
    raw = sqlite3.connect(str(db))
    raw.executescript('''
        CREATE TABLE runs_meta (
            run_id TEXT PRIMARY KEY, spec_id TEXT NOT NULL, label TEXT,
            params_json TEXT, started_at REAL NOT NULL, completed_at REAL,
            n_steps INTEGER, status TEXT NOT NULL
        );
    ''')
    raw.execute(
        "INSERT INTO runs_meta VALUES (?,?,?,?,?,?,?,?)",
        ("r1", "pkg.foo", "lbl", "{}", 1.0, 2.0, 5, "completed"),
    )
    raw.commit()
    raw.close()

    from vivarium_dashboard.lib.composite_runs import connect
    conn = connect(db)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(runs_meta)")}
    # Existing row survives the migration.
    n = conn.execute("SELECT COUNT(*) FROM runs_meta").fetchone()[0]
    conn.close()
    assert "sim_name" in cols
    assert n == 1


def test_connect_adds_new_columns_to_legacy_db(tmp_path):
    """connect() migrates a pre-existing DB that lacks the new columns."""
    import sqlite3
    db_file = tmp_path / "runs.db"
    # Simulate a legacy DB: original 8-column schema, one row.
    legacy = sqlite3.connect(str(db_file))
    legacy.execute(
        "CREATE TABLE runs_meta (run_id TEXT PRIMARY KEY, spec_id TEXT NOT NULL, "
        "label TEXT, params_json TEXT, started_at REAL NOT NULL, "
        "completed_at REAL, n_steps INTEGER, status TEXT NOT NULL)"
    )
    legacy.execute(
        "INSERT INTO runs_meta (run_id, spec_id, started_at, status) "
        "VALUES ('r-old', 's', 1.0, 'completed')"
    )
    legacy.commit()
    legacy.close()

    conn = connect(db_file)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(runs_meta)")}
    assert {"pid", "progress_step", "log_path", "heartbeat_at",
            "generation_id"} <= cols
    # Legacy row survived.
    row = conn.execute("SELECT spec_id FROM runs_meta WHERE run_id='r-old'").fetchone()
    assert row["spec_id"] == "s"


def test_save_metadata_stamps_generation_id(tmp_path):
    """Expert-feedback A.2: a run can carry the workspace's current
    coordinated-generation id so the report can flag stale panels."""
    conn = connect(tmp_path / "runs.db")
    save_metadata(conn, spec_id="s", run_id="r1", params={}, label="",
                  started_at=1.0, n_steps=10,
                  generation_id="gen-20260521T084700Z-a1b2c3")
    meta = query_run_meta(conn, run_id="r1")
    assert meta["generation_id"] == "gen-20260521T084700Z-a1b2c3"


def test_save_metadata_generation_id_defaults_none(tmp_path):
    conn = connect(tmp_path / "runs.db")
    save_metadata(conn, spec_id="s", run_id="r1", params={}, label="",
                  started_at=1.0, n_steps=10)
    assert query_run_meta(conn, run_id="r1")["generation_id"] is None


def test_query_runs_includes_generation_id(tmp_path):
    conn = connect(tmp_path / "runs.db")
    save_metadata(conn, spec_id="s", run_id="r1", params={}, label="",
                  started_at=1.0, n_steps=10, generation_id="gen-X")
    runs = query_runs(conn, spec_id="s")
    assert runs[0]["generation_id"] == "gen-X"


def test_save_metadata_stores_requested_n_steps_and_log_path(tmp_path):
    conn = connect(tmp_path / "runs.db")
    save_metadata(conn, spec_id="s", run_id="r1", params={}, label="",
                  started_at=1.0, n_steps=20, log_path=".pbg/runs/r1/run.log")
    meta = query_run_meta(conn, run_id="r1")
    assert meta["n_steps"] == 20
    assert meta["log_path"] == ".pbg/runs/r1/run.log"
    assert meta["status"] == "running"
    assert meta["progress_step"] == 0


def test_update_progress_advances_step_and_heartbeat(tmp_path):
    conn = connect(tmp_path / "runs.db")
    save_metadata(conn, spec_id="s", run_id="r1", params={}, label="",
                  started_at=1.0, n_steps=10)
    update_progress(conn, run_id="r1", progress_step=4, heartbeat_at=123.0)
    meta = query_run_meta(conn, run_id="r1")
    assert meta["progress_step"] == 4
    assert meta["heartbeat_at"] == 123.0


def test_set_pid_records_pid(tmp_path):
    conn = connect(tmp_path / "runs.db")
    save_metadata(conn, spec_id="s", run_id="r1", params={}, label="",
                  started_at=1.0, n_steps=10)
    set_pid(conn, run_id="r1", pid=4242)
    assert query_run_meta(conn, run_id="r1")["pid"] == 4242


def test_mark_orphaned_sets_terminal_status(tmp_path):
    conn = connect(tmp_path / "runs.db")
    save_metadata(conn, spec_id="s", run_id="r1", params={}, label="",
                  started_at=1.0, n_steps=10)
    mark_orphaned(conn, run_id="r1")
    meta = query_run_meta(conn, run_id="r1")
    assert meta["status"] == "orphaned"
    assert meta["completed_at"] is not None


def test_query_run_meta_returns_none_for_unknown(tmp_path):
    conn = connect(tmp_path / "runs.db")
    assert query_run_meta(conn, run_id="nope") is None


def test_prune_runs_keeps_only_newest_n_per_spec(tmp_path):
    conn = connect(tmp_path / "runs.db")
    for i in range(5):
        save_metadata(conn, spec_id="s", run_id=f"r{i}", params={}, label="",
                      started_at=float(i), n_steps=1)
    save_metadata(conn, spec_id="other", run_id="x", params={}, label="",
                  started_at=99.0, n_steps=1)
    prune_runs(conn, spec_id="s", keep=2)
    remaining = sorted(r["run_id"] for r in query_runs(conn, spec_id="s"))
    assert remaining == ["r3", "r4"]
    # Other spec untouched.
    assert len(query_runs(conn, spec_id="other")) == 1


# ---------------------------------------------------------------------------
# collect_emit_paths_from_spec — readout-driven emit paths (#5)
# ---------------------------------------------------------------------------

def test_collect_emit_paths_honors_resolved_readouts():
    """Canonical/legacy readouts (identifier/index_by) must drive emit paths.

    Before #5 only ``store_path`` was read, so the real dnaa studies (which use
    ``identifier:`` with bracket-index, bulk fraction expressions, and bare
    dotted paths) contributed ZERO emit paths. Now ``resolve_study_readouts``
    feeds the underlying observables into the emit set:
      - ``listeners.monomer_counts[3861]``   (element/literal_index) → parent
        array ``listeners/monomer_counts`` (whole vector emitted; the element
        is selected at read time).
      - bulk fraction expression                (expression)         → ``bulk``
        (the bulk array carries every operand id).
      - ``listeners.mass.cell_mass``            (scalar)             → the path.
    Each also appears in its ``agents/0/...`` per-agent variant.
    """
    spec = {"readouts": [
        {"name": "monomer", "identifier": "listeners.monomer_counts[3861]"},
        {"name": "frac",
         "identifier": "bulk CPLX0-3933[c] / (CPLX0-3933[c] + MONOMER0-160[c])"},
        {"name": "mass", "identifier": "listeners.mass.cell_mass"},
    ]}
    paths = collect_emit_paths_from_spec(spec)
    for base in ("listeners/monomer_counts", "bulk", "listeners/mass/cell_mass"):
        assert base in paths, f"missing {base}: {paths}"
        assert f"agents/0/{base}" in paths, f"missing agents/0/{base}: {paths}"


def test_collect_emit_paths_expression_scalar_operands():
    """Non-bulk expression operands contribute their dotted observable paths."""
    spec = {"readouts": [
        {"name": "ratio",
         "identifier": "listeners.mass.cell_mass / listeners.mass.dry_mass"},
    ]}
    paths = collect_emit_paths_from_spec(spec)
    assert "listeners/mass/cell_mass" in paths
    assert "listeners/mass/dry_mass" in paths


def test_collect_emit_paths_skips_unresolved_readouts():
    """Prose/derived readouts that cannot be resolved are NOT fabricated.

    ``identifier: derived`` resolves to an UnresolvedReadout and carries no
    ``store_path``, so it contributes nothing — the resolver never invents a
    path it could not parse.
    """
    spec = {"readouts": [
        {"name": "vague", "identifier": "derived"},
        {"name": "multi",
         "identifier": "bulk MONOMER0-160[c] · MONOMER0-161[c]"},
    ]}
    paths = collect_emit_paths_from_spec(spec)
    assert paths == [], f"expected no fabricated paths, got {paths}"


def test_collect_emit_paths_store_path_regression():
    """Existing store_path / tests / viz collection is unchanged by #5."""
    spec = {
        "readouts": [{"name": "rp", "store_path": "listeners.rna_counts"}],
        "tests": [{"measure": {"path": "listeners.mass.cell_mass"}}],
        "behavior_tests": [{"measure": {"path": "global_time"}}],
        "visualizations": [{"inputs_map": {"x": "listeners.foo"}}],
        "comparative_visualizations": [{"observable_path": "listeners.bar"}],
    }
    paths = collect_emit_paths_from_spec(spec)
    for base in ("listeners/rna_counts", "listeners/mass/cell_mass",
                 "global_time", "listeners/foo", "listeners/bar"):
        assert base in paths, f"missing {base}: {paths}"
        assert f"agents/0/{base}" in paths


def test_collect_emit_paths_empty_spec():
    assert collect_emit_paths_from_spec({}) == []
