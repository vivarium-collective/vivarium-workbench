"""The workbench dispatching study runs to compose-api (docs/backend-compose-api.md).

Two kinds of evidence, kept apart:

* **Real artifacts** (``tests/_fixtures/compose_api/``, provenance in its README): the export of a real
  viva-pde-particle composite made in that environment's own image, the results its real runner wrote, a real
  production run's results, and the environment's real interface manifest. Address form, the manifest check and
  landing are tested against these, so they check the workbench against what compose-api and its runners produce.
* **Transport** over a real workbench server, against ``VivaV1Stub`` generated from compose-api's OpenAPI
  (``tests/_contracts/compose_api_openapi_0.7.72.json``, the spec shipped in the locked client). These prove the
  requests the workbench sends are ones the contract accepts and that the UI routes wire through. They do NOT
  prove a deployment runs the document: only a real run does (the PR's live run against compose.cam.uchc.edu).
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest
import yaml

from _viva_v1_stub import VivaV1Stub
from vivarium_workbench.lib import remote_run_compose as rc
from vivarium_workbench.lib import remote_run_viva_v1
from vivarium_workbench.lib import server_capabilities as sc
from vivarium_workbench.lib.remote_run_landing import land_composite_results

HERE = Path(__file__).parent
FIX = HERE / "_fixtures" / "compose_api"
SPEC_PATH = HERE / "_contracts" / "compose_api_openapi_0.7.72.json"
FIXTURE_WS = HERE / "_fixtures" / "ws_increase_demo"
COMPOSITE = "pbg_ws_increase_demo.composites.increase-demo"
SIM_ID = 4706
RUN_ID = f"compose-{SIM_ID}"
PILOT = "viva-pde-particle"


def _json(name: str):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def _manifest_addresses() -> list[str]:
    return [c["address"] for c in _json("viva-pde-particle.manifest.json")["components"]]


# --- the contract ----------------------------------------------------------------------------------------------

def test_the_contract_snapshot_is_the_spec_the_locked_client_ships():
    """The drift alarm: bumping compose-api-client without refreshing the snapshot (or the reverse) fails here."""
    import compose_api_client

    shipped = json.loads((Path(compose_api_client.__file__).parent / "openapi.json").read_text(encoding="utf-8"))
    assert json.loads(SPEC_PATH.read_text(encoding="utf-8")) == shipped


def test_every_operation_the_compose_mode_uses_is_in_the_contract():
    ops = {o["operationId"] for item in json.loads(SPEC_PATH.read_text())["paths"].values() for o in item.values()}
    used = sc.COMPOSE_OPERATIONS | {"get-simulation", "cancel-simulation", "get-simulation-trace-chrome"}
    assert used <= ops, sorted(used - ops)


def test_run_ids_reach_their_own_backend():
    """compose-api's ids are integers, which the other routes read as legacy ids; the compose id is namespaced and
    checked first, because viva-v1's own check takes ANY non-digit string."""
    assert rc.is_run_id(RUN_ID) and rc.simulation_id_of(RUN_ID) == SIM_ID
    assert not rc.is_run_id("4706") and not rc.is_run_id("simulation-D55FF7B") and not rc.is_run_id("compose-")
    assert remote_run_viva_v1.is_run_id(RUN_ID), "why the compose check must come first"


# --- real artifacts --------------------------------------------------------------------------------------------

def test_the_real_export_runs_in_its_environment_as_far_as_the_manifest_can_tell():
    doc = _json("particle_diffusion.pbg")
    assert not [a for a in rc._addresses(doc) if a.startswith("local:!")]
    assert rc.manifest_violations(doc, _manifest_addresses(), ["viva_pde_particle"]) == []


def test_the_manifest_check_refuses_what_the_environment_does_not_have():
    doc = _json("particle_diffusion.pbg")
    doc["extra_import"] = {"_type": "step", "address": "local:!viva_pde_particle.steps.transfer.MeshToGridField"}
    doc["unlisted"] = {"_type": "step", "address": "local:viva_pde_particle.steps.transfer.Nonexistent"}
    doc["elsewhere"] = {"_type": "step", "address": "local:numpy.linalg.Solver"}
    problems = rc.manifest_violations(doc, _manifest_addresses(), ["viva_pde_particle"])
    assert len(problems) == 3
    assert any("only local:<module>.<Class>" in p for p in problems)
    assert any("Nonexistent: not in the environment's interface manifest" in p for p in problems)
    assert any("module 'numpy' is not in this environment" in p for p in problems)


def test_the_real_runners_results_land_one_history_row_per_emitted_step(tmp_path):
    db = tmp_path / "runs.db"
    n = land_composite_results(FIX / "results_particle_diffusion.zip", db, "run-1", emitter=rc.STUDY_EMITTER)
    assert n == 6
    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT step, global_time, state FROM history ORDER BY step").fetchall()
    assert [r[1] for r in rows] == pytest.approx([0.0, 0.01, 0.02, 0.03, 0.04, 0.05])
    assert json.loads(rows[0][2])["particle_totals"] == {"A": 200.0}


def test_a_real_run_whose_document_had_no_emitter_is_refused_not_landed_empty(tmp_path):
    with pytest.raises(ValueError, match="exactly one emitter"):
        land_composite_results(FIX / "results_4706_no_emitter.zip", tmp_path / "runs.db", "run-1")
    assert not (tmp_path / "runs.db").exists() or not sqlite3.connect(tmp_path / "runs.db").execute(
        "SELECT name FROM sqlite_master WHERE name = 'history'").fetchall()


def test_the_manifest_check_reads_dict_form_addresses_as_compose_api_does():
    doc = _json("particle_diffusion.pbg")
    listed = rc._class_path(_manifest_addresses()[0])
    doc["as_dict"] = {"_type": "step", "address": {"protocol": "local", "data": listed}}
    doc["other_protocol"] = {"_type": "step", "address": {"protocol": "parallel", "data": "x.Y"}}
    doc["import_dict"] = {"_type": "step", "address": {"protocol": "local", "data": "!viva_pde_particle.x.Y"}}
    doc["no_data"] = {"_type": "step", "address": {"protocol": "local", "data": 3}}
    doc["declaration"] = {"_type": "map", "address": {"_type": "string", "_default": "x"}}
    problems = rc.manifest_violations(doc, _manifest_addresses(), ["viva_pde_particle"])
    assert len(problems) == 3, problems
    assert all("only local:<module>.<Class>" in p for p in problems)
    assert any(p.startswith("parallel:x.Y") for p in problems) and any("!viva_pde_particle" in p for p in problems)


@pytest.mark.parametrize("remote", [
    "git@github.com:Vivarium-Collective/viva-pde-particle.git",
    "https://github.com/vivarium-collective/viva-pde-particle",
    "https://github.com/vivarium-collective/viva-pde-particle.git/",
    "https://user:secret@github.com/vivarium-collective/viva-pde-particle.git",
    "ssh://git@github.com/vivarium-collective/viva-pde-particle.git",
    # how a registry entry pinned by commit links its repository (viva-basic-processes' `source` in the live registry)
    "https://github.com/vivarium-collective/viva-pde-particle/tree/70438826a348cfb3feddbc928e2c9038129ff69e",
])
def test_every_spelling_of_a_git_remote_names_the_same_repository(remote):
    assert rc._canonical_repo(remote) == "github.com/vivarium-collective/viva-pde-particle"


# --- the registry address form, on a real workspace ------------------------------------------------------------

@pytest.fixture
def ws(tmp_path) -> Path:
    dest = tmp_path / "ws"
    shutil.copytree(FIXTURE_WS, dest)
    (dest / ".pbg").mkdir(exist_ok=True)
    sd = dest / "studies" / "demo"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "name": "demo", "schema_version": 3,
        "baseline": [{"name": "core", "composite": COMPOSITE, "params": {}}],
        "readouts": [{"name": "level", "store_path": "stores/level"}],
    }))
    return dest


def test_the_registry_form_names_every_class_by_its_dotted_path_without_the_import_marker(ws, tmp_path):
    from vivarium_workbench.lib.pbg_export import export_composite_pbg

    imported = json.loads(export_composite_pbg(ws, COMPOSITE, tmp_path / "i.pbg").read_text())
    registry = json.loads(export_composite_pbg(ws, COMPOSITE, tmp_path / "r.pbg", address_form="registry").read_text())
    assert rc._addresses(imported) and all(a.startswith("local:!") for a in rc._addresses(imported))
    assert rc._addresses(registry) == [a.replace("local:!", "local:", 1) for a in rc._addresses(imported)]


def test_a_generator_composite_exports_when_nothing_has_imported_its_module(tmp_path):
    """viva-pde-particle's build_core does not import its composites (the fixture's package does, which hid this):
    the export found no spec for a generator id. Run in a fresh interpreter, since the registry is per process."""
    import sys

    dest = tmp_path / "ws"
    shutil.copytree(FIXTURE_WS, dest)
    (dest / "pbg_ws_increase_demo" / "__init__.py").write_text('"""Imports nothing on import."""\n')
    script = ("import sys, json; from pathlib import Path\n"
              "from vivarium_workbench.lib.pbg_export import export_composite_pbg\n"
              "out = export_composite_pbg(Path(sys.argv[1]), 'pbg_ws_increase_demo.composites.hint_test', "
              "Path(sys.argv[2]), address_form='registry')\n"
              "print(json.loads(out.read_text()) == {})\n")
    done = subprocess.run([sys.executable, "-c", script, str(dest), str(tmp_path / "out.pbg")],
                          capture_output=True, text=True, cwd=tmp_path, timeout=300)
    assert done.returncode == 0, done.stderr[-2000:]
    assert done.stdout.strip().splitlines()[-1] == "True"


def test_a_numpy_array_exports_as_the_list_it_is_realized_from_and_an_unknown_value_is_refused(ws, tmp_path):
    import numpy as np

    from vivarium_workbench.lib.pbg_export import export_composite_pbg

    def with_array(doc):
        return {**doc, "field": np.arange(3.0), "count": np.int64(4)}

    doc = json.loads(export_composite_pbg(ws, COMPOSITE, tmp_path / "a.pbg", document_hook=with_array,
                                          address_form="registry").read_text())
    assert doc["field"] == [0.0, 1.0, 2.0] and doc["count"] == 4
    with pytest.raises(TypeError, match="cannot export"):
        export_composite_pbg(ws, COMPOSITE, tmp_path / "b.pbg", document_hook=lambda d: {**d, "x": object()},
                             address_form="registry")
    structured = np.zeros(2, dtype=[("id", "U4"), ("count", "i4")])
    with pytest.raises(TypeError, match="cannot export"):
        export_composite_pbg(ws, COMPOSITE, tmp_path / "c.pbg", document_hook=lambda d: {**d, "s": structured},
                             address_form="registry")


def test_the_import_form_keeps_writing_what_it_always_wrote_for_a_value_json_has_no_type_for(ws, tmp_path):
    """viva-v1 and SMS exports (the import form) wrote ``str`` of such values before compose-api existed; the
    registry form's strictness must not start failing them (a dtype, a set, a datetime64 in an SMS config)."""
    import numpy as np

    from vivarium_workbench.lib.pbg_export import export_composite_pbg

    values = {"dtype": np.dtype("float64"), "when": np.datetime64("2026-10-22"), "set": {1}}
    doc = json.loads(export_composite_pbg(ws, COMPOSITE, tmp_path / "i.pbg",
                                          document_hook=lambda d: {**d, **values}).read_text())
    assert (doc["dtype"], doc["when"], doc["set"]) == ("float64", "2026-10-22", "{1}")


# --- transport, over a real workbench server -------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _no_ambient_logins(tmp_path, monkeypatch):
    home = tmp_path / "no-login-home"
    home.mkdir()
    for k in ("GH_TOKEN", "GITHUB_TOKEN", rc.TOKEN_ENV_VAR):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")
    sc._PROFILE_CACHE.clear()
    yield
    sc._PROFILE_CACHE.clear()


@pytest.fixture
def backend_env(monkeypatch):
    for v in ("VIVARIUM_WORKBENCH_BACKEND_BASE_URL", "VIVARIUM_WORKBENCH_BACKEND_ENVIRONMENT",
              "VIVARIUM_WORKBENCH_REMOTE_PINNED", "VIVARIUM_WORKBENCH_REMOTE_REPO_URL", "VIVA_API_BASE",
              "SMS_API_BASE"):
        monkeypatch.delenv(v, raising=False)
    return monkeypatch


def _hpcrun(status: str) -> dict:
    return {"database_id": 1, "slurmjobid": 4286252, "correlation_id": "c-1", "job_type": "simulation",
            "sim_id": SIM_ID, "simulator_id": 282, "status": status}


@pytest.fixture
def stub(ws):
    s = VivaV1Stub(json.loads(SPEC_PATH.read_text(encoding="utf-8")), openapi_path="/openapi.json")
    s.url = s.start()
    s.respond("GET", "/registry/environments", 200, _json("environments.json"))
    s.respond("GET", "/registry/entries", 200, _json("entries.json"))
    # An environment that lists the fixture workspace's classes, the way compose-api's describe script would.
    from vivarium_workbench.lib.pbg_export import export_composite_pbg

    exported = json.loads(export_composite_pbg(ws, COMPOSITE, ws / ".pbg" / "probe.pbg").read_text())
    manifest = copy.deepcopy(_json("viva-pde-particle.manifest.json"))
    template = manifest["components"][0]
    manifest["components"] = [{**template, "address": a} for a in sorted(set(rc._addresses(exported)))
                              if not a.startswith("local:!process_bigraph.")]
    s.respond("GET", "/registry/environments/{name}/manifest", 200, manifest)
    s.respond("POST", "/simulation/run", 200, {"simulation_database_id": SIM_ID, "simulator_database_id": 282})
    s.respond("GET", "/results/simulation/status", 200, _hpcrun("running"))
    yield s
    s.stop()


def _name(monkeypatch, url: str, environment: str | None = f"name:{PILOT}") -> None:
    monkeypatch.setenv("VIVARIUM_WORKBENCH_BACKEND_BASE_URL", url)
    if environment:
        monkeypatch.setenv("VIVARIUM_WORKBENCH_BACKEND_ENVIRONMENT", environment)


def test_a_named_compose_api_is_detected_and_reported(ws, stub, backend_env, dashboard_client):
    _name(backend_env, stub.url)
    cfg = dashboard_client(ws).get("/api/remote-run-config").json()
    assert cfg["backend"]["dispatch"] == "compose-api"
    assert cfg["backend"]["version"] == "0.7.72"


def test_without_a_named_backend_compose_api_is_never_asked(ws, stub, backend_env, dashboard_client):
    backend_env.setenv("VIVA_API_BASE", stub.url)  # an alias, which never moves a run
    c = dashboard_client(ws)
    assert "backend" not in c.get("/api/remote-run-config").json()
    assert stub.calls_to("run-simulation") == []


def test_a_study_run_submits_the_exported_document_with_its_environment_and_tags(ws, stub, backend_env,
                                                                                dashboard_client):
    entries = _json("entries.json")
    for e in entries:
        if PILOT in (e.get("bundles") or []):
            e["module_roots"] = ["pbg_ws_increase_demo"]
    stub.respond("GET", "/registry/entries", 200, entries)
    _name(backend_env, stub.url)
    c = dashboard_client(ws)

    res = c.post("/api/study-run-baseline", json={"study": "demo", "overrides": {}})
    assert res.status_code == 202, res.text
    assert res.json()["run_id"] == RUN_ID and res.json()["environment"] == PILOT

    (call,) = stub.calls_to("run-simulation")
    assert call.status == 200  # the stub validated the form and query against the contract
    assert call.query["environment"] == [PILOT] and "simulator" not in call.query
    assert call.query["interval_time"] == ["1.0"]
    assert call.query["tags"][:2] == ["workbench", "study:demo"]
    assert call.query["tags"][2].startswith(rc.DISPATCH_TAG_PREFIX) and len(call.query["tags"]) == 3
    doc = json.loads(call.body["uploaded_file"])
    assert all(a.startswith("local:") and not a.startswith("local:!") for a in rc._addresses(doc))
    assert rc._state(doc)[rc.STUDY_EMITTER]["inputs"]["stores"]["level"] == ["stores", "level"]
    assert rc.top_level_emitters(doc) == ["emitter", rc.STUDY_EMITTER], "the composite's own emitter is kept"

    poll = c.get(f"/api/remote-run-poll?simulation_id={RUN_ID}").json()
    assert (poll["phase"], poll["run_id"]) == ("running", RUN_ID)
    assert [x.query["simulation_id"] for x in stub.calls_to("get-simulation-status")] == [[str(SIM_ID)]]


def test_a_class_the_environment_lacks_is_refused_before_anything_is_sent(ws, stub, backend_env, dashboard_client):
    stub.respond("GET", "/registry/environments/{name}/manifest", 200, _json("viva-pde-particle.manifest.json"))
    _name(backend_env, stub.url)
    res = dashboard_client(ws).post("/api/remote-run-submit", json={"study": "demo"})
    assert res.status_code == 409 and res.json()["reason"] == "not-in-environment"
    assert "pbg_ws_increase_demo" in res.json()["error"]
    assert stub.calls_to("run-simulation") == []


def test_a_workspace_no_environment_runs_is_told_how_to_register(ws, stub, backend_env, dashboard_client):
    subprocess.run(["git", "init", "-q", str(ws)], check=True)
    subprocess.run(["git", "-C", str(ws), "remote", "add", "origin", "git@github.com:someone/unregistered.git"],
                   check=True)
    _name(backend_env, stub.url, environment=None)
    res = dashboard_client(ws).post("/api/remote-run-submit", json={"study": "demo"})
    assert res.status_code == 409 and res.json()["reason"] == "not-registered"
    assert "manifest.yaml" in res.json()["error"] and "PREBUILT_SIMULATORS" not in res.json()["error"]
    assert stub.calls_to("run-simulation") == []


def test_the_registry_entry_is_found_from_the_workspaces_git_origin(ws, stub, backend_env):
    subprocess.run(["git", "init", "-q", str(ws)], check=True)
    subprocess.run(["git", "-C", str(ws), "remote", "add", "origin",
                    "git@github.com:vivarium-collective/viva-pde-particle.git"], check=True)
    _name(backend_env, stub.url, environment=None)
    with rc._session() as session:
        env = rc.resolve_environment(session, ws)
    assert (env["name"], env["simulator"]) == (PILOT, PILOT)
    assert env["image"].startswith("ghcr.io/vivarium-collective/viva-pde-particle-compose@sha256:")


@pytest.mark.parametrize("version_field", [{}, {"environment_version": None}], ids=["absent", "null"])
def test_a_finished_run_lands_the_real_runners_results_once(ws, stub, backend_env, dashboard_client, version_field):
    """A run from before environment versions has none (the field absent or null): its image is resolved from the
    simulator name through the environment listing, as before, and no version is looked up."""
    stub.respond("GET", "/results/simulation/status", 200, _hpcrun("completed"))
    # compose-api answers a prebuilt simulator's NAME here when the deployment lists its image
    # (compose_api/api/routers/simulations.py, _simulator_label); the digest is the environment's image.
    detail = {"simulation_id": SIM_ID, "experiment_id": "exp-1", "simulator_id": 282, "container_def_hash": "h",
              "status": "completed", "simulator": PILOT, **version_field}
    image = next(e["image"] for e in _json("environments.json") if e["name"] == PILOT)
    assert "@sha256:" in image
    stub.respond("GET", "/simulations/{simulation_id}", 200, detail)
    stub.respond_bytes("GET", "/results/simulation/results/file", 200,
                       (FIX / "results_particle_diffusion.zip").read_bytes(), "application/octet-stream")
    _name(backend_env, stub.url)
    c = dashboard_client(ws)
    res = c.post("/api/remote-run-land", json={"study": "demo", "simulation_id": RUN_ID})
    assert res.status_code == 200, res.text
    landed = res.json()["run_id"]
    with sqlite3.connect(ws / "studies" / "demo" / "runs.db") as db:
        (params,) = db.execute("SELECT params_json FROM runs_meta WHERE run_id = ?", (landed,)).fetchone()
        assert json.loads(params)["compose"]["image"] == image
        assert json.loads(params)["compose"]["environment_version"] is None
        assert db.execute("SELECT count(*) FROM history WHERE simulation_id = ?", (landed,)).fetchone()[0] == 6
    assert stub.calls_to("get-environment-version") == []
    again = c.post("/api/remote-run-land", json={"study": "demo", "simulation_id": RUN_ID}).json()
    assert again == {"run_id": landed, "already_landed": True}
    assert len(stub.calls_to("get-simulation-results-file")) == 1


def test_a_landed_run_records_the_environment_version_it_ran_in_not_the_current_default(
        ws, stub, backend_env, dashboard_client):
    """Regression: the image was resolved from the environment's CURRENT listing, which names its default version.
    Once an environment has versions that default moves, so a run of version 2 landed after version 3 became the
    default recorded the wrong digest. The run's own ``environment_version`` names the version, and the version names
    its image."""
    listed = next(e["image"] for e in _json("environments.json") if e["name"] == PILOT)
    ran_in = listed.rsplit("@sha256:", 1)[0] + "@sha256:" + "ab" * 32
    assert ran_in != listed, "the version the run used must differ from what the listing names today"
    stub.respond("GET", "/results/simulation/status", 200, _hpcrun("completed"))
    stub.respond("GET", "/simulations/{simulation_id}", 200,
                 {"simulation_id": SIM_ID, "experiment_id": "exp-1", "simulator_id": 282, "container_def_hash": "h",
                  "status": "completed", "simulator": PILOT,
                  "environment_version": {"environment": PILOT, "number": 2}})
    stub.respond("GET", "/registry/environments/{name}/versions/{number}", 200,
                 {"database_id": 7, "environment_id": 3, "environment": PILOT, "number": 2, "state": "ready",
                  "simulator_id": 282, "image": ran_in})
    stub.respond_bytes("GET", "/results/simulation/results/file", 200,
                       (FIX / "results_particle_diffusion.zip").read_bytes(), "application/octet-stream")
    _name(backend_env, stub.url)
    res = dashboard_client(ws).post("/api/remote-run-land", json={"study": "demo", "simulation_id": RUN_ID})
    assert res.status_code == 200, res.text
    (version_call,) = stub.calls_to("get-environment-version")
    assert version_call.path == f"/registry/environments/{PILOT}/versions/2"
    with sqlite3.connect(ws / "studies" / "demo" / "runs.db") as db:
        params, manifest = db.execute("SELECT params_json, manifest_json FROM runs_meta WHERE run_id = ?",
                                      (res.json()["run_id"],)).fetchone()
    compose = json.loads(params)["compose"]
    assert (compose["environment_version"], compose["image"]) == (f"{PILOT}@2", ran_in)
    assert json.loads(manifest)["code_version"]["image"] == ran_in


def _land_in_version(ws, stub, backend_env, dashboard_client, number=2, environment=PILOT):
    """Land a completed run whose own record names ``environment@number``; returns the landing response."""
    stub.respond("GET", "/results/simulation/status", 200, _hpcrun("completed"))
    stub.respond("GET", "/simulations/{simulation_id}", 200,
                 {"simulation_id": SIM_ID, "experiment_id": "exp-1", "simulator_id": 282, "container_def_hash": "h",
                  "status": "completed", "simulator": None,
                  "environment_version": {"environment": environment, "number": number}})
    stub.respond_bytes("GET", "/results/simulation/results/file", 200,
                       (FIX / "results_particle_diffusion.zip").read_bytes(), "application/octet-stream")
    _name(backend_env, stub.url)
    return dashboard_client(ws).post("/api/remote-run-land", json={"study": "demo", "simulation_id": RUN_ID})


def _landed_provenance(ws, run_id):
    with sqlite3.connect(ws / "studies" / "demo" / "runs.db") as db:
        params, manifest = db.execute("SELECT params_json, manifest_json FROM runs_meta WHERE run_id = ?",
                                      (run_id,)).fetchone()
    return json.loads(params)["compose"], json.loads(manifest)["code_version"]


def test_a_composed_version_has_no_image_and_still_lands_with_its_version_recorded(
        ws, stub, backend_env, dashboard_client):
    """A mash-up version is composed from entries, not built from an image: its record carries none."""
    stub.respond("GET", "/registry/environments/{name}/versions/{number}", 200,
                 {"database_id": 9, "environment_id": 4, "environment": "mash-05956395cf", "number": 2,
                  "state": "ready", "simulator_id": 290})
    res = _land_in_version(ws, stub, backend_env, dashboard_client, environment="mash-05956395cf")
    assert res.status_code == 200, res.text
    compose, code_version = _landed_provenance(ws, res.json()["run_id"])
    assert compose["environment_version"] == "mash-05956395cf@2" and compose["image"] is None
    assert code_version["image"] is None


def test_a_version_record_that_is_gone_does_not_block_landing_a_completed_run(
        ws, stub, backend_env, dashboard_client):
    """The version's record can be gone (its environment no longer declared): that is permanent, so the run's results
    are landed with the version label it carries and no image."""
    stub.respond_error("GET", "/registry/environments/{name}/versions/{number}", 404,
                       {"detail": f"no environment {PILOT!r}"})
    res = _land_in_version(ws, stub, backend_env, dashboard_client)
    assert res.status_code == 200, res.text
    compose, code_version = _landed_provenance(ws, res.json()["run_id"])
    assert compose["environment_version"] == f"{PILOT}@2" and compose["image"] is None
    assert code_version["image"] is None


def test_a_version_lookup_that_fails_to_answer_leaves_the_run_unlanded_to_retry(
        ws, stub, backend_env, dashboard_client):
    """Unlike a missing record, a server error is transient: landing is refused, not recorded without its image, so
    asking again lands it with the image."""
    tmpl = "/registry/environments/{name}/versions/{number}"
    stub.respond_error("GET", tmpl, 503, {"detail": "unavailable"})
    res = _land_in_version(ws, stub, backend_env, dashboard_client)
    assert res.status_code == 502 and res.json()["phase"] == "backend_error", res.text
    image = next(e["image"] for e in _json("environments.json") if e["name"] == PILOT)
    stub.respond("GET", tmpl, 200, {"database_id": 7, "environment_id": 3, "environment": PILOT, "number": 2,
                                    "state": "ready", "simulator_id": 282, "image": image})
    again = dashboard_client(ws).post("/api/remote-run-land", json={"study": "demo", "simulation_id": RUN_ID})
    assert again.status_code == 200 and "already_landed" not in again.json(), again.text
    compose, _ = _landed_provenance(ws, again.json()["run_id"])
    assert compose["image"] == image


def test_an_unfinished_run_is_not_landed(ws, stub, backend_env, dashboard_client):
    _name(backend_env, stub.url)
    res = dashboard_client(ws).post("/api/remote-run-land", json={"study": "demo", "simulation_id": RUN_ID})
    assert res.status_code == 409 and "running" in res.json()["error"]
    assert stub.calls_to("get-simulation-results-file") == []


def test_cancel_sends_the_token_and_says_when_one_is_needed(ws, stub, backend_env, dashboard_client):
    _name(backend_env, stub.url)
    stub.respond("DELETE", "/simulations/{simulation_id}", 401, {"detail": "Cancelling a simulation needs a signed-in caller"})
    c = dashboard_client(ws)
    res = c.post("/api/remote-run-cancel", json={"simulation_id": RUN_ID})
    assert res.status_code == 401 and rc.TOKEN_ENV_VAR in res.json()["error"]

    stub.respond("DELETE", "/simulations/{simulation_id}", 202,
                 {"simulation_id": SIM_ID, "withdrawn": False, "slurm_job_id": 4286252})
    backend_env.setenv(rc.TOKEN_ENV_VAR, "token-for-test")
    res = dashboard_client(ws).post("/api/remote-run-cancel", json={"simulation_id": RUN_ID})
    assert res.status_code == 200 and res.json()["slurm_job_id"] == 4286252
    assert [x.path for x in stub.calls_to("cancel-simulation")] == [f"/simulations/{SIM_ID}"] * 2


def _register_fixture_workspace(stub) -> None:
    """The pilot environment's registry entry, made to provide the fixture workspace's module (its classes are in
    the stub's manifest)."""
    entries = _json("entries.json")
    for e in entries:
        if PILOT in (e.get("bundles") or []):
            e["module_roots"] = ["pbg_ws_increase_demo"]
    stub.respond("GET", "/registry/entries", 200, entries)


def _completed(stub) -> None:
    stub.respond("GET", "/results/simulation/status", 200, _hpcrun("completed"))
    stub.respond_bytes("GET", "/results/simulation/results/file", 200,
                       (FIX / "results_particle_diffusion.zip").read_bytes(), "application/octet-stream")


def _summary(simulation_id: int) -> dict:
    return {"simulation_id": simulation_id, "experiment_id": "exp-1", "simulator_id": 282,
            "container_def_hash": "h", "status": "running"}


@pytest.mark.parametrize("key", ["simulation_id", "composite_run_id"])
def test_the_trace_of_a_compose_run_is_served_whichever_id_the_ui_sends(ws, stub, backend_env, dashboard_client,
                                                                       key):
    """The run panel sends ``simulation_id=compose-N`` (perfetto-open.js); both ids reach compose-api's trace."""
    # The shape production serves (checked on simulation 5337): the 0.7.72 contract requires all three keys.
    trace = {"traceEvents": [{"name": "job", "ph": "X", "ts": 0, "dur": 5, "pid": 1, "tid": 1}],
             "displayTimeUnit": "ms", "otherData": {"source": "compose-api", "simulation_id": str(SIM_ID)}}
    stub.respond("GET", "/results/simulation/trace/chrome", 200, trace)
    _name(backend_env, stub.url)
    c = dashboard_client(ws)
    assert c.get("/api/remote-run-trace-support").json()["supported"] is True
    res = c.get(f"/api/remote-run-trace?{key}={RUN_ID}")
    assert res.status_code == 200, res.text
    assert res.json() == trace
    assert [x.query["simulation_id"] for x in stub.calls_to("get-simulation-trace-chrome")] == [[str(SIM_ID)]]


def test_progress_reports_one_job_and_its_terminal_phase(ws, stub, backend_env, dashboard_client):
    _name(backend_env, stub.url)
    c = dashboard_client(ws)
    # study-detail.js sends a non-numeric id as run_id
    running = c.get(f"/api/remote-run-chain-progress?run_id={RUN_ID}").json()
    assert (running["phase"], running["terminal"], running["run_id"]) == ("running", False, RUN_ID)
    stub.respond("GET", "/results/simulation/status", 200, _hpcrun("failed"))
    failed = c.get(f"/api/remote-run-chain-progress?run_id={RUN_ID}").json()
    assert (failed["phase"], failed["terminal"]) == ("failed", True)


def test_a_run_compose_api_does_not_have_is_not_found_and_stops_the_progress_poll(ws, stub, backend_env,
                                                                                dashboard_client):
    """The status route answers 404 both before SLURM has the job and for no such simulation; the client reads
    either as "submitting", so without the simulation lookup an unknown id polled "queued" forever."""
    stub.respond_error("GET", "/results/simulation/status", 404, {"detail": "no job yet"})
    stub.respond_error("GET", "/simulations/{simulation_id}", 404, {"detail": "no simulation 4706"})
    _name(backend_env, stub.url)
    c = dashboard_client(ws)
    res = c.get(f"/api/remote-run-poll?simulation_id={RUN_ID}")
    assert res.status_code == 404 and res.json()["phase"] == "not_found", res.text
    progress = c.get(f"/api/remote-run-chain-progress?run_id={RUN_ID}").json()
    assert progress["phase"] == "not_found" and progress["terminal"] is True
    detail = {"simulation_id": SIM_ID, "experiment_id": "exp-1", "simulator_id": 282, "container_def_hash": "h",
              "status": "submitting", "simulator": PILOT}
    stub.respond("GET", "/simulations/{simulation_id}", 200, detail)
    queued = c.get(f"/api/remote-run-poll?simulation_id={RUN_ID}").json()
    assert (queued["phase"], queued["raw_status"]) == ("queued", "submitting")


def test_an_environments_module_roots_are_every_entry_it_bundles(ws, stub, backend_env):
    entries = _json("entries.json")
    bundling = [e for e in entries if PILOT in (e.get("bundles") or [])]
    extra = {**copy.deepcopy(bundling[0]), "id": "second-entry", "module_roots": ["second_pkg"]}
    stub.respond("GET", "/registry/entries", 200, [*entries, extra])
    _name(backend_env, stub.url)
    with rc._session() as session:
        env = rc.resolve_environment(session, ws)
    assert set(env["module_roots"]) == {*bundling[0]["module_roots"], "second_pkg"}


def test_a_checkout_without_git_is_not_warned_about_its_commit(ws):
    class Entry:
        commit = "0123456789abcdef"

    assert rc.head_matches_entry(ws, Entry()) is None


def test_a_compose_id_with_no_backend_named_is_a_conflict_not_a_crash(ws, backend_env, dashboard_client):
    c = dashboard_client(ws)
    for res in (c.get(f"/api/remote-run-poll?simulation_id={RUN_ID}"),
                c.get(f"/api/remote-run-chain-progress?run_id={RUN_ID}"),
                c.post("/api/remote-run-cancel", json={"simulation_id": RUN_ID}),
                c.get(f"/api/remote-run-trace?simulation_id={RUN_ID}"),
                c.post("/api/remote-run-land", json={"study": "demo", "simulation_id": RUN_ID})):
        assert res.status_code == 409, res.text
        assert res.json()["reason"] == "no-backend"


def test_cancel_sends_the_token_as_a_bearer_and_passes_each_refusal_through(ws, stub, backend_env,
                                                                           dashboard_client):
    _name(backend_env, stub.url)
    backend_env.setenv(rc.TOKEN_ENV_VAR, "token-for-test")
    c = dashboard_client(ws)
    cases = [(409, {"detail": "simulation 4706 already finished"}, 409, "not-cancellable"),
             (403, {"detail": "not your simulation"}, 403, "forbidden"),
             (500, {"detail": "boom"}, 502, "backend error")]
    for upstream, detail, expected, reason in cases:
        stub.respond_error("DELETE", "/simulations/{simulation_id}", upstream, detail)
        res = c.post("/api/remote-run-cancel", json={"simulation_id": RUN_ID})
        assert (res.status_code, res.json()["reason"]) == (expected, reason), res.text
        assert res.json().get("phase") != "unreachable"
    assert {x.headers["Authorization"] for x in stub.calls_to("cancel-simulation")} == {"Bearer token-for-test"}


def test_only_a_network_failure_reads_as_unreachable(ws, stub, backend_env, dashboard_client):
    _name(backend_env, stub.url)
    c = dashboard_client(ws)
    for code in (500, 503, 429):  # 503 and 429 are NetworkErrors in the client, yet compose-api answered
        stub.respond_error("GET", "/results/simulation/status", code, {"detail": "not now"})
        res = c.get(f"/api/remote-run-poll?simulation_id={RUN_ID}")
        assert res.status_code == 502 and res.json()["phase"] == "backend_error", (code, res.text)
        assert res.json()["reachable"] is True
    stub.stop()
    res = c.get(f"/api/remote-run-poll?simulation_id={RUN_ID}")
    assert res.status_code == 502 and res.json()["phase"] == "unreachable" and res.json()["reachable"] is False


def test_an_environment_named_that_compose_api_lacks_is_refused(ws, stub, backend_env, dashboard_client):
    _name(backend_env, stub.url, environment="name:no-such-environment")
    res = dashboard_client(ws).post("/api/remote-run-submit", json={"study": "demo"})
    assert res.status_code == 409 and res.json()["reason"] == "unknown-environment"
    assert PILOT in res.json()["error"]
    assert stub.calls_to("run-simulation") == []


def test_an_environment_without_an_interface_manifest_is_a_conflict(ws, stub, backend_env, dashboard_client):
    _register_fixture_workspace(stub)
    stub.respond("GET", "/registry/environments/{name}/manifest", 404, {"detail": "no manifest"})
    _name(backend_env, stub.url)
    res = dashboard_client(ws).post("/api/remote-run-submit", json={"study": "demo"})
    assert res.status_code == 409 and res.json()["reason"] == "no-manifest"
    assert stub.calls_to("run-simulation") == []


def test_a_composite_with_nothing_to_record_is_refused(ws, monkeypatch):
    """No readouts and not exactly one emitter of its own: no single history to land."""
    from vivarium_workbench.lib import pbg_export

    real = pbg_export.export_composite_pbg

    def two_emitters(ws_root, composite_id, out, **kw):
        path = real(ws_root, composite_id, out, **kw)
        doc = json.loads(path.read_text())
        state = rc._state(doc)
        state["second_emitter"] = copy.deepcopy(state["emitter"])
        path.write_text(json.dumps(doc))
        return path

    assert rc.top_level_emitters(rc.export_document(ws, COMPOSITE, {}, [])) == ["emitter"]
    monkeypatch.setattr(pbg_export, "export_composite_pbg", two_emitters)
    with pytest.raises(rc.ComposeDispatchError) as e:
        rc.export_document(ws, COMPOSITE, {}, [])
    assert (e.value.status, e.value.body["reason"]) == (422, "nothing-to-emit")


def test_more_steps_than_compose_api_runs_are_refused_not_cut_short(ws, stub, backend_env, dashboard_client):
    _register_fixture_workspace(stub)
    _name(backend_env, stub.url)
    res = dashboard_client(ws).post("/api/remote-run-submit", json={"study": "demo", "n_steps": 1001})
    assert res.status_code == 400 and res.json()["reason"] == "bad-steps"
    assert stub.calls_to("run-simulation") == []


def test_a_lost_submission_answer_finds_the_run_by_its_dispatch_tag(ws, stub, backend_env, dashboard_client):
    _register_fixture_workspace(stub)
    stub.respond_error("POST", "/simulation/run", 500, {"detail": "proxy timeout"})
    stub.respond("GET", "/simulations", 200, {"simulations": [_summary(SIM_ID)], "total": 1})
    _name(backend_env, stub.url)
    res = dashboard_client(ws).post("/api/remote-run-submit", json={"study": "demo"})
    assert res.status_code == 202, res.text
    assert res.json()["run_id"] == RUN_ID
    (submitted,) = stub.calls_to("run-simulation")
    (dispatch_tag,) = [t for t in submitted.query["tags"] if t.startswith(rc.DISPATCH_TAG_PREFIX)]
    assert [x.query["tag"] for x in stub.calls_to("list-simulations")] == [[dispatch_tag]]
    assert len(stub.calls_to("run-simulation")) == 1, "never resubmitted"


def test_a_lost_submission_answer_with_no_run_found_says_it_may_exist(ws, stub, backend_env, dashboard_client):
    _register_fixture_workspace(stub)
    stub.respond_error("POST", "/simulation/run", 500, {"detail": "proxy timeout"})
    stub.respond("GET", "/simulations", 200, {"simulations": [], "total": 0})
    _name(backend_env, stub.url)
    res = dashboard_client(ws).post("/api/remote-run-submit", json={"study": "demo"})
    assert res.status_code == 502 and res.json()["may_exist"] is True
    assert res.json()["dispatch_tag"].startswith(rc.DISPATCH_TAG_PREFIX)
    assert res.json()["dispatch_tag"] in res.json()["error"]
    assert len(stub.calls_to("run-simulation")) == 1


@pytest.mark.parametrize("documents", [True, False])
def test_a_viva_v1_backend_is_not_mistaken_for_compose_api(ws, backend_env, dashboard_client, documents):
    """viva-api is FastAPI too, so it serves its own /openapi.json: that must not read as compose-api, whether or
    not it runs documents."""
    from test_backend_dispatch_viva_v1 import CAPS_DOC

    viva = VivaV1Stub(openapi_path="/openapi.json")
    viva.url = viva.start()
    # Without the composites surface (an older viva-api, SMS), backend_profile reads /openapi.json: that read is
    # what this case exercises.
    caps = CAPS_DOC if documents else [c for c in CAPS_DOC if not c.startswith("viva-v1-composites")]
    viva.respond("GET", "/viva/v1/capabilities", 200, {"version": "0.1.9", "capabilities": caps})
    viva.respond("GET", "/viva/v1/health", 200, {"status": "ok", "version": "0.1.9", "services": {"composites": True}})
    try:
        _name(backend_env, viva.url, environment=None)
        backend = dashboard_client(ws).get("/api/remote-run-config").json()["backend"]
        assert backend["dispatch"] == (sc.DISPATCH_DOCUMENT if documents else sc.DISPATCH_LEGACY)
        assert bool([x for x in viva.calls if x.path == "/openapi.json"]) is not documents
    finally:
        viva.stop()


def test_an_openapi_document_that_is_not_json_reads_as_legacy(ws, backend_env, dashboard_client):
    class NotJson(VivaV1Stub):
        def handle(self, method, raw_path, raw_body, content_type=None):
            if raw_path.startswith("/openapi.json"):
                return 200, "application/json", b"<html>a proxy page</html>"
            return super().handle(method, raw_path, raw_body, content_type)

    s = NotJson(json.loads(SPEC_PATH.read_text(encoding="utf-8")))
    s.url = s.start()
    try:
        _name(backend_env, s.url, environment=None)
        res = dashboard_client(ws).get("/api/remote-run-config")
        assert res.status_code == 200 and res.json()["backend"]["dispatch"] == sc.DISPATCH_LEGACY
    finally:
        s.stop()


def test_the_dispatch_preflight_names_the_environment_or_why_there_is_none(ws, stub, backend_env, dashboard_client):
    _name(backend_env, stub.url)
    ready = dashboard_client(ws).get("/api/remote-dispatch-preflight").json()
    assert (ready["ok"], ready["dispatch"], ready["environment"]) == (True, "compose-api", PILOT)
    _name(backend_env, stub.url, environment="name:no-such-environment")
    refused = dashboard_client(ws).get("/api/remote-dispatch-preflight").json()
    assert (refused["ok"], refused["reason"]) == (False, "unknown-environment")


def test_the_composites_card_runs_on_compose_api_end_to_end(ws, stub, backend_env, dashboard_client):
    """The Composites tab's remote run, through the real server and its real detached runner: refused up front
    for more steps than compose-api runs, otherwise dispatched, waited on, and landed as the card's run."""
    import time

    _register_fixture_workspace(stub)
    _completed(stub)
    _name(backend_env, stub.url)
    c = dashboard_client(ws)
    res = c.post("/api/composite-test-run", json={"id": COMPOSITE, "steps": 1001})
    assert res.status_code == 400 and res.json()["reason"] == "bad-steps"
    assert stub.calls_to("run-simulation") == []

    res = c.post("/api/composite-test-run", json={"id": COMPOSITE, "steps": 3, "emit_paths": ["stores/level"]})
    assert res.status_code == 202, res.text
    run_id = res.json()["run_id"]
    request = json.loads((ws / ".pbg" / "runs" / run_id / "request.json").read_text())
    assert request["target"] == "deployment"
    deadline = time.monotonic() + 120
    while (state := c.get(f"/api/composite-run/{run_id}/status").json())["status"] == "running":
        assert time.monotonic() < deadline, state
        time.sleep(0.5)
    assert state["status"] == "completed", (ws / ".pbg" / "runs" / run_id / "run.log").read_text()
    assert state["n_steps"] == 6, "the landed history's steps, not the requested count"
    (call,) = stub.calls_to("run-simulation")
    assert call.query["environment"] == [PILOT] and "simulator" not in call.query
    assert call.query["interval_time"] == ["3.0"]


def test_the_composites_card_runner_runs_on_compose_api_and_lands_the_history(ws, stub, backend_env):
    """The detached runner's own code path, for real: run_remote's compose branch submits, waits, downloads, and
    the runner lands the study emitter's history into the run's store."""
    from vivarium_workbench.lib import composite_runs as cr
    from vivarium_workbench.lib import run_runner

    _register_fixture_workspace(stub)
    _completed(stub)
    _name(backend_env, stub.url)
    run_dir = ws / ".pbg" / "runs" / "card-1"
    run_dir.mkdir(parents=True)
    db = ws / ".pbg" / "composite-runs.db"
    req = run_runner.RunRequest(
        run_id="card-1", spec_id=COMPOSITE, pkg="pbg_ws_increase_demo", workspace=ws, overrides={}, steps=3,
        emit_paths=["stores/level"], db_file=str(db), log_path=str(run_dir / "run.log"), target="deployment")
    assert run_runner._execute_remote(req, run_dir) == 0, (run_dir / "run.log").read_text()

    (call,) = stub.calls_to("run-simulation")
    assert call.query["environment"] == [PILOT] and "simulator" not in call.query
    assert call.query["interval_time"] == ["3.0"]
    doc = json.loads(call.body["uploaded_file"])
    assert rc._state(doc)[rc.STUDY_EMITTER]["inputs"]["stores"]["level"] == ["stores", "level"]
    conn = cr.connect(db)
    try:
        assert conn.execute("SELECT count(*) FROM history WHERE simulation_id = 'card-1'").fetchone()[0] == 6
    finally:
        conn.close()


# --- an entry no prebuilt environment bundles: compose-api chooses or builds the environment ------------------------
#
# The registry below is production's (0.7.76, 2026-10-30; tests/_fixtures/compose_api/README.md): `viva-basic-processes`
# is a real entry at level tested, pinned by commit (its `source` links the commit), in no environment. The fixture
# workspace stands in for its repository only through the git origin that matches it to the entry: what is under test
# is what the workbench sends and how it reads the answers, which the stub validates against the contract. That
# compose-api then picks or builds the environment is proven by a real run only.

BASIC = "viva-basic-processes"
BASIC_REMOTE = "git@github.com:vivarium-collective/viva-basic-processes.git"
PILOT_REMOTE = "git@github.com:vivarium-collective/viva-pde-particle.git"


def _live_registry(stub) -> None:
    stub.respond("GET", "/registry/entries", 200, _json("entries_0.7.76.json"))
    stub.respond("GET", "/registry/environments", 200, _json("environments_0.7.76.json"))


def _with_origin(ws: Path, remote: str) -> None:
    subprocess.run(["git", "init", "-q", str(ws)], check=True)
    subprocess.run(["git", "-C", str(ws), "remote", "remove", "origin"], check=False, capture_output=True)
    subprocess.run(["git", "-C", str(ws), "remote", "add", "origin", remote], check=True)


def test_a_commit_pinned_registry_source_names_the_same_repository_as_the_remote():
    entry = next(e for e in _json("entries_0.7.76.json") if e["id"] == BASIC)
    assert "/tree/" in entry["source"], "the real entry links its commit; if this changed, so did the shape under test"
    assert rc._canonical_repo(entry["source"]) == rc._canonical_repo(BASIC_REMOTE)
    assert rc._canonical_repo(BASIC_REMOTE) == "github.com/vivarium-collective/viva-basic-processes"


def test_an_entry_no_prebuilt_environment_bundles_is_run_in_an_environment_compose_api_chooses(ws, stub, backend_env):
    _live_registry(stub)
    _with_origin(ws, BASIC_REMOTE)
    _name(backend_env, stub.url, environment=None)
    with rc._session() as session:
        env = rc.resolve_environment(session, ws)
    assert (env["name"], env["simulator"], env["image"]) == (None, None, None)
    assert (env["entry"].id, env["module_roots"]) == (BASIC, ["viva_basic_processes"])


def test_the_prebuilt_environment_still_wins_when_the_entry_has_one(ws, stub, backend_env):
    """The path viva-pde-particle runs on today is unchanged by the inferred one, on production's current registry."""
    _live_registry(stub)
    _with_origin(ws, PILOT_REMOTE)
    _name(backend_env, stub.url, environment=None)
    with rc._session() as session:
        env = rc.resolve_environment(session, ws)
    assert (env["name"], env["simulator"]) == (PILOT, PILOT)
    assert env["image"].startswith("ghcr.io/vivarium-collective/viva-pde-particle-compose@sha256:")
    # every entry the environment bundles, as before: since 0.7.76 that includes the engine's emitters entry
    assert sorted(env["module_roots"]) == ["process_bigraph.emitter", "viva_pde_particle"]


def test_a_repository_no_entry_has_is_still_told_to_add_one(ws, stub, backend_env):
    _live_registry(stub)
    _with_origin(ws, "git@github.com:someone/unregistered.git")
    _name(backend_env, stub.url, environment=None)
    with rc._session() as session, pytest.raises(rc.ComposeDispatchError) as refused:
        rc.resolve_environment(session, ws)
    assert refused.value.status == 409 and refused.value.body["reason"] == "not-registered"
    assert "manifest.yaml" in refused.value.body["error"] and "environments.yaml" not in refused.value.body["error"]


def test_a_study_run_with_no_prebuilt_environment_names_none_and_reads_no_manifest(ws, stub, backend_env,
                                                                                    dashboard_client):
    _live_registry(stub)
    _with_origin(ws, BASIC_REMOTE)
    _name(backend_env, stub.url, environment=None)
    res = dashboard_client(ws).post("/api/study-run-baseline", json={"study": "demo", "overrides": {}})
    assert res.status_code == 202, res.text
    assert res.json()["run_id"] == RUN_ID and res.json()["environment"] is None

    (call,) = stub.calls_to("run-simulation")
    assert call.status == 200  # the stub validated the form and query against the contract
    assert "environment" not in call.query and "simulator" not in call.query
    assert call.query["tags"][:2] == ["workbench", "study:demo"]
    assert call.query["tags"][2].startswith(rc.DISPATCH_TAG_PREFIX) and len(call.query["tags"]) == 3
    doc = json.loads(call.body["uploaded_file"])
    assert all(a.startswith("local:") and not a.startswith("local:!") for a in rc._addresses(doc))
    assert stub.calls_to("get-environment-manifest") == [], "an environment that may not exist yet has no manifest"


def test_with_no_manifest_only_the_address_form_is_checked():
    doc = {"a": {"address": "local:module_nobody_registered.Anything"}, "b": {"address": "local:!x.Y"},
           "c": {"address": {"protocol": "parallel", "data": "x.Y"}}}
    problems = rc.manifest_violations(doc, None, [])
    assert len(problems) == 2 and all("only local:<module>.<Class>" in p for p in problems), problems
    assert not any("module_nobody_registered" in p for p in problems), "the service answers for classes it may not know"
    # an environment with a manifest is checked as before: the same document has one more thing wrong with it
    assert len(rc.manifest_violations(doc, [], [])) == 3


def test_the_preflight_for_an_inferred_environment_says_what_a_first_run_needs(ws, stub, backend_env,
                                                                              dashboard_client):
    _live_registry(stub)
    _with_origin(ws, BASIC_REMOTE)
    _name(backend_env, stub.url, environment=None)
    anonymous = dashboard_client(ws).get("/api/remote-dispatch-preflight").json()
    assert (anonymous["ok"], anonymous["reason"], anonymous["environment"]) == (True, "inferred", None)
    assert (anonymous["registry_entry"], anonymous["registry_level"], anonymous["signed_in"]) == (BASIC, "tested", False)
    assert rc.TOKEN_ENV_VAR in anonymous["message"]

    backend_env.setenv(rc.TOKEN_ENV_VAR, "token-for-test")
    signed_in = dashboard_client(ws).get("/api/remote-dispatch-preflight").json()
    assert signed_in["signed_in"] is True and rc.TOKEN_ENV_VAR not in signed_in["message"]


def test_a_first_run_that_needs_a_signed_in_caller_says_so_and_is_not_retried(ws, stub, backend_env, dashboard_client):
    _live_registry(stub)
    _with_origin(ws, BASIC_REMOTE)
    _name(backend_env, stub.url, environment=None)
    stub.respond_error("POST", "/simulation/run", 401, {"detail": "Creating an environment needs a signed-in caller"})
    res = dashboard_client(ws).post("/api/study-run-baseline", json={"study": "demo", "overrides": {}})
    assert res.status_code == 401 and rc.TOKEN_ENV_VAR in res.json()["error"], res.text
    assert len(stub.calls_to("run-simulation")) == 1, "a refused submission is never sent twice"


def test_compose_apis_refusal_is_shown_in_its_own_words_with_each_violation(ws, stub, backend_env, dashboard_client):
    _live_registry(stub)
    _with_origin(ws, BASIC_REMOTE)
    _name(backend_env, stub.url, environment=None)
    detail = {"message": "the composite names processes this service will not run",
              "violations": [{"document": "demo.pbg", "address": "local:pbg_ws_increase_demo.Increase",
                              "reason": "module `pbg_ws_increase_demo` is not provided by any registered simulator",
                              "always_rejected": False}]}
    stub.respond("POST", "/simulation/run", 400, {"detail": detail})
    res = dashboard_client(ws).post("/api/study-run-baseline", json={"study": "demo", "overrides": {}})
    error = res.json()["error"]
    assert res.status_code == 409 and res.json()["reason"] == "refused", res.text
    assert "the composite names processes this service will not run" in error
    assert "local:pbg_ws_increase_demo.Increase: module `pbg_ws_increase_demo` is not provided" in error
    assert "{" not in error, "sentences, not a dict"


def test_a_lost_answer_in_an_inferred_environment_is_found_by_its_tag_and_never_resubmitted(ws, stub, backend_env,
                                                                                          dashboard_client):
    """A second submission could start a second environment build, so this matters more here than for an image."""
    _live_registry(stub)
    _with_origin(ws, BASIC_REMOTE)
    _name(backend_env, stub.url, environment=None)
    stub.respond_error("POST", "/simulation/run", 500, {"detail": "proxy timeout"})
    stub.respond("GET", "/simulations", 200, {"simulations": [_summary(SIM_ID)], "total": 1})
    res = dashboard_client(ws).post("/api/remote-run-submit", json={"study": "demo"})
    assert res.status_code == 202 and res.json()["run_id"] == RUN_ID, res.text
    assert len(stub.calls_to("run-simulation")) == 1


LIVE_COMPOSE = os.environ.get("VIVARIUM_WORKBENCH_LIVE_COMPOSE", "").strip()


@pytest.mark.skipif(not LIVE_COMPOSE, reason="opt-in: VIVARIUM_WORKBENCH_LIVE_COMPOSE=<base URL> reads a live registry")
def test_the_live_registry_resolves_the_pilot_to_its_image_and_a_bundle_less_entry_to_none(ws, backend_env):
    """Read-only (two GETs): the stub is not the only oracle. It fails when the registry moves (viva-basic-processes
    gains an environment, the pilot loses its image), which is the signal to refresh the 0.7.76 fixtures."""
    backend_env.setenv("VIVARIUM_WORKBENCH_BACKEND_BASE_URL", LIVE_COMPOSE)
    backend_env.delenv("VIVARIUM_WORKBENCH_BACKEND_ENVIRONMENT", raising=False)
    with rc._session() as session:
        _with_origin(ws, PILOT_REMOTE)
        pilot = rc.resolve_environment(session, ws)
        _with_origin(ws, BASIC_REMOTE)
        basic = rc.resolve_environment(session, ws)
    assert pilot["name"] == PILOT and pilot["image"].startswith("ghcr.io/")
    assert basic["name"] is None and basic["entry"].id == BASIC


# --- what the adversarial review found ---------------------------------------------------------------------------------

GIT_QUIET = ["git", "-c", "user.name=t", "-c", "user.email=t@example.org", "-c", "commit.gpgsign=false",
             "-c", "core.hooksPath=/dev/null"]


@pytest.mark.parametrize("remote, expected", [
    ("git@github.com:tree/r.git", "github.com/tree/r"),  # an owner named tree
    ("https://github.com/o/tree/", "github.com/o/tree"),  # a repository named tree
    ("https://github.com/o/tree/tree/9a1a624dc925cf700c3f89a8cc008a8fa4b4e5d4", "github.com/o/tree"),
    ("https://github.com/o/r/tree/feature/x", "github.com/o/r"),  # a branch name with a slash
])
def test_a_name_that_is_tree_is_a_name_and_not_a_link_suffix(remote, expected):
    assert rc._canonical_repo(remote) == expected


class _Answered(Exception):
    """What the client raises for a refusal: its message names the operation and the status; the body is on detail."""

    def __init__(self, detail):
        super().__init__("the API answered POST /simulation/run with HTTP 409")
        self.detail = detail


@pytest.mark.parametrize("detail, expected", [
    ({"message": "will not run", "violations": [{"address": "local:a.B", "reason": "unknown"},
                                                 {"address": "local:c.D", "reason": "level listed"}]},
     "will not run: local:a.B: unknown; local:c.D: level listed"),
    ({"message": "will not run"}, "will not run"),
    ({"message": "will not run", "violations": []}, "will not run"),
    ({"message": "will not run", "violations": ["local:a.B refused", None, ""]}, "will not run: local:a.B refused"),
    ({"message": "will not run", "violations": "one sentence"}, "will not run: one sentence"),
    ({}, "refused"),
    ("pixi could not solve the environment: a conflicts with b", "pixi could not solve the environment: a conflicts with b"),
    (None, "the API answered POST /simulation/run with HTTP 409"),
])
def test_a_refusal_is_put_in_the_services_words_whatever_shape_it_arrives_in(detail, expected):
    assert rc._refusal_text(_Answered(detail)) == expected


def test_a_conflict_the_service_explains_is_shown_with_its_explanation(ws, stub, backend_env, dashboard_client):
    """A lock pixi cannot solve, or an environment that already failed for this set of entries, is a 409 whose words
    are on `detail`; the client's own message says only "HTTP 409"."""
    _live_registry(stub)
    _with_origin(ws, BASIC_REMOTE)
    _name(backend_env, stub.url, environment=None)
    stub.respond_error("POST", "/simulation/run", 409, {"detail": "pixi could not solve the environment: a==1 conflicts with b==2"})
    res = dashboard_client(ws).post("/api/study-run-baseline", json={"study": "demo", "overrides": {}})
    assert res.status_code == 409 and res.json()["reason"] == "refused", res.text
    assert "pixi could not solve the environment: a==1 conflicts with b==2" in res.json()["error"]
    assert len(stub.calls_to("run-simulation")) == 1


def test_a_busy_service_reads_as_rate_limited_and_the_run_is_sent_once(ws, stub, backend_env, dashboard_client):
    _live_registry(stub)
    _with_origin(ws, BASIC_REMOTE)
    _name(backend_env, stub.url, environment=None)
    stub.respond_error("POST", "/simulation/run", 429, {"detail": "two environments are already being built"})
    res = dashboard_client(ws).post("/api/study-run-baseline", json={"study": "demo", "overrides": {}})
    assert res.status_code == 502 and res.json()["reason"] == "rate limited", res.text
    assert len(stub.calls_to("run-simulation")) == 1, "a busy builder is not asked again"


def test_the_composites_card_runner_shows_a_conflict_with_its_explanation(ws, stub, backend_env):
    _live_registry(stub)
    _with_origin(ws, BASIC_REMOTE)
    _name(backend_env, stub.url, environment=None)
    stub.respond_error("POST", "/simulation/run", 409,
                       {"detail": "environment mash-0a364f43c1 failed to build; an administrator can make a new version"})
    with pytest.raises(RuntimeError) as refused:
        rc.run_composite(ws, COMPOSITE, dest=ws / ".pbg" / "out", n_steps=1, overrides={}, emit_paths=["stores/level"],
                         poll_interval=0.01, poll_timeout=5)
    assert "compose-api refused the run" in str(refused.value) and "failed to build" in str(refused.value)


def test_the_401_names_the_token_without_the_clients_own_cli_advice(ws, stub, backend_env, dashboard_client):
    _live_registry(stub)
    _with_origin(ws, BASIC_REMOTE)
    _name(backend_env, stub.url, environment=None)
    stub.respond_error("POST", "/simulation/run", 401, {"detail": "Creating an environment needs a signed-in caller"})
    anonymous = dashboard_client(ws).post("/api/study-run-baseline", json={"study": "demo", "overrides": {}})
    assert anonymous.status_code == 401
    assert anonymous.json()["error"] == f"compose-api needs a signed-in caller: set {rc.TOKEN_ENV_VAR}"

    backend_env.setenv(rc.TOKEN_ENV_VAR, "token-for-test")
    rejected = dashboard_client(ws).post("/api/study-run-baseline", json={"study": "demo", "overrides": {}})
    assert rejected.status_code == 401 and rejected.json()["error"].startswith(f"compose-api rejected the token in {rc.TOKEN_ENV_VAR}")


def _commit(ws: Path) -> None:
    """A commit no registry entry pins, so the checkout's HEAD differs from every pin."""
    subprocess.run([*GIT_QUIET, "-C", str(ws), "commit", "-q", "--allow-empty", "-m", "head"], check=True)


def test_a_head_that_is_not_the_pin_is_warned_about_for_a_prebuilt_image_exactly_as_before(ws, stub, backend_env,
                                                                                        dashboard_client):
    entries = _json("entries_0.7.76.json")
    pilot = next(e for e in entries if e["id"] == PILOT)
    pilot["module_roots"] = ["pbg_ws_increase_demo"]  # as test_a_study_run_submits_... does, so the roots check passes
    stub.respond("GET", "/registry/entries", 200, entries)
    stub.respond("GET", "/registry/environments", 200, _json("environments_0.7.76.json"))
    _with_origin(ws, PILOT_REMOTE)
    _commit(ws)
    _name(backend_env, stub.url, environment=None)
    res = dashboard_client(ws).post("/api/study-run-baseline", json={"study": "demo", "overrides": {}})
    assert res.status_code == 202, res.text
    image = next(e for e in _json("environments_0.7.76.json") if e["name"] == PILOT)["image"]
    assert res.json()["warning"] == (f"this checkout's HEAD is not commit {pilot['commit']} that compose-api's registry pins "
                                     f"for {PILOT}: the run uses the environment's image ({image}), not this checkout")


def test_a_head_that_is_not_the_pin_is_warned_about_for_an_inferred_environment_too(ws, stub, backend_env,
                                                                                   dashboard_client):
    _live_registry(stub)
    _with_origin(ws, BASIC_REMOTE)
    _commit(ws)
    _name(backend_env, stub.url, environment=None)
    res = dashboard_client(ws).post("/api/study-run-baseline", json={"study": "demo", "overrides": {}})
    assert res.status_code == 202, res.text
    pin = next(e for e in _json("entries_0.7.76.json") if e["id"] == BASIC)["commit"]
    assert res.json()["warning"] == (f"this checkout's HEAD is not commit {pin} that compose-api's registry pins for {BASIC}: "
                                     "the run uses the environment compose-api has for that commit, not this checkout")
