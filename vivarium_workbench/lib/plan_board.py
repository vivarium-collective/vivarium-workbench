"""Investigation plan-progress board.

An investigation.yaml may declare a ``plan_gates`` block: the cumulative gates
(A→E, each ``extends`` the prior) the campaign is working toward, their member
studies, and the matched-null ``criterion_findings`` each gate must clear. This
module derives each gate's LIVE status from member-study state — not the YAML's
static ``status:`` — so the board reflects reality as studies progress.

Generic: any investigation whose spec declares ``plan_gates`` gets a board. The
derivation mirrors the viva-genesis ``build_plan_progress`` rules so the workspace
and the workbench tell the same story.

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
            "optional": bool(gate.get("optional")),
            "off_spine": bool(gate.get("off_spine") or gate.get("branch")),
            "criterion": gate.get("criterion"),
            "criterion_findings": list(gate.get("criterion_findings") or []),
            "member_studies": members,
            "pending_studies": pending_studies,
            "status": status,
        })

    return rows
