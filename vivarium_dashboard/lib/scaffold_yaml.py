"""Scaffold YAML text for new study.yaml + investigation.yaml files.

Emits v4 study + v2 investigation specs as YAML text (not a dict + safe_dump),
because the only way to put `# TODO:` guidance inside a YAML file is to write
the file as text. New users open a freshly-seeded study.yaml and see the
14-section narrative spine the dnaa-replication investigation evolved through
use, with one-line hints inviting them to fill each section in.

All v4/v2 narrative fields are optional per the schemas. The scaffold writes
them as YAML comments (rather than empty values) so:
  * the file validates as a minimal v4/v2 spec out of the box;
  * the user sees the target shape immediately, not after reading docs;
  * uncommenting a field is the explicit act of opting in.

These helpers are dialect-free strings — no f-string magic, no formatting
beyond simple template substitution — so the diff of a hand-edit against
the scaffold stays clean.
"""
from __future__ import annotations

import datetime


def v4_study_scaffold(
    name: str,
    *,
    composite: str | None = None,
    baseline_name: str | None = None,
    created: str | None = None,
) -> str:
    """Return the text body for a new studies/<name>/study.yaml file.

    The minimal required fields (schema_version, name, baseline) are populated
    as live YAML. The 14-section narrative spine is included as commented
    placeholders with one-line hints, so the user sees the target shape
    without the spec failing validation on day one.

    Args:
        name: study slug (matches the enclosing directory name).
        composite: optional dotted composite ref to seed `baseline:`. When
            None, the baseline block is itself a comment placeholder so the
            scaffold still parses (baseline is required by the schema, so we
            emit a minimal stub entry pointing at a placeholder composite —
            the user is expected to replace it).
        baseline_name: short name for the baseline entry (defaults to
            "baseline").
        created: optional ISO-8601 date string (defaults to today).
    """
    created = created or datetime.date.today().isoformat()
    bname = baseline_name or "baseline"
    if composite:
        baseline_block = (
            f"baseline:\n"
            f"  - name: {bname}\n"
            f"    composite: {composite}\n"
            f"    params: {{}}\n"
        )
    else:
        # Placeholder must satisfy the schema's composite-path regex
        # (^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$) so the scaffold validates
        # out of the box. The user replaces it with their real composite ref.
        baseline_block = (
            f"baseline:\n"
            f"  - name: {bname}\n"
            f"    composite: replace_me.composites.placeholder\n"
            f"    params: {{}}\n"
        )

    return f"""\
# {name}/study.yaml — schema v4
#
# A complete study has 14 sections grouped into 4 layers (executive,
# framing, validation, implementation+decisions). The required fields
# (schema_version, name, baseline) are populated below; the rest of the
# narrative spine is commented out — uncomment + fill the sections that
# matter as the study matures. See NEXT_STEPS.md and
# docs/concepts/vivarium-dashboard-model.md for the full pattern.
#
# Sections marked ★ are the ones to author FIRST — they render at the top
# of the study page and let a reviewer land on the study without reading
# the YAML.

schema_version: 4
name: {name}
created: '{created}'
status: planned
phase: Design

# ─── Executive layer ─────────────────────────────────────────────────
#
# runtime:                          # per-study execution overrides
#   subprocess_timeout_s: 600       # override workspace default
#   default_emitter: sqlite         # sqlite | xarray
#   max_generations: 1              # multi-gen runs (workspace owns the loop)
#
# ★ report:                         # exec summary panel (top of study page)
#   title: ""
#   verdict: not-yet-run            # passing | passing-with-caveats | failing-bio | failing-impl | inconclusive | not-yet-run
#   confidence: low                 # high | medium | low
#   evidence_quality: aspirational  # calibrated | literature-matched | aspirational | regression-only
#   objective: |
#     (one paragraph: what this study measures)
#   conclusion: ""
#   main_insight: ""
#   caveat: ""
#   key_metrics:
#     - {{label: "metric", value: null, status: pending}}
#
# ★ study_card:                     # one-paragraph dashboard card
#   goal: ""
#   mechanism: ""
#   why_before_next: ""             # why this study must finish before the next
#   expected_result: ""
#   main_expert_question: ""        # the one question you most want an expert to answer

# ─── Framing layer ───────────────────────────────────────────────────
#
# ★ question: |
#   (the testable question this study answers)
#
# assumptions:
#   - text: (literature fact the study assumes)
#     cites: [bib_key]
#     verified_in_v2ecoli: false

{baseline_block}variants: []
# observables / readouts → declare in `readouts:` below (v4 prefers `readouts`)

# ★ conditions:                     # v4 alternative to top-level baseline/variants
#   baseline:
#     composite: pkg.composites.foo
#     params: {{}}
#   variants:
#     - name: variant-1
#       diff_from_baseline: "..."
#   model_settings:                 # tunable parameter catalog
#     - name: parameter_name
#       default: 0
#       current: 0
#       range: [0, 1]
#       units: ""
#       cites: [bib_key]
#   expert_inputs: []               # one-off knobs an expert can twist
#
# enforced_params:                  # values the study REQUIRES be applied
#   parameter_name: expected_value
#   # or:
#   # params: {{parameter_name: expected_value}}
#   # source: "Boesen 2024 PNAS"

# ─── Validation layer ────────────────────────────────────────────────
#
# ★ behavior_tests:                 # pass/fail bound to literature targets
#   - name: kebab-test-name
#     classification: primary       # primary | supporting | diagnostic | regression
#     question: ""
#     measure:
#       kind: listener_median       # listener_median | listener_sum | bulk_count | ...
#       path: listeners.x.y         # emission path
#       window: second_half         # second_half | whole_run | seconds=<N>
#     pass_if:
#       op: in_range                # in_range | at_least | at_most | equals | ...
#       low: 0.0
#       high: 1.0
#     status: pending
#     cites: [bib_key]
#
# ★ readouts:                       # observable extraction plan
#   - name: kebab-readout-name
#     description: ""
#     store_path: agents.0.listeners.x.y
#     units: ""
#     status: derived-needed        # available | derived-needed | aspirational
#
# biological_summary: |
#   (multi-paragraph plain-English mechanism narrative — the textbook
#   write-up a non-modeler would read)
#
# literature_anchors:               # pair each lit claim to a model observable
#   - expectation: ""
#     model_observable: ""
#     source: ""
#     status_in_workspace: ""       # Not yet measurable | Available via X | Partial | Verified

# ─── Implementation + decisions layer ────────────────────────────────
#
# model_change:                     # declarative inventory of code changes
#   base_model: pkg.composites.foo
#   new_processes: []
#   new_state_variables: []
#   new_parameters: []
#   new_listeners: []
#   modified_processes: []
#
# implementation_requirements:      # TODO list
#   - id: req-1
#     kind: listener                # listener | process | parameter_hook | data
#     title: ""
#     status: planned               # planned | in-progress | done | blocked | done-no-op
#     effort: S                     # XS | S | M | L | XL
#     description: ""
#     steps: []
#     unblocks: []
#
# design_pivot_required:            # named open decisions
#   - id: STUDY-EQ-01
#     status: open                  # open | accepted | rejected | superseded-by-<slug>
#     question: ""
#     alternatives: []
#     requested_response: ""
#
# ★ conclusion_verdicts:            # three-track verdict
#   regression_compatibility:
#     result: PENDING               # PASS | FAIL | MIXED | PENDING
#     basis: ""
#   biological_validation:
#     result: PENDING
#     basis: ""
#   explanatory_gain:
#     result: PENDING               # POSITIVE | NEUTRAL | NEGATIVE | PENDING
#     basis: ""
"""


def v2_investigation_scaffold(
    name: str,
    *,
    title: str | None = None,
    overview: str | None = None,
    parent_studies: list[str] | None = None,
    created: str | None = None,
) -> str:
    """Return the text body for a new investigations/<name>/investigation.yaml.

    The minimal required fields (schema_version, name, title) are populated
    as live YAML. The 9-section narrative spine is included as commented
    placeholders so the user sees the target shape without the spec failing
    validation on day one.

    Args:
        name: investigation slug (matches the enclosing directory name).
        title: human-readable title (defaults to "<name> (untitled)").
        overview: optional initial `description:` text (legacy v1 field;
            v2 prefers `biological_story` + `lead`).
        parent_studies: optional list of study slugs to pre-populate
            `studies:`.
        created: optional ISO-8601 date string (defaults to today).
    """
    created = created or datetime.date.today().isoformat()
    title = title or f"{name} (untitled)"
    studies_block = ""
    if parent_studies:
        studies_block = "studies:\n" + "\n".join(
            f"  - {s}" for s in parent_studies
        ) + "\n"
    else:
        studies_block = "studies: []\n"
    description_block = ""
    if overview:
        description_block = f"description: |\n  {overview}\n"
    else:
        description_block = (
            "# description: |                # legacy v1 field; v2 prefers "
            "biological_story + lead\n"
            "#   (markdown narrative)\n"
        )

    return f"""\
# {name}/investigation.yaml — schema v2
#
# An investigation groups Studies under a shared research question. A
# complete investigation pairs the study list with a narrative spine that
# mirrors the per-study spine at a level up: executive panel, scientific
# argument, biological story, evaluator guides, glossary, investigation-
# wide guidelines.
#
# The required fields (schema_version, name, title) are populated below;
# the rest of the narrative spine is commented out — uncomment + fill the
# sections that matter as the investigation matures. See NEXT_STEPS.md
# and docs/concepts/vivarium-dashboard-model.md for the full pattern.

schema_version: 2
name: {name}
title: "{title}"
created: '{created}'
status: planning

# ─── Front matter ────────────────────────────────────────────────────
#
# question: |
#   (the overarching research question)
#
# hypothesis: |
#   (predicted outcome across the full study sequence)
#
# lead: |
#   (3-4 sentence front-of-textbook intro — first thing a reader sees)

{description_block}

# ─── Narrative spine ─────────────────────────────────────────────────
#
# executive:                        # headline panel at the top of the report
#   what_is_this: ""
#   verdict: ""
#   verdict_status: in-progress     # in-progress | passing | passing-with-caveats | failing | inconclusive | not-yet-run
#   decisions_needed: []
#
# scientific_argument:              # structured claim/evidence
#   main_claim: ""
#   evidence_for: []
#   evidence_against: []
#   key_figures: []
#   caveats: []
#   interpretation_ref: ""
#
# biological_story: |
#   (multi-paragraph plain-English mechanism narrative)
#
# at_a_glance:                      # one-line role per member study
#   - {{study: study-slug, role: "what this study contributes"}}
#
# how_to_read: |
#   (evaluator tips: how to read the report, which study to start with)
#
# glossary:
#   - {{term: "TERM", definition: "one-sentence definition"}}
#
# guidelines:                       # investigation-wide rules
#   literature_anchors: []
#   parameter_catalog: []
#   calibration_targets: []
#   naming_conventions: ""

{studies_block}
expert_docs: []

acceptance_criteria: []
"""
