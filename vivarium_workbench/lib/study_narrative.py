"""Direct-to-YAML writers for the v4 narrative-spine fields on a study.

Backs four new /viva-study subcommands:

- set-verdicts          — write conclusion_verdicts (3-track verdict block)
- add-literature-anchor — append to literature_anchors[]
- add-pivot             — append to design_pivot_required[]
- add-requirement       — append to implementation_requirements[]

These fields don't have dedicated dashboard API endpoints today (the v4
narrative spine is read-only on the server side; mutation flows through
the skills + the existing study-set-* family). This module gives the skill
a clean Python entry point that:

  * walks up from cwd to find workspace.yaml
  * loads the study.yaml
  * applies the edit
  * preserves all other top-level keys verbatim
  * writes via atomic tmp-file + rename
  * supports --dry-run (print proposed diff, do not write)

It mirrors the shape of viva_superpowers/seed_from_followup.py — small,
focused, no dashboard runtime needed, easy to test in isolation.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from viva_superpowers.paths import find_workspace_root
from viva_superpowers.workspace_paths import WorkspacePaths
from viva_superpowers.study_io import (
    atomic_write as _atomic_write,
    dump_yaml as _dump,
    load_yaml_mapping as _load,
)


# ---------------------------------------------------------------------------
# Workspace + study resolution (mirrors seed_from_followup.py's helpers)
# ---------------------------------------------------------------------------


def _walk_to_workspace(start: Path) -> Path:
    """Walk up from `start` until a workspace.yaml is found; raise if absent."""
    return find_workspace_root(start)


def _study_yaml(ws_root: Path, slug: str) -> Path:
    p = WorkspacePaths.load(ws_root).study_dir(slug) / "study.yaml"
    if not p.is_file():
        raise FileNotFoundError(
            f"Study '{slug}' not found at {p}. Run '/viva-study new <name> "
            f"<composite>' to create it."
        )
    return p


# _load / _atomic_write / _dump are imported from study_io (see the import
# block above) — they were duplicated here before the Theme 3 consolidation.


# ---------------------------------------------------------------------------
# set_verdicts — write conclusion_verdicts (3-track block)
# ---------------------------------------------------------------------------


_REGRESSION_RESULTS = {"PASS", "FAIL", "MIXED", "PENDING"}
_BIOLOGICAL_RESULTS = _REGRESSION_RESULTS
_EXPLANATORY_RESULTS = {"POSITIVE", "NEUTRAL", "NEGATIVE", "PENDING"}


@dataclass
class VerdictUpdate:
    """One verdict track update. None fields are not written."""
    result: str | None = None
    basis: str | None = None


def _apply_verdict(track: dict | None, update: VerdictUpdate) -> dict | None:
    """Merge `update` into existing `track` dict (preserve unset fields).

    Returns None if both the existing track and the update are empty (so an
    untouched track stays absent in YAML).
    """
    if track is None and update.result is None and update.basis is None:
        return None
    out = dict(track or {})
    if update.result is not None:
        out["result"] = update.result
    if update.basis is not None:
        out["basis"] = update.basis
    return out or None


def set_verdicts(
    ws_root: Path,
    slug: str,
    *,
    regression: VerdictUpdate | None = None,
    biological: VerdictUpdate | None = None,
    explanatory: VerdictUpdate | None = None,
    dry_run: bool = False,
) -> tuple[dict, str]:
    """Write conclusion_verdicts on studies/<slug>/study.yaml.

    Each track update is merged into any existing values (unset sub-fields
    are preserved). Tracks with no update and no existing value are not
    written.

    Returns (updated_spec, diff_summary).
    """
    if regression and regression.result and regression.result not in _REGRESSION_RESULTS:
        raise ValueError(
            f"regression.result must be one of {sorted(_REGRESSION_RESULTS)}, "
            f"got {regression.result!r}"
        )
    if biological and biological.result and biological.result not in _BIOLOGICAL_RESULTS:
        raise ValueError(
            f"biological.result must be one of {sorted(_BIOLOGICAL_RESULTS)}, "
            f"got {biological.result!r}"
        )
    if explanatory and explanatory.result and explanatory.result not in _EXPLANATORY_RESULTS:
        raise ValueError(
            f"explanatory.result must be one of {sorted(_EXPLANATORY_RESULTS)}, "
            f"got {explanatory.result!r}"
        )

    sf = _study_yaml(ws_root, slug)
    spec = _load(sf)
    cv = dict(spec.get("conclusion_verdicts") or {})
    before = _dump({"conclusion_verdicts": cv}) if cv else "(empty)\n"

    if regression is not None:
        new = _apply_verdict(cv.get("regression_compatibility"), regression)
        if new is not None:
            cv["regression_compatibility"] = new
    if biological is not None:
        new = _apply_verdict(cv.get("biological_validation"), biological)
        if new is not None:
            cv["biological_validation"] = new
    if explanatory is not None:
        new = _apply_verdict(cv.get("explanatory_gain"), explanatory)
        if new is not None:
            cv["explanatory_gain"] = new

    spec["conclusion_verdicts"] = cv
    after = _dump({"conclusion_verdicts": cv})

    if not dry_run:
        _atomic_write(sf, _dump(spec))

    return spec, f"--- before\n{before}+++ after\n{after}"


# ---------------------------------------------------------------------------
# add_literature_anchor — append to literature_anchors[]
# ---------------------------------------------------------------------------


@dataclass
class LiteratureAnchor:
    expectation: str
    model_observable: str
    source: str = ""
    status_in_workspace: str = ""
    cites: list[str] = field(default_factory=list)


def add_literature_anchor(
    ws_root: Path,
    slug: str,
    anchor: LiteratureAnchor,
    *,
    dry_run: bool = False,
) -> tuple[dict, str]:
    """Append one entry to studies/<slug>/study.yaml.literature_anchors[].

    Empty optional fields (source, status_in_workspace, cites) are omitted
    from the written entry to keep the YAML tidy.
    """
    if not anchor.expectation.strip():
        raise ValueError("literature anchor: expectation must not be empty")
    if not anchor.model_observable.strip():
        raise ValueError("literature anchor: model_observable must not be empty")

    sf = _study_yaml(ws_root, slug)
    spec = _load(sf)
    anchors = list(spec.get("literature_anchors") or [])

    entry: dict[str, Any] = {
        "expectation": anchor.expectation,
        "model_observable": anchor.model_observable,
    }
    if anchor.source:
        entry["source"] = anchor.source
    if anchor.status_in_workspace:
        entry["status_in_workspace"] = anchor.status_in_workspace
    if anchor.cites:
        entry["cites"] = list(anchor.cites)

    before_n = len(anchors)
    anchors.append(entry)
    spec["literature_anchors"] = anchors

    if not dry_run:
        _atomic_write(sf, _dump(spec))

    return spec, (
        f"literature_anchors[]: {before_n} -> {len(anchors)} entries "
        f"(added {entry['expectation'][:60]!r})"
    )


# ---------------------------------------------------------------------------
# add_pivot — append to design_pivot_required[]
# ---------------------------------------------------------------------------


_PIVOT_ID_PATTERN = __import__("re").compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


@dataclass
class DesignPivot:
    id: str
    question: str
    alternatives: list[str] = field(default_factory=list)
    status: str = "open"
    requested_response: str = ""
    notes: str = ""


def add_pivot(
    ws_root: Path,
    slug: str,
    pivot: DesignPivot,
    *,
    dry_run: bool = False,
) -> tuple[dict, str]:
    """Append one entry to studies/<slug>/study.yaml.design_pivot_required[].

    Raises ValueError if `pivot.id` doesn't match the schema pattern, or if
    the id already exists on the study.
    """
    if not _PIVOT_ID_PATTERN.match(pivot.id):
        raise ValueError(
            f"pivot id {pivot.id!r} must match ^[A-Za-z0-9][A-Za-z0-9_-]*$"
        )
    if not pivot.question.strip():
        raise ValueError("pivot: question must not be empty")

    sf = _study_yaml(ws_root, slug)
    spec = _load(sf)
    pivots = list(spec.get("design_pivot_required") or [])
    if any(p.get("id") == pivot.id for p in pivots if isinstance(p, dict)):
        raise ValueError(
            f"pivot id {pivot.id!r} already exists on study {slug!r}"
        )

    entry: dict[str, Any] = {
        "id": pivot.id,
        "status": pivot.status,
        "question": pivot.question,
    }
    if pivot.alternatives:
        entry["alternatives"] = list(pivot.alternatives)
    if pivot.requested_response:
        entry["requested_response"] = pivot.requested_response
    if pivot.notes:
        entry["notes"] = pivot.notes

    before_n = len(pivots)
    pivots.append(entry)
    spec["design_pivot_required"] = pivots

    if not dry_run:
        _atomic_write(sf, _dump(spec))

    return spec, (
        f"design_pivot_required[]: {before_n} -> {len(pivots)} entries "
        f"(added {pivot.id!r})"
    )


# ---------------------------------------------------------------------------
# add_requirement — append to implementation_requirements[]
# ---------------------------------------------------------------------------


_REQUIREMENT_EFFORT = {"XS", "S", "M", "L", "XL"}


@dataclass
class ImplementationRequirement:
    id: str
    title: str
    kind: str = ""
    effort: str = ""
    status: str = "planned"
    description: str = ""
    steps: list[str] = field(default_factory=list)
    unblocks: list[str] = field(default_factory=list)
    defer_until: str = ""


def add_requirement(
    ws_root: Path,
    slug: str,
    req: ImplementationRequirement,
    *,
    dry_run: bool = False,
) -> tuple[dict, str]:
    """Append one entry to studies/<slug>/study.yaml.implementation_requirements[].

    Raises ValueError on duplicate id or invalid effort.
    """
    if not req.id.strip():
        raise ValueError("requirement: id must not be empty")
    if not req.title.strip():
        raise ValueError("requirement: title must not be empty")
    if req.effort and req.effort not in _REQUIREMENT_EFFORT:
        raise ValueError(
            f"effort must be one of {sorted(_REQUIREMENT_EFFORT)} or empty, "
            f"got {req.effort!r}"
        )

    sf = _study_yaml(ws_root, slug)
    spec = _load(sf)
    reqs_raw = spec.get("implementation_requirements")
    # Schema allows both array + object shapes; coerce to array for append.
    if isinstance(reqs_raw, dict):
        # Object shape: ignore for now (user can convert manually).
        raise ValueError(
            "implementation_requirements is in object shape; please convert "
            "to a list before using add-requirement"
        )
    reqs = list(reqs_raw or [])
    if any(isinstance(r, dict) and r.get("id") == req.id for r in reqs):
        raise ValueError(
            f"requirement id {req.id!r} already exists on study {slug!r}"
        )

    entry: dict[str, Any] = {
        "id": req.id,
        "title": req.title,
        "status": req.status,
    }
    if req.kind:
        entry["kind"] = req.kind
    if req.effort:
        entry["effort"] = req.effort
    if req.description:
        entry["description"] = req.description
    if req.steps:
        entry["steps"] = list(req.steps)
    if req.unblocks:
        entry["unblocks"] = list(req.unblocks)
    if req.defer_until:
        entry["defer_until"] = req.defer_until

    before_n = len(reqs)
    reqs.append(entry)
    spec["implementation_requirements"] = reqs

    if not dry_run:
        _atomic_write(sf, _dump(spec))

    return spec, (
        f"implementation_requirements[]: {before_n} -> {len(reqs)} entries "
        f"(added {req.id!r})"
    )


# ---------------------------------------------------------------------------
# CLI entry point — invoked by the /viva-study bash dispatcher
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m viva_superpowers.study_narrative",
        description="Direct-to-YAML writers for v4 narrative-spine fields.",
    )
    p.add_argument("--ws", default=None, help="workspace root (default: walk from cwd)")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_v = sub.add_parser("set-verdicts", help="write conclusion_verdicts")
    p_v.add_argument("slug")
    p_v.add_argument("--regression", choices=sorted(_REGRESSION_RESULTS))
    p_v.add_argument("--biological", choices=sorted(_BIOLOGICAL_RESULTS))
    p_v.add_argument("--explanatory", choices=sorted(_EXPLANATORY_RESULTS))
    p_v.add_argument("--basis-regression", default=None)
    p_v.add_argument("--basis-biological", default=None)
    p_v.add_argument("--basis-explanatory", default=None)
    p_v.add_argument("--dry-run", action="store_true")

    p_l = sub.add_parser(
        "add-literature-anchor",
        help="append to literature_anchors[]",
    )
    p_l.add_argument("slug")
    p_l.add_argument("--expectation", required=True)
    p_l.add_argument("--model-observable", required=True)
    p_l.add_argument("--source", default="")
    p_l.add_argument("--status", default="", dest="status_in_workspace")
    p_l.add_argument("--cite", action="append", default=[], dest="cites")
    p_l.add_argument("--dry-run", action="store_true")

    p_p = sub.add_parser("add-pivot", help="append to design_pivot_required[]")
    p_p.add_argument("slug")
    p_p.add_argument("--id", required=True, dest="pid")
    p_p.add_argument("--question", required=True)
    p_p.add_argument(
        "--alternatives",
        default="",
        help="semicolon-separated list (e.g. 'A. Add locked species;B. Patch stoichMatrix')",
    )
    p_p.add_argument("--status", default="open")
    p_p.add_argument("--requested-response", default="")
    p_p.add_argument("--notes", default="")
    p_p.add_argument("--dry-run", action="store_true")

    p_r = sub.add_parser(
        "add-requirement",
        help="append to implementation_requirements[]",
    )
    p_r.add_argument("slug")
    p_r.add_argument("--id", required=True, dest="rid")
    p_r.add_argument("--title", required=True)
    p_r.add_argument("--kind", default="")
    p_r.add_argument("--effort", choices=sorted(_REQUIREMENT_EFFORT), default="")
    p_r.add_argument("--status", default="planned")
    p_r.add_argument("--description", default="")
    p_r.add_argument(
        "--step",
        action="append",
        default=[],
        dest="steps",
        help="repeatable: one --step per step",
    )
    p_r.add_argument(
        "--unblocks",
        default="",
        help="comma-separated list of test/study names this requirement unblocks",
    )
    p_r.add_argument("--defer-until", default="")
    p_r.add_argument("--dry-run", action="store_true")

    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    ws_root = Path(args.ws) if args.ws else _walk_to_workspace(Path.cwd())

    if args.cmd == "set-verdicts":
        reg = (VerdictUpdate(args.regression, args.basis_regression)
               if args.regression or args.basis_regression else None)
        bio = (VerdictUpdate(args.biological, args.basis_biological)
               if args.biological or args.basis_biological else None)
        exp = (VerdictUpdate(args.explanatory, args.basis_explanatory)
               if args.explanatory or args.basis_explanatory else None)
        if not (reg or bio or exp):
            print("ERROR: pass at least one of --regression/--biological/"
                  "--explanatory or --basis-*", file=sys.stderr)
            return 2
        _, diff = set_verdicts(
            ws_root, args.slug,
            regression=reg, biological=bio, explanatory=exp,
            dry_run=args.dry_run,
        )
        print(diff)

    elif args.cmd == "add-literature-anchor":
        anchor = LiteratureAnchor(
            expectation=args.expectation,
            model_observable=args.model_observable,
            source=args.source,
            status_in_workspace=args.status_in_workspace,
            cites=args.cites,
        )
        _, msg = add_literature_anchor(
            ws_root, args.slug, anchor, dry_run=args.dry_run
        )
        print(msg)

    elif args.cmd == "add-pivot":
        alts = [
            a.strip() for a in args.alternatives.split(";")
            if a.strip()
        ]
        pivot = DesignPivot(
            id=args.pid,
            question=args.question,
            alternatives=alts,
            status=args.status,
            requested_response=args.requested_response,
            notes=args.notes,
        )
        _, msg = add_pivot(ws_root, args.slug, pivot, dry_run=args.dry_run)
        print(msg)

    elif args.cmd == "add-requirement":
        unblocks = [
            u.strip() for u in args.unblocks.split(",") if u.strip()
        ]
        req = ImplementationRequirement(
            id=args.rid,
            title=args.title,
            kind=args.kind,
            effort=args.effort,
            status=args.status,
            description=args.description,
            steps=args.steps,
            unblocks=unblocks,
            defer_until=args.defer_until,
        )
        _, msg = add_requirement(
            ws_root, args.slug, req, dry_run=args.dry_run
        )
        print(msg)

    return 0


if __name__ == "__main__":
    sys.exit(main())
