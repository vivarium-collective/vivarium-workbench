"""Tests for ``lib.viz_preview_views.visualization_preview`` — the HTTP-side
orchestrator.

The class-touching render now runs in the env worker (``viz_preview``); this
builder only validates the request, assembles the investigation ``inputs_store``
HTTP-side, calls the worker, and maps its reply to a ``(body, status)`` tuple. So
these tests stub the worker pool's ``.call`` and assert the *orchestration*:

  * 400 on missing/blank address (no worker call);
  * 404 when the worker reports ``{"status": "not_registered"}``;
  * demo/streaming/error results pass through verbatim as 200;
  * for an ``investigation:<name>`` source, that ``gather_emitter_outputs`` +
    ``build_viz_composite`` run and the resulting ``inputs_store`` is forwarded to
    the worker (with the worker-provided ``inputs_by_class`` mode);
  * no-runs.db and assemble-exception record the fallback note (``note_prefix``)
    and forward ``investigation_inputs_store=None`` so the worker renders demo;
  * a worker that fails to start soft-degrades to a 200 error stub.

The worker's render internals (demo single-update, streaming 12-step, no-html
fallback div, exception → ok:False) are covered in ``test_env_worker_viz_preview.py``.
"""
from __future__ import annotations

from pathlib import Path

from vivarium_workbench.lib import viz_preview_views as views
from vivarium_workbench.lib import study_spec


def _make_ws(tmp_path: Path, *, name: str = "demo-ws") -> Path:
    (tmp_path / "workspace.yaml").write_text(f"name: {name}\n", encoding="utf-8")
    return tmp_path


class _FakePool:
    """Records the last ``.call`` and returns a canned reply (or raises)."""

    def __init__(self, reply=None, exc=None):
        self._reply = reply
        self._exc = exc
        self.calls = []

    def call(self, ws_root, method, params=None):
        self.calls.append((method, params))
        if self._exc is not None:
            raise self._exc
        return self._reply


def _patch_pool(monkeypatch, pool):
    import vivarium_workbench.lib.env_worker_pool as ewp
    monkeypatch.setattr(ewp, "get_pool", lambda: pool)


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

def test_missing_address_400(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)
    pool = _FakePool(reply={"ok": True})
    _patch_pool(monkeypatch, pool)
    body, status = views.visualization_preview(ws, {})
    assert status == 400
    assert body == {"error": "address is required"}
    assert pool.calls == []  # never reached the worker


def test_blank_address_400(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)
    pool = _FakePool(reply={"ok": True})
    _patch_pool(monkeypatch, pool)
    body, status = views.visualization_preview(ws, {"address": "   "})
    assert status == 400
    assert pool.calls == []


# ---------------------------------------------------------------------------
# 404 / passthrough
# ---------------------------------------------------------------------------

def test_class_not_registered_404(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)
    _patch_pool(monkeypatch, _FakePool(reply={"status": "not_registered"}))
    body, status = views.visualization_preview(ws, {"address": "local:Nope"})
    assert status == 404
    assert body == {"error": "class not registered: local:Nope"}


def test_demo_result_passes_through_200(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)
    reply = {"ok": True, "html": "<b>x</b>", "source_used": "demo", "notes": ""}
    pool = _FakePool(reply=reply)
    _patch_pool(monkeypatch, pool)
    body, status = views.visualization_preview(ws, {"address": "local:FakeViz"})
    assert status == 200
    assert body == reply
    method, params = pool.calls[0]
    assert method == "viz_preview"
    assert params["address"] == "local:FakeViz"
    assert params["source"] == "demo"
    assert params["investigation_inputs_store"] is None
    assert params["note_prefix"] == []


def test_ok_false_result_still_200(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)
    reply = {"ok": False, "html": "<p>demo render failed: ValueError: kaboom</p>",
             "source_used": "demo", "notes": ""}
    _patch_pool(monkeypatch, _FakePool(reply=reply))
    body, status = views.visualization_preview(ws, {"address": "local:Boom"})
    assert status == 200
    assert body == reply


def test_malformed_worker_reply_soft_degrades_200(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)
    _patch_pool(monkeypatch, _FakePool(reply="not a dict"))
    body, status = views.visualization_preview(ws, {"address": "local:X"})
    assert status == 200
    assert body["ok"] is False
    assert "malformed worker response" in body["html"]


# ---------------------------------------------------------------------------
# worker-unavailable soft-degrade
# ---------------------------------------------------------------------------

def test_worker_unavailable_soft_degrades_200(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)
    _patch_pool(monkeypatch, _FakePool(exc=RuntimeError("no venv")))
    body, status = views.visualization_preview(ws, {"address": "local:X"})
    assert status == 200
    assert body["ok"] is False
    assert body["source_used"] == "demo"
    assert "preview unavailable" in body["html"]
    assert "RuntimeError" in body["html"]


# ---------------------------------------------------------------------------
# investigation source: HTTP-side inputs_store assembly
# ---------------------------------------------------------------------------

def test_investigation_forwards_inputs_store(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)
    inv = "inv-x"
    inv_dir = study_spec.study_dir(ws, inv)
    inv_dir.mkdir(parents=True, exist_ok=True)
    (inv_dir / "runs.db").write_text("", encoding="utf-8")  # just needs to exist

    from vivarium_workbench.lib import investigations, viz_render
    monkeypatch.setattr(investigations, "gather_emitter_outputs",
                        lambda db_path: {"gathered": True})
    monkeypatch.setattr(viz_render, "viz_render_hooks",
                        lambda ws_root: ({"FakeViz": {"observable": "list[float]"}}, None))

    captured = {}

    def _fake_build(viz_spec, gathered, registry, *, inputs_by_class=None):
        captured["viz_spec"] = viz_spec
        captured["gathered"] = gathered
        captured["inputs_by_class"] = inputs_by_class
        return {"inputs_store": {"observable": [1.0, 2.0]}}

    monkeypatch.setattr(investigations, "build_viz_composite", _fake_build)

    reply = {"ok": True, "html": "<b>x</b>",
             "source_used": f"investigation:{inv}", "notes": ""}
    pool = _FakePool(reply=reply)
    _patch_pool(monkeypatch, pool)

    body, status = views.visualization_preview(
        ws, {"address": "local:FakeViz", "source": f"investigation:{inv}"})
    assert status == 200
    assert body == reply
    # build_viz_composite ran in worker-provided (inputs_by_class) mode...
    assert captured["inputs_by_class"] == {"FakeViz": {"observable": "list[float]"}}
    assert captured["gathered"] == {"gathered": True}
    # ...and its inputs_store was forwarded to the worker.
    _method, params = pool.calls[0]
    assert params["investigation_inputs_store"] == {"observable": [1.0, 2.0]}
    assert params["source"] == f"investigation:{inv}"


def test_investigation_no_runs_db_records_note_and_no_store(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)
    inv = "inv-x"  # no runs.db created
    pool = _FakePool(reply={"ok": True, "html": "<b>x</b>",
                            "source_used": "demo", "notes": ""})
    _patch_pool(monkeypatch, pool)

    body, status = views.visualization_preview(
        ws, {"address": "local:FakeViz", "source": f"investigation:{inv}"})
    assert status == 200
    _method, params = pool.calls[0]
    assert params["investigation_inputs_store"] is None
    assert any("has no runs.db; falling back to demo" in n
               for n in params["note_prefix"])


def test_investigation_assemble_exception_records_note_and_no_store(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)
    inv = "inv-x"
    inv_dir = study_spec.study_dir(ws, inv)
    inv_dir.mkdir(parents=True, exist_ok=True)
    (inv_dir / "runs.db").write_text("", encoding="utf-8")

    from vivarium_workbench.lib import investigations

    def _boom(db_path):
        raise RuntimeError("gather failed")

    monkeypatch.setattr(investigations, "gather_emitter_outputs", _boom)

    pool = _FakePool(reply={"ok": True, "html": "<b>x</b>",
                            "source_used": "demo", "notes": ""})
    _patch_pool(monkeypatch, pool)

    body, status = views.visualization_preview(
        ws, {"address": "local:FakeViz", "source": f"investigation:{inv}"})
    assert status == 200
    _method, params = pool.calls[0]
    assert params["investigation_inputs_store"] is None
    assert any("investigation render failed (RuntimeError: gather failed)" in n
               for n in params["note_prefix"])
