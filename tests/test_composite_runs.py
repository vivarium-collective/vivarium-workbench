"""Unit tests for vivarium_dashboard.lib.composite_runs."""
from vivarium_dashboard.lib.composite_runs import (
    connect, save_metadata, complete_metadata, query_runs, query_run,
    inject_sqlite_emitter, auto_label, inject_emitter_for_paths,
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
                  started_at=0.0)
    complete_metadata(conn, run_id="r1", n_steps=10, status="completed")
    runs = query_runs(conn, spec_id="s")
    assert runs[0]["status"] == "completed"
    assert runs[0]["n_steps"] == 10
    assert runs[0]["completed_at"] is not None


def test_query_runs_filtered_by_spec_id(tmp_path):
    db_file = tmp_path / "runs.db"
    conn = connect(db_file)
    save_metadata(conn, spec_id="A", run_id="r1", params={}, label="",
                  started_at=1.0)
    save_metadata(conn, spec_id="B", run_id="r2", params={}, label="",
                  started_at=2.0)
    save_metadata(conn, spec_id="A", run_id="r3", params={}, label="",
                  started_at=3.0)
    runs_a = query_runs(conn, spec_id="A")
    assert sorted(r["run_id"] for r in runs_a) == ["r1", "r3"]


def test_query_runs_returns_newest_first(tmp_path):
    db_file = tmp_path / "runs.db"
    conn = connect(db_file)
    save_metadata(conn, spec_id="A", run_id="r_old", params={}, label="",
                  started_at=1.0)
    save_metadata(conn, spec_id="A", run_id="r_new", params={}, label="",
                  started_at=10.0)
    runs = query_runs(conn, spec_id="A")
    assert runs[0]["run_id"] == "r_new"


def test_query_run_returns_empty_when_no_history(tmp_path):
    db_file = tmp_path / "runs.db"
    conn = connect(db_file)
    # No SQLiteEmitter ran against this DB yet → history table empty.
    save_metadata(conn, spec_id="s", run_id="r1", params={}, label="",
                  started_at=0.0)
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
    state = _example_state_with_emitter()
    out = inject_sqlite_emitter(state, run_id="r1", db_file="/tmp/x.db")
    # Original state unchanged
    assert "sqlite_emitter" not in state
    # New emitter present in returned state
    assert "sqlite_emitter" in out
    sql_em = out["sqlite_emitter"]
    assert sql_em["_type"] == "step"
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


def test_inject_sqlite_emitter_no_emitter_in_spec():
    """When the spec has no emitter, inject a SQLite emitter with an empty
    schema — the run still persists step counts even without observables."""
    state = {
        "p": {"_type": "process", "address": "local:Foo",
              "outputs": {}, "interval": 1.0},
        "stores": {},
    }
    out = inject_sqlite_emitter(state, run_id="r1", db_file="/tmp/x.db")
    assert "sqlite_emitter" in out
    assert out["sqlite_emitter"]["config"]["emit"] == {}
    assert out["sqlite_emitter"]["inputs"] == {}


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
