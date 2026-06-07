"""Pure-seam test for GET /api/simulations backing.

Builds a nested git workspace with two studies under one investigation, each
with a ``runs.db`` ``runs_meta`` row (one Parquet emitter_path, one SQLite),
checks out the investigation's branch, and asserts ``_simulations_payload``
returns both runs tagged investigation/study/emitter_type with the matching
``current`` slug.
"""
import sqlite3
import subprocess

from vivarium_dashboard.server import _simulations_payload

# Inline DDL mirroring vivarium_dashboard/lib/composite_runs.py's runs_meta,
# plus the nullable emitter_path column the run index reads.
RUNS_META_DDL = """
CREATE TABLE IF NOT EXISTS runs_meta (
    run_id        TEXT PRIMARY KEY,
    spec_id       TEXT NOT NULL,
    label         TEXT,
    params_json   TEXT,
    started_at    REAL NOT NULL,
    completed_at  REAL,
    n_steps       INTEGER,
    status        TEXT NOT NULL,
    sim_name      TEXT,
    emitter_path  TEXT
);
"""


def _write_run(db_path, run_id, started_at, emitter_path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(RUNS_META_DDL)
        conn.execute(
            "INSERT INTO runs_meta (run_id, spec_id, started_at, completed_at, "
            "status, emitter_path) VALUES (?,?,?,?,?,?)",
            (run_id, run_id, started_at, started_at + 1, "completed", emitter_path),
        )
        conn.commit()
    finally:
        conn.close()


def _git_ws(tmp):
    (tmp / "workspace.yaml").write_text("name: demo\n", encoding="utf-8")
    inv = tmp / "investigations" / "dnaa-replication"
    (inv / "studies").mkdir(parents=True)
    (inv / "investigation.yaml").write_text(
        "name: dnaa-replication\ntitle: dnaa-replication\nstudies: []\n",
        encoding="utf-8")
    # Two studies under the one investigation.
    for slug in ("alpha", "beta"):
        sdir = inv / "studies" / slug
        sdir.mkdir(parents=True)
        (sdir / "study.yaml").write_text(
            f"name: {slug}\ninvestigation: dnaa-replication\nruns: []\n",
            encoding="utf-8")
    # alpha → Parquet emitter, beta → SQLite emitter.
    _write_run(inv / "studies" / "alpha" / "runs.db", "run-alpha",
               100.0, "out/r/data.parquet")
    _write_run(inv / "studies" / "beta" / "runs.db", "run-beta",
               200.0, "studies/s/runs.db")
    for c in (["init", "-q"], ["config", "user.email", "t@t"],
              ["config", "user.name", "t"], ["add", "-A"],
              ["commit", "-qm", "init"], ["branch", "-M", "main"],
              ["checkout", "-qb", "investigation/dnaa-replication-v3"]):
        subprocess.run(["git", *c], cwd=tmp, check=True)
    return tmp


def test_simulations_payload(tmp_path):
    ws = _git_ws(tmp_path)
    payload = _simulations_payload(ws)

    assert payload["current"] == "dnaa-replication"
    runs = {r["run_id"]: r for r in payload["runs"]}
    assert set(runs) == {"run-alpha", "run-beta"}

    alpha = runs["run-alpha"]
    assert alpha["investigation"] == "dnaa-replication"
    assert alpha["study"] == "alpha"
    assert alpha["emitter_type"] == "Parquet"

    beta = runs["run-beta"]
    assert beta["investigation"] == "dnaa-replication"
    assert beta["study"] == "beta"
    assert beta["emitter_type"] == "SQLite"
