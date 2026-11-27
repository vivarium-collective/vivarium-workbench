"""The doctor's workspace-composite check — catches a stale venv after a sim-split,
where the workspace pins a composite package that was never installed into the venv,
so the registry silently resolves no composites."""
from pathlib import Path

from vivarium_workbench.lib import dep_doctor


def _ws(tmp_path: Path, *refs: str) -> Path:
    """A minimal workspace whose one study references the given composite dotted paths."""
    study = tmp_path / "studies" / "s0"
    study.mkdir(parents=True)
    body = "baseline:\n" + "".join(f"  - composite: {r}\n" for r in refs)
    (study / "study.yaml").write_text(body, encoding="utf-8")
    return tmp_path


def test_gathers_composite_package_prefixes(tmp_path):
    ws = _ws(tmp_path, "viva_genesis.composites.carbon_fixation",
                        "viva_genesis.composites.autopoiesis",
                        "other_pkg.composites.thing")
    assert dep_doctor._workspace_composite_packages(ws) == {"viva_genesis", "other_pkg"}


def test_importable_package_is_ok_missing_one_is_a_problem(tmp_path):
    # vivarium_workbench is importable in its own test env; the random name is not.
    ws = _ws(tmp_path, "vivarium_workbench.composites.x",
                        "nonexistent_sim_pkg_zzz.composites.y")
    findings = dep_doctor.check_workspace_composites(ws)
    by = {f["target"]: f for f in findings}
    assert by["vivarium_workbench"]["ok"] is True
    assert by["nonexistent_sim_pkg_zzz"]["ok"] is False
    # the fix must point at the re-sync, not a generic message
    assert "uv pip install -e ." in by["nonexistent_sim_pkg_zzz"]["fix"]
    assert dep_doctor.problems(findings) == [by["nonexistent_sim_pkg_zzz"]]


def test_no_composites_means_no_findings(tmp_path):
    (tmp_path / "studies").mkdir()
    assert dep_doctor.check_workspace_composites(tmp_path) == []


def test_report_renders_the_stale_venv_fix(tmp_path):
    ws = _ws(tmp_path, "nonexistent_sim_pkg_zzz.composites.y")
    report = dep_doctor.format_report(dep_doctor.check_workspace_composites(ws))
    assert "nonexistent_sim_pkg_zzz" in report
    assert "re-sync" in report
