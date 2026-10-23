"""The workbench dispatching study runs to compose-api (docs/backend-compose-api.md).

Two kinds of evidence, kept apart:

* **Real artifacts** (``tests/_fixtures/compose_api/``, provenance in its README): the export of a real
  viva-pde-particle composite made in that environment's own image, the results its real runner wrote, a real
  production run's results, and the environment's real interface manifest. Address form, the manifest check and
  landing are tested against these, so they check the workbench against what compose-api and its runners produce.
* **Transport** over a real workbench server, against ``VivaV1Stub`` generated from compose-api's OpenAPI
  (``tests/_contracts/compose_api_openapi_0.7.35.json``, the spec shipped in the locked client). These prove the
  requests the workbench sends are ones the contract accepts and that the UI routes wire through. They do NOT
  prove a deployment runs the document: only a real run does (the PR's live run against compose.cam.uchc.edu).
"""
from __future__ import annotations

import copy
import json
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
SPEC_PATH = HERE / "_contracts" / "compose_api_openapi_0.7.35.json"
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
    assert cfg["backend"]["version"] == "0.7.35"


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
    assert call.query["simulator"] == [PILOT] and call.query["interval_time"] == ["1.0"]
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
    assert "PREBUILT_SIMULATORS" in res.json()["error"]
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


def test_a_finished_run_lands_the_real_runners_results_once(ws, stub, backend_env, dashboard_client):
    stub.respond("GET", "/results/simulation/status", 200, _hpcrun("completed"))
    # compose-api answers a prebuilt simulator's NAME here when the deployment lists its image
    # (compose_api/api/routers/simulations.py, _simulator_label); the digest is the environment's image.
    detail = {"simulation_id": SIM_ID, "experiment_id": "exp-1", "simulator_id": 282, "container_def_hash": "h",
              "status": "completed", "simulator": PILOT}
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
        assert db.execute("SELECT count(*) FROM history WHERE simulation_id = ?", (landed,)).fetchone()[0] == 6
    again = c.post("/api/remote-run-land", json={"study": "demo", "simulation_id": RUN_ID}).json()
    assert again == {"run_id": landed, "already_landed": True}
    assert len(stub.calls_to("get-simulation-results-file")) == 1


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
    trace = {"traceEvents": [{"name": "job", "ph": "X", "ts": 0, "dur": 5, "pid": 1, "tid": 1}]}
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
    assert call.query["simulator"] == [PILOT] and call.query["interval_time"] == ["3.0"]


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
    assert call.query["simulator"] == [PILOT] and call.query["interval_time"] == ["3.0"]
    doc = json.loads(call.body["uploaded_file"])
    assert rc._state(doc)[rc.STUDY_EMITTER]["inputs"]["stores"]["level"] == ["stores", "level"]
    conn = cr.connect(db)
    try:
        assert conn.execute("SELECT count(*) FROM history WHERE simulation_id = 'card-1'").fetchone()[0] == 6
    finally:
        conn.close()
