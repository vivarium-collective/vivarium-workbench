"""The workbench dispatching study runs to compose-api (docs/backend-compose-api.md).

Two kinds of evidence, kept apart:

* **Real artifacts** (``tests/_fixtures/compose_api/``, provenance in its README): the export of a real
  viva-pde-particle composite made in that environment's own image, the results its real runner wrote, a real
  production run's results, and the environment's real interface manifest. Address form, the manifest check and
  landing are tested against these, so they check the workbench against what compose-api and its runners produce.
* **Transport** over a real workbench server, against ``VivaV1Stub`` generated from compose-api's OpenAPI
  (``tests/_contracts/compose_api_openapi_0.7.100.json``, the spec shipped in the locked client). These prove the
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
import sys
from pathlib import Path

import pytest
import yaml

from _viva_v1_stub import VivaV1Stub
from vivarium_workbench.lib import remote_run_compose as rc
from vivarium_workbench.lib import server_capabilities as sc
from vivarium_workbench.lib.remote_run_landing import land_composite_results

HERE = Path(__file__).parent
FIX = HERE / "_fixtures" / "compose_api"
SPEC_PATH = HERE / "_contracts" / "compose_api_openapi_0.7.100.json"
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


@pytest.mark.plugin
def test_viva_cores_id_check_also_takes_a_compose_id():
    """Why the compose check must come first (split from the test above, which runs core-only: docs/backend-adapters.md
    step P3, K2a): viva-core's check, the SMS plugin's, takes ANY non-digit string."""
    from vivarium_workbench_sms import remote_run_viva_v1

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


def test_an_empty_workspace_yaml_does_not_break_exporting_a_composite_whose_spec_is_found(ws, tmp_path):
    """workspace.yaml is read before the spec lookup (the generator fallback needs the package name), so every export
    reads it: an empty file loads as None and must read as 'no settings', not fail a composite that needs nothing
    from it. Run in a fresh interpreter, since the spec registry is per process."""
    import sys

    (ws / "workspace.yaml").write_text("", encoding="utf-8")
    script = ("import sys; from pathlib import Path\n"
              "from process_bigraph import allocate_core\n"
              "from process_bigraph.composite_spec import get\n"
              "from vivarium_workbench.lib.pbg_export import export_composite_pbg\n"
              "ws = Path(sys.argv[1]); cid = 'pbg_ws_increase_demo.composites.hint_test'\n"
              "sys.path.insert(0, str(ws))\n"
              "import pbg_ws_increase_demo  # registers its composites\n"
              "assert get(cid) is not None, 'precondition: the spec is found without the workspace.yaml fallback'\n"
              "export_composite_pbg(ws, cid, Path(sys.argv[2]), core=allocate_core())\n"
              "print('exported')\n")
    done = subprocess.run([sys.executable, "-c", script, str(ws), str(tmp_path / "e.pbg")],
                          capture_output=True, text=True, cwd=tmp_path, timeout=300)
    assert done.returncode == 0, done.stderr[-2000:]
    assert done.stdout.strip().splitlines()[-1] == "exported"


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
    # The compose-api CLI's profile settings and config/state directories too (they derive from HOME once the XDG
    # overrides are gone): a developer's own profile or login must not decide what these tests send.
    for k in ("GH_TOKEN", "GITHUB_TOKEN", rc.TOKEN_ENV_VAR, "XDG_CONFIG_HOME", "XDG_STATE_HOME",
              *[k for k in os.environ if k.startswith("COMPOSE_API_CLI_")]):
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
    assert cfg["backend"]["version"] == "0.7.100"


# The alias variant needs a provider to claim the alias: with none installed, serve refuses it (step P4b2).
@pytest.mark.parametrize("alias", [pytest.param(True, marks=pytest.mark.plugin, id="alias"),
                                   pytest.param(False, id="nothing")])
def test_without_a_named_backend_compose_api_is_never_asked(ws, stub, backend_env, dashboard_client, alias):
    if alias:
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


def test_a_workspace_no_entry_has_is_run_in_an_environment_compose_api_infers(ws, stub, backend_env, dashboard_client):
    """A workspace that composes other entries' classes (viva-multiscale-nfkb) needs no entry of its own: the run
    names no environment, reads no manifest, and compose-api admits or refuses it by its classes."""
    _live_registry(stub)
    _with_origin(ws, "git@github.com:someone/unregistered.git")
    _name(backend_env, stub.url, environment=None)
    res = dashboard_client(ws).post("/api/study-run-baseline", json={"study": "demo", "overrides": {}})
    assert res.status_code == 202, res.text
    assert res.json()["environment"] is None and "warning" not in res.json(), "no entry, so no pin to compare HEAD to"
    (call,) = stub.calls_to("run-simulation")
    assert "environment" not in call.query and "simulator" not in call.query
    assert stub.calls_to("get-environment-manifest") == []


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
    # The shape production serves (checked on simulation 5337): the 0.7.100 contract requires all three keys.
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


LONG_RUN_REFUSAL = "Invalid interval time, it has to be between 0 and 1000; up to 86400 with the long-runs role"


def test_how_long_a_run_may_be_is_compose_apis_to_say(ws, stub, backend_env, dashboard_client):
    """Falsifies: the workbench caps a compose-api run's length itself (it once refused anything past 1,000 steps,
    so no one, not even a holder of compose-api's long-runs role, could start a multi-generation run from it), or
    hides compose-api's own refusal. A 7,700-step run reaches compose-api as asked; compose-api's 400, which names the
    limit and the role, reaches the person in compose-api's words. A non-positive count is still refused here."""
    _register_fixture_workspace(stub)
    _name(backend_env, stub.url)
    c = dashboard_client(ws)

    res = c.post("/api/remote-run-submit", json={"study": "demo", "n_steps": -5})
    assert res.status_code == 400 and res.json()["reason"] == "bad-steps"
    assert stub.calls_to("run-simulation") == []

    res = c.post("/api/remote-run-submit", json={"study": "demo", "n_steps": 7700})
    assert res.status_code < 400, res.text
    (call,) = stub.calls_to("run-simulation")
    assert call.query["interval_time"] == ["7700.0"]

    stub.respond_error("POST", "/simulation/run", 400, {"detail": LONG_RUN_REFUSAL})
    res = c.post("/api/remote-run-submit", json={"study": "demo", "n_steps": 7700})
    assert res.status_code == 409, res.text
    assert res.json()["error"] == f"compose-api refused the run: {LONG_RUN_REFUSAL}"


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


@pytest.mark.parametrize("documents", [pytest.param(True, marks=pytest.mark.plugin), False])
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


@pytest.mark.parametrize("paths", [["/simulation/run"], "paths"], ids=["array", "string"])
def test_an_openapi_document_whose_paths_is_not_an_object_reads_as_legacy(ws, backend_env, dashboard_client, paths):
    """``backend_profile`` never raises. JSON that is not an OpenAPI document (here, ``paths`` that is truthy and not an
    object) is not compose-api's either, and must not fail the routes that ask which backend they are talking to."""
    class OddPaths(VivaV1Stub):
        def handle(self, method, raw_path, raw_body, content_type=None):
            if raw_path.startswith("/openapi.json"):
                return 200, "application/json", json.dumps({"info": {"version": "9.9.9"}, "paths": paths}).encode()
            return super().handle(method, raw_path, raw_body, content_type)

    s = OddPaths(json.loads(SPEC_PATH.read_text(encoding="utf-8")))
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
    for a step count that is not positive, otherwise dispatched, waited on, and landed as the card's run."""
    import time

    _register_fixture_workspace(stub)
    _completed(stub)
    _name(backend_env, stub.url)
    c = dashboard_client(ws)
    res = c.post("/api/composite-test-run", json={"id": COMPOSITE, "steps": -5})
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


def test_a_repository_no_entry_has_resolves_to_an_inferred_environment(ws, stub, backend_env):
    _live_registry(stub)
    _with_origin(ws, "git@github.com:someone/unregistered.git")
    _name(backend_env, stub.url, environment=None)
    with rc._session() as session:
        env = rc.resolve_environment(session, ws)
    assert env == {"name": None, "simulator": None, "image": None, "entry": None, "module_roots": [], "stale": False}


def test_a_workspace_with_no_git_origin_is_inferred_too(ws, stub, backend_env):
    """Inference needs no entry, so it needs no origin to match one: a local-only workspace runs the same way."""
    _live_registry(stub)
    _name(backend_env, stub.url, environment=None)
    with rc._session() as session:
        env = rc.resolve_environment(session, ws)
    assert (env["name"], env["entry"]) == (None, None)


def test_a_class_compose_api_refuses_is_shown_for_a_workspace_no_entry_has(ws, stub, backend_env, dashboard_client):
    """The claim this path rests on: with no entry, compose-api is the one that decides each class, and its refusal
    reaches the user in its own words."""
    _live_registry(stub)
    _with_origin(ws, "git@github.com:someone/unregistered.git")
    _name(backend_env, stub.url, environment=None)
    detail = {"message": "the composite names processes this service will not run",
              "violations": [{"document": "demo.pbg", "address": "local:pbg_ws_increase_demo.Increase",
                              "reason": "module `pbg_ws_increase_demo` is not provided by any registered simulator",
                              "always_rejected": False}]}
    stub.respond("POST", "/simulation/run", 400, {"detail": detail})
    res = dashboard_client(ws).post("/api/study-run-baseline", json={"study": "demo", "overrides": {}})
    assert res.status_code == 409 and res.json()["reason"] == "refused", res.text
    assert "module `pbg_ws_increase_demo` is not provided by any registered simulator" in res.json()["error"]
    assert len(stub.calls_to("run-simulation")) == 1


def test_the_preflight_for_a_workspace_no_entry_has_says_it_needs_none(ws, stub, backend_env, dashboard_client):
    _live_registry(stub)
    _with_origin(ws, "git@github.com:someone/unregistered.git")
    _name(backend_env, stub.url, environment=None)
    pre = dashboard_client(ws).get("/api/remote-dispatch-preflight").json()
    assert (pre["ok"], pre["reason"], pre["environment"], pre["registry_entry"], pre["registry_level"]) == (
        True, "inferred", None, None, None)
    assert "none is needed" in pre["message"] and rc.TOKEN_ENV_VAR in pre["message"]


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
    # the workbench's own advice and none of the client's words, which would speak of credentials never sent (no
    # login can be: the CLI's default profile, production, is not this backend); the advice: the signing-in tests below
    error = anonymous.json()["error"]
    assert error.startswith("compose-api needs a signed-in caller: ") and rc.TOKEN_ENV_VAR in error, error
    assert "credentials" not in error, error

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


# --- files the composite names travel with it --------------------------------------------------------------------


def _mesh_workspace(tmp_path: Path) -> tuple[Path, Path]:
    ws = tmp_path / "files-ws"
    (ws / "workspace" / "datasets").mkdir(parents=True)
    mesh = ws / "workspace" / "datasets" / "test_square.hf5"
    mesh.write_bytes(b"\x89HDF\r\n\x1a\n mesh")
    return ws, mesh


def test_a_workspace_file_the_composite_names_travels_under_its_own_name(tmp_path):
    """vivarium-tyssue's EulerSolver reads `eptm: workspace/datasets/test_square.hf5`: compose-api's runner finds the
    file only if it is in the upload and the document names it by its bare name, which it then makes absolute."""
    ws, mesh = _mesh_workspace(tmp_path)
    solver = {"_type": "process", "address": "local:vivarium_tyssue.processes.eulersolver.EulerSolver",
              "config": {"eptm": "workspace/datasets/test_square.hf5", "tissue_type": "Sheet", "geom": "SheetGeometry",
                         "absolute": str(mesh)}}
    document = {"state": {"Tyssue": solver, "label": "a/b", "name": "notes.txt"}}

    sent, files = rc.workspace_files(document, ws)

    assert files == {"test_square.hf5": mesh.resolve()}
    assert sent["state"]["Tyssue"]["config"]["eptm"] == "test_square.hf5"
    assert sent["state"]["Tyssue"]["config"]["absolute"] == "test_square.hf5", "an absolute path inside is the same file"
    assert sent["state"]["Tyssue"]["address"] == solver["address"], "an address is not a file"
    assert (sent["state"]["label"], sent["state"]["name"]) == ("a/b", "notes.txt"), "no such file: left as written"
    assert document["state"]["Tyssue"]["config"]["eptm"] == "workspace/datasets/test_square.hf5", "the input is kept"


def test_a_file_outside_the_workspace_is_never_sent(tmp_path):
    ws, _ = _mesh_workspace(tmp_path)
    secret = tmp_path / "secret.h5"
    secret.write_bytes(b"not the workspace's")
    document = {"state": {"a": str(secret), "b": "../secret.h5", "c": "workspace/../../secret.h5"}}
    assert rc.workspace_files(document, ws) == (document, {})


def test_two_files_with_one_name_or_a_file_named_like_a_document_are_refused(tmp_path):
    ws, _ = _mesh_workspace(tmp_path)
    (ws / "other").mkdir()
    (ws / "other" / "test_square.hf5").write_bytes(b"another mesh")
    with pytest.raises(rc.ComposeDispatchError) as raised:
        rc.workspace_files({"a": "workspace/datasets/test_square.hf5", "b": "other/test_square.hf5"}, ws)
    assert raised.value.status == 422 and raised.value.body["reason"] == "file-name-collision"
    (ws / "nested.pbg").write_text("{}")
    with pytest.raises(rc.ComposeDispatchError) as raised:
        rc.workspace_files({"a": "nested.pbg"}, ws)
    assert raised.value.body["reason"] == "file-name-reserved"


def test_a_run_whose_composite_names_a_workspace_file_is_sent_as_an_archive_holding_it(ws, stub, backend_env,
                                                                                     dashboard_client):
    composite = ws / "pbg_ws_increase_demo" / "composites" / "increase-demo.composite.yaml"
    spec = yaml.safe_load(composite.read_text())
    spec["state"]["stores"]["mesh"] = "data/mesh.h5"
    composite.write_text(yaml.safe_dump(spec))
    (ws / "data").mkdir()
    (ws / "data" / "mesh.h5").write_bytes(b"mesh bytes")
    _live_registry(stub)
    _with_origin(ws, "git@github.com:someone/unregistered.git")
    _name(backend_env, stub.url, environment=None)

    res = dashboard_client(ws).post("/api/study-run-baseline", json={"study": "demo", "overrides": {}})
    assert res.status_code == 202, res.text
    (call,) = stub.calls_to("run-simulation")
    assert call.status == 200  # the stub checked the upload against compose-api's contract
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(call.body["uploaded_file"])) as omex:
        names = sorted(omex.namelist())
        documents = [n for n in names if n.endswith(".pbg")]
        assert len(documents) == 1 and "mesh.h5" in names and len(names) == 2
        assert omex.read("mesh.h5") == b"mesh bytes"
        sent = json.loads(omex.read(documents[0]))
    assert rc._state(sent)["stores"]["mesh"] == "mesh.h5"


def test_a_run_that_names_no_workspace_file_is_still_sent_as_the_document_alone(ws, stub, backend_env,
                                                                                dashboard_client):
    _live_registry(stub)
    _with_origin(ws, "git@github.com:someone/unregistered.git")
    _name(backend_env, stub.url, environment=None)
    assert dashboard_client(ws).post("/api/study-run-baseline", json={"study": "demo", "overrides": {}}).status_code == 202
    (call,) = stub.calls_to("run-simulation")
    assert isinstance(json.loads(call.body["uploaded_file"]), dict), "a .pbg, as before"


# --- signing in as the person who ran `compose-api auth login` (docs/backend-compose-api.md, "Signing in") ---------
#
# The profile is a real `[profiles.<name>]` table in the compose-api CLI's own config file (under the test's HOME),
# resolved by the client's own `load_profile`. The login is a real `SessionRecord`, committed through the client's
# journaled `AuthSession` as `compose-api auth login` commits one. Only the OS keychain is replaced: the client's
# `storage.open_persistent_store` returns `_Keychain`, whole records by binding as the keychain holds them. It is not
# the client's `MemoryCredentialStore`, so the client keeps its session state on disk (under the test's HOME) as it
# does beside the real keychain. Everything between runs for real: the routes (in-process, so the replacement reaches
# them), `signed_in_session`, the profile-to-backend match, the stored session and its on-disk state, and the HTTP
# call to the contract stub, which records the Authorization header it received.
# The profiles' Auth0 domain cannot resolve, so any call to Auth0 would fail the test: a valid login needs none.

AUTH0_NOWHERE = "auth0.workbench-test.invalid"
CANCELLED = {"simulation_id": SIM_ID, "withdrawn": False, "slurm_job_id": 4286252}


@pytest.fixture
def cli_auth():
    return pytest.importorskip("compose_api_client.auth", reason="needs the compose-api-signin extra (in [dev])")


def _cli_profile(name: str, api_base_url: str):
    """``[profiles.<name>]`` in the compose-api CLI's config file, read back through the client's profile resolution."""
    from compose_api_client.profiles import default_config_path, load_profile

    path = default_config_path()
    assert Path(os.environ["HOME"]) in path.parents, f"{path} is not under the test's HOME"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(f'[profiles.{name}]\napi_base_url = "{api_base_url}"\nauth0_domain = "{AUTH0_NOWHERE}"\n'
                f'auth0_audience = "https://{name}.workbench-test.invalid"\nauth0_client_id = "workbench-{name}"\n')
    path.chmod(0o600)  # the CLI refuses a config file others can write
    return load_profile(name).settings


def _login(profile, store, *, renewable: bool = True) -> str:
    """What `compose-api auth login` leaves for ``profile`` in ``store``: a grant committed through the client's
    journaled ``AuthSession``. Returns its access token."""
    import asyncio
    import secrets
    from datetime import UTC, datetime, timedelta

    from compose_api_client.auth import AuthSession, Identity, TokenGrant
    from compose_api_client.profiles import requested_scopes
    from pydantic import SecretStr

    now = datetime.now(UTC)
    token = f"access-{profile.profile}-{secrets.token_hex(8)}"
    grant = TokenGrant(identity=Identity(issuer=profile.issuer, subject=f"auth0|{profile.profile}-tester"),
                       access_token=SecretStr(token), access_token_expires_at=now + timedelta(hours=1),
                       refresh_token=SecretStr(secrets.token_hex(8)) if renewable else None,
                       scopes=requested_scopes(persistent=True),
                       received_at=now)

    async def commit() -> None:
        session = AuthSession(profile, store)
        async with session.login_transaction() as snapshot:
            await session.commit(grant, snapshot)

    asyncio.run(commit())
    return token


class _Keychain:
    """The OS keychain, stood in for: one whole record per binding, checked on save as the client's keychain store
    checks it."""

    def __init__(self):
        self.records: dict = {}

    def load(self, binding_key):
        return self.records.get(binding_key)

    def save(self, binding_key, record):
        from compose_api_client.auth.storage import validate_record

        validate_record(binding_key, record)
        self.records[binding_key] = record

    def delete(self, binding_key):
        return self.records.pop(binding_key, None) is not None


def _replace_keychain(monkeypatch, store) -> list:
    """The OS credential store, replaced by ``store``. Returns the profile it is opened for, each time."""
    from compose_api_client.auth import storage

    opened: list = []

    def open_persistent_store(settings, *, check=True, writable=True):
        opened.append(settings.profile)
        return store

    monkeypatch.setattr(storage, "open_persistent_store", open_persistent_store)
    return opened


def _bearer(call) -> "str | None":
    return {k.lower(): v for k, v in (call.headers or {}).items()}.get("authorization")


def _signed_in_backend(stub, backend_env, ws) -> None:
    """compose-api named, with a workspace whose environment compose-api infers (so the preflight says signed_in)."""
    _live_registry(stub)
    _with_origin(ws, BASIC_REMOTE)
    _name(backend_env, stub.url, environment=None)


def test_a_cli_login_signs_the_workbench_in_when_its_profile_is_the_named_backend(ws, stub, backend_env,
                                                                                  inproc_client, cli_auth):
    """Falsifies: the workbench calls anonymously, or with another credential, when the person signed in with the
    compose-api CLI on the profile COMPOSE_API_CLI_PROFILE selects and that profile's API is the named backend.
    Without the change no request carries an Authorization header."""
    store = _Keychain()
    token = _login(_cli_profile("lab", stub.url), store)
    opened = _replace_keychain(backend_env, store)
    backend_env.setenv("COMPOSE_API_CLI_PROFILE", "lab")
    _signed_in_backend(stub, backend_env, ws)
    stub.respond("DELETE", "/simulations/{simulation_id}", 202, CANCELLED)
    c = inproc_client(ws)

    res = c.post("/api/remote-run-cancel", json={"simulation_id": RUN_ID})
    assert res.status_code == 200, res.text
    (call,) = stub.calls_to("cancel-simulation")
    assert _bearer(call) == f"Bearer {token}"

    pre = c.get("/api/remote-dispatch-preflight").json()
    assert (pre["ok"], pre["reason"], pre["signed_in"]) == (True, "inferred", True), pre
    assert "auth login" not in pre["message"] and rc.TOKEN_ENV_VAR not in pre["message"]
    # the registry reads are signed in too (compose-api lists a private repository's entries only to its role)
    assert {_bearer(x) for x in stub.calls_to("list-registry-entries")} == {f"Bearer {token}"}
    assert opened and set(opened) == {"lab"}, "the store of the profile COMPOSE_API_CLI_PROFILE names, and no other"
    from compose_api_client.auth.state import user_state_path

    sessions = user_state_path("compose-api") / "sessions"  # the client's on-disk session state, beside the keychain
    assert Path(os.environ["HOME"]) in sessions.parents and any(sessions.iterdir()), sessions


def test_compose_api_token_comes_before_a_cli_login_and_the_store_is_not_opened(ws, stub, backend_env,
                                                                               inproc_client, cli_auth):
    """Falsifies: a stored CLI login overrides COMPOSE_API_TOKEN, or is even read while the token is set."""
    store = _Keychain()
    _login(_cli_profile("lab", stub.url), store)
    opened = _replace_keychain(backend_env, store)
    backend_env.setenv("COMPOSE_API_CLI_PROFILE", "lab")
    backend_env.setenv(rc.TOKEN_ENV_VAR, "token-from-the-environment")
    _signed_in_backend(stub, backend_env, ws)
    stub.respond("DELETE", "/simulations/{simulation_id}", 202, CANCELLED)

    res = inproc_client(ws).post("/api/remote-run-cancel", json={"simulation_id": RUN_ID})
    assert res.status_code == 200, res.text
    (call,) = stub.calls_to("cancel-simulation")
    assert _bearer(call) == "Bearer token-from-the-environment"
    assert opened == []


def test_a_login_goes_only_to_its_profiles_api_and_the_401_says_which_profile(ws, stub, backend_env, inproc_client,
                                                                              cli_auth):
    """Falsifies: the workbench sends a profile's login to a backend that is not that profile's API; it picks a
    profile by URL (a signed-in `lab` profile for this backend exists, but the CLI's default profile is
    `production`); and its 401 does not say why the call was anonymous or how to change that."""
    store = _Keychain()
    production = cli_auth.load_profile().settings
    assert production.profile == "production" and not production.matches_url(stub.url)
    _login(production, store)
    _login(_cli_profile("lab", stub.url), store)
    opened = _replace_keychain(backend_env, store)
    _signed_in_backend(stub, backend_env, ws)
    stub.respond("DELETE", "/simulations/{simulation_id}", 401, {"detail": "Cancelling a simulation needs a signed-in caller"})

    res = inproc_client(ws).post("/api/remote-run-cancel", json={"simulation_id": RUN_ID})
    assert res.status_code == 401, res.text
    (call,) = stub.calls_to("cancel-simulation")
    assert _bearer(call) is None
    assert opened == [], "another API's profile: its store is not even opened"
    error = res.json()["error"]
    assert error.startswith("compose-api needs a signed-in caller: the compose-api CLI's profile 'production' is for "
                            f"{production.api_base_url}, not this backend ({stub.url})"), error
    assert "COMPOSE_API_CLI_PROFILE" in error and rc.TOKEN_ENV_VAR in error


class _LockedKeychain:
    """A credential store that cannot be read: what the client's keychain store raises for a locked keychain."""

    def load(self, binding_key):
        from compose_api_client.ext import StorageError

        raise StorageError("OS credential store is unavailable or locked; unlock it and retry")

    def save(self, binding_key, record):
        raise AssertionError("not reached: load fails first")

    def delete(self, binding_key):
        raise AssertionError("not reached: load fails first")


@pytest.mark.parametrize("problem, says", [
    ("no such profile", "profile 'nosuch' (env COMPOSE_API_CLI_PROFILE) is not built in"),
    ("blank token", f"{rc.TOKEN_ENV_VAR} must not be empty"),
    ("locked keychain", "OS credential store is unavailable or locked"),
])
def test_a_sign_in_this_host_cannot_set_up_is_a_409_and_never_an_anonymous_call(ws, stub, backend_env,
                                                                               inproc_client, cli_auth, problem, says):
    """Falsifies: a sign-in the host cannot set up (a profile that does not exist; a set but blank COMPOSE_API_TOKEN
    where the CLI's profile is this backend's, with or without a stored login: none is stored here; a credential store
    that cannot be read) is sent anonymously, or reported as compose-api's own error (502)."""
    _cli_profile("lab", stub.url)
    _replace_keychain(backend_env, _LockedKeychain() if problem == "locked keychain" else _Keychain())
    backend_env.setenv("COMPOSE_API_CLI_PROFILE", "nosuch" if problem == "no such profile" else "lab")
    if problem == "blank token":
        backend_env.setenv(rc.TOKEN_ENV_VAR, "")
    _signed_in_backend(stub, backend_env, ws)

    res = inproc_client(ws).post("/api/remote-run-cancel", json={"simulation_id": RUN_ID})
    assert res.status_code == 409, res.text
    assert res.json()["reason"] == "sign-in-setup"
    assert res.json()["error"].startswith("the workbench cannot sign in to compose-api: ") and says in res.json()["error"]
    assert stub.calls_to("cancel-simulation") == []


def test_without_the_extra_only_compose_api_token_signs_a_call_in(ws, stub, backend_env, inproc_client, cli_auth):
    """Falsifies: without the compose-api-signin extra (its import blocked here, as an install without the client's
    [auth] dependencies fails it) the workbench reads a CLI login, fails, or stops sending COMPOSE_API_TOKEN; and its
    401 does not name the extra."""
    store = _Keychain()
    _login(_cli_profile("lab", stub.url), store)
    opened = _replace_keychain(backend_env, store)
    backend_env.setenv("COMPOSE_API_CLI_PROFILE", "lab")
    backend_env.setitem(sys.modules, "compose_api_client.auth", None)
    with pytest.raises(ImportError):
        import compose_api_client.auth  # noqa: F401
    _signed_in_backend(stub, backend_env, ws)
    stub.respond("DELETE", "/simulations/{simulation_id}", 401, {"detail": "Cancelling a simulation needs a signed-in caller"})
    c = inproc_client(ws)

    anonymous = c.post("/api/remote-run-cancel", json={"simulation_id": RUN_ID})
    assert anonymous.status_code == 401, anonymous.text
    assert anonymous.json()["error"] == (f"compose-api needs a signed-in caller: set {rc.TOKEN_ENV_VAR}, or install "
                                         "vivarium-workbench[compose-api-signin] and sign in with `compose-api auth login`")
    assert c.get("/api/remote-dispatch-preflight").json()["signed_in"] is False

    backend_env.setenv(rc.TOKEN_ENV_VAR, "token-for-test")
    stub.respond("DELETE", "/simulations/{simulation_id}", 202, CANCELLED)
    assert c.post("/api/remote-run-cancel", json={"simulation_id": RUN_ID}).status_code == 200
    assert [_bearer(x) for x in stub.calls_to("cancel-simulation")] == [None, "Bearer token-for-test"]
    assert opened == []


def test_with_no_login_for_this_backends_profile_a_call_is_anonymous_and_says_how_to_sign_in(ws, stub, backend_env,
                                                                                             inproc_client, cli_auth):
    """Falsifies: with the extra and a CLI profile for this backend but no login stored, the workbench sends a
    credential, fails, or reports itself signed in; or its 401 and preflight do not say how to sign in."""
    _cli_profile("lab", stub.url)
    opened = _replace_keychain(backend_env, _Keychain())
    backend_env.setenv("COMPOSE_API_CLI_PROFILE", "lab")
    _signed_in_backend(stub, backend_env, ws)
    stub.respond("DELETE", "/simulations/{simulation_id}", 401, {"detail": "Cancelling a simulation needs a signed-in caller"})
    c = inproc_client(ws)

    res = c.post("/api/remote-run-cancel", json={"simulation_id": RUN_ID})
    assert res.status_code == 401, res.text
    (call,) = stub.calls_to("cancel-simulation")
    assert _bearer(call) is None
    advice = (f"sign in, or sign in again, with `compose-api --profile lab auth login` (CLI profile 'lab', {stub.url}), "
              f"or set {rc.TOKEN_ENV_VAR}")
    assert res.json()["error"] == f"compose-api needs a signed-in caller: {advice}"
    pre = c.get("/api/remote-dispatch-preflight").json()
    assert pre["signed_in"] is False and f"({advice})" in pre["message"], pre
    assert opened and set(opened) == {"lab"}


def test_a_cli_login_compose_api_refuses_is_sent_and_the_401_says_to_sign_in_again(ws, stub, backend_env,
                                                                                    inproc_client, cli_auth):
    """Falsifies: the workbench does not send the CLI login it found, or answers a refused one with anything but a 401
    that says to sign in again (here a login with no refresh token, so the client cannot renew it: no Auth0 call)."""
    store = _Keychain()
    token = _login(_cli_profile("lab", stub.url), store, renewable=False)
    _replace_keychain(backend_env, store)
    backend_env.setenv("COMPOSE_API_CLI_PROFILE", "lab")
    _signed_in_backend(stub, backend_env, ws)
    stub.respond("DELETE", "/simulations/{simulation_id}", 401, {"detail": "Invalid bearer token"})

    res = inproc_client(ws).post("/api/remote-run-cancel", json={"simulation_id": RUN_ID})
    assert res.status_code == 401, res.text
    assert [_bearer(x) for x in stub.calls_to("cancel-simulation")] == [f"Bearer {token}"], "sent once, not renewed"
    assert res.json()["error"] == ("compose-api needs a signed-in caller: sign in, or sign in again, with "
                                   f"`compose-api --profile lab auth login` (CLI profile 'lab', {stub.url}), or set "
                                   f"{rc.TOKEN_ENV_VAR}")


# --- making a run public (the Runs table's "Make public": the optional runs.change_visibility) -----------------------

CHANGED = {"visibility": "public", "simulation_ids": [SIM_ID, SIM_ID + 1]}


def test_make_public_sends_the_owners_login_and_says_which_runs_changed(ws, stub, backend_env, inproc_client,
                                                                        cli_auth):
    """Falsifies: the workbench cannot make a signed-in person's run public, or does it without their login, or
    sends another body than compose-api's ``{visibility}``. The other runs of a comparison change with it."""
    store = _Keychain()
    token = _login(_cli_profile("lab", stub.url), store)
    _replace_keychain(backend_env, store)
    backend_env.setenv("COMPOSE_API_CLI_PROFILE", "lab")
    _signed_in_backend(stub, backend_env, ws)
    stub.respond("PATCH", "/simulations/{simulation_id}", 200, CHANGED)

    res = inproc_client(ws).post("/api/remote-run-visibility", json={"simulation_id": RUN_ID, "visibility": "public"})
    assert res.status_code == 200, res.text
    assert res.json() == {"kind": "visibility", "run_id": RUN_ID, "simulation_id": RUN_ID, "visibility": "public",
                          "changed": [RUN_ID, f"compose-{SIM_ID + 1}"]}
    (call,) = stub.calls_to("change-simulation-visibility")
    assert call.path == f"/simulations/{SIM_ID}" and call.body == {"visibility": "public"}
    assert _bearer(call) == f"Bearer {token}"


# compose-api's own details (compose_api/api/routers/simulations.py, change_simulation_visibility, 0.7.100).
@pytest.mark.parametrize("status, detail, reason", [
    (401, "Changing a simulation's visibility needs a signed-in caller", "unauthenticated"),
    (403, "Only its owner may change this simulation's visibility", "forbidden"),
    (403, "A run with no owner stays public", "forbidden"),
    (404, "Simulation with id 4706 not found.", None),
    (409, "Simulation 4706 is compared with runs you may not change: 4707", "not-changeable"),
])
def test_make_public_passes_compose_apis_refusals_through(ws, stub, backend_env, inproc_client, status, detail,
                                                           reason):
    """Each of compose-api's documented answers comes back as itself, never a 502: anonymous (401, with how to sign
    in), not the owner (403), no such or unreadable run (404), a comparison with runs this caller may not change
    (409, compose-api's words)."""
    _name(backend_env, stub.url)
    stub.respond("PATCH", "/simulations/{simulation_id}", status, {"detail": detail})
    res = inproc_client(ws).post("/api/remote-run-visibility", json={"simulation_id": RUN_ID, "visibility": "public"})
    assert res.status_code == status, res.text
    body = res.json()
    assert body["run_id"] == RUN_ID and body.get("reason") == reason
    if status == 401:
        assert "auth login" in body["error"] or rc.TOKEN_ENV_VAR in body["error"]
    if status == 403:
        # The client keeps no detail on a 403, so the answer names both of compose-api's causes, whichever it was:
        # never "your account was refused" for an anonymous run that is public already.
        assert "only its owner" in body["error"] and "already public" in body["error"]
    if status == 409:
        assert detail in body["error"]


@pytest.mark.parametrize("payload, says", [
    ({"simulation_id": RUN_ID, "visibility": "everyone"}, "visibility must be one of public, private"),
    ({"simulation_id": RUN_ID}, "visibility must be one of public, private"),
    ({"visibility": "public"}, "simulation_id required"),
])
def test_make_public_refuses_a_bad_request_before_asking_compose_api(ws, stub, backend_env, inproc_client, payload,
                                                                     says):
    _name(backend_env, stub.url)
    res = inproc_client(ws).post("/api/remote-run-visibility", json=payload)
    assert res.status_code == 400 and says in res.json()["error"], res.text
    assert stub.calls_to("change-simulation-visibility") == []


# --- signing in from the page (lib/compose_signin.py; /api/auth/compose-api/*) -----------------------------------
#
# Real here: the routes and the CSRF middleware, the app on ONE event loop across requests (a TestClient entered as a
# context manager, as uvicorn keeps one loop), the background task, the client's `resolve`, `connection_for`,
# `SignInRequest`, profile resolution and journaled `AuthSession`, `signed_in_session` reading the result, and a run
# request to the contract stub. Stood in: the OS keychain (`_Keychain`, as above) and Auth0's side of the sign-in.
# `persistent_sign_in` is replaced by a double that calls `notify` as the client does, waits for the test to let the
# person "finish", then commits a real grant through the client's `AuthSession` into the store it was handed: what
# a finished sign-in leaves. The client's OAuth exchange itself is the client's to test (compose-api's tests).

import threading as _threading

_REAL_CARRIES = rc._carries_credential


@pytest.fixture
def signin_client(ws, backend_env):
    """A client on one event loop for the whole test, and the module's sign-in state reset around it."""
    from fastapi.testclient import TestClient

    from vivarium_workbench.api.app import create_app, get_workspace
    from vivarium_workbench.lib import _root, compose_signin

    saved_root = _root.get_workspace_root()
    compose_signin._FLOW = None
    _root.set_workspace_root(ws)
    app = create_app()
    app.dependency_overrides[get_workspace] = lambda: ws
    with TestClient(app, base_url="http://127.0.0.1") as tc:
        yield tc
    compose_signin._FLOW = None
    if saved_root is not None:
        _root.set_workspace_root(saved_root)


class _PersonAtAuth0:
    """``persistent_sign_in`` with Auth0's side stood in: tells the person what to do through ``notify``, waits until
    the test says they finished, then commits a real grant into the store, through the client's ``AuthSession``."""

    def __init__(self):
        self.calls: list = []
        self.finished = _threading.Event()
        self.token = None

    async def __call__(self, settings, request, store, *, notify, **kwargs):
        import asyncio
        import secrets
        from datetime import UTC, datetime, timedelta

        from compose_api_client.auth import AuthSession, Identity, TokenGrant
        from compose_api_client.profiles import requested_scopes
        from pydantic import SecretStr

        self.calls.append({"profile": settings.profile, "method": str(request.method),
                           "persistent": request.persistent, "signup": request.signup, "store": store})
        notify("Open https://login.example.invalid/activate and enter WXYZ-1234")
        while not self.finished.is_set():
            await asyncio.sleep(0.02)
        now = datetime.now(UTC)
        self.token = f"access-{settings.profile}-{secrets.token_hex(8)}"
        grant = TokenGrant(identity=Identity(issuer=settings.issuer, subject="auth0|page-signer"),
                           access_token=SecretStr(self.token), access_token_expires_at=now + timedelta(hours=1),
                           refresh_token=SecretStr(secrets.token_hex(8)), scopes=requested_scopes(persistent=True),
                           received_at=now)
        session = AuthSession(settings, store)
        async with session.login_transaction() as snapshot:
            await session.commit(grant, snapshot)


def _principal(settings, subject="auth0|page-signer") -> dict:
    """compose-api's ``GET /auth/me`` answer for a signed-in caller (the contract's CurrentPrincipalResponse)."""
    return {"issuer": settings.issuer, "subject": subject, "audience": [settings.auth0_audience], "roles": ["user"],
            "scopes": [], "permissions": []}


def _poll(client, until, timeout=10.0):
    import time

    deadline = time.monotonic() + timeout
    while True:
        status = client.get("/api/auth/compose-api/status").json()
        if until(status) or time.monotonic() > deadline:
            return status
        time.sleep(0.05)


def test_signing_in_from_the_page_makes_the_cli_login_and_signs_runs_in(ws, stub, backend_env, signin_client,
                                                                         cli_auth):
    """Falsifies: the page's sign-in signs in on another profile or store than the CLI's, is not persistent, does not
    survive the request that started it (it must keep waiting for the person), hides the client's instructions, or
    leaves runs unsigned. After it, a run request carries the login it made."""
    from compose_api_client.auth import signin

    store = _Keychain()
    opened = _replace_keychain(backend_env, store)
    profile = _cli_profile("lab", stub.url)
    backend_env.setenv("COMPOSE_API_CLI_PROFILE", "lab")
    _signed_in_backend(stub, backend_env, ws)
    stub.respond("GET", "/auth/me", 200, _principal(profile))
    person = _PersonAtAuth0()
    backend_env.setattr(signin, "persistent_sign_in", person)
    c = signin_client

    before = c.get("/api/auth/compose-api/status").json()
    assert (before["available"], before["signed_in"], before["profile"]) == (True, False, "lab"), before

    res = c.post("/api/auth/compose-api/start", json={"method": "device"})
    assert res.status_code == 202, res.text
    waiting = _poll(c, lambda s: s["flow"] and s["flow"]["notes"])
    assert waiting["flow"]["state"] == "running"
    assert "WXYZ-1234" in "\n".join(waiting["flow"]["notes"])          # the client's instruction reaches the page
    # Not read while waiting: a read may renew the stored login, and a renewal makes the client discard the sign-in.
    assert waiting["signed_in"] is None
    reads = []
    backend_env.setattr(rc, "_carries_credential", lambda session: reads.append(session) or False)
    c.get("/api/auth/compose-api/status")
    assert reads == []
    backend_env.setattr(rc, "_carries_credential", _REAL_CARRIES)
    (call,) = person.calls
    assert (call["profile"], call["method"], call["persistent"], call["signup"]) == ("lab", "device", True, False)
    assert call["store"] is store and set(opened) == {"lab"}           # the CLI profile's own credential store

    person.finished.set()
    done = _poll(c, lambda s: s["flow"]["state"] != "running")
    assert done["flow"]["state"] == "ok", done
    assert done["signed_in"] is True
    (me,) = stub.calls_to("get-current-principal")      # the API confirmed the new login, as the CLI has it confirm
    assert _bearer(me) == f"Bearer {person.token}"

    stub.respond("DELETE", "/simulations/{simulation_id}", 202, CANCELLED)
    assert c.post("/api/remote-run-cancel", json={"simulation_id": RUN_ID}).status_code == 200
    (cancel,) = stub.calls_to("cancel-simulation")
    assert _bearer(cancel) == f"Bearer {person.token}"


def test_a_login_the_api_does_not_confirm_is_not_reported_as_signed_in(ws, stub, backend_env, signin_client,
                                                                          cli_auth):
    """Falsifies: the page says the sign-in worked when compose-api refuses the new login (a profile whose audience is
    wrong for this API, say). The CLI counts a login done only once the API confirms who it is; so must the page."""
    from compose_api_client.auth import signin

    _replace_keychain(backend_env, _Keychain())
    _cli_profile("lab", stub.url)
    backend_env.setenv("COMPOSE_API_CLI_PROFILE", "lab")
    _signed_in_backend(stub, backend_env, ws)
    stub.respond_error("GET", "/auth/me", 401, {"detail": "Invalid token audience"})
    person = _PersonAtAuth0()
    backend_env.setattr(signin, "persistent_sign_in", person)
    c = signin_client

    assert c.post("/api/auth/compose-api/start", json={"method": "device"}).status_code == 202
    person.finished.set()
    done = _poll(c, lambda s: s["flow"]["state"] != "running")
    assert done["flow"]["state"] == "error", done
    assert "did not confirm" in done["flow"]["error"]


def test_a_sign_in_that_fails_before_waiting_does_not_block_the_next(ws, stub, backend_env, signin_client, cli_auth):
    """Falsifies: a failure while preparing (before the person is asked) leaves the sign-in "running" with nothing
    behind it, so every later start is refused as already waiting."""
    from compose_api_client.auth import signin

    _replace_keychain(backend_env, _Keychain())
    _cli_profile("lab", stub.url)
    backend_env.setenv("COMPOSE_API_CLI_PROFILE", "lab")
    _signed_in_backend(stub, backend_env, ws)

    def broken_resolve(*args, **kwargs):
        raise RuntimeError("resolve broke")

    backend_env.setattr(signin, "resolve", broken_resolve)
    c = signin_client
    first = c.post("/api/auth/compose-api/start", json={"method": "device"})
    assert first.status_code == 409 and "resolve broke" in first.json()["error"]
    assert c.get("/api/auth/compose-api/status").json()["flow"]["state"] == "error"
    second = c.post("/api/auth/compose-api/start", json={"method": "device"})
    assert "already" not in second.json().get("error", ""), second.json()


def test_one_sign_in_at_a_time_and_only_known_methods(ws, stub, backend_env, signin_client, cli_auth):
    """Falsifies: a second sign-in starts while one waits for the person, or an unknown method is passed on."""
    from compose_api_client.auth import signin

    _replace_keychain(backend_env, _Keychain())
    profile = _cli_profile("lab", stub.url)
    backend_env.setenv("COMPOSE_API_CLI_PROFILE", "lab")
    _signed_in_backend(stub, backend_env, ws)
    stub.respond("GET", "/auth/me", 200, _principal(profile))
    person = _PersonAtAuth0()
    backend_env.setattr(signin, "persistent_sign_in", person)
    c = signin_client

    assert c.post("/api/auth/compose-api/start", json={"method": "carrier-pigeon"}).status_code == 400
    assert c.post("/api/auth/compose-api/start", json={"method": "device"}).status_code == 202
    again = c.post("/api/auth/compose-api/start", json={"method": "device"})
    assert again.status_code == 409 and "already" in again.json()["error"]
    person.finished.set()
    assert _poll(c, lambda s: s["flow"]["state"] != "running")["flow"]["state"] == "ok"
    assert len(person.calls) == 1


def test_sign_in_is_offered_only_where_it_would_sign_the_calls_in(ws, stub, backend_env, signin_client, cli_auth):
    """Falsifies: the page offers a sign-in that would not sign this workbench's calls in: no backend named, a
    COMPOSE_API_TOKEN that takes precedence, or a CLI profile for another API (its login is never sent here)."""
    from compose_api_client.auth import signin

    person = _PersonAtAuth0()
    backend_env.setattr(signin, "persistent_sign_in", person)
    _replace_keychain(backend_env, _Keychain())
    c = signin_client

    assert c.get("/api/auth/compose-api/status").json()["reason"] == "no-backend"
    _signed_in_backend(stub, backend_env, ws)
    _cli_profile("elsewhere", "https://compose.elsewhere.invalid")
    backend_env.setenv("COMPOSE_API_CLI_PROFILE", "elsewhere")
    other = c.get("/api/auth/compose-api/status").json()
    assert (other["available"], other["reason"]) == (False, "other-profile"), other
    assert c.post("/api/auth/compose-api/start", json={}).status_code == 409
    _cli_profile("lab", stub.url)
    backend_env.setenv("COMPOSE_API_CLI_PROFILE", "lab")
    backend_env.setenv(rc.TOKEN_ENV_VAR, "a-token")
    assert c.get("/api/auth/compose-api/status").json()["reason"] == "token"
    backend_env.delenv(rc.TOKEN_ENV_VAR)
    assert c.get("/api/auth/compose-api/status").json()["available"] is True
    assert person.calls == []


def test_signing_out_from_the_page_erases_the_cli_login(ws, stub, backend_env, signin_client, cli_auth):
    """Falsifies: signing out leaves the CLI's stored login (so runs stay signed in), or needs Auth0 to clear it."""
    store = _Keychain()
    _login(_cli_profile("lab", stub.url), store)
    _replace_keychain(backend_env, store)
    backend_env.setenv("COMPOSE_API_CLI_PROFILE", "lab")
    _signed_in_backend(stub, backend_env, ws)
    c = signin_client

    assert c.get("/api/auth/compose-api/status").json()["signed_in"] is True
    out = c.post("/api/auth/compose-api/logout", json={})
    assert out.status_code == 200, out.text
    assert out.json()["signed_in"] is False
    # Auth0 cannot be reached here (the profile's domain does not resolve): the login is erased all the same, and the
    # answer says the revocation did not happen, as the CLI's logout does.
    assert out.json()["revocation"] == "unavailable"
    assert store.records == {}                       # erased from the store the CLI reads too
    assert c.get("/api/auth/compose-api/status").json()["signed_in"] is False


def test_the_sign_in_routes_refuse_another_origin_and_are_not_read_only(ws, backend_env, signin_client):
    """Falsifies: a page on another origin can start or end a sign-in here, or a read-only workbench offers it."""
    from vivarium_workbench.api import app as app_module

    c = signin_client
    for path in ("/api/auth/compose-api/start", "/api/auth/compose-api/logout"):
        assert c.post(path, json={}).status_code != 404                # the route exists (same origin reaches it)
        assert c.post(path, json={}, headers={"Origin": "https://evil.example"}).status_code == 403
        assert path not in app_module._READONLY_ALLOWED_MUTATIONS


def test_the_card_waits_for_a_long_run_as_long_as_compose_api_may_run_it(ws, stub, backend_env):
    """Falsifies: the card's runner gives up on a long run at its stall guard (``poll_timeout``) while compose-api still
    runs it, so the row is marked failed and never landed. compose-api allows a job 30 min per 1,000 steps (1.8 s per
    step); the card waits its base plus 2 s per step. Here the run is still running past ``poll_timeout``, and a
    7,700-step run must still be waited for and land."""
    import time

    from compose_api_client.ext import ComposeSession

    _register_fixture_workspace(stub)
    _completed(stub)
    stub.respond("GET", "/results/simulation/status", 200, _hpcrun("running"))
    _name(backend_env, stub.url)
    real_status = ComposeSession.status
    started = {}

    def status(self, simulation_id):
        started.setdefault("at", time.monotonic())
        if time.monotonic() - started["at"] > 0.3:     # well past poll_timeout below: compose-api finishes the run
            stub.respond("GET", "/results/simulation/status", 200, _hpcrun("completed"))
        return real_status(self, simulation_id)

    backend_env.setattr(ComposeSession, "status", status)
    archive = rc.run_composite(ws, COMPOSITE, dest=ws / ".pbg" / "out", n_steps=7700, overrides={},
                               emit_paths=["stores/level"], poll_interval=0.02, poll_timeout=0.1)
    assert archive.is_file()
    (call,) = stub.calls_to("run-simulation")
    assert call.query["interval_time"] == ["7700.0"]
    assert rc.wait_timeout(7700, 7200) == 7200 + 2.0 * 7700
    assert rc.wait_timeout(7700, 0) is None, "a poller with its deadline off sets none here either"


def test_the_card_shows_compose_apis_own_refusal_of_a_long_run(ws, stub, backend_env):
    """Falsifies: the card's run hides or rewrites compose-api's refusal of a run longer than the caller may run."""
    _register_fixture_workspace(stub)
    _name(backend_env, stub.url)
    stub.respond_error("POST", "/simulation/run", 400, {"detail": LONG_RUN_REFUSAL})
    with pytest.raises(RuntimeError) as refused:
        rc.run_composite(ws, COMPOSITE, dest=ws / ".pbg" / "out", n_steps=7700, overrides={},
                         emit_paths=["stores/level"], poll_interval=0.01, poll_timeout=5)
    assert str(refused.value) == f"compose-api refused the run: {LONG_RUN_REFUSAL}"


# --- a sent run is read through a credential store that refuses for a while ---------------------------------------
#
# Stood in: only the client's credential store refusing (its StorageError, raised by a status poll or the download, as
# a session another process holds past the client's 30 s, or a locked keychain, raises it). Each refusal here takes
# longer than the runner's backoff, as the client's 30 s lock wait does. The run, its dispatch, the polls, the download
# and the landing are real, against the contract stub.

BUSY = "Session is busy; finish the other sign-in or retry later"
REFUSAL_TAKES = 0.05      # longer than the patched maximum backoff below, as a real refusal (30 s) is longer than 30 s


def _quick_backoff(backend_env, bound=60.0):
    backend_env.setattr(rc, "STORE_RETRY_FIRST_DELAY", 0.01)
    backend_env.setattr(rc, "STORE_RETRY_MAX_DELAY", 0.02)
    backend_env.setattr(rc, "STORE_RETRY_SECONDS", bound)


def _refusing(backend_env, method_name, pattern, before_call=None):
    """``ComposeSession.<method_name>`` refusing (the client's StorageError, after REFUSAL_TAKES) on the calls whose
    entry in ``pattern`` is True, and real otherwise; past the pattern, the last entry repeats."""
    import time as _time

    from compose_api_client.ext import ComposeSession, StorageError

    real = getattr(ComposeSession, method_name)
    calls = {"n": 0}

    def refusing(self, *args, **kwargs):
        calls["n"] += 1
        if before_call is not None:
            before_call(calls["n"])
        if pattern[min(calls["n"], len(pattern)) - 1]:
            _time.sleep(REFUSAL_TAKES)
            raise StorageError(BUSY)
        return real(self, *args, **kwargs)

    backend_env.setattr(ComposeSession, method_name, refusing)
    return calls


def test_a_run_is_read_through_a_store_that_refuses_for_a_while(ws, stub, backend_env):
    """Falsifies: a credential store that refuses for a while as the card's runner polls a sent run or downloads it (a
    session another process holds) fails the run, though compose-api runs it to the end. A refusal that takes longer
    than the backoff (the client's 30 s lock wait) must still count towards the bound, not reset it."""
    _register_fixture_workspace(stub)
    _completed(stub)
    _name(backend_env, stub.url)
    _quick_backoff(backend_env)
    polls = _refusing(backend_env, "status", [True, True, True, False])
    downloads = _refusing(backend_env, "download", [True, True, False])
    archive = rc.run_composite(ws, COMPOSITE, dest=ws / ".pbg" / "out", n_steps=3, overrides={},
                               emit_paths=["stores/level"], poll_interval=0.01, poll_timeout=60)
    assert archive.is_file()
    assert (polls["n"], downloads["n"]) == (4, 3)


def test_a_store_that_never_yields_fails_the_run_with_its_cause_after_the_bound(ws, stub, backend_env):
    """Falsifies: a store that never yields (a locked keychain, a session held for good) is retried forever, which a
    refusal slower than the backoff once caused, or the failure hides its cause or the run compose-api may finish."""
    import time

    _register_fixture_workspace(stub)
    _completed(stub)
    _name(backend_env, stub.url)
    _quick_backoff(backend_env, bound=0.5)
    polls = _refusing(backend_env, "status", [True])
    started = time.monotonic()
    with pytest.raises(RuntimeError) as failed:
        rc.run_composite(ws, COMPOSITE, dest=ws / ".pbg" / "out", n_steps=3, overrides={},
                         emit_paths=["stores/level"], poll_interval=0.01, poll_timeout=60)
    assert time.monotonic() - started < 5, "the bound ended the wait"
    message = str(failed.value)
    assert BUSY in message
    assert "on compose-api, which may finish it all the same" in message and rc.RUN_ID_PREFIX in message
    assert polls["n"] >= 2


def test_a_poll_that_succeeds_ends_a_streak_of_refusals(ws, stub, backend_env):
    """Falsifies: refusals separated by successful polls add up to the bound (a long run with an occasional busy
    store would then fail); only consecutive refusals count."""
    _register_fixture_workspace(stub)
    _completed(stub)
    _name(backend_env, stub.url)
    _quick_backoff(backend_env, bound=0.12)
    stub.respond("GET", "/results/simulation/status", 200, _hpcrun("running"))

    def finish_at_the_last_poll(n):
        if n == 6:
            stub.respond("GET", "/results/simulation/status", 200, _hpcrun("completed"))

    # Three refusals in a row (~0.15 s, past the bound) would fail. Two, a successful poll (still running), then two
    # more, then a successful poll (completed) must not: the success in between ends the first streak.
    polls = _refusing(backend_env, "status", [True, True, False, True, True, False], finish_at_the_last_poll)
    archive = rc.run_composite(ws, COMPOSITE, dest=ws / ".pbg" / "out", n_steps=3, overrides={},
                               emit_paths=["stores/level"], poll_interval=0.01, poll_timeout=60)
    assert archive.is_file() and polls["n"] == 6


# --- a run that stopped before completing: its results up to the last snapshot ----------------------------------------
#
# Real artifacts (tests/_fixtures/compose_api/README.md): production simulation 12966 (mash-f3ae3f0e91@3, compose-api
# 0.7.109), cancelled about seven minutes into a 3000 s run. Its listing as `GET /datasets?simulation_id=12966` served it
# and its one `results` dataset, copied byte for byte: 1,721 states, t = 0 to 1720. compose-api keeps no results.zip for
# it; nothing marks the file as partial, so the workbench reads that from the run's status.

PARTIAL = "results_12966_cancelled.pber"


def _stopped(stub, status: str, *, kept: bool = True) -> None:
    """A run that ended ``status``, and the datasets compose-api lists for it: 12966's real ``results`` row (the page
    the server answers to ``kind=results``) and its real file, or none."""
    stub.respond("GET", "/results/simulation/status", 200, _hpcrun(status))
    rows = [d for d in _json("datasets_12966_cancelled.json") if d["kind"] == "results"] if kept else []
    stub.respond("GET", "/datasets", 200, {"datasets": rows, "total": len(rows), "next_offset": None})
    stub.respond_bytes("GET", "/datasets/{dataset_id}/content", 200, (FIX / PARTIAL).read_bytes(),
                       "application/octet-stream")


def _card_run(ws, steps: float):
    from vivarium_workbench.lib import composite_runs as cr
    from vivarium_workbench.lib import run_runner

    run_dir = ws / ".pbg" / "runs" / "card-1"
    run_dir.mkdir(parents=True)
    db = ws / ".pbg" / "composite-runs.db"
    conn = cr.connect(db)
    try:
        cr.save_metadata(conn, spec_id=COMPOSITE, run_id="card-1", params={}, label="", started_at=0.0, n_steps=steps)
        cr.ensure_history_table(conn)
    finally:
        conn.close()
    req = run_runner.RunRequest(
        run_id="card-1", spec_id=COMPOSITE, pkg="pbg_ws_increase_demo", workspace=ws, overrides={}, steps=steps,
        emit_paths=["stores/level"], db_file=str(db), log_path=str(run_dir / "run.log"), target="deployment")
    code = run_runner._execute_remote(req, run_dir)
    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT status, n_steps FROM runs_meta WHERE run_id = 'card-1'").fetchone()
        times = conn.execute("SELECT min(global_time), max(global_time), count(*) FROM history "
                             "WHERE simulation_id = 'card-1'").fetchone()
    log = run_dir / "run.log"  # written by the runner only on a stop or failure; a completed run's is its stdout
    return code, row, times, log.read_text() if log.is_file() else ""


def test_a_cancelled_run_lands_its_results_up_to_the_last_snapshot(ws, stub, backend_env):
    """Falsifies: the card's runner records a cancelled run that kept its results as failed with nothing (as before
    this change), lands only part of them, or reads them from anywhere but the run's `results` dataset."""
    _register_fixture_workspace(stub)
    _stopped(stub, "cancelled")
    _name(backend_env, stub.url)
    code, row, times, log = _card_run(ws, 3000)
    assert code == 1
    assert row == ("cancelled", 1721)
    assert times == (0.0, 1720.0, 1721)
    assert (f"REMOTE RUN STOPPED: compose-api run {RUN_ID} stopped (cancelled) at t=1720 of 3000; "
            "the results up to the last snapshot are landed") in log
    (listing,) = stub.calls_to("list-datasets")
    assert listing.query["simulation_id"] == [str(SIM_ID)] and listing.query["kind"] == ["results"]
    (content,) = stub.calls_to("get-dataset-content")
    assert content.path == "/datasets/c8120c16-68f6-4cf8-b7be-4fa66dbbef91/content"
    assert stub.calls_to("get-simulation-results-file") == []


def test_a_timed_out_run_lands_its_results_and_is_recorded_failed(ws, stub, backend_env):
    """ASSUMED SHAPE: no real timed-out run's file exists yet. compose-api's time limit sends the same SIGTERM a
    cancel does (#581), so this serves 12966's cancelled file as a timed-out run's; it checks only the workbench's
    handling of the status, not what a real timeout keeps."""
    _register_fixture_workspace(stub)
    _stopped(stub, "timeout")
    _name(backend_env, stub.url)
    code, row, times, log = _card_run(ws, 3000)
    assert code == 1
    assert row == ("failed", 1721)
    assert times[2] == 1721
    assert f"compose-api run {RUN_ID} stopped (timeout) at t=1720 of 3000" in log


def test_a_stopped_run_that_kept_nothing_fails_as_before(ws, stub, backend_env):
    """Falsifies: a stopped run with no `results` dataset (an old environment version, a lost node) is recorded as
    stopped with results, or its failure stops naming the status."""
    _register_fixture_workspace(stub)
    _stopped(stub, "cancelled", kept=False)
    _name(backend_env, stub.url)
    code, row, times, log = _card_run(ws, 3000)
    assert code == 1
    assert row == ("failed", 0)
    assert times[2] == 0
    assert f"REMOTE RUN FAILED: RuntimeError: compose-api run {RUN_ID} ended cancelled" in log
    assert stub.calls_to("get-dataset-content") == []


def test_a_stopped_run_whose_results_cannot_be_read_fails_naming_its_status(ws, stub, backend_env):
    """Falsifies: a listing that fails after the run ended reports the backend's error with "may finish it all the
    same", hiding that the run ended (found in review)."""
    _register_fixture_workspace(stub)
    _stopped(stub, "cancelled")
    stub.respond_error("GET", "/datasets", 502, {"detail": "bad gateway"})
    _name(backend_env, stub.url)
    code, row, _times, log = _card_run(ws, 3000)
    assert code == 1 and row == ("failed", 0)
    assert f"compose-api run {RUN_ID} ended cancelled; its results so far could not be read" in log
    assert "may finish it all the same" not in log


def test_a_completed_run_still_lands_results_zip_and_never_lists_datasets(ws, stub, backend_env):
    """The unchanged path: a completed run is read from results.zip only."""
    _register_fixture_workspace(stub)
    _completed(stub)
    _name(backend_env, stub.url)
    code, row, times, _log = _card_run(ws, 3)
    assert code == 0 and row == ("completed", 6) and times[2] == 6
    assert len(stub.calls_to("get-simulation-results-file")) == 1
    assert stub.calls_to("list-datasets") == []


def _detail(status: str) -> dict:
    return {"simulation_id": SIM_ID, "experiment_id": "exp-1", "simulator_id": 282, "container_def_hash": "h",
            "status": status, "simulator": PILOT}


def test_a_cancelled_run_lands_in_a_study_as_partial(ws, stub, backend_env, dashboard_client):
    """Falsifies: ⬇ Land results still refuses a cancelled run that kept its results, or lands it as completed."""
    _stopped(stub, "cancelled")
    stub.respond("GET", "/simulations/{simulation_id}", 200, _detail("cancelled"))
    _name(backend_env, stub.url)
    res = dashboard_client(ws).post("/api/remote-run-land", json={"study": "demo", "simulation_id": RUN_ID})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["partial"] is True and body["status"] == "cancelled"
    with sqlite3.connect(ws / "studies" / "demo" / "runs.db") as db:
        assert db.execute("SELECT status, n_steps FROM runs_meta WHERE run_id = ?",
                          (body["run_id"],)).fetchone() == ("cancelled", 1721)
        assert db.execute("SELECT max(global_time) FROM history WHERE simulation_id = ?",
                          (body["run_id"],)).fetchone() == (1720.0,)
    assert stub.calls_to("get-simulation-results-file") == []


def test_a_stopped_run_that_kept_nothing_is_not_landed_in_a_study(ws, stub, backend_env, dashboard_client):
    _stopped(stub, "out_of_memory", kept=False)
    _name(backend_env, stub.url)
    res = dashboard_client(ws).post("/api/remote-run-land", json={"study": "demo", "simulation_id": RUN_ID})
    assert res.status_code == 409 and res.json()["error"] == f"run {RUN_ID!r} is out_of_memory and kept no results to land"
    assert not (ws / "studies" / "demo" / "runs.db").is_file() or sqlite3.connect(
        ws / "studies" / "demo" / "runs.db").execute("SELECT count(*) FROM runs_meta").fetchone() == (0,)
