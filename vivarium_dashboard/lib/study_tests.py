"""Per-study pytest runner. Shells out to pytest with --json-report,
parses results, writes a compact summary into study.yaml.tests.last_results.
"""
from __future__ import annotations
import json, os, subprocess, sys, time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import yaml

from .workspace_paths import WorkspacePaths


class StudyTestsConcurrentError(RuntimeError):
    """Raised when a second test run is requested while one is already running."""


@dataclass
class StudyTestsResult:
    summary: dict        # {passed, failed, skipped, duration_s}
    tests: list[dict]    # [{nodeid, outcome, duration, message?, traceback?}]
    note: str | None = None
    raw_stderr: str = ""


def _study_paths(workspace: Path, slug: str) -> tuple[Path, Path, Path]:
    study_dir = WorkspacePaths.load(workspace).studies / slug
    tests_dir = study_dir / "tests"
    spec_path = study_dir / "study.yaml"
    return study_dir, tests_dir, spec_path


@contextmanager
def _study_lock(workspace: Path, slug: str):
    lockdir = WorkspacePaths.load(workspace).pbg / "study-test-results"
    lockdir.mkdir(parents=True, exist_ok=True)
    lockfile = lockdir / f"{slug}.lock"
    if lockfile.exists():
        raise StudyTestsConcurrentError(f"tests already running for study {slug!r}")
    lockfile.write_text(str(os.getpid()))
    try:
        yield lockfile
    finally:
        try:
            lockfile.unlink()
        except FileNotFoundError:
            pass


def run_study_tests(workspace: Path, slug: str) -> StudyTestsResult:
    """Run pytest against studies/<slug>/tests/. Returns a StudyTestsResult.

    Writes a compact summary to study.yaml.tests.last_results.
    """
    workspace = Path(workspace)
    study_dir, tests_dir, spec_path = _study_paths(workspace, slug)
    if not spec_path.exists():
        raise FileNotFoundError(f"study not found: {spec_path}")

    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
    # `tests:` is the behaviour-test LIST in spine studies (not a {pytest_args:...}
    # config dict) — guard so we never call .get() on a list.
    _tests_cfg = spec.get("tests")
    pytest_args = (_tests_cfg.get("pytest_args", []) if isinstance(_tests_cfg, dict) else []) or []

    has_pytest = tests_dir.is_dir() and any(tests_dir.glob("test_*.py"))
    # Spine studies declare `tests:` as a behaviour-test LIST (measure / pass_if),
    # evaluated by the run/outcome-spine evaluator against the latest run — not by
    # pytest. Route those to the evaluator so "Run tests" actually re-evaluates them.
    if not has_pytest and isinstance(_tests_cfg, list) and _tests_cfg:
        return _run_spine_tests(workspace, slug, study_dir, spec_path)

    if not has_pytest:
        result = StudyTestsResult(
            summary={"passed": 0, "failed": 0, "skipped": 0, "duration_s": 0.0},
            tests=[], note="no tests directory",
        )
        _write_last_results(spec_path, result)
        return result

    with _study_lock(workspace, slug):
        results_dir = WorkspacePaths.load(workspace).pbg / "study-test-results"
        results_dir.mkdir(parents=True, exist_ok=True)
        json_report = results_dir / f"{slug}.json"
        if json_report.exists():
            json_report.unlink()

        cmd = [
            sys.executable, "-m", "pytest", str(tests_dir),
            "--json-report", f"--json-report-file={json_report}",
            "-q", "--no-header", "--tb=short",
            *pytest_args,
        ]
        t0 = time.time()
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(workspace))
        duration = time.time() - t0

        if not json_report.exists():
            # pytest crashed before writing the report
            result = StudyTestsResult(
                summary={"passed": 0, "failed": 0, "skipped": 0, "duration_s": duration},
                tests=[],
                note=f"pytest exited with code {proc.returncode}, no JSON report",
                raw_stderr=proc.stderr,
            )
            _write_last_results(spec_path, result)
            return result

        report = json.loads(json_report.read_text(encoding="utf-8"))
        tests = []
        for t in report.get("tests", []):
            entry = {
                "nodeid": t["nodeid"],
                "outcome": t["outcome"],
                "duration": t.get("duration", 0.0),
            }
            call = t.get("call") or {}
            if call.get("longrepr"):
                entry["traceback"] = call["longrepr"]
            if call.get("crash"):
                entry["message"] = call["crash"].get("message", "")
            tests.append(entry)
        summary = report.get("summary", {})
        result = StudyTestsResult(
            summary={
                "passed": summary.get("passed", 0),
                "failed": summary.get("failed", 0),
                "skipped": summary.get("skipped", 0),
                "duration_s": summary.get("duration", duration),
            },
            tests=tests,
        )
        _write_last_results(spec_path, result)
        return result


def _run_spine_tests(
    workspace: Path, slug: str, study_dir: Path, spec_path: Path
) -> StudyTestsResult:
    """Run the run/outcome-spine evaluator for a study whose ``tests:`` is the
    behaviour-test list (measure / pass_if), instead of pytest.

    Calls ``pbg_superpowers.study_evaluator.compute_outcomes`` (writes the parallel
    ``computed_outcomes`` block per run), then reports the latest run's per-test
    verdicts in the StudyTestsResult shape the Tests tab already renders. The
    authored verdict (if any) is the headline; the code-computed measured value /
    evaluator / reconcile flag is attached as the per-test detail.
    """
    t0 = time.time()
    try:
        from pbg_superpowers.study_evaluator import compute_outcomes
    except Exception as e:  # noqa: BLE001
        return StudyTestsResult(
            summary={"passed": 0, "failed": 0, "skipped": 0, "duration_s": 0.0},
            tests=[], note=f"spine evaluator unavailable: {type(e).__name__}: {e}",
        )
    try:
        meta = compute_outcomes(study_dir, workspace) or {}
    except Exception as e:  # noqa: BLE001
        return StudyTestsResult(
            summary={"passed": 0, "failed": 0, "skipped": 0, "duration_s": time.time() - t0},
            tests=[], note=f"spine evaluator error: {type(e).__name__}: {e}",
        )
    duration = time.time() - t0

    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
    runs = spec.get("runs") or []
    run = next(
        (r for r in reversed(runs) if isinstance(r, dict) and r.get("computed_outcomes")),
        None,
    )
    computed = (run or {}).get("computed_outcomes") or {}
    authored = (run or {}).get("outcomes") or {}

    passed = failed = skipped = 0
    tests = []
    for t in (spec.get("tests") or []):
        if not isinstance(t, dict):
            continue
        name = t.get("name")
        if not name:
            continue
        c = computed.get(name) or {}
        a = authored.get(name) or {}
        result = a.get("result") or c.get("result")     # authored headline, else code
        evb = c.get("evaluated_by") or ("authored" if a.get("result") else None)
        if result == "PASS":
            outcome = "passed"; passed += 1
        elif result == "FAIL":
            outcome = "failed"; failed += 1
        else:                                            # PARTIAL / SKIP / agent / pending
            outcome = "skipped"; skipped += 1
        bits = []
        if c.get("measured_value") is not None:
            bits.append(f"measured: {c['measured_value']}")
        if evb:
            bits.append(f"evaluated_by: {evb}")
        if c.get("reconcile"):
            bits.append(f"reconcile: {c['reconcile']}")
        if c.get("detail") or c.get("reason"):
            bits.append(str(c.get("detail") or c.get("reason")))
        tests.append({
            "nodeid": name,
            "outcome": outcome,
            "duration": 0.0,
            "traceback": "  ·  ".join(bits) if bits else "",
        })

    if run is None:
        note = "spine evaluator — no run with computed outcomes (run the study first)"
    else:
        note = (
            f"spine evaluator — evaluated {meta.get('runs_evaluated', 0)} run(s); "
            f"code={meta.get('tests_code', 0)} agent={meta.get('tests_agent', 0)} "
            f"(latest run: {run.get('name', '?')})"
        )
    result = StudyTestsResult(
        summary={"passed": passed, "failed": failed, "skipped": skipped, "duration_s": duration},
        tests=tests, note=note,
    )
    _write_last_results(spec_path, result)
    return result


def _write_last_results(spec_path: Path, result: StudyTestsResult) -> None:
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
    meta = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **result.summary,
    }
    # In spine studies `tests:` is the behaviour-test LIST — don't clobber it; write
    # the pytest run-meta to a separate top-level key instead.
    if isinstance(spec.get("tests"), dict):
        spec["tests"]["last_results"] = meta
    else:
        spec["last_test_run"] = meta
    tmp = spec_path.with_suffix(spec_path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(spec, sort_keys=False))
    os.replace(tmp, spec_path)
