import json
from vivarium_workbench.lib import composite_runs as cr


def test_migration_adds_manifest_column(tmp_path):
    conn = cr.connect(tmp_path / "runs.db")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(runs_meta)")}
    assert "manifest_json" in cols


def test_save_metadata_writes_manifest(tmp_path):
    conn = cr.connect(tmp_path / "runs.db")
    manifest = {"version": 1, "spec_id": "s", "params": {"seed": 0, "cache_dir": "out/cache"},
                "n_steps": 100, "emitter": "parquet", "emit_paths": ["bulk"],
                "runtime": {"emitter": "parquet"}, "origin": "study", "study": "s1"}
    cr.save_metadata(conn, spec_id="s", run_id="r1", params={"seed": 0}, label="b",
                     started_at=0.0, n_steps=100, manifest=manifest)
    row = conn.execute("SELECT manifest_json FROM runs_meta WHERE run_id='r1'").fetchone()
    assert json.loads(row[0])["emitter"] == "parquet"
    assert json.loads(row[0])["params"]["cache_dir"] == "out/cache"


def test_save_metadata_no_manifest_is_null(tmp_path):
    conn = cr.connect(tmp_path / "runs.db")
    cr.save_metadata(conn, spec_id="s", run_id="r2", params={"seed": 0}, label="b",
                     started_at=0.0, n_steps=100)
    row = conn.execute("SELECT manifest_json FROM runs_meta WHERE run_id='r2'").fetchone()
    assert row[0] is None


def test_build_run_manifest_shape():
    m = cr.build_run_manifest(spec_id="s", params={"seed": 0}, n_steps=100,
                              emitter="parquet", emit_paths=["bulk"], runtime={"x": 1},
                              origin="study", study="s1", pkg="v2ecoli", generation_id=None)
    for k in ("version", "spec_id", "params", "n_steps", "emitter", "emit_paths",
              "runtime", "origin", "study", "pkg", "code_version"):
        assert k in m


def test_build_run_manifest_is_version_2_with_null_placeholders():
    # reproducible-rerun-spine Task 1: manifest schema bumped to v2 with new
    # keys filled in by later tasks (env=Task 2 [now populated, see below],
    # fingerprint_fields=Task 3 [now populated, see below], seed=Task 4 [now
    # populated, see test_build_run_manifest_first_class_seed* below]).
    # result_fingerprint stays null at manifest-build time even after Task 3:
    # no result exists yet at launch — it's computed post-hoc at completion
    # (run_runner.execute / composite_subprocess.run_composite_subprocess)
    # and stored in the runs_meta.result_fingerprint COLUMN, not written back
    # into this snapshot.
    m = cr.build_run_manifest(spec_id="s", params={}, n_steps=100,
                              emitter="parquet", emit_paths=["bulk"], runtime={"x": 1},
                              origin="study", study="s1", pkg="v2ecoli", generation_id=None)
    assert m["version"] == 2
    for k in ("seed", "result_fingerprint"):
        assert k in m
        assert m[k] is None


# --- seed (reproducible-rerun-spine Task 4) ---------------------------------

def test_build_run_manifest_explicit_seed():
    m = cr.build_run_manifest(spec_id="s", params={}, n_steps=1,
                              emitter=None, emit_paths=[], runtime={},
                              origin="composite", seed=7)
    assert m["seed"] == 7


def test_build_run_manifest_seed_sniffed_from_params_when_not_explicit():
    # Every pre-Task-4 caller already stashes the seed as a plain params key
    # (e.g. a study baseline's params: {seed: 7, ...}) — build_run_manifest
    # sniffs it so those call sites get a non-null first-class seed without
    # every one needing to pop it out and pass it explicitly.
    m = cr.build_run_manifest(spec_id="s", params={"seed": 7}, n_steps=1,
                              emitter=None, emit_paths=[], runtime={},
                              origin="composite")
    assert m["seed"] == 7


def test_build_run_manifest_explicit_seed_wins_over_params():
    m = cr.build_run_manifest(spec_id="s", params={"seed": 999}, n_steps=1,
                              emitter=None, emit_paths=[], runtime={},
                              origin="composite", seed=7)
    assert m["seed"] == 7


def test_build_run_manifest_fingerprint_fields_defaults_to_emit_paths():
    # Task 3 / G4: fingerprint_fields defaults to this run's own emit_paths
    # (the study/composite's declared observables, already resolved at
    # launch by the caller — e.g. collect_emit_paths_from_spec) when the
    # caller doesn't pass fingerprint_fields explicitly.
    m = cr.build_run_manifest(spec_id="s", params={}, n_steps=100,
                              emitter="parquet", emit_paths=["bulk", "mass"],
                              runtime={}, origin="study", study="s1")
    assert m["fingerprint_fields"] == ["bulk", "mass"]


def test_build_run_manifest_fingerprint_fields_explicit_override():
    m = cr.build_run_manifest(spec_id="s", params={}, n_steps=100,
                              emitter="parquet", emit_paths=["bulk", "mass"],
                              runtime={}, origin="study", study="s1",
                              fingerprint_fields=["doubling_time"])
    assert m["fingerprint_fields"] == ["doubling_time"]


def test_build_run_manifest_code_version_best_effort_ok_without_ws_root():
    # No ws_root / unresolvable pkg → code_version degrades to Nones, never raises.
    m = cr.build_run_manifest(spec_id="s", params={}, n_steps=1,
                              emitter=None, emit_paths=[], runtime={},
                              origin="composite")
    assert m["code_version"]["git_sha"] is None
    assert m["code_version"]["package"] is None


# --- env / env_id (reproducible-rerun-spine Task 2) ------------------------

def test_build_run_manifest_populates_env():
    # env is no longer a null placeholder: it's a compute_env() dict, always
    # present with the documented keys, even without ws_root/cache_fingerprint.
    m = cr.build_run_manifest(spec_id="s", params={}, n_steps=1,
                              emitter=None, emit_paths=[], runtime={},
                              origin="composite")
    assert isinstance(m["env"], dict)
    for k in ("workspace_commit", "sim_packages", "lockfile_hash", "python",
              "platform", "cache_fingerprint"):
        assert k in m["env"]


def test_build_run_manifest_threads_explicit_cache_fingerprint(tmp_path):
    (tmp_path / "uv.lock").write_text("lock")
    m = cr.build_run_manifest(spec_id="s", params={}, n_steps=1,
                              emitter=None, emit_paths=[], runtime={},
                              origin="composite", ws_root=tmp_path,
                              cache_fingerprint="cf-explicit")
    assert m["env"]["cache_fingerprint"] == "cf-explicit"
    assert m["env"]["lockfile_hash"] is not None


def test_build_run_manifest_sniffs_cache_fingerprint_from_params():
    # v2ecoli's run_condition_multigen_parquet.py threads its already-computed
    # cache fingerprint scalar onto params["cache_fingerprint"] — build_run_manifest
    # picks it up when no explicit cache_fingerprint kwarg is given.
    m = cr.build_run_manifest(spec_id="s", params={"cache_fingerprint": "cf-from-params"},
                              n_steps=1, emitter=None, emit_paths=[], runtime={},
                              origin="composite")
    assert m["env"]["cache_fingerprint"] == "cf-from-params"


def test_migration_adds_env_id_column(tmp_path):
    conn = cr.connect(tmp_path / "runs.db")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(runs_meta)")}
    assert "env_id" in cols


def test_save_metadata_writes_env_id_from_manifest_env(tmp_path):
    conn = cr.connect(tmp_path / "runs.db")
    manifest = cr.build_run_manifest(spec_id="s", params={}, n_steps=1,
                                     emitter=None, emit_paths=[], runtime={},
                                     origin="composite")
    cr.save_metadata(conn, spec_id="s", run_id="r3", params={}, label="b",
                     started_at=0.0, n_steps=1, manifest=manifest)
    row = conn.execute("SELECT env_id FROM runs_meta WHERE run_id='r3'").fetchone()
    assert row[0] is not None
    assert len(row[0]) == 16


def test_save_metadata_no_manifest_env_id_is_null(tmp_path):
    conn = cr.connect(tmp_path / "runs.db")
    cr.save_metadata(conn, spec_id="s", run_id="r4", params={"seed": 0}, label="b",
                     started_at=0.0, n_steps=100)
    row = conn.execute("SELECT env_id FROM runs_meta WHERE run_id='r4'").fetchone()
    assert row[0] is None


# --- result_fingerprint / provenance_status columns (reproducible-rerun-spine
# Task 3 / G4) ----------------------------------------------------------------

def test_migration_adds_result_fingerprint_and_provenance_status_columns(tmp_path):
    conn = cr.connect(tmp_path / "runs.db")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(runs_meta)")}
    assert "result_fingerprint" in cols
    assert "provenance_status" in cols


def test_set_result_fingerprint_roundtrips(tmp_path):
    conn = cr.connect(tmp_path / "runs.db")
    cr.save_metadata(conn, spec_id="s", run_id="r5", params={}, label="b",
                     started_at=0.0, n_steps=1)
    cr.set_result_fingerprint(conn, run_id="r5", fingerprint="deadbeef")
    row = conn.execute(
        "SELECT result_fingerprint FROM runs_meta WHERE run_id='r5'").fetchone()
    assert row[0] == "deadbeef"


def test_set_result_fingerprint_missing_run_id_does_not_raise(tmp_path):
    conn = cr.connect(tmp_path / "runs.db")
    cr.set_result_fingerprint(conn, run_id="no-such-run", fingerprint="x")  # no raise


def test_set_provenance_status_roundtrips(tmp_path):
    conn = cr.connect(tmp_path / "runs.db")
    cr.save_metadata(conn, spec_id="s", run_id="r6", params={}, label="b",
                     started_at=0.0, n_steps=1)
    cr.set_provenance_status(conn, run_id="r6", status="nondeterministic")
    row = conn.execute(
        "SELECT provenance_status FROM runs_meta WHERE run_id='r6'").fetchone()
    assert row[0] == "nondeterministic"


def test_query_run_meta_includes_env_id_and_fingerprint_columns(tmp_path):
    # env_id predates Task 3 but was never added to query_run_meta's SELECT
    # (a latent gap); fixed alongside the new columns since verify_reproduction
    # needs all three off the same row.
    conn = cr.connect(tmp_path / "runs.db")
    manifest = cr.build_run_manifest(spec_id="s", params={}, n_steps=1,
                                     emitter=None, emit_paths=[], runtime={},
                                     origin="composite")
    cr.save_metadata(conn, spec_id="s", run_id="r7", params={}, label="b",
                     started_at=0.0, n_steps=1, manifest=manifest)
    cr.set_result_fingerprint(conn, run_id="r7", fingerprint="abc123")
    cr.set_provenance_status(conn, run_id="r7", status="nondeterministic")
    row = cr.query_run_meta(conn, run_id="r7")
    assert row["env_id"] is not None
    assert row["result_fingerprint"] == "abc123"
    assert row["provenance_status"] == "nondeterministic"


# ---------------------------------------------------------------------------
# environments — multi-entry env pins (dual-engine W1)
# ---------------------------------------------------------------------------

def test_manifest_environments_primary_always_present():
    m = cr.build_run_manifest(spec_id="s", params={}, n_steps=1,
                              emitter="sqlite", emit_paths=[], runtime={},
                              origin="study")
    envs = m["environments"]
    assert isinstance(envs, list) and len(envs) == 1
    p = envs[0]
    assert p["role"] == "primary"
    # no ws_root → every provenance field independently degrades to None
    for k in ("repo", "commit", "remote_url", "lockfile_hash"):
        assert p[k] is None


def test_manifest_environments_primary_matches_code_version(tmp_path):
    import subprocess
    ws = tmp_path / "ws"
    ws.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=ws, check=True)
    (ws / "uv.lock").write_text("lock-bytes", encoding="utf-8")
    (ws / "f").write_text("x")
    subprocess.run(["git", "add", "."], cwd=ws, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], cwd=ws, check=True)
    m = cr.build_run_manifest(spec_id="s", params={}, n_steps=1,
                              emitter="sqlite", emit_paths=[], runtime={},
                              origin="study", ws_root=ws)
    p = m["environments"][0]
    assert p["commit"] == m["code_version"]["git_sha"] and p["commit"]
    assert p["repo"] == m["code_version"]["repo"]
    assert p["lockfile_hash"]  # the uv.lock hash landed


def test_manifest_declared_environment_recorded_unresolved():
    m = cr.build_run_manifest(spec_id="s", params={}, n_steps=1,
                              emitter="sqlite", emit_paths=[], runtime={},
                              origin="study",
                              declared_environment={"repo": "CovertLabEcoli/vEcoli-private",
                                                    "ref": "a1b2c3d"})
    envs = m["environments"]
    assert [e["role"] for e in envs] == ["primary", "declared"]
    d = envs[1]
    assert d["repo"] == "CovertLabEcoli/vEcoli-private" and d["ref"] == "a1b2c3d"
    # honesty: declared ≠ executed until a W4/W5 dispatch resolves it
    assert d["commit"] is None and d["lockfile_hash"] is None


# --- declared environment + verification (environment-declaration) ----------

_PULSE_ENV_YAML = """\
default: pulse
environments:
  pulse:
    core:
      process-bigraph: ">=1.10.1"
    packages:
      protobuf: "==7.35.0"
"""


def test_manifest_records_declared_environment_and_verification(tmp_path):
    # A workspace that declares a named environment and a run that references it:
    # the manifest records the declared name and a verification verdict, and the
    # captured fingerprint is driven by the environment's members.
    (tmp_path / "environments.yaml").write_text(_PULSE_ENV_YAML)
    m = cr.build_run_manifest(spec_id="s", params={}, n_steps=1, emitter=None,
                              emit_paths=[], runtime={}, origin="composite",
                              ws_root=tmp_path, environment_ref="pulse")
    assert m["environment_ref"] == "pulse"
    assert "environment_verification" in m
    assert "matches" in m["environment_verification"]
    # capture is driven by the declared members (core + packages)
    assert set(m["env"]["sim_packages"]) == {"process-bigraph", "protobuf"}


def test_manifest_environment_keys_present_and_null_when_undeclared(tmp_path):
    # Back-compat: no environments.yaml, no ref — keys exist (stable shape) but
    # the declaration is null and verification trivially matches.
    m = cr.build_run_manifest(spec_id="s", params={}, n_steps=1, emitter=None,
                              emit_paths=[], runtime={}, origin="composite",
                              ws_root=tmp_path)
    assert m["environment_ref"] is None
    assert m["environment_verification"]["matches"] is True
    assert m["environment_verification"]["drift"] == []
