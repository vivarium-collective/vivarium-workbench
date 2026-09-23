"""Worker-side runtime module provisioning (env_worker._provision_modules)."""
import sys
import types

import pytest

from vivarium_workbench import env_worker


# ---- _pip_target_for_spec: spec -> pip install target ---------------------

def test_pip_target_pypi_mode():
    assert env_worker._pip_target_for_spec(
        {"name": "viva-munk", "mode": "pypi", "pypi_name": "viva-munk"}) == "viva-munk"


def test_pip_target_git_reference_with_ref():
    spec = {"name": "viva-tumor-tcell", "mode": "reference",
            "source": "https://github.com/vivarium-collective/viva-tumor-tcell.git",
            "ref": "main", "path": "external/viva-tumor-tcell"}
    assert env_worker._pip_target_for_spec(spec) == (
        "git+https://github.com/vivarium-collective/viva-tumor-tcell.git@main")


def test_pip_target_git_reference_without_ref():
    assert env_worker._pip_target_for_spec(
        {"source": "https://example.com/x.git"}) == "git+https://example.com/x.git"


def test_pip_target_pypi_name_without_mode():
    assert env_worker._pip_target_for_spec({"pypi_name": "numpy"}) == "numpy"


def test_pip_target_none_when_not_installable():
    assert env_worker._pip_target_for_spec({"name": "x", "mode": "reference"}) is None


# ---- _provision_modules: install + make importable ------------------------

def _fake_run(returncode=0, stderr=""):
    def run(cmd, **kw):
        return types.SimpleNamespace(returncode=returncode, stdout="", stderr=stderr)
    return run


def test_provision_installs_and_prepends_syspath(tmp_path, monkeypatch):
    calls = []
    def run(cmd, **kw):
        calls.append(cmd)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(env_worker.subprocess, "run", run)

    target = str(tmp_path / "site")
    res = env_worker._provision_modules(
        [{"name": "viva-munk", "mode": "pypi", "pypi_name": "viva-munk"}], target=target)

    assert res == [{"name": "viva-munk", "ok": True, "detail": "installed"}]
    # pip was invoked with --target and the pypi name
    assert calls and "--target" in calls[0] and "viva-munk" in calls[0]
    assert target in calls[0]
    # target is now importable (front of sys.path)
    assert sys.path[0] == target


def test_provision_reports_failure_without_raising(tmp_path, monkeypatch):
    monkeypatch.setattr(env_worker.subprocess, "run",
                        _fake_run(returncode=1, stderr="ERROR: Could not find a version"))
    target = str(tmp_path / "site")
    res = env_worker._provision_modules(
        [{"name": "nope", "mode": "pypi", "pypi_name": "nope"}], target=target)
    assert res[0]["ok"] is False
    assert "Could not find a version" in res[0]["detail"]
    # a pure-failure run must NOT prepend the target
    assert sys.path[0] != target


def test_provision_skips_non_installable(tmp_path, monkeypatch):
    monkeypatch.setattr(env_worker.subprocess, "run", _fake_run(returncode=0))
    res = env_worker._provision_modules(
        [{"name": "browse-only", "mode": "reference"}], target=str(tmp_path / "s"))
    assert res == [{"name": "browse-only", "ok": False, "detail": "no installable form (skipped)"}]


def test_provision_empty_is_noop():
    assert env_worker._provision_modules([]) == []


def test_provision_timeout_is_reported(tmp_path, monkeypatch):
    def run(cmd, **kw):
        raise env_worker.subprocess.TimeoutExpired(cmd, kw.get("timeout", 1))
    monkeypatch.setattr(env_worker.subprocess, "run", run)
    res = env_worker._provision_modules(
        [{"pypi_name": "slow", "name": "slow"}], target=str(tmp_path / "s"), timeout=5)
    assert res[0]["ok"] is False and "timed out" in res[0]["detail"]


def test_install_modules_rpc(tmp_path, monkeypatch):
    monkeypatch.setattr(env_worker.subprocess, "run", _fake_run(returncode=0))
    monkeypatch.setenv("VIVARIUM_ENV_WORKER_SITE", str(tmp_path / "site"))
    out = env_worker._handle("install_modules",
                             {"modules": [{"name": "viva-munk", "mode": "pypi",
                                           "pypi_name": "viva-munk"}]})
    assert out["ok"] is True
    assert out["results"][0] == {"name": "viva-munk", "ok": True, "detail": "installed"}


# ---- _provision_target: writable default for non-root single-pod ----------

def test_provision_target_honours_env_override(monkeypatch):
    monkeypatch.setenv("VIVARIUM_ENV_WORKER_SITE", "/some/explicit/dir")
    assert env_worker._provision_target() == "/some/explicit/dir"


def test_provision_target_default_is_writable_tempdir_when_no_scratch(monkeypatch):
    # Single-pod HeLx: env_worker runs as a non-root in-pod subprocess with no
    # /scratch emptyDir. The default must not point at an unwritable /scratch
    # (which raised PermissionError on Phil's cluster) — it falls back to a
    # user-writable temp dir.
    monkeypatch.delenv("VIVARIUM_ENV_WORKER_SITE", raising=False)
    monkeypatch.setattr(env_worker.os.path, "isdir",
                        lambda p: False if p == "/scratch" else os.path.isdir(p))
    target = env_worker._provision_target()
    import tempfile
    assert target.startswith(tempfile.gettempdir())
    assert "env-worker-site" in target


def test_provision_target_prefers_scratch_when_writable(monkeypatch):
    # Two-pod (Stanford) env-worker Job mounts a writable /scratch emptyDir —
    # keep using it there.
    monkeypatch.delenv("VIVARIUM_ENV_WORKER_SITE", raising=False)
    monkeypatch.setattr(env_worker.os.path, "isdir",
                        lambda p: True if p == "/scratch" else os.path.isdir(p))
    monkeypatch.setattr(env_worker.os, "access",
                        lambda p, m: True if p == "/scratch" else os.access(p, m))
    assert env_worker._provision_target() == "/scratch/env-worker-site"


# ---- _DISCOVERED reset: runtime installs become visible in a warm worker --

def test_install_modules_resets_discovery_gate(tmp_path, monkeypatch):
    # The single-pod warm-worker race: env_worker is long-lived and has already
    # run discovery (_DISCOVERED=True). A runtime Catalog install pushes
    # install_modules; without resetting the gate, _ensure_generators_discovered
    # early-returns forever and the freshly installed @composite_generators never
    # register. Pushing modules must re-arm discovery.
    monkeypatch.setattr(env_worker.subprocess, "run", _fake_run(returncode=0))
    monkeypatch.setenv("VIVARIUM_ENV_WORKER_SITE", str(tmp_path / "site"))
    monkeypatch.setattr(env_worker, "_DISCOVERED", True)
    env_worker._handle("install_modules",
                       {"modules": [{"name": "viva-munk", "mode": "pypi",
                                     "pypi_name": "viva-munk"}]})
    assert env_worker._DISCOVERED is False


def test_install_modules_empty_does_not_reset_discovery(monkeypatch):
    # No modules pushed → nothing changed → don't pay for a re-scan.
    monkeypatch.setattr(env_worker, "_DISCOVERED", True)
    env_worker._handle("install_modules", {"modules": []})
    assert env_worker._DISCOVERED is True
