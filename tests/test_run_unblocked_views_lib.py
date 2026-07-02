"""Behavioural tests for the pure ``lib.run_unblocked_views`` builder (P5).

Port parity for ``server.Handler._post_investigation_run_unblocked``: every
external is monkeypatched on the lib module — ``manager.submit`` (capture the
worker), ``enumerate_unblocked`` (canned plan), and the E4/E5 orchestrators
(``study_runs`` / ``comparative_runs``) — so NO real sim ever runs.

The captured worker is then driven against a fake job to assert the
baseline/variant dispatch, the done/failed item updates, the BaseException
path, and the trailing comparative-renderer call.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vivarium_workbench.lib import run_unblocked_views as ruv


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_ws(tmp_path: Path, inv_slug: str, members, study_files=None) -> Path:
    """Create a tmp ws_root with an investigation.yaml listing ``members`` and a
    study.yaml per member (unless ``study_files`` overrides the text)."""
    inv_dir = tmp_path / "investigations" / inv_slug
    inv_dir.mkdir(parents=True)
    members_yaml = "\n".join(f"  - {m}" for m in members)
    (inv_dir / "investigation.yaml").write_text(
        f"studies:\n{members_yaml}\n", encoding="utf-8")
    study_files = study_files or {}
    for m in members:
        text = study_files.get(m, "baseline: []\n")
        if text is None:
            continue  # caller wants this study.yaml ABSENT (skipped path)
        sdir = tmp_path / "studies" / m
        sdir.mkdir(parents=True)
        (sdir / "study.yaml").write_text(text, encoding="utf-8")
    return tmp_path


class _FakeJob:
    """Minimal RunJob stand-in for driving the worker."""

    def __init__(self, items):
        self.job_id = "JOB123"
        self.items = items
        self.updates = []

    def update_item(self, idx, **fields):
        self.updates.append((idx, dict(fields)))
        if 0 <= idx < len(self.items):
            self.items[idx].update(fields)


# ---------------------------------------------------------------------------
# Validation / status parity
# ---------------------------------------------------------------------------

def test_missing_investigation_400():
    body, code = ruv.investigation_run_unblocked(Path("/nope"), {})
    assert code == 400
    assert body == {"error": "investigation is required"}


def test_blank_investigation_400():
    body, code = ruv.investigation_run_unblocked(Path("/nope"), {"investigation": "   "})
    assert code == 400
    assert body == {"error": "investigation is required"}


def test_investigation_not_found_404(tmp_path):
    body, code = ruv.investigation_run_unblocked(tmp_path, {"investigation": "ghost"})
    assert code == 404
    assert body == {"error": "investigation not found: ghost"}


def test_yaml_parse_failure_500(tmp_path):
    inv_dir = tmp_path / "investigations" / "inv-x"
    inv_dir.mkdir(parents=True)
    (inv_dir / "investigation.yaml").write_text("studies: [\n  - bad: : :\n", encoding="utf-8")
    body, code = ruv.investigation_run_unblocked(tmp_path, {"investigation": "inv-x"})
    assert code == 500
    assert body["error"].startswith("yaml parse failed:")


# ---------------------------------------------------------------------------
# Enumeration + submit (happy path)
# ---------------------------------------------------------------------------

def test_happy_path_202_submits_and_returns_items(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path, "inv-h", ["study-a"])

    runnable = [{"study": "study-a", "variant": "baseline", "kind": "baseline",
                 "status": "queued"}]
    blocked = [{"study": "study-a", "variant": "v-gated", "kind": "variant",
                "status": "blocked", "error": "gate unset"}]
    monkeypatch.setattr(ruv, "enumerate_unblocked", lambda spec: (runnable, blocked))

    captured = {}

    class _Job:
        job_id = "JZ"

    def _submit(inv_slug, items, worker_fn):
        captured["inv_slug"] = inv_slug
        captured["items"] = items
        captured["worker"] = worker_fn
        return _Job()

    monkeypatch.setattr(ruv.manager, "submit", _submit)

    body, code = ruv.investigation_run_unblocked(ws, {"investigation": "inv-h"})
    assert code == 202
    assert body["job_id"] == "JZ"
    assert body["items"] == runnable + blocked
    assert captured["inv_slug"] == "inv-h"
    assert captured["items"] == runnable + blocked
    assert callable(captured["worker"])


def test_studies_filter_narrows_members(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path, "inv-f", ["study-a", "study-b"])

    seen = []

    def _enum(spec):
        seen.append(spec)
        return ([{"study": "x", "variant": "baseline", "kind": "baseline",
                  "status": "queued"}], [])

    monkeypatch.setattr(ruv, "enumerate_unblocked", _enum)

    class _Job:
        job_id = "JF"

    monkeypatch.setattr(ruv.manager, "submit", lambda i, it, w: _Job())

    # Filter to a single study (string form) — only study-a enumerated.
    body, code = ruv.investigation_run_unblocked(
        ws, {"investigation": "inv-f", "studies": "study-a"})
    assert code == 202
    assert len(seen) == 1

    seen.clear()
    # List form.
    body, code = ruv.investigation_run_unblocked(
        ws, {"investigation": "inv-f", "studies": ["study-b"]})
    assert code == 202
    assert len(seen) == 1


def test_skipped_when_study_yaml_missing(tmp_path, monkeypatch):
    # study-b has NO study.yaml -> skipped item; study-a yields one queued.
    ws = _make_ws(tmp_path, "inv-s", ["study-a", "study-b"],
                  study_files={"study-a": "baseline: []\n", "study-b": None})

    monkeypatch.setattr(
        ruv, "enumerate_unblocked",
        lambda spec: ([{"study": "study-a", "variant": "baseline",
                        "kind": "baseline", "status": "queued"}], []))

    class _Job:
        job_id = "JS"

    captured = {}
    monkeypatch.setattr(ruv.manager, "submit",
                        lambda i, it, w: (captured.update(items=it), _Job())[1])

    body, code = ruv.investigation_run_unblocked(ws, {"investigation": "inv-s"})
    assert code == 202
    # queued item + the skipped study-b appended at the end.
    skipped = [it for it in body["items"] if it.get("status") == "skipped"]
    assert skipped == [{"study": "study-b", "variant": "?", "status": "skipped",
                        "error": "study.yaml not found"}]


# ---------------------------------------------------------------------------
# No-queued breakdown (400)
# ---------------------------------------------------------------------------

def test_no_queued_breakdown_400(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path, "inv-b", ["study-a"])
    # All blocked, nothing queued.
    blocked = [{"study": "study-a", "variant": "v1", "kind": "variant",
                "status": "blocked", "error": "gate unset"}]
    monkeypatch.setattr(ruv, "enumerate_unblocked", lambda spec: ([], blocked))

    # submit must NOT be called.
    monkeypatch.setattr(ruv.manager, "submit",
                        lambda *a, **k: pytest.fail("submit should not run"))

    body, code = ruv.investigation_run_unblocked(ws, {"investigation": "inv-b"})
    assert code == 400
    assert body["items"] == blocked
    assert body["error"] == (
        "no variants to queue (1 blocked). Each item's reason "
        "is in `items[].error` — see the per-item panel."
    )


def test_no_items_enumerated_breakdown_400(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path, "inv-e", ["study-a"])
    monkeypatch.setattr(ruv, "enumerate_unblocked", lambda spec: ([], []))
    monkeypatch.setattr(ruv.manager, "submit",
                        lambda *a, **k: pytest.fail("submit should not run"))
    body, code = ruv.investigation_run_unblocked(ws, {"investigation": "inv-e"})
    assert code == 400
    assert "no items enumerated" in body["error"]
    assert body["items"] == []


# ---------------------------------------------------------------------------
# Worker behaviour (drive the captured worker against a fake job)
# ---------------------------------------------------------------------------

def _capture_worker(ws, monkeypatch, items):
    """Run the builder with a canned plan; return (worker, render_calls)."""
    monkeypatch.setattr(ruv, "enumerate_unblocked", lambda spec: (items, []))
    captured = {}

    class _Job:
        job_id = "JW"

    monkeypatch.setattr(
        ruv.manager, "submit",
        lambda i, it, w: (captured.update(worker=w), _Job())[1])
    body, code = ruv.investigation_run_unblocked(ws, {"investigation": "inv-w"})
    assert code == 202
    return captured["worker"]


def test_worker_dispatches_baseline_and_variant(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path, "inv-w", ["study-a"])
    items = [
        {"study": "study-a", "variant": "baseline", "kind": "baseline",
         "status": "queued"},
        {"study": "study-a", "variant": "v1", "kind": "variant",
         "status": "queued"},
    ]

    base_calls, var_calls, render_calls = [], [], []

    def _base(ws_root, body):
        base_calls.append((ws_root, body))
        return {"run_id": "RB"}, 200

    def _var(ws_root, body):
        var_calls.append((ws_root, body))
        return {"run_id": "RV"}, 200

    def _render(ws_root, inv_slug, iset, job):
        render_calls.append((ws_root, inv_slug, iset, job))

    monkeypatch.setattr(ruv.study_runs, "run_study_baseline", _base)
    monkeypatch.setattr(ruv.study_runs, "run_study_variant", _var)
    monkeypatch.setattr(
        ruv.comparative_runs,
        "render_investigation_comparative_visualisations", _render)

    worker = _capture_worker(ws, monkeypatch, items)
    job = _FakeJob([dict(it) for it in items])
    worker(job)

    assert base_calls == [(ws, {"study": "study-a"})]
    assert var_calls == [(ws, {"study": "study-a", "variant": "v1"})]
    assert job.items[0]["status"] == "done" and job.items[0]["run_id"] == "RB"
    assert job.items[1]["status"] == "done" and job.items[1]["run_id"] == "RV"
    # Comparative renderer fired once with (ws_root, inv_slug, iset, job).
    assert len(render_calls) == 1
    assert render_calls[0][0] == ws
    assert render_calls[0][1] == "inv-w"
    assert isinstance(render_calls[0][2], dict)
    assert render_calls[0][3] is job


def test_worker_marks_failed_on_non_200(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path, "inv-w", ["study-a"])
    items = [{"study": "study-a", "variant": "baseline", "kind": "baseline",
              "status": "queued"}]

    monkeypatch.setattr(ruv.study_runs, "run_study_baseline",
                        lambda w, b: ({"error": "boom"}, 500))
    monkeypatch.setattr(
        ruv.comparative_runs,
        "render_investigation_comparative_visualisations",
        lambda *a, **k: None)

    worker = _capture_worker(ws, monkeypatch, items)
    job = _FakeJob([dict(it) for it in items])
    worker(job)
    assert job.items[0]["status"] == "failed"
    assert job.items[0]["error"] == "boom"


def test_worker_failed_default_http_message(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path, "inv-w", ["study-a"])
    items = [{"study": "study-a", "variant": "v1", "kind": "variant",
              "status": "queued"}]
    monkeypatch.setattr(ruv.study_runs, "run_study_variant",
                        lambda w, b: ({}, 503))
    monkeypatch.setattr(
        ruv.comparative_runs,
        "render_investigation_comparative_visualisations",
        lambda *a, **k: None)
    worker = _capture_worker(ws, monkeypatch, items)
    job = _FakeJob([dict(it) for it in items])
    worker(job)
    assert job.items[0]["status"] == "failed"
    assert job.items[0]["error"] == "HTTP 503"


def test_worker_baseexception_marks_failed(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path, "inv-w", ["study-a"])
    items = [{"study": "study-a", "variant": "baseline", "kind": "baseline",
              "status": "queued"}]

    def _boom(w, b):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(ruv.study_runs, "run_study_baseline", _boom)
    monkeypatch.setattr(
        ruv.comparative_runs,
        "render_investigation_comparative_visualisations",
        lambda *a, **k: None)
    worker = _capture_worker(ws, monkeypatch, items)
    job = _FakeJob([dict(it) for it in items])
    worker(job)
    assert job.items[0]["status"] == "failed"
    assert job.items[0]["error"] == "kaboom"


def test_worker_skips_non_queued_items(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path, "inv-w", ["study-a"])
    # Plan has a queued + a pre-blocked item; worker must skip the blocked one.
    items = [
        {"study": "study-a", "variant": "baseline", "kind": "baseline",
         "status": "queued"},
        {"study": "study-a", "variant": "v-blk", "kind": "variant",
         "status": "blocked"},
    ]
    base_calls = []
    monkeypatch.setattr(ruv.study_runs, "run_study_baseline",
                        lambda w, b: (base_calls.append(b), ({"run_id": "R"}, 200))[1])
    monkeypatch.setattr(
        ruv.comparative_runs,
        "render_investigation_comparative_visualisations",
        lambda *a, **k: None)
    # Need both queued+blocked enumerated -> override capture helper inline.
    monkeypatch.setattr(ruv, "enumerate_unblocked",
                        lambda spec: ([items[0]], [items[1]]))
    captured = {}

    class _Job:
        job_id = "JW"

    monkeypatch.setattr(ruv.manager, "submit",
                        lambda i, it, w: (captured.update(worker=w), _Job())[1])
    body, code = ruv.investigation_run_unblocked(ws, {"investigation": "inv-w"})
    assert code == 202
    job = _FakeJob([dict(it) for it in body["items"]])
    captured["worker"](job)
    assert len(base_calls) == 1
    assert job.items[1]["status"] == "blocked"  # untouched
