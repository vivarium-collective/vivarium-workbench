"""Layer-4 pull-or-compute trigger: converter + endpoint.

Exercises ``lib.investigation_composite`` (workspace investigation -> pbg
investigation document with ``_study`` metadata) and ``lib.investigation_trigger``
(wrapping ``process_bigraph.templates.trigger``), plus the live
``POST /api/investigation-trigger`` / ``GET /api/investigation-trigger-status``
endpoints.

The artifact store is pre-populated in-process with a cheap stub ``compute_fn``
(same pattern as ``tests/test_investigation_resolve_api.py``) so the "cached
upstream is pulled, not recomputed" scenarios never invoke a real engine, and
every endpoint test uses ``launch=false`` so no detached run is spawned — the
trigger *plan/report* is what Layer-4 exposes.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest
import yaml

from vivarium_workbench.lib.artifacts.pipeline import resolve_study
from vivarium_workbench.lib.investigation_composite import (
    STUDY_META,
    build_investigation_document,
    node_cache_status,
)
from vivarium_workbench.lib.investigation_trigger import investigation_trigger


def _write_study(ws_root, slug, *, inputs_from=(), config=None, composite=None):
    d = ws_root / "studies" / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "study.yaml").write_text(yaml.safe_dump({
        "name": slug,
        "composite": composite if composite is not None else f"c.{slug}",
        "config": config if config is not None else {},
        "inputs": [{"artifact": f"{f}_out", "from": f} for f in inputs_from],
        "outputs": [f"{slug}_out"],
    }))


def _make_stub():
    def stub(ws_root, slug, *, artifact_id, composite, config, input_ids, out_dir,
             resolved_inputs=None):
        out_dir.mkdir(parents=True, exist_ok=True)
        p = out_dir / "out.bin"
        p.write_bytes(b"fake:" + slug.encode())
        return p
    return stub


def _diamond(tmp_path):
    """parca -> a, parca -> b, {a, b} -> c, all declared inv1 members."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: test-ws\n")
    _write_study(ws, "parca")
    _write_study(ws, "a", inputs_from=["parca"])
    _write_study(ws, "b", inputs_from=["parca"])
    _write_study(ws, "c", inputs_from=["a", "b"])
    inv_dir = ws / "investigations" / "inv1"
    inv_dir.mkdir(parents=True, exist_ok=True)
    (inv_dir / "investigation.yaml").write_text(yaml.safe_dump({
        "name": "inv1", "members": ["parca", "a", "b", "c"],
    }))
    return ws


@pytest.fixture
def diamond_ws(tmp_path):
    return _diamond(tmp_path)


# --------------------------------------------------------------------------
# Converter (in-process)
# --------------------------------------------------------------------------

def test_build_document_shape(diamond_ws):
    doc = build_investigation_document(diamond_ws, "inv1")
    assert set(doc) == {"parca", "a", "b", "c"}
    # Each region carries _study metadata + a placeholder sim step node.
    a = doc["a"]
    assert a["sim"]["_type"] == "step"
    assert a["sim"]["outputs"] == {"results": ["results"]}
    meta = a[STUDY_META]
    # id is the composite id (matches resolve_study's composite_id), NOT the slug.
    assert meta["id"] == "c.a"
    assert meta["inputs"] == [{"artifact": "parca_out", "from": "parca"}]
    # Producer with no inputs is a leaf; its declared output sets its kind.
    assert doc["parca"][STUDY_META]["inputs"] == []
    assert doc["parca"][STUDY_META]["kind"] == "parca_out"
    # Consumer of two producers keeps both edges.
    assert {e["from"] for e in doc["c"][STUDY_META]["inputs"]} == {"a", "b"}


def test_unresolvable_edge_is_dropped(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: t\n")
    # 'x' declares an input from 'ghost', which has no study.yaml.
    _write_study(ws, "x", inputs_from=["ghost"])
    inv = ws / "investigations" / "inv1"
    inv.mkdir(parents=True)
    (inv / "investigation.yaml").write_text(yaml.safe_dump(
        {"name": "inv1", "members": ["x"]}))
    doc = build_investigation_document(ws, "inv1")
    assert set(doc) == {"x"}  # ghost never became a member
    assert doc["x"][STUDY_META]["inputs"] == []  # dangling edge pruned


def test_addressing_matches_resolve_study(diamond_ws):
    """The converter's addresses must equal the pipeline's stored ids — that
    lock-step is what lets trigger find pipeline-produced artifacts."""
    from process_bigraph.templates import study_address
    from vivarium_workbench.lib.investigation_composite import workspace_commit

    r = resolve_study(diamond_ws, "a", compute_fn=_make_stub())  # seeds parca + a
    doc = build_investigation_document(diamond_ws, "inv1")
    commit = workspace_commit(diamond_ws)
    assert study_address(doc, "parca", commit=commit) == r["inputs"]["parca"]
    assert study_address(doc, "a", commit=commit) == r["artifact_id"]


def test_node_cache_status_reflects_store(diamond_ws):
    before = {n["slug"]: n["cached"] for n in
              node_cache_status(diamond_ws, "inv1")["nodes"]}
    assert before == {"parca": False, "a": False, "b": False, "c": False}

    resolve_study(diamond_ws, "a", compute_fn=_make_stub())  # caches parca + a only
    after = {n["slug"]: n["cached"] for n in
             node_cache_status(diamond_ws, "inv1")["nodes"]}
    assert after["parca"] is True and after["a"] is True
    assert after["b"] is False and after["c"] is False


# --------------------------------------------------------------------------
# Trigger logic (in-process, launch=False)
# --------------------------------------------------------------------------

def test_trigger_pulls_cached_upstream(diamond_ws):
    resolve_study(diamond_ws, "parca", compute_fn=_make_stub())  # cache upstream
    body, status = investigation_trigger(diamond_ws, {
        "investigation": "inv1", "target_study": "a", "launch": False})
    assert status == 200, body
    report = body["report"]
    assert report["target"] == "a"
    assert report["pulled"] == ["parca"]           # cached upstream pulled
    assert report["computed"] == ["a"]             # target computes
    assert set(report["pruned"]) == {"b", "c"}     # non-ancestors pruned
    assert "run" not in body                        # launch=False -> no run


def test_trigger_downstream_reuses_whole_chain(diamond_ws):
    # Cache the whole upstream chain, then continue from the sink 'c'.
    resolve_study(diamond_ws, "c", compute_fn=_make_stub())
    body, status = investigation_trigger(diamond_ws, {
        "investigation": "inv1", "target_study": "c", "launch": False})
    assert status == 200, body
    report = body["report"]
    assert set(report["pulled"]) == {"parca", "a", "b"}
    assert report["computed"] == ["c"]
    assert report["pruned"] == []


def test_trigger_error_on_uncached_prerequisite(diamond_ws):
    # Nothing cached: continuing from 'a' under on_missing='error' must refuse
    # and name the uncached prerequisite (parca).
    body, status = investigation_trigger(diamond_ws, {
        "investigation": "inv1", "target_study": "a",
        "on_missing": "error", "launch": False})
    assert status == 409, body
    assert "parca" in body["error"]


def test_trigger_compute_missing_upstream(diamond_ws):
    body, status = investigation_trigger(diamond_ws, {
        "investigation": "inv1", "target_study": "a",
        "on_missing": "compute", "launch": False})
    assert status == 200, body
    report = body["report"]
    assert "parca" in report["computed"]  # uncached upstream now computes
    assert "a" in report["computed"]
    assert report["pulled"] == []


def test_trigger_unknown_target_404(diamond_ws):
    _body, status = investigation_trigger(diamond_ws, {
        "investigation": "inv1", "target_study": "nope", "launch": False})
    assert status == 404


def test_trigger_unknown_investigation_404(diamond_ws):
    _body, status = investigation_trigger(diamond_ws, {
        "investigation": "ghost", "target_study": "a", "launch": False})
    assert status == 404


def test_trigger_launch_keeps_the_refusal_of_a_named_backend_that_cannot_be_reached(
        diamond_ws, tmp_path, monkeypatch):
    """The launch is the Composites run: when the backend named for this workbench cannot be asked it is refused
    (502 ``unreachable``). The trigger report still succeeds, and carries the refusal as the launch's own status and
    body rather than as an anonymous 500 "launch failed"."""
    from vivarium_workbench.lib import server_capabilities as sc

    # The probe is an SmsApiClient call, which reads the GitHub login and caches it for the whole process: keep the
    # developer's real one out (the other backend tests do the same), or later tests inherit it.
    home = tmp_path / "no-login-home"
    home.mkdir()
    for k in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN",
              "VIVARIUM_WORKBENCH_GH_TOKEN", "VIVARIUM_DASHBOARD_GH_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("GH_CONFIG_DIR", str(home / ".config" / "gh"))
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")
    monkeypatch.setenv("VIVARIUM_WORKBENCH_BACKEND_BASE_URL", "http://127.0.0.1:9")
    for v in ("VIVA_API_BASE", "SMS_API_BASE"):
        monkeypatch.delenv(v, raising=False)
    sc._PROFILE_CACHE.clear()
    try:
        body, status = investigation_trigger(diamond_ws, {
            "investigation": "inv1", "target_study": "parca", "on_missing": "compute", "launch": True})
    finally:
        sc._PROFILE_CACHE.clear()
    assert status == 200  # the report
    assert body["launch_status"] == 502
    run = body["run"]
    assert (run["phase"], run["reachable"], run["reason"], run["backend"]) == (
        "unreachable", False, "backend unreachable", "http://127.0.0.1:9")
    assert "not started locally" in run["error"]


# --------------------------------------------------------------------------
# Live endpoint
# --------------------------------------------------------------------------

def _post_with_origin(base_url, path, body, *, origin):
    req = urllib.request.Request(
        base_url + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Origin": origin},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def test_endpoint_trigger_cached_upstream_pulled(dashboard_client, diamond_ws):
    resolve_study(diamond_ws, "parca", compute_fn=_make_stub())  # seed before serve
    client = dashboard_client(workspace=diamond_ws)
    r = client.post("/api/investigation-trigger", json={
        "investigation": "inv1", "target_study": "a", "launch": False})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["target"] == "a"
    assert body["report"]["pulled"] == ["parca"]
    assert body["report"]["computed"] == ["a"]


def test_endpoint_trigger_status_badges(dashboard_client, diamond_ws):
    resolve_study(diamond_ws, "a", compute_fn=_make_stub())  # caches parca + a
    client = dashboard_client(workspace=diamond_ws)
    r = client.get("/api/investigation-trigger-status?investigation=inv1")
    assert r.status_code == 200, r.text
    nodes = {n["slug"]: n for n in r.json()["nodes"]}
    assert set(nodes) == {"parca", "a", "b", "c"}
    assert nodes["parca"]["cached"] is True
    assert nodes["a"]["cached"] is True
    assert nodes["c"]["cached"] is False
    assert nodes["parca"]["artifact_id"]


def test_endpoint_trigger_uncached_prereq_409(dashboard_client, diamond_ws):
    client = dashboard_client(workspace=diamond_ws)
    r = client.post("/api/investigation-trigger", json={
        "investigation": "inv1", "target_study": "a",
        "on_missing": "error", "launch": False})
    assert r.status_code == 409, r.text
    assert "parca" in r.json()["error"]


def test_endpoint_trigger_cross_origin_rejected(dashboard_client, diamond_ws):
    client = dashboard_client(workspace=diamond_ws)
    code = _post_with_origin(
        client.base_url, "/api/investigation-trigger",
        {"investigation": "inv1", "target_study": "a", "launch": False},
        origin="http://evil.example.com")
    assert code in (400, 403)
