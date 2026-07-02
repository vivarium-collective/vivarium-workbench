"""Unit tests for the ptools Omics Viewer launch helper.

Tests focus on the pure _build_ptools_launch_url() helper so no live HTTP
server or Pathway Tools instance is required.
"""
from __future__ import annotations
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helper import
# ---------------------------------------------------------------------------

from vivarium_workbench.lib.study_viz_views import (
    build_ptools_launch_url as _build_ptools_launch_url,
)
from vivarium_workbench.lib.system_info import _PTOOLS_DEFAULT_OMICS_URL_TEMPLATE


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def study_with_ptools(tmp_path):
    """Minimal workspace + study directory containing two ptools TSV files."""
    ws = tmp_path / "ws"
    studies = ws / "studies" / "my_study"
    ptools_dir = studies / "ptools"
    ptools_dir.mkdir(parents=True)
    # Two TSVs for different analyses
    (ptools_dir / "flux_analysis__partition1.tsv").write_text("gene\tt1\nA\t1.0\n")
    (ptools_dir / "expression__partition1.tsv").write_text("gene\tt1\nB\t2.0\n")
    return ws, studies


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_build_ptools_launch_url_basic(study_with_ptools):
    """Returns a well-formed launch URL for an unfiltered discovery."""
    ws, study_dir = study_with_ptools
    result = _build_ptools_launch_url(
        study_dir=study_dir,
        ws_root=ws,
        ptools_server_url="http://ptools.example.com",
        ptools_omics_url_template=_PTOOLS_DEFAULT_OMICS_URL_TEMPLATE,
        public_base="http://dashboard.example.com:8771",
    )
    assert "error" not in result
    assert result["url"].startswith("http://ptools.example.com/overviewsWeb/celOv.shtml")
    assert "orgid=ECOLI" in result["url"]
    # Omics Viewer auto-load params (verified format)
    assert "omics=t" in result["url"]
    assert f"url={result['tsv_url']}" in result["url"]
    assert "class=" in result["url"]
    assert "column1=" in result["url"]
    # tsv_url must be an absolute URL the PTools server can fetch
    assert result["tsv_url"].startswith("http://dashboard.example.com:8771/")
    assert result["tsv_url"].endswith(".tsv")
    # Both TSVs are listed in available
    assert len(result["available"]) == 2


def test_build_ptools_launch_url_analysis_filter(study_with_ptools):
    """Filtering by analysis name narrows the available list."""
    ws, study_dir = study_with_ptools
    result = _build_ptools_launch_url(
        study_dir=study_dir,
        ws_root=ws,
        ptools_server_url="http://ptools.example.com",
        ptools_omics_url_template=_PTOOLS_DEFAULT_OMICS_URL_TEMPLATE,
        public_base="http://dashboard.example.com",
        analysis="flux_analysis",
    )
    assert "error" not in result
    assert len(result["available"]) == 1
    assert "flux_analysis" in result["available"][0]
    assert "flux_analysis" in result["tsv_url"]


def test_build_ptools_launch_url_no_tsvs(tmp_path):
    """Returns an error dict when no ptools TSVs exist."""
    ws = tmp_path / "ws"
    study_dir = ws / "studies" / "empty_study"
    study_dir.mkdir(parents=True)
    result = _build_ptools_launch_url(
        study_dir=study_dir,
        ws_root=ws,
        ptools_server_url="http://ptools.example.com",
        ptools_omics_url_template=_PTOOLS_DEFAULT_OMICS_URL_TEMPLATE,
        public_base="http://dashboard.example.com",
    )
    assert "error" in result
    assert result["available"] == []


def test_build_ptools_launch_url_analysis_no_match(study_with_ptools):
    """Returns an error when analysis filter matches nothing."""
    ws, study_dir = study_with_ptools
    result = _build_ptools_launch_url(
        study_dir=study_dir,
        ws_root=ws,
        ptools_server_url="http://ptools.example.com",
        ptools_omics_url_template=_PTOOLS_DEFAULT_OMICS_URL_TEMPLATE,
        public_base="http://dashboard.example.com",
        analysis="nonexistent_analysis",
    )
    assert "error" in result
    assert result["available"] == []


def test_build_ptools_launch_url_relpath_is_workspace_relative(study_with_ptools):
    """TSV relpaths in 'available' are relative to the workspace root."""
    ws, study_dir = study_with_ptools
    result = _build_ptools_launch_url(
        study_dir=study_dir,
        ws_root=ws,
        ptools_server_url="http://ptools.example.com",
        ptools_omics_url_template=_PTOOLS_DEFAULT_OMICS_URL_TEMPLATE,
        public_base="http://dashboard.example.com",
    )
    for rel in result["available"]:
        # Must not be absolute; must start with studies/
        assert not rel.startswith("/")
        assert rel.startswith("studies/my_study/ptools/")
        assert rel.endswith(".tsv")


def test_build_ptools_launch_url_custom_template(study_with_ptools):
    """Custom URL templates are honored."""
    ws, study_dir = study_with_ptools
    custom_template = "{server}/omics?org={orgid}&file={tsv_url}"
    result = _build_ptools_launch_url(
        study_dir=study_dir,
        ws_root=ws,
        ptools_server_url="http://ptools.mylab.org",
        ptools_omics_url_template=custom_template,
        public_base="http://dash.mylab.org",
    )
    assert result["url"].startswith("http://ptools.mylab.org/omics?")
    assert "org=ECOLI" in result["url"]
    assert "file=http://dash.mylab.org/" in result["url"]


def test_default_omics_template_has_all_placeholders():
    """The default template contains every placeholder the builder supplies."""
    t = _PTOOLS_DEFAULT_OMICS_URL_TEMPLATE
    assert "{server}" in t
    assert "{orgid}" in t
    assert "{tsv_url}" in t
    assert "{cls}" in t
    assert "{columns}" in t
    # It targets the Omics Viewer auto-load endpoint.
    assert "omics=t" in t


def test_ptools_object_class_inference():
    """Object class is inferred from the analysis / TSV name."""
    from vivarium_workbench.lib.study_viz_views import ptools_object_class as _ptools_object_class

    assert _ptools_object_class("ptools_rxns__p1.tsv") == "reaction"
    assert _ptools_object_class("ptools_proteins__p1.tsv") == "protein"
    assert _ptools_object_class("ptools_rna__p1.tsv") == "gene"
    assert _ptools_object_class("anything_else") == "gene"


def test_launch_url_infers_class_and_columns(tmp_path):
    """The builder sets class=<type> and column1=1-N from the chosen TSV."""
    ws = tmp_path / "ws"
    pt = ws / "studies" / "s" / "ptools"
    pt.mkdir(parents=True)
    # ptools_rxns TSV with a $-header carrying 3 timepoint columns.
    (pt / "ptools_rxns__p1.tsv").write_text("$\tt0\tt1\tt2\nRXN-1\t1\t2\t3\n")
    result = _build_ptools_launch_url(
        study_dir=ws / "studies" / "s",
        ws_root=ws,
        ptools_server_url="http://ptools.example.com",
        ptools_omics_url_template=_PTOOLS_DEFAULT_OMICS_URL_TEMPLATE,
        public_base="http://dash.example.com",
    )
    assert "class=reaction" in result["url"]
    assert "column1=1-3" in result["url"]
