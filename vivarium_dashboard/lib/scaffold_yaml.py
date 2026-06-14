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
    # The baseline composite MUST reference a REAL, registered composite (one
    # that resolves in /api/composites — a .composite.yaml registered in
    # core.py, or an installed pbg-* composite / @composite_generator). The
    # dashboard lints unresolved refs (composite-resolution lint) and flags any
    # study whose baseline doesn't resolve. Runs of this study PERSIST via the
    # workspace's emitter (runtime.default_emitter — sqlite by default).
    baseline_note = (
        "# baseline.composite MUST be a REAL, registered composite (resolves in\n"
        "# the Composites registry / /api/composites). The dashboard flags an\n"
        "# unresolved ref with a 'composite not found in registry' banner. Runs\n"
        "# persist via the workspace emitter (runtime.default_emitter: sqlite).\n"
    )
    if composite:
        baseline_block = (
            baseline_note
            + f"baseline:\n"
            f"  - name: {bname}\n"
            f"    composite: {composite}\n"
            f"    params: {{}}\n"
        )
    else:
        # Placeholder must satisfy the schema's composite-path regex
        # (^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$) so the scaffold validates
        # out of the box. The user replaces it with their real composite ref.
        baseline_block = (
            baseline_note
            + f"baseline:\n"
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

# study_type: standard             # critique #10 — exploratory | confirmatory |
#                                   # diagnostic | adversarial | standard. Default
#                                   # (unset) = standard; `kind: adversarial` still
#                                   # works as an alias. Shapes how rigor credits the
#                                   # study (e.g. an exploratory pass is an observation,
#                                   # not falsification exposure).

# ─── Executive layer ─────────────────────────────────────────────────
#
# runtime:                          # per-study execution overrides
#   subprocess_timeout_s: 600       # override workspace default
#   default_emitter: sqlite         # sqlite | xarray
#   max_generations: 1              # multi-gen runs (workspace owns the loop)
#
# report:                          # exec summary panel (top of study page)
#   # title/objective/main_insight/verdict/key_metrics are DERIVED (read-only
#   # computed) from canonical fields when absent — only author the optional
#   # overrides below (caveat / evidence_quality / confidence).
#   confidence: low                 # high | medium | low
#   evidence_quality: aspirational  # calibrated | literature-matched | aspirational | regression-only
#   caveat: ""
#   # title: ""                     # override; else derived from name/objective
#   # objective: |                  # override; else aliased to top-level objective
#   #   (one paragraph: what this study measures)
#   # main_insight: ""             # override; else derived from findings
#   # verdict: not-yet-run         # override; else derived from gate_evaluator
#   # key_metrics: []              # override; else derived from findings/outcomes
#
# study_card:                       # one-paragraph dashboard card.
#   # ALL slots are DERIVED from canonical fields when absent (goal←objective,
#   # mechanism←biological_summary/findings, expected_result←hypothesis,
#   # main_expert_question←expert_questions[0], why_before_next←
#   # pipeline_gate.proceed_condition). Author a slot only to override.
#   # goal: ""
#   # mechanism: ""
#   # why_before_next: ""           # why this study must finish before the next
#   # expected_result: ""
#   # main_expert_question: ""      # the one question you most want an expert to answer

# ─── Framing layer ───────────────────────────────────────────────────
#
# ★ question: |
#   (the testable question this study answers)
#
# assumptions:
#   - text: (literature fact the study assumes)
#     cites: [bib_key]
#     verified_in_v2ecoli: false
#
# pipeline_gate:                      # Section 2 — DAG edges + proceed gate
#   prerequisites:                    # upstream studies this one depends on
#     - study: upstream-study-slug
#       condition: tests-passed       # gate the upstream must satisfy
#       relation: leads-to            # leads-to (default) | model-input |
#                                     # evidence | calibrates-threshold |
#                                     # refutes-alternative
#       outputs_used: []              # upstream outputs consumed (→ model-input)
#   enables: []                       # downstream studies unblocked by this one
#   proceed_condition: ""             # when may the next study start
#
# composition_commitment:             # the THEORETICAL commitment this study makes
#   # Optional but high-value: states what this study ADDS to the prerequisite
#   # study and what that buys you, so a reviewer reads the compositional claim
#   # without diffing composites. Renders as a "Theoretical commitment" panel.
#   component_added: []               # process(es) new vs the prerequisite study
#   deficit_addressed:                # the gap the new component closes
#     note: ""                        # free-text: what was missing before
#     closure_gap_item: []            # gap store(s) this closes (auto-fillable from the meter)
#   new_behavior: []                  # this study's primary behavior_tests[].name (link to test)
#   invariants_required:              # earlier guarantees that must still hold (feeds invariant_check)
#     - study: upstream-study-slug
#       test: upstream-behavior-test-name
#   alternatives_excluded: []         # ref to controls[].name / alternative_hypotheses this rules out

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
#       provenance:                 # critique #9 — WHERE this threshold came from
#         kind: literature          # theory | calibration | literature | expert |
#                                   # exploratory | post_hoc (distinct from `cites`/
#                                   # `calibration_anchor`, which link a source)
#         note: ""                  # one line justifying the cutoff
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
#   representational_claims:          # authored rationale the meter can't compute:
#     # WHY each store is modelled the way it is + which alternatives were
#     # excluded. Pairs with the computed `model_representation` (inside /
#     # boundary-crossing / derived / self-produced labels + closure status).
#     - store: store_name
#       role: self-produced           # inside | boundary-crossing | derived | self-produced
#       rationale: ""                 # why this representation (not an alternative)
#       alternatives_excluded: []     # representations considered and rejected
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
# conclusion_verdicts:              # three-track verdict.
#   # Each `result` is DERIVED (read-only badge), NOT authored:
#   #   biological_validation   ← pipeline_gate.gate_evaluator.result
#   #   regression_compatibility← all runs completed without error
#   #   explanatory_gain        ← >=1 finding with tier=='interpretation'
#   # Author only the `basis` free-text (the *why*).
#   regression_compatibility:
#     basis: ""
#   biological_validation:
#     basis: ""
#   explanatory_gain:
#     basis: ""
#
# ─── RIGOR (what a skeptic asks for; the evidence & rigor scorecard reads these).
#     Fill these before the study is "done". Guide: docs/conventions/rigor-checklist.md
#
# robustness:                       # replication: >=3 seeds (stochastic) OR a sweep (deterministic)
#   n_replicates: 1
#   seeds: [0]
#   parameter_sweep: false
#
# preregistered:                    # critique #18 — criteria fixed BEFORE the run.
#   # A confirmatory study earns full credit only when its pass criteria were
#   # registered (registered_at predates the canonical run). Renders a
#   # "pre-registered ✓ / post-hoc ⚠" chip in the verdict area.
#   criteria: []                    # the pass/fail criteria fixed in advance
#   thresholds:                     # test_name: pass_if (mirrors behavior_tests[].pass_if)
#     test-name: {{op: in_range, low: 0.0, high: 1.0}}
#   predictions: []                 # what you predict the run will show
#   controls: []                    # controls registered in advance
#   registered_at: ''               # ISO-8601 timestamp you registered these (author-supplied)
#
# controls:                         # a system that SHOULD fail + a passing/borderline case
#   - name: ""                      # build the negative control with pbg_superpowers.intervention
#     kind: negative                # negative | positive | borderline | adversarial
#     hypothesis: ""                # why this should (not) qualify
#     expected: ""
#     observed: ""
#     result: PENDING               # PENDING until run; PASS only with a non-empty `observed` (= the control behaved as expected / discriminating)
#
# calibration_ladder:               # critique #20 — per metric, index controls[] by rung.
#   # Each rung is a `controls[].name` (or null when that rung is unbuilt — the gap
#   # is the signal). >=3 filled rungs = a calibrated metric; known_fail+known_pass
#   # but no borderline = WARN; <=1 rung = GAP.
#   - metric: metric_name
#     known_fail: ""                # control that SHOULD fail (negative)
#     known_pass: ""                # control that SHOULD pass (positive)
#     borderline: null              # a near-threshold case (discriminating power)
#     stress: null                  # an extreme / adversarial case
#
# alternative_hypotheses:           # competing explanations + how the evidence excludes them
#   - claim: ""
#     hypothesis_id: ""             # critique #6 — optional link to an investigation
#                                   # hypotheses[].id this study's evidence bears on
#     discriminated_by: ""          # often the negative control
#     status: not-excluded          # excluded | not-excluded | untested
#
# falsifiability: ""                # what result would overturn the claim
#
# limitations:                      # what this result does NOT show
#   - ""
#
# discovery_implications:           # Decide-phase synthesis + next steps
#   resolved_uncertainties: []
#   remaining_uncertainties: []
#   alternate_hypotheses:           # critique #6 — competing explanations (canonical home)
#     - claim: ""
#       hypothesis_id: ""           # optional link to an investigation hypotheses[].id
#       discriminated_by: ""        # often the negative control
#       status: not-excluded        # excluded | not-excluded | untested
#   followup_study_proposals:
#     - id: ""
#       title: ""
#       motivation: ""              # give a real motivation, not just a title
#
# kind: standard                    # set to `adversarial` for a probe that should NOT qualify
#
# findings:                         # each finding: tier + evidence (claim discipline)
#   - id: F-01
#     tier: observation             # observation | mechanism | interpretation
#     mechanism_origin: ""          # engineered | emergent (on interpretation claims)
#     statement: ""
#     claim_scope: mechanism        # critique #21 — local-implementation | mechanism |
#                                   # behavioral | theoretical | generality (DISTINCT
#                                   # from tier; the scope/reach of the claim)
#     lifecycle_state: observation  # critique #25 — observation | candidate-explanation |
#                                   # tested-vs-alternatives | provisional-claim |
#                                   # generalized | retired | superseded. The derived
#                                   # FLOOR (study_verdict.lifecycle_floor) is the minimum;
#                                   # author may declare higher, never below it.
#     generality:                   # critique #22 — across which axes was this checked?
#       axes_tested: [parameter_regime]   # parameter_regime | initial_conditions |
#                                   # discretization | geometry | alt_implementation |
#                                   # independent_authoring
#       level: instance_specific    # instance_specific | mechanism | framework
#     next_action: ""               # free-text rationale: what to do next
#     next_action_type: ""          # critique #7 — replicate | calibrate | ablate |
#                                   # adversarially_probe | refine_representation |
#                                   # split_hypothesis | retire_hypothesis | escalate_model
#     evidence: {{from_test: ""}}
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

# runtime — execution defaults inherited by this investigation's studies.
# default_emitter is the persistence backend every run uses so its trajectory
# is recorded (not just a summary). sqlite is the portable default; xarray
# suits large ensembles. A per-study runtime.emitter overrides this.
runtime:
  default_emitter: sqlite           # sqlite | xarray

# object_of_evaluation: model       # critique #1 — what this investigation
#                                    # primarily evaluates: method | model |
#                                    # hypothesis | composition-protocol. Names the
#                                    # thing the investigation's rigor section judges
#                                    # ("how well the method defends its claims").

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

# ─── RIGOR (the comparative-framing dimension; the rigor scorecard reads this).
#     An investigation scores a comparative_framing GAP until it names the
#     competing frameworks it discriminates AND includes >=1 adversarial study.
#
# competing_frameworks:             # rival models/hypotheses this investigation tests against
#   - name: ""                      # e.g. "alternative mechanism / null model X"
#     prediction: ""                # what it would predict, that we discriminate
#     discriminated_by: ""          # which study/control rules it out
#
# hypotheses:                       # critique #6/#16 — the competing hypotheses this
#                                   # investigation discriminates. `statement` +
#                                   # `predictions` are AUTHORED; `support_log` is
#                                   # COMPUTED (folded from each study's findings +
#                                   # discovery_implications.alternate_hypotheses that
#                                   # carry a matching hypothesis_id).
#   - id: H1
#     statement: ""                 # one-line competing explanation
#     predictions:                  # what this hypothesis predicts, per observable
#       - observable: closure_gap   # an emitted measure / finding-evidence key
#         expected: ""              # e.g. "< 0.1" / "increases with rate" / a band
#     required_controls: []         # controls[].name needed to test it
#     failure_modes: []             # what observation would weaken/exclude it
#     status: open                  # open | supported | weakened | excluded
#     # support_log: []             # COMPUTED — list of per-study entries
#     #                             #   (study, observation, delta) where
#     #                             #   delta is supports | weakens | excludes
#
# NOTE: include at least one member study with `kind: adversarial` (a study
# designed to break the main claim) so the investigation satisfies the
# adversarial-study rigor dimension.

{studies_block}
expert_docs: []

acceptance_criteria: []
"""
