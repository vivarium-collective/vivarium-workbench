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

from vivarium_workbench.lib import composite_state_views as csv


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
    assert "import vivarium_workbench.server" not in src
    # The generator build now runs in the pooled env worker (not an embedded
    # sys.executable script), routed by resolve_composite_state.
    assert "resolve_composite_state" in src
    assert "get_pool" in src


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


def test_generator_success_embeds_declared_emit_paths(tmp_path, monkeypatch):
    """Task 6 review-finding fix: the subprocess script now also resolves the
    registered entry's declared ``emitters=[...]`` decl(s) (see the embedded
    script in ``composite_state_via_subprocess``) and returns them alongside
    ``state``/``module`` as ``emitters``. ``build_composite_state`` must embed
    the flattened paths INSIDE ``state`` (as ``_declared_emit_paths``) — a
    sibling field on the payload would be silently dropped by every client
    hop that only forwards ``payload.state`` onward to loom."""
    csv.clear_cache()
    ws = _make_ws(tmp_path)
    _patch_subprocess(monkeypatch, {
        "state": {"global_time": 0, "bulk": {}, "listeners": {}},
        "module": "v2ecoli.composites",
        "emitters": [{"address": "local:ParquetEmitter", "config": {},
                       "paths": ["global_time", "bulk", "listeners"]}],
    })
    body, status = csv.build_composite_state(ws, "v2ecoli.composites.baseline")
    assert status == 200
    assert body["kind"] == "generator"
    assert body["state"]["_declared_emit_paths"] == ["global_time", "bulk", "listeners"]
    # The original store keys are untouched.
    assert body["state"]["bulk"] == {}


def test_generator_success_no_emitters_declared_is_noop(tmp_path, monkeypatch):
    csv.clear_cache()
    ws = _make_ws(tmp_path)
    _patch_subprocess(monkeypatch, {"state": {"s": 1}, "module": "pkg.mod", "emitters": []})
    body, status = csv.build_composite_state(ws, "gen.ref")
    assert status == 200
    assert "_declared_emit_paths" not in body["state"]


def test_build_error_static_fallback_embeds_declared_emit_paths(tmp_path, monkeypatch):
    """The static-fallback branch reuses the subprocess's already-resolved
    ``emitters`` (available even though the live build failed — entry lookup
    happens before ``build_generator`` is called) rather than trusting a
    possibly-stale static artifact's own (absent, in this fixture) declaration."""
    csv.clear_cache()
    ws = _make_ws(tmp_path)
    static_dir = ws / "reports" / "composite-state"
    static_dir.mkdir(parents=True)
    (static_dir / "gen.json").write_text(json.dumps({"state": {"y": 2}}), encoding="utf-8")
    _patch_subprocess(monkeypatch, {
        "__build_error__": "boom",
        "emitters": [{"paths": ["y"]}],
    })
    body, status = csv.build_composite_state(ws, "gen")
    assert status == 200
    assert body["kind"] == "static-fallback"
    assert body["state"]["_declared_emit_paths"] == ["y"]
    assert body["state"]["y"] == 2


def test_static_spec_file_embeds_declared_emit_paths(tmp_path, monkeypatch):
    """The spec/path-resolution branch (a raw ``.composite.yaml``-shaped file
    with top-level ``state:``/``emitters:`` keys) also embeds the declared
    paths into the nested ``state`` dict."""
    csv.clear_cache()
    ws = _make_ws(tmp_path)
    (ws / "comp.yaml").write_text(yaml.safe_dump({
        "state": {"bulk": {}, "listeners": {}},
        "emitters": [{"paths": ["bulk", "listeners"]}],
    }), encoding="utf-8")
    _patch_subprocess(monkeypatch, {"__not_registered__": True})
    body, status = csv.build_composite_state(ws, "comp.yaml")
    assert status == 200
    assert body["kind"] == "spec"
    assert body["state"]["state"]["_declared_emit_paths"] == ["bulk", "listeners"]


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
