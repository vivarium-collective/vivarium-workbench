"""Investigation plan-progress board.

An investigation.yaml declares its plan in one of two shapes, both supported here:

* ``plan_acts`` — the five-act spine: a list of acts (I–V), each with a
  ``primary_question`` / ``decisive_exit_condition`` and its matched-null
  checkpoints nested under ``gates:``. The spine order is act order, then gate
  order within an act; ``off_spine`` gates branch off and never become a prior.
* ``plan_gates`` — the legacy flat list of cumulative gates (A→E, each
  ``extends`` the prior).

``iter_plan_gates(spec)`` flattens either shape into one ordered gate list (for
``plan_acts`` it synthesizes each gate's ``extends`` from spine order and
annotates the gate with its act context), so the rest of this module — and any
caller — works unchanged on both. This keeps the board backward-compatible for
every workspace that still authors ``plan_gates``.

This module derives each gate's LIVE status from member-study state — not the
YAML's static ``status:`` — so the board reflects reality as studies progress.
The derivation mirrors the viva-genesis ``build_plan_progress`` rules so the
workspace and the workbench tell the same story.

A gate's status:
  pending     — no scaffolded member studies yet (all its studies are pending_studies)
  blocked     — scaffolded members exist, but the gate it extends has not passed
  passed      — every member study's Decision gate passed, every criterion_finding
                is confirmed on a member, no pending studies remain, and the prior
                gate passed
  in_progress — members exist and the prior gate passed, but not all of the above
"""
from __future__ import annotations

from typing import Any, Optional

_CONFIRMED = {"confirms", "confirmed"}


def iter_plan_gates(spec: dict) -> list[dict[str, Any]]:
    """Flatten an investigation spec's plan into one ordered list of gate dicts.

    Reads the five-act ``plan_acts`` shape (gates nested under ``acts[].gates``)
    OR the legacy flat ``plan_gates`` list, so callers handle both without
    branching. Backward-compatible: a spec with only ``plan_gates`` is returned
    unchanged (its explicit ``extends`` preserved), and a spec with neither
    returns ``[]``.

    For ``plan_acts`` each gate is returned as a shallow copy annotated with:

    * ``act`` — ``{id, name, primary_question, decisive_exit_condition}`` of its act.
    * ``extends`` — synthesized as the prior SPINE gate's id (the previous
      non-``off_spine`` gate in flatten order) when the gate does not set one,
      so the existing ladder derivation is unchanged. An ``off_spine`` gate takes
      the current prior spine gate as ``extends`` but never becomes a prior itself.
    """
    acts = spec.get("plan_acts")
    if acts:
        out: list[dict[str, Any]] = []
        prior_spine: Optional[str] = None
        for act in acts or []:
            act = act or {}
            act_ctx = {
                "id": act.get("id"),
                "name": act.get("name"),
                "primary_question": act.get("primary_question"),
                "decisive_exit_condition": act.get("decisive_exit_condition"),
            }
            for gate in act.get("gates") or []:
                gate = dict(gate or {})
                off = bool(gate.get("off_spine") or gate.get("branch"))
                if gate.get("extends") is None:
                    gate["extends"] = prior_spine
                gate["act"] = act_ctx
                out.append(gate)
                if not off:
                    prior_spine = gate.get("id")
        return out
    return list(spec.get("plan_gates") or [])


def _criterion_met(criterion_findings: list, studies: dict) -> bool:
    """Every named finding must be CONFIRMED on some member study. An empty list
    means no explicit matched-null requirement (criterion trivially met)."""
    for fid in criterion_findings or []:
        hit = False
        for sd in studies.values():
            for f in (sd.get("findings") or []):
                if (f or {}).get("id") == fid and (f or {}).get("status") in _CONFIRMED:
                    hit = True
                    break
            if hit:
                break
        if not hit:
            return False
    return True


def build_plan_board(
    plan_gates: Optional[list], studies_by_name: dict
) -> list[dict[str, Any]]:
    """Derive live gate rows from a ``plan_gates`` block + member-study state.

    ``studies_by_name`` maps study slug → a dict with ``gate_status`` and
    ``findings`` (each ``{id, status, ...}``) — the per-study shape the
    investigation-detail builder already produces.

    Returns one row per gate (in the gates' authored order) carrying the gate's
    metadata plus a derived ``status``. Returns ``[]`` for a missing/empty block.
    """
    if not plan_gates:
        return []

    status_by_id: dict[str, str] = {}
    rows: list[dict[str, Any]] = []

    for gate in plan_gates:
        gate = gate or {}
        members = list(gate.get("member_studies") or [])
        scaffolded = [m for m in members if m in studies_by_name]
        pending_studies = list(gate.get("pending_studies") or [])
        extends = gate.get("extends")
        prior_passed = extends is None or status_by_id.get(extends) == "passed"

        if not scaffolded:
            status = "pending"
        elif not prior_passed:
            status = "blocked"
        else:
            member_specs = {m: studies_by_name[m] for m in scaffolded}
            all_decided = all(
                (sd.get("gate_status") == "passed") for sd in member_specs.values()
            )
            criterion_met = _criterion_met(gate.get("criterion_findings"), member_specs)
            if all_decided and criterion_met and not pending_studies:
                status = "passed"
            else:
                status = "in_progress"

        status_by_id[str(gate.get("id"))] = status
        rows.append({
            "id": gate.get("id"),
            "name": gate.get("name"),
            "extends": extends,
            "act": gate.get("act"),
            "optional": bool(gate.get("optional")),
            "off_spine": bool(gate.get("off_spine") or gate.get("branch")),
            "criterion": gate.get("criterion"),
            "criterion_findings": list(gate.get("criterion_findings") or []),
            "member_studies": members,
            "pending_studies": pending_studies,
            "status": status,
        })

    return rows
