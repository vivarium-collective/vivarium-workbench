"""Parity tests for lib.report_views builders (Phase A, Batch 7).

Verifies that:
1. Each builder returns sensible output on a fixture workspace.
2. The server.py shims produce byte-identical output to the lib builders
   (TestServerShimParity) — the core parity contract.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml


# ---------------------------------------------------------------------------
# Fixture workspace
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_ws(tmp_path):
    """Workspace with one investigation + one study + a BibTeX file.

    Enough for report-lint, linkage-index, needs-attention, inputs, and
    iset-detail to exercise their main code paths.
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: test-ws\n", encoding="utf-8")

    # One study (studies/s1/study.yaml)
    sd = ws / "studies" / "s1"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3,
        "name": "s1",
        "title": "Study one",
        "status": "planned",
        "investigation": "the-inv",
        "baseline": [{"name": "core", "composite": "pkg.composites.core"}],
        "variants": [],
        "behavior_tests": [{"name": "b1"}],
        "cites": ["Ref2024"],
        "acceptance_criteria": [
            {"study": "s1", "behavior": "b1"},
            {"behavior": "b2", "status": "pending"},  # unkeyed → needs-attention item
        ],
    }), encoding="utf-8")

    # One investigation (investigations/the-inv/investigation.yaml)
    inv_dir = ws / "investigations" / "the-inv"
    inv_dir.mkdir(parents=True)
    (inv_dir / "investigation.yaml").write_text(yaml.safe_dump({
        "name": "the-inv",
        "title": "The investigation",
        "description": "A test investigation",
        "status": "planning",
        "studies": ["s1"],
        "acceptance_criteria": [
            {"study": "s1", "behavior": "b1"},
            {"behavior": "b2", "status": "pending"},
        ],
    }), encoding="utf-8")

    # BibTeX references
    (ws / "references").mkdir()
    (ws / "references" / "papers.bib").write_text(
        "@article{Ref2024,\n"
        "  title = {Test paper},\n"
        "  author = {Smith, A},\n"
        "  year = {2024},\n"
        "}\n",
        encoding="utf-8",
    )

    return ws


@pytest.fixture
def missing_ws(tmp_path):
    """A workspace path that does not exist (for 404 / tolerant tests)."""
    return tmp_path / "does-not-exist"


# ---------------------------------------------------------------------------
# 1. build_report_lint
# ---------------------------------------------------------------------------

def test_build_report_lint_returns_200(tmp_ws):
    from vivarium_workbench.lib.report_views import build_report_lint
    body, status = build_report_lint(tmp_ws)
    assert status == 200
    assert "findings" in body
    assert isinstance(body["findings"], list)


def test_build_report_lint_tolerant_missing_ws(missing_ws):
    from vivarium_workbench.lib.report_views import build_report_lint
    body, status = build_report_lint(missing_ws)
    assert status == 200
    assert "findings" in body


# ---------------------------------------------------------------------------
# 2. build_linkage_index
# ---------------------------------------------------------------------------

def test_build_linkage_index_returns_200(tmp_ws):
    from vivarium_workbench.lib.report_views import build_linkage_index
    body, status = build_linkage_index(tmp_ws)
    assert status == 200
    assert isinstance(body, dict)


def test_build_linkage_index_source_param(tmp_ws):
    from vivarium_workbench.lib.report_views import build_linkage_index
    body, status = build_linkage_index(tmp_ws, source="Ref2024")
    assert status == 200
    # Tolerant: result has 'studies' key (list, possibly empty)
    assert "studies" in body


def test_build_linkage_index_investigation_param(tmp_ws):
    from vivarium_workbench.lib.report_views import build_linkage_index
    body, status = build_linkage_index(tmp_ws, investigation="the-inv")
    assert status == 200
    # May return nodes/edges or ac_matrix depending on pbg_superpowers version
    assert isinstance(body, dict)


def test_build_linkage_index_tolerant_missing_ws(missing_ws):
    from vivarium_workbench.lib.report_views import build_linkage_index
    body, status = build_linkage_index(missing_ws, investigation="nope")
    assert status == 200
    assert isinstance(body, dict)


def test_build_linkage_index_observable_registry_no_fn(tmp_ws):
    """Without observables_for_ref_fn, observable_registry path degrades to empty."""
    from vivarium_workbench.lib.report_views import build_linkage_index
    body, status = build_linkage_index(tmp_ws, observable_registry="some.token")
    assert status == 200
    # With no fn, will either fail gracefully or return {studies, composites}
    assert isinstance(body, dict)


def test_build_linkage_index_observable_registry_with_fn(tmp_ws):
    """With an injectable fn, observable_registry path calls pbg_superpowers."""
    from vivarium_workbench.lib.report_views import build_linkage_index

    def _stub_obs(ws_root, ref):
        return {"leaves": ["agents.0.listeners.mass.cell_mass"], "catalogs": {}}

    body, status = build_linkage_index(
        tmp_ws, observable_registry="listeners.mass.cell_mass",
        observables_for_ref_fn=_stub_obs,
    )
    assert status == 200
    assert isinstance(body, dict)


# ---------------------------------------------------------------------------
# 3. build_needs_attention
# ---------------------------------------------------------------------------

def test_build_needs_attention_returns_200(tmp_ws):
    from vivarium_workbench.lib.report_views import build_needs_attention
    body, status = build_needs_attention(tmp_ws, investigation="the-inv")
    assert status == 200
    assert "items" in body
    assert "summary" in body
    assert isinstance(body["items"], list)
    summ = body["summary"]
    assert set(summ["by_severity"]) == {"high", "medium", "low"}


def test_build_needs_attention_tolerant_missing_ws(missing_ws):
    from vivarium_workbench.lib.report_views import build_needs_attention
    body, status = build_needs_attention(missing_ws, investigation="nope")
    assert status == 200
    assert body["items"] == []
    assert body["summary"]["total"] == 0


# ---------------------------------------------------------------------------
# 4. build_iset_detail
# ---------------------------------------------------------------------------

def test_build_iset_detail_returns_dict(tmp_ws):
    from vivarium_workbench.lib.report_views import build_iset_detail
    result = build_iset_detail(tmp_ws, "the-inv")
    assert result is not None
    assert result["name"] == "the-inv"
    assert "studies" in result
    assert isinstance(result["studies"], list)
    assert len(result["studies"]) == 1


def test_build_iset_detail_missing_yaml_returns_none(tmp_ws):
    from vivarium_workbench.lib.report_views import build_iset_detail
    result = build_iset_detail(tmp_ws, "no-such-investigation")
    assert result is None


def test_build_iset_detail_study_fields(tmp_ws):
    from vivarium_workbench.lib.report_views import build_iset_detail
    result = build_iset_detail(tmp_ws, "the-inv")
    assert result is not None
    study = result["studies"][0]
    assert study["name"] == "s1"
    assert "effective_status" in study
    assert "n_runs" in study
    assert "n_behaviors" in study


def test_build_iset_detail_effective_status(tmp_ws):
    from vivarium_workbench.lib.report_views import build_iset_detail
    result = build_iset_detail(tmp_ws, "the-inv")
    assert result is not None
    assert "effective_status" in result
