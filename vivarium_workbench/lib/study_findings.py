"""Findings walk for ``/viva-study findings`` (Pass 10A).

Given a study slug, walk every ``behavior_tests[]`` outcome under
``runs[]`` and propose one finding per outcome not already covered by an
entry in ``findings[]``. Heuristic-suggest kind + status from the
outcome's pass/fail, surface candidate expert-doc quotes via
:mod:`viva_superpowers.expert_search`, and append the curated entries
back to ``study.yaml`` via atomic tmp+rename.

Public surface:

  - :func:`run_findings_walk` — entry point invoked by the SKILL shell
    dispatcher. Three modes:
      • interactive (default) — pre-fill drafts, ask for confirmation per
        outcome, then write.
      • ``auto=True`` — skip prompts and write drafts directly.
      • ``dry_run=True`` — print the proposed YAML diff; don't write.
  - :func:`load_study` / :func:`save_study_atomic` — helpers shared with
    the rest of /viva-study's YAML-direct subcommands.

The function returns a structured summary suitable for the host
Claude / dashboard to render. The CLI surface is intentionally thin
— the bulk of the interaction is meant to live in the host agent's
prompt (mirrors how ``propose-followup`` / ``seed-from-followup`` are
documented in SKILL.md).
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

import yaml

from viva_superpowers.bibtex import bib_keys
from viva_superpowers.study_io import load_yaml, save_yaml_atomic
from viva_superpowers.workspace_paths import WorkspacePaths

from vivarium_workbench.lib.expert_search import search_expert_docs
from vivarium_workbench.lib.path_safety import plain_name


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class OutcomeRow:
    """A single behavior_tests[] outcome inside a run."""

    run_name: str
    test_name: str
    result: str  # "pass" | "fail" | "error" | "skipped"
    observed: Any = None
    expected_summary: str | None = None
    description: str | None = None


@dataclass
class ProposedFinding:
    """A draft finding produced by the walk before user curation."""

    id: str
    kind: str
    status: str
    statement: str
    evidence: dict = field(default_factory=dict)
    expected: dict = field(default_factory=dict)
    next_action: str | None = None
    candidate_quotes: list[dict] = field(default_factory=list)

    def to_yaml_entry(self) -> dict:
        out: dict = {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "statement": self.statement,
        }
        if self.evidence:
            out["evidence"] = dict(self.evidence)
        if self.expected:
            out["expected"] = dict(self.expected)
        if self.next_action:
            out["next_action"] = self.next_action
        return out


@dataclass
class WalkResult:
    """Return value of :func:`run_findings_walk`."""

    study_slug: str
    proposed: list[ProposedFinding] = field(default_factory=list)
    appended: list[ProposedFinding] = field(default_factory=list)
    skipped_existing: list[str] = field(default_factory=list)  # test names
    cited_bib_keys: set[str] = field(default_factory=set)
    unknown_bib_keys: set[str] = field(default_factory=set)
    wrote_path: Path | None = None
    dry_run: bool = False


# ---------------------------------------------------------------------------
# YAML IO (atomic write)
# ---------------------------------------------------------------------------


def load_study(study_yaml: Path) -> dict:
    return load_yaml(study_yaml)


def save_study_atomic(study_yaml: Path, data: dict) -> None:
    """tmp + rename — same convention used by propose-followup / seed-from-followup."""
    save_yaml_atomic(study_yaml, data)


# ---------------------------------------------------------------------------
# Workspace discovery
# ---------------------------------------------------------------------------

# Canonical implementation lives in paths.py; re-exported here for the
# historical callers (and study_verify) that import it from this module.
from viva_superpowers.paths import find_workspace_root  # noqa: E402,F401


def study_dir_from_slug(ws_root: Path, slug: str) -> Path:
    sd = WorkspacePaths.load(ws_root).study_dir(plain_name(slug, "study name"))
    if not (sd / "study.yaml").is_file():
        raise FileNotFoundError(f"studies/{slug}/study.yaml not found under {ws_root}")
    return sd


# ---------------------------------------------------------------------------
# Behavior-test outcome extraction
# ---------------------------------------------------------------------------


def extract_outcomes(study: dict) -> list[OutcomeRow]:
    """Walk ``runs[*].outcomes[*]`` (or ``runs[*].test_results[*]``) and return one
    OutcomeRow per behavior_test result.

    Tolerant of multiple historical shapes:

      - runs[].outcomes — a list of {behavior_test, result, observed} dicts
      - runs[].outcomes — a dict {behavior_test: {result, observed}}
      - runs[].test_results — flat {test_name: 'pass'|'fail'}
    """
    out: list[OutcomeRow] = []
    runs = study.get("runs") or []
    tests_index = {
        t.get("name"): t
        for t in (study.get("behavior_tests") or [])
        if isinstance(t, dict) and t.get("name")
    }
    for run in runs:
        if not isinstance(run, dict):
            continue
        run_name = (
            run.get("name")
            or run.get("simulation")
            or run.get("id")
            or "<unnamed-run>"
        )
        outcomes = run.get("outcomes")
        if outcomes is None:
            outcomes = run.get("test_results")
        if outcomes is None:
            continue
        if isinstance(outcomes, dict):
            iterator: Iterable[tuple[str, Any]] = outcomes.items()
        elif isinstance(outcomes, list):
            iterator = (
                (
                    (o.get("behavior_test") or o.get("name") or o.get("test")),
                    o,
                )
                for o in outcomes
                if isinstance(o, dict)
            )
        else:
            continue
        for tname, payload in iterator:
            if not tname:
                continue
            if isinstance(payload, dict):
                result = (
                    payload.get("result")
                    or payload.get("status")
                    or payload.get("verdict")
                    or ""
                )
                observed = payload.get("observed")
            else:
                result = str(payload)
                observed = None
            test_meta = tests_index.get(tname, {})
            out.append(
                OutcomeRow(
                    run_name=str(run_name),
                    test_name=str(tname),
                    result=str(result).lower(),
                    observed=observed,
                    expected_summary=(
                        test_meta.get("expected_summary")
                        or (test_meta.get("calibration_anchor") or {}).get("literature_summary")
                    ),
                    description=test_meta.get("description"),
                )
            )
    return out


# ---------------------------------------------------------------------------
# Draft heuristics
# ---------------------------------------------------------------------------


def existing_finding_tests(study: dict) -> set[str]:
    """Set of behavior_test names already covered by a finding via evidence.from_test."""
    out: set[str] = set()
    for f in (study.get("findings") or []):
        if not isinstance(f, dict):
            continue
        ev = f.get("evidence") or {}
        if isinstance(ev, dict):
            ft = ev.get("from_test")
            if isinstance(ft, str):
                out.add(ft)
    return out


def next_finding_id(study: dict) -> str:
    """Auto-assign F-NN where NN is the next free integer."""
    used: set[int] = set()
    for f in (study.get("findings") or []):
        if not isinstance(f, dict):
            continue
        fid = f.get("id", "")
        if isinstance(fid, str) and fid.startswith("F-"):
            try:
                used.add(int(fid[2:]))
            except ValueError:
                continue
    n = 1
    while n in used:
        n += 1
    return f"F-{n:02d}"


def _draft_for_pass(outcome: OutcomeRow) -> ProposedFinding:
    desc = outcome.description or outcome.test_name
    statement = f"v2ecoli reproduces {desc!r} within tolerance (test {outcome.test_name!r} PASS)."
    return ProposedFinding(
        id="",  # filled in by caller
        kind="biological",
        status="confirms",
        statement=statement,
        evidence={
            "from_run": outcome.run_name,
            "from_test": outcome.test_name,
            **({"observed": outcome.observed} if outcome.observed is not None else {}),
        },
        expected={
            **({"summary": outcome.expected_summary} if outcome.expected_summary else {}),
        },
        next_action=None,
    )


def _draft_for_fail(outcome: OutcomeRow, *, kind: str = "biological") -> ProposedFinding:
    desc = outcome.description or outcome.test_name
    if kind == "biological":
        status = "contradicts"
        statement = (
            f"v2ecoli does NOT reproduce {desc!r} (test {outcome.test_name!r} FAIL); "
            "the gap likely reflects a real biological mismatch."
        )
    else:
        kind = "computational"
        status = "novel"
        statement = (
            f"Test {outcome.test_name!r} fails in a way that points at a "
            "wiring/listener/calibration issue rather than a biological mismatch."
        )
    return ProposedFinding(
        id="",
        kind=kind,
        status=status,
        statement=statement,
        evidence={
            "from_run": outcome.run_name,
            "from_test": outcome.test_name,
            **({"observed": outcome.observed} if outcome.observed is not None else {}),
        },
        expected={
            **({"summary": outcome.expected_summary} if outcome.expected_summary else {}),
        },
        next_action=(
            "Seed a follow-up calibration study; do not mark the gate passed."
            if status == "contradicts"
            else "Investigate the implementation gap before re-evaluating the test."
        ),
    )


def draft_for_outcome(
    outcome: OutcomeRow,
    *,
    fail_kind_hint: str = "biological",
) -> ProposedFinding:
    if outcome.result.lower() in {"pass", "passed"}:
        return _draft_for_pass(outcome)
    return _draft_for_fail(outcome, kind=fail_kind_hint)


# ---------------------------------------------------------------------------
# Citation / quote candidates
# ---------------------------------------------------------------------------


def keyword_terms_for(outcome: OutcomeRow) -> list[str]:
    """Crude term extractor — pull the test name slug + key words from description."""
    terms: list[str] = []
    if outcome.test_name:
        # Use the slug parts as terms (e.g. "dnaA-count-in-range" → ["dnaa", "count", "range"]).
        for piece in outcome.test_name.replace("_", "-").split("-"):
            if len(piece) >= 3:
                terms.append(piece)
    if outcome.description:
        for word in outcome.description.split():
            cleaned = "".join(c for c in word if c.isalnum())
            if len(cleaned) >= 5 and cleaned.lower() not in _STOP:
                terms.append(cleaned)
    # Deduplicate preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for t in terms:
        k = t.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(t)
    return out[:6]


_STOP = {
    "study", "studies", "model", "models", "value", "values", "across", "within",
    "between", "should", "would", "could", "during", "after", "before", "their",
    "there", "which", "while", "these", "those",
}


def load_bib_keys(ws_root: Path) -> set[str]:
    """Every @entry key declared in the workspace bibliography (best-effort).

    Thin wrapper over the shared :func:`viva_superpowers.bibtex.bib_keys` so the
    findings writer, the report linter, and study verify all resolve the same
    file with the same parser.
    """
    return bib_keys(ws_root)


def collect_cited_bib_keys(findings: Iterable[ProposedFinding]) -> set[str]:
    out: set[str] = set()
    for f in findings:
        cites = (f.expected or {}).get("cites") or []
        if isinstance(cites, list):
            for k in cites:
                if isinstance(k, str):
                    out.add(k)
    return out


# ---------------------------------------------------------------------------
# Top-level walk
# ---------------------------------------------------------------------------


Prompter = Callable[[ProposedFinding, OutcomeRow], ProposedFinding | None]


def run_findings_walk(
    study_dir: Path,
    *,
    auto: bool = False,
    dry_run: bool = False,
    prompter: Prompter | None = None,
    out: Callable[[str], None] = print,
    fail_kind_hint: str = "biological",
) -> WalkResult:
    """Walk a study's outcomes and propose / write findings.

    Args:
        study_dir: ``studies/<slug>/`` directory.
        auto: Skip the interactive prompter — write drafts as-is.
        dry_run: Compute the proposed diff and print; don't write.
        prompter: Optional callable invoked per drafted finding. Receives
            (proposed_finding, outcome_row); returns the final
            ProposedFinding (mutated as the user wishes) or ``None`` to
            skip this outcome. Ignored when ``auto=True``.
        out: Where to send human-readable progress messages.
        fail_kind_hint: 'biological' (default) or 'computational' — used
            for FAIL outcomes when no prompter is supplied or auto=True.
    """
    study_dir = Path(study_dir)
    study_yaml = study_dir / "study.yaml"
    if not study_yaml.is_file():
        raise FileNotFoundError(f"missing {study_yaml}")
    study = load_study(study_yaml)
    slug = study.get("name") or study_dir.name

    ws_root = study_dir.parent.parent  # studies/<slug>/.. = workspace root
    bib_keys_known = load_bib_keys(ws_root)

    outcomes = extract_outcomes(study)
    covered = existing_finding_tests(study)

    result = WalkResult(study_slug=slug, dry_run=dry_run)

    drafted: list[ProposedFinding] = []
    # Use a snapshot of used ids; assign incrementally as we draft.
    used_ids: set[int] = set()
    for f in (study.get("findings") or []):
        if isinstance(f, dict):
            fid = f.get("id", "")
            if isinstance(fid, str) and fid.startswith("F-"):
                try:
                    used_ids.add(int(fid[2:]))
                except ValueError:
                    pass

    def _alloc_id() -> str:
        n = 1
        while n in used_ids:
            n += 1
        used_ids.add(n)
        return f"F-{n:02d}"

    for outcome in outcomes:
        if outcome.test_name in covered:
            result.skipped_existing.append(outcome.test_name)
            continue
        draft = draft_for_outcome(outcome, fail_kind_hint=fail_kind_hint)
        draft.id = _alloc_id()
        # Expert-doc candidate quotes (cheap; cached after first PDF parse).
        try:
            draft.candidate_quotes = search_expert_docs(
                ws_root,
                keyword_terms_for(outcome),
                max_hits=3,
            )
        except Exception as e:  # noqa: BLE001
            out(f"[warn] expert search failed for {outcome.test_name}: {e}")
            draft.candidate_quotes = []
        drafted.append(draft)
        result.proposed.append(draft)

    if not drafted:
        out(f"[{slug}] No new outcomes to draft findings for.")
        return result

    # Curation loop ----------------------------------------------------------
    final: list[ProposedFinding] = []
    for draft, outcome in zip(drafted, [o for o in outcomes if o.test_name not in covered]):
        if auto or prompter is None:
            final.append(draft)
        else:
            curated = prompter(draft, outcome)
            if curated is not None:
                final.append(curated)

    # Bibliography crosscheck (warn, don't reject).
    cited = collect_cited_bib_keys(final)
    result.cited_bib_keys = cited
    unknown = {k for k in cited if k not in bib_keys_known} if bib_keys_known else set()
    result.unknown_bib_keys = unknown
    if unknown:
        out(
            f"[warn] {len(unknown)} cited bib_key(s) not in references/papers.bib: "
            + ", ".join(sorted(unknown))
        )

    # Diff preview -----------------------------------------------------------
    if dry_run or auto:
        out(f"[{slug}] Proposed {len(final)} new findings (dry_run={dry_run}, auto={auto}):")
        for f in final:
            out(yaml.safe_dump([f.to_yaml_entry()], sort_keys=False))

    if dry_run:
        return result

    # Write ------------------------------------------------------------------
    study.setdefault("findings", [])
    study["findings"].extend(f.to_yaml_entry() for f in final)
    save_study_atomic(study_yaml, study)
    result.appended = list(final)
    result.wrote_path = study_yaml
    out(f"[{slug}] Wrote {len(final)} new findings to {study_yaml}.")
    return result


# ---------------------------------------------------------------------------
# CLI entry: python -m viva_superpowers.study_findings <slug> [--auto] [--dry-run]
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(
        prog="python -m viva_superpowers.study_findings",
        description="Walk a study's behavior_test outcomes and propose findings.",
    )
    p.add_argument("slug", help="study slug under studies/<slug>/")
    p.add_argument("--ws", default=".", help="workspace root (default: cwd; auto-discovered upward)")
    p.add_argument("--auto", action="store_true", help="skip prompts; write drafts as-is")
    p.add_argument("--dry-run", action="store_true", help="print proposed YAML; do not write")
    args = p.parse_args(argv)

    try:
        ws_root = find_workspace_root(Path(args.ws))
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    try:
        sd = study_dir_from_slug(ws_root, args.slug)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    res = run_findings_walk(sd, auto=args.auto, dry_run=args.dry_run)
    print(
        f"summary: proposed={len(res.proposed)} "
        f"skipped_existing={len(res.skipped_existing)} "
        f"unknown_bib_keys={len(res.unknown_bib_keys)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
