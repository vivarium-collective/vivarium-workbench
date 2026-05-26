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


def seed_followup_study(workspace: Path, parent_name: str,
                        followup_idx: int) -> str:
    """Create the child study.yaml and return its directory name."""
    if not parent_name:
        raise ValueError("parent study name is required")
    if followup_idx < 0:
        raise ValueError("followup_idx must be non-negative")

    studies_root = Path(workspace) / "studies"
    parent_dir = studies_root / parent_name
    parent_yaml = parent_dir / "study.yaml"
    if not parent_yaml.is_file():
        raise FileNotFoundError(f"parent study not found: {parent_yaml}")

    parent_spec = yaml.safe_load(parent_yaml.read_text()) or {}
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
            "prerequisites": [parent_name],
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
        "parent_studies": [parent_name],
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
        child_spec, sort_keys=False, default_flow_style=False, allow_unicode=True))

    # Add the new study to every investigation.yaml that references the
    # parent — otherwise the seeded study is orphaned (on disk but invisible
    # in the dashboard's Investigations DAG view).
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
    invs_root = Path(workspace) / "investigations"
    if not invs_root.is_dir():
        return []
    updated: list[Path] = []
    for inv_yaml in invs_root.glob("*/investigation.yaml"):
        try:
            text = inv_yaml.read_text()
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
            inv_yaml.write_text("".join(out_lines))
            updated.append(inv_yaml)
    return updated
