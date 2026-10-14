"""Wiring for the investigation-graph "Methods · Simulations" study-card block.

Mirrors tests/test_b4_wiring.py's marker-presence style for the parts that
aren't node-requirable (walkthrough.js needs a full DOM; the backend projection
is exercised end-to-end elsewhere), plus runs the pure `_methodsBlockHtml`
node DOM-stub test when node is available.
"""
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "vivarium_workbench/static"


def test_backend_projects_methods_sims_fields():
    src = (ROOT / "vivarium_workbench/lib/report_views.py").read_text()
    # The per-study dict carries the new projection keys.
    for key in ('"baseline_composite"', '"methods"', '"fidelity"',
                '"behavior_tests"', '"runs"'):
        assert key in src, f"report_views missing study-node key {key}"
    # And resolves provenance via the composite-lookup path.
    assert "composite_provenance_index" in src
    assert "baseline_provenance_for_study" in src
    assert "build_behavior_tests_verdict" in src
    assert "read_runs_db_for_study" in src


def test_composite_lookup_threads_methods_fidelity():
    src = (ROOT / "vivarium_workbench/lib/composite_lookup.py").read_text()
    # Generator rec carries methods/fidelity; provenance index + resolver exist.
    assert 'rec["methods"]' in src and 'rec["fidelity"]' in src
    assert "def composite_provenance_index(" in src
    assert "def baseline_provenance_for_study(" in src


def test_env_worker_serializes_methods_fidelity():
    src = (ROOT / "vivarium_workbench/env_worker.py").read_text()
    assert '"methods"' in src and '"fidelity"' in src


def test_aig_graph_defines_and_exports_methods_block():
    js = (STATIC / "aig-graph.js").read_text()
    assert "function _methodsBlockHtml(study)" in js
    assert "global._methodsBlockHtml = _methodsBlockHtml;" in js
    assert "_methodsBlockHtml: _methodsBlockHtml" in js
    # Band-2 (near) carries the methods flag.
    assert "methods: true" in js
    # The locked block header + container.
    assert "Methods · Simulations" in js
    assert "aig-methods-block" in js
    assert "aig-fidelity-real" in js and "aig-fidelity-repro" in js


def test_walkthrough_inserts_methods_block_after_chain():
    js = (STATIC / "walkthrough.js").read_text()
    assert "_opts.methods" in js
    assert "window._methodsBlockHtml(s)" in js
    # Inserted AFTER the evidence-chain block and BEFORE the footer helpers.
    i_chain = js.index("window._chainBlockHtml(chainsBySlug[s.name])")
    i_methods = js.index("window._methodsBlockHtml(s)")
    i_footer = js.index("_dagCacheBadgeHtml(s.name)")
    assert i_chain < i_methods < i_footer


def test_style_defines_methods_and_fidelity_classes():
    css = (STATIC / "style.css").read_text()
    assert ".aig-fidelity-chip.aig-fidelity-real" in css
    assert ".aig-fidelity-chip.aig-fidelity-repro" in css
    # Dark-mode overrides following the .iset-dag-node / .aig-claim-row pattern.
    assert ':root[data-theme="dark"] .aig-methods-block' in css
    assert ':root[data-theme="dark"] .aig-methods-label' in css


def test_methods_block_node_logic():
    node = shutil.which("node")
    if not node:  # pragma: no cover - CI always has node for the JS suite
        import pytest
        pytest.skip("node not available")
    subprocess.run([node, str(ROOT / "tests/js/test_methods_block.js")], check=True)
