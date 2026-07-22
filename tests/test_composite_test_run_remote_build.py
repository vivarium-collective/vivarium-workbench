"""SP-D2: remote-build workspace → dispatches to deployment (was SP-A's 409 guard).

A workspace carrying a ``.viv-build.json`` stamp has been materialised from a
remote build; ``run_core.run_target_for`` resolves it to the ``deployment``
target. Under SP-A this returned 409 (deployment execution unbuilt). SP-D2 BUILDS
that path: ``composite_test_run`` now accepts (202) and stamps ``target:
"deployment"`` into the run-request, so the detached runner dispatches to sms-api
``/compose/v1`` instead of running locally.
"""
from __future__ import annotations

import json


def test_composite_test_run_on_remote_build_dispatches(tmp_path, monkeypatch):
    from vivarium_workbench.lib import composite_test_run_views as v
    from vivarium_workbench.lib import run_registry

    (tmp_path / ".pbg").mkdir()
    (tmp_path / "workspace.yaml").write_text("name: remote-ws\n", encoding="utf-8")
    (tmp_path / ".viv-build.json").write_text('{"simulator_id": 66}')

    monkeypatch.setattr(run_registry, "count_running", lambda db_file: 0)
    monkeypatch.setattr(run_registry, "spawn_detached", lambda *a, **k: 4242)

    body, status = v.composite_test_run(
        tmp_path, {"id": "pkg.composites.x", "overrides": {}, "steps": 7})

    assert status == 202
    assert body["status"] == "running"

    # The run-request carries the deployment target so run_runner.execute dispatches remotely.
    run_dir = tmp_path / ".pbg" / "runs" / body["run_id"]
    req = json.loads((run_dir / "request.json").read_text())
    assert req["target"] == "deployment"
    assert req["steps"] == 7
