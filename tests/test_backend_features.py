"""Declared backend features (docs/backend-adapters.md §2.1, step P3 F0).

``GET /api/backend/features`` answers the process's features (``lib/backends/features.process_features``) and every
``/api/simulations`` row with a ``remote_origin`` carries the run's own, ``remote_origin.features``. Both are registry
reads (#1106). Pinned here: the three modes (providers on, off, plugin absent), where a row's backend comes from
(listing backend, record, id form), the no-blocking rule, read-only mode, the published bundle (the asset ships and
makes no request in snapshot mode), and the page shells that load it. No consumer reads features yet, so the UI does
not change; ``test_backend_features_goldens.py`` pins that the payload is otherwise unchanged.
"""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

from vivarium_workbench.lib import backends
from vivarium_workbench.lib import run_log
from vivarium_workbench.lib import simulations_index as si
from vivarium_workbench.lib.backends import features as bf
from vivarium_workbench.lib.backends import provider as provider_mod

SMS_PROCESS = ["analyses", "artifacts", "builds", "listing", "sms-launch"]
SMS_RUN = ["analyses", "artifacts"]


@pytest.fixture
def providers_off(monkeypatch):
    monkeypatch.setenv(provider_mod.PROVIDERS_ENV, "none")
    backends.reset_registry()
    si.clear_build_cache()
    yield
    monkeypatch.delenv(provider_mod.PROVIDERS_ENV, raising=False)
    backends.reset_registry()
    si.clear_build_cache()


def _origin(sim_id, *, exp=None):
    return {"deployment": "dep", "simulation_id": sim_id, "experiment_id": exp, "backend": "q", "s3_uri": None}


def _remote(kind, rid):
    return {"kind": kind, "id": str(rid), "base_url": "http://x.test", "deployment": "dep", "server_id": None}


EVENTS = [
    ("local-run", {"status": "completed", "started_at": 1.0}),
    ("sms-recorded", {"status": "completed", "started_at": 2.0, "remote_origin": _origin(41),
                      "params": {"source": "dep", "simulation_id": 41, "remote": _remote("sms-legacy", 41)}}),
    ("sms-unrecorded", {"status": "running", "started_at": 3.0, "remote_origin": _origin(42)}),
    ("viva-core-run", {"status": "completed", "started_at": 4.0, "remote_origin": _origin("simulation-ABC1234"),
                       "params": {"remote": _remote("viva-core", "simulation-ABC1234")}}),
    # The record decides over the id's form: an integer id recorded as viva-core is viva-core's.
    ("viva-core-int-id", {"status": "completed", "started_at": 4.5, "remote_origin": _origin(99),
                          "params": {"remote": _remote("viva-core", 99)}}),
    ("compose-run", {"status": "failed", "started_at": 5.0, "remote_origin": _origin("compose-4706"),
                     "params": {"remote": _remote("compose-api", "compose-4706")}}),
    ("remote-pending-77", {"status": "queued", "started_at": 6.0,
                           "params": {"source": "dep", "simulation_id": 77, "experiment_id": "exp-77"}}),
]
LISTED = [
    {"run_id": "listed-int", "status": "completed", "started_at": 7.0, "remote_origin": _origin(501)},
    # A listed row takes its LISTING backend's kind, not its id form's (which would be viva-core's).
    {"run_id": "listed-str", "status": "completed", "started_at": 8.0, "remote_origin": _origin("simulation-L1")},
]

EXPECTED_ON = {
    "local-run": None,
    "sms-recorded": SMS_RUN,
    "sms-unrecorded": SMS_RUN,
    "viva-core-run": [],
    "viva-core-int-id": [],
    "compose-run": [],
    "remote-pending-77": SMS_RUN,
    "listed-int": SMS_RUN,
    "listed-str": SMS_RUN,
}


def _write_log(ws: Path) -> None:
    (ws / "workspace.yaml").write_text("name: features\n", encoding="utf-8")
    for rid, fields in EVENTS:
        run_log.append_run_event(ws, {"run_id": rid, "event": "started", **fields})


@pytest.fixture
def ws(tmp_path, monkeypatch):
    _write_log(tmp_path)
    si.clear_build_cache()
    yield tmp_path
    si.clear_build_cache()


@pytest.fixture
def listing(monkeypatch):
    rs = pytest.importorskip("vivarium_workbench_sms.remote_simulations")  # providers on: the plugin's listing
    monkeypatch.setattr(rs, "list_remote_simulations_swr", lambda ws, *a, **k: copy.deepcopy(LISTED))
    monkeypatch.setattr(rs, "remote_state", lambda ws, *a, **k: {"state": "fresh", "as_of": 1.0, "error": None})
    return rs


def _features_by_run(rows):
    return {r["run_id"]: (r["remote_origin"] or {}).get("features") if r.get("remote_origin") else None
            for r in rows}


# --- the process's features ------------------------------------------------------------------------------------------

def test_route_with_providers_on(ws, inproc_client):
    r = inproc_client(ws).get("/api/backend/features")
    assert r.status_code == 200
    assert r.json() == {"features": SMS_PROCESS}


def test_route_with_providers_off(ws, providers_off, inproc_client):
    r = inproc_client(ws).get("/api/backend/features")
    assert r.status_code == 200
    assert r.json() == {"features": []}


def test_sms_provider_declares_only_what_no_facet_implies():
    sms = pytest.importorskip("vivarium_workbench_sms.provider")
    assert sms.PROVIDER.features() == frozenset({"sms-launch"})


def test_process_features_are_the_facets_plus_the_providers_own():
    assert bf.process_features() == frozenset(SMS_PROCESS)


def test_run_features_by_kind(providers_off):
    assert bf.run_features("sms-legacy") == frozenset()  # no provider serves it now
    assert bf.run_features("compose-api") == frozenset()
    assert bf.run_features(None) == frozenset()


def test_run_features_by_kind_providers_on():
    assert bf.run_features("sms-legacy") == frozenset(SMS_RUN)
    assert bf.run_features("viva-core") == frozenset()
    assert bf.run_features("compose-api") == frozenset()
    assert bf.run_features("no-such-kind") == frozenset()


def test_read_only_mode_keeps_the_route(ws, monkeypatch, inproc_client):
    monkeypatch.setenv("VIVARIUM_WORKBENCH_READONLY", "1")
    r = inproc_client(ws).get("/api/backend/features")
    assert r.status_code == 200
    assert r.json() == {"features": SMS_PROCESS}


def test_the_route_is_in_the_openapi_schema(ws, inproc_client):
    from vivarium_workbench.api.app import create_app

    paths = create_app().openapi()["paths"]
    assert set(paths["/api/backend/features"]) == {"get"}


# --- each run's features ---------------------------------------------------------------------------------------------

def test_rows_carry_their_runs_features(ws, listing, inproc_client):
    body = inproc_client(ws).get("/api/simulations").json()
    assert _features_by_run(body["simulations"]) == EXPECTED_ON


def test_the_library_build_annotates_every_remote_row(ws, listing):
    data = si.build_simulations_data(ws)
    assert _features_by_run(data["simulations"]) == EXPECTED_ON
    # The listing's own rows are not mutated (a copy of remote_origin is annotated).
    assert "features" not in LISTED[0]["remote_origin"]


def test_the_cached_build_annotates_too(ws, listing):
    data = si.build_simulations_data_cached(ws)
    assert _features_by_run(data["simulations"]) == EXPECTED_ON
    assert si.build_simulations_data_cached(ws) is data  # served from the cache, still annotated


def test_include_remote_false_still_annotates_the_local_rows(ws, listing, inproc_client):
    body = inproc_client(ws).get("/api/simulations?include_remote=false").json()
    expected = {k: v for k, v in EXPECTED_ON.items() if not k.startswith("listed-")}
    assert _features_by_run(body["simulations"]) == expected


def test_rows_with_providers_off_have_no_features(ws, providers_off, inproc_client):
    body = inproc_client(ws).get("/api/simulations").json()
    assert _features_by_run(body["simulations"]) == {
        k: (None if v is None else []) for k, v in EXPECTED_ON.items() if not k.startswith("listed-")}


def test_no_blocking_reads(ws, listing, monkeypatch, inproc_client):
    """#1106: neither answer may consult the active backend, a capability profile or a run record."""
    def boom(*a, **k):
        raise AssertionError("a blocking read")

    monkeypatch.setattr(backends, "active", boom)
    monkeypatch.setattr(backends, "select", boom)
    monkeypatch.setattr(backends, "named", boom)
    monkeypatch.setattr("vivarium_workbench.lib.server_capabilities.explicit_backend_profile", boom)
    monkeypatch.setattr("vivarium_workbench.lib.backends.records.resolve_existing", boom)
    client = inproc_client(ws)
    r = client.get("/api/backend/features")
    assert r.status_code == 200 and r.json() == {"features": SMS_PROCESS}
    s = client.get("/api/simulations")
    assert s.status_code == 200
    assert _features_by_run(s.json()["simulations"]) == EXPECTED_ON


# --- plugin absent ---------------------------------------------------------------------------------------------------

_ABSENT = textwrap.dedent('''
    import json, pathlib, sys, tempfile

    class _NoPlugin:
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "vivarium_workbench_sms" or fullname.startswith("vivarium_workbench_sms."):
                raise ModuleNotFoundError(f"No module named {fullname!r}", name=fullname)
            return None

    sys.meta_path.insert(0, _NoPlugin())
    from fastapi.testclient import TestClient
    from vivarium_workbench.api.app import create_app, get_workspace
    from vivarium_workbench.lib import _root, run_log

    ws = pathlib.Path(tempfile.mkdtemp())
    (ws / "workspace.yaml").write_text("name: absent\\n")
    for rid, fields in EVENTS:
        run_log.append_run_event(ws, {"run_id": rid, "event": "started", **fields})
    _root.set_workspace_root(ws)
    app = create_app()
    app.dependency_overrides[get_workspace] = lambda: ws
    c = TestClient(app, base_url="http://127.0.0.1")
    f = c.get("/api/backend/features")
    s = c.get("/api/simulations")
    rows = {r["run_id"]: (r["remote_origin"] or {}).get("features") if r["remote_origin"] else None
            for r in s.json()["simulations"]}
    print("@@RESULT@@" + json.dumps({"status": [f.status_code, s.status_code], "features": f.json(), "rows": rows,
                                     "plugin": any(m.startswith("vivarium_workbench_sms") for m in sys.modules)}))
''')


def test_route_and_rows_without_the_plugin():
    code = f"EVENTS = {EVENTS!r}\n" + _ABSENT
    env = {k: v for k, v in os.environ.items() if k != provider_mod.PROVIDERS_ENV}
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=600, env=env)
    marker = [ln for ln in proc.stdout.splitlines() if ln.startswith("@@RESULT@@")]
    assert marker, f"probe crashed (exit {proc.returncode}):\n{proc.stdout[-3000:]}\n{proc.stderr[-3000:]}"
    result = json.loads(marker[-1][len("@@RESULT@@"):])
    assert result["status"] == [200, 200]
    assert result["features"] == {"features": []}
    assert result["rows"] == {k: (None if v is None else []) for k, v in EXPECTED_ON.items()
                              if not k.startswith("listed-")}
    assert result["plugin"] is False


# --- the browser asset -----------------------------------------------------------------------------------------------

def _script_pos(html: str, name: str) -> int:
    m = re.search(r'<script[^>]*\bsrc="[^"]*/?' + re.escape(name) + r'[?"{]', html)
    assert m, f"{name} not loaded"
    return m.start()


def _write_study(ws: Path, slug: str) -> None:
    sd = ws / "studies" / slug
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    spec = {
        "schema_version": 3, "name": slug, "objective": "test", "status": "in_progress",
        "baseline": [{"name": "core", "composite": "pkg.composites.core", "params": {}}],
        "variants": [], "runs": [],
    }
    (sd / "study.yaml").write_text(yaml.safe_dump(spec, sort_keys=False))


def test_the_page_shells_load_it_after_data_source(tmp_path, inproc_client):
    ws = tmp_path / "ws"
    _write_study(ws, "demo")
    client = inproc_client(ws)
    study = client.get("/studies/demo").text
    assert _script_pos(study, "data-source.js") < _script_pos(study, "backend-features.js")
    js = client.get("/backend-features.js")
    assert js.status_code == 200 and "window.BackendFeatures" in js.text
    from vivarium_workbench.lib.static_serving import STATIC_DIR

    tpl = (STATIC_DIR.parent / "templates" / "index.html.j2").read_text(encoding="utf-8")
    assert _script_pos(tpl, "data-source.js") < _script_pos(tpl, "backend-features.js")
    # The snapshot switch publish.py rewrites is untouched.
    assert 'window.__DASH_CONFIG__ = { mode: "local-server" };' in tpl


_NODE_PROBE = textwrap.dedent('''
    const fs = require('fs'); const vm = require('vm');
    const [asset, cfg] = process.argv.slice(1);  // node -e: argv[1] is the first argument
    const calls = [];
    const win = { fetch: (u) => { calls.push(u); return Promise.reject(new Error('no server')); } };
    win.window = win;
    vm.createContext(win);
    vm.runInContext(cfg, win);
    vm.runInContext(fs.readFileSync(asset, 'utf8'), win);
    win.BackendFeatures.ready().then((r) => console.log(JSON.stringify({ r, calls })));
''')


def test_the_published_bundle_ships_it_and_makes_no_request(tmp_path):
    from vivarium_workbench import publish

    ws = tmp_path / "ws"
    _write_study(ws, "demo")
    out = tmp_path / "bundle"
    publish.build_bundle(ws, out)
    asset = out / "assets" / "backend-features.js"
    assert asset.is_file()
    for page in (out / "index.html", out / "studies" / "demo" / "index.html"):
        html = page.read_text(encoding="utf-8")
        assert 'src="/assets/backend-features.js' in html or 'src="assets/backend-features.js' in html, page
        assert _script_pos(html, "data-source.js") < _script_pos(html, "backend-features.js")
        m = re.search(r"window\.__DASH_CONFIG__ = (\{[^<]*?\});", html)
        assert m and '"snapshot"' in m.group(1), page
        if shutil.which("node") is None:
            continue
        proc = subprocess.run(["node", "-e", _NODE_PROBE, str(asset), m.group(0)],
                              capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr
        assert json.loads(proc.stdout) == {"r": {"features": [], "known": True}, "calls": []}
