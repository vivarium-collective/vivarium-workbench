"""Tests for the workspace-module import doctor (installed != importable)."""
import yaml

from vivarium_workbench.lib.module_import_doctor import (
    diagnose_module_imports,
    module_import_problems,
    format_report,
)


def _write_ws(tmp_path, imports: dict, package_path=None):
    data = {"name": "host", "imports": imports}
    if package_path is not None:
        data["package_path"] = package_path
    (tmp_path / "workspace.yaml").write_text(yaml.safe_dump(data))
    return tmp_path


def test_reports_importable_and_broken_modules(tmp_path):
    _write_ws(tmp_path, {"json": {}, "nonexistent_pkg_zzz": {}})
    findings = diagnose_module_imports(tmp_path)
    by_mod = {f["module"]: f for f in findings}

    assert by_mod["json"]["ok"] is True
    assert by_mod["json"]["detail"] == "importable"

    broken = by_mod["nonexistent_pkg_zzz"]
    assert broken["ok"] is False
    # The real ImportError reason is surfaced (installed metadata would hide it).
    assert "No module named" in broken["detail"]
    assert broken["source"] == "imports"


def test_problems_filters_to_failing_only(tmp_path):
    _write_ws(tmp_path, {"json": {}, "nonexistent_pkg_zzz": {}})
    findings = diagnose_module_imports(tmp_path)
    probs = module_import_problems(findings)
    assert [p["module"] for p in probs] == ["nonexistent_pkg_zzz"]


def test_reference_mode_modules_are_skipped(tmp_path):
    # A `mode: reference` module is declared for browsing, not import — it must
    # not be probed (and so never flagged as a problem).
    _write_ws(tmp_path, {"nonexistent_ref_zzz": {"mode": "reference"}})
    findings = diagnose_module_imports(tmp_path)
    assert findings == []


def test_dash_distribution_name_normalized_to_import(tmp_path):
    # A distribution named with dashes imports with underscores.
    _write_ws(tmp_path, {"nonexistent-dash-pkg": {}})
    findings = diagnose_module_imports(tmp_path)
    assert len(findings) == 1
    assert findings[0]["module"] == "nonexistent_dash_pkg"
    assert findings[0]["name"] == "nonexistent-dash-pkg"


def test_explicit_package_field_wins(tmp_path):
    _write_ws(tmp_path, {"some-dist": {"package": "json"}})
    findings = diagnose_module_imports(tmp_path)
    assert findings[0]["module"] == "json"
    assert findings[0]["ok"] is True


def test_package_path_is_probed(tmp_path):
    _write_ws(tmp_path, {}, package_path="nonexistent_wspkg_zzz")
    findings = diagnose_module_imports(tmp_path)
    assert any(f["source"] == "package_path" and f["module"] == "nonexistent_wspkg_zzz"
               for f in findings)


def test_missing_workspace_yaml_never_raises(tmp_path):
    assert diagnose_module_imports(tmp_path) == []


def test_format_report_flags_problems(tmp_path):
    _write_ws(tmp_path, {"nonexistent_pkg_zzz": {}})
    report = format_report(ws_root=tmp_path)
    assert "fail to import" in report
    assert "installed ≠ importable" in report
