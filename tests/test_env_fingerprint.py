"""env_fingerprint — reconstructable env_id for the manifest (Task 2 / G1).

`compute_env()` snapshots the environment a run executed under
(workspace commit, sim package versions/SHAs, uv.lock hash, python/platform,
and the caller's already-computed cache fingerprint) and `env_id()` folds
that dict into a short, stable, order-independent digest.
"""
from vivarium_workbench.lib.env_fingerprint import compute_env, env_id


def test_env_id_stable_and_sensitive():
    base = {"workspace_commit": "abc", "sim_packages": {"v2ecoli": {"version": "1.2", "git_sha": "d"}},
            "lockfile_hash": "L", "python": "3.12.1", "platform": "mac", "cache_fingerprint": "cf"}
    reordered = dict(reversed(list(base.items())))
    assert env_id(base) == env_id(reordered)
    bumped = {**base, "sim_packages": {"v2ecoli": {"version": "1.3", "git_sha": "d"}}}
    assert env_id(base) != env_id(bumped)


def test_env_id_is_16_hex_chars():
    base = {"a": 1, "b": 2}
    h = env_id(base)
    assert isinstance(h, str) and len(h) == 16
    int(h, 16)  # raises ValueError if not hex


def test_compute_env_never_raises_and_has_documented_shape(tmp_path):
    # No ws_root, no cache_fingerprint: every field best-effort None, but the
    # full key set is always present so consumers can rely on it rather than
    # probing. This is the CANONICAL env shape — it MUST stay identical to
    # discovery_engine.study.run_registry.capture_environment (the two writers are
    # kept in sync by hand; changing one key set without the other is a drift bug).
    env = compute_env()
    assert set(env) == {"workspace_commit", "dirty", "branch", "sim_packages",
                        "lockfile_hash", "python", "platform", "cache_fingerprint"}
    assert env["cache_fingerprint"] is None


def test_compute_env_threads_cache_fingerprint_and_lockfile_hash(tmp_path):
    (tmp_path / "uv.lock").write_text("fake lockfile contents")
    env = compute_env(ws_root=tmp_path, cache_fingerprint="cf-123")
    assert env["cache_fingerprint"] == "cf-123"
    assert env["lockfile_hash"] is not None
    assert env["python"] is not None
    assert env["platform"] is not None


def test_compute_env_extra_packages(tmp_path):
    # extra_packages adds dists beyond the framework set (e.g. a study's simulator).
    env = compute_env(ws_root=tmp_path, extra_packages=["process-bigraph"])
    assert "process-bigraph" in env["sim_packages"]
