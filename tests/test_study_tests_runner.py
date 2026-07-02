"""Tests for the per-study pytest runner."""
import yaml
from pathlib import Path
from vivarium_workbench.lib.study_tests import (
    run_study_tests, StudyTestsResult, StudyTestsConcurrentError,
)


def _make_study(workspace: Path, slug: str, *, test_body: str) -> Path:
    study = workspace / "studies" / slug
    (study / "tests").mkdir(parents=True)
    (study / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 4, "name": slug, "baseline": [],
        "tests": {"auto_discover": True, "data_source": "latest_run", "pytest_args": [], "last_results": None},
        "references": [], "implementation_tasks": "",
    }))
    (study / "tests" / "conftest.py").write_text(
        "from vivarium_workbench.testing import run  # noqa: F401\n"
    )
    (study / "tests" / "test_demo.py").write_text(test_body)
    return study


def test_run_study_tests_no_tests_dir(tmp_path):
    study = tmp_path / "studies" / "demo"
    study.mkdir(parents=True)
    (study / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 4, "name": "demo", "baseline": [],
        "tests": {"auto_discover": True, "data_source": "latest_run", "pytest_args": [], "last_results": None},
        "references": [], "implementation_tasks": "",
    }))
    result = run_study_tests(tmp_path, "demo")
    assert result.summary == {"passed": 0, "failed": 0, "skipped": 0, "duration_s": 0.0}
    assert result.tests == []
    assert result.note == "no tests directory"


def test_run_study_tests_collects_passing_test(tmp_path):
    _make_study(tmp_path, "demo", test_body="def test_one(): assert 1 == 1\n")
    result = run_study_tests(tmp_path, "demo")
    assert result.summary["passed"] == 1
    assert result.summary["failed"] == 0
    assert len(result.tests) == 1
    assert result.tests[0]["outcome"] == "passed"


def test_run_study_tests_collects_failing_test(tmp_path):
    _make_study(tmp_path, "demo", test_body="def test_fail(): assert 1 == 2\n")
    result = run_study_tests(tmp_path, "demo")
    assert result.summary["failed"] == 1
    assert result.tests[0]["outcome"] == "failed"
    assert "assert 1 == 2" in result.tests[0].get("message", "") or \
           "assert 1 == 2" in result.tests[0].get("traceback", "")


def test_run_study_tests_writes_last_results_to_yaml(tmp_path):
    _make_study(tmp_path, "demo", test_body="def test_one(): assert True\n")
    run_study_tests(tmp_path, "demo")
    spec = yaml.safe_load((tmp_path / "studies" / "demo" / "study.yaml").read_text())
    lr = spec["tests"]["last_results"]
    assert lr is not None
    assert lr["passed"] == 1
    assert "timestamp" in lr


def test_run_study_tests_concurrent_raises(tmp_path):
    import threading, time
    _make_study(tmp_path, "demo", test_body="import time\ndef test_slow(): time.sleep(0.5); assert True\n")
    results = []
    errors = []
    def worker():
        try:
            results.append(run_study_tests(tmp_path, "demo"))
        except StudyTestsConcurrentError as e:
            errors.append(e)
    t1 = threading.Thread(target=worker); t1.start()
    time.sleep(0.05)  # ensure t1 grabs the lock first
    t2 = threading.Thread(target=worker); t2.start()
    t1.join(); t2.join()
    assert len(results) == 1
    assert len(errors) == 1
