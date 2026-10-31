"""Phase 3 lock-in: the workbench may import only a NARROW, DECLARED core of
``viva_superpowers`` modules.

This is the end-state of the rewire-first re-architecture (Phase 2.1). All the
investigation-science *compute* the workbench used to borrow from the plugin was
moved into ``vivarium_workbench/lib/`` during Phase 2.1k. What remains importable
from the plugin is a small, deliberate core: the workbench-free audit/evaluation
spine (also imported by v2ecoli/sms-ecoli, so it must never depend on the
workbench), a handful of compute modules that are transitively required by that
workbench-free core (so they cannot move without dragging the core into the
workbench), the ecosystem ledger, and the pre-server bootstrap helpers.

This test AST-scans the whole ``vivarium_workbench`` package and fails if it
imports any ``viva_superpowers`` module outside the allowlist below. It catches
both a moved module creeping back AND a brand-new plugin-compute dependency.

Adding to ALLOWED should be rare and scrutinized. The default answer for new
compute is to put it in ``vivarium_workbench/lib/`` (see the Phase 2.1k history),
or inline a trivial helper — not to widen the plugin surface.

Post discovery-engine extraction (viva-superpowers #387) the investigation/study
framework lives in the ``discovery_engine`` package, which the workbench now
imports directly. The same AST scan therefore also covers ``discovery_engine``
against ``ALLOWED_DISCOVERY`` below (declared + must resolve to a real module).

The complementary import-linter contract in pyproject.toml forbids the specific
modules that were moved out (belt); this test is the allowlist (suspenders).
"""
from __future__ import annotations

import ast
import pathlib

# module name -> why the workbench is allowed to import it from the plugin.
#
# Trimmed to what the workbench ACTUALLY imports (verified by the AST scan below).
# This used to list ~40 modules — most were dead permissions: modules the workbench
# now sources from `discovery_engine` instead (study_audit, study_evaluator, rigor,
# report_linter, …) and phantom entries for discovery_engine-only modules that have
# no viva_superpowers twin at all (discover, narrative, synthesis, validation, …).
# The companion `test_no_unused_allowed_entries` keeps this set honest.
ALLOWED: dict[str, str] = {
    "catalog": "ecosystem ledger (canonical_registry); workbench delegates to it",
    "chart_store": "chart manifest store",
    "feedback_import": "feedback ledger import (used by feedback_tracking + the flush)",
    "feedback_tracking": "deterministic feedback ledger",
    "loop_state": "model-build loop-state reader for the Assurance>Build panel (pure JSON at .pbg/loop/<study>.json)",
    "run_params": "run-params sync (also read by the workbench's study_runs)",
    "test_audit": "deterministic test-sufficiency scoring rendered read-only by the Assurance>Audit panel (also run by /viva-audit-tests)",
}


# discovery_engine submodules the workbench imports (the investigation/study
# framework + discovery cycle that moved out of viva_superpowers in #387). This
# is the intended core, enumerated from the current tree; a new one must be
# added here deliberately.
ALLOWED_DISCOVERY: dict[str, str] = {
    "argument_store": "claims + argument-graph projection",
    # DE2.0 cycle phase modules — pure, read-only projections the Discovery Engine
    # (Cycle) panel runs as its traced step graph (lib/cycle_trace.py). None writes.
    "evidence_integrity": "Phase A: reconcile evidence integrity (VERIFIED/PROVISIONAL/…)",
    "candidate_action": "Phase B: plan candidate actions + select portfolio",
    "experiment_design": "Phase C: design a discriminating study",
    "model_architect": "Phase D: model catalog + acquisition routing",
    "evaluation": "Phase F: compare hypotheses, disagreement map, DiscoveryDelta",
    "transition_model": "N+2: transition composition + phase diagram",
    "study_seeding": "Growth loop: select-for-launch + seed a study (preview)",
    "study_refinement": "Growth loop: refinement needs + refine a study (preview)",
    "band_provenance": "acceptance-band provenance",
    "bibtex": "citation parsing for the report linter",
    "discover": "read-only discovery cycle (run_cycle / load_cycle_inputs)",
    "discovery_engine": "crank/frontier/closure/opportunities/diagnose projection",
    "finding_observations": "finding observation slots",
    "hypotheses": "hypothesis support log",
    "investigation_close": "close mechanic (backs /api/iset-close)",
    "investigation_inputs": "investigation input resolution",
    "investigation_status": "investigation acceptance roll-up",
    "knowledge_store": "canonical-verdict + concept projection",
    "narrative": "narrate + audit projection over the argument graph",
    "paths": "offline workspace-root/layout resolution",
    "post_sim": "post-sim Step family + build_report",
    "readout_migration": "readout-migration status/apply",
    "readout_resolver": "readout resolution",
    "relations": "study-relation projection",
    "report_linter": "report structural lint",
    "rigor": "rigor scorecard",
    "scaffold": "workspace/investigation scaffolding",
    "seed_from_followup": "follow-up seeding for the report linter",
    "study_audit": "L0-L5 audit",
    "study_evaluator": "outcome evaluation",
    "study_io": "atomic YAML load/save",
    "study_outcomes": "study outcomes",
    "study_status": "study test bucketing/status",
    "study_verdict": "lifecycle verdicts + severity_gate",
    "synthesis": "synthesis spine + open-calls roll-up",
    "test_diff": "cross-iteration test diff (diff_reports)",
    "viz_freshness": "viz freshness/staleness",
    "workspace_catalog": "workspace catalog registration",
    "workspace_paths": "canonical workspace layout resolution",
}


def _workbench_imports(package: str) -> dict[str, set[str]]:
    """Map each imported ``package`` submodule -> the files importing it."""
    root = pathlib.Path(__file__).resolve().parents[1] / "vivarium_workbench"
    found: dict[str, set[str]] = {}
    for f in root.rglob("*.py"):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        rel = str(f.relative_to(root.parent))
        for node in ast.walk(tree):
            mods: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module == package:
                    mods = [a.name for a in node.names]
                elif node.module.startswith(package + "."):
                    mods = [node.module.split(".")[1]]
            elif isinstance(node, ast.Import):
                mods = [a.name.split(".")[1] for a in node.names
                        if a.name.startswith(package + ".")]
            for m in mods:
                found.setdefault(m, set()).add(rel)
    return found


def _workbench_plugin_imports() -> dict[str, set[str]]:
    return _workbench_imports("viva_superpowers")


def test_workbench_imports_only_the_declared_plugin_core():
    found = _workbench_plugin_imports()
    offenders = {m: sorted(fs) for m, fs in found.items() if m not in ALLOWED}
    assert not offenders, (
        "vivarium_workbench imports viva_superpowers modules outside the declared core:\n"
        + "\n".join(f"  - {m}: {', '.join(fs)}" for m, fs in offenders.items())
        + "\n\nThe compute belongs in vivarium_workbench/lib/ (see Phase 2.1k), or "
        "— only if it is genuinely workbench-free core — add it to ALLOWED in "
        "tests/test_plugin_import_allowlist.py with a one-line justification."
    )


def test_no_unused_allowed_entries():
    """Symmetry: every ALLOWED entry must be a module the workbench actually imports.
    Catches the allowlist re-bloating with dead permissions (the state this trim
    fixed: ~40 entries granted, 7 used). A removed import => remove its ALLOWED entry."""
    found = _workbench_plugin_imports()
    unused = sorted(set(ALLOWED) - set(found))
    assert not unused, (
        "ALLOWED grants import rights the workbench never uses (dead permissions):\n"
        + "\n".join(f"  - {m}" for m in unused)
        + "\n\nRemove each from ALLOWED in tests/test_plugin_import_allowlist.py; if the "
        "workbench should import it, add the import instead."
    )


def test_workbench_imports_only_the_declared_discovery_engine_core():
    found = _workbench_imports("discovery_engine")
    offenders = {m: sorted(fs) for m, fs in found.items() if m not in ALLOWED_DISCOVERY}
    assert not offenders, (
        "vivarium_workbench imports discovery_engine modules not declared in "
        "ALLOWED_DISCOVERY:\n"
        + "\n".join(f"  - {m}: {', '.join(fs)}" for m, fs in offenders.items())
    )


def test_declared_discovery_engine_modules_resolve():
    import importlib.util

    if importlib.util.find_spec("discovery_engine") is None:
        import pytest

        pytest.skip("discovery_engine not installed")
    missing = [m for m in _workbench_imports("discovery_engine")
               if importlib.util.find_spec(f"discovery_engine.{m}") is None]
    assert not missing, f"imported discovery_engine modules that do not exist: {missing}"
