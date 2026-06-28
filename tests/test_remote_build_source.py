import io
import json
import os
import tarfile
from pathlib import Path

import pytest

from vivarium_dashboard.lib import sms_api_client as sac
from vivarium_dashboard.lib import remote_build_source as rbs


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
            from vivarium_dashboard.lib.sms_api_client import SmsApiError
            raise SmsApiError("tunnel down")
    out = rbs.list_build_sources(_Boom())
    assert out["builds"] == [] and "tunnel down" in out["error"]


def test_source_builds_route_in_do_get(monkeypatch):
    from vivarium_dashboard import server
    from vivarium_dashboard.lib import remote_build_source
    monkeypatch.setattr(
        remote_build_source, "list_build_sources",
        lambda client: {"builds": [{"simulator_id": 7, "label": "x"}], "error": None},
    )
    captured = {}

    class H:
        path = "/api/source/builds"
        def _json(self, obj, code):
            captured.update(obj=obj, code=code)

    server.Handler._get_source_builds(H())
    assert captured["code"] == 200
    assert captured["obj"]["builds"][0]["simulator_id"] == 7


def test_switch_build_unknown_id_404(monkeypatch):
    from vivarium_dashboard import server
    from vivarium_dashboard.lib import remote_build_source
    monkeypatch.setattr(remote_build_source, "list_build_sources",
                        lambda client: {"builds": [], "error": None})
    captured = {}

    class H:
        def _json(self, obj, code):
            captured.update(obj=obj, code=code)

    server.Handler._post_source_switch_build(H(), {"simulator_id": 999})
    assert captured["code"] == 404


def test_switch_build_materializes_and_switches(monkeypatch, tmp_path):
    from vivarium_dashboard import server
    from vivarium_dashboard.lib import remote_build_source
    cache = tmp_path / "sim45-32b901"; cache.mkdir()
    (cache / "workspace.yaml").write_text("name: built\n")
    monkeypatch.setattr(remote_build_source, "list_build_sources",
                        lambda client: {"builds": [{"simulator_id": 45, "commit": "32b901",
                                                    "label": "v2ecoli @ 32b901 (build #45)"}], "error": None})
    monkeypatch.setattr(remote_build_source, "materialize_build",
                        lambda client, sim_id, commit, **k: cache)
    switched = {}
    monkeypatch.setattr(server, "_switch_active_workspace", lambda root: switched.update(root=root))
    captured = {}

    class H:
        def _json(self, obj, code):
            captured.update(obj=obj, code=code)

    server.Handler._post_source_switch_build(H(), {"simulator_id": 45})
    assert captured["code"] == 200 and captured["obj"]["ok"] is True
    assert switched["root"] == cache
    assert server._POST_ROUTE_MAP.get("/api/source/switch-build") == "_post_source_switch_build"


def test_switch_build_sms_api_down_502_not_404(monkeypatch):
    from vivarium_dashboard import server
    from vivarium_dashboard.lib import remote_build_source
    # sms-api unreachable: list degrades to empty builds + an error reason.
    monkeypatch.setattr(remote_build_source, "list_build_sources",
                        lambda client: {"builds": [], "error": "tunnel down"})
    captured = {}

    class H:
        def _json(self, obj, code):
            captured.update(obj=obj, code=code)

    server.Handler._post_source_switch_build(H(), {"simulator_id": 45})
    assert captured["code"] == 502  # not a misleading 404
    assert "tunnel down" in captured["obj"]["error"]


def test_switch_build_missing_id_400():
    from vivarium_dashboard import server
    captured = {}

    class H:
        def _json(self, obj, code):
            captured.update(obj=obj, code=code)

    server.Handler._post_source_switch_build(H(), {})
    assert captured["code"] == 400


def test_switch_build_materialize_failure_502_leaves_state_unchanged(monkeypatch):
    from vivarium_dashboard import server
    from vivarium_dashboard.lib import remote_build_source
    from vivarium_dashboard.lib.sms_api_client import SmsApiError
    monkeypatch.setattr(remote_build_source, "list_build_sources",
                        lambda client: {"builds": [{"simulator_id": 45, "commit": "32b901",
                                                    "label": "v2ecoli @ 32b901 (build #45)"}], "error": None})

    def _boom(client, sim_id, commit, **k):
        raise SmsApiError("tunnel down")

    monkeypatch.setattr(remote_build_source, "materialize_build", _boom)
    switched = {}
    monkeypatch.setattr(server, "_switch_active_workspace", lambda root: switched.update(root=root))
    captured = {}

    class H:
        def _json(self, obj, code):
            captured.update(obj=obj, code=code)

    server.Handler._post_source_switch_build(H(), {"simulator_id": 45})
    assert captured["code"] == 502
    assert switched == {}  # switch never fired → active workspace unchanged

# NOTE: test_source_switch_js_has_builds_section was removed — source-switch.js's
# two-optgroup <select> was superseded (the dropdown moved to the Branch-tab
# Source panel, branch-source.js). The builds API contract is covered above and
# in tests/test_source_branch.py.
