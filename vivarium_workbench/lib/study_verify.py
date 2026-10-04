"""Spec verification for ``/viva-study verify <slug>``.

Closes the design→build gate: validates that a study.yaml is internally
consistent and that its cross-references resolve against the workspace,
**before** the operator wastes a Simulate phase on a study with broken
observable names, dangling parent_studies, missing bib keys, or a
behavior_test pointing at a simulation that doesn't exist.

This complements two existing checks:

  - ``report_linter`` runs at *publish* time — its job is presentation
    quality (unresolved placeholders, contradictory badges, truncated
    takeaways).
  - The dashboard schema validator runs on every save — catches
    structural / type errors.

``verify`` sits in between: the spec parses and is structurally valid,
but does it *make sense* relative to the workspace it lives in?

What's checked (workspace-agnostic):

  - Each baseline has ``name`` + ``composite``.
  - Each variant references a real baseline by name.
  - Each simulation_set entry references a real baseline or variant.
  - Each behavior_test.requires_simulation matches a simulation_set
    entry.
  - Each behavior_test.measure.observable matches an entry in the
    declared ``observables[]`` (when an observables list is present).
  - Each ``parent_studies`` slug resolves to an existing
    ``../<slug>/study.yaml`` under the workspace.
  - Each ``cites`` bibtex key appears in the workspace
    ``references.bib`` (when one exists; soft-skipped otherwise).
  - Each ``findings[].evidence.from_test`` (or ``from_tests[]``)
    matches a ``behavior_tests[].name``.
  - Each ``followup_proposals[].linked_finding`` matches a
    ``findings[].id``.

What's NOT checked (out of scope, would require workspace-specific
runtime knowledge):

  - Whether the composite's parameters actually accept the variant's
    overrides (would require importing the composite — workspace venv).
  - Whether observable store paths resolve in a real simulation run
    (would require running the composite or reading initial_state.json
    — workspace-specific cache layout).
  - Whether ``literature_target`` values are plausible (biology
    judgement).

The CLI is the single source of truth for the skill — ``/viva-study
verify`` shells out to ``python -m viva_superpowers.study_verify
<study.yaml>`` and surfaces the findings.
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from viva_superpowers.bibtex import bib_keys
from viva_superpowers.paths import find_workspace_root
from viva_superpowers.study_io import load_yaml as _load_yaml
from viva_superpowers.workspace_paths import WorkspacePaths


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VerifyFinding:
    """One verification result.

    Attributes:
        level: ``error`` (verify exit 1), ``warning`` (verify exit 0
            unless ``--strict``), ``info`` (informational only).
        check: short identifier (e.g. ``behavior-test-requires-sim``).
        field_path: dotted path to the offending field.
        message: human-readable explanation.
    """

    level: str
    check: str
    field_path: str
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Load helpers
# ---------------------------------------------------------------------------


def _names_from_list(items: list | None, key: str = "name") -> list[str]:
    """Pull the named field from a list of dicts; tolerant of None / non-dict."""
    out: list[str] = []
    for x in items or []:
        if isinstance(x, dict):
            name = x.get(key)
            if isinstance(name, str) and name:
                out.append(name)
    return out


def _resolve_baselines(study: dict) -> tuple[list, bool]:
    """Resolve baseline entries from either schema shape.

    Returns ``(entries, present)`` — ``present`` is False only when *neither*
    location declares a baseline.

    - **v3** — top-level ``baseline:`` as a single dict or a list of dicts.
    - **v4** — ``conditions.baseline:`` as a single dict that carries a
      ``composite`` but no ``name``. Synthesize a ``name`` from the study's
      ``name`` (falling back to ``"baseline"``), matching the loader's
      ``conditions``→legacy projection, so variant→baseline resolution and the
      per-entry checks behave identically across schema versions.

    Without this, ``verify`` read only the v3 top-level ``baseline`` and fired a
    false ``baseline-missing`` on every correctly-authored v4 study (#207).
    """
    bl = study.get("baseline")
    if bl is None:
        conditions = study.get("conditions")
        cond_bl = conditions.get("baseline") if isinstance(conditions, dict) else None
        if cond_bl is None:
            return [], False
        if isinstance(cond_bl, dict):
            entry = dict(cond_bl)
            entry.setdefault("name", study.get("name") or "baseline")
            return [entry], True
        # Unexpected non-dict under conditions.baseline — surface as present so
        # the shape check reports it rather than a misleading "missing".
        return (list(cond_bl) if isinstance(cond_bl, list) else [cond_bl]), True
    if isinstance(bl, dict):
        return [bl], True
    if isinstance(bl, list):
        return bl, True
    # present but a wrong scalar type (e.g. a string) — the shape check reports it
    return [bl], True


def _baseline_names(study: dict) -> list[str]:
    entries, _ = _resolve_baselines(study)
    return _names_from_list(entries)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def _check_baselines(study: dict) -> Iterable[VerifyFinding]:
    items, present = _resolve_baselines(study)
    if not present:
        yield VerifyFinding(
            level="error",
            check="baseline-missing",
            field_path="baseline",
            message=(
                "study.yaml has no baseline. Add a top-level `baseline:` (v3) "
                "or a `conditions.baseline:` mapping (v4)."
            ),
        )
        return
    if not items:
        yield VerifyFinding(
            level="error",
            check="baseline-empty",
            field_path="baseline",
            message="baseline must be a non-empty list (or single object).",
        )
        return
    for i, b in enumerate(items):
        if not isinstance(b, dict):
            yield VerifyFinding(
                level="error",
                check="baseline-shape",
                field_path=f"baseline[{i}]",
                message=f"baseline entry must be an object, got {type(b).__name__}.",
            )
            continue
        if not b.get("name"):
            yield VerifyFinding(
                level="error",
                check="baseline-name",
                field_path=f"baseline[{i}].name",
                message="baseline entry is missing `name`.",
            )
        if not b.get("composite"):
            yield VerifyFinding(
                level="error",
                check="baseline-composite",
                field_path=f"baseline[{i}].composite",
                message="baseline entry is missing `composite`.",
            )


def _check_variants(study: dict) -> Iterable[VerifyFinding]:
    baselines = set(_baseline_names(study))
    for i, v in enumerate(study.get("variants") or []):
        if not isinstance(v, dict):
            continue
        # v3 uses `base_composite`; v2 used `base`. Accept either.
        base = v.get("base_composite") or v.get("base")
        if base and base not in baselines:
            yield VerifyFinding(
                level="error",
                check="variant-base-unknown",
                field_path=f"variants[{i}].base_composite",
                message=(
                    f"variant {v.get('name', f'#{i}')!r} references "
                    f"baseline {base!r}, which is not declared. "
                    f"Known baselines: {sorted(baselines)}."
                ),
            )


def _simulation_names(study: dict) -> set[str]:
    """Return the set of valid `requires_simulation` targets.

    A behavior_test can reference any baseline name, any variant name,
    or any simulation_set[].name.
    """
    names: set[str] = set(_baseline_names(study))
    for v in study.get("variants") or []:
        n = v.get("name") if isinstance(v, dict) else None
        if isinstance(n, str) and n:
            names.add(n)
    sim_set = study.get("simulation_set")
    if isinstance(sim_set, list):
        for s in sim_set:
            if isinstance(s, dict):
                n = s.get("name")
                if isinstance(n, str) and n:
                    names.add(n)
    return names


def _check_simulation_set(study: dict) -> Iterable[VerifyFinding]:
    baselines = set(_baseline_names(study))
    variants = {v.get("name") for v in (study.get("variants") or [])
                if isinstance(v, dict) and isinstance(v.get("name"), str)}
    candidates = baselines | variants
    sim_set = study.get("simulation_set")
    if not isinstance(sim_set, list):
        return
    for i, s in enumerate(sim_set):
        if not isinstance(s, dict):
            continue
        # simulation_set entries can declare `from:` to reference a
        # baseline / variant. v2 used `composite:`; accept either.
        ref = s.get("from") or s.get("composite") or s.get("base")
        if ref and ref not in candidates and ref not in baselines:
            yield VerifyFinding(
                level="warning",
                check="simulation-from-unknown",
                field_path=f"simulation_set[{i}].from",
                message=(
                    f"simulation_set entry {s.get('name', f'#{i}')!r} "
                    f"references {ref!r}, which is not a known baseline "
                    f"or variant. Known: {sorted(candidates)}."
                ),
            )


def _check_behavior_tests(study: dict) -> Iterable[VerifyFinding]:
    sims = _simulation_names(study)
    observables = {o.get("name") for o in (study.get("observables") or [])
                   if isinstance(o, dict) and isinstance(o.get("name"), str)}
    has_observables_block = bool(study.get("observables"))
    for i, t in enumerate(study.get("behavior_tests") or []):
        if not isinstance(t, dict):
            continue
        req = t.get("requires_simulation")
        if isinstance(req, str) and req and req not in sims:
            yield VerifyFinding(
                level="error",
                check="behavior-test-requires-sim",
                field_path=f"behavior_tests[{i}].requires_simulation",
                message=(
                    f"behavior_test {t.get('name', f'#{i}')!r} requires "
                    f"simulation {req!r}, but no baseline / variant / "
                    f"simulation_set with that name is declared. "
                    f"Known: {sorted(sims)}."
                ),
            )
        # When an observables[] block is declared, every measure.observable
        # reference must resolve. Without an observables block we can't
        # validate — too many workspaces inline measure shapes.
        if has_observables_block:
            measure = t.get("measure")
            if isinstance(measure, dict):
                obs = measure.get("observable")
                if isinstance(obs, str) and obs and obs not in observables:
                    yield VerifyFinding(
                        level="error",
                        check="behavior-test-observable",
                        field_path=f"behavior_tests[{i}].measure.observable",
                        message=(
                            f"behavior_test {t.get('name', f'#{i}')!r} "
                            f"references observable {obs!r}, which is "
                            f"not declared in observables[]. "
                            f"Known: {sorted(observables)}."
                        ),
                    )


def _check_parent_studies(study: dict, ws_root: Path | None) -> Iterable[VerifyFinding]:
    if ws_root is None:
        return
    for i, p in enumerate(study.get("parent_studies") or []):
        slug = p if isinstance(p, str) else (p.get("study") if isinstance(p, dict) else None)
        if not isinstance(slug, str) or not slug:
            continue
        target = WorkspacePaths.load(ws_root).study_dir(slug) / "study.yaml"
        if not target.is_file():
            yield VerifyFinding(
                level="error",
                check="parent-study-not-found",
                field_path=f"parent_studies[{i}]",
                message=(
                    f"parent study {slug!r} does not resolve to "
                    f"studies/{slug}/study.yaml under the workspace."
                ),
            )


def _load_bib_keys(ws_root: Path | None) -> set[str] | None:
    """Workspace bib keys, or None when there's no bib file (soft-skip).

    Delegates to the shared :func:`viva_superpowers.bibtex.bib_keys` so verify
    resolves the SAME file (``references/papers.bib`` first) and parser as the
    report linter — previously verify read a different file with a different
    regex, so a cite could pass one gate and fail the other.
    """
    if ws_root is None:
        return None
    return bib_keys(ws_root, missing_ok=True)


def _iter_cites(study: dict) -> Iterable[tuple[str, str]]:
    """Yield (field_path, cite_key) over every `cites:` list in the study."""
    for i, t in enumerate(study.get("behavior_tests") or []):
        if not isinstance(t, dict):
            continue
        for j, k in enumerate(t.get("cites") or []):
            if isinstance(k, str) and k:
                yield (f"behavior_tests[{i}].cites[{j}]", k)
    for i, f in enumerate(study.get("findings") or []):
        if not isinstance(f, dict):
            continue
        for j, k in enumerate(f.get("cites") or []):
            if isinstance(k, str) and k:
                yield (f"findings[{i}].cites[{j}]", k)
    # bibliography.bib_keys is a flat list of declared keys — those are
    # SOURCES of cite keys, not references, so we don't validate them here.


def _check_cites(study: dict, bib_keys: set[str] | None) -> Iterable[VerifyFinding]:
    if bib_keys is None:
        return  # no bib file — soft skip
    for path, key in _iter_cites(study):
        if key not in bib_keys:
            yield VerifyFinding(
                level="warning",
                check="cite-key-not-in-bib",
                field_path=path,
                message=(
                    f"cite key {key!r} not found in workspace "
                    f"references.bib."
                ),
            )


def _check_findings_evidence(study: dict) -> Iterable[VerifyFinding]:
    test_names = {t.get("name") for t in (study.get("behavior_tests") or [])
                  if isinstance(t, dict) and isinstance(t.get("name"), str)}
    finding_ids = {f.get("id") for f in (study.get("findings") or [])
                   if isinstance(f, dict) and isinstance(f.get("id"), str)}

    for i, f in enumerate(study.get("findings") or []):
        if not isinstance(f, dict):
            continue
        ev = f.get("evidence")
        if not isinstance(ev, dict):
            continue
        # Single ref via from_test.
        ft = ev.get("from_test")
        if isinstance(ft, str) and ft and ft not in test_names:
            yield VerifyFinding(
                level="warning",
                check="finding-from-test-unknown",
                field_path=f"findings[{i}].evidence.from_test",
                message=(
                    f"finding {f.get('id', f'#{i}')!r} references "
                    f"behavior_test {ft!r}, which is not declared. "
                    f"Known: {sorted(test_names)}."
                ),
            )
        # Multi via from_tests.
        for j, t in enumerate(ev.get("from_tests") or []):
            if isinstance(t, str) and t and t not in test_names:
                yield VerifyFinding(
                    level="warning",
                    check="finding-from-tests-unknown",
                    field_path=f"findings[{i}].evidence.from_tests[{j}]",
                    message=(
                        f"finding {f.get('id', f'#{i}')!r} references "
                        f"behavior_test {t!r}, which is not declared."
                    ),
                )

    # followup_proposals[].linked_finding must resolve to a real finding id.
    for i, p in enumerate(study.get("followup_proposals") or []):
        if not isinstance(p, dict):
            continue
        link = p.get("linked_finding")
        if isinstance(link, str) and link and link not in finding_ids:
            yield VerifyFinding(
                level="error",
                check="proposal-linked-finding-unknown",
                field_path=f"followup_proposals[{i}].linked_finding",
                message=(
                    f"followup_proposal {p.get('id', f'#{i}')!r} links "
                    f"finding {link!r}, which is not declared. "
                    f"Known finding ids: {sorted(finding_ids)}."
                ),
            )


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------


def verify_study(study_yaml: Path, ws_root: Path | None = None) -> list[VerifyFinding]:
    """Run every check on a study.yaml. Returns a flat list of findings.

    ``ws_root`` is used for workspace-level cross-references (parent
    studies, bibtex keys). When ``None``, those checks are skipped.
    """
    study_yaml = Path(study_yaml)
    if not study_yaml.is_file():
        raise FileNotFoundError(study_yaml)
    study = _load_yaml(study_yaml)

    if ws_root is None:
        try:
            ws_root = find_workspace_root(study_yaml.parent)
        except FileNotFoundError:
            ws_root = None

    bib_keys = _load_bib_keys(ws_root)

    findings: list[VerifyFinding] = []
    findings.extend(_check_baselines(study))
    findings.extend(_check_variants(study))
    findings.extend(_check_simulation_set(study))
    findings.extend(_check_behavior_tests(study))
    findings.extend(_check_parent_studies(study, ws_root))
    findings.extend(_check_cites(study, bib_keys))
    findings.extend(_check_findings_evidence(study))
    return findings


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _format_text(findings: list[VerifyFinding], study_yaml: Path) -> str:
    if not findings:
        return f"verify: OK  ({study_yaml})\n"
    lines: list[str] = []
    by_level = {"error": [], "warning": [], "info": []}
    for f in findings:
        by_level.setdefault(f.level, []).append(f)
    n_err = len(by_level["error"])
    n_warn = len(by_level["warning"])
    n_info = len(by_level.get("info", []))
    lines.append(f"verify: {n_err} error(s), {n_warn} warning(s), {n_info} info  ({study_yaml})")
    for level in ("error", "warning", "info"):
        for f in by_level.get(level, []):
            mark = {"error": "✗", "warning": "!", "info": "·"}.get(level, "·")
            lines.append(f"  {mark} [{f.check}] {f.field_path}: {f.message}")
    return "\n".join(lines) + "\n"


def _format_json(findings: list[VerifyFinding], study_yaml: Path) -> str:
    return json.dumps(
        {
            "study_yaml": str(study_yaml),
            "findings": [f.to_dict() for f in findings],
            "summary": {
                "error": sum(1 for f in findings if f.level == "error"),
                "warning": sum(1 for f in findings if f.level == "warning"),
                "info": sum(1 for f in findings if f.level == "info"),
            },
        },
        indent=2,
    ) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(
        prog="python -m viva_superpowers.study_verify",
        description=(
            "Spec verification for a study.yaml. Checks internal consistency "
            "and workspace cross-references; does not execute simulations. "
            "Exit 0 if no errors, 1 if errors (or 1 if warnings with --strict)."
        ),
    )
    p.add_argument("study_yaml", help="path to studies/<slug>/study.yaml")
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    p.add_argument("--strict", action="store_true",
                   help="also fail on warnings")
    p.add_argument("--quiet", action="store_true",
                   help="suppress output on success (still nonzero exit on failure)")
    args = p.parse_args(argv)

    try:
        findings = verify_study(Path(args.study_yaml))
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    has_errors = any(f.level == "error" for f in findings)
    has_warnings = any(f.level == "warning" for f in findings)
    failing = has_errors or (has_warnings and args.strict)

    if args.json:
        sys.stdout.write(_format_json(findings, Path(args.study_yaml)))
    elif not (args.quiet and not failing):
        sys.stdout.write(_format_text(findings, Path(args.study_yaml)))

    return 1 if failing else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
