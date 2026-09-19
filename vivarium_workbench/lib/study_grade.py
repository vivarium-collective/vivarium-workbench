"""On-demand grading of a study's declared behavior tests against its latest
completed run — the fast 'Run tests' path (no re-simulation).

Writes the authoritative runs[].outcomes via auto_evaluate (overwrite_authored
=False) and refreshes the behavior-test card. Returns (body, status).
"""
from __future__ import annotations

from pathlib import Path

import yaml
from viva_workspace.outcomes import canonical_run

from . import auto_evaluate, behavior_test_card
from .workspace_paths import WorkspacePaths


def grade_study(ws_root: Path, slug: str) -> tuple[dict, int]:
    ws_root = Path(ws_root)
    study_dir = WorkspacePaths.load(ws_root).studies / slug
    spec_path = study_dir / "study.yaml"
    if not spec_path.exists():
        return {"error": f"study not found: {slug}"}, 404

    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
    run = canonical_run(spec)
    if run is None:
        return {"graded": False, "reason": "no_run"}, 200
    run_id = run.get("run_id") or run.get("name")
    if not run_id:
        return {"graded": False, "reason": "no_run"}, 200

    result = auto_evaluate.evaluate_on_run_completion(
        study_dir, run_id, ws_root=ws_root, overwrite_authored=False,
    )
    # evaluate_on_run_completion's real status vocabulary (auto_evaluate.py):
    # "ok" | "run_not_found" | "no_tests" | "store_unresolved"
    # | "evaluator_unavailable: <msg>" | "runner_error: <msg>" — the last two
    # carry a trailing detail, so they can't be matched by equality. Anything
    # other than "ok" means there is no usable graded run right now.
    status = result.get("status") if isinstance(result, dict) else None
    if status != "ok":
        return {"graded": False, "reason": status}, 200

    behavior_test_card.write_behavior_test_card(study_dir)

    # Re-read the enriched spec surfaces the tab consumes.
    spec2 = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
    rollup = _rollup(spec2)
    return {"graded": True, "outcome_rollup": rollup, "run_id": run_id}, 200


def _rollup(spec: dict) -> dict:
    passed = failed = skipped = 0
    for r in spec.get("runs") or []:
        for o in (r.get("outcomes") or {}).values():
            res = (o or {}).get("result")
            if res == "PASS":
                passed += 1
            elif res == "FAIL":
                failed += 1
            elif res == "SKIP":
                skipped += 1
    total = passed + failed + skipped
    return {"PASS": passed, "FAIL": failed, "SKIP": skipped, "total": total}
