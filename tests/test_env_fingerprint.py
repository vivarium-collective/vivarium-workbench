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
    # probing.
    env = compute_env()
    for k in ("workspace_commit", "sim_packages", "lockfile_hash", "python",
              "platform", "cache_fingerprint"):
        assert k in env
    assert env["cache_fingerprint"] is None


def test_compute_env_threads_cache_fingerprint_and_lockfile_hash(tmp_path):
    (tmp_path / "uv.lock").write_text("fake lockfile contents")
    env = compute_env(ws_root=tmp_path, cache_fingerprint="cf-123")
    assert env["cache_fingerprint"] == "cf-123"
    assert env["lockfile_hash"] is not None
    assert env["python"] is not None
    assert env["platform"] is not None


def test_compute_env_default_packages_unchanged():
    # Back-compat: with no packages arg, the captured set is the built-in
    # _SIM_PACKAGES list (byte-for-byte today's behavior for v2ecoli).
    from vivarium_workbench.lib.env_fingerprint import _SIM_PACKAGES
    env = compute_env()
    assert set(env["sim_packages"]) == set(_SIM_PACKAGES)


def test_compute_env_captures_declared_packages():
    # A workspace declares its own members (e.g. viva-pulse: viva_pulse +
    # protobuf, which the v2ecoli-shaped default list would miss). Capture is
    # driven by that list so the fingerprint covers the right packages.
    env = compute_env(packages=["process-bigraph", "protobuf", "viva_pulse"])
    assert set(env["sim_packages"]) == {"process-bigraph", "protobuf", "viva_pulse"}
    for info in env["sim_packages"].values():
        assert set(info) == {"version", "git_sha"}
