"""Narrative-audit gaps surface in the Needs-Attention scan (RFC-0005).

An unsupported transition between adjacent claims in the derived narrative is a
deterministic finding — it belongs in the existing needs-attention scan (the
chip/popover), not a front-of-page block. `scan_investigation` gains a
`narrative_gap` signal fed by viva_superpowers' derive_argument + narrate + audit.

Skips when the viva_superpowers derivation layer isn't importable (the scan
degrades to omitting the signal); runs locally with it on the path.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("discovery_engine.argument_store")
pytest.importorskip("discovery_engine.narrative")

from discovery_engine import study_io  # noqa: E402
from vivarium_workbench.lib.needs_attention import scan_investigation  # noqa: E402


def _write(path: Path, spec: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    study_io.save_yaml_atomic(path, spec)


def _ws_two_unlinked_claims(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir(parents=True, exist_ok=True)
    _write(root / "workspace.yaml", {"name": "ws", "package_path": "pbg_ws"})
    _write(root / "investigations" / "inv" / "investigation.yaml",
           {"name": "inv", "studies": ["s-a", "s-b"], "question": "Why?"})
    # Two independent supported claims with NO parent_relations edge between them:
    # the narrative orders them adjacent (by id) with no supporting path -> a gap.
    for slug in ("s-a", "s-b"):
        _write(root / "studies" / slug / "study.yaml",
               {"name": slug, "schema_version": 4, "title": slug,
                "claim": f"{slug} holds", "verdict": "supported", "investigation": "inv"})
    return root


def test_narrative_gap_becomes_a_needs_attention_item(tmp_path, monkeypatch):
    # The scan derives its argument graph via discovery_engine.derive_argument,
    # which only ever emits supporting (prerequisite/support) or challenge edges —
    # so a unit↔unit narrative gap (which audit() raises ONLY for a DECLARED,
    # non-supporting, non-challenge edge between adjacent claims) cannot arise from
    # a plain two-study workspace. (This is the engine's post-extraction "don't
    # over-report on a wide DAG" behavior: bare unlinked claims no longer qualify.)
    # To exercise the audit → scan → needs-attention WIRING on a genuine gap, inject
    # an argument graph carrying such an edge: two adjacent claims joined by a
    # declared "association" edge with no supporting path between them. Whether the
    # scan should additionally SURFACE such gaps from real workspaces (i.e. derive
    # non-supporting edges, or merge an authored argument) is the open product
    # question tracked in #1499.
    root = _ws_two_unlinked_claims(tmp_path)

    import discovery_engine.argument_store as _argstore

    def _graph_with_declared_gap(_inv_spec, _studies_by_slug):
        return {
            "claims": {
                "claim:s-a": {"proposition": "A holds", "verdict": "supported"},
                "claim:s-b": {"proposition": "B holds", "verdict": "supported"},
            },
            # a declared edge that is neither supporting nor a challenge → the
            # reading asserts a transition with no justifying link: a gap.
            "edges": [{"from": "claim:s-a", "to": "claim:s-b",
                       "type": "association", "relation": "authored"}],
        }

    # _narrative_gap_items does `from discovery_engine.argument_store import
    # derive_argument` at call time, so patching the source attribute takes effect.
    monkeypatch.setattr(_argstore, "derive_argument", _graph_with_declared_gap)

    d = scan_investigation(root, "inv")
    gaps = [it for it in d["items"] if it["kind"] == "narrative_gap"]
    assert gaps, "a declared unsupported transition should surface as a needs-attention item"
    g = gaps[0]
    assert g["severity"] in ("high", "medium")
    assert "→" in g["ref"]          # "from→to"
    assert g["title"] and g["action_hint"]
    # it counts toward the scan summary like any other signal
    assert d["summary"]["by_kind"].get("narrative_gap") == len(gaps)


def test_well_supported_chain_yields_no_narrative_gap(tmp_path):
    root = tmp_path / "ws"
    root.mkdir(parents=True, exist_ok=True)
    _write(root / "workspace.yaml", {"name": "ws", "package_path": "pbg_ws"})
    _write(root / "investigations" / "inv" / "investigation.yaml",
           {"name": "inv", "studies": ["s-a", "s-b"], "question": "Why?"})
    _write(root / "studies" / "s-a" / "study.yaml",
           {"name": "s-a", "schema_version": 4, "title": "s-a", "claim": "a holds",
            "verdict": "supported", "investigation": "inv"})
    # s-b builds-on s-a -> a prerequisite edge a->b -> supported transition, no gap.
    _write(root / "studies" / "s-b" / "study.yaml",
           {"name": "s-b", "schema_version": 4, "title": "s-b", "claim": "b holds",
            "verdict": "supported", "investigation": "inv",
            "parent_relations": [{"study": "s-a", "relation": "builds-on"}]})
    d = scan_investigation(root, "inv")
    assert [it for it in d["items"] if it["kind"] == "narrative_gap"] == []
