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

The complementary import-linter contract in pyproject.toml forbids the specific
modules that were moved out (belt); this test is the allowlist (suspenders).
"""
from __future__ import annotations

import ast
import pathlib

# module name -> why the workbench is allowed to import it from the plugin.
ALLOWED: dict[str, str] = {
    # --- workbench-free core spine (also imported by v2ecoli/sms-ecoli; the
    #     study_audit --gate runs it WITHOUT vivarium-workbench installed) ---
    "study_audit": "L0-L5 audit; audit-gated, must stay workbench-free",
    "study_evaluator": "core: outcome evaluation",
    "readout_resolver": "core: readout resolution",
    "viz_freshness": "core: viz freshness/staleness (de-vendored to plugin-canonical in 2.1a)",
    "study_io": "core: atomic YAML load/save",
    "study_status": "core: study test bucketing/status",
    "workspace_paths": "core: canonical workspace layout resolution",
    "run_params": "core: run-params sync (also read by the workbench's study_runs)",
    "diff_reports": "core: cross-iteration test diff (pure stdlib; the agent-feedback signal the flush writes + the Tests panel renders)",
    "test_audit": "core: deterministic test-sufficiency scoring rendered read-only by the Assurance>Audit panel (workbench-free; also run by /viva-audit-tests)",
    "loop_state": "core: model-build loop-state reader for the Assurance>Build panel (workbench-free; pure JSON at .pbg/loop/<study>.json)",
    "build_report": "core: aggregate a run's graded cards into a test_report/v1 (pure stdlib; the flush gates it via study_verdict.severity_gate)",
    "post_sim": "core: post-sim Step family (AnalysisStep/ResultsHandle/Analysis); the study flush dispatches generic record-based analyses over a run's emitter output without v2ecoli",
    # --- STAY science modules (external-repo-imported) ---
    "feedback_tracking": "external-imported; deterministic feedback ledger",
    "hypotheses": "external-imported; hypothesis support log",
    "investigation_close": "external-imported; close mechanic (backs /api/iset-close)",
    "rigor": "external-imported; rigor scorecard",
    "study_verdict": "external-imported; lifecycle verdicts",
    # --- compute transitively required BY the workbench-free core, so it is
    #     itself workbench-free and stays in the plugin (see 2.1k closure map) ---
    "band_provenance": "closure-blocked (rigor/finding_observations depend on it)",
    "study_outcomes": "closure-blocked (rigor/study_status/study_verdict depend on it)",
    "finding_observations": "closure-blocked (study_outcomes depends on it)",
    "report_linter": "closure-blocked (report -> report_linter, via investigation_close)",
    "bibtex": "closure-blocked (report_linter depends on it)",
    "seed_from_followup": "closure-blocked (report_linter depends on it)",
    "feedback_import": "closure-blocked (feedback_tracking depends on it)",
    # --- ecosystem ledger + investigation/report derivations that stay plugin-side ---
    "catalog": "ecosystem ledger (canonical_registry); workbench delegates to it",
    "chart_store": "chart manifest store",
    "readout_migration": "readout-migration status/apply (endpoint-backed)",
    "investigation_inputs": "investigation input resolution",
    "investigation_status": "investigation acceptance roll-up",
    # --- RFC-0005/0006 derive-first inquiry layer (pure dict->dict projections;
    #     workbench-free core the workbench projects into the argument/narrative
    #     views + the knowledge step) ---
    "knowledge_store": "core: canonical-verdict + concept projection (derive-first, pure)",
    "argument_store": "core: claims + argument-graph projection (derive-first, pure)",
    "narrative": "core: narrate + audit projection over the argument graph (pure)",
    "synthesis": "core: synthesis spine + open-calls roll-up (pure)",
    "relations": "core: study-relation projection feeding the argument graph (pure)",
    # --- RFC-0007 discovery engine: the read-only crank the report surfaces as the
    #     "what to do next" panel (value-ranked opportunities, diagnosis, suggestions,
    #     drift). Same derive-first family as knowledge_store/argument_store/narrative;
    #     the report builder calls only the read-only entries (run_cycle / load_cycle_inputs
    #     / frontier / opportunities / diagnose), never a write verb. ---
    "discover": "discovery engine: read-only run_cycle + load_cycle_inputs for the report's discovery panel",
    "discovery_engine": "discovery engine: pure crank/frontier/closure/opportunities/diagnose projection",
    "validation": "discovery engine: pure validator-layer records run inside run_cycle",
    # --- pre-server bootstrap helpers (structurally cannot be HTTP) ---
    "scaffold": "workspace/investigation scaffolding (backs vwb scaffold-* verbs)",
    "workspace_catalog": "workspace catalog registration (backs vwb catalog-add)",
    "paths": "offline workspace-root/layout resolution",
}


def _workbench_plugin_imports() -> dict[str, set[str]]:
    """Map each imported ``viva_superpowers`` submodule -> the files importing it."""
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
                if node.module == "viva_superpowers":
                    mods = [a.name for a in node.names]
                elif node.module.startswith("viva_superpowers."):
                    mods = [node.module.split(".")[1]]
            elif isinstance(node, ast.Import):
                mods = [a.name.split(".")[1] for a in node.names
                        if a.name.startswith("viva_superpowers.")]
            for m in mods:
                found.setdefault(m, set()).add(rel)
    return found


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
