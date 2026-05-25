"""Investigation spec loading, validation, and simulation expansion.

An Investigation is a directory at ``investigations/<name>/`` containing a
``spec.yaml`` plus generated artifacts (``runs.db``, ``viz/<name>.html``,
``data/*.csv``, ``notes.md``). This module owns:

  - load_spec(path): parse + validate a single spec.yaml
  - expand_simulations(spec): flatten the three simulation kinds into runs

The orchestration (run a composite for each expanded run, persist via
SQLiteEmitter, render visualizations) lives in further functions added in
subsequent tasks.
"""
from __future__ import annotations
import itertools
from pathlib import Path
from typing import Any

import yaml

from .spec_migration import migrate_study_to_v2_vocabulary, migrate_v2_to_v3, migrate_v3_to_v4


class InvestigationSpecError(ValueError):
    """Raised when an investigation spec.yaml fails validation."""


_VALID_KINDS = {"single", "sweep", "seeds"}
_REQUIRED_TOP_LEVEL = ("name", "composite")
_VALID_STATUSES = {"planned", "running", "ran", "complete", "failed", "invalid"}
# Status semantics:
#   planned  — user created the investigation but hasn't run it yet
#   running  — orchestrator is mid-execution
#   ran      — runs completed without error; user hasn't drawn conclusions
#   complete — user-set; signals "I've analyzed the results and we're done"
#   failed   — at least one run failed
#   invalid  — spec.yaml didn't validate


def _validate_composites_list(spec: dict) -> None:
    """Validate the new multi-composite ``composites:`` list shape.

    Checks:
    - Non-empty list of mappings, each with a ``name`` field.
    - Each entry has ``source`` (registered) or ``extends`` (derived), or both.
    - ``extends`` must reference a *previously-declared* composite (no forward refs).
    - No duplicate ``name`` values.
    - If ``runs`` is present it must be a list; every run entry must have
      a ``composite`` field that names a declared composite.
    """
    composites = spec["composites"]
    if not isinstance(composites, list) or not composites:
        raise InvestigationSpecError(
            "'composites' must be a non-empty list of mappings"
        )

    declared_names: list[str] = []
    for i, entry in enumerate(composites):
        if not isinstance(entry, dict):
            raise InvestigationSpecError(f"composites[{i}] must be a mapping")
        name = entry.get("name")
        if not name:
            raise InvestigationSpecError(f"composites[{i}].name is required")
        if name in declared_names:
            raise InvestigationSpecError(
                f"duplicate composite name: {name!r} (composites[{i}])"
            )
        # Must have source OR extends (or both — allowed for override + extend)
        has_source = bool(entry.get("source"))
        has_extends = bool(entry.get("extends"))
        if not has_source and not has_extends:
            raise InvestigationSpecError(
                f"composites[{i}] ({name!r}) must declare 'source' or 'extends'"
            )
        if has_extends:
            parent = entry["extends"]
            if parent not in declared_names:
                raise InvestigationSpecError(
                    f"composites[{i}] extends {parent!r}, which is not declared "
                    f"before it (forward references are not allowed)"
                )
        declared_names.append(name)

    # Validate runs[] if present
    runs = spec.get("runs")
    if runs is not None:
        if not isinstance(runs, list):
            raise InvestigationSpecError("'runs' must be a list")
        for j, run in enumerate(runs):
            if not isinstance(run, dict):
                raise InvestigationSpecError(f"runs[{j}] must be a mapping")
            composite_ref = run.get("composite")
            if not composite_ref:
                raise InvestigationSpecError(
                    f"runs[{j}] must have a 'composite' field referencing a declared composite"
                )
            if composite_ref not in declared_names:
                raise InvestigationSpecError(
                    f"runs[{j}].composite {composite_ref!r} is not in the declared "
                    f"composites list ({declared_names})"
                )


def _v4_field_hint(field_name: str) -> str:
    """Return a suffix hint for v4 reserved-field validation errors.

    Appended to error messages when a v4 reserved field fails shape validation.
    This helps users who authored v3 specs with custom fields that collide with
    v4 reserved names (e.g. ``references: {a: b}`` instead of the required list
    of ``{file: ...}`` dicts).
    """
    return (
        f" (Note: `{field_name}` is reserved by schema_version 4. "
        f"If you authored this as a custom field in a v3 spec, rename it to avoid the collision. "
        f"See docs/concepts/vivarium-dashboard-model.md#v4-reserved-fields for the list.)"
    )


def _validate_study_v3_or_v4(spec: dict) -> None:
    """Validate a schema_version=3 or 4 Study spec.

    v3 shape (distinct from the v2 ``variants:``-as-composites shape):
      - ``baseline``: a non-empty list of ``{name, composite, params}`` mappings.
      - ``variants``: optional list (MAY be empty) of parameter-overlay
        mappings, each with a ``name``.
      - ``runs``, ``visualizations``: optional lists.
      - ``objective``, ``conclusion``: optional.

    v4 shape: adds three new fields:
      - ``tests``: mapping with auto_discover, data_source, pytest_args, last_results
      - ``references``: list of reference mappings (each with at least a 'file' key)
      - ``implementation_tasks``: string field for tracking tasks
    """
    baseline = spec.get("baseline")
    if not isinstance(baseline, list) or not baseline:
        raise InvestigationSpecError(
            "v3 study: 'baseline' must be a non-empty list of composites"
        )
    for i, c in enumerate(baseline):
        if not isinstance(c, dict):
            raise InvestigationSpecError(f"v3 study: baseline[{i}] must be a mapping")
        if not c.get("name"):
            raise InvestigationSpecError(f"v3 study: baseline[{i}].name is required")
        if not c.get("composite"):
            raise InvestigationSpecError(f"v3 study: baseline[{i}].composite is required")

    baseline_names = {c["name"] for c in baseline}
    variants = spec.get("variants", [])
    if not isinstance(variants, list):
        raise InvestigationSpecError("v3 study: 'variants' must be a list")
    for i, v in enumerate(variants):
        if not isinstance(v, dict) or not v.get("name"):
            raise InvestigationSpecError(
                f"v3 study: variants[{i}] must be a mapping with a 'name'"
            )
        base = v.get("base_composite")
        if base and base not in baseline_names:
            raise InvestigationSpecError(
                f"v3 study: variants[{i}].base_composite {base!r} is not a "
                f"declared baseline composite ({sorted(baseline_names)})"
            )
        po = v.get("parameter_overrides", {})
        if not isinstance(po, dict):
            raise InvestigationSpecError(
                f"v3 study: variants[{i}].parameter_overrides must be a mapping"
            )

    runs = spec.get("runs", [])
    if not isinstance(runs, list):
        raise InvestigationSpecError("v3 study: 'runs' must be a list")

    visualizations = spec.get("visualizations", [])
    if not isinstance(visualizations, list):
        raise InvestigationSpecError("v3 study: 'visualizations' must be a list")

    interventions = spec.get("interventions", [])
    if not isinstance(interventions, list):
        raise InvestigationSpecError("v3 study: 'interventions' must be a list")
    for i, iv in enumerate(interventions):
        if not isinstance(iv, dict) or not iv.get("name"):
            raise InvestigationSpecError(
                f"v3 study: interventions[{i}] must be a mapping with a 'name'"
            )

    # v4-only field validation
    if spec.get("schema_version") == 4:
        tests = spec.get("tests")
        if tests is not None and not isinstance(tests, dict):
            raise InvestigationSpecError("tests must be a mapping" + _v4_field_hint("tests"))
        tests = tests or {}
        ds = tests.get("data_source", "latest_run")
        if ds not in ("latest_run", "first_run", "all_runs"):
            raise InvestigationSpecError(
                f"tests.data_source must be one of latest_run|first_run|all_runs, got {ds!r}"
                + _v4_field_hint("tests")
            )
        if not isinstance(tests.get("pytest_args", []), list):
            raise InvestigationSpecError("tests.pytest_args must be a list" + _v4_field_hint("tests"))
        refs = spec.get("references")
        if refs is not None and not isinstance(refs, list):
            raise InvestigationSpecError("references must be a list" + _v4_field_hint("references"))
        refs = refs or []
        for i, ref in enumerate(refs):
            if not isinstance(ref, dict) or not ref.get("file"):
                raise InvestigationSpecError(
                    f"references[{i}] must be a mapping with at least a 'file' key"
                    + _v4_field_hint("references")
                )
        if not isinstance(spec.get("implementation_tasks", ""), str):
            raise InvestigationSpecError(
                "implementation_tasks must be a string" + _v4_field_hint("implementation_tasks")
            )
        _validate_expected_behavior(spec)


def _validate_study_v4_redesign(spec: dict) -> None:
    """Validate the redesigned v4 study spec (question/assumptions/conditions/tests/status).

    This is a *different* schema_version=4 shape than the legacy "v3 + extras"
    one validated by ``_validate_study_v3_or_v4``. The two are disambiguated
    in ``load_spec`` by the presence of a top-level ``conditions:`` block.

    Required fields::

        schema_version: 4
        name: <slug>
        question: <str>
        conditions:
          baseline: {composite: <dotted.path>, params: {...}}
          variants: [ {name, ...}, ... ]            # may be empty
          model_settings: [ {name, gate, ...}, ... ] # may be empty
          # (accepts the legacy `expert_inputs` alias)
        tests: [ {name, measure, pass_if, ...}, ... ]
        status: <str>                                # free-form gate keyword

    Optional: ``assumptions``, ``created``, ``cites``, top-level metadata.
    """
    if not spec.get("question") or not isinstance(spec["question"], str):
        raise InvestigationSpecError(
            "v4 study: 'question' must be a non-empty string"
        )

    cond = spec.get("conditions")
    if not isinstance(cond, dict):
        raise InvestigationSpecError("v4 study: 'conditions' must be a mapping")

    baseline = cond.get("baseline")
    if not isinstance(baseline, dict) or not baseline.get("composite"):
        raise InvestigationSpecError(
            "v4 study: conditions.baseline must be a mapping with at least a "
            "'composite' key (dotted path to the composite function)"
        )

    variants = cond.get("variants", [])
    if not isinstance(variants, list):
        raise InvestigationSpecError("v4 study: conditions.variants must be a list")
    for i, v in enumerate(variants):
        if not isinstance(v, dict) or not v.get("name"):
            raise InvestigationSpecError(
                f"v4 study: conditions.variants[{i}] must be a mapping with a 'name'"
            )

    # ``model_settings`` is the canonical name; ``expert_inputs`` is
    # accepted as a deprecated alias so older study yamls keep working
    # without a forced migration.
    model_settings = cond.get("model_settings")
    if model_settings is None:
        model_settings = cond.get("expert_inputs", [])
    if not isinstance(model_settings, list):
        raise InvestigationSpecError(
            "v4 study: conditions.model_settings must be a list"
        )
    for i, ms in enumerate(model_settings):
        if not isinstance(ms, dict) or not ms.get("name"):
            raise InvestigationSpecError(
                f"v4 study: conditions.model_settings[{i}] must be a mapping with a 'name'"
            )
        gate = ms.get("gate", "optional")
        if gate not in ("optional", "required-before-run"):
            raise InvestigationSpecError(
                f"v4 study: conditions.model_settings[{i}].gate must be one of "
                f"'optional' or 'required-before-run' (got {gate!r})"
            )
    # Promote the legacy alias to the canonical key so downstream code
    # only needs to read one field.
    if "model_settings" not in cond and "expert_inputs" in cond:
        cond["model_settings"] = cond.pop("expert_inputs")

    assumptions = spec.get("assumptions", [])
    if not isinstance(assumptions, list):
        raise InvestigationSpecError("v4 study: 'assumptions' must be a list")
    for i, a in enumerate(assumptions):
        if not isinstance(a, dict) or not a.get("text"):
            raise InvestigationSpecError(
                f"v4 study: assumptions[{i}] must be a mapping with at least a 'text' field"
            )

    tests = spec.get("tests", [])
    if not isinstance(tests, list):
        raise InvestigationSpecError(
            "v4 study: 'tests' must be a list (one entry per pass/fail criterion). "
            "The legacy 'tests: {auto_discover, pytest_args, ...}' mapping is the "
            "older v4 shape — not the redesign."
        )
    for i, t in enumerate(tests):
        if not isinstance(t, dict) or not t.get("name"):
            raise InvestigationSpecError(
                f"v4 study: tests[{i}] must be a mapping with at least a 'name' field"
            )
        meas = t.get("measure")
        if meas is not None and not isinstance(meas, dict):
            raise InvestigationSpecError(
                f"v4 study: tests[{i}].measure must be a mapping when present"
            )


def _project_v4_redesign_to_legacy_view(spec: dict) -> dict:
    """Project a redesigned v4 study spec onto legacy-shaped synonyms.

    The dashboard's study-detail page + report renderer were built against
    the v3 field names (``baseline``, ``variants``, ``key_assumptions``,
    ``behavior_tests``, ``purpose.question``, etc.). To avoid rewriting the
    whole renderer, we synthesise those legacy fields IN-MEMORY from the
    new structured shape. The originals stay in place so v4-aware code
    (e.g. ``render_v4_test_charts``) can still read them.

    Adds:
      - ``purpose.question`` ← top-level ``question``
      - ``key_assumptions`` ← ``[a.text for a in assumptions]``
      - ``baseline`` ← single-entry list synthesised from ``conditions.baseline``
      - ``variants`` ← ``conditions.variants`` projected onto legacy variant shape
      - ``behavior_tests`` ← legacy projection of ``tests[]``
      - ``status`` ← unchanged

    The v3 baseline ``name`` is filled with the study slug since v4
    collapses to a single baseline per study.
    """
    out = dict(spec)
    cond = out.get("conditions") or {}

    # purpose.question — fed by ``question`` paragraph
    if out.get("question"):
        purpose = dict(out.get("purpose") or {})
        purpose.setdefault("question", out["question"])
        out["purpose"] = purpose

    # key_assumptions — flatten assumptions[].text
    if out.get("assumptions") and not out.get("key_assumptions"):
        out["key_assumptions"] = [
            a.get("text", "") for a in out["assumptions"] if isinstance(a, dict)
        ]

    # baseline — synthesise the v3 single-baseline list shape
    bl = cond.get("baseline") or {}
    if bl.get("composite") and not out.get("baseline"):
        out["baseline"] = [{
            "name": out.get("name") or "baseline",
            "composite": bl["composite"],
            "params": dict(bl.get("params") or {}),
        }]

    # variants — re-shape new variants list onto the legacy projection.
    # v4 variants may declare their own ``composite`` (a different
    # generator than the baseline); we pass that through so the variant
    # runner can pick it up without first looking up a baseline entry.
    new_variants = cond.get("variants") or []
    baseline_name = out.get("name") or "baseline"
    baseline_composite = bl.get("composite")
    if new_variants and not out.get("variants"):
        out["variants"] = [{
            "name": v.get("name"),
            "composite": v.get("composite"),  # may be None — falls back to base_composite lookup
            "base_composite": v.get("base_composite", baseline_name),
            "parameter_overrides": dict(v.get("parameter_overrides") or v.get("params") or {}),
            "description": v.get("description", ""),
        } for v in new_variants if isinstance(v, dict)]

    # simulation_set — synthesise the v3 planned-simulations list so the
    # study-detail Simulations tab + the report's "What we ran" section
    # render content for v4 studies. Each variant maps to one planned sim.
    # The baseline itself appears as a leading "baseline" entry so the
    # tab isn't empty when there are no variants.
    if "simulation_set" not in out:
        sim_set = []
        if baseline_composite:
            sim_set.append({
                "name":        baseline_name + "-baseline",
                "kind":        "single",
                "base_model":  baseline_composite,
                "is_baseline": True,
                "description": "Reference run for the study — variants below perturb this.",
                "params":      dict(bl.get("params") or {}),
            })
        for v in new_variants:
            if not isinstance(v, dict):
                continue
            sim_set.append({
                "name":        v.get("name"),
                "kind":        v.get("kind", "single"),
                "base_model":  v.get("composite") or v.get("base_composite") or baseline_composite,
                "description": v.get("description", ""),
                "params":      dict(v.get("parameter_overrides") or v.get("params") or {}),
                "status":      v.get("status", "ready"),
            })
        if sim_set:
            out["simulation_set"] = sim_set

    # behavior_tests — keep ``tests`` as-is for v4-aware code; expose a
    # legacy projection so the v3 detail page has something to enumerate.
    new_tests = out.get("tests")
    if isinstance(new_tests, list) and "behavior_tests" not in out:
        out["behavior_tests"] = [{
            "name": t.get("name"),
            "en": t.get("question") or t.get("name"),
            "measure": t.get("measure"),
            "expect": t.get("pass_if"),
            "status": t.get("status"),
        } for t in new_tests if isinstance(t, dict)]

    # interventions placeholder — the legacy validator + renderer expect
    # a list; v4 doesn't carry interventions yet.
    out.setdefault("interventions", [])
    # runs default — populated by _study_detail_spec from runs.db
    out.setdefault("runs", [])
    return out


def _validate_expected_behavior(spec: dict) -> None:
    """Validate the ``expected_behavior:`` field (v4 structured form).

    Accepts two shapes for backward compatibility:

    1. Legacy free-form — a list of plain strings (v3 / unstructured studies).
       Passes without validation.
    2. Structured DSL (v4) — a list of mappings with at least ``name``,
       ``en``, ``measure``, and ``expect`` fields.

    Each structured entry must have:
      - ``name`` (str, required, non-empty)
      - ``en`` (str, required, non-empty)
      - ``given`` (dict, optional)
      - ``measure`` (dict, required, with at least ``kind``)
      - ``expect`` (dict, required, with at least ``op``)
      - ``status`` (str, optional)
      - ``requires`` (list, optional)

    Unknown ``measure.kind`` / ``expect.op`` values are **not** validated
    here (forward-compatible; the evaluator catches them at run time).
    """
    entries = spec.get("expected_behavior")
    if entries is None:
        return
    if not isinstance(entries, list):
        raise InvestigationSpecError(
            "expected_behavior must be a list"
        )
    if not entries:
        return

    # Detect free-form (all strings) — pass through.
    if all(isinstance(e, str) for e in entries):
        return

    # Mixed or structured form.
    for i, entry in enumerate(entries):
        if isinstance(entry, str):
            continue  # tolerate strings mixed with dicts during migration
        if not isinstance(entry, dict):
            raise InvestigationSpecError(
                f"expected_behavior[{i}] must be a string or mapping"
            )
        # Required fields
        if not entry.get("name"):
            raise InvestigationSpecError(
                f"expected_behavior[{i}].name is required and must be non-empty"
            )
        if not entry.get("en"):
            raise InvestigationSpecError(
                f"expected_behavior[{i}].en is required and must be non-empty"
            )
        measure = entry.get("measure")
        if measure is None:
            raise InvestigationSpecError(
                f"expected_behavior[{i}].measure is required"
            )
        if not isinstance(measure, dict):
            raise InvestigationSpecError(
                f"expected_behavior[{i}].measure must be a mapping"
            )
        if not measure.get("kind"):
            raise InvestigationSpecError(
                f"expected_behavior[{i}].measure.kind is required"
            )
        expect = entry.get("expect")
        if expect is None:
            raise InvestigationSpecError(
                f"expected_behavior[{i}].expect is required"
            )
        if not isinstance(expect, dict):
            raise InvestigationSpecError(
                f"expected_behavior[{i}].expect must be a mapping"
            )
        if not expect.get("op"):
            raise InvestigationSpecError(
                f"expected_behavior[{i}].expect.op is required"
            )
        # Optional fields — type checks only
        given = entry.get("given")
        if given is not None and not isinstance(given, dict):
            raise InvestigationSpecError(
                f"expected_behavior[{i}].given must be a mapping"
            )
        requires = entry.get("requires")
        if requires is not None and not isinstance(requires, list):
            raise InvestigationSpecError(
                f"expected_behavior[{i}].requires must be a list"
            )


# Backwards-compatible alias for tests that pre-date v4 migration
_validate_study_v3 = _validate_study_v3_or_v4


def _validate_variants_list(spec: dict) -> None:
    """Validate the v2 ``variants:`` list shape.

    Checks:
    - Non-empty list of mappings, each with a ``name`` field.
    - Baseline variants have ``source`` and no ``extends``; non-baseline
      variants have ``extends`` referencing another (previously-declared)
      variant by name.
    - No duplicate ``name`` values.
    - ``spec.baseline`` (if present) must name a declared variant.
    """
    variants = spec["variants"]
    if not isinstance(variants, list) or not variants:
        raise InvestigationSpecError(
            "'variants' must be a non-empty list of mappings"
        )

    declared_names: list[str] = []
    for i, entry in enumerate(variants):
        if not isinstance(entry, dict):
            raise InvestigationSpecError(f"variants[{i}] must be a mapping")
        name = entry.get("name")
        if not name:
            raise InvestigationSpecError(f"variants[{i}].name is required")
        if name in declared_names:
            raise InvestigationSpecError(
                f"duplicate variant name: {name!r} (variants[{i}])"
            )
        has_source = bool(entry.get("source"))
        has_extends = bool(entry.get("extends"))
        if not has_source and not has_extends:
            raise InvestigationSpecError(
                f"variants[{i}] ({name!r}) must declare 'source' or 'extends'"
            )
        if has_extends:
            parent = entry["extends"]
            if parent not in declared_names:
                raise InvestigationSpecError(
                    f"variants[{i}] extends {parent!r}, which is not declared "
                    f"before it (forward references are not allowed)"
                )
        declared_names.append(name)

    baseline = spec.get("baseline")
    if baseline is not None and baseline != "":
        if baseline not in declared_names:
            raise InvestigationSpecError(
                f"baseline {baseline!r} not in variants {declared_names}"
            )

    # Validate groups[] if present. Groups are named experimental conditions
    # that bundle 1+ variants; the values in each group's ``variants`` list
    # must reference declared variant names.
    groups = spec.get("groups")
    if groups is not None:
        if not isinstance(groups, list):
            raise InvestigationSpecError("'groups' must be a list")
        seen_group_names: list[str] = []
        for gi, group in enumerate(groups):
            if not isinstance(group, dict):
                raise InvestigationSpecError(f"groups[{gi}] must be a mapping")
            gname = group.get("name")
            if not gname or not isinstance(gname, str):
                raise InvestigationSpecError(
                    f"groups[{gi}].name is required (non-empty string)"
                )
            if gname in seen_group_names:
                raise InvestigationSpecError(
                    f"duplicate group name: {gname!r} (groups[{gi}])"
                )
            seen_group_names.append(gname)
            gvariants = group.get("variants")
            if not isinstance(gvariants, list) or not gvariants:
                raise InvestigationSpecError(
                    f"groups[{gi}] ({gname!r}).variants must be a non-empty list"
                )
            for vref in gvariants:
                if vref not in declared_names:
                    raise InvestigationSpecError(
                        f"groups[{gi}] ({gname!r}) references unknown variant "
                        f"{vref!r}; declared variants: {declared_names}"
                    )

    # Validate runs[] if present (post-migration the legacy ``runs:`` block
    # is preserved alongside variants; its ``composite`` field references a
    # declared variant name).
    runs = spec.get("runs")
    if runs is not None:
        if not isinstance(runs, list):
            raise InvestigationSpecError("'runs' must be a list")
        for j, run in enumerate(runs):
            if not isinstance(run, dict):
                raise InvestigationSpecError(f"runs[{j}] must be a mapping")
            composite_ref = run.get("composite")
            if not composite_ref:
                raise InvestigationSpecError(
                    f"runs[{j}] must have a 'composite' field referencing a declared variant"
                )
            if composite_ref not in declared_names:
                raise InvestigationSpecError(
                    f"runs[{j}].composite {composite_ref!r} is not in the declared "
                    f"variants list ({declared_names})"
                )


def load_spec(path: Path) -> dict:
    """Parse + validate ``investigations/<name>/spec.yaml``.

    Accepts these shapes:

    *V2 variants shape* (``variants:`` key):
      - ``name`` (required)
      - ``variants:`` non-empty list of variant entries; the baseline variant
        has ``source:`` and no ``extends``, derived variants ``extends:``
        another (already-declared) variant.
      - ``baseline:`` optional name of the baseline variant.

    *Legacy multi-composite shape* (``composites:`` key — auto-migrated):
      Auto-migrated in place to the v2 variants shape via
      :func:`migrate_study_to_v2_vocabulary` before parsing.

    *Legacy single-composite shape* (``composite:`` key):
      - ``name`` + ``composite`` (both required)
      - ``simulations:`` list validated as before

    Raises:
        InvestigationSpecError: on any structural problem.
    """
    path = Path(path)

    # ------------------------------------------------------------------
    # Auto-migrate legacy ``composites:`` specs to v2 ``variants:`` shape
    # before parsing. The migration helper is idempotent and atomic.
    # ------------------------------------------------------------------
    try:
        _peek = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        raise InvestigationSpecError(f"malformed YAML: {e}") from e
    if isinstance(_peek, dict) and "composites" in _peek and "variants" not in _peek:
        migrate_study_to_v2_vocabulary(path)

    text = path.read_text()
    try:
        spec = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        raise InvestigationSpecError(f"malformed YAML: {e}") from e

    if not isinstance(spec, dict):
        raise InvestigationSpecError("spec must be a YAML mapping at top level")

    # Phase 1 transition: auto-migrate v2 → v3 on read (in-memory only).
    spec = migrate_v2_to_v3(spec)

    # Phase 2 transition: auto-migrate v3 → v4 on read (in-memory only).
    spec = migrate_v3_to_v4(spec)

    # name is always required
    if not spec.get("name"):
        raise InvestigationSpecError("missing required field: name")

    # ``study_card`` is rendered by walkthrough.js as a multi-row table reading
    # sc.goal / sc.mechanism / sc.why_before_next / sc.expected_result /
    # sc.main_expert_question. If an author wrote the field as a plain string
    # (a common mistake — early scaffold templates suggested prose), the
    # renderer silently drops it (sc.goal is undefined on a string). Auto-
    # promote string -> {goal: string} so the card renders the authored prose
    # in the "Goal" row instead of being empty.
    sc = spec.get("study_card")
    if isinstance(sc, str) and sc.strip():
        spec["study_card"] = {"goal": sc.strip()}

    # NEW v4 study shape (question / assumptions / conditions / tests / status).
    # Distinct from the legacy "v4 = v3 + extras" shape by the presence of a
    # top-level ``conditions:`` block. Validate the new fields, then project
    # them onto legacy-shaped synonyms so the existing detail page renders
    # without needing a parallel template.
    if spec.get("schema_version") == 4 and isinstance(spec.get("conditions"), dict):
        _validate_study_v4_redesign(spec)
        spec = _project_v4_redesign_to_legacy_view(spec)
        return spec

    # v3/v4 (Studies) shape: single baseline + optional variants/runs/etc.
    # Detect v3-shape even when schema_version is absent: a top-level
    # ``baseline:`` that is a list-of-mappings is v3 (legacy v2 used a
    # string ``baseline:`` naming a single variant, or no ``baseline:`` at all).
    baseline_field = spec.get("baseline")
    looks_like_v3 = isinstance(baseline_field, list) and all(
        isinstance(b, dict) for b in baseline_field
    )
    if spec.get("schema_version") in (3, 4) or looks_like_v3:
        spec.setdefault("schema_version", 3)
        spec = migrate_v3_to_v4(spec)
        _validate_study_v3_or_v4(spec)
        return spec

    has_variants_list = "variants" in spec
    has_composites_list = "composites" in spec
    has_legacy_composite = "composite" in spec and spec["composite"]

    if has_variants_list:
        # V2 variants shape
        _validate_variants_list(spec)
    elif has_composites_list:
        # Transient state: a legacy composites-shape spec that the migration
        # helper declined to rewrite (e.g. empty list). Fall through to the
        # old validator so we don't lose coverage.
        _validate_composites_list(spec)
    elif has_legacy_composite:
        # Legacy single-composite shape — validate the simulations block as before
        sims = spec.get("simulations") or []
        if not isinstance(sims, list):
            raise InvestigationSpecError("simulations must be a list")

        for i, sim in enumerate(sims):
            if not isinstance(sim, dict):
                raise InvestigationSpecError(f"simulations[{i}] must be a mapping")
            if not sim.get("name"):
                raise InvestigationSpecError(f"simulations[{i}].name is required")
            kind = sim.get("kind")
            if kind not in _VALID_KINDS:
                raise InvestigationSpecError(
                    f"simulations[{i}].kind must be one of {sorted(_VALID_KINDS)}; got {kind!r}"
                )
            if kind == "sweep":
                sweep_over = sim.get("sweep_over") or {}
                if not isinstance(sweep_over, dict) or not sweep_over:
                    raise InvestigationSpecError(
                        f"simulations[{i}].sweep_over must be a non-empty mapping"
                    )
                for k, vals in sweep_over.items():
                    if not isinstance(vals, list) or not vals:
                        raise InvestigationSpecError(
                            f"simulations[{i}].sweep_over.{k} must be a non-empty list"
                        )
            elif kind == "seeds":
                n = sim.get("n_seeds", 0)
                if not isinstance(n, int) or n < 1:
                    raise InvestigationSpecError(
                        f"simulations[{i}].n_seeds must be a positive integer; got {n!r}"
                    )
            steps = sim.get("steps", 0)
            if not isinstance(steps, int) or steps < 1:
                raise InvestigationSpecError(
                    f"simulations[{i}].steps must be a positive integer"
                )
    else:
        # Neither shape present
        raise InvestigationSpecError(
            "spec must declare either 'variants' (v2 study shape) "
            "or 'composite' (legacy single-composite shape)"
        )

    observables = spec.get("observables") or []
    if not isinstance(observables, list):
        raise InvestigationSpecError("observables must be a list")

    visualizations = spec.get("visualizations") or []
    if not isinstance(visualizations, list):
        raise InvestigationSpecError("visualizations must be a list")
    for i, viz in enumerate(visualizations):
        if not isinstance(viz, dict):
            raise InvestigationSpecError(f"visualizations[{i}] must be a mapping")
        if not viz.get("name"):
            raise InvestigationSpecError(f"visualizations[{i}].name is required")
        if not viz.get("address"):
            raise InvestigationSpecError(f"visualizations[{i}].address is required")

    return spec


def expand_simulations(spec: dict) -> list[dict]:
    """Flatten ``spec.simulations`` into a list of concrete runs.

    Each returned entry has keys:
      sim_name: str  — name of the originating simulation block
      run_label: str — unique label within the simulation (e.g. 'rate=0.1', 'seed=2')
      overrides: dict — composite parameter overrides for this run
      steps: int     — number of composite ticks
    """
    out: list[dict] = []
    for sim in spec.get("simulations") or []:
        kind = sim["kind"]
        steps = int(sim["steps"])
        if kind == "single":
            out.append({
                "sim_name": sim["name"],
                "run_label": "single",
                "overrides": dict(sim.get("overrides") or {}),
                "steps": steps,
            })
        elif kind == "sweep":
            sweep_over = sim["sweep_over"]
            base = sim.get("base_overrides") or {}
            keys = list(sweep_over.keys())
            value_lists = [sweep_over[k] for k in keys]
            for combo in itertools.product(*value_lists):
                ovr = dict(base)
                for k, v in zip(keys, combo):
                    ovr[k] = v
                label = ", ".join(f"{k}={ovr[k]}" for k in keys)
                out.append({
                    "sim_name": sim["name"],
                    "run_label": label,
                    "overrides": ovr,
                    "steps": steps,
                })
        elif kind == "seeds":
            n = int(sim["n_seeds"])
            base = sim.get("base_overrides") or {}
            for k in range(n):
                ovr = dict(base)
                ovr["seed"] = k
                out.append({
                    "sim_name": sim["name"],
                    "run_label": f"seed={k}",
                    "overrides": ovr,
                    "steps": steps,
                })
    return out


# ----------------------------------------------------------------------------
# Results aggregation + overlay resolution
# ----------------------------------------------------------------------------

import csv
import json
import sqlite3


def gather_results(spec: dict, db_path: Path) -> dict:
    """Read the investigation's runs.db and group trajectories by sim_name.

    Returns: {<sim_name>: {"runs": [{"run_id", "params", "trajectory"}, ...]}}

    Trajectory shape: [{"step", "time", "state"}, ...] where ``state`` is a
    parsed JSON dict (whatever SQLiteEmitter wrote).
    """
    db_path = Path(db_path)
    if not db_path.is_file():
        return {}
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        out: dict[str, dict] = {}
        # Check whether the SQLiteEmitter ever wrote a history row. If every
        # run failed before the first emit, the history table won't exist
        # — return empty-trajectory results so visualizations can show a
        # warning rather than crashing.
        has_history = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='history'"
        ).fetchone() is not None
        # All metadata rows for this investigation
        meta_rows = conn.execute(
            "SELECT run_id, sim_name, params_json FROM runs_meta"
        ).fetchall()
        run_meta: dict[str, dict] = {}
        for row in meta_rows:
            try:
                params = json.loads(row["params_json"] or "{}")
            except json.JSONDecodeError:
                params = {}
            run_meta[row["run_id"]] = {
                "sim_name": row["sim_name"] or "default",
                "params": params,
            }
        # Trajectories per run — skip the history query entirely if the
        # SQLiteEmitter never wrote a row (table absent).
        for run_id, meta in run_meta.items():
            if has_history:
                traj_rows = conn.execute(
                    "SELECT step, global_time AS time, state FROM history "
                    "WHERE simulation_id=? ORDER BY step ASC",
                    (run_id,),
                ).fetchall()
            else:
                traj_rows = []
            traj = []
            for tr in traj_rows:
                try:
                    state = json.loads(tr["state"]) if tr["state"] else {}
                except json.JSONDecodeError:
                    state = {}
                traj.append({"step": tr["step"], "time": tr["time"], "state": state})
            sim_name = meta["sim_name"]
            out.setdefault(sim_name, {"runs": []})
            out[sim_name]["runs"].append({
                "run_id": run_id, "params": meta["params"], "trajectory": traj,
            })
    finally:
        conn.close()
    return out


# ----------------------------------------------------------------------------
# Visualization v2 — emitter-driven, composite-dispatched
# ----------------------------------------------------------------------------

def gather_emitter_outputs(db_path: Path) -> dict:
    """Flatten runs.db into per-observable trajectories + emitter schemas.

    Returns:
        {
          "schemas": {<run_id>: {<observable>: <type_str>}, ...},
          "by_sim": {<sim_name>: [{run_id, sim_name, params, observables}, ...]},
        }
    """
    db_path = Path(db_path)
    if not db_path.is_file():
        return {"schemas": {}, "by_sim": {}}

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        meta_rows = conn.execute(
            "SELECT run_id, sim_name, params_json FROM runs_meta"
        ).fetchall()
        run_meta = {}
        for r in meta_rows:
            try:
                params = json.loads(r["params_json"] or "{}")
            except json.JSONDecodeError:
                params = {}
            run_meta[r["run_id"]] = {
                "sim_name": r["sim_name"] or "default",
                "params": params,
            }

        schemas = {}
        try:
            sim_rows = conn.execute(
                "SELECT simulation_id, emit_schema FROM simulations"
            ).fetchall()
            for r in sim_rows:
                if r["emit_schema"]:
                    try:
                        schemas[r["simulation_id"]] = json.loads(r["emit_schema"])
                    except json.JSONDecodeError:
                        pass
        except sqlite3.OperationalError:
            pass

        by_sim = {}
        has_history = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='history'"
        ).fetchone() is not None
        for run_id, meta in run_meta.items():
            observables = {}
            if has_history:
                rows = conn.execute(
                    "SELECT step, global_time, state FROM history "
                    "WHERE simulation_id=? ORDER BY step ASC",
                    (run_id,),
                ).fetchall()
                for row in rows:
                    try:
                        state = json.loads(row["state"]) if row["state"] else {}
                    except json.JSONDecodeError:
                        continue
                    for k, v in state.items():
                        observables.setdefault(k, []).append(v)
                    # Only fall back to global_time if state doesn't carry a "time" key
                    if "time" not in state:
                        observables.setdefault("time", []).append(row["global_time"])
            sim_name = meta["sim_name"]
            by_sim.setdefault(sim_name, []).append({
                "run_id": run_id,
                "sim_name": sim_name,
                "params": meta["params"],
                "observables": observables,
            })
        return {"schemas": schemas, "by_sim": by_sim}
    finally:
        conn.close()


def inject_emitter_step(doc: dict, observables: list) -> dict:
    """Return ``doc`` with its emitter step rewritten/added to record the observable paths.

    ``observables`` is the spec.yaml.observables list of ``{path: [...]}`` dicts.
    Paths not present in ``doc['state']`` are silently skipped (the orchestrator
    can log a warning at run time).

    Special case: a single observable with ``path: []`` is the "emit entire state"
    sentinel — wires the emitter at the document root via ``inputs: {state: []}``.
    """
    import copy
    out = copy.deepcopy(doc)
    state = out.setdefault('state', {})

    obs_list = observables or []
    emit_all = (len(obs_list) == 1 and obs_list[0].get('path') == [])

    if emit_all:
        state['emitter'] = {
            '_type': 'step',
            'address': 'local:SQLiteEmitter',
            'config': {'emit': {}, 'emit_all': True},
            'inputs': {'state': []},
        }
        return out

    inputs: dict = {}
    emit_schema: dict = {}
    for obs in obs_list:
        path = obs.get('path') or []
        if not path:
            continue
        # Walk to verify the path exists in state; capture leaf type if recorded
        node = state
        for seg in path:
            if not isinstance(node, dict) or seg not in node:
                node = None
                break
            node = node[seg]
        if node is None:
            continue
        port_name = path[-1]
        inputs[port_name] = list(path)
        if isinstance(node, dict) and node.get('_type'):
            emit_schema[port_name] = node['_type']
        else:
            emit_schema[port_name] = 'any'

    state['emitter'] = {
        '_type': 'step',
        'address': 'local:SQLiteEmitter',
        'config': {'emit': emit_schema},
        'inputs': inputs,
    }
    return out


def _resolve_observable(observables: dict, path: str) -> list | None:
    """Resolve a dotted observable path against per-tick observables.

    The gather pipeline records observables as
    ``{top_level_key: [value_at_tick_0, value_at_tick_1, ...]}`` — top-level
    only. For nested listener fields like
    ``listeners.dnaA_cycle.atp_count`` we need to walk into the per-tick
    dict at each index. Returns a list with one element per tick (None if
    the segment is missing at that tick) or None if the top-level key
    doesn't exist at all.
    """
    if not path:
        return None

    def _walk(parts: list[str]) -> list | None:
        series = observables.get(parts[0])
        if series is None:
            return None
        if len(parts) == 1:
            return series
        out = []
        for tick_val in series:
            v = tick_val
            for seg in parts[1:]:
                if isinstance(v, dict) and seg in v:
                    v = v[seg]
                else:
                    v = None
                    break
            out.append(v)
        return out

    def _has_scalar(series: list | None) -> bool:
        return series is not None and any(
            v is not None and not isinstance(v, (dict, list)) for v in series)

    parts = path.split(".")
    res = _walk(parts)
    # v2ecoli single-cell composites scope every listener store under
    # agents/0/...; study inputs_map paths are declared in biology form
    # (listeners.dnaA_binding...). If the literal path yields no usable scalar
    # (missing, or an empty-container '{}' capture per tick), retry under
    # agents.0. so the viz gets real data instead of crashing on dicts.
    if not _has_scalar(res) and parts[0] != "agents":
        ag = _walk(["agents", "0"] + parts)
        if _has_scalar(ag):
            return ag
    return res


def build_viz_composite(viz_spec: dict, gathered: dict, core_registry: dict) -> dict:
    """Build the small composite that dispatches one visualization."""
    address = viz_spec["address"]
    class_key = address.split(":", 1)[1] if ":" in address else address
    viz_class = core_registry.get(class_key)
    if viz_class is None:
        raise KeyError(f"Visualization class not registered: {address}")

    config = dict(viz_spec.get("config") or {})
    inputs_map = config.get("inputs_map") or {}
    sources = config.get("sources")

    try:
        instance = viz_class.__new__(viz_class)
        declared_inputs = instance.inputs()
    except Exception:
        declared_inputs = {}

    candidate_runs = []
    by_sim = gathered.get("by_sim") or {}
    for sim_name, runs in by_sim.items():
        if sources and sim_name not in sources:
            continue
        candidate_runs.extend(runs)

    inputs_store = {}
    run_labels = []
    for port, port_type in declared_inputs.items():
        observable_name = inputs_map.get(port, port)
        per_run_values = []
        for run in candidate_runs:
            vals = _resolve_observable(
                run.get("observables", {}) or {}, observable_name)
            if vals is None:
                continue
            per_run_values.append(vals)
            params = run.get("params") or {}
            label = ", ".join(f"{k}={v}" for k, v in sorted(params.items())) \
                    or run["run_id"][-8:]
            if label not in run_labels:
                run_labels.append(label)
        if port_type == "list[float]":
            if len(per_run_values) == 1:
                inputs_store[port] = per_run_values[0]
            else:
                inputs_store[port] = per_run_values
        elif port_type == "float":
            inputs_store[port] = per_run_values[0][-1] if per_run_values else None
        elif port_type == "list[list[float]]":
            inputs_store[port] = per_run_values
        else:
            inputs_store[port] = per_run_values[0] if per_run_values else None

    inputs_store["_run_labels"] = run_labels

    return {
        "inputs_store": inputs_store,
        "output_store": "",
        "visualization": {
            "_type": "step",
            "address": address,
            "config": {k: v for k, v in config.items() if k not in ("inputs_map", "sources")},
            "inputs": {port: ["inputs_store", port] for port in declared_inputs},
            "outputs": {"html": ["output_store"]},
        },
    }


def load_overlays(spec: dict, viz_config: dict, ws_root: Path,
                  investigation_name: str) -> list[dict]:
    """Resolve each overlay entry into a uniform payload.

    Args:
        spec: the parent investigation spec (for context if needed)
        viz_config: the visualization dict, expected to have an 'overlays' list
        ws_root: workspace root path (overlay files are resolved relative to
                 investigations/<investigation_name>/)
        investigation_name: directory name of the current investigation

    Returns: list of overlay payload dicts. Failed lookups become
        {"kind": "warning", "message": "..."} so visualizations can either
        skip them or annotate the figure.
    """
    overlays = viz_config.get("overlays") or []
    payload: list[dict] = []
    inv_dir = Path(ws_root) / "investigations" / investigation_name

    for ov in overlays:
        kind = ov.get("kind")
        if kind == "reference-range":
            payload.append({
                "kind": "reference-range",
                "y_min": ov.get("y_min"),
                "y_max": ov.get("y_max"),
                "label": ov.get("label", "reference range"),
            })
        elif kind == "experimental-points":
            data_rel = ov.get("data") or ""
            data_path = inv_dir / data_rel
            if not data_path.is_file():
                payload.append({
                    "kind": "warning",
                    "message": f"experimental-points file missing: {data_rel}",
                })
                continue
            x_col = ov.get("x_column", "x")
            y_col = ov.get("y_column", "y")
            try:
                with data_path.open() as fh:
                    reader = csv.DictReader(fh)
                    points = [{"x": r.get(x_col), "y": r.get(y_col)} for r in reader]
            except Exception as e:
                payload.append({
                    "kind": "warning",
                    "message": f"experimental-points read failed: {e}",
                })
                continue
            payload.append({
                "kind": "experimental-points",
                "label": ov.get("label", "experimental"),
                "points": points,
            })
        elif kind == "cross-investigation-series":
            other_name = ov.get("investigation", "")
            other_db = Path(ws_root) / "investigations" / other_name / "runs.db"
            if not other_db.is_file():
                payload.append({
                    "kind": "warning",
                    "message": f"cross-investigation reference not found: {other_name}",
                })
                continue
            other_obs = ov.get("observable", "")
            xs, ys = [], []
            conn = sqlite3.connect(str(other_db))
            try:
                rows = conn.execute(
                    "SELECT global_time, state FROM history ORDER BY step ASC"
                ).fetchall()
                for tm, st in rows:
                    try:
                        s = json.loads(st) if st else {}
                    except json.JSONDecodeError:
                        continue
                    if other_obs in s:
                        xs.append(tm)
                        ys.append(s[other_obs])
            finally:
                conn.close()
            if not xs:
                payload.append({
                    "kind": "warning",
                    "message": f"cross-investigation observable not present: {other_obs} in {other_name}",
                })
                continue
            payload.append({
                "kind": "cross-investigation-series",
                "label": ov.get("label", f"{other_name}.{other_obs}"),
                "style": ov.get("style", "dashed-line"),
                "x": xs, "y": ys,
            })
        else:
            payload.append({
                "kind": "warning",
                "message": f"unknown overlay kind: {kind!r}",
            })
    return payload


# ----------------------------------------------------------------------------
# Effective-status derivation (F1 of the framework cleanup)
# ----------------------------------------------------------------------------


# Multi-axis precedence: when computing a single "headline" status string,
# the most-downstream axis wins. A study with gate_status: passed should be
# shown as "passed" even if its simulation_status is also set to "ran".
_MULTI_AXIS_PRECEDENCE = (
    "gate_status",
    "evaluation_status",
    "simulation_status",
    "implementation_status",
    "design_status",
    "expert_review_status",
)


def effective_status(spec: dict) -> str | None:
    """Return the single headline status string for a study.

    Multi-axis precedence (most-downstream wins): gate > evaluation >
    simulation > implementation > design > expert_review. Falls back to
    the legacy `status` field when no multi-axis axis is set, emitting a
    DeprecationWarning naming the study.

    Returns None when no status of any flavour is set — callers can
    decide whether to render that as "planned", "unknown", or empty.
    """
    for axis in _MULTI_AXIS_PRECEDENCE:
        val = spec.get(axis)
        if val:
            return val

    legacy = spec.get("status")
    if legacy:
        import warnings
        warnings.warn(
            f"Study {spec.get('name', '<unnamed>')!r}: legacy `status` "
            f"field ({legacy!r}) is the only status source. The canonical "
            "fields are the Pass A multi-axis status flags "
            "(design_status, implementation_status, simulation_status, "
            "evaluation_status, gate_status, expert_review_status). "
            "Set the appropriate axis to drop this warning.",
            DeprecationWarning,
            stacklevel=2,
        )
        return legacy

    return None


# ----------------------------------------------------------------------------
# DAG-edge normalization (F3 of the framework cleanup)
# ----------------------------------------------------------------------------


def normalize_dag_edges(spec: dict) -> list[dict]:
    """Unified read of a study's DAG dependencies.

    Two declaration forms have coexisted: the canonical Pass A field
    ``pipeline_gate.prerequisites`` (Section 2 of the 8-section structure)
    and the legacy ``parent_studies``. Both accept the same item shapes
    (bare slug string, or ``{study, condition}`` object), but only
    ``parent_studies`` is consumed by the dashboard today. This helper
    is the single read path going forward: ``pipeline_gate.prerequisites``
    wins, with a transparent fallback to ``parent_studies`` for v3 specs
    that haven't been migrated yet.

    Returns a normalized list of ``{study: str, condition: str}`` dicts.
    Items in ``pipeline_gate.prerequisites`` may carry additional Pass A
    fields (``required_gate_status``, ``outputs_used``, etc.); those are
    passed through verbatim.

    When the canonical source is empty/absent AND the legacy source has
    entries, a one-time DeprecationWarning is emitted naming the study
    so the workspace can migrate.
    """
    pg = spec.get("pipeline_gate") or {}
    prereqs = pg.get("prerequisites") if isinstance(pg, dict) else None

    legacy = spec.get("parent_studies") or []
    using_legacy = bool(legacy) and not prereqs

    if using_legacy:
        import warnings
        warnings.warn(
            f"Study {spec.get('name', '<unnamed>')!r}: "
            "`parent_studies` is the legacy DAG-edge field. The canonical "
            "location is `pipeline_gate.prerequisites` (Section 2 of the "
            "8-section structure). The dashboard still reads `parent_studies` "
            "as a back-compat fallback, but a future version will require "
            "the canonical field. Migrate with /pbg-study migrate-dag.",
            DeprecationWarning,
            stacklevel=2,
        )

    source = prereqs if prereqs else legacy
    if not source:
        return []

    out: list[dict] = []
    for entry in source:
        if isinstance(entry, str):
            out.append({"study": entry, "condition": "tests-passed"})
        elif isinstance(entry, dict) and entry.get("study"):
            # Preserve any extra Pass A fields (required_gate_status etc.)
            # so downstream consumers can use them without re-reading the
            # raw spec.
            normalized = dict(entry)
            normalized.setdefault("condition", "tests-passed")
            out.append(normalized)
        # Silently drop malformed entries; the JSON-schema validator is the
        # right place to surface those as errors.
    return out


# ----------------------------------------------------------------------------
# Spec status updater + run lock + orchestrator
# ----------------------------------------------------------------------------

import datetime


def update_spec_status(ws_root: Path, name: str, *, status: str,
                       last_run: str | None = None) -> None:
    """Update the status + last_run fields in investigations/<name>/spec.yaml.

    Preserves the rest of the spec verbatim by parsing → mutating → re-dumping.
    """
    if status not in _VALID_STATUSES:
        raise ValueError(f"invalid status {status!r}; must be one of {sorted(_VALID_STATUSES)}")
    spec_path = Path(ws_root) / "investigations" / name / "spec.yaml"
    spec = yaml.safe_load(spec_path.read_text()) or {}
    spec["status"] = status
    if last_run is not None:
        spec["last_run"] = last_run
    spec_path.write_text(yaml.safe_dump(spec, sort_keys=False))


def _lock_path(ws_root: Path, name: str) -> Path:
    return Path(ws_root) / "investigations" / name / ".run.lock"


def acquire_run_lock(ws_root: Path, name: str) -> bool:
    """Try to acquire an exclusive run lock for one investigation.

    Returns True if acquired, False if another run is already in progress.
    """
    lock = _lock_path(ws_root, name)
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = lock.open("x")
        fd.write(str(datetime.datetime.utcnow()))
        fd.close()
        return True
    except FileExistsError:
        return False


def release_run_lock(ws_root: Path, name: str) -> None:
    """Release the run lock. No-op if the lock doesn't exist."""
    lock = _lock_path(ws_root, name)
    try:
        lock.unlink()
    except FileNotFoundError:
        pass


def _load_composite_doc(inv_dir: Path, composite_name: str) -> dict:
    """Load a composite document from ``<inv_dir>/composites/<name>.yaml``.

    Raises FileNotFoundError if the document does not exist.
    """
    doc_path = inv_dir / "composites" / f"{composite_name}.yaml"
    if not doc_path.is_file():
        raise FileNotFoundError(
            f"composite document not found: {doc_path}"
        )
    return yaml.safe_load(doc_path.read_text()) or {}


def _apply_parameter_overrides(doc: dict, params: dict) -> dict:
    """Best-effort overlay of ``params`` onto ``doc['state']``.

    Each key in ``params`` is treated as a dot-separated path into the state
    tree (e.g. ``chromosome.DnaA_count``).  Unknown paths are silently ignored
    so that the orchestrator can warn rather than crash.
    """
    if not params:
        return doc
    import copy
    out = copy.deepcopy(doc)
    state = out.get("state") or {}
    for key, value in params.items():
        segments = key.split(".")
        node = state
        for seg in segments[:-1]:
            if isinstance(node, dict) and seg in node:
                node = node[seg]
            else:
                node = None
                break
        if isinstance(node, dict) and segments[-1] in node:
            leaf = node[segments[-1]]
            if isinstance(leaf, dict):
                leaf["_default"] = value
            else:
                node[segments[-1]] = value
        # Unknown paths are silently skipped (orchestrator may log separately)
    return out


def run_investigation(ws_root: Path, name: str, *,
                      run_one_composite: callable,
                      core_registry: dict,
                      build_and_run=None) -> dict:
    """Top-level orchestrator. Returns a summary dict.

    Supports two spec shapes:

    *Multi-composite* (``composites:`` + ``runs:`` keys):
        For each run entry in ``spec.runs``:
          1. Load the composite document from ``composites/<run.composite>.yaml``.
          2. Inject the emitter step via ``inject_emitter_step(doc, spec.observables)``.
          3. Apply per-run ``params`` via ``_apply_parameter_overrides``.
          4. Dispatch via ``run_one_composite(..., state_doc=doc)``.

    *Legacy single-composite* (``composite:`` + ``simulations:`` keys):
        Expand simulations via ``expand_simulations`` and dispatch each run
        without a ``state_doc`` (the factory resolves the composite by ID).

    Args:
        ws_root: workspace root path
        name: investigation directory name
        run_one_composite: callable(*, spec_id, overrides, steps, sim_name,
            run_id, db_file[, state_doc]) -> {"status": "completed"|"failed", "error"?: str}
            (injected so the orchestrator can be unit-tested with a mock;
            in production the server passes a function that resolves the
            composite + subprocess-runs it)
        core_registry: process_bigraph core.link_registry — used to look up
            Visualization classes by address (e.g. "local:TimeSeriesPlot")
        build_and_run: optional callable(doc, core_registry) -> str passed through
            to render_visualizations.  When None and the spec has no visualizations,
            the viz pass is skipped cleanly.  When None but visualizations are
            present, render_visualizations raises ValueError.

    Side effects: writes runs.db + viz/<name>.html, updates spec.yaml.
    Each invocation APPENDS new runs to runs.db (does not clear prior runs).

    Returns:
        {name, n_runs, n_visualizations, status, viz_paths, errors}
    """
    from vivarium_dashboard.lib import composite_runs as cr

    ws_root = Path(ws_root)
    inv_dir = ws_root / "investigations" / name
    spec_path = inv_dir / "spec.yaml"
    if not spec_path.is_file():
        # v3 convention uses study.yaml; fall back to it when spec.yaml is absent.
        alt = inv_dir / "study.yaml"
        if alt.is_file():
            spec_path = alt
    spec = load_spec(spec_path)  # raises InvestigationSpecError on bad shape

    if not acquire_run_lock(ws_root, name):
        return {"name": name, "error": "investigation is already running",
                "status": "running"}

    # Determine which orchestration path to use. Post-A2, legacy
    # ``composites:`` specs are auto-migrated to ``variants:`` on read; both
    # keys mark a multi-composite spec, while ``runs:`` survives migration.
    is_multi_composite = ("variants" in spec or "composites" in spec) and "runs" in spec

    try:
        update_spec_status(ws_root, name, status="running")
        db_file = str(inv_dir / "runs.db")
        conn = cr.connect(db_file)
        # Add sim_name column to runs_meta if our local copy doesn't have it.
        try:
            conn.execute("ALTER TABLE runs_meta ADD COLUMN sim_name TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists

        errors: list[dict] = []
        any_failed = False
        n_runs = 0
        import time as _time

        if is_multi_composite:
            # ----------------------------------------------------------------
            # New multi-composite path: iterate spec.runs, load composite
            # documents from disk, inject emitter, dispatch with state_doc=.
            # ----------------------------------------------------------------
            observables = spec.get("observables") or []
            for run_entry in spec.get("runs") or []:
                composite_name = run_entry["composite"]
                overrides = dict(run_entry.get("params") or {})
                steps = int(run_entry.get("steps", 1))

                try:
                    raw_doc = _load_composite_doc(inv_dir, composite_name)
                except FileNotFoundError as e:
                    errors.append({"composite": composite_name, "error": str(e)})
                    any_failed = True
                    continue

                doc = inject_emitter_step(raw_doc, observables)
                doc = _apply_parameter_overrides(doc, overrides)

                run_id = cr.generate_run_id(composite_name, overrides)
                cr.save_metadata(conn, spec_id=composite_name, run_id=run_id,
                                  params=overrides,
                                  label=composite_name,
                                  started_at=_time.time(),
                                  n_steps=steps)
                conn.execute("UPDATE runs_meta SET sim_name=? WHERE run_id=?",
                              (composite_name, run_id))
                conn.commit()

                res = run_one_composite(
                    spec_id=composite_name,
                    overrides=overrides,
                    steps=steps,
                    sim_name=composite_name,
                    run_id=run_id,
                    db_file=db_file,
                    state_doc=doc,
                )
                n_runs += 1
                if res.get("status") == "completed" or res.get("ok"):
                    cr.complete_metadata(conn, run_id=run_id, n_steps=steps,
                                          status="completed")
                else:
                    any_failed = True
                    cr.complete_metadata(conn, run_id=run_id, n_steps=0, status="failed")
                    errors.append({"run_id": run_id, "composite": composite_name,
                                   "error": res.get("error", "")})

        else:
            # ----------------------------------------------------------------
            # Legacy single-composite path: expand simulations, dispatch by
            # spec_id without a pre-built state_doc.
            # ----------------------------------------------------------------
            expanded = expand_simulations(spec)
            n_runs = len(expanded)
            for run in expanded:
                run_id = cr.generate_run_id(spec["composite"], run["overrides"])
                cr.save_metadata(conn, spec_id=spec["composite"], run_id=run_id,
                                  params=run["overrides"],
                                  label=run["run_label"],
                                  started_at=_time.time(),
                                  n_steps=run["steps"])
                conn.execute("UPDATE runs_meta SET sim_name=? WHERE run_id=?",
                              (run["sim_name"], run_id))
                conn.commit()
                res = run_one_composite(
                    spec_id=spec["composite"],
                    overrides=run["overrides"],
                    steps=run["steps"],
                    sim_name=run["sim_name"],
                    run_id=run_id,
                    db_file=db_file,
                )
                if res.get("status") == "completed":
                    cr.complete_metadata(conn, run_id=run_id, n_steps=run["steps"],
                                          status="completed")
                else:
                    any_failed = True
                    cr.complete_metadata(conn, run_id=run_id, n_steps=0, status="failed")
                    errors.append({"run_id": run_id, "error": res.get("error", "")})

        conn.close()

        # Visualization pass — skipped cleanly when build_and_run is None and
        # spec has no visualizations (backward-compat with tests that omit both).
        viz_paths = render_visualizations(spec, inv_dir, name,
                                          core_registry=core_registry,
                                          build_and_run=build_and_run)

        # 'ran' = runs finished without error; user explicitly sets 'complete'
        # after analyzing results (avoids over-claiming "complete" before the
        # researcher has drawn conclusions).
        final_status = "ran" if not any_failed else "failed"
        update_spec_status(ws_root, name, status=final_status,
                           last_run=datetime.datetime.utcnow().isoformat())

        return {
            "name": name,
            "n_runs": n_runs,
            "n_visualizations": len(viz_paths),
            "status": final_status,
            "viz_paths": [str(p) for p in viz_paths],
            "errors": errors,
        }
    except Exception:
        update_spec_status(ws_root, name, status="failed")
        raise
    finally:
        release_run_lock(ws_root, name)


def render_visualizations(spec: dict, inv_dir: Path, name: str, *,
                          core_registry: dict,
                          build_and_run=None) -> list[Path]:
    """Render every viz in ``spec.visualizations`` against the investigation's runs.db.

    For each viz:
      1. Build the viz composite via ``build_viz_composite``.
      2. Run it for 1 step via ``build_and_run(doc, core_registry) -> str``.
      3. Write the resulting HTML to ``<inv_dir>/viz/<viz_name>.html``.
      4. On any error, write an error stub HTML (other vizzes still render).

    Args:
        spec: investigation spec dict
        inv_dir: path to the investigation directory (contains runs.db)
        name: investigation name (used only for error messages / doc purposes)
        core_registry: mapping of class key -> Visualization class
        build_and_run: callable(doc, core_registry) -> str that runs the composite
            and returns an HTML string.  Must be provided when there are
            visualizations to render; raises ValueError otherwise.
    """
    inv_dir = Path(inv_dir)
    viz_dir = inv_dir / "viz"
    viz_dir.mkdir(parents=True, exist_ok=True)

    visualizations = spec.get("visualizations") or []
    if not visualizations:
        return []

    if build_and_run is None:
        raise ValueError(
            "render_visualizations requires a build_and_run hook "
            "(production path: see server._post_investigation_run_viz_hook)."
        )

    gathered = gather_emitter_outputs(inv_dir / "runs.db")
    paths = []
    for viz_spec in visualizations:
        target = viz_dir / f"{viz_spec['name']}.html"
        try:
            doc = build_viz_composite(viz_spec, gathered, core_registry)
            html = build_and_run(doc, core_registry)
        except Exception as e:
            html = (
                f'<p style="color:#991b1b">Failed to render '
                f'<code>{viz_spec.get("name", "?")}</code>: '
                f'<code>{type(e).__name__}: {e}</code></p>'
            )
        target.write_text(html)
        paths.append(target)
    return paths
