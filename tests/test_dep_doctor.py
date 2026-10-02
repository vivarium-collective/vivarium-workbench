import builtins
import importlib
import importlib.metadata

from vivarium_workbench.lib import dep_doctor


def test_check_returns_a_finding_per_probe():
    findings = dep_doctor.check_framework_deps()
    targets = {f["target"] for f in findings}
    assert {"process_bigraph.artifacts", "viva_superpowers.test_audit",
            "viva_superpowers.loop_state"} <= targets
    for f in findings:
        assert set(f) >= {"ok", "target", "why", "detail", "fix"}


def test_never_raises_and_flags_a_missing_module(monkeypatch):
    real_import = importlib.import_module

    def fake(name, *a, **k):
        if name == "viva_superpowers.test_audit":
            raise ModuleNotFoundError("No module named 'viva_superpowers.test_audit'")
        return real_import(name, *a, **k)

    monkeypatch.setattr(dep_doctor.importlib, "import_module", fake)
    findings = dep_doctor.check_framework_deps()          # must not raise
    audit = next(f for f in findings if f["target"] == "viva_superpowers.test_audit")
    assert audit["ok"] is False
    assert audit["fix"]                                    # actionable fix text present
    assert "unavailable" in audit["fix"].lower()
    probs = dep_doctor.problems(findings)
    assert any(p["target"] == "viva_superpowers.test_audit" for p in probs)
    # warn_lines produces one compact line per problem
    lines = dep_doctor.warn_lines(findings)
    assert any("viva_superpowers.test_audit" in ln for ln in lines)


def test_version_floor_flags_a_too_old_dep(monkeypatch):
    monkeypatch.setattr(dep_doctor.importlib.metadata, "version",
                        lambda dist: "1.4.12" if dist == "process-bigraph" else "9.9.9")
    findings = dep_doctor.check_framework_deps()          # must not raise
    pb = next(f for f in findings if f["target"] == "process-bigraph>=1.8.5")
    assert pb["ok"] is False
    assert "1.4.12" in pb["detail"]
    assert pb["fix"]                                       # actionable fix text present
    assert any(p["target"] == "process-bigraph>=1.8.5" for p in dep_doctor.problems(findings))


def test_version_floor_passes_when_current(monkeypatch):
    monkeypatch.setattr(dep_doctor.importlib.metadata, "version", lambda dist: "2.0.0")
    findings = dep_doctor.check_framework_deps()
    pb = next(f for f in findings if f["target"] == "process-bigraph>=1.8.5")
    assert pb["ok"] is True


def test_version_floor_skips_an_uninstalled_dep(monkeypatch):
    def fake(dist):
        raise importlib.metadata.PackageNotFoundError(dist)
    monkeypatch.setattr(dep_doctor.importlib.metadata, "version", fake)
    findings = dep_doctor.check_framework_deps()          # must not raise
    assert not any(f["target"].startswith("process-bigraph>=") for f in findings)


def test_format_report_is_readable():
    txt = dep_doctor.format_report()
    assert "Framework dependency doctor:" in txt
