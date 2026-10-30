"""Tests for the Phase 2.1e cite-bands view builders
(``lib.cite_bands_views``) backing the ``/viva-cite-bands`` skill.

Phase 2.1e (rewire-first): these endpoints wrap
``discovery_engine.band_provenance`` / ``citation_gaps`` / ``expert_search``
unchanged — the plugin still computes, only the caller (the workbench, on
behalf of the skill) moves. These tests exercise the lib builders directly
(the same "endpoint test calls the lib fn" idiom as
``test_study_readout_migrate_endpoint.py`` / ``test_needs_attention_endpoint.py``)
plus equivalence checks against calling the plugin functions directly, since
the rewire must not drift from the plugin's own behavior (esp. the WRITE
path, which must also be idempotent).
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from vivarium_workbench.lib import cite_bands_views

# Mirrors the DnaA-ATP fraction band example used throughout the skill docs.
STUDY_YAML_TEXT = """\
# A hand-authored study with comments that MUST survive a provenance write.
name: dnaa-2
objective: |
  Multi-line objective prose.
behavior_tests:
  - name: frac-test
    pass_if:
      low: 0.2
      high: 0.5
  - name: already-cited-test
    pass_if:
      low: 1.0
      high: 2.0
    cites: [Existing2020]
  - name: no-band-test
    pass_if: {}
readouts:
  - name: prose-readout
    notes: "expect roughly [300, 800] molecules/cell"
# trailing comment
baselines: []
"""


def _study_ws(tmp_path: Path, slug: str = "dnaa-2") -> "tuple[Path, Path]":
    ws = tmp_path / "ws"
    sd = ws / "studies" / slug
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: cite-bands-test\n")
    (ws / ".pbg").mkdir()
    sy = sd / "study.yaml"
    sy.write_text(STUDY_YAML_TEXT)
    return ws, sy


# ---------------------------------------------------------------------------
# GET /api/band-provenance
# ---------------------------------------------------------------------------

def test_band_provenance_missing_study_400(tmp_path):
    ws, _ = _study_ws(tmp_path)
    body, status = cite_bands_views.build_band_provenance_missing(ws, None)
    assert status == 400
    assert "study" in body["error"]

    body2, status2 = cite_bands_views.build_band_provenance_missing(ws, "  ")
    assert status2 == 400


def test_band_provenance_unknown_study_404(tmp_path):
    ws, _ = _study_ws(tmp_path)
    body, status = cite_bands_views.build_band_provenance_missing(ws, "nope")
    assert status == 404
    assert "nope" in body["error"]


def test_band_provenance_missing_lists_uncited_bands(tmp_path):
    ws, _ = _study_ws(tmp_path)
    body, status = cite_bands_views.build_band_provenance_missing(ws, "dnaa-2")
    assert status == 200
    assert body["study"] == "dnaa-2"
    names = {e["name"] for e in body["missing"]}
    # frac-test (band, no cites) and prose-readout (prose band, no cites)
    # are missing; already-cited-test and no-band-test are not.
    assert names == {"frac-test", "prose-readout"}


def test_band_provenance_missing_equivalence_with_plugin(tmp_path):
    pytest.importorskip("discovery_engine.band_provenance")
    from discovery_engine.band_provenance import bands_missing_provenance

    ws, sy = _study_ws(tmp_path)
    spec = yaml.safe_load(sy.read_text())

    endpoint_body, status = cite_bands_views.build_band_provenance_missing(ws, "dnaa-2")
    assert status == 200
    direct = bands_missing_provenance(spec)
    assert endpoint_body["missing"] == direct


# ---------------------------------------------------------------------------
# POST /api/band-provenance
# ---------------------------------------------------------------------------

def test_write_band_provenance_missing_study_400(tmp_path):
    ws, _ = _study_ws(tmp_path)
    body, status = cite_bands_views.write_band_provenance(
        ws, {"test_name": "frac-test", "cites": ["Boesen2024"]}
    )
    assert status == 400
    assert "study" in body["error"]


def test_write_band_provenance_missing_test_name_400(tmp_path):
    ws, _ = _study_ws(tmp_path)
    body, status = cite_bands_views.write_band_provenance(
        ws, {"study": "dnaa-2", "cites": ["Boesen2024"]}
    )
    assert status == 400
    assert "test_name" in body["error"]


@pytest.mark.parametrize("cites", [None, [], "Boesen2024", [123], [""]])
def test_write_band_provenance_invalid_cites_400(tmp_path, cites):
    ws, _ = _study_ws(tmp_path)
    body, status = cite_bands_views.write_band_provenance(
        ws, {"study": "dnaa-2", "test_name": "frac-test", "cites": cites}
    )
    assert status == 400
    assert "cites" in body["error"]


def test_write_band_provenance_unknown_study_404(tmp_path):
    ws, _ = _study_ws(tmp_path)
    body, status = cite_bands_views.write_band_provenance(
        ws, {"study": "nope", "test_name": "frac-test", "cites": ["Boesen2024"]}
    )
    assert status == 404


def test_write_band_provenance_writes_and_reports(tmp_path):
    ws, sy = _study_ws(tmp_path)
    original = sy.read_text()

    body, status = cite_bands_views.write_band_provenance(
        ws,
        {
            "study": "dnaa-2",
            "test_name": "frac-test",
            "cites": ["Boesen2024"],
            "calibration_anchor": {"literature_target": 0.35, "cites": ["Boesen2024"]},
        },
    )

    assert status == 200
    assert body == {"study": "dnaa-2", "test_name": "frac-test", "written": True}
    text = sy.read_text()
    assert text != original
    assert "Boesen2024" in text
    # comments + hand-authored content survive the round-trip.
    assert "MUST survive a provenance write" in text
    assert "trailing comment" in text

    # The band is no longer missing.
    missing_body, _ = cite_bands_views.build_band_provenance_missing(ws, "dnaa-2")
    assert "frac-test" not in {e["name"] for e in missing_body["missing"]}


def test_write_band_provenance_idempotent(tmp_path):
    ws, sy = _study_ws(tmp_path)
    body1, status1 = cite_bands_views.write_band_provenance(
        ws, {"study": "dnaa-2", "test_name": "frac-test", "cites": ["Boesen2024"]}
    )
    assert status1 == 200 and body1["written"] is True
    text_after_first = sy.read_text()

    body2, status2 = cite_bands_views.write_band_provenance(
        ws, {"study": "dnaa-2", "test_name": "frac-test", "cites": ["Boesen2024"]}
    )
    assert status2 == 200
    assert body2["written"] is False  # no-op — identical
    assert sy.read_text() == text_after_first  # byte-identical, no rewrite


def test_write_band_provenance_never_fabricates_entry(tmp_path):
    ws, sy = _study_ws(tmp_path)
    original = sy.read_text()

    body, status = cite_bands_views.write_band_provenance(
        ws, {"study": "dnaa-2", "test_name": "does-not-exist", "cites": ["Boesen2024"]}
    )
    assert status == 200
    assert body["written"] is False
    assert sy.read_text() == original  # untouched — never fabricates


def test_write_band_provenance_equivalence_with_plugin(tmp_path):
    pytest.importorskip("discovery_engine.band_provenance")
    from discovery_engine.band_provenance import set_band_provenance

    ws1, sy1 = _study_ws(tmp_path / "endpoint")
    ws2, sy2 = _study_ws(tmp_path / "direct")

    endpoint_body, status = cite_bands_views.write_band_provenance(
        ws1, {"study": "dnaa-2", "test_name": "frac-test", "cites": ["Boesen2024"]}
    )
    assert status == 200
    direct_written = set_band_provenance(
        sy2.parent, test_name="frac-test", cites=["Boesen2024"]
    )
    assert endpoint_body["written"] == direct_written
    # Same net text mutation from equivalent starting files.
    assert sy1.read_text() == sy2.read_text()


# ---------------------------------------------------------------------------
# GET /api/citation-gaps
# ---------------------------------------------------------------------------

def _inv_ws(tmp_path):
    ws = tmp_path / "ws"
    inv_dir = ws / "investigations" / "dnaa-inv"
    inv_dir.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: cite-bands-test\n")
    inv_dir.joinpath("investigation.yaml").write_text(yaml.safe_dump({
        "name": "dnaa-inv",
        "studies": ["dnaa-2"],
        "inputs": {"references": ["Boesen2024", "Other1999"]},
    }))
    sd = ws / "studies" / "dnaa-2"
    sd.mkdir(parents=True)
    sd.joinpath("study.yaml").write_text(STUDY_YAML_TEXT)
    return ws


def test_citation_gaps_missing_investigation_400(tmp_path):
    ws = _inv_ws(tmp_path)
    body, status = cite_bands_views.build_citation_gaps(ws, None)
    assert status == 400


def test_citation_gaps_unknown_investigation_404(tmp_path):
    ws = _inv_ws(tmp_path)
    body, status = cite_bands_views.build_citation_gaps(ws, "nope")
    assert status == 404


def test_citation_gaps_surfaces_uncited_bands_and_references(tmp_path):
    ws = _inv_ws(tmp_path)
    body, status = cite_bands_views.build_citation_gaps(ws, "dnaa-inv")
    assert status == 200
    assert body["investigation"] == "dnaa-inv"
    gaps = body["gaps"]
    assert "dnaa-2" in gaps
    entry = gaps["dnaa-2"]
    assert {b["test"] for b in entry["uncited_bands"]} == {"frac-test", "prose-readout"}
    assert entry["available_references"] == ["Boesen2024", "Other1999"]


def test_citation_gaps_equivalence_with_plugin(tmp_path):
    pytest.importorskip("vivarium_workbench.lib.citation_gaps")
    from vivarium_workbench.lib.citation_gaps import investigation_citation_gaps

    ws = _inv_ws(tmp_path)
    endpoint_body, status = cite_bands_views.build_citation_gaps(ws, "dnaa-inv")
    assert status == 200
    direct = investigation_citation_gaps(ws, "dnaa-inv")
    assert endpoint_body["gaps"] == direct


# ---------------------------------------------------------------------------
# GET /api/expert-search
# ---------------------------------------------------------------------------

def test_expert_search_missing_q_400(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    body, status = cite_bands_views.build_expert_search(ws, None)
    assert status == 400
    body2, status2 = cite_bands_views.build_expert_search(ws, "   ,  ,")
    assert status2 == 400


def test_expert_search_no_docs_returns_empty_hits(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: no-docs\n")
    body, status = cite_bands_views.build_expert_search(ws, "DnaA-ATP,0.2,0.5")
    assert status == 200
    assert body["terms"] == ["DnaA-ATP", "0.2", "0.5"]
    assert body["hits"] == []


def test_expert_search_equivalence_with_plugin(tmp_path):
    pytest.importorskip("vivarium_workbench.lib.expert_search")
    from vivarium_workbench.lib.expert_search import search_expert_docs

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: no-docs\n")

    endpoint_body, status = cite_bands_views.build_expert_search(
        ws, "DnaA-ATP,0.2", max_hits=3
    )
    assert status == 200
    direct = search_expert_docs(ws, terms=["DnaA-ATP", "0.2"], max_hits=3)
    assert endpoint_body["hits"] == direct


def test_expert_search_term_parsing_strips_and_drops_empties(tmp_path):
    """"a, , b ,," -> ["a", "b"] — whitespace-only and empty terms dropped."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: no-docs\n")
    body, status = cite_bands_views.build_expert_search(ws, "a, , b ,,")
    assert status == 200
    assert body["terms"] == ["a", "b"]
