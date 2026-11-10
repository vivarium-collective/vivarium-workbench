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

    # Force the install path (Q1 would otherwise skip a module already
    # importable in this dev venv).
    monkeypatch.setattr(env_worker.importlib.util, "find_spec", lambda name: None)
    target = str(tmp_path / "site")
    res = env_worker._provision_modules(
        [{"name": "viva-munk", "mode": "pypi", "pypi_name": "viva-munk"}], target=target)

    assert res == [{"name": "viva-munk", "ok": True, "detail": "installed"}]
    # pip was invoked with --target and the pypi name
    assert calls and "--target" in calls[0] and "viva-munk" in calls[0]
    assert target in calls[0]
    # target is now importable (front of sys.path)
    assert sys.path[0] == target


def test_provision_install_uses_no_deps(tmp_path, monkeypatch):
    # Deps are already present in the pod's base venv (resolve from sys.path);
    # ``--no-deps`` keeps the fallback install cheap and, crucially, hang-proof
    # when a package depends on something that isn't on PyPI (e.g. a module that
    # requires the git-only ``vivarium-workbench``) — that otherwise burned the
    # whole timeout re-resolving on every pod start.
    calls = []
    monkeypatch.setattr(env_worker.subprocess, "run",
                        lambda cmd, **kw: calls.append(cmd) or types.SimpleNamespace(
                            returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(env_worker.importlib.util, "find_spec", lambda name: None)
    env_worker._provision_modules(
        [{"name": "viva-cpm", "mode": "reference",
          "source": "https://github.com/vivarium-collective/viva-cpm.git", "ref": "main",
          "package": "viva_cpm"}],  # import name doesn't match the real module -> skip misses
        target=str(tmp_path / "site"))
    assert calls and "--no-deps" in calls[0]


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
    monkeypatch.setattr(env_worker.importlib.util, "find_spec", lambda name: None)
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


# ---- Q1: skip reinstall of an already-importable module (single-pod) --------

def test_provision_skips_already_importable(tmp_path, monkeypatch):
    # On single-pod the worker runs the workspace venv the Catalog install
    # populated; a module already importable must NOT be reinstalled (that would
    # re-download / re-clone something already present).
    calls = []
    def run(cmd, **kw):
        calls.append(cmd)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(env_worker.subprocess, "run", run)
    monkeypatch.setattr(env_worker.importlib.util, "find_spec",
                        lambda name: object() if name == "viva_munk" else None)
    res = env_worker._provision_modules(
        [{"name": "viva-munk", "mode": "pypi", "pypi_name": "viva-munk", "package": "viva_munk"}],
        target=str(tmp_path / "site"))
    assert res == [{"name": "viva-munk", "ok": True, "detail": "already importable"}]
    assert calls == []  # no pip subprocess ran


def test_provision_installs_when_not_importable(tmp_path, monkeypatch):
    calls = []
    def run(cmd, **kw):
        calls.append(cmd)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(env_worker.subprocess, "run", run)
    monkeypatch.setattr(env_worker.importlib.util, "find_spec", lambda name: None)
    res = env_worker._provision_modules(
        [{"name": "nope", "mode": "pypi", "pypi_name": "nope", "package": "nope_pkg"}],
        target=str(tmp_path / "site"))
    assert res[0]["ok"] is True and res[0]["detail"] == "installed"
    assert calls and "--target" in calls[0]


def test_import_name_for_spec():
    assert env_worker._import_name_for_spec({"package": "viva_munk"}) == "viva_munk"
    assert env_worker._import_name_for_spec({"pypi_name": "viva-munk"}) == "viva_munk"
    assert env_worker._import_name_for_spec({"name": "spatio-flux"}) == "spatio_flux"
    assert env_worker._import_name_for_spec({}) is None


# ---- Q3: a persistent pip/uv download cache is passed to the install --------

def test_provision_passes_cache_env(tmp_path, monkeypatch):
    captured = {}
    def run(cmd, **kw):
        captured["env"] = kw.get("env")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(env_worker.subprocess, "run", run)
    monkeypatch.setattr(env_worker.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "uvc"))
    env_worker._provision_modules(
        [{"name": "x", "mode": "pypi", "pypi_name": "x", "package": "x_absent"}],
        target=str(tmp_path / "site"))
    assert captured["env"] is not None
    # Honors a deployment-set UV_CACHE_DIR and mirrors it to PIP_CACHE_DIR.
    assert captured["env"].get("PIP_CACHE_DIR") == str(tmp_path / "uvc")


# ---- #1575: provisioning must work against a REAL uv-created venv -----------
# uv-created venvs do NOT ship ``pip``; the old code ran ``<py> -m pip install``,
# which failed for *every* declared module with ``No module named pip`` (so the
# pool logged "N module(s) failed" at startup). The fix: skip modules already
# importable in the worker interpreter, and when an install IS needed, use
# ``uv pip install --python <interpreter>`` rather than the venv's absent pip.
# These tests drive a real uv venv (not a fake interpreter), per the issue.

import os  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import base64  # noqa: E402
import hashlib  # noqa: E402
import zipfile  # noqa: E402


_UV = shutil.which("uv")
requires_uv = pytest.mark.skipif(_UV is None, reason="uv not on PATH")


def _make_real_uv_venv(tmp_path):
    """Create a genuine uv venv and return the path to its interpreter.

    A real uv venv is the point of this regression: it has NO ``pip``, which is
    exactly what broke provisioning in #1575."""
    venv = tmp_path / "uvenv"
    subprocess.run([_UV, "venv", str(venv)], check=True, capture_output=True)
    py = venv / "bin" / "python"
    if not py.exists():  # Windows layout (CI is Linux/macOS, but be safe)
        py = venv / "Scripts" / "python.exe"
    return str(py)


def _make_minimal_wheel(dir_path, name="probe1575", version="0.0.1"):
    """A hand-built pure-python wheel so the install is genuine but fully offline
    (no build backend, no network) — installing a ``.whl`` needs no pip."""
    whl = os.path.join(dir_path, f"{name}-{version}-py3-none-any.whl")
    files = {
        f"{name}/__init__.py": b"VALUE = 1575\n",
        f"{name}-{version}.dist-info/METADATA":
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n".encode(),
        f"{name}-{version}.dist-info/WHEEL":
            b"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    record = []
    for arc, data in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        record.append(f"{arc},sha256={digest},{len(data)}")
    record.append(f"{name}-{version}.dist-info/RECORD,,")
    files[f"{name}-{version}.dist-info/RECORD"] = ("\n".join(record) + "\n").encode()
    with zipfile.ZipFile(whl, "w", zipfile.ZIP_DEFLATED) as z:
        for arc, data in files.items():
            z.writestr(arc, data)
    return whl


@requires_uv
def test_real_uv_venv_has_no_pip(tmp_path):
    # Guards the premise of #1575: a uv venv cannot run ``python -m pip``.
    py = _make_real_uv_venv(tmp_path)
    proc = subprocess.run([py, "-m", "pip", "--version"], capture_output=True, text=True)
    assert proc.returncode != 0
    assert "No module named pip" in (proc.stderr + proc.stdout)


@requires_uv
def test_install_command_uses_uv_when_interpreter_has_no_pip(tmp_path):
    # The crux: for a pip-less uv venv the install command must be the uv form
    # (``uv pip install --python <interpreter>``), NOT ``<py> -m pip`` (#1575).
    py = _make_real_uv_venv(tmp_path)
    cmd = env_worker._install_command("some-pkg", str(tmp_path / "site"), py)
    assert cmd is not None
    assert cmd[0] == _UV and cmd[1:3] == ["pip", "install"]
    assert "--python" in cmd and cmd[cmd.index("--python") + 1] == py
    assert "--target" in cmd and "some-pkg" in cmd
    # Never the broken "<py> -m pip" that failed on uv venvs.
    assert not (cmd[0] == py and "pip" in cmd and "-m" in cmd)


@requires_uv
def test_provision_skips_importable_module_in_real_uv_venv(tmp_path):
    # (a) A module already importable in the worker interpreter is SKIPPED — no
    # install subprocess is spawned. ``json`` is in every uv venv's stdlib.
    py = _make_real_uv_venv(tmp_path)
    res = env_worker._provision_modules(
        [{"name": "json", "mode": "pypi", "pypi_name": "json", "package": "json"}],
        target=str(tmp_path / "site"), interpreter=py)
    assert res == [{"name": "json", "ok": True, "detail": "already importable"}]


@requires_uv
def test_provision_installs_missing_module_via_uv_in_real_uv_venv(tmp_path):
    # (b) A module NOT importable in the pip-less uv venv is genuinely installed
    # via ``uv pip install --python``. On the OLD code this returned
    # ``No module named pip``; now it succeeds and lands on disk.
    py = _make_real_uv_venv(tmp_path)
    wheel = _make_minimal_wheel(str(tmp_path))
    target = tmp_path / "site"
    res = env_worker._provision_modules(
        [{"name": "probe1575", "mode": "pypi",
          "pypi_name": str(wheel),       # local .whl path -> installable, offline
          "package": "probe1575"}],      # not importable anywhere -> forces install
        target=str(target), interpreter=py)
    assert res[0]["ok"] is True, res
    assert res[0]["detail"] == "installed"
    assert (target / "probe1575" / "__init__.py").exists()
