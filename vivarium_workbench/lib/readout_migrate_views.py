"""``POST /api/study-readout-migrate`` builder — wraps
``viva_superpowers.readout_migration.migrate_study_file``.

Phase 2.1d (rewire-first, ``docs/superpowers/plans/2026-08-04-phase2.1-rewire-
first.md`` §2.1d): the workbench keeps importing the plugin's compute to BACK
this endpoint (no module move yet — that's 2.1k). This lets
``skills/viva-report/SKILL.md:215`` stop importing
``viva_superpowers.readout_migration`` directly and call the dashboard API
instead, matching the rewire-first pattern already used by ``/api/report-lint``
and ``/api/study-sync-runs``.

``migrate_study_file(study_dir, write=False)`` defaults to a dry-run: it
computes the migration report without touching ``study.yaml``.
``write=True`` rewrites only the ``readouts:`` block (comment-preserving
ruamel round-trip); it is a true no-op when nothing actually changes.

NOT to be confused with ``vivarium_workbench.lib.readout_migration`` (a
distinct, pre-existing, one-shot "lift store_path out of notes" helper with no
relation to the plugin's readout-dialect migrator this module wraps).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from vivarium_workbench.lib.study_spec import study_dir as resolve_study_dir
from vivarium_workbench.lib.study_spec import study_spec_file


def study_readout_migrate(ws_root: Path, body: dict) -> "tuple[dict, int]":
    """POST /api/study-readout-migrate: migrate a study's legacy readouts.

    Body: ``{study: <slug>, write?: bool, apply?: bool}`` — ``write`` (or its
    alias ``apply``) defaults to ``False`` (dry-run), matching
    ``migrate_study_file``'s own ``write`` keyword.

    Returns ``(report, 200)`` on success, where ``report`` is exactly what
    ``viva_superpowers.readout_migration.migrate_study_file`` returns —
    ``{entries, migrated, needs_human, study_yaml, written, changed,
    canonicalized}`` — plus the resolved ``study`` slug. Render-error-tolerant:
    unresolvable readouts are collected into ``needs_human`` by the plugin
    function itself, never raised.

    - 400 ``{"error": "study slug required"}`` — missing/blank ``study``.
    - 404 ``{"error": "study not found: <slug>"}`` — no ``study.yaml``/
      ``spec.yaml`` for the slug.
    - 500 ``{"error": "readout migration requires viva_superpowers: <e>"}`` —
      the plugin (or its ``readout_migration`` module) isn't installed.
    - 500 ``{"error": "readout migration failed: <e>"}`` — any other failure
      raised while computing/writing the migration.
    """
    slug: Optional[str] = (body or {}).get("study")
    if not slug or not str(slug).strip():
        return {"error": "study slug required"}, 400
    slug = str(slug).strip()

    write = bool((body or {}).get("write") or (body or {}).get("apply") or False)

    sdir = resolve_study_dir(Path(ws_root), slug)
    if not study_spec_file(sdir).is_file():
        return {"error": f"study not found: {slug}"}, 404

    try:
        from discovery_engine.readout_migration import migrate_study_file
    except ImportError as e:  # noqa: BLE001
        return {"error": f"readout migration requires viva_superpowers: {e}"}, 500

    try:
        report = migrate_study_file(sdir, write=write)
    except FileNotFoundError as e:
        return {"error": str(e)}, 404
    except Exception as e:  # noqa: BLE001 — never a bare 500 with no message
        return {"error": f"readout migration failed: {e}"}, 500

    report = dict(report)
    report["study"] = slug
    return report, 200
