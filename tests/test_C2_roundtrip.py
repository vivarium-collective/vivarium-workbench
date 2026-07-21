"""C2: Roundtrip test for export_composite_pbg against the fixture workspace."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

FIXTURE_WS = Path(__file__).parent / "_fixtures" / "ws_increase_demo"
# Fully-qualified composite id expected by find_composite_path: <pkg>.composites.<stem>
COMPOSITE_ID = "pbg_ws_increase_demo.composites.increase-demo"


@pytest.fixture(autouse=True)
def _ws_on_path():
    """Make the fixture workspace package importable for the duration of the test."""
    ws = str(FIXTURE_WS)
    inserted = ws not in sys.path
    if inserted:
        sys.path.insert(0, ws)
    yield
    if inserted:
        try:
            sys.path.remove(ws)
        except ValueError:
            pass


def test_export_composite_pbg_creates_file(tmp_path):
    """export_composite_pbg writes a .pbg JSON file."""
    from vivarium_workbench.lib.pbg_export import export_composite_pbg

    out = tmp_path / "increase-demo.pbg"
    result = export_composite_pbg(FIXTURE_WS, COMPOSITE_ID, out)
    assert result == out
    assert out.is_file()


def test_exported_json_has_state_and_schema(tmp_path):
    """The exported .pbg must have top-level 'state' and 'schema' keys."""
    from vivarium_workbench.lib.pbg_export import export_composite_pbg

    out = tmp_path / "increase-demo.pbg"
    export_composite_pbg(FIXTURE_WS, COMPOSITE_ID, out)
    doc = json.loads(out.read_text())
    assert "state" in doc
    assert "schema" in doc


def test_all_local_addresses_are_full_path(tmp_path):
    """Every local: address in the exported document must be in local:!module.qualname form."""
    from vivarium_workbench.lib.pbg_export import export_composite_pbg

    out = tmp_path / "increase-demo.pbg"
    export_composite_pbg(FIXTURE_WS, COMPOSITE_ID, out)
    doc = json.loads(out.read_text())

    short_addresses = _collect_short_local_addresses(doc)
    assert short_addresses == [], (
        f"Found non-full-path local: addresses: {short_addresses}"
    )


def test_addresses_use_full_module_path(tmp_path):
    """Exported addresses should contain the workspace module path."""
    from vivarium_workbench.lib.pbg_export import export_composite_pbg

    out = tmp_path / "increase-demo.pbg"
    export_composite_pbg(FIXTURE_WS, COMPOSITE_ID, out)
    doc = json.loads(out.read_text())

    all_addresses = _collect_all_addresses(doc)
    # At least some addresses should reference pbg_ws_increase_demo package
    full_path_addrs = [a for a in all_addresses if a.startswith("local:!")]
    assert len(full_path_addrs) > 0, "Expected at least one full-path address"
    # Verify the workspace processes are properly encoded
    ws_addrs = [a for a in full_path_addrs if "pbg_ws_increase_demo" in a]
    assert len(ws_addrs) > 0, (
        f"Expected addresses containing 'pbg_ws_increase_demo', got: {full_path_addrs}"
    )


def test_exported_document_is_valid_json(tmp_path):
    """The exported file must be valid, parseable JSON."""
    from vivarium_workbench.lib.pbg_export import export_composite_pbg

    out = tmp_path / "exported.pbg"
    export_composite_pbg(FIXTURE_WS, COMPOSITE_ID, out)
    # Should not raise
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert isinstance(doc, dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _collect_short_local_addresses(node: object) -> list[str]:
    """Collect any local:<Name> (non-full-path) addresses in the document tree."""
    found: list[str] = []
    _scan(node, found)
    return found


def _scan(node: object, found: list[str]) -> None:
    if not isinstance(node, dict):
        return
    if "address" in node:
        addr = node["address"]
        if isinstance(addr, str) and addr.startswith("local:") and not addr.startswith("local:!"):
            found.append(addr)
    for v in node.values():
        if isinstance(v, dict):
            _scan(v, found)
        elif isinstance(v, list):
            for item in v:
                _scan(item, found)


def _collect_all_addresses(node: object) -> list[str]:
    """Collect all addresses (any protocol) in the document tree."""
    found: list[str] = []
    _scan_all(node, found)
    return found


def _scan_all(node: object, found: list[str]) -> None:
    if not isinstance(node, dict):
        return
    if "address" in node and isinstance(node["address"], str):
        found.append(node["address"])
    for v in node.values():
        if isinstance(v, dict):
            _scan_all(v, found)
        elif isinstance(v, list):
            for item in v:
                _scan_all(item, found)


# ---------------------------------------------------------------------------
# Item C: run_remote clamps n_steps to the sms-api compose contract (0..1000).
# sms-api rejects interval_time outside 0..1000 with a 400 (compose.py:121-122);
# interval_time IS the step channel, so run_remote must clamp before submitting.
# ---------------------------------------------------------------------------

class _CaptureClient:
    """Fake SmsApiClient capturing the interval_time (== step count) and the
    extra_pip_deps it's given."""

    def __init__(self):
        self.interval_time = None
        self.extra_pip_deps = None

    def compose_submit(self, pbg_bytes, *, extra_pip_deps=None, interval_time=None):
        self.interval_time = interval_time
        self.extra_pip_deps = extra_pip_deps
        return 123

    def compose_status(self, sim_id):
        return {"status": "completed"}

    def download_compose_results(self, sim_id, dest):
        p = Path(dest) / "results.zip"
        p.write_bytes(b"")
        return p


def _stub_remote_boundaries(monkeypatch):
    """Mock the git/export boundaries so run_remote's clamp is unit-testable."""
    from vivarium_workbench.lib import remote_run
    monkeypatch.setattr(remote_run, "git_pip_url", lambda ws: "git+file:///x@abc1234")
    monkeypatch.setattr(remote_run, "workspace_pinned_deps", lambda ws: [])
    monkeypatch.setattr(
        remote_run, "export_composite_pbg",
        lambda ws, cid, path: Path(path).write_bytes(b"{}"))


@pytest.mark.parametrize("n_steps,expected", [
    (5000, 1000.0),   # over the ceiling → clamped to 1000
    (2700, 1000.0),   # the default_n_steps that would 400 → clamped
    (20, 20.0),       # valid → passed through unchanged
    (-3, 0.0),        # below the floor → clamped to 0
])
def test_run_remote_clamps_steps(tmp_path, monkeypatch, n_steps, expected):
    from vivarium_workbench.lib import remote_run
    _stub_remote_boundaries(monkeypatch)
    client = _CaptureClient()
    remote_run.run_remote(
        tmp_path, "some.composite", client=client,
        poll_interval=0, dest=tmp_path, n_steps=n_steps)
    assert client.interval_time == expected


# ---------------------------------------------------------------------------
# N3 / option C: run_remote's pip-URL derivation.
# On a pinned deployment the prod pod's /workspace is dirty-by-design, so run_remote
# must NOT call git_pip_url (it raises on a dirty tree). Instead it derives the commit
# from sms-api's resolved *built* simulator and ships git+<repo>@<commit>.
# ---------------------------------------------------------------------------

def _stub_export_deps_only(monkeypatch):
    """Stub the export + framework-deps boundaries but NOT git_pip_url, so a pinned
    test can assert git_pip_url is never reached."""
    from vivarium_workbench.lib import remote_run
    monkeypatch.setattr(remote_run, "workspace_pinned_deps", lambda ws: [])
    monkeypatch.setattr(
        remote_run, "export_composite_pbg",
        lambda ws, cid, path: Path(path).write_bytes(b"{}"))


def _forbid_git_pip_url(monkeypatch):
    from vivarium_workbench.lib import remote_run

    def _boom(ws):
        raise AssertionError("git_pip_url must not be called in pinned mode")

    monkeypatch.setattr(remote_run, "git_pip_url", _boom)


def test_run_remote_pinned_derives_pip_url_from_built_commit(tmp_path, monkeypatch):
    """Pinned mode: the pip URL is git+<repo>.git@<resolved commit> and git_pip_url
    (which would raise on the dirty prod /workspace) is never called."""
    from vivarium_workbench.lib import remote_run, remote_pinned
    _stub_export_deps_only(monkeypatch)
    _forbid_git_pip_url(monkeypatch)
    monkeypatch.setattr(
        remote_pinned, "pinned_config",
        lambda: remote_pinned.PinnedConfig(
            repo_url="https://github.com/vivarium-collective/v2ecoli", branch="main"))
    monkeypatch.setattr(
        remote_pinned, "resolve_pinned_build",
        lambda client, repo, branch: {
            "simulator_id": 7, "commit": "abcdef123456", "branch": branch, "repo_url": repo})
    client = _CaptureClient()
    remote_run.run_remote(tmp_path, "some.composite", client=client,
                          poll_interval=0, dest=tmp_path, n_steps=5)
    assert client.extra_pip_deps == [
        "git+https://github.com/vivarium-collective/v2ecoli.git@abcdef123456"]


def test_run_remote_pinned_normalizes_dotgit_repo_url(tmp_path, monkeypatch):
    """A repo_url that already ends in .git yields a single .git suffix (no dupe)."""
    from vivarium_workbench.lib import remote_run, remote_pinned
    _stub_export_deps_only(monkeypatch)
    _forbid_git_pip_url(monkeypatch)
    monkeypatch.setattr(
        remote_pinned, "pinned_config",
        lambda: remote_pinned.PinnedConfig(
            repo_url="https://github.com/vivarium-collective/v2ecoli.git", branch="main"))
    monkeypatch.setattr(
        remote_pinned, "resolve_pinned_build",
        lambda client, repo, branch: {"commit": "deadbeef", "repo_url": repo})
    client = _CaptureClient()
    remote_run.run_remote(tmp_path, "some.composite", client=client,
                          poll_interval=0, dest=tmp_path, n_steps=5)
    assert client.extra_pip_deps == [
        "git+https://github.com/vivarium-collective/v2ecoli.git@deadbeef"]


def test_run_remote_unpinned_falls_back_to_git_pip_url(tmp_path, monkeypatch):
    """Local dev (no pinned config): keeps the clean+pushed git_pip_url path."""
    from vivarium_workbench.lib import remote_run, remote_pinned
    _stub_remote_boundaries(monkeypatch)  # stubs git_pip_url → git+file:///x@abc1234
    monkeypatch.setattr(remote_pinned, "pinned_config", lambda: None)
    client = _CaptureClient()
    remote_run.run_remote(tmp_path, "some.composite", client=client,
                          poll_interval=0, dest=tmp_path, n_steps=5)
    assert client.extra_pip_deps == ["git+file:///x@abc1234"]


def test_run_remote_pinned_no_build_raises(tmp_path, monkeypatch):
    """Pinned mode with no built simulator surfaces NoPinnedBuildError, so the
    detached runner (_execute_remote) marks the run failed rather than submitting
    garbage. compose_submit is never reached."""
    from vivarium_workbench.lib import remote_run, remote_pinned
    _stub_export_deps_only(monkeypatch)
    _forbid_git_pip_url(monkeypatch)
    monkeypatch.setattr(
        remote_pinned, "pinned_config",
        lambda: remote_pinned.PinnedConfig(repo_url="https://github.com/x/y", branch="main"))

    def _no_build(client, repo, branch):
        raise remote_pinned.NoPinnedBuildError("no built simulator")

    monkeypatch.setattr(remote_pinned, "resolve_pinned_build", _no_build)
    client = _CaptureClient()
    with pytest.raises(remote_pinned.NoPinnedBuildError):
        remote_run.run_remote(tmp_path, "some.composite", client=client,
                              poll_interval=0, dest=tmp_path, n_steps=5)
    assert client.extra_pip_deps is None  # never submitted
