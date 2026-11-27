"""One-shot migrator: nested investigations/<inv>/studies/<slug>/ studies -> the
top-level studies/ registry, rewriting each investigation.yaml to reference its
studies via `members: [slug, ...]` and backfilling `inputs` where inferable.

Companion to `cli.migrate_investigations_to_studies` (a different, earlier
migration: investigations/<name>/spec.yaml v2 -> studies/<name>/study.yaml v3).
This one moves studies that are already nested *under* an investigation out to
the shared registry so multiple investigations can reference the same study.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import yaml


class StudyMigrationError(Exception):
    """Raised on an unrecoverable migration condition (e.g. slug collision)."""


def _discover_nested_studies(ws_root: Path) -> list[tuple[str, str, Path]]:
    """Return [(inv_name, slug, src_dir), ...] for every nested study found."""
    inv_root = ws_root / "investigations"
    discovered: list[tuple[str, str, Path]] = []
    if not inv_root.is_dir():
        return discovered

    for inv_dir in sorted(inv_root.iterdir()):
        if not inv_dir.is_dir():
            continue
        studies_dir = inv_dir / "studies"
        if not studies_dir.is_dir():
            continue
        for study_dir in sorted(studies_dir.iterdir()):
            if not study_dir.is_dir():
                continue
            if not (study_dir / "study.yaml").is_file():
                continue
            discovered.append((inv_dir.name, study_dir.name, study_dir))

    return discovered


def _detect_producer(ws_root: Path, discovered: list[tuple[str, str, Path]]) -> str | None:
    """Find the sim_data producer among discovered studies: the first study that
    declares ``sim_data`` in its ``outputs``. A producer is whatever the spec
    declares, regardless of how the study happens to be named."""
    for _inv_name, slug, src_dir in discovered:
        try:
            spec = yaml.safe_load((src_dir / "study.yaml").read_text(encoding="utf-8")) or {}
        except Exception:
            spec = {}
        if "sim_data" in (spec.get("outputs") or []):
            return slug
    return None


def _needs_backfill(spec: dict) -> bool:
    """True unless the study already declares an `inputs:` key (even `[]`,
    which is an intentional "no upstream artifacts" declaration, not a gap)."""
    return "inputs" not in spec


def migrate_studies(ws_root: Path, *, dry_run: bool = False) -> dict:
    """Move investigations/<inv>/studies/<slug>/ -> studies/<slug>/, rewrite each
    investigation.yaml to members:[...], backfill sim_data inputs where inferable.
    """
    ws_root = Path(ws_root)
    discovered = _discover_nested_studies(ws_root)

    move_key = "would_move" if dry_run else "moved"
    result: dict = {
        move_key: [],
        "rewritten_investigations": [],
        "backfilled": [],
        "todos": [],
        "dry_run": dry_run,
    }

    if not discovered:
        return result

    studies_root = ws_root / "studies"

    # --- Pre-flight collision check (near-atomic: no moves before this passes) ---
    seen_slugs: dict[str, str] = {}
    for inv_name, slug, _src_dir in discovered:
        if slug in seen_slugs and seen_slugs[slug] != inv_name:
            raise StudyMigrationError(f"slug collision: {slug}")
        seen_slugs[slug] = inv_name
        if (studies_root / slug).exists():
            raise StudyMigrationError(f"slug collision: {slug}")

    producer_slug = _detect_producer(ws_root, discovered)

    slugs_by_inv: dict[str, list[str]] = {}
    for inv_name, slug, _src_dir in discovered:
        slugs_by_inv.setdefault(inv_name, []).append(slug)

    if dry_run:
        result["would_move"] = sorted(slug for _inv, slug, _src in discovered)
        for inv_name, slug, src_dir in discovered:
            if slug == producer_slug:
                continue
            spec = yaml.safe_load((src_dir / "study.yaml").read_text(encoding="utf-8")) or {}
            if not _needs_backfill(spec):
                continue
            if producer_slug is not None:
                result["backfilled"].append({"study": slug, "from": producer_slug})
            else:
                result["todos"].append(slug)
        result["rewritten_investigations"] = sorted(slugs_by_inv.keys())
        return result

    # --- Move each nested study to the top-level registry ---
    for inv_name, slug, src_dir in discovered:
        studies_root.mkdir(parents=True, exist_ok=True)
        dst = studies_root / slug
        shutil.move(str(src_dir), str(dst))
        result["moved"].append(slug)

        study_yaml = dst / "study.yaml"
        spec = yaml.safe_load(study_yaml.read_text(encoding="utf-8")) or {}

        if slug == producer_slug or not _needs_backfill(spec):
            # Producer itself, or a study that already declares inputs: (even
            # an explicit empty list) — leave unchanged, no guess.
            continue

        if producer_slug is not None:
            spec["inputs"] = [{"artifact": "sim_data", "from": producer_slug}]
            study_yaml.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
            result["backfilled"].append({"study": slug, "from": producer_slug})
        else:
            study_yaml.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
            with study_yaml.open("a", encoding="utf-8") as f:
                f.write(
                    "\n# TODO(inputs): could not infer upstream artifacts — "
                    "declare inputs: [{artifact, from}] manually\n"
                )
            result["todos"].append(slug)

    result["moved"] = sorted(result["moved"])

    # --- Rewrite each investigation.yaml with members: [...] ---
    for inv_name, slugs in slugs_by_inv.items():
        inv_yaml = ws_root / "investigations" / inv_name / "investigation.yaml"
        if not inv_yaml.is_file():
            continue
        inv_spec = yaml.safe_load(inv_yaml.read_text(encoding="utf-8")) or {}
        inv_spec["members"] = sorted(slugs)
        inv_spec.pop("studies", None)
        inv_yaml.write_text(yaml.safe_dump(inv_spec, sort_keys=False), encoding="utf-8")
        result["rewritten_investigations"].append(inv_name)

    result["rewritten_investigations"] = sorted(result["rewritten_investigations"])

    # --- Remove now-empty investigations/<inv>/studies/ dirs ---
    for inv_name in slugs_by_inv:
        studies_dir = ws_root / "investigations" / inv_name / "studies"
        if studies_dir.is_dir() and not any(studies_dir.iterdir()):
            studies_dir.rmdir()

    return result
