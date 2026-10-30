"""Surface the "investigation references → member-study band citations" gap.

An investigation collects supporting references in its ``inputs.references``
block (workspace bib_keys that resolve in ``references/papers.bib``). Its member
studies carry acceptance bands in ``behavior_tests[]`` / ``tests[]`` /
``readouts[]`` — and those bands often lack a ``cites`` link. Nothing connects
the two pools, so today the agent hand-matches.

``investigation_citation_gaps(ws_root, inv_slug)`` is the deterministic surface:
for each member study it reports the uncited bands and the investigation's
available references, so the ``/viva-cite-bands`` skill can propose+apply the
pairings (the topical judgment stays in the skill — this module is AI-free).

This is a PURE READ — it never writes. It is best-effort per study: a member
study with a missing or malformed ``study.yaml`` is skipped, not raised.

Reuses (does not reimplement):
- ``band_provenance.bands_missing_provenance`` — the uncited bands per study spec.
- ``investigation_inputs.investigation_inputs`` — the investigation's references.
- ``workspace_paths.WorkspacePaths`` — the nested studies/ layout resolution.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from discovery_engine.band_provenance import bands_missing_provenance
from discovery_engine.investigation_inputs import investigation_inputs
from discovery_engine.workspace_paths import WorkspacePaths


def _member_slugs(wp: WorkspacePaths, inv_slug: str) -> list[str]:
    """Member study slugs from the investigation.yaml ``members:`` list.

    Falls back to the legacy ``studies:`` key when ``members:`` is absent,
    for investigation.yaml files written before the Phase 1 rename.
    """
    inv_yaml = wp.dir("investigations") / inv_slug / "investigation.yaml"
    if not inv_yaml.is_file():
        return []
    try:
        spec = yaml.safe_load(inv_yaml.read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    entries = spec.get("members")
    if not isinstance(entries, list):
        entries = spec.get("studies") or []
    if not isinstance(entries, list):
        return []
    return [s for s in entries if isinstance(s, str)]


def _uncited_bands(study_spec: dict) -> list[dict]:
    """Map ``bands_missing_provenance`` output to ``{"test", "observable"?}``.

    ``observable`` is included only when the source test entry carries an
    explicit ``observable`` field.
    """
    missing = bands_missing_provenance(study_spec)

    # Build a name -> observable map from the band-bearing sections.
    observable_by_name: dict[str, str] = {}
    for section in ("behavior_tests", "tests", "readouts"):
        items = study_spec.get(section) or []
        if not isinstance(items, list):
            continue
        for entry in items:
            if isinstance(entry, dict) and entry.get("observable"):
                name = entry.get("name")
                if isinstance(name, str):
                    observable_by_name[name] = entry["observable"]

    out: list[dict] = []
    for band in missing:
        rec: dict = {"test": band["name"]}
        obs = observable_by_name.get(band["name"])
        if obs is not None:
            rec["observable"] = obs
        out.append(rec)
    return out


def investigation_citation_gaps(ws_root: Path | str, inv_slug: str) -> dict:
    """Surface uncited member-study bands against the investigation's references.

    Parameters
    ----------
    ws_root:
        Workspace root (holds ``workspace.yaml``).
    inv_slug:
        Investigation slug under ``investigations/``.

    Returns
    -------
    dict
        ``{study_slug: {"uncited_bands": [{"test", "observable"?}],
        "available_references": [bib_key]}}``. Empty dict when the
        investigation has no resolvable member studies. PURE READ.
    """
    wp = WorkspacePaths.load(Path(ws_root))
    available = list(investigation_inputs(ws_root, inv_slug).get("references") or [])

    gaps: dict[str, dict] = {}
    for slug in _member_slugs(wp, inv_slug):
        # Best-effort: a missing/malformed study.yaml → skip, don't raise.
        try:
            study_dir = wp.study_dir(slug)
        except FileNotFoundError:
            continue
        study_yaml = study_dir / "study.yaml"
        if not study_yaml.is_file():
            continue
        try:
            study_spec = yaml.safe_load(study_yaml.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        if not isinstance(study_spec, dict):
            continue

        gaps[slug] = {
            "uncited_bands": _uncited_bands(study_spec),
            "available_references": list(available),
        }

    return gaps


def main(argv: list[str] | None = None) -> int:
    """CLI: print the citation gaps as JSON. ``pbg-citation-gaps`` console script."""
    parser = argparse.ArgumentParser(
        prog="pbg-citation-gaps",
        description="Surface uncited member-study bands x investigation references.",
    )
    parser.add_argument("--workspace", "-w", default=".", help="Workspace root.")
    parser.add_argument("--investigation", "-i", required=True, help="Investigation slug.")
    args = parser.parse_args(argv)

    gaps = investigation_citation_gaps(args.workspace, args.investigation)
    print(json.dumps(gaps, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
