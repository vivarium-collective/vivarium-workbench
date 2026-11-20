"""What a compose-api run records about itself, on the Composites tab's route and on the study route.

The study route lands a finished run through ``remote_run_compose.land`` and records the backend's own record of it
(environment version, image digest, SLURM job) and that the code that ran is the image's, not the checkout's. The
Composites tab's remote run (``POST /api/composite-test-run`` -> the detached runner -> ``run_remote`` ->
``run_composite``) landed the same results but recorded none of that, and kept the CHECKOUT's commit as the code
that ran, so a run could not be traced to the environment that ran it (docs/backend-compose-api.md, "A landed run
records ...").

Only the compose service is stood in for (``VivaV1Stub``, generated from compose-api's own OpenAPI, over real HTTP
to the real ``compose_api_client``). Everything under test is real: the endpoint that records the launch, the
detached runner, the dispatch, the landing, and the runs.db rows the assertions read. The fixtures are the ones of
``test_backend_dispatch_compose``.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from vivarium_workbench.lib import remote_run_compose as rc
from test_backend_dispatch_compose import (  # noqa: F401 -- fixtures, found by name
    COMPOSITE, PILOT, RUN_ID, SIM_ID, _completed, _json, _name, _no_ambient_logins,
    _register_fixture_workspace, backend_env, stub, ws)

VERSION = 3
#: The digest of the version the run ran in: NOT the one the environment's listing names today.
IMAGE = next(e["image"] for e in _json("environments.json") if e["name"] == PILOT).rsplit("@sha256:", 1)[0] \
    + "@sha256:" + "cd" * 32
SLURM_JOB = 4286252
VERSION_URL = "/registry/environments/{name}/versions/{number}"


def _git(ws: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(ws), "-c", "user.name=t", "-c", "user.email=t@example.com",
                           "-c", "commit.gpgsign=false", *args], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def head(ws) -> str:
    """The workspace is a checkout with a commit and an origin: what a launch records as the code that runs."""
    _git(ws, "init", "-q")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "the checkout")
    _git(ws, "remote", "add", "origin", "https://github.com/example/ws-increase-demo.git")
    return _git(ws, "rev-parse", "HEAD")


@pytest.fixture
def finished(ws, stub, backend_env):
    """compose-api has a completed run, SIM_ID, whose own record names the version it ran in."""
    _register_fixture_workspace(stub)
    _completed(stub)
    stub.respond("GET", "/simulations/{simulation_id}", 200,
                 {"simulation_id": SIM_ID, "experiment_id": "exp-1", "simulator_id": 282, "container_def_hash": "h",
                  "status": "completed", "simulator": None,
                  "environment_version": {"environment": PILOT, "number": VERSION}})
    stub.respond("GET", VERSION_URL, 200,
                 {"database_id": 7, "environment_id": 3, "environment": PILOT, "number": VERSION, "state": "ready",
                  "simulator_id": 282, "image": IMAGE})
    _name(backend_env, stub.url)
    return stub


def _card_run(ws: Path, client) -> "tuple[str, dict]":
    """Run the fixture composite from the Composites tab (the real endpoint and runner, a real subprocess) and
    return ``(run_id, the run's runs_meta row)`` once it is over."""
    res = client.post("/api/composite-test-run", json={"id": COMPOSITE, "steps": 3, "emit_paths": ["stores/level"]})
    assert res.status_code == 202, res.text
    run_id = res.json()["run_id"]
    deadline = time.monotonic() + 100
    while (state := client.get(f"/api/composite-run/{run_id}/status").json())["status"] == "running":
        assert time.monotonic() < deadline, state
        time.sleep(0.5)
    log = (ws / ".pbg" / "runs" / run_id / "run.log").read_text()
    assert state["status"] == "completed", log
    db_file = json.loads((ws / ".pbg" / "runs" / run_id / "request.json").read_text())["db_file"]
    with sqlite3.connect(db_file) as db:
        db.row_factory = sqlite3.Row
        row = dict(db.execute("SELECT * FROM runs_meta WHERE run_id = ?", (run_id,)).fetchone())
        row["history"] = db.execute("SELECT count(*) FROM history WHERE simulation_id = ?", (run_id,)).fetchone()[0]
    row["manifest"] = json.loads(row["manifest_json"])
    row["log"] = log
    return run_id, row


def _land_study(ws: Path, client) -> dict:
    """Land SIM_ID through the study route and return that run's params and manifest."""
    res = client.post("/api/remote-run-land", json={"study": "demo", "simulation_id": RUN_ID})
    assert res.status_code == 200, res.text
    with sqlite3.connect(ws / "studies" / "demo" / "runs.db") as db:
        params, manifest = db.execute("SELECT params_json, manifest_json FROM runs_meta WHERE run_id = ?",
                                      (res.json()["run_id"],)).fetchone()
    return {"params": json.loads(params), "manifest": json.loads(manifest)}


def test_a_composites_card_run_records_the_environment_version_image_and_job_that_ran_it(
        ws, head, finished, dashboard_client):
    """Falsifies: "a Composites-tab run on compose-api can be traced to the environment that ran it". The run's row
    must carry compose-api's own record of it (version it ran in, that version's image digest, SLURM job), and the
    code that ran is that image -- never the checkout's commit, which the launch recorded. Its history is landed."""
    c = dashboard_client(ws)
    run_id, row = _card_run(ws, c)
    manifest = row["manifest"]

    assert manifest["code_version"] == {"git_sha": None, "package": None, "repo": None, "remote_url": None,
                                        "image": IMAGE}
    assert manifest["compose"] == {"run_id": RUN_ID, "simulation_id": SIM_ID, "experiment_id": "exp-1",
                                   "image": IMAGE, "environment_version": f"{PILOT}@{VERSION}",
                                   "slurm_job_id": SLURM_JOB}
    (primary,) = [e for e in manifest["environments"] if e["role"] == "primary"]
    assert (primary["repo"], primary["commit"], primary["remote_url"], primary["lockfile_hash"]) == (None,) * 4
    assert head not in json.dumps({k: v for k, v in manifest.items() if k != "env"}), \
        "the checkout's commit is not what ran, and is recorded nowhere as the code that ran"
    assert manifest["ran_on"] == urlsplit(finished.url).hostname
    assert row["history"] == 6, "the results are landed as before"


def test_a_composites_card_runs_params_stay_the_composites_inputs(ws, head, finished, dashboard_client):
    """Falsifies: "recording the backend's record of a run put it in the run's params". A re-run sends a run's params
    back to the composite as its inputs, so the record is the manifest's (``ran_on`` is there for the same reason)
    and the params, and what a re-run would send, are exactly what the run was launched with."""
    from vivarium_workbench.lib import rerun

    run_id, row = _card_run(ws, dashboard_client(ws))
    assert json.loads(row["params_json"]) == {}
    assert rerun.resolve_rerun_target(ws, run_id)["params"] == {}


def test_a_composites_card_run_records_what_the_study_route_records_for_the_same_run(
        ws, head, finished, dashboard_client):
    """Falsifies: "the two routes describe the same compose-api run differently". Landing SIM_ID through the study
    route gives the record in its params; the Composites tab's run of it must carry the same record, and the same
    code identity, in its manifest."""
    c = dashboard_client(ws)
    _, row = _card_run(ws, c)
    study = _land_study(ws, c)

    assert row["manifest"]["compose"] == study["params"]["compose"]
    assert row["manifest"]["code_version"] == study["manifest"]["code_version"]
    assert row["manifest"]["environments"] == study["manifest"]["environments"]


def test_a_backend_that_cannot_be_asked_after_the_run_does_not_fail_it_nor_claim_the_checkout_ran(
        ws, head, finished, dashboard_client):
    """Falsifies: "a transient failure of the lookup that records the backend's record fails or loses a completed
    run", and "without that record, the run's code is the checkout's". The run's results are landed, the run says
    in its log that its record is missing, and it claims no commit (nor image) for the code that ran."""
    finished.respond_error("GET", "/simulations/{simulation_id}", 503, {"detail": "unavailable"})
    run_id, row = _card_run(ws, dashboard_client(ws))

    assert row["history"] == 6 and row["status"] == "completed"
    assert "PROVENANCE NOT RECORDED" in row["log"]
    assert "compose" not in row["manifest"]
    assert row["manifest"]["code_version"] == {"git_sha": None, "package": None, "repo": None, "remote_url": None,
                                               "image": None}


def test_the_study_routes_recorded_provenance_is_unchanged(ws, finished, dashboard_client):
    """Falsifies: "sharing the provenance construction between the routes changed what the study route stores".
    Its params are the backend's record under ``compose``, its manifest names the image as the code that ran, and
    the params are also the manifest's (a study run has no inputs to keep apart). Exact, key for key."""
    study = _land_study(ws, dashboard_client(ws))
    deployment = urlsplit(finished.url).hostname
    record = {"source": deployment, "backend": "compose-api",
              "compose": {"run_id": RUN_ID, "simulation_id": SIM_ID, "experiment_id": "exp-1", "image": IMAGE,
                          "environment_version": f"{PILOT}@{VERSION}", "slurm_job_id": SLURM_JOB}}
    manifest = study["manifest"]
    # step 1c: the row also records where the run went (lib/backends/records.py); the manifest leaves it out
    params = dict(study["params"])
    remote = params.pop("remote")
    assert (remote["kind"], remote["id"], remote["base_url"]) == ("compose-api", RUN_ID, finished.url.rstrip("/"))
    assert params == record
    assert manifest["params"] == record
    assert manifest["code_version"] == {"git_sha": None, "package": None, "repo": None, "remote_url": None,
                                        "image": IMAGE}
    assert manifest["environments"] == [{"role": "primary", "repo": None, "ref": None, "commit": None,
                                         "remote_url": None, "lockfile_hash": None}]
    assert manifest["origin"] == "remote" and manifest["emitter"] == "sqlite" and "compose" not in manifest


@pytest.mark.parametrize("archive", ["results.zip", "results.tar.gz", "compose-x-results.zip"])
def test_a_run_that_was_not_dispatched_by_compose_dispatch_is_left_as_launched(ws, archive):
    """Falsifies: "recording a compose-api run's record touches runs that did not run on compose-api". A viva-v1
    composite run's archive is ``results.zip`` and an sms-api run's ``results.tar.gz``: neither names a compose-api
    run, so the manifest stays exactly as the launch wrote it, and no backend is asked (none is named here, so
    asking would raise)."""
    from vivarium_workbench.lib import composite_runs as cr

    conn = cr.connect(ws / ".pbg" / "composite-runs.db")
    try:
        manifest = {"code_version": {"git_sha": "abc", "image": None}, "environments": [{"role": "primary", "commit": "abc"}]}
        cr.save_metadata(conn, spec_id=COMPOSITE, run_id="r1", params={}, label="x", started_at=0, n_steps=1,
                         manifest=manifest)
        rc.record_provenance(conn, "r1", Path(archive))
        (stored,) = conn.execute("SELECT manifest_json FROM runs_meta WHERE run_id = 'r1'").fetchone()
    finally:
        conn.close()
    assert json.loads(stored) == manifest
