"""Build the investigations index payload for a workspace.

Extracted from ``vivarium_dashboard.server._investigations_data`` so the
FastAPI seam (``api/app.py``) can call it without importing the stdlib server
module.  The single implementation is shared: ``server.py`` re-imports
``build_investigations`` and keeps its old ``_investigations_data`` name as a
thin wrapper.

Helpers moved here from server.py:
  - ``_conclusions_excerpt``
  - ``_condition_satisfied``
  - ``_count_runs_for_study`` (ws_root-parameterized)
  - ``_format_baseline_source``
  - ``_http_get_json``
  - ``_iter_study_dirs`` (ws_root-parameterized)
  - ``_normalize_parents``

``_normalize_requirements`` is shared with Task 2 — imported from
``lib.spec_norm`` rather than duplicated here.
"""

from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# sys.path helper (mirrors visualization_classes.py / composite_resolve.py)
# ---------------------------------------------------------------------------

def _ws_add_to_sys_path(ws_root: Path) -> None:
    """Ensure the workspace root is on ``sys.path`` so its package is importable."""
    ws = str(ws_root)
    if ws not in sys.path:
        sys.path.insert(0, ws)


# ---------------------------------------------------------------------------
# Study directory iteration (ws_root-parameterized)
# ---------------------------------------------------------------------------

def _iter_study_dirs(ws_root: Path):
    """Yield every study directory across studies/ and investigations/.

    Parameterized version of ``server._iter_study_dirs`` — accepts
    ``ws_root`` instead of reading the ``WORKSPACE`` global.

    Delegates to ``WorkspacePaths.iter_study_dirs`` (nested layout) and
    also picks up legacy ``investigations/<name>/spec.yaml`` studies that
    pre-date the nested layout.
    """
    from vivarium_dashboard.lib.workspace_paths import WorkspacePaths

    try:
        wp = WorkspacePaths.load(ws_root)
    except Exception:
        return

    seen: set[str] = set()
    try:
        for d in wp.iter_study_dirs():
            seen.add(d.name)
            yield d
    except Exception:
        pass

    # Legacy: studies stored directly under investigations/<name>/spec.yaml.
    try:
        inv_root = wp.dir("investigations")
    except Exception:
        return
    if inv_root.is_dir():
        for d in sorted(inv_root.iterdir()):
            if not d.is_dir() or d.name in seen:
                continue
            if (d / "investigation.yaml").is_file():
                continue  # an investigation collection, not a study
            if (d / "spec.yaml").is_file() or (d / "study.yaml").is_file():
                seen.add(d.name)
                yield d


# ---------------------------------------------------------------------------
# Run counting (ws_root-parameterized)
# ---------------------------------------------------------------------------

def _count_runs_for_study(
    ws_root: Path, name: str, spec: Optional[dict] = None
) -> int:
    """Count runs for a study, parameterized by ws_root.

    Checks the study's runs.db for row counts in runs_meta; falls back
    to ``len(spec.runs)``.  Returns the larger of the two so the dashboard
    never undercounts.  Never raises.
    """
    from vivarium_dashboard.lib.workspace_paths import WorkspacePaths

    db_count = 0
    try:
        wp = WorkspacePaths.load(ws_root)
        # Prefer the WorkspacePaths resolver (handles nested layout).
        try:
            study_d = wp.study_dir(name)
        except FileNotFoundError:
            # Fall back to the flat studies/ path so the count still works
            # even when WorkspacePaths can't locate the study.
            study_d = ws_root / "studies" / name

        runs_db = study_d / "runs.db"
        if runs_db.is_file():
            conn = sqlite3.connect(str(runs_db))
            try:
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                if "runs_meta" in tables:
                    row = conn.execute("SELECT COUNT(*) FROM runs_meta").fetchone()
                    db_count = row[0] if row else 0
            except Exception:
                db_count = 0
            finally:
                conn.close()
    except Exception:
        db_count = 0

    spec_count = 0
    if spec is not None:
        spec_count = len(spec.get("runs") or [])
    return max(db_count, spec_count)


# ---------------------------------------------------------------------------
# Pure helpers (stateless, no workspace state)
# ---------------------------------------------------------------------------

def _format_baseline_source(spec: dict) -> str:
    """Summarise a v3 study's baseline as a short label.

    - 1 entry: ``pkg:name`` if the composite contains ``.composites.``;
      otherwise the composite verbatim.
    - N entries: format the first as above, then append ``(+N-1 more)``.
    - 0 entries / missing / not a list: ``''``.
    """
    baseline = spec.get("baseline") or []
    if not isinstance(baseline, list) or not baseline:
        return ""
    first = baseline[0] if isinstance(baseline[0], dict) else None
    if first is None:
        return ""
    composite = (first.get("composite") or "").strip()
    if not composite:
        return ""
    if ".composites." in composite:
        pkg, _, rest = composite.partition(".composites.")
        label = f"{pkg}:{rest}"
    else:
        label = composite
    if len(baseline) > 1:
        return f"{label} (+{len(baseline) - 1} more)"
    return label


def _conclusions_excerpt(spec: dict, limit: int = 240) -> str:
    """Return a single-line preview of ``spec.conclusions`` for index cards.

    Drops the structured H2 headers (``## Claims``, ``## Evidence``,
    ``## Limitations``, ``## Next steps``), collapses whitespace, and
    truncates to ``limit`` characters.
    """
    text = (spec.get("conclusions") or "").strip()
    if not text:
        return ""
    # Drop the structured H2 headers so the excerpt is just the prose.
    text = re.sub(
        r"^##\s+(Claims|Evidence|Limitations|Next steps)\s*$",
        "",
        text,
        flags=re.MULTILINE | re.IGNORECASE,
    )
    # Collapse whitespace + truncate.
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


def _http_get_json(url: str, timeout: float = 1.5) -> Optional[dict]:
    """Best-effort GET -> JSON.

    Returns ``None`` on ANY failure (timeout, non-2xx, invalid JSON,
    network error).  Never raises -- callers treat ``None`` as
    'peer unreachable'.
    """
    try:
        import json as _json
        import urllib.error
        import urllib.request

        with urllib.request.urlopen(url, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            data = resp.read()
            return _json.loads(data.decode("utf-8", errors="replace"))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# DAG / condition helpers
# ---------------------------------------------------------------------------

def _normalize_parents(spec: dict) -> list[dict]:
    """Normalize a study's DAG prerequisites to ``[{study, condition}]``."""
    from vivarium_dashboard.lib.investigations import normalize_dag_edges

    return normalize_dag_edges(spec)


def _condition_satisfied(parent: Optional[dict], condition: str) -> bool:
    """Return True if ``parent`` satisfies the given prerequisite ``condition``.

    Conditions:
      - ``"ran"``          -- study has at least been executed (``status`` in
                             ``ran | evaluated | complete``)
      - ``"complete"``     -- study is fully done
      - ``"tests-passed"`` -- all behavior-test outcomes are PASS (pbg-superpowers)
    """
    if parent is None:
        return False
    status = parent.get("status", "planned")
    if condition == "ran":
        # "evaluated" is a later lifecycle state than "ran"; treat it as
        # satisfying a "ran" prerequisite too.
        return status in ("ran", "evaluated", "complete")
    if condition == "complete":
        return status == "complete"
    if condition == "tests-passed":
        try:
            from pbg_superpowers import study_status  # type: ignore[import]

            counts = study_status.count_test_outcomes(parent, parent.get("runs"))
            return counts["fail"] == 0 and counts["pass"] > 0
        except Exception:
            return False
    return False


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_investigations(ws_root: Path) -> dict:
    """Pure data builder for ``GET /api/investigations``.

    Returns ``{"investigations": [...]}`` -- the same shape the stdlib
    handler produces.  ``ws_root``-parameterized: no dependency on the
    ``WORKSPACE`` global so the FastAPI route can call it directly.

    Parameters
    ----------
    ws_root:
        Workspace root directory (typically contains ``workspace.yaml``).

    Returns
    -------
    dict
        ``{"investigations": [...]}``.  Each entry is either a full row
        dict (valid spec) or a minimal ``{name, status: "invalid", error}``
        dict (malformed spec.yaml).
    """
    _ws_add_to_sys_path(ws_root)

    from vivarium_dashboard.lib.investigations import (
        InvestigationSpecError,
        load_spec,
    )
    from vivarium_dashboard.lib.spec_norm import normalize_requirements

    # First pass: load every spec so we can resolve cross-study conditions.
    loaded: list[tuple[Path, dict]] = []
    for d in _iter_study_dirs(ws_root):
        spec_path = (
            d / "study.yaml" if (d / "study.yaml").is_file() else d / "spec.yaml"
        )
        if not spec_path.is_file():
            continue
        try:
            loaded.append((d, load_spec(spec_path)))
        except InvestigationSpecError as e:
            loaded.append(
                (d, {"__invalid__": True, "name": d.name, "error": str(e)})
            )

    by_name: dict[str, dict] = {
        s["name"]: s for _, s in loaded if not s.get("__invalid__")
    }

    out = []
    for d, spec in loaded:
        if spec.get("__invalid__"):
            out.append(
                {"name": spec["name"], "status": "invalid", "error": spec["error"]}
            )
            continue

        composites = spec.get("composites") or []
        if composites:
            composite_summary = ", ".join(c.get("name", "") for c in composites)
            n_runs = _count_runs_for_study(ws_root, spec["name"], spec)
        else:
            composite_summary = spec.get("composite", "")
            n_runs = _count_runs_for_study(ws_root, spec["name"], spec)
            if n_runs == 0:
                n_runs = len(spec.get("simulations") or [])

        parents = _normalize_parents(spec)
        blocked_by = []
        for p in parents:
            parent_spec = by_name.get(p["study"])
            if not _condition_satisfied(parent_spec, p["condition"]):
                blocked_by.append(
                    {
                        "study": p["study"],
                        "condition": p["condition"],
                        "missing": (
                            "parent-not-found"
                            if parent_spec is None
                            else f"parent.status={parent_spec.get('status', 'planned')}"
                        ),
                    }
                )

        sim_set_top = spec.get("simulation_set") or []
        beh_tests_top = (
            spec.get("behavior_tests") or spec.get("expected_behavior") or []
        )
        readouts_top = spec.get("readouts") or spec.get("observables") or []
        reqs_top = normalize_requirements(
            spec.get("implementation_requirements") or spec.get("gaps")
        )
        n_variants_top = (
            len(sim_set_top) if sim_set_top else len(spec.get("variants") or [])
        )
        row = {
            "name": spec["name"],
            "composite": composite_summary,
            "composites": composites,
            "description": spec.get("description", ""),
            "topic": spec.get("topic", ""),
            "tags": spec.get("tags") or [],
            "status": spec.get("status", "planned"),
            "phase": spec.get("phase"),
            "last_run": spec.get("last_run"),
            "n_simulations": n_runs,
            "baseline_names": [
                b.get("name", "")
                for b in (spec.get("baseline") or [])
                if isinstance(b, dict)
            ],
            "n_baseline": len(spec.get("baseline") or []),
            "n_variants": n_variants_top,
            "n_groups": len(spec.get("groups") or []),
            "n_interventions": len(spec.get("interventions") or []),
            "n_behaviors": len(beh_tests_top),
            "n_readouts": len(readouts_top),
            "n_requirements": len(reqs_top),
            "n_comparisons": len(spec.get("comparisons") or []),
            "n_runs": n_runs,
            "baseline_source": _format_baseline_source(spec),
            "conclusions_excerpt": _conclusions_excerpt(spec),
            "parent_studies": parents,
            "blocked": len(blocked_by) > 0,
            "blocked_by": blocked_by,
        }
        out.append(row)
    return {"investigations": out}
