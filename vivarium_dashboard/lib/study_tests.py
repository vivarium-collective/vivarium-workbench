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
    pytest_args = (spec.get("tests") or {}).get("pytest_args", []) or []

    if not tests_dir.is_dir() or not any(tests_dir.glob("test_*.py")):
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


def _write_last_results(spec_path: Path, result: StudyTestsResult) -> None:
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
    spec.setdefault("tests", {})
    spec["tests"]["last_results"] = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **result.summary,
    }
    tmp = spec_path.with_suffix(spec_path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(spec, sort_keys=False))
    os.replace(tmp, spec_path)
