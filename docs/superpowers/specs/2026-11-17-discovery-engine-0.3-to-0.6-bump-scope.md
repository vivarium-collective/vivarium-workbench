# Scoping: bump `viva-discovery-engine` 0.3 → 0.6 in the workbench

**Date:** 2026-11-17
**Status:** scope / investigation (read-only analysis; no code changed)
**Driver:** the RFC-0007 validation panel (PR #1616) should surface the *fuller consolidation validators*; those live in a discovery-engine newer than the pinned 0.3.0.

**Verdict: this is a SMALL, additive bump — not a migration — but it is BLOCKED on a lockstep co-bump of `viva-superpowers`, whose own `<0.4` pin on discovery-engine gates it.**

All facts below verified on 2026-11-17 against the discovery-engine git tags (v0.3.0 … v0.6.0), PyPI, and the two consumers' `pyproject.toml`.

---

## 1. What the panel actually needs (and what it already has)

Correcting an earlier assumption: **v0.3.0 already contains the RFC-0007 validation layer.** `discovery_engine/reporting/validation.py` at v0.3.0 defines `run_validators`, `coverage_matrix`, `epistemic_separation_violations`, and `run_cycle` already emits `validations`. So the validation panel **already works against the pinned 0.3.0 wheel** — it is not dormant. (The subagent that built it saw "no layer" only because its *local editable* discovery-engine was a pre-0.3 checkout; the pinned wheel is what CI and production use.)

What the bump buys is the **additional validators the 2026-11-15 contract/audit consolidation registered** (e.g. `structural.study.contract_face`, `empirical.claim.evidence_integrity`, `structural.study.observable_coverage`, `structural.{study,investigation}.report_lint`, `structural.study.reproducibility`, `structural.component.contract_completeness`) — i.e. more filled cells in the coverage matrix and more records, not the panel turning on.

## 2. The 0.3 → 0.6 delta is additive

- **The subpackage reorg is already absorbed.** It landed **in 0.3.0** (`e6f387d — 0.3.0: subpackage reorg + giant-file decomposition`), and the workbench already imports the subpackage paths (`discovery_engine.study.verdict`, `discovery_engine.reporting.rigor`, …). The bump does **not** cross the reorg.
- **No files deleted or renamed** between v0.3.0 and v0.6.0: `git diff --diff-filter=DR --name-status v0.3.0 v0.6.0 -- discovery_engine` is empty.
- The three modules that look like files but are **subpackages** (`reporting/rigor/`, `study/evaluator/`, `reporting/linter/`) are subpackages at **both** versions — no move.
- The releases in between are feature adds: 0.3.1 (compute_outcomes fix), 0.4.0 (generative cycle), 0.4.1 (engine-native report/lint/observations CLI verbs), 0.5.0 (linkage_index + investigation_members relocated into the engine), 0.6.0 (versioned EvidenceSnapshot + stale-evidence gate).
- **No transitive pressure:** DE 0.6.0 declares `process-bigraph` and `bigraph-schema` with **no version floor**, so the workbench's own `process-bigraph>=1.11.0` / `bigraph-schema>=1.8.1` continue to govern.

The workbench imports ~30 discovery_engine modules (top consumers: `study.verdict` ×14, `study` ×13, `integration` ×10, `reporting.rigor` ×7, `workspace_paths` ×7). All resolve at v0.6.0 (the panel branch's tests ran against local 0.6.0 source and passed). The only residual risk is **symbol-level signature drift** inside a file that stayed — caught by the workbench test suite, not by this static survey.

## 3. The blocker: the `viva_superpowers` facade pin

`viva_superpowers` is a **permanent facade** over discovery-engine: for every moved module, `viva_superpowers.<m>` *is the same object* as `discovery_engine.<m>` (`sys.modules[__name__] = discovery_engine.<m>`), and viva-superpowers' own `test_discovery_engine_facade.py` asserts that object identity. The workbench installs **both** packages into one environment, so they must resolve to **one** `discovery_engine`.

Both consumers currently pin the same narrow range:

| Package | Version | discovery-engine pin |
|---|---|---|
| vivarium-workbench | (main) | `viva-discovery-engine>=0.3,<0.4` (pyproject.toml:127) |
| viva-superpowers | 0.26.0 | `viva-discovery-engine>=0.3,<0.4` (pyproject.toml:34) |

**Because both are co-installed and share one `discovery_engine`, the workbench cannot move DE to 0.6 while viva-superpowers constrains `<0.4` — the resolver cannot satisfy `0.6` and `<0.4` at once.** viva-superpowers must relax its pin **first** (or in the same change set).

**Sub-risk inside viva-superpowers (its own validation, flagged here for completeness):** viva-superpowers 0.26.0 declares console-script entry points against **flat** DE paths — `discovery_engine.study_evaluator:compute_outcomes_cli`, `discovery_engine.study_outcomes:main`, `discovery_engine.migrate_nested:main`, `discovery_engine.migrate_inputs:main`. These resolve at 0.3.0 via back-compat shims left by the reorg; whoever bumps viva-superpowers must confirm the shims still resolve at 0.6.0 (or repoint the entry points to the subpackage paths).

## 4. `viva-discovery-engine 0.6.0` is published

PyPI carries `0.1.0 … 0.6.0` (latest 0.6.0). **No discovery-engine release is needed** — the bump is a specifier relax + re-lock in the two consumers.

## 5. Recommended plan (ordered — the facade gates it)

**Task A — viva-superpowers (prerequisite, its own repo + release):**
1. Relax `viva-discovery-engine>=0.3,<0.4` → `>=0.3,<0.7` (or `<1.0`).
2. Confirm the facade identity test passes against 0.6.0 and the flat-path console-script entry points still resolve (repoint to subpackage paths if not).
3. Run its suite; release (e.g. 0.27.0) so a **published** viva-superpowers carries the relaxed pin.

**Task B — vivarium-workbench (this repo):**
1. Relax `viva-discovery-engine>=0.3,<0.4` → `>=0.3,<0.7` (pyproject.toml:127) and comment-doc nearby (lines 123-127, 195).
2. Bump the viva-superpowers floor to the release from Task A.
3. `uv lock` (resolves DE to 0.6.0), then run the **full** suite (4 pytest shards), `lint-imports`, `mypy`, and the loom/js/smoke jobs — the suite is the real check for symbol-level drift across the ~30 imported modules.
4. If `tests/test_plugin_import_allowlist.py` / the `[tool.importlinter]` allowlist blocks any **newly** imported 0.4–0.6 module, add it — a pure version bump that imports nothing new needs no allowlist change.

**Task C — optional follow-on (the actual payoff):** extend the validation panel to surface the consolidation validators now registered, and add the record-rendering test the first cut lacked (its fixture had no studies).

## 6. Risk & size

**Low–medium, dominated by two-repo coordination, not code.** No reorg, no renames, no transitive bumps, target version already published. The two real work items are (1) relaxing viva-superpowers' pin + re-releasing it, and (2) re-locking the workbench and running the suite to catch any signature drift. A sensible single engineer closes A+B in well under a day, assuming the suites are green.
