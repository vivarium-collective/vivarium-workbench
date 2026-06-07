# Investigation narrative schema

A framework for writing study-driven investigations that READ as cumulative
scientific arguments instead of sequences of implementation tasks.

The renderer surfaces these fields automatically — declare them in your
`study.yaml` / `investigation.yaml` and they appear in the downloaded report
and the dashboard's investigation page. All fields are optional; only declared
fields render.

## Per-study fields (`studies/<slug>/study.yaml`)

Each of these is a free-text string. Treat them as a 5-question
self-interview every study should answer:

| Field | What it answers | Example phrasing |
|---|---|---|
| `biological_role` | What mechanism does this study introduce? | "Splits the DnaA pool into nucleotide states (apo / DnaA-ATP / DnaA-ADP) and wires intrinsic hydrolysis…" |
| `mechanism_replaced` | What existing heuristic / placeholder does it replace? | "Adds intrinsic hydrolysis MONOMER0-160 → MONOMER0-4565 at rate 0.046 min⁻¹." |
| `dependency_rationale` | Why must this study happen at THIS point in the chain? | "Must precede dnaa-03 (box binding). The downstream study reads DnaA-ATP fraction…" |
| `primary_claim` | What observable would convince us the mechanism is behaving correctly? | "Intrinsic hydrolysis alone cannot bring DnaA-ATP into [0.2, 0.5] — failing-bio is the EXPECTED result, proving the need for the reset network." |
| `primary_visualization` | What's the explanatory figure for the primary claim? | "DnaA nucleotide-state trajectory (apo, ATP, ADP) with the [0.2, 0.5] literature band shaded." |
| `scope_boundary` | What is explicitly in scope for this study? | "Intrinsic hydrolysis ONLY. No extrinsic mechanisms (RIDA / datA / DARS)." |
| `deferred_biology` | What biology is INTENTIONALLY deferred to later studies? | "RIDA → dnaa-06. datA → dnaa-06. SeqA sequestration → separate study." |

Why these specific seven: they force a study author to confront the
biological framing (`biological_role` + `mechanism_replaced`), the dependency
chain (`dependency_rationale`), the validation criterion (`primary_claim` +
`primary_visualization`), and honest scope (`scope_boundary` +
`deferred_biology`). A study that can't fill these in honestly probably isn't
well-scoped.

The renderer surfaces them as a single **"Mechanism narrative"** table at
the top of each study card.

## Per-study: `discovery_implications` (`studies/<slug>/study.yaml`)

An optional block that turns a study's results into alternate hypotheses,
mechanism-update proposals, and selectable follow-up study proposals. It
renders as a **"Discovery Implications"** section in the study card and the
downloadable report, placed after the evidence/follow-ups and before the
Decide box. All fields are optional; the section is hidden when empty.

```yaml
discovery_implications:
  mechanism_uncertainty_addressed: []   # list[str]
  resolved_uncertainties: []            # list[str]
  remaining_uncertainties: []           # list[str]
  alternate_hypotheses:
    - id:
      statement:
      mechanism_elements_affected: []
      why_plausible:
      evidence_for: []
      evidence_against: []
      discriminating_observables: []
  mechanism_update_proposals:
    - mechanism_node_or_edge:
      update_type:        # strengthen|weaken|revise|reject|split|merge
      confidence_change:
      rationale:
      requires_expert_approval: true
  followup_study_proposals:
    - id:
      title:
      study_type:         # parameter_sweep|mechanism_discrimination|model_extension|validation|rerun|calibration
      source_trigger:     # low_confidence|contradiction|ambiguity|missing_parameter|missing_interaction|expert_concern
      target_mechanism_elements: []
      proposed_experiment:
      expected_information_gain:  # low|medium|high
      required_build_changes: []
      required_inputs: []
      blocks_or_unblocks: []
      priority:
      expert_gate_required: true
```

`followup_study_proposals` is the richer successor to the legacy
`follow_up_studies`. Everywhere a follow-up is read (the study card, the
report, and the DAG node popover) the proposals list is preferred, with a
fallback to `follow_up_studies` for back-compat — don't delete the legacy
field.

Each follow-up proposal carries an **"➕ Add to investigation"** button that
seeds a new child study node (`POST /api/study-seed-followup` with
`proposal_id` or `proposal_idx`). The child inherits the proposal's title /
`study_type` / `target_mechanism_elements` / `required_inputs` and gets a
`parent_studies` edge back to the originating study with
`relation: leads-to`.

## Investigation-level: `parts` grouping (`investigations/<slug>/investigation.yaml`)

Group studies into conceptual phases so the report reads as a coherent
mechanism progression. Each `part` has a `name`, optional `overview`, and a
list of study slugs in dependency order:

```yaml
parts:
  - name: "I. Foundations"
    overview: >
      Catalog the baseline before any DnaA mechanism is ported. Every
      downstream study compares observables back to this baseline.
    studies:
      - dnaa-00-parameter-foundation
      - dnaa-01-expression-dynamics

  - name: "II. Nucleotide cycle"
    overview: >
      Split the DnaA pool into apo / ATP / ADP and wire the cycle (intrinsic
      hydrolysis here; full extrinsic reset network lands in Part V).
    studies:
      - dnaa-02-atp-hydrolysis
      - dnaa-02f-equilibrium-cleanup

  - name: "III. Chromosome binding"
    overview: "Add the DnaA-box titration landscape — chromosomal background, oriC, dnaA promoter."
    studies:
      - dnaa-03-box-binding

  - name: "IV. Initiation trigger"
    overview: "Replace mass-per-oriC heuristic with the actual DnaA-occupancy gate."
    studies:
      - dnaa-04-initiation-mechanism

  - name: "V. Reset mechanisms"
    overview: "RIDA + datA + DARS1/2 — the extrinsic reset network deferred from Part II."
    studies:
      - dnaa-06-extrinsic-regulation

  - name: "VI. Validation"
    overview: "Cross-check the full mechanism against an external analytical reference."
    studies:
      - dnaa-05-itv2-comparison
```

Studies not declared in any part still render under an automatic **"Other studies"**
section so nothing silently disappears.

## When `parts` is absent

If `investigation.yaml` has no `parts:` field the renderer falls back to the
original flat list — backwards compatible. Adopt the schema incrementally.
