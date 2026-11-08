"""Named environment declarations for reproducibility.

A workspace declares named environments in ``environments.yaml`` (pins +
members, in the shape of compose-api's ``environments.yaml``). A composite or
study condition references one by name (``environment_ref:``); a run captures
the actual environment it executed in (``env_fingerprint.compute_env``) and
``verify()`` reports whether the capture satisfies the declaration, so drift is
surfaced instead of silently shipping an unreproducible run.
"""
import pytest

from vivarium_workbench.lib.environments import (
    load_environments,
    resolve_environment,
    verify,
)

PULSE_YAML = """\
default: pulse
environments:
  pulse:
    python: "3.12"
    core:
      process-bigraph: ">=1.10.1"
      bigraph-schema: ">=1.8.3"
    packages:
      viva_pulse:
        install: git
        commit: abc1230000000000000000000000000000000000
      protobuf: "==7.35.0"
    native:
      - "Kitware Pulse engine (built from source)"
    notes: "Pulse is built from source; protobuf must match the build."
"""


def _write(ws, text):
    (ws / "environments.yaml").write_text(text, encoding="utf-8")


# ─── load_environments ───────────────────────────────────────────────────────

def test_load_absent_returns_empty(tmp_path):
    assert load_environments(tmp_path) == {}


def test_load_reads_named_envs_with_name_injected(tmp_path):
    _write(tmp_path, PULSE_YAML)
    envs = load_environments(tmp_path)
    assert set(envs) == {"pulse"}
    pulse = envs["pulse"]
    assert pulse["name"] == "pulse"
    assert pulse["core"]["process-bigraph"] == ">=1.10.1"
    assert pulse["packages"]["protobuf"] == "==7.35.0"


def test_load_malformed_top_level_raises(tmp_path):
    _write(tmp_path, "environments: [not, a, mapping]\n")
    with pytest.raises(ValueError):
        load_environments(tmp_path)


def test_load_malformed_entry_raises(tmp_path):
    _write(tmp_path, "environments:\n  pulse: just-a-string\n")
    with pytest.raises(ValueError):
        load_environments(tmp_path)


# ─── resolve_environment ─────────────────────────────────────────────────────

def test_resolve_by_ref(tmp_path):
    _write(tmp_path, PULSE_YAML)
    env = resolve_environment(tmp_path, environment_ref="pulse")
    assert env["name"] == "pulse"


def test_resolve_falls_back_to_default_when_ref_absent(tmp_path):
    _write(tmp_path, PULSE_YAML)
    env = resolve_environment(tmp_path, environment_ref=None)
    assert env["name"] == "pulse"  # via `default: pulse`


def test_resolve_unknown_ref_raises(tmp_path):
    # A declared-but-missing environment is the silent-failure class this
    # codebase retires: a user who named an env believes the run is pinned.
    _write(tmp_path, PULSE_YAML)
    with pytest.raises(ValueError):
        resolve_environment(tmp_path, environment_ref="nope")


def test_resolve_none_when_no_file_and_no_ref(tmp_path):
    assert resolve_environment(tmp_path, environment_ref=None) is None


# ─── verify ──────────────────────────────────────────────────────────────────

def _captured(**versions):
    """A compute_env()-shaped capture with the given package versions."""
    sim = {name: {"version": v, "git_sha": sha}
           for name, (v, sha) in versions.items()}
    return {"sim_packages": sim, "python": "3.12.12", "platform": "mac"}


def test_verify_match(tmp_path):
    _write(tmp_path, PULSE_YAML)
    declared = resolve_environment(tmp_path, environment_ref="pulse")
    captured = _captured(
        **{"process-bigraph": ("1.10.1", None),
           "bigraph-schema": ("1.8.3", None),
           "protobuf": ("7.35.0", None),
           "viva_pulse": ("0.1.0", "abc1230000000000000000000000000000000000")},
    )
    result = verify(declared, captured)
    assert result["matches"] is True
    assert result["drift"] == []


def test_verify_drift_on_version(tmp_path):
    _write(tmp_path, PULSE_YAML)
    declared = resolve_environment(tmp_path, environment_ref="pulse")
    captured = _captured(
        **{"process-bigraph": ("1.9.1", None),   # violates >=1.10.1
           "bigraph-schema": ("1.8.3", None),
           "protobuf": ("7.35.0", None),
           "viva_pulse": ("0.1.0", "abc1230000000000000000000000000000000000")},
    )
    result = verify(declared, captured)
    assert result["matches"] is False
    pkgs = {d["package"] for d in result["drift"]}
    assert "process-bigraph" in pkgs


def test_verify_drift_on_missing_capture(tmp_path):
    _write(tmp_path, PULSE_YAML)
    declared = resolve_environment(tmp_path, environment_ref="pulse")
    captured = _captured(**{"process-bigraph": ("1.10.1", None)})  # others absent
    result = verify(declared, captured)
    assert result["matches"] is False
    missing = {d["package"]: d for d in result["drift"]}
    assert missing["protobuf"]["captured"] is None


def test_verify_git_commit_pin(tmp_path):
    _write(tmp_path, PULSE_YAML)
    declared = resolve_environment(tmp_path, environment_ref="pulse")
    captured = _captured(
        **{"process-bigraph": ("1.10.1", None),
           "bigraph-schema": ("1.8.3", None),
           "protobuf": ("7.35.0", None),
           "viva_pulse": ("0.1.0", "deadbeef00000000000000000000000000000000")},  # wrong sha
    )
    result = verify(declared, captured)
    pkgs = {d["package"] for d in result["drift"]}
    assert "viva_pulse" in pkgs
