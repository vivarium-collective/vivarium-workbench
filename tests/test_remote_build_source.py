import io
import json
import os
import subprocess
import tarfile
import warnings
from pathlib import Path

import pytest

from vivarium_workbench.lib import sms_api_client as sac
from vivarium_workbench.lib import remote_build_source as rbs


class _Resp:
    """Minimal urlopen() context-manager response."""
    def __init__(self, body: bytes):
        self._body = body
        self._pos = 0
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def read(self, size=-1):
        if size < 0:
            result = self._body[self._pos:]
            self._pos = len(self._body)
        else:
            result = self._body[self._pos:self._pos + size]
            self._pos += len(result)
        return result


def test_list_simulators_hits_versions_endpoint(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        return _Resp(json.dumps({"versions": [{"database_id": 1}]}).encode())

    monkeypatch.setattr(sac, "urlopen", fake_urlopen)
    out = sac.SmsApiClient("http://x").list_simulators()
    assert out == {"versions": [{"database_id": 1}]}
    assert seen["url"] == "http://x/core/v1/simulator/versions"


def test_download_workspace_streams_to_file(monkeypatch, tmp_path):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        return _Resp(b"TARBALLBYTES")

    monkeypatch.setattr(sac, "urlopen", fake_urlopen)
    out = sac.SmsApiClient("http://x").download_workspace(45, tmp_path)
    assert out == tmp_path / "workspace.tar.gz"
    assert out.read_bytes() == b"TARBALLBYTES"
    assert seen["url"] == "http://x/api/v1/simulations/workspace?simulator_id=45"


def test_download_workspace_honors_per_call_timeout(monkeypatch, tmp_path):
    seen = {}
    def fake_urlopen(req, timeout=None):
        seen["timeout"] = timeout
        return _Resp(b"X")
    monkeypatch.setattr(sac, "urlopen", fake_urlopen)
    sac.SmsApiClient("http://x", timeout=30).download_workspace(45, tmp_path, timeout=600)
    assert seen["timeout"] == 600


def test_download_workspace_defaults_to_client_timeout(monkeypatch, tmp_path):
    seen = {}
    def fake_urlopen(req, timeout=None):
        seen["timeout"] = timeout
        return _Resp(b"X")
    monkeypatch.setattr(sac, "urlopen", fake_urlopen)
    sac.SmsApiClient("http://x", timeout=30).download_workspace(45, tmp_path)
    assert seen["timeout"] == 30


def _make_tarball(path, top="org-repo-abc1234"):
    """A GitHub-style tarball: one top-level dir containing workspace.yaml."""
    with tarfile.open(path, "w:gz") as tar:
        data = b"name: built-ws\n"
        info = tarfile.TarInfo(f"{top}/workspace.yaml")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))


class _FakeClient:
    def __init__(self, tarball_src):
        self._src = tarball_src
        self.downloads = 0
        self.timeout_seen = None

    def download_workspace(self, simulator_id, dest_dir, timeout=None):
        import shutil
        self.downloads += 1
        self.timeout_seen = timeout
        dest = Path(dest_dir) / "workspace.tar.gz"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(self._src, dest)
        return dest

    def list_simulators(self):
        return {"versions": [
            {"database_id": 45, "git_repo_url": "https://github.com/org/v2ecoli",
             "git_commit_hash": "32b901", "git_branch": "main", "created_at": "2026-06-18T00:00:00"},
        ]}


@pytest.fixture
def _cache(tmp_path, monkeypatch):
    monkeypatch.setenv("VIVARIUM_DASHBOARD_BUILD_CACHE", str(tmp_path / "bc"))
    return tmp_path


def test_materialize_extracts_and_strips_top_dir(_cache, tmp_path):
    tb = tmp_path / "src.tar.gz"; _make_tarball(tb)
    client = _FakeClient(tb)
    cache = rbs.materialize_build(client, 45, "32b901")
    assert cache == rbs.cache_dir_for(45, "32b901")
    assert (cache / "workspace.yaml").read_text() == "name: built-ws\n"   # top dir stripped


def test_materialize_reuses_cache(_cache, tmp_path):
    tb = tmp_path / "src.tar.gz"; _make_tarball(tb)
    client = _FakeClient(tb)
    rbs.materialize_build(client, 45, "32b901")
    rbs.materialize_build(client, 45, "32b901")   # second call
    assert client.downloads == 1                  # reused, not re-downloaded


def test_materialize_uses_long_download_timeout(_cache, tmp_path):
    tb = tmp_path / "src.tar.gz"; _make_tarball(tb)
    client = _FakeClient(tb)
    rbs.materialize_build(client, 45, "32b901")
    assert client.timeout_seen is not None and client.timeout_seen >= 300


def test_materialize_stamps_viv_build_json(_cache, tmp_path):
    tb = tmp_path / "src.tar.gz"; _make_tarball(tb)
    cache = rbs.materialize_build(_FakeClient(tb), 45, "32b901")
    meta = json.loads((cache / ".viv-build.json").read_text())
    assert meta["simulator_id"] == 45
    assert meta["commit"] == "32b901"


def test_materialize_does_not_clobber_existing_stamp(_cache, tmp_path):
    tb = tmp_path / "src.tar.gz"; _make_tarball(tb)
    cache = rbs.materialize_build(_FakeClient(tb), 45, "32b901")
    # simulate switch-build's richer stamp, then re-materialize (reuse path)
    (cache / ".viv-build.json").write_text('{"simulator_id": 45, "branch": "main", "rich": true}')
    rbs.materialize_build(_FakeClient(tb), 45, "32b901")
    meta = json.loads((cache / ".viv-build.json").read_text())
    assert meta.get("rich") is True


def test_materialize_rejects_unsafe_commit(_cache, tmp_path):
    tb = tmp_path / "src.tar.gz"; _make_tarball(tb)
    client = _FakeClient(tb)
    for bad in ["../escape", "", "abc/../../etc", "deadbeef; rm -rf"]:
        with pytest.raises(sac.SmsApiError):
            rbs.materialize_build(client, 45, bad)
    assert client.downloads == 0  # never even reached the download


# ---------------------------------------------------------------------------
# materialize_session_build — item 20: per-session write isolation
# ---------------------------------------------------------------------------
def test_materialize_session_build_is_independent_per_session(_cache, tmp_path):
    """The actual bug: two sessions bound to the same (simulator_id, commit)
    build must NOT share one mutable directory. A write in one session's clone
    must never appear in another's."""
    tb = tmp_path / "src.tar.gz"; _make_tarball(tb)
    client = _FakeClient(tb)

    dir_a = rbs.materialize_session_build(client, "session-aaaaaaaa", 45, "32b901")
    dir_b = rbs.materialize_session_build(client, "session-bbbbbbbb", 45, "32b901")

    assert dir_a != dir_b
    (dir_a / "workspace.yaml").write_text("mutated-by-session-a\n")
    assert (dir_b / "workspace.yaml").read_text() == "name: built-ws\n"  # untouched


def test_materialize_session_build_reuses_shared_base_download(_cache, tmp_path):
    """The expensive part (network fetch) stays shared/cached by commit — only
    the per-session clone is new work."""
    tb = tmp_path / "src.tar.gz"; _make_tarball(tb)
    client = _FakeClient(tb)

    rbs.materialize_session_build(client, "session-aaaaaaaa", 45, "32b901")
    rbs.materialize_session_build(client, "session-bbbbbbbb", 45, "32b901")
    assert client.downloads == 1  # base fetched once, reused for both sessions


def test_materialize_session_build_reuses_same_session_clone(_cache, tmp_path):
    tb = tmp_path / "src.tar.gz"; _make_tarball(tb)
    client = _FakeClient(tb)

    d1 = rbs.materialize_session_build(client, "session-aaaaaaaa", 45, "32b901")
    (d1 / "extra.txt").write_text("session-local state\n")
    d2 = rbs.materialize_session_build(client, "session-aaaaaaaa", 45, "32b901")

    assert d1 == d2
    assert (d2 / "extra.txt").exists()  # not clobbered by a re-clone


def test_materialize_session_build_path_is_session_scoped(_cache, tmp_path):
    tb = tmp_path / "src.tar.gz"; _make_tarball(tb)
    client = _FakeClient(tb)
    out = rbs.materialize_session_build(client, "session-aaaaaaaa", 45, "32b901")
    assert out == rbs.session_cache_dir_for("session-aaaaaaaa", 45, "32b901")
    assert out != rbs.cache_dir_for(45, "32b901")  # never the shared base itself


def test_materialize_session_build_rejects_unsafe_session_key(_cache, tmp_path):
    tb = tmp_path / "src.tar.gz"; _make_tarball(tb)
    client = _FakeClient(tb)
    for bad in ["", "short", "../escape", "a/../../etc", "has spaces"]:
        with pytest.raises(sac.SmsApiError):
            rbs.materialize_session_build(client, bad, 45, "32b901")
    assert client.downloads == 0  # validated before the (expensive) base fetch


def _make_tarball_with_dangling_symlink(path, top="org-repo-abc1234"):
    """A GitHub-style tarball whose one symlink member points one level short
    of its real target — the same shape as sms-ecoli's actual investigation/
    study symlinks. `materialize_build`'s tar extraction faithfully carries
    this into the shared base cache, dangling and all, same as production."""
    with tarfile.open(path, "w:gz") as tar:
        data = b"name: built-ws\n"
        info = tarfile.TarInfo(f"{top}/workspace.yaml")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))

        link = tarfile.TarInfo(f"{top}/investigations/demo/studies/orphan-study")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../studies/orphan-study"  # one `../` short — dangling
        tar.addfile(link)


def test_materialize_session_build_preserves_dangling_symlinks(_cache, tmp_path):
    """Real-world regression (found live against sms-ecoli build #55): a
    dangling symlink anywhere in the shared base must not crash the whole
    session clone. The clone should faithfully carry the same dangling
    symlink, not dereference-and-crash, not silently drop it."""
    tb = tmp_path / "src.tar.gz"
    _make_tarball_with_dangling_symlink(tb)
    client = _FakeClient(tb)

    session_dir = rbs.materialize_session_build(client, "session-aaaaaaaa", 45, "32b901")

    link = session_dir / "investigations" / "demo" / "studies" / "orphan-study"
    assert link.is_symlink()
    assert not link.exists()  # still dangling, same as the source — not silently dropped
    assert (session_dir / "workspace.yaml").read_text() == "name: built-ws\n"  # rest of the tree unaffected


def _make_tarball_with_valid_symlink(path, top="org-repo-abc1234"):
    """Same investigation/study symlink shape as the dangling case above, but
    correctly-depthed and resolvable — to test that a WORKING symlink both
    survives the clone and stays session-isolated through it, not just that
    a broken one doesn't crash."""
    with tarfile.open(path, "w:gz") as tar:
        ws = b"name: built-ws\n"
        info = tarfile.TarInfo(f"{top}/workspace.yaml")
        info.size = len(ws)
        tar.addfile(info, io.BytesIO(ws))

        data = b"original\n"
        info2 = tarfile.TarInfo(f"{top}/studies/real-study/data.txt")
        info2.size = len(data)
        tar.addfile(info2, io.BytesIO(data))

        link = tarfile.TarInfo(f"{top}/investigations/demo/studies/real-study")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../../studies/real-study"  # correct depth — resolves
        tar.addfile(link)


def test_materialize_session_build_symlinked_content_stays_isolated(_cache, tmp_path):
    """The actual property item 20 exists for, proven through a symlink
    specifically: a valid relative symlink must resolve WITHIN each
    session's own clone (not back to the shared base), and a write through
    it in one session must never appear in another's."""
    tb = tmp_path / "src.tar.gz"
    _make_tarball_with_valid_symlink(tb)
    client = _FakeClient(tb)

    dir_a = rbs.materialize_session_build(client, "session-aaaaaaaa", 45, "32b901")
    dir_b = rbs.materialize_session_build(client, "session-bbbbbbbb", 45, "32b901")

    link_a = dir_a / "investigations" / "demo" / "studies" / "real-study"
    link_b = dir_b / "investigations" / "demo" / "studies" / "real-study"
    assert link_a.is_symlink() and link_b.is_symlink()
    assert (link_a / "data.txt").read_text() == "original\n"
    assert (link_b / "data.txt").read_text() == "original\n"

    # the symlink must resolve INSIDE its own clone, not back to the shared base
    assert os.path.realpath(link_a).startswith(str(dir_a))
    assert os.path.realpath(link_b).startswith(str(dir_b))

    (link_a / "data.txt").write_text("mutated-by-session-a\n")
    assert (link_b / "data.txt").read_text() == "original\n"  # untouched


# ---------------------------------------------------------------------------
# build_cache_root() durability — item 21: PVC-backed persistence
#
# The deployment-side fix (mounting the existing `workbench-workspace` EBS PVC
# a second time at the container path `build_cache_root()` already defaults to
# — see that function's docstring) lives in kustomize manifests, outside this
# repo's test surface. What IS testable here is the property durability
# actually depends on: given a directory that already exists on disk (modeling
# "survived a restart because it's on a PVC now"), the materialize functions
# must recognize and reuse it rather than assuming a cold/empty start. A fresh
# `_FakeClient` with its own zeroed `downloads` counter stands in for a fresh
# process after a pod restart — no in-memory state carries over, only whatever
# is still on disk.
# ---------------------------------------------------------------------------
def test_build_cache_root_honors_new_prefix_env_var(tmp_path, monkeypatch):
    """The actual production knob (`VIVARIUM_WORKBENCH_BUILD_CACHE` — the
    kustomize env var the deployment-side fix sets) resolves correctly and
    without the deprecated-alias warning. Every other test in this file only
    ever exercises the OLD `VIVARIUM_DASHBOARD_BUILD_CACHE` alias via the
    `_cache` fixture; this closes that gap directly."""
    target = tmp_path / "durable-build-cache"
    monkeypatch.setenv("VIVARIUM_WORKBENCH_BUILD_CACHE", str(target))
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        assert rbs.build_cache_root() == target  # raises if the deprecated path fires


def test_materialize_build_survives_simulated_pod_restart(tmp_path, monkeypatch):
    """The shared base fetch, once on disk, is found by a process that has no
    memory of having created it — the property that makes it safe to move
    build_cache_root() onto durable storage."""
    monkeypatch.setenv("VIVARIUM_WORKBENCH_BUILD_CACHE", str(tmp_path / "bc"))
    tb = tmp_path / "src.tar.gz"
    _make_tarball(tb)

    first_process_client = _FakeClient(tb)
    cache = rbs.materialize_build(first_process_client, 45, "32b901")
    assert first_process_client.downloads == 1

    # "restart": brand-new client, brand-new download counter, same disk.
    second_process_client = _FakeClient(tb)
    cache_again = rbs.materialize_build(second_process_client, 45, "32b901")

    assert cache_again == cache
    assert second_process_client.downloads == 0  # never re-fetched — found on disk
    assert (cache_again / "workspace.yaml").read_text() == "name: built-ws\n"


def test_materialize_session_build_survives_simulated_pod_restart(tmp_path, monkeypatch):
    """A session's OWN clone — and any data it wrote into that clone — is
    still there for a "new process" that resolves the same session_key. This
    is what makes durable storage actually preserve in-progress session state
    across a pod restart, instead of only avoiding the shared base re-download."""
    monkeypatch.setenv("VIVARIUM_WORKBENCH_BUILD_CACHE", str(tmp_path / "bc"))
    tb = tmp_path / "src.tar.gz"
    _make_tarball(tb)

    first_process_client = _FakeClient(tb)
    session_dir = rbs.materialize_session_build(first_process_client, "session-aaaaaaaa", 45, "32b901")
    (session_dir / "workspace.yaml").write_text("mutated-before-restart\n")

    second_process_client = _FakeClient(tb)
    session_dir_again = rbs.materialize_session_build(second_process_client, "session-aaaaaaaa", 45, "32b901")

    assert session_dir_again == session_dir
    assert second_process_client.downloads == 0  # neither the base fetch...
    assert (session_dir_again / "workspace.yaml").read_text() == "mutated-before-restart\n"  # ...nor a re-clone


def test_list_build_sources_maps_and_labels():
    client = _FakeClient(None)
    out = rbs.list_build_sources(client)
    assert out["error"] is None
    b = out["builds"][0]
    assert b["simulator_id"] == 45 and b["commit"] == "32b901"
    assert b["label"] == "v2ecoli @ 32b901 (build #45)"
    # repo_url must be the raw URL from sms-api (not the bare display name)
    assert b["repo_url"] == "https://github.com/org/v2ecoli"
    assert b["created_at"] == "2026-06-18T00:00:00"


def test_list_build_sources_degrades_on_error():
    class _Boom:
        def list_simulators(self):
            from vivarium_workbench.lib.sms_api_client import SmsApiError
            raise SmsApiError("tunnel down")
    out = rbs.list_build_sources(_Boom())
    assert out["builds"] == [] and "tunnel down" in out["error"]


def test_source_builds_route_in_do_get(monkeypatch):
    """The GET /api/source/builds builder returns the sms-api build list."""
    from vivarium_workbench.lib import workspace_deps_views as wdv
    from vivarium_workbench.lib import remote_build_source
    monkeypatch.setattr(
        remote_build_source, "list_build_sources",
        lambda client: {"builds": [{"simulator_id": 7, "label": "x"}], "error": None},
    )
    out = wdv.build_source_builds()
    assert out["builds"][0]["simulator_id"] == 7


def test_switch_build_unknown_id_404(monkeypatch):
    from vivarium_workbench.lib import source_build_views as sbv
    monkeypatch.setattr(sbv, "list_build_sources",
                        lambda client: {"builds": [], "error": None})
    obj, code = sbv.switch_build({"simulator_id": 999})
    assert code == 404


def test_switch_build_materializes_and_switches(monkeypatch, tmp_path):
    from vivarium_workbench.lib import source_build_views as sbv
    cache = tmp_path / "sim45-32b901"; cache.mkdir()
    (cache / "workspace.yaml").write_text("name: built\n")
    monkeypatch.setattr(sbv, "list_build_sources",
                        lambda client: {"builds": [{"simulator_id": 45, "commit": "32b901",
                                                    "label": "v2ecoli @ 32b901 (build #45)"}], "error": None})
    monkeypatch.setattr(sbv, "materialize_build",
                        lambda client, sim_id, commit, **k: cache)
    switched = {}
    monkeypatch.setattr(sbv.active_workspace, "switch_workspace",
                        lambda root: switched.update(root=root))

    obj, code = sbv.switch_build({"simulator_id": 45})
    assert code == 200 and obj["ok"] is True
    assert obj["source"]["path"] == str(cache)
    assert switched["root"] == cache


def test_switch_build_sms_api_down_502_not_404(monkeypatch):
    from vivarium_workbench.lib import source_build_views as sbv
    # sms-api unreachable: list degrades to empty builds + an error reason.
    monkeypatch.setattr(sbv, "list_build_sources",
                        lambda client: {"builds": [], "error": "tunnel down"})
    obj, code = sbv.switch_build({"simulator_id": 45})
    assert code == 502  # not a misleading 404
    assert "tunnel down" in obj["error"]


def test_switch_build_missing_id_400():
    from vivarium_workbench.lib import source_build_views as sbv
    obj, code = sbv.switch_build({})
    assert code == 400


def test_switch_build_materialize_failure_502_leaves_state_unchanged(monkeypatch):
    from vivarium_workbench.lib import source_build_views as sbv
    from vivarium_workbench.lib.sms_api_client import SmsApiError
    monkeypatch.setattr(sbv, "list_build_sources",
                        lambda client: {"builds": [{"simulator_id": 45, "commit": "32b901",
                                                    "label": "v2ecoli @ 32b901 (build #45)"}], "error": None})

    def _boom(client, sim_id, commit, **k):
        raise SmsApiError("tunnel down")

    monkeypatch.setattr(sbv, "materialize_build", _boom)
    switched = {}
    monkeypatch.setattr(sbv.active_workspace, "switch_workspace",
                        lambda root: switched.update(root=root))

    obj, code = sbv.switch_build({"simulator_id": 45})
    assert code == 502
    assert switched == {}  # switch never fired → active workspace unchanged

# NOTE: test_source_switch_js_has_builds_section was removed — source-switch.js's
# two-optgroup <select> was superseded (the dropdown moved to the Branch-tab
# Source panel, branch-source.js). The builds API contract is covered above and
# in tests/test_source_branch.py.


# ---------------------------------------------------------------------------
# ensure_git_workspace — makes a tarball-materialized build push-ready
# ---------------------------------------------------------------------------
def test_ensure_git_workspace_creates_origin_and_branch(tmp_path):
    from vivarium_workbench.lib import git_status

    cache = tmp_path / "sim45-32b901deadbeef"
    cache.mkdir()
    (cache / "workspace.yaml").write_text("name: built\n")

    rbs.ensure_git_workspace(cache, "https://github.com/org/repo.git", "main", "32b901deadbeef", 45)

    assert (cache / ".git").exists()
    assert git_status.has_origin_remote(cache)
    assert git_status.remote_repo_url(cache) == "https://github.com/org/repo"
    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=cache, capture_output=True, text=True,
    ).stdout.strip()
    assert branch == "workbench/sim45-32b901deadbe"  # never the upstream 'main' ref
    # a commit must already exist (dispatch's `rev-parse --abbrev-ref HEAD` /
    # push both require a named branch with at least one commit on it)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cache, capture_output=True, text=True)
    assert sha.returncode == 0 and sha.stdout.strip()


def test_ensure_git_workspace_is_idempotent(tmp_path):
    cache = tmp_path / "sim45-32b901"
    cache.mkdir()
    (cache / "workspace.yaml").write_text("name: built\n")
    rbs.ensure_git_workspace(cache, "https://github.com/org/repo.git", "main", "32b901", 45)
    sha1 = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cache, capture_output=True, text=True).stdout

    rbs.ensure_git_workspace(cache, "https://github.com/org/repo.git", "main", "32b901", 45)  # no-op: .git exists
    sha2 = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cache, capture_output=True, text=True).stdout
    assert sha1 == sha2


def test_ensure_git_workspace_skips_without_repo_url(tmp_path):
    cache = tmp_path / "sim45-32b901"
    cache.mkdir()
    rbs.ensure_git_workspace(cache, "", "main", "32b901", 45)
    assert not (cache / ".git").exists()


def test_switch_build_wires_ensure_git_workspace(monkeypatch, tmp_path):
    """switch_build must pass the sms-api-reported repo_url/branch/commit through
    to ensure_git_workspace so a switched build becomes push-ready — the actual
    fix for the "no GitHub remote configured" dispatch failure on any session
    bound to a switched build (found live trying to dispatch an sms-ecoli pilot
    run from a switch-build session)."""
    from vivarium_workbench.lib import source_build_views as sbv

    cache = tmp_path / "sim45-32b901"
    cache.mkdir()
    (cache / "workspace.yaml").write_text("name: built\n")
    monkeypatch.setattr(sbv, "list_build_sources", lambda client: {
        "builds": [{"simulator_id": 45, "commit": "32b901", "branch": "main",
                    "repo_url": "https://github.com/CovertLabEcoli/sms-ecoli",
                    "label": "sms-ecoli @ 32b901 (build #45)"}],
        "error": None,
    })
    monkeypatch.setattr(sbv, "materialize_build", lambda client, sim_id, commit, **k: cache)
    monkeypatch.setattr(sbv.active_workspace, "switch_workspace", lambda root: None)

    seen = {}
    monkeypatch.setattr(sbv, "ensure_git_workspace",
                        lambda cd, repo_url, branch, commit, sim_id: seen.update(
                            cache_dir=cd, repo_url=repo_url, branch=branch, commit=commit, sim_id=sim_id))

    obj, code = sbv.switch_build({"simulator_id": 45})
    assert code == 200
    assert seen == {
        "cache_dir": cache, "repo_url": "https://github.com/CovertLabEcoli/sms-ecoli",
        "branch": "main", "commit": "32b901", "sim_id": 45,
    }
