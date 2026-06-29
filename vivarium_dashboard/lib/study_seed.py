"""Seed a child study from a parent's ``follow_up_studies:`` entry.

Used by POST /api/study-seed-followup. The new study.yaml inherits a
minimal scaffold:

  - Pipeline gate prerequisites point back at the parent so the dashboard
    DAG draws the dependency edge.
  - Purpose copies the follow-up's why + hypothesized_mechanism into
    question / mechanism / expected_outcome slots.
  - status: planned, phase: Design — the new study starts at the very
    beginning of the lifecycle.

We don't try to translate ``acceptance:`` into ``behavior_tests:``
automatically — those need human + domain context. The seeded study lists
the acceptance criteria as ``key_assumptions:`` notes so they're visible
in the next walkthrough.
"""
from __future__ import annotations

import datetime
import re
from pathlib import Path

import yaml

from .workspace_paths import WorkspacePaths


def _slugify(text: str, max_len: int = 60) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s or "untitled-followup")[:max_len].rstrip("-")


def _unique_dir(studies_root: Path, base: str) -> Path:
    candidate = studies_root / base
    if not candidate.exists():
        return candidate
    for i in range(2, 100):
        c = studies_root / f"{base}-v{i}"
        if not c.exists():
            return c
    raise RuntimeError(f"Could not find a unique slug for {base!r}")


def _select_proposal(proposals: list, proposal_id, proposal_idx):
    """Pick a followup_study_proposals entry by id (preferred) or index.

    Returns ``(proposal_dict, idx)``. Raises IndexError/KeyError when the
    selector doesn't resolve.
    """
    if proposal_id is not None and str(proposal_id) != "":
        for i, p in enumerate(proposals):
            if str(p.get("id")) == str(proposal_id):
                return p, i
        raise KeyError(f"no followup_study_proposal with id {proposal_id!r}")
    if proposal_idx is None or proposal_idx < 0:
        raise ValueError("proposal_id or a non-negative proposal_idx is required")
    if proposal_idx >= len(proposals):
        raise IndexError(f"proposal_idx {proposal_idx} out of range "
                         f"(parent has {len(proposals)} followup proposals)")
    return proposals[proposal_idx], proposal_idx


def _seed_from_finding(workspace: Path, parent_name: str, finding_id: str, *,
                       proposal_id=None, new_slug=None, study_type=None) -> str:
    """Seed a child study from a parent FINDING by DELEGATING to the shared
    pbg-superpowers seed mechanism (``resolve_seed_source`` +
    ``write_child_study``), then add the dashboard's investigation back-link.

    This is the centralize-over-duplication path: the finding→child seed math
    + the parent stamp live once, in pbg-superpowers; the dashboard does not
    reimplement them — it only contributes the investigation DAG back-link.

    A finding with a ``next_action`` seeds STANDALONE (no pre-existing
    ``followup_proposals[]`` row needed); ``resolve_seed_source`` synthesizes
    an inline proposal stub.
    """
    from pbg_superpowers.seed_from_followup import (
        resolve_seed_source,
        write_child_study,
    )

    studies_root = WorkspacePaths.load(workspace).studies
    parent_yaml = studies_root / parent_name / "study.yaml"
    if not parent_yaml.is_file():
        raise FileNotFoundError(f"parent study not found: {parent_yaml}")
    parent_spec = yaml.safe_load(parent_yaml.read_text(encoding="utf-8")) or {}

    src = resolve_seed_source(
        parent_spec, finding_id=finding_id, proposal_id=proposal_id)
    # Wave 3a #19 — pass study_type (e.g. 'diagnostic') through to the pbg
    # writer when supported. Defensive: older pbg-superpowers writers don't
    # accept the kwarg, so fall back to the un-typed call (the child still
    # seeds; it just isn't pre-typed).
    if study_type:
        try:
            res = write_child_study(workspace, parent_name, src,
                                    new_slug=new_slug, study_type=study_type)
        except TypeError:
            res = write_child_study(workspace, parent_name, src, new_slug=new_slug)
    else:
        res = write_child_study(workspace, parent_name, src, new_slug=new_slug)
    new_name = res["new_slug"]

    # The pbg writer is workspace-layout aware but knows nothing about the
    # dashboard's investigation DAG — add the back-link here so the seeded
    # study shows up in the Investigations view.
    _add_to_parent_investigations(workspace, parent_name, new_name)
    return new_name


def seed_followup_study(workspace: Path, parent_name: str,
                        followup_idx: int = -1, *,
                        proposal_id=None, proposal_idx: int | None = None,
                        finding_id=None, study_type=None) -> str:
    """Create the child study.yaml and return its directory name.

    Source forms (the four unified followup field families):

    - **Finding** ``finding.next_action`` — pass ``finding_id``. DELEGATES to
      the pbg seed mechanism (standalone; synthesizes an inline proposal stub
      when there's no ``followup_proposals[]`` row). Wins over all others.
    - **Legacy** ``follow_up_studies[followup_idx]`` — pass ``followup_idx``.
    - **Richer** ``discovery_implications.followup_study_proposals`` — pass
      ``proposal_id`` (preferred) or ``proposal_idx``. The child inherits the
      proposal's title / study_type / target_mechanism_elements /
      required_inputs and a ``pipeline_gate.prerequisites`` edge back to this
      study with ``relation: leads-to``.

    When a proposal selector is given it wins over ``followup_idx``.
    """
    if not parent_name:
        raise ValueError("parent study name is required")

    # Finding family — delegate to the shared pbg mechanism.
    if finding_id is not None and str(finding_id) != "":
        return _seed_from_finding(
            workspace, parent_name, finding_id, proposal_id=proposal_id,
            study_type=study_type)

    studies_root = WorkspacePaths.load(workspace).studies
    parent_dir = studies_root / parent_name
    parent_yaml = parent_dir / "study.yaml"
    if not parent_yaml.is_file():
        raise FileNotFoundError(f"parent study not found: {parent_yaml}")

    parent_spec = yaml.safe_load(parent_yaml.read_text(encoding="utf-8")) or {}

    # Decide which source we're seeding from. A proposal selector (id or idx)
    # routes to the richer discovery_implications path.
    using_proposal = (proposal_id is not None and str(proposal_id) != "") \
        or (proposal_idx is not None)
    if using_proposal:
        return _seed_from_proposal(
            workspace, parent_name, parent_spec, studies_root,
            proposal_id=proposal_id, proposal_idx=proposal_idx)

    if followup_idx < 0:
        raise ValueError("followup_idx must be non-negative")
    follow_ups = parent_spec.get("follow_up_studies") or []
    if followup_idx >= len(follow_ups):
        raise IndexError(f"followup_idx {followup_idx} out of range "
                         f"(parent has {len(follow_ups)} follow-ups)")
    fu = follow_ups[followup_idx]

    title = (fu.get("title") or "untitled follow-up").strip()
    base_slug = _slugify(title)
    # Prefix with parent's prefix when it shares a "dnaa-N-" pattern
    parent_prefix_match = re.match(r"^([a-z]+-\d+)", parent_name)
    if parent_prefix_match:
        base_slug = f"{parent_prefix_match.group(1)}f-{base_slug}"

    new_dir = _unique_dir(studies_root, base_slug)
    new_name = new_dir.name
    new_dir.mkdir(parents=True, exist_ok=False)

    today = datetime.date.today().isoformat()
    question = (fu.get("why") or title).strip()
    mechanism = (fu.get("hypothesized_mechanism") or
                 f"Investigate the mechanism implied by follow-up '{title}'. "
                 "Add concrete model_change details before moving past Design.").strip()
    expected = ""
    accept = fu.get("acceptance") or []
    if accept:
        expected = "Satisfies the acceptance criteria inherited from the parent's "\
                   f"follow_up_studies[{followup_idx}]:\n  - " + \
                   "\n  - ".join(accept)
    else:
        expected = "TBD — populate before exiting Design phase."

    child_spec: dict = {
        "schema_version": 4,
        "name": new_name,
        "created": today,
        "status": "planned",
        "phase": "Design",
        "seeded_from": {
            "parent": parent_name,
            "followup_idx": followup_idx,
            "followup_title": title,
            "kind": fu.get("kind"),
        },

        # Required by the schema. Placeholder; user wires the real composite
        # before exiting Design.
        "baseline": [{
            "name": "baseline-placeholder",
            "composite": "v2ecoli.composites.baseline.baseline",
            "params": {"seed": 0, "cache_dir": "out/cache"},
        }],

        "purpose": {
            "question": question,
            "mechanism": mechanism,
            "expected_outcome": expected,
        },

        "pipeline_gate": {
            # Prerequisite item shape carries an optional `relation` key
            # (default leads-to) so the DAG renderer can style the edge.
            "prerequisites": [{"study": parent_name, "relation": "leads-to"}],
            "enables": [],
            "proceed_condition": "TBD — define before Simulate.",
        },

        "simulation_set": [],
        "model_change": {
            "base_model": "v2ecoli.composites.baseline.baseline",
            "new_processes": [],
            "new_state_variables": [],
            "new_parameters": [],
            "modified_processes": [],
            "notes": "Populate during Build phase.",
        },
        "key_assumptions": (
            [f"Inherited acceptance criterion: {a}" for a in (accept or [])]
            or ["TBD — list during Design phase."]
        ),
        "readouts": [],
        "behavior_tests": [],
        "conclusion_logic": {
            "if_primary_tests_pass": {"implementation_status": "TBD",
                                       "biological_validation": "TBD"},
            "if_primary_tests_fail": {"diagnose": ["TBD"],
                                       "block_downstream": "TBD"},
        },
        "limitations": ["TBD — fill before Decide phase."],
        "implementation_requirements": [],
        "bibliography": {
            "expert": parent_spec.get("bibliography", {}).get("expert", []),
            "bib_keys": [],
        },
        "conclusion": None,
        # The parent edge lives in pipeline_gate.prerequisites above; the legacy
        # parent_studies field is no longer written (normalize_dag_edges reads
        # prerequisites first — see lib/investigations.py).
        "tests": {"auto_discover": True, "data_source": "latest_run",
                  "pytest_args": [], "last_results": None},
    }

    # v4 narrative-spine fields, populated as placeholders so the seeded
    # study is dnaa-style on day one. The user fills in the report /
    # study_card / verdicts when the simulations run.
    child_spec["report"] = {
        "title": title,
        "verdict": "not-yet-run",
        "confidence": "low",
        "evidence_quality": "aspirational",
        "objective": question,
        "conclusion": "",
        "main_insight": "",
        "caveat": "",
        "key_metrics": [],
    }
    child_spec["study_card"] = {
        "goal": title,
        "mechanism": mechanism if mechanism else "",
        "why_before_next": "TBD — explain why this study unblocks downstream work.",
        "expected_result": "",
        "main_expert_question": "",
    }
    child_spec["biological_summary"] = (
        "(TBD — multi-paragraph plain-English mechanism narrative.)"
    )
    child_spec["literature_anchors"] = []
    child_spec["design_pivot_required"] = []
    child_spec["conclusion_verdicts"] = {
        "regression_compatibility": {"result": "PENDING", "basis": ""},
        "biological_validation": {"result": "PENDING", "basis": ""},
        "explanatory_gain": {"result": "PENDING", "basis": ""},
    }
    # Wave 3a #19 — pre-type the seeded child (e.g. diagnostic) when requested.
    if study_type:
        child_spec["study_type"] = study_type

    new_yaml = new_dir / "study.yaml"
    header = (
        f"# Auto-seeded {today} from {parent_name}'s "
        f"follow_up_studies[{followup_idx}] ('{title}').\n"
        f"# Original kind: {fu.get('kind') or 'other'}. "
        f"Effort estimate: {fu.get('effort') or '?'}.\n"
        "# schema v4 — 14-section narrative spine. Fill the placeholder fields\n"
        "# (report / study_card / biological_summary / literature_anchors / etc.)\n"
        "# as the study matures. See NEXT_STEPS.md for the full pattern.\n\n"
    )
    new_yaml.write_text(header + yaml.safe_dump(
        child_spec, sort_keys=False, default_flow_style=False, allow_unicode=True),
        encoding="utf-8")

    # Add the new study to every investigation.yaml that references the
    # parent — otherwise the seeded study is orphaned (on disk but invisible
    # in the dashboard's Investigations DAG view).
    _add_to_parent_investigations(workspace, parent_name, new_name)

    return new_name


def _seed_from_proposal(workspace: Path, parent_name: str, parent_spec: dict,
                        studies_root: Path, *,
                        proposal_id=None, proposal_idx: int | None = None) -> str:
    """Seed a child study from a ``discovery_implications.followup_study_proposals``
    entry. The child inherits the proposal's title / study_type /
    target_mechanism_elements / required_inputs and a
    ``pipeline_gate.prerequisites`` edge back to the parent with
    ``relation: leads-to``.
    """
    disc = parent_spec.get("discovery_implications") or {}
    proposals = (disc.get("followup_study_proposals")
                 if isinstance(disc, dict) else None) or []
    if not proposals:
        raise IndexError(
            f"parent study {parent_name!r} has no "
            "discovery_implications.followup_study_proposals")
    proposal, idx = _select_proposal(proposals, proposal_id, proposal_idx)

    title = (proposal.get("title") or "untitled follow-up proposal").strip()
    study_type = proposal.get("study_type") or ""
    targets = proposal.get("target_mechanism_elements") or []
    required_inputs = proposal.get("required_inputs") or []

    base_slug = _slugify(title)
    parent_prefix_match = re.match(r"^([a-z]+-\d+)", parent_name)
    if parent_prefix_match:
        base_slug = f"{parent_prefix_match.group(1)}f-{base_slug}"
    new_dir = _unique_dir(studies_root, base_slug)
    new_name = new_dir.name
    new_dir.mkdir(parents=True, exist_ok=False)

    today = datetime.date.today().isoformat()
    question = (proposal.get("proposed_experiment") or title).strip()
    mechanism = (
        f"Discovery-implications follow-up '{title}' (study_type: "
        f"{study_type or 'unspecified'}). "
        + (f"Targets mechanism elements: {', '.join(targets)}. " if targets else "")
        + "Add concrete model_change details before moving past Design.").strip()
    expected = (proposal.get("expected_information_gain")
                and f"Expected information gain: {proposal['expected_information_gain']}."
                or "TBD — populate before exiting Design phase.")

    child_spec: dict = {
        "schema_version": 4,
        "name": new_name,
        "created": today,
        "status": "planned",
        "phase": "Design",
        "study_type": study_type or None,
        "seeded_from": {
            "parent": parent_name,
            "source": "discovery_implications.followup_study_proposals",
            "proposal_id": proposal.get("id"),
            "proposal_idx": idx,
            "proposal_title": title,
            "source_trigger": proposal.get("source_trigger"),
        },
        "baseline": [{
            "name": "baseline-placeholder",
            "composite": "v2ecoli.composites.baseline.baseline",
            "params": {"seed": 0, "cache_dir": "out/cache"},
        }],
        "purpose": {
            "question": question,
            "mechanism": mechanism,
            "expected_outcome": expected,
        },
        "pipeline_gate": {
            "prerequisites": [{"study": parent_name, "relation": "leads-to"}],
            "enables": [],
            "proceed_condition": "TBD — define before Simulate.",
        },
        "simulation_set": [],
        "model_change": {
            "base_model": "v2ecoli.composites.baseline.baseline",
            "new_processes": [],
            "new_state_variables": [],
            "new_parameters": [],
            "modified_processes": [],
            "notes": "Populate during Build phase.",
        },
        "key_assumptions": (
            [f"Targets mechanism element: {t}" for t in targets]
            or ["TBD — list during Design phase."]
        ),
        "readouts": [],
        "behavior_tests": [],
        "implementation_requirements": [
            {"requirement": f"Required input: {ri}"} for ri in required_inputs
        ],
        "limitations": ["TBD — fill before Decide phase."],
        "bibliography": {
            "expert": parent_spec.get("bibliography", {}).get("expert", []),
            "bib_keys": [],
        },
        "conclusion": None,
        # The parent edge lives in pipeline_gate.prerequisites above (with the
        # leads-to relation); the legacy parent_studies field is no longer
        # written (normalize_dag_edges reads prerequisites first).
        "target_mechanism_elements": list(targets),
        "required_inputs": list(required_inputs),
        "tests": {"auto_discover": True, "data_source": "latest_run",
                  "pytest_args": [], "last_results": None},
    }
    child_spec["report"] = {
        "title": title,
        "verdict": "not-yet-run",
        "confidence": "low",
        "evidence_quality": "aspirational",
        "objective": question,
        "conclusion": "",
        "main_insight": "",
        "caveat": "",
        "key_metrics": [],
    }
    child_spec["study_card"] = {
        "goal": title,
        "mechanism": mechanism,
        "why_before_next": "TBD — explain why this study unblocks downstream work.",
        "expected_result": "",
        "main_expert_question": "",
    }

    new_yaml = new_dir / "study.yaml"
    header = (
        f"# Auto-seeded {today} from {parent_name}'s "
        f"discovery_implications.followup_study_proposals "
        f"(id={proposal.get('id')!r}, idx={idx}; '{title}').\n"
        f"# study_type: {study_type or '?'}. "
        f"source_trigger: {proposal.get('source_trigger') or '?'}.\n"
        "# schema v4 — fill the placeholder fields as the study matures.\n\n"
    )
    new_yaml.write_text(header + yaml.safe_dump(
        child_spec, sort_keys=False, default_flow_style=False, allow_unicode=True),
        encoding="utf-8")

    _add_to_parent_investigations(workspace, parent_name, new_name)
    return new_name


def _add_to_parent_investigations(workspace: Path, parent_name: str,
                                  new_study_name: str) -> list[Path]:
    """Append new_study_name to the studies: list of every investigation.yaml
    that already lists parent_name. Returns the list of files updated.

    Uses ruamel.yaml when available so formatting + comments survive; falls
    back to a minimal text-append that preserves the rest of the file
    byte-for-byte.
    """
    invs_root = WorkspacePaths.load(workspace).investigations
    if not invs_root.is_dir():
        return []
    updated: list[Path] = []
    for inv_yaml in invs_root.glob("*/investigation.yaml"):
        try:
            text = inv_yaml.read_text(encoding="utf-8")
            spec = yaml.safe_load(text) or {}
        except Exception:
            continue
        studies = spec.get("studies") or []
        if not isinstance(studies, list) or parent_name not in studies:
            continue
        if new_study_name in studies:
            continue   # already there (idempotent)
        # Minimal text edit: insert "  - <new>\n" right after the
        # parent_name entry under studies:. Preserves YAML formatting +
        # comments without round-tripping via yaml.safe_dump.
        lines = text.splitlines(keepends=True)
        out_lines = []
        in_studies = False
        inserted = False
        for line in lines:
            out_lines.append(line)
            if line.startswith("studies:"):
                in_studies = True
                continue
            if in_studies and not inserted:
                if line.lstrip().startswith("- " + parent_name):
                    indent = line[: len(line) - len(line.lstrip())]
                    out_lines.append(f"{indent}- {new_study_name}\n")
                    inserted = True
                elif line.strip() and not line.startswith((" ", "\t", "-")):
                    # left the studies: block without finding parent_name —
                    # bail rather than insert in the wrong section
                    in_studies = False
        if inserted:
            inv_yaml.write_text("".join(out_lines), encoding="utf-8")
            updated.append(inv_yaml)
    return updated
