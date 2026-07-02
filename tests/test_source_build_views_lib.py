"""Tests for the pure sms-api source-build builders (``lib.source_build_views``).

These are NETWORK routes: every test monkeypatches the module-level sms-api
names (``SmsApiClient`` / ``list_build_sources`` / ``materialize_build``) with
fakes so nothing ever touches a real network.  The builders reproduce the
stdlib handlers' messages/status/field shapes byte-identically.
"""
from __future__ import annotations

import json

import pytest

from vivarium_workbench.lib import _root
from vivarium_workbench.lib import active_workspace
from vivarium_workbench.lib import source_build_views
from vivarium_workbench.lib.sms_api_client import SmsApiError


@pytest.fixture(autouse=True)
def _reset_root():
    saved = _root.get_workspace_root()
    _root._WS_ROOT = None
    yield
    _root._WS_ROOT = saved


# ---------------------------------------------------------------------------
# build_remote
# ---------------------------------------------------------------------------
class _FakeClient:
    """Fake SmsApiClient capturing the constructor base + canned responses."""

    last_base = None

    def __init__(self, base=None):
        type(self).last_base = base
        self._latest = {"git_commit_hash": "abcdef123"}
        self._reg = {"database_id": 77}

    def latest_simulator(self, repo, branch):
        self.latest_args = (repo, branch)
        return self._latest

    def register_simulator(self, repo, branch, commit):
        self.register_args = (repo, branch, commit)
        return self._reg


def test_build_remote_missing_repo_or_branch_400():
    body, status = source_build_views.build_remote({})
    assert status == 400
    assert body == {"error": "repo and branch are required"}

    body, status = source_build_views.build_remote({"repo": "x"})
    assert status == 400
    assert body == {"error": "repo and branch are required"}

    body, status = source_build_views.build_remote({"branch": "main"})
    assert status == 400
    assert body == {"error": "repo and branch are required"}


def test_build_remote_no_commit_502(monkeypatch):
    class _NoCommit(_FakeClient):
        def __init__(self, base=None):
            super().__init__(base)
            self._latest = {"git_commit_hash": ""}

    monkeypatch.setattr(source_build_views, "SmsApiClient", _NoCommit)
    body, status = source_build_views.build_remote({"repo": "r", "branch": "b"})
    assert status == 502
    assert body == {"error": "could not resolve branch HEAD via sms-api"}


def test_build_remote_sms_api_error_502(monkeypatch):
    class _Boom(_FakeClient):
        def latest_simulator(self, repo, branch):
            raise SmsApiError("kaboom")

    monkeypatch.setattr(source_build_views, "SmsApiClient", _Boom)
    body, status = source_build_views.build_remote({"repo": "r", "branch": "b"})
    assert status == 502
    assert body == {"error": "sms-api: kaboom"}


def test_build_remote_happy_normalizes_repo(monkeypatch):
    monkeypatch.setattr(source_build_views, "SmsApiClient", _FakeClient)
    body, status = source_build_views.build_remote(
        {"repo": "  https://github.com/x/y.git  ", "branch": "main"}
    )
    assert status == 200
    assert body == {
        "ok": True,
        "simulator_id": 77,
        "repo": "https://github.com/x/y",  # _normalize_repo_url stripped .git + whitespace
        "branch": "main",
        "commit": "abcdef123",
    }


# ---------------------------------------------------------------------------
# switch_build
# ---------------------------------------------------------------------------
def _build_entry(sim_id=5):
    return {
        "simulator_id": sim_id,
        "repo": "y",
        "repo_url": "https://github.com/x/y",
        "commit": "deadbeef",
        "branch": "main",
        "label": "y @ deadbeef (build #5)",
    }


def test_switch_build_missing_sim_id_400():
    body, status = source_build_views.switch_build({})
    assert status == 400
    assert body == {"error": "missing 'simulator_id'"}


def test_switch_build_listing_error_502(monkeypatch):
    monkeypatch.setattr(source_build_views, "SmsApiClient", lambda base=None: object())
    monkeypatch.setattr(
        source_build_views, "list_build_sources",
        lambda client: {"builds": [], "error": "tunnel down"},
    )
    body, status = source_build_views.switch_build({"simulator_id": 5})
    assert status == 502
    assert body == {"error": "sms-api unavailable: tunnel down"}


def test_switch_build_not_found_404(monkeypatch):
    monkeypatch.setattr(source_build_views, "SmsApiClient", lambda base=None: object())
    monkeypatch.setattr(
        source_build_views, "list_build_sources",
        lambda client: {"builds": [_build_entry(99)]},
    )
    body, status = source_build_views.switch_build({"simulator_id": 5})
    assert status == 404
    assert body == {"error": "build 5 not found"}


def test_switch_build_materialize_error_502(monkeypatch):
    monkeypatch.setattr(source_build_views, "SmsApiClient", lambda base=None: object())
    monkeypatch.setattr(
        source_build_views, "list_build_sources",
        lambda client: {"builds": [_build_entry(5)]},
    )

    def _boom(client, sim_id, commit):
        raise SmsApiError("no tarball")

    monkeypatch.setattr(source_build_views, "materialize_build", _boom)
    body, status = source_build_views.switch_build({"simulator_id": 5})
    assert status == 502
    assert body == {"error": "materialize failed: no tarball"}


def test_switch_build_happy_stamps_and_repoints(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(source_build_views, "SmsApiClient", lambda base=None: object())
    monkeypatch.setattr(
        source_build_views, "list_build_sources",
        lambda client: {"builds": [_build_entry(5)]},
    )
    materialize_args = {}

    def _materialize(client, sim_id, commit):
        materialize_args["call"] = (sim_id, commit)
        return cache

    monkeypatch.setattr(source_build_views, "materialize_build", _materialize)

    fired = []
    active_workspace.register_clear_cb(lambda: fired.append(True))

    body, status = source_build_views.switch_build({"simulator_id": 5})

    assert status == 200
    assert body == {"ok": True, "source": {"path": str(cache), "name": "y @ deadbeef (build #5)"}}
    # materialize was called with the entry's commit.
    assert materialize_args["call"] == (5, "deadbeef")
    # provenance stamp written.
    stamp = json.loads((cache / ".viv-build.json").read_text())
    assert stamp == {
        "simulator_id": 5, "repo": "y", "branch": "main",
        "commit": "deadbeef", "repo_url": "https://github.com/x/y",
    }
    # lib._root re-pointed to the resolved cache dir + caches invalidated.
    assert _root.get_workspace_root() == cache.resolve()
    assert fired, "switch_workspace must call active_workspace.invalidate()"


def test_switch_build_stamp_failure_is_swallowed(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(source_build_views, "SmsApiClient", lambda base=None: object())
    monkeypatch.setattr(
        source_build_views, "list_build_sources",
        lambda client: {"builds": [_build_entry(5)]},
    )
    monkeypatch.setattr(
        source_build_views, "materialize_build",
        lambda client, sim_id, commit: cache,
    )

    # Make the stamp write raise — the switch must still complete + return 200.
    orig_write_text = source_build_views.Path.write_text

    def _raise(self, *a, **k):
        if self.name == ".viv-build.json":
            raise OSError("disk full")
        return orig_write_text(self, *a, **k)

    monkeypatch.setattr(source_build_views.Path, "write_text", _raise)

    body, status = source_build_views.switch_build({"simulator_id": 5})

    assert status == 200
    assert body == {"ok": True, "source": {"path": str(cache), "name": "y @ deadbeef (build #5)"}}
    assert not (cache / ".viv-build.json").exists()
    assert _root.get_workspace_root() == cache.resolve()
