"""Live endpoint: POST /api/investigation-resolve.

Task 6 — opt-in topological pull-or-compute over ``lib.artifacts.pipeline.
resolve_investigation``, exposed via a NEW ``lib.investigation_resolve_views``
(the declared-order ``/api/investigation-rerun`` stays untouched). Builds a
tiny 4-study "diamond" workspace (flat pipeline schema, same shape as
``tests/test_resolve_investigation.py``'s fixture) directly under ``tmp_path``
— no heavy real-engine composite run is needed: the artifact store is
pre-populated in-process (a cheap stub ``compute_fn``) before the live server
subprocess is spawned, so the endpoint's own (real-adapter) resolve call hits
an all-cached store and never invokes the engine.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest
import yaml


def _write_study(ws_root, slug, *, inputs_from=(), config=None):
    d = ws_root / "studies" / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "study.yaml").write_text(yaml.safe_dump({
        "name": slug,
        "composite": f"c.{slug}",
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


@pytest.fixture
def diamond_ws(tmp_path):
    """parca -> a, parca -> b, {a, b} -> c, all declared as inv1 members.

    Mirrors ``tests/test_resolve_investigation.py``'s ``diamond`` fixture but
    lives under a real (minimal) workspace root — ``workspace.yaml`` present
    so the live ``dashboard_client`` subprocess will mount it (same minimal
    shape as ``tests/test_study_tests_endpoint.py``).
    """
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
        "name": "inv1",
        "members": ["parca", "a", "b", "c"],
    }))
    return ws


def _post_with_origin(base_url: str, path: str, body: dict, *, origin: str) -> int:
    """POST with an explicit cross-origin ``Origin`` header against a real bound
    socket (the in-process client has no reachable base_url; this must exercise
    the real CSRF/Origin guard — see test_csrf_origin_guard.py). The cross-origin
    test below therefore keeps the subprocess ``dashboard_client``."""
    req = urllib.request.Request(
        base_url + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Origin": origin},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def test_investigation_resolve_unknown_investigation_returns_error_200(
    inproc_client, diamond_ws,
):
    client = inproc_client(workspace=diamond_ws)
    r = client.post("/api/investigation-resolve", json={"investigation": "nope"})
    assert r.status_code == 200
    body = r.json()
    assert body["investigation"] == "nope"
    assert body["order"] == []
    assert body["nodes"] == []
    assert body["error"]


def test_investigation_resolve_all_cached_returns_structured_result(
    inproc_client, diamond_ws,
):
    # Pre-populate the artifact store in-process (cheap stub compute_fn) so
    # the live server's own (real-adapter) resolve call is an all-cache-hit
    # walk — never invokes the engine.
    from vivarium_workbench.lib.artifacts.pipeline import resolve_investigation
    pre = resolve_investigation(diamond_ws, "inv1", compute_fn=_make_stub())
    assert pre["error"] is None
    assert {n["status"] for n in pre["nodes"]} == {"computed"}

    client = inproc_client(workspace=diamond_ws)
    r = client.post("/api/investigation-resolve", json={"investigation": "inv1"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["investigation"] == "inv1"
    assert body["error"] is None
    order = body["order"]
    assert order.index("parca") < order.index("a")
    assert order.index("parca") < order.index("b")
    assert order.index("a") < order.index("c")
    assert order.index("b") < order.index("c")

    nodes = {n["slug"]: n for n in body["nodes"]}
    assert set(nodes) == {"parca", "a", "b", "c"}
    assert all(n["status"] == "cached" for n in nodes.values())
    assert nodes["c"]["inputs"] == ["a", "b"]
    assert nodes["parca"]["artifact_id"]


def test_investigation_resolve_cross_origin_is_rejected(dashboard_client, diamond_ws):
    client = dashboard_client(workspace=diamond_ws)
    code = _post_with_origin(
        client.base_url, "/api/investigation-resolve", {"investigation": "inv1"},
        origin="http://evil.example.com")
    assert code in (400, 403)
