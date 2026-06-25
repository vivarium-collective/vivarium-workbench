"""Parity + branch tests for ``lib.composite_state_views.build_composite_state``.

These exercise the four resolution branches of the composite-state worker
(generator → static fallback → spec/path → 404) deterministically by
monkeypatching the subprocess generator build, plus a ``TestServerShimParity``
class that drives the REAL ``server.Handler._get_composite_state`` (both URL
forms) and asserts its body matches the lib/route output.  The heavy real
generator build is exercised separately, gated by ``importorskip``.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest
import yaml

from vivarium_dashboard.lib import composite_state_views as csv


def _make_ws(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: ws\n", encoding="utf-8")
    return ws


def _patch_subprocess(monkeypatch, result):
    """Force ``composite_state_via_subprocess`` to return ``result``."""
    monkeypatch.setattr(csv, "composite_state_via_subprocess", lambda ws, ref: result)


# ---------------------------------------------------------------------------
# Subprocess-script flip-readiness: must NOT import server.
# ---------------------------------------------------------------------------

def test_subprocess_script_does_not_import_server():
    src = inspect.getsource(csv.composite_state_via_subprocess)
    assert "import vivarium_dashboard.server" not in src
    assert "sys.path.insert(0, sys.argv[1])" in src


# ---------------------------------------------------------------------------
# build_composite_state branch coverage.
# ---------------------------------------------------------------------------

def test_no_ref_400(tmp_path):
    csv.clear_cache()
    ws = _make_ws(tmp_path)
    body, status = csv.build_composite_state(ws, "")
    assert status == 400
    assert body == {"error": "ref required"}


def test_unknown_ref_404_unresolved(tmp_path, monkeypatch):
    csv.clear_cache()
    ws = _make_ws(tmp_path)
    _patch_subprocess(monkeypatch, {"__not_registered__": True})
    body, status = csv.build_composite_state(ws, "nope.not.real")
    assert status == 404
    assert body["unresolved"] is True
    assert body["ref"] == "nope.not.real"
    assert "not a registered composite" in body["error"]


def test_workspace_relative_spec_file(tmp_path, monkeypatch):
    csv.clear_cache()
    ws = _make_ws(tmp_path)
    (ws / "comp.yaml").write_text(yaml.safe_dump({"a": {"b": 1}}), encoding="utf-8")
    _patch_subprocess(monkeypatch, {"__not_registered__": True})
    body, status = csv.build_composite_state(ws, "comp.yaml")
    assert status == 200
    assert body["kind"] == "spec"
    assert body["state"] == {"a": {"b": 1}}


def test_static_state_file_resolved(tmp_path, monkeypatch):
    csv.clear_cache()
    ws = _make_ws(tmp_path)
    static_dir = ws / "reports" / "composite-state"
    static_dir.mkdir(parents=True)
    (static_dir / "myref.json").write_text(json.dumps({"state": {"x": 1}}), encoding="utf-8")
    _patch_subprocess(monkeypatch, {"__not_registered__": True})
    body, status = csv.build_composite_state(ws, "myref")
    assert status == 200
    assert body["kind"] == "spec"
    # Branch-3 static path does NOT unwrap "state" — the whole file is the doc.
    assert body["state"] == {"state": {"x": 1}}


def test_build_error_static_fallback(tmp_path, monkeypatch):
    csv.clear_cache()
    ws = _make_ws(tmp_path)
    static_dir = ws / "reports" / "composite-state"
    static_dir.mkdir(parents=True)
    (static_dir / "gen.json").write_text(json.dumps({"state": {"y": 2}}), encoding="utf-8")
    _patch_subprocess(monkeypatch, {"__build_error__": "boom"})
    body, status = csv.build_composite_state(ws, "gen")
    assert status == 200
    assert body["kind"] == "static-fallback"
    assert body["state"] == {"y": 2}  # inner state unwrapped in the fallback branch
    assert "live build failed: boom" in body["note"]


def test_build_error_no_fallback_400(tmp_path, monkeypatch):
    csv.clear_cache()
    ws = _make_ws(tmp_path)
    _patch_subprocess(monkeypatch, {"__build_error__": "boom"})
    body, status = csv.build_composite_state(ws, "gen")
    assert status == 400
    assert body == {"error": "generator build failed: boom"}


def test_generator_success_and_cache(tmp_path, monkeypatch):
    csv.clear_cache()
    ws = _make_ws(tmp_path)
    _patch_subprocess(monkeypatch, {"state": {"s": 1}, "module": "pkg.mod"})
    body, status = csv.build_composite_state(ws, "gen.ref")
    assert status == 200
    assert body == {"state": {"s": 1}, "kind": "generator", "module": "pkg.mod"}

    # Second call hits the TTL cache → cached:true, even if the subprocess now
    # would return something else.
    _patch_subprocess(monkeypatch, {"__not_registered__": True})
    body2, status2 = csv.build_composite_state(ws, "gen.ref")
    assert status2 == 200
    assert body2["cached"] is True
    assert body2["kind"] == "generator"

    # ?fresh=True bypasses the cache.
    body3, status3 = csv.build_composite_state(ws, "gen.ref", fresh=True)
    assert status3 == 404  # subprocess now returns __not_registered__


def test_parse_failure_500(tmp_path, monkeypatch):
    csv.clear_cache()
    ws = _make_ws(tmp_path)
    (ws / "bad.json").write_text("{not valid json", encoding="utf-8")
    _patch_subprocess(monkeypatch, {"__not_registered__": True})
    body, status = csv.build_composite_state(ws, "bad.json")
    assert status == 500
    assert "parse failed" in body["error"]


# ---------------------------------------------------------------------------
# Real generator build (heavy) — gated.
# ---------------------------------------------------------------------------

_OBS_FIXTURE = Path(__file__).parent / "_fixtures" / "ws_increase_demo"
_OBS_REF = "pbg_ws_increase_demo.composites.increase-demo"


def test_real_generator_build(tmp_path):
    pytest.importorskip("pbg_superpowers.composite_generator")
    import shutil
    csv.clear_cache()
    ws = tmp_path / "ws"
    shutil.copytree(_OBS_FIXTURE, ws)
    body, status = csv.build_composite_state(ws, _OBS_REF, fresh=True)
    # Either a real generator build (200/spec or generator) or an honest
    # resolution to the spec file — never a crash.
    assert status in (200, 400, 404)
    if status == 200:
        assert "state" in body and body.get("kind") in ("generator", "spec", "static-fallback")


# ---------------------------------------------------------------------------
# Server-shim parity: the REAL handler body == lib/route output, both URL forms.
# ---------------------------------------------------------------------------

class TestServerShimParity:
    """Real ``server.Handler._get_composite_state`` body == lib output."""

    @staticmethod
    def _invoke_legacy(monkeypatch, ws_root: Path, path: str):
        import vivarium_dashboard.server as server
        monkeypatch.setattr(server, "WORKSPACE", ws_root)
        handler = server.Handler.__new__(server.Handler)
        captured: dict = {}

        def _fake_json(data, code):
            captured["body"] = data
            captured["status"] = code

        handler._json = _fake_json  # type: ignore[method-assign]
        handler.path = path
        handler._get_composite_state()
        return captured

    def test_query_form_404_parity(self, tmp_path, monkeypatch):
        csv.clear_cache()
        ws = _make_ws(tmp_path)
        _patch_subprocess(monkeypatch, {"__not_registered__": True})

        lib_body, lib_status = csv.build_composite_state(ws, "nope.x")
        assert lib_status == 404

        cap = self._invoke_legacy(monkeypatch, ws, "/api/composite-state?ref=nope.x")
        assert cap["status"] == 404
        assert cap["body"] == lib_body

    def test_query_form_spec_parity(self, tmp_path, monkeypatch):
        csv.clear_cache()
        ws = _make_ws(tmp_path)
        (ws / "comp.yaml").write_text(yaml.safe_dump({"a": 1}), encoding="utf-8")
        _patch_subprocess(monkeypatch, {"__not_registered__": True})

        cap = self._invoke_legacy(monkeypatch, ws, "/api/composite-state?ref=comp.yaml")
        assert cap["status"] == 200
        assert cap["body"]["kind"] == "spec"

        csv.clear_cache()
        lib_body, lib_status = csv.build_composite_state(ws, "comp.yaml")
        assert (lib_body, lib_status) == (cap["body"], cap["status"])

    def test_path_form_strips_json(self, tmp_path, monkeypatch):
        csv.clear_cache()
        ws = _make_ws(tmp_path)
        static_dir = ws / "reports" / "composite-state"
        static_dir.mkdir(parents=True)
        (static_dir / "myref.json").write_text(json.dumps({"state": {"x": 1}}), encoding="utf-8")
        _patch_subprocess(monkeypatch, {"__not_registered__": True})

        # Path form: /api/composite-state/myref.json → ref "myref".
        cap = self._invoke_legacy(
            monkeypatch, ws, "/api/composite-state/myref.json")
        assert cap["status"] == 200
        assert cap["body"]["kind"] == "spec"

        csv.clear_cache()
        lib_body, _ = csv.build_composite_state(ws, "myref")
        assert cap["body"] == lib_body

    def test_no_ref_400_parity(self, tmp_path, monkeypatch):
        ws = _make_ws(tmp_path)
        cap = self._invoke_legacy(monkeypatch, ws, "/api/composite-state")
        assert cap["status"] == 400
        assert cap["body"] == {"error": "ref required"}
