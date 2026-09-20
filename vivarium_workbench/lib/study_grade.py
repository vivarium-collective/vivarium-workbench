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

# Measure kinds that grade WITHOUT a run store: derived-scalar checks read
# persisted observables through the workspace derived-scalar registry, and
# config-value checks read the study's declared params. A study whose tests are
# all of these kinds can be graded even when it has no simulation run.
_STORELESS_KINDS = frozenset({"derived_scalar", "derived", "config_value"})


def grade_study(ws_root: Path, slug: str) -> tuple[dict, int]:
    ws_root = Path(ws_root)
    study_dir = WorkspacePaths.load(ws_root).studies / slug
    spec_path = study_dir / "study.yaml"
    if not spec_path.exists():
        return {"error": f"study not found: {slug}"}, 404

    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
    run = canonical_run(spec)
    if run is None:
        # No simulation run. Grade store-less ONLY when every declared test can
        # be evaluated without a run store — derived-scalar checks (read
        # persisted observables via the workspace registry) and config-value
        # checks (read declared params). This is the params-only study case
        # (e.g. a ParCa study). Synthesize an evaluation-only run so the outcomes
        # have a home in runs[], then grade it store-less. A study whose tests
        # need run data has genuinely nothing to grade until it runs.
        # ``tests`` may be a behavior-test LIST or the pytest-config DICT
        # (auto_discover/data_source/...). Only a non-empty list of test dicts
        # can be graded store-less.
        tests = spec.get("behavior_tests")
        if not isinstance(tests, list):
            candidate = spec.get("tests")
            tests = candidate if isinstance(candidate, list) else []
        if tests and all(
            isinstance(t, dict)
            and (t.get("measure") or {}).get("kind") in _STORELESS_KINDS
            for t in tests
        ):
            run_id = _ensure_evaluation_run(spec_path)
        else:
            return {"graded": False, "reason": "no_run"}, 200
    else:
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


def _ensure_evaluation_run(spec_path: Path) -> str:
    """Append an evaluation-only run to a study.yaml ``runs[]`` if absent.

    Gives a run-less (params-only) study a home for its outcomes and marks the
    run store-less (``evaluation_only: true``) so grading proceeds without an
    openable store. Idempotent (reuses an existing evaluation run). Returns the
    run id. Comment-preserving ruamel round-trip.
    """
    import io  # noqa: PLC0415
    from datetime import datetime, timezone  # noqa: PLC0415

    import ruamel.yaml  # noqa: PLC0415

    from .atomic_io import atomic_write_text  # noqa: PLC0415

    slug = spec_path.parent.name
    run_id = f"{slug}-evaluation"
    ryaml = ruamel.yaml.YAML()
    with spec_path.open("r", encoding="utf-8") as fh:
        doc = ryaml.load(fh)
    runs = doc.get("runs")
    if not isinstance(runs, list):
        runs = ryaml.load("[]")
        doc["runs"] = runs
    for r in runs:
        if isinstance(r, dict) and (r.get("run_id") == run_id or r.get("name") == run_id):
            return run_id  # already present — idempotent
    entry = ruamel.yaml.comments.CommentedMap()
    entry["name"] = "evaluation"
    entry["run_id"] = run_id
    entry["status"] = "completed"
    entry["evaluation_only"] = True
    # Float epoch timestamp — matches the convention real runs carry
    # (composite_runs writes time.time(); on-disk runs[].timestamp are floats),
    # so canonical_run's max()-by-timestamp never mixes float and str.
    entry["timestamp"] = datetime.now(timezone.utc).timestamp()
    runs.append(entry)
    sio = io.StringIO()
    ryaml.dump(doc, sio)
    atomic_write_text(spec_path, sio.getvalue())  # never a half-written study.yaml
    return run_id


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
