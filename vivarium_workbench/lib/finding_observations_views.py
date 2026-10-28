"""``POST /api/study-findings-populate-observations`` builder — wraps
``discovery_engine.finding_observations.populate_finding_observations``.

Phase 2.1f (rewire-first, ``docs/superpowers/plans/2026-08-04-phase2.1-rewire-
first.md`` §2.1f): the workbench keeps importing the plugin's compute to BACK
this endpoint (no module move yet — that's 2.1k). This lets
``skills/viva-biology-forward/SKILL.md`` stop importing
``discovery_engine.finding_observations`` directly and call the dashboard API
instead, matching the rewire-first pattern already used by
``/api/study-readout-migrate`` and ``/api/study-sync-runs``.

``populate_finding_observations(study_dir)`` fills only ABSENT code-owned
finding slots (``evidence.observed``/``units``/``divergence_factor``,
``expected.range``/``threshold``/``cites``, ``provenance.run_ids``, and the
measured side of ``calibration_anchor``) from ``computed_outcomes`` + band +
readouts. It is inherently a fill-absent-only, idempotent writer — there is no
dry-run mode (a second call on already-filled data returns ``filled=0`` and
does not touch ``study.yaml``), so this endpoint takes no ``write`` flag.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from vivarium_workbench.lib.study_spec import study_dir as resolve_study_dir
from vivarium_workbench.lib.study_spec import study_spec_file


def study_findings_populate_observations(ws_root: Path, body: dict) -> "tuple[dict, int]":
    """POST /api/study-findings-populate-observations: fill code-owned finding slots.

    Body: ``{study: <slug>}``.

    Returns ``(result, 200)`` on success, where ``result`` is exactly what
    ``discovery_engine.finding_observations.populate_finding_observations``
    returns — ``{filled, skipped}`` — plus the resolved ``study`` slug.
    Idempotent: a second call on an already-filled study returns
    ``filled=0`` and does not rewrite ``study.yaml``.

    - 400 ``{"error": "study slug required"}`` — missing/blank ``study``.
    - 404 ``{"error": "study not found: <slug>"}`` — no ``study.yaml``/
      ``spec.yaml`` for the slug.
    - 500 ``{"error": "finding population requires viva_superpowers: <e>"}`` —
      the plugin (or its ``finding_observations`` module) isn't installed.
    - 500 ``{"error": "finding population failed: <e>"}`` — any other failure
      raised while computing/writing the fills.
    """
    slug: Optional[str] = (body or {}).get("study")
    if not slug or not str(slug).strip():
        return {"error": "study slug required"}, 400
    slug = str(slug).strip()

    sdir = resolve_study_dir(Path(ws_root), slug)
    if not study_spec_file(sdir).is_file():
        return {"error": f"study not found: {slug}"}, 404

    try:
        from discovery_engine.finding_observations import populate_finding_observations
    except ImportError as e:  # noqa: BLE001
        return {"error": f"finding population requires viva_superpowers: {e}"}, 500

    try:
        result = populate_finding_observations(sdir)
    except FileNotFoundError as e:
        return {"error": str(e)}, 404
    except Exception as e:  # noqa: BLE001 — never a bare 500 with no message
        return {"error": f"finding population failed: {e}"}, 500

    result = dict(result)
    result["study"] = slug
    return result, 200
