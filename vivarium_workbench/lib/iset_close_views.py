"""``POST /api/iset-close`` builder — wraps
``discovery_engine.investigation_close.close_investigation``.

Phase 2.1h (rewire-first, ``docs/superpowers/plans/2026-08-04-phase2.1-rewire-
first.md`` §2.1h): the workbench keeps importing the plugin's compute to BACK
this endpoint (no module move yet — that's 2.1k). This lets
``skills/viva-investigation/SKILL.md``'s ``close`` subcommand stop shelling out
to ``python -m discovery_engine.investigation_close`` and call the dashboard API
instead — realizing the "Close investigation" endpoint the skill's prose already
anticipated (``the dashboard button ... will call the same close_investigation
function via a new POST /api/iset-close handler``).

``close_investigation(ws_root, slug, *, dry_run, auto_pr, skip_report)`` renders
the workspace report, copies it under the investigation dir, stamps the
investigation YAML (``status: closed`` + ``closed_at`` + ``report_url`` +
``contributors[]``), commits on the investigation branch, and — unless
``auto_pr=False`` — opens/refreshes a PR (NEVER ``--auto``). It runs git/gh via
subprocess in ``ws_root`` exactly as the plugin CLI did, so behavior is
unchanged; only the caller moves.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional


def iset_close(ws_root: Path, body: dict) -> "tuple[dict, int]":
    """POST /api/iset-close: close an investigation.

    Body: ``{slug, dry_run?, no_pr?, skip_report?}`` — ``no_pr`` maps to
    ``auto_pr=False`` (skip the ``gh pr create`` step). ``dry_run`` proposes the
    actions without rendering/writing/committing/pushing.

    Returns ``(CloseResult.to_dict(), 200)`` — the same result the plugin CLI
    printed (``slug, branch, contributors, actions, pr_url, dry_run``) plus the
    resolved ``slug``.

    - 400 ``{"error": "investigation slug required"}`` — missing/blank ``slug``.
    - 404 ``{"error": "<msg>"}`` — investigation YAML or the ``slug`` branch not
      found (``close_investigation`` raises ``FileNotFoundError`` for both, per
      the Investigation ≡ branch convention).
    - 500 ``{"error": "investigation close requires viva_superpowers: <e>"}`` —
      the plugin (or its ``investigation_close`` module) isn't installed.
    - 500 ``{"error": "investigation close failed: <e>"}`` — any other failure.
    """
    slug: Optional[str] = (body or {}).get("slug")
    if not slug or not str(slug).strip():
        return {"error": "investigation slug required"}, 400
    slug = str(slug).strip()

    dry_run = bool((body or {}).get("dry_run") or False)
    skip_report = bool((body or {}).get("skip_report") or False)
    auto_pr = not bool((body or {}).get("no_pr") or False)

    try:
        from discovery_engine.investigation_close import close_investigation
    except ImportError as e:  # noqa: BLE001
        return {"error": f"investigation close requires viva_superpowers: {e}"}, 500

    try:
        result = close_investigation(
            Path(ws_root),
            slug,
            dry_run=dry_run,
            auto_pr=auto_pr,
            skip_report=skip_report,
        )
    except FileNotFoundError as e:
        return {"error": str(e)}, 404
    except Exception as e:  # noqa: BLE001 — never a bare 500 with no message
        return {"error": f"investigation close failed: {e}"}, 500

    payload = result.to_dict()
    payload.setdefault("slug", slug)
    return payload, 200
