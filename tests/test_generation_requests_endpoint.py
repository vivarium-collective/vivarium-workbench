"""HTTP endpoint test for GET /api/generation-requests/{slug}.

The Discovery-cycle panel fetches this to render the typed GenerationRequests the
frontier emits (AI-drivable cycle, Phase 1). It wraps
lib.investigation_report.build_generation_requests and always returns a JSON
object ({} on an engine without the generation contract), so the panel degrades
gracefully. Read-only — nothing is written.
"""
import yaml

_KINDS = {"propose_study", "acquire_model", "author_tests", "interpret_finding",
          "resolve_decision", "explain", "visualize"}


def test_generation_requests_endpoint_returns_typed_dict(tmp_path, inproc_client):
    (tmp_path / "workspace.yaml").write_text("name: test-ws\n")
    inv = tmp_path / "investigations" / "demo-inv"
    inv.mkdir(parents=True)
    (inv / "investigation.yaml").write_text(yaml.safe_dump({"name": "demo-inv", "studies": []}))
    client = inproc_client(workspace=tmp_path)
    r = client.get("/api/generation-requests/demo-inv")
    assert r.status_code == 200
    payload = r.json()
    assert isinstance(payload, dict)
    # With the generation contract installed, the payload carries the typed shape;
    # an empty investigation yields an empty request list, never an error.
    if payload:
        assert payload["investigation"] == "demo-inv"
        assert isinstance(payload["total"], int)
        assert set(payload["by_gate"]) <= {"auto", "review"}
        assert len(payload["requests"]) == payload["total"]
        for req in payload["requests"]:
            assert req["kind"] in _KINDS
            assert req["gate"] in {"auto", "review"}
            assert req["id"] and req["target"] and req["produces"]


def test_generation_gates_preset_persists_and_rejects_bad(tmp_path, inproc_client):
    (tmp_path / "workspace.yaml").write_text("name: test-ws\n")
    inv = tmp_path / "investigations" / "demo-inv"
    inv.mkdir(parents=True)
    (inv / "investigation.yaml").write_text(yaml.safe_dump({"name": "demo-inv", "studies": []}))
    client = inproc_client(workspace=tmp_path)

    # a one-click preset is persisted to discovery.gates and echoed back
    r = client.post("/api/generation-gates/demo-inv", json={"preset": "free_run"})
    assert r.status_code == 200, r.text
    assert r.json()["default"] == "auto"
    written = yaml.safe_load((inv / "investigation.yaml").read_text())
    assert written["discovery"]["gates"]["default"] == "auto"

    # and the requests endpoint now reflects the persisted config
    g = client.get("/api/generation-requests/demo-inv").json()
    if g:
        assert g["gates"]["default"] == "auto"

    # an unknown preset is a 400, not a write
    bad = client.post("/api/generation-gates/demo-inv", json={"preset": "nope"})
    assert bad.status_code == 400

    # an explicit per-kind gate round-trips
    r2 = client.post("/api/generation-gates/demo-inv",
                     json={"default": "review", "by_kind": {"author_tests": "auto"}})
    assert r2.status_code == 200
    assert r2.json()["by_kind"]["author_tests"] == "auto"
    # an invalid gate value is rejected
    assert client.post("/api/generation-gates/demo-inv",
                       json={"by_kind": {"author_tests": "maybe"}}).status_code == 400
