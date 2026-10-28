"""``GET /api/study-readout-migration-status`` builder — wraps
``discovery_engine.readout_migration.readout_migration_status``.

Phase 2.1d (rewire-first, ``docs/superpowers/plans/2026-08-04-phase2.1-rewire-
first.md`` §2.1d): the read-only STATUS sibling of
``/api/study-readout-migrate``. Where that (POST) endpoint wraps the WRITE op
``migrate_study_file``, this (GET) endpoint wraps the PURE classifier
``readout_migration_status(study_dir)`` — it only reads ``study.yaml`` and
buckets every readout into ``{canonical, migratable, needs_human}`` so a skill
can ask "what would migrate?" via the dashboard API instead of importing
``discovery_engine.readout_migration`` directly, matching the rewire-first
pattern already used by ``/api/report-lint`` and ``/api/study-sync-runs``.

``readout_migration_status`` performs NO writes; the actual rewrite is the
sibling ``migrate_study_file(write=True)`` behind ``/api/study-readout-migrate``.

NOT to be confused with ``vivarium_workbench.lib.readout_migration`` (a
distinct, pre-existing, one-shot "lift store_path out of notes" helper with no
relation to the plugin's readout-dialect migrator this module wraps).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from vivarium_workbench.lib.study_spec import study_dir as resolve_study_dir
from vivarium_workbench.lib.study_spec import study_spec_file


def readout_migration_status_view(ws_root: Path, body: dict) -> "tuple[dict, int]":
    """GET /api/study-readout-migration-status: classify a study's readouts.

    Body: ``{study: <slug>}`` — the route passes the ``?study=`` query param as
    ``{"study": q}``; the builder accepts a body dict for uniformity with the
    POST builders.

    Returns ``(status, 200)`` on success, where ``status`` is exactly what
    ``discovery_engine.readout_migration.readout_migration_status`` returns —
    ``{canonical, migratable, needs_human}`` — plus the resolved ``study``
    slug. PURE: reads ``study.yaml`` only, never writes. Render-error-tolerant:
    unresolvable readouts are collected into ``needs_human`` by the plugin
    function itself, never raised.

    - 400 ``{"error": "study slug required"}`` — missing/blank ``study``.
    - 404 ``{"error": "study not found: <slug>"}`` — no ``study.yaml``/
      ``spec.yaml`` for the slug.
    - 500 ``{"error": "readout migration requires viva_superpowers: <e>"}`` —
      the plugin (or its ``readout_migration`` module) isn't installed.
    - 500 ``{"error": "readout migration status failed: <e>"}`` — any other
      failure raised while computing the status.
    """
    slug: Optional[str] = (body or {}).get("study")
    if not slug or not str(slug).strip():
        return {"error": "study slug required"}, 400
    slug = str(slug).strip()

    sdir = resolve_study_dir(Path(ws_root), slug)
    if not study_spec_file(sdir).is_file():
        return {"error": f"study not found: {slug}"}, 404

    try:
        from discovery_engine.readout_migration import readout_migration_status
    except ImportError as e:  # noqa: BLE001
        return {"error": f"readout migration requires viva_superpowers: {e}"}, 500

    try:
        status = readout_migration_status(sdir)
    except FileNotFoundError as e:
        return {"error": str(e)}, 404
    except Exception as e:  # noqa: BLE001 — never a bare 500 with no message
        return {"error": f"readout migration status failed: {e}"}, 500

    status = dict(status)
    status["study"] = slug
    return status, 200
