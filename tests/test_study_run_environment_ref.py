"""The study run-launch path forwards a declared environment_ref to the manifest.

``run_study_baseline`` reads the condition's ``environment_ref`` from the study
spec and ``launch_into_study`` threads it into ``build_run_manifest``, where it is
resolved + verified (see test_environments / test_run_manifest). These tests pin
the *threading* — the heavy collaborators (run target resolution, the subprocess
launch) are stubbed so no run actually executes.
"""
from vivarium_workbench.lib import study_runs as sr
from vivarium_workbench.lib import composite_runs as cr
from vivarium_workbench.lib import run_core
from vivarium_workbench.lib import remote_pinned


class _Plan:
    target = "local"


def _stub_launch(monkeypatch, tmp_path):
    """Stub everything after the manifest build; return the captured-kwargs dict."""
    captured: dict = {}

    def fake_manifest(**kw):
        captured.update(kw)
        return {}

    monkeypatch.setattr(cr, "build_run_manifest", fake_manifest)
    monkeypatch.setattr(run_core, "invoke_run", lambda *a, **k: _Plan())
    monkeypatch.setattr(remote_pinned, "resolve_run_target", lambda ws: "local")
    monkeypatch.setattr(sr, "_launch_run_and_flush", lambda *a, **k: ({"ok": True}, 200))
    sd = tmp_path / "studies" / "s"
    sd.mkdir(parents=True)
    monkeypatch.setattr(sr, "_resolve_study_dir", lambda ws, study: sd)
    return captured


def test_launch_into_study_forwards_environment_ref(tmp_path, monkeypatch):
    captured = _stub_launch(monkeypatch, tmp_path)
    resp, code = sr.launch_into_study(
        tmp_path, "s", "spec", {"seed": 0}, 10,
        dry_run=True, environment_ref="pulse")
    assert code == 200
    assert captured["environment_ref"] == "pulse"


def test_launch_into_study_defaults_environment_ref_none(tmp_path, monkeypatch):
    captured = _stub_launch(monkeypatch, tmp_path)
    sr.launch_into_study(tmp_path, "s", "spec", {"seed": 0}, 10, dry_run=True)
    assert captured["environment_ref"] is None
