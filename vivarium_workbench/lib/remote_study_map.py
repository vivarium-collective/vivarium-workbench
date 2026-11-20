"""The workspace's ``remote_run_study_map``: which study a remote run belongs to, by its experiment id.

Workspace configuration (``workspace.yaml``), not a backend API, so it lives in the core (step P2, Remote tab R1):
the core gates the study cards' remote-run counts on it (``investigations_index._remote_study_run_stats``), and a
backend's listing applies it to the rows it returns (``vivarium_workbench_sms.remote_simulations``, read through
this module).
"""

from __future__ import annotations

import re
from pathlib import Path


def load_rules(ws_root: Path) -> list:
    """Workspace-declared experiment_id -> study_slug rules for remote runs.

    Reads ``remote_run_study_map`` from the workspace config (``workspace.yaml``):
    an ordered list of ``{pattern: <regex>, study: <slug>}`` entries, first match
    wins. Returns a list of ``(compiled_regex, study_slug)``.

    Why this exists: a remote (GovCloud) run is surfaced with ``db_path=None``
    (its store is an ``s3://`` uri), so the local index's study-slug-from-db_path
    inference cannot fire and every remote run lands orphaned
    (``study_slug=None``). This lets a workspace declare how its remote runs'
    ``experiment_id``s map to its studies so they show under the right study.

    INTERIM (stopgap). The durable fix is stamping ``study_slug`` + the
    RunIdentity record into the run at dispatch (viva-api#590), after which
    remote runs carry their own study association and this heuristic is moot.
    """
    cfg = Path(ws_root) / "workspace.yaml"
    if not cfg.is_file():
        return []
    try:
        import yaml
        doc = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    rules = doc.get("remote_run_study_map") or []
    out = []
    for r in rules:
        if isinstance(r, dict) and r.get("pattern") and r.get("study"):
            try:
                out.append((re.compile(str(r["pattern"]), re.I), str(r["study"])))
            except re.error:
                continue
    return out


def infer_study_slug(experiment_id: str, rules: list) -> "str | None":
    """First workspace rule whose pattern matches ``experiment_id`` (or None)."""
    e = experiment_id or ""
    for pat, slug in rules:
        if pat.search(e):
            return slug
    return None
