"""build_argument_overlay — the server-side Argument/Narrative projection
(RFC-0005 N+3 render backend).

Loads an investigation + its member study specs, calls viva_superpowers'
derive_argument + narrate + audit (injectable here for determinism), and returns
{investigation, claims, edges, narrative, gaps, available}. Read-only and
tolerant: unknown investigation 404s; when the viva_superpowers derivation isn't
importable it degrades gracefully to an empty, available=False payload with 200.
"""
import yaml
from pathlib import Path

from vivarium_workbench.lib.investigation_graph_views import build_argument_overlay


def _ws(tmp_path: Path) -> Path:
    inv = tmp_path / "investigations" / "inv1"
    inv.mkdir(parents=True)
    (inv / "investigation.yaml").write_text(yaml.dump({
        "schema_version": 2, "name": "inv1", "question": "Why?",
        "studies": ["s-parent", "s-child"],
    }))
    for slug in ("s-parent", "s-child"):
        d = tmp_path / "studies" / slug
        d.mkdir(parents=True)
        (d / "study.yaml").write_text(yaml.dump({
            "schema_version": 4, "name": slug, "title": slug.upper(),
            "claim": f"{slug} holds", "verdict": "supported",
        }))
    (tmp_path / "workspace.yaml").write_text(yaml.dump({"schema_version": 2, "name": "w"}))
    return tmp_path


# --- injectable stubs so the test doesn't depend on viva_superpowers -------

def _derive(inv_spec, studies_by_slug):
    claims = {f"claim:{s}": {"proposition": spec.get("claim"), "verdict": "supported",
                             "evidence_refs": []}
              for s, spec in studies_by_slug.items()}
    return {"claims": claims, "edges": []}


def _narrate(graph, question=None, audience=None):
    return {"question": question, "audience": audience,
            "units": [{"claim_id": c} for c in sorted(graph["claims"])],
            "limitations": [], "open_questions": []}


def _audit(narrative, graph):
    return [{"from": "claim:s-child", "to": "claim:s-parent",
             "followup": {"id": "discriminate-x", "title": "t", "motivation": "m",
                          "parent_study": "s-parent"}}]


class TestBuildArgumentOverlay:
    def test_unknown_investigation_404(self, tmp_path):
        payload, code = build_argument_overlay(_ws(tmp_path), "ghost")
        assert code == 404

    def test_wires_investigation_and_studies_through_derivation(self, tmp_path):
        payload, code = build_argument_overlay(
            _ws(tmp_path), "inv1",
            derive_fn=_derive, narrate_fn=_narrate, audit_fn=_audit)
        assert code == 200
        assert payload["available"] is True
        assert payload["investigation"] == "inv1"
        assert set(payload["claims"]) == {"claim:s-parent", "claim:s-child"}
        assert payload["narrative"]["question"] == "Why?"       # investigation question carried
        assert len(payload["narrative"]["units"]) == 2
        assert payload["gaps"][0]["followup"]["id"] == "discriminate-x"

    def test_graceful_degrade_when_derivation_raises(self, tmp_path):
        def _boom(*a, **k):
            raise RuntimeError("no viva_superpowers")
        payload, code = build_argument_overlay(
            _ws(tmp_path), "inv1", derive_fn=_boom, narrate_fn=_narrate, audit_fn=_audit)
        assert code == 200
        assert payload["available"] is False
        assert payload["claims"] == {} and payload["gaps"] == []


class TestArgumentOverlayRoute:
    """The /api/argument-overlay endpoint — thin wire over build_argument_overlay.
    Robust to whether the viva_superpowers derivation layer is importable in the
    test venv: it asserts the stable shape, not the derived content."""

    def _client(self, ws):
        import pytest
        from fastapi.testclient import TestClient
        from vivarium_workbench.api.app import create_app, get_workspace
        app = create_app()
        app.dependency_overrides[get_workspace] = lambda: ws
        return TestClient(app)

    def test_route_200_shape(self, tmp_path):
        r = self._client(_ws(tmp_path)).get("/api/argument-overlay?investigation=inv1")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["investigation"] == "inv1"
        assert {"claims", "edges", "narrative", "gaps", "available"} <= set(body)
        assert isinstance(body["available"], bool)

    def test_route_unknown_404(self, tmp_path):
        r = self._client(_ws(tmp_path)).get("/api/argument-overlay?investigation=nope")
        assert r.status_code == 404
