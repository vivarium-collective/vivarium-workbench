"""Tests for worker-backed analysis viewers (sys.path cleanup 5/5).

Discovery + launch of repo-contributed viewers (their live ``applies`` /
``get_viewers`` / ``targets`` / ``launch`` callables) run in the env worker now.
Two layers:

  * ``env_worker._analysis_viewers`` (+ its ``_av_*`` helpers) — the callable-touching
    discover/launch, exercised with a fake ``workbench_viewers`` module and the
    candidate-package scan stubbed to just that package;
  * ``analysis_viewers.viewers_public`` / ``resolve_launch`` — the HTTP-side seams,
    with the worker pool stubbed (passthrough, and worker-down degrade).
"""
from __future__ import annotations

from pathlib import Path

from vivarium_workbench import env_worker
from vivarium_workbench.lib import analysis_viewers


def _fake_viewers_module():
    import types
    mod = types.ModuleType("fakeviz_pkg.workbench_viewers")

    def get_viewers(ws_root):
        return [
            {"id": "demo", "title": "Demo", "kind": "launcher",
             "launch": lambda ws, study, run, ctx: {"url": f"/x/{study}"},
             "targets": lambda ws: [{"study": "s1", "label": "S1"}]},
            {"id": "embedonly", "kind": "embed",
             "assets": {"js": ["/a.js"], "mount_id": "m", "api_prefix": "/p"}},
            {"id": "hidden", "applies": lambda ws: False,
             "launch": lambda *a: {"url": "/y"}},
            {"no_id": True},  # no id → dropped
        ]

    mod.get_viewers = get_viewers  # type: ignore[attr-defined]
    return mod


def _patch_candidates(monkeypatch, mod):
    monkeypatch.setattr(env_worker, "_av_candidate_packages",
                        lambda ws_root: ["fakeviz_pkg"])
    monkeypatch.setattr(env_worker, "_av_load_viewers_module",
                        lambda pkg: mod if pkg == "fakeviz_pkg" else None)


# ---------------------------------------------------------------------------
# worker internals
# ---------------------------------------------------------------------------

def test_discover_filters_and_tags(monkeypatch):
    _patch_candidates(monkeypatch, _fake_viewers_module())
    viewers = env_worker._av_discover_viewers(Path("/ws"))
    ids = [v["id"] for v in viewers]
    assert ids == ["demo", "embedonly"]  # hidden (applies False) + no_id dropped
    assert viewers[0]["uid"] == "fakeviz_pkg::demo"
    assert viewers[0]["package"] == "fakeviz_pkg"


def test_list_action_returns_public_specs(monkeypatch):
    _patch_candidates(monkeypatch, _fake_viewers_module())
    monkeypatch.setattr(env_worker, "_workspace", "/ws")
    out = env_worker._analysis_viewers({"action": "list"})
    specs = {v["uid"]: v for v in out["viewers"]}
    demo = specs["fakeviz_pkg::demo"]
    assert demo["title"] == "Demo"
    assert demo["kind"] == "launcher"
    assert demo["targets"] == [{"study": "s1", "label": "S1", "detail": ""}]
    assert demo["assets"] is None  # launcher, no assets → None
    embed = specs["fakeviz_pkg::embedonly"]
    assert embed["assets"] == {"js": ["/a.js"], "mount_id": "m", "api_prefix": "/p"}
    # public spec is JSON-safe: no callables leak.
    assert "launch" not in demo and "applies" not in demo


def test_launch_action_invokes_callable(monkeypatch):
    _patch_candidates(monkeypatch, _fake_viewers_module())
    monkeypatch.setattr(env_worker, "_workspace", "/ws")
    out = env_worker._analysis_viewers(
        {"action": "launch", "uid": "fakeviz_pkg::demo", "study": "s1"})
    assert out["result"] == {"url": "/x/s1"}


def test_launch_unknown_uid(monkeypatch):
    _patch_candidates(monkeypatch, _fake_viewers_module())
    monkeypatch.setattr(env_worker, "_workspace", "/ws")
    out = env_worker._analysis_viewers({"action": "launch", "uid": "nope::x"})
    assert out["result"] == {"error": "viewer not found: nope::x", "status": 404}


def test_launch_not_launchable(monkeypatch):
    _patch_candidates(monkeypatch, _fake_viewers_module())
    monkeypatch.setattr(env_worker, "_workspace", "/ws")
    out = env_worker._analysis_viewers(
        {"action": "launch", "uid": "fakeviz_pkg::embedonly"})
    assert out["result"]["status"] == 400  # embed has no launch callable


def test_launch_callable_raises(monkeypatch):
    import types
    mod = types.ModuleType("fakeviz_pkg.workbench_viewers")

    def _boom(ws, study, run, ctx):
        raise RuntimeError("launch failed")

    mod.get_viewers = lambda ws_root: [  # type: ignore[attr-defined]
        {"id": "b", "launch": _boom}]
    _patch_candidates(monkeypatch, mod)
    monkeypatch.setattr(env_worker, "_workspace", "/ws")
    out = env_worker._analysis_viewers(
        {"action": "launch", "uid": "fakeviz_pkg::b"})
    assert out["result"] == {"error": "RuntimeError: launch failed", "status": 500}


def test_get_viewers_raises_is_skipped(monkeypatch):
    import types
    mod = types.ModuleType("fakeviz_pkg.workbench_viewers")

    def _boom(ws_root):
        raise ValueError("bad contributor")

    mod.get_viewers = _boom  # type: ignore[attr-defined]
    _patch_candidates(monkeypatch, mod)
    assert env_worker._av_discover_viewers(Path("/ws")) == []


# ---------------------------------------------------------------------------
# HTTP-side orchestrator
# ---------------------------------------------------------------------------

class _FakePool:
    def __init__(self, reply=None, exc=None):
        self._reply, self._exc, self.calls = reply, exc, []

    def call(self, ws_root, method, params=None):
        self.calls.append((method, params))
        if self._exc is not None:
            raise self._exc
        return self._reply


def _patch_pool(monkeypatch, pool):
    import vivarium_workbench.lib.env_worker_pool as ewp
    monkeypatch.setattr(ewp, "get_pool", lambda: pool)


def test_viewers_public_passthrough(monkeypatch, tmp_path):
    viewers = [{"uid": "pkg::demo", "title": "Demo"}]
    pool = _FakePool(reply={"viewers": viewers})
    _patch_pool(monkeypatch, pool)
    assert analysis_viewers.viewers_public(tmp_path) == viewers
    assert pool.calls[0] == ("analysis_viewers", {"action": "list"})


def test_viewers_public_worker_down_is_empty(monkeypatch, tmp_path):
    _patch_pool(monkeypatch, _FakePool(exc=RuntimeError("no venv")))
    assert analysis_viewers.viewers_public(tmp_path) == []


def test_resolve_launch_passthrough(monkeypatch, tmp_path):
    pool = _FakePool(reply={"result": {"url": "/z"}})
    _patch_pool(monkeypatch, pool)
    out = analysis_viewers.resolve_launch(tmp_path, "pkg::demo", study="s1", run="r1")
    assert out == {"url": "/z"}
    _method, params = pool.calls[0]
    assert params["action"] == "launch"
    assert params["uid"] == "pkg::demo"
    assert params["study"] == "s1" and params["run"] == "r1"


def test_resolve_launch_worker_down_is_503(monkeypatch, tmp_path):
    _patch_pool(monkeypatch, _FakePool(exc=RuntimeError("no venv")))
    out = analysis_viewers.resolve_launch(tmp_path, "pkg::demo")
    assert out["status"] == 503


def test_resolve_launch_malformed_reply_is_500(monkeypatch, tmp_path):
    _patch_pool(monkeypatch, _FakePool(reply={"unexpected": True}))
    out = analysis_viewers.resolve_launch(tmp_path, "pkg::demo")
    assert out["status"] == 500
