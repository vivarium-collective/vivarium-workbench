"""Managed environment materialization (materialization-lifecycle.md §9b): the
coordinate-keyed venv store + synchronous ``uv sync`` primitive.

The store is redirected to a tmp dir via ``VIVARIUM_WORKBENCH_VENV_STORE`` so no
test touches a real user cache. The offline tests fabricate a venv layout to
exercise the cache/coordinate logic without running ``uv``; one opt-in test runs
a real ``uv sync`` of a minimal no-dep project (skipped when ``uv`` is absent).
"""
import base64
import http.server
import os
import shutil
import subprocess
import sys
import threading
import urllib.error
from pathlib import Path

import pytest

from vivarium_workbench.lib import env_resolver, github_auth, materialization as m


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Redirect the venv store to a tmp dir."""
    d = tmp_path / "venv-store"
    monkeypatch.setenv("VIVARIUM_WORKBENCH_VENV_STORE", str(d))
    return d


def _make_project(root: Path, *, lock: str = "lock-a") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        "[project]\nname = 'p'\nversion = '0'\nrequires-python = '>=3.11'\n")
    (root / "uv.lock").write_text(f"# {lock}\n")
    return root


def _fake_venv(venv_dir: Path, *, complete: bool = True) -> Path:
    """A minimal ``<venv>/bin/python``; ``complete`` also writes the completion
    marker so ``cached_interpreter`` treats it as a finished (cacheable) venv."""
    b = venv_dir / "bin"
    b.mkdir(parents=True, exist_ok=True)
    py = b / "python"
    py.write_text("#!/bin/sh\n")
    py.chmod(0o755)
    if complete:
        (venv_dir / m._MARKER).write_text("ok\n")
    return py


# -- coordinate --------------------------------------------------------------
def test_coordinate_is_deterministic(tmp_path):
    src = _make_project(tmp_path / "ws")
    assert m.environment_coordinate(src) == m.environment_coordinate(src)


def test_coordinate_changes_with_lock_content(tmp_path):
    a = _make_project(tmp_path / "a", lock="lock-a")
    b = _make_project(tmp_path / "b", lock="lock-b")
    assert m.environment_coordinate(a) != m.environment_coordinate(b)


def test_coordinate_is_source_scoped(tmp_path):
    """Same lock content, different source paths → different coordinates (no false
    venv sharing between checkouts with editable path-dep locks)."""
    a = _make_project(tmp_path / "a", lock="same")
    b = _make_project(tmp_path / "b", lock="same")
    assert m.environment_coordinate(a) != m.environment_coordinate(b)


# -- cache lookups -----------------------------------------------------------
def test_cached_interpreter_finds_a_present_venv(store, tmp_path):
    coord = "abc123"
    py = _fake_venv(store / coord)
    assert m.cached_interpreter(coord) == str(py)


def test_cached_interpreter_absent_is_none(store):
    assert m.cached_interpreter("nope") is None


def test_partial_venv_without_marker_is_not_cached(store, tmp_path):
    """An interrupted `uv sync` leaves a bin/python but no completion marker — it
    must NOT be treated as cached (it would be a broken interpreter)."""
    coord = "partial123"
    _fake_venv(store / coord, complete=False)
    assert m.cached_interpreter(coord) is None            # re-sync, don't serve it


def test_cached_interpreter_for_none_without_pyproject(store, tmp_path):
    bare = tmp_path / "bare"
    bare.mkdir()
    assert m.cached_interpreter_for(bare) is None


def test_cached_interpreter_for_hits_the_store(store, tmp_path):
    src = _make_project(tmp_path / "ws")
    _fake_venv(store / m.environment_coordinate(src))
    got = m.cached_interpreter_for(src)
    assert got is not None and got.endswith("/bin/python")


# -- materialize (offline paths) ---------------------------------------------
def test_materialize_no_pyproject_is_sys_executable(store, tmp_path):
    bare = tmp_path / "bare"
    bare.mkdir()
    assert m.materialize(bare) == sys.executable


def test_materialize_cache_hit_skips_uv(store, tmp_path, monkeypatch):
    src = _make_project(tmp_path / "ws")
    _fake_venv(store / m.environment_coordinate(src))

    def _boom(*a, **k):  # uv must NOT run on a cache hit
        raise AssertionError("uv sync should not run on a cache hit")

    monkeypatch.setattr(subprocess, "run", _boom)
    got = m.materialize(src)
    assert got.endswith("/bin/python")


# -- resolver integration (behavior-preserving) ------------------------------
def test_resolver_prefers_in_place_venv(tmp_path):
    ws = _make_project(tmp_path / "ws")
    inplace = _fake_venv(ws / ".venv")
    assert env_resolver.resolve_interpreter(ws) == str(inplace)


def test_resolver_falls_back_to_sys_executable(store, tmp_path):
    """No in-place .venv and no managed venv → running interpreter (today's
    behavior)."""
    ws = _make_project(tmp_path / "ws")
    assert env_resolver.resolve_interpreter(ws) == sys.executable


def test_resolver_uses_managed_venv_when_present(store, tmp_path):
    ws = _make_project(tmp_path / "ws")  # no in-place .venv
    managed = _fake_venv(store / m.environment_coordinate(ws))
    assert env_resolver.resolve_interpreter(ws) == str(managed)


# -- real uv sync (opt-in: needs uv; a tiny no-dep project, ~1s) --------------
@pytest.mark.skipif(not __import__("shutil").which("uv"), reason="uv not on PATH")
def test_materialize_runs_real_uv_sync(store, tmp_path, monkeypatch):
    """End-to-end: materialize a minimal project → a real venv with an
    interpreter; a second call is a cache hit (same path)."""
    # Pin uv to the running interpreter so it need not download a managed CPython
    # (materialize inherits os.environ). No deps → the sync is ~1s and offline.
    monkeypatch.setenv("UV_PYTHON", sys.executable)
    # Not under test: GitHub session discovery shells out to `gh` (ambient on a laptop or CI).
    monkeypatch.setattr(github_auth, "current_session", lambda: None)
    src = tmp_path / "proj"
    src.mkdir()
    (src / "pyproject.toml").write_text(
        "[project]\nname = 'tinyproj'\nversion = '0'\nrequires-python = '>=3.11'\n")
    # A managed workspace ships a uv.lock; generate one so the coordinate is
    # lock-stable across materialize calls (idempotence).
    lock = subprocess.run(["uv", "lock"], cwd=str(src), capture_output=True, text=True)
    if lock.returncode != 0:
        pytest.skip(f"uv lock unavailable: {lock.stderr[:200]}")
    assert m.cached_interpreter_for(src) is None          # nothing yet
    try:
        interp = m.materialize(src, timeout=180)
    except m.MaterializationError as e:
        pytest.skip(f"uv sync unavailable in this env: {e} {e.tail[:200]}")
    assert Path(interp).is_file()
    assert str(store) in interp                           # built into the store
    assert m.cached_interpreter_for(src) == interp        # now a cache hit
    assert m.materialize(src, timeout=180) == interp      # reuses, no rebuild


# -- private git dependencies: the GitHub session must reach the subprocess's git --
#
# A managed workspace can pin a PRIVATE git dependency (sms-ecoli pins the private
# vivarium-collective/v2ecoli). The workbench container has no git credential helper and
# `uv` runs git with terminal prompts disabled, so unless `materialize` hands the subprocess
# the signed-in session's token the sync dies with "could not read Username for
# 'https://github.com'" and the UI shows "Workspace failed to prepare".
#
# The oracle is REAL git: a stand-in `uv` (the periphery: it only has to leave an interpreter
# behind) asks `git config --get-urlmatch http.extraHeader <url>` which header git would send
# to each URL, given the environment `materialize` handed it. Proven live, not here: GitHub
# accepts `x-access-token:<token>` Basic auth for a private fetch (uv 0.11.26 and 0.13.7, real
# sms-ecoli lock, exit 1 without it, exit 0 with it).
_GIT_PROBES = {
    "private_dep": "https://github.com/vivarium-collective/v2ecoli.git",
    "pypi": "https://pypi.org/simple/",
    "lookalike_host": "https://github.com.evil.example/vivarium-collective/v2ecoli.git",
    "cleartext": "http://github.com/vivarium-collective/v2ecoli.git",
}
_needs_posix_git = pytest.mark.skipif(
    sys.platform == "win32" or not shutil.which("git"), reason="needs a POSIX shell and git")
_TOKEN = "ghp_" + "a1B2c3D4" * 5


def _materialize_under_probing_uv(tmp_path, monkeypatch) -> dict:
    """Run ``materialize`` with a stand-in ``uv`` first on PATH; return git's answers."""
    out = tmp_path / "git-answers"
    script = ["#!/bin/sh", "set -e",
              'mkdir -p "$UV_PROJECT_ENVIRONMENT/bin"', ': > "$UV_PROJECT_ENVIRONMENT/bin/python"']
    for name, url in _GIT_PROBES.items():
        script.append(
            f'printf "{name}=%s\\n" "$(git config --get-urlmatch http.extraHeader {url} || true)" >> "{out}"')
    script.append(f'printf "kept=%s\\n" "$(git config --get vwb.kept || true)" >> "{out}"')
    script.append(f'printf "count=%s\\n" "${{GIT_CONFIG_COUNT-unset}}" >> "{out}"')
    bindir = tmp_path / "bin"
    bindir.mkdir()
    uv = bindir / "uv"
    uv.write_text("\n".join(script) + "\n")
    uv.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    # Only what materialize hands the subprocess may influence git's answer.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    m.materialize(_make_project(tmp_path / "proj"), timeout=60)
    return dict(line.partition("=")[::2] for line in out.read_text().splitlines())


def _signed_in(monkeypatch):
    monkeypatch.setattr(github_auth, "_CACHED_SESSION",
                        github_auth.Session(login="octocat", token=_TOKEN, source="device_flow"))


@_needs_posix_git
def test_materialize_hands_the_subprocess_the_session_token_for_github_only(store, tmp_path, monkeypatch):
    _signed_in(monkeypatch)
    for var in ("GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0"):
        monkeypatch.delenv(var, raising=False)
    answers = _materialize_under_probing_uv(tmp_path, monkeypatch)
    basic = base64.b64encode(f"x-access-token:{_TOKEN}".encode()).decode()
    assert answers["private_dep"] == f"AUTHORIZATION: basic {basic}"
    # The token goes to https://github.com/ and nowhere else.
    assert answers["pypi"] == answers["lookalike_host"] == answers["cleartext"] == ""


@_needs_posix_git
def test_materialize_keeps_git_config_the_operator_already_set(store, tmp_path, monkeypatch):
    _signed_in(monkeypatch)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "vwb.kept")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "yes")
    answers = _materialize_under_probing_uv(tmp_path, monkeypatch)
    assert answers["kept"] == "yes"                       # not overwritten
    assert answers["private_dep"].startswith("AUTHORIZATION: basic ")


@_needs_posix_git
def test_materialize_without_a_session_adds_no_git_credentials(store, tmp_path, monkeypatch):
    monkeypatch.setattr(github_auth, "current_session", lambda: None)
    for var in ("GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0"):
        monkeypatch.delenv(var, raising=False)
    answers = _materialize_under_probing_uv(tmp_path, monkeypatch)
    assert answers["private_dep"] == ""
    assert answers["count"] == "unset"


@_needs_posix_git
def test_materialize_builds_without_credentials_when_the_session_lookup_fails(store, tmp_path, monkeypatch):
    """A build with no private dependency needs no credential: an unreachable GitHub (a
    machine token is validated against the API), keyring or `gh` must not fail it."""
    def _unreachable():
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(github_auth, "current_session", _unreachable)
    for var in ("GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0"):
        monkeypatch.delenv(var, raising=False)
    answers = _materialize_under_probing_uv(tmp_path, monkeypatch)   # must not raise
    assert answers["private_dep"] == ""


@_needs_posix_git
def test_materialize_failure_tail_never_carries_the_credential(store, tmp_path, monkeypatch):
    """The tail goes to the browser. A tool that echoes the environment git was given
    (GIT_TRACE_REDACT=0, a verbose wrapper) must not leak the header."""
    _signed_in(monkeypatch)
    for var in ("GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0"):
        monkeypatch.delenv(var, raising=False)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    uv = bindir / "uv"
    uv.write_text('#!/bin/sh\necho "trace: $GIT_CONFIG_KEY_0=$GIT_CONFIG_VALUE_0" >&2\nexit 1\n')
    uv.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    with pytest.raises(m.MaterializationError) as exc:
        m.materialize(_make_project(tmp_path / "proj"), timeout=60)
    basic = base64.b64encode(f"x-access-token:{_TOKEN}".encode()).decode()
    assert basic not in exc.value.tail
    assert "trace: http.https://github.com/.extraHeader=<redacted>" in exc.value.tail



# The toast headlines `str(MaterializationError)` and puts the uv tail in a scroll box, where the
# cause sits under ~40 "Downloading ..." lines. git prints the SAME line whether no credential
# reached it or GitHub answered 401 to the one it got (real GitHub, probed 2026-11-12: no header, and
# a bogus-token header), so the message is chosen from what `materialize` knows: did it hand git a
# credential at all. The two github.com cases use a stand-in `uv` (the periphery) that prints that
# line verbatim, as captured from real git against github.com and from the field log: real git cannot
# produce the github.com wording hermetically, because it prints the host it actually connects to. The
# host control below therefore uses REAL git against a loopback server.
_GITHUB_LINE = "fatal: could not read Username for 'https://github.com': terminal prompts disabled"
_NOISE = 'echo "Downloading torch (502.2MiB)" >&2; echo "Downloading pandas (11.8MiB)" >&2'


class _Always401(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="x"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format, *args):
        pass


@pytest.fixture
def private_git_url():
    server = http.server.HTTPServer(("127.0.0.1", 0), _Always401)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/org/private.git"
    server.shutdown()
    server.server_close()


def _failing_materialize(tmp_path, monkeypatch, uv_body: str) -> m.MaterializationError:
    """``materialize`` with a stand-in ``uv`` that runs ``uv_body`` and fails."""
    for var in ("GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0", "GIT_CONFIG_PARAMETERS",
                "GIT_ASKPASS", "SSH_ASKPASS", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")      # what uv sets for the git it runs
    bindir = tmp_path / "bin"
    bindir.mkdir()
    uv = bindir / "uv"
    uv.write_text(f"#!/bin/sh\n{uv_body}\nexit 1\n")
    uv.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    with pytest.raises(m.MaterializationError) as exc:
        m.materialize(_make_project(tmp_path / "proj"), timeout=60)
    return exc.value


_GITHUB_FAILURE = f'{_NOISE}; echo "{_GITHUB_LINE}" >&2'


@_needs_posix_git
def test_a_missing_github_credential_is_named_in_the_error(store, tmp_path, monkeypatch):
    monkeypatch.setattr(github_auth, "current_session", lambda: None)
    err = _failing_materialize(tmp_path, monkeypatch, _GITHUB_FAILURE)
    assert _GITHUB_LINE in err.tail                         # git's own words stay for the operator
    assert str(err).startswith("environment build failed")
    assert "no GitHub sign-in or token" in str(err)
    assert "VIVARIUM_WORKBENCH_GH_TOKEN" in str(err)


@_needs_posix_git
def test_a_refused_github_credential_is_named_in_the_error(store, tmp_path, monkeypatch):
    """A session exists, so git was handed a header; the same line then means GitHub said 401."""
    _signed_in(monkeypatch)
    err = _failing_materialize(tmp_path, monkeypatch, _GITHUB_FAILURE)
    assert _GITHUB_LINE in err.tail
    assert str(err).startswith("environment build failed")
    assert "refused the workbench's credential" in str(err)
    assert "expired, revoked or invalid" in str(err)
    assert "no GitHub sign-in or token" not in str(err)     # it HAD one: do not tell the operator to add one
    assert _TOKEN not in str(err) and _TOKEN not in err.tail


@_needs_posix_git
@pytest.mark.parametrize("signed_in", [True, False])
def test_another_hosts_credential_prompt_is_not_blamed_on_github(store, tmp_path, monkeypatch, private_git_url, signed_in):
    """Control, with REAL git: a private dependency on a host that is not github.com asks for a
    username too, but our credential is scoped to github.com, so the headline must stay plain."""
    if signed_in:
        _signed_in(monkeypatch)
    else:
        monkeypatch.setattr(github_auth, "current_session", lambda: None)
    monkeypatch.setenv("VWB_TEST_GIT_URL", private_git_url)
    err = _failing_materialize(tmp_path, monkeypatch, 'git ls-remote "$VWB_TEST_GIT_URL" >&2')
    assert "could not read Username for 'http://127.0.0.1" in err.tail   # real git asked for a credential
    assert str(err) == "environment build failed"


@_needs_posix_git
@pytest.mark.parametrize("signed_in", [True, False])
def test_an_unrelated_build_failure_keeps_the_plain_message(store, tmp_path, monkeypatch, signed_in):
    """Control: a failure that is not git asking for a credential must not be blamed on one."""
    if signed_in:
        _signed_in(monkeypatch)
    else:
        monkeypatch.setattr(github_auth, "current_session", lambda: None)
    err = _failing_materialize(tmp_path, monkeypatch, 'echo "No solution found when resolving dependencies" >&2')
    assert str(err) == "environment build failed"
    assert "No solution found" in err.tail
