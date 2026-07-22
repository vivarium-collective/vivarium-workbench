"""Slice 2: per-request workspace root (`_root` ContextVar).

`workspace_root()` prefers a request-scoped override over the process-global
default, so concurrent sessions on different workspaces don't collide — while
unset (serve-time, CLI, cookie-less clients) it falls back to the global exactly
as before.
"""
import threading

import pytest

from vivarium_workbench.lib import _root


@pytest.fixture(autouse=True)
def _restore_global():
    saved = _root.get_workspace_root()
    yield
    if saved is not None:
        _root.set_workspace_root(saved)


def test_request_root_overrides_global(tmp_path):
    g = tmp_path / "global"; g.mkdir()
    r = tmp_path / "request"; r.mkdir()
    _root.set_workspace_root(g)
    assert _root.workspace_root() == g.resolve()

    token = _root.set_request_workspace_root(r)
    try:
        assert _root.workspace_root() == r.resolve()      # per-request wins
    finally:
        _root.reset_request_workspace_root(token)
    assert _root.workspace_root() == g.resolve()          # falls back after reset


def test_get_workspace_root_returns_the_global_not_the_request(tmp_path):
    """The boot-time global accessor is unaffected by a per-request override."""
    g = tmp_path / "global"; g.mkdir()
    r = tmp_path / "request"; r.mkdir()
    _root.set_workspace_root(g)
    token = _root.set_request_workspace_root(r)
    try:
        assert _root.get_workspace_root() == g.resolve()
        assert _root.workspace_root() == r.resolve()
    finally:
        _root.reset_request_workspace_root(token)


def test_request_roots_are_isolated_across_concurrent_contexts(tmp_path):
    """Two threads each setting a different per-request root must not leak into
    each other — the property that makes concurrent multi-session safe."""
    _root.set_workspace_root(tmp_path / "global")
    (tmp_path / "global").mkdir()
    seen = {}
    barrier = threading.Barrier(2)

    def worker(name):
        wd = tmp_path / name
        wd.mkdir(exist_ok=True)
        tok = _root.set_request_workspace_root(wd)
        try:
            barrier.wait(timeout=5)          # both set before either reads
            seen[name] = _root.workspace_root()
        finally:
            _root.reset_request_workspace_root(tok)

    ts = [threading.Thread(target=worker, args=(n,)) for n in ("a", "b")]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    assert seen["a"] == (tmp_path / "a").resolve()
    assert seen["b"] == (tmp_path / "b").resolve()
