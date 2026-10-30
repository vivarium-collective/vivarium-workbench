"""POST reconcile-status endpoints + post-run flush self-heal hook."""
import json

import pytest
import yaml
from fastapi.testclient import TestClient

from vivarium_workbench.api.app import create_app, get_workspace
from vivarium_workbench.lib import active_workspace, study_runs
from vivarium_workbench.lib.composite_runs import (
    complete_metadata, connect, save_metadata,
)

SLUG = "s3-protrusions-to-enclosure"


@pytest.fixture(autouse=True)
def _reset_ws():
    saved = active_workspace.get_workspace_root()
    active_workspace._WS_ROOT = None
    yield
    active_workspace._WS_ROOT = saved


@pytest.fixture
def ws(tmp_path):
    schemas = tmp_path / ".pbg" / "schemas"
    schemas.mkdir(parents=True)
    (tmp_path / "workspace.yaml").write_text("name: t\n")
    (schemas / "study.schema.json").write_text(json.dumps({"properties": {
        "simulation_status": {"enum": ["not_run", "running", "ran", "failed"]},
    }}))
    sd = tmp_path / "studies" / SLUG
    sd.mkdir(parents=True)
    conn = connect(sd / "runs.db")
    save_metadata(conn, spec_id="demo", run_id="r1", params={}, label="",
                  started_at=100.0, n_steps=5)
    complete_metadata(conn, run_id="r1", n_steps=5, status="completed")
    conn.close()
    (sd / "study.yaml").write_text(
        yaml.safe_dump({"name": SLUG, "simulation_status": "not_run"}), encoding="utf-8")
    inv = tmp_path / "investigations" / "inv"
    inv.mkdir(parents=True)
    (inv / "investigation.yaml").write_text(yaml.safe_dump({"studies": [SLUG]}))
    return tmp_path


@pytest.fixture
def client(ws):
    app = create_app()
    app.dependency_overrides[get_workspace] = lambda: ws
    return TestClient(app)


def _stored(ws):
    return yaml.safe_load((ws / "studies" / SLUG / "study.yaml").read_text())[
        "simulation_status"]


def test_study_reconcile_endpoint_and_idempotent(client, ws):
    r = client.post(f"/api/study/{SLUG}/reconcile-status")
    assert r.status_code == 200, r.text
    assert r.json()["changed"] is True
    assert _stored(ws) == "ran"
    r2 = client.post(f"/api/study/{SLUG}/reconcile-status")
    assert r2.status_code == 200 and r2.json()["changed"] is False


def test_study_reconcile_unknown_slug_is_404(client):
    r = client.post("/api/study/no-such-study/reconcile-status")
    assert r.status_code == 404
    assert r.json() == {"error": "study not found", "slug": "no-such-study"}


def test_investigation_reconcile_endpoint(client, ws):
    r = client.post("/api/investigation/inv/reconcile-status")
    assert r.status_code == 200, r.text
    assert [s["slug"] for s in r.json()["studies"]] == [SLUG]
    assert _stored(ws) == "ran"


def test_flush_hook_reconciles(ws, monkeypatch):
    """Spy-based: the flush calls reconcile_study_status(ws_root, study_dir.name)."""
    from vivarium_workbench.lib import study_reconcile
    calls = []
    real = study_reconcile.reconcile_study_status
    monkeypatch.setattr(study_reconcile, "reconcile_study_status",
                        lambda w, s: (calls.append((w, s)), real(w, s))[1])
    for name in ("render_study_visualizations",):
        monkeypatch.setattr(study_runs.study_run_post, name, lambda *a, **k: ([], []))
    sd = ws / "studies" / SLUG
    try:
        study_runs._run_post_run_flush(ws, sd, {"name": SLUG}, "demo", "r1", {}, {},
                                       {}, skip_analyses=True)
    except Exception:
        pass  # other stages are irrelevant here; the hook is what we assert
    assert calls and calls[0] == (ws, SLUG)
    assert _stored(ws) == "ran"
