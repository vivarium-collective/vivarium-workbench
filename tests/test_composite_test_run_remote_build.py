"""SP-A: remote-build workspace → 409 guard on composite_test_run.

A workspace that carries a ``.viv-build.json`` stamp has been materialised
from a remote build (SP-D); running it locally is not supported.  The view
must detect this via ``run_core.run_target_for`` and return 409 instead of
silently spawning a local subprocess.
"""
from __future__ import annotations


def test_composite_test_run_on_remote_build_409(tmp_path, monkeypatch):
    from vivarium_workbench.lib import composite_test_run_views as v

    (tmp_path / ".pbg").mkdir()
    (tmp_path / "workspace.yaml").write_text("name: remote-ws\n", encoding="utf-8")
    (tmp_path / ".viv-build.json").write_text('{"simulator_id": 66}')

    body, status = v.composite_test_run(tmp_path, {"id": "pkg.composites.x", "overrides": {}})

    assert status == 409
    assert "deployment" in (body.get("error") or "").lower()
