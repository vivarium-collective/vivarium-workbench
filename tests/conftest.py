"""Test config for vivarium-dashboard.

The dashboard package itself is import-able from the venv (``pip install -e .``)
so we don't need to munge sys.path for ``vivarium_workbench.*``. We do need
the fixture workspaces (``_fixtures/<name>/<pbg_pkg>``) on sys.path for
end-to-end tests that import the workspace's own package.
"""
from __future__ import annotations
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

_FIXTURES = Path(__file__).parent / "_fixtures"
for fixture_ws in _FIXTURES.iterdir() if _FIXTURES.is_dir() else []:
    if fixture_ws.is_dir() and (fixture_ws / "workspace.yaml").exists():
        p = str(fixture_ws)
        if p not in sys.path:
            sys.path.insert(0, p)

# ---------------------------------------------------------------------------
# Global catalog isolation
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def _isolate_viva_home(tmp_path_factory):
    """Point the GLOBAL workspace catalog at a temp dir for the whole run.

    ``cli serve`` calls ``workspace_catalog.add(workspace)`` on every boot so the
    workspace shows up in other dashboards' switchers. That add is idempotent by
    PATH — and every test workspace is a fresh ``tmp_path``, so each spawned
    server appended a new entry to the developer's REAL ``~/.pbg/workspaces.json``.
    ``dashboard_client`` spawns one per test across 34 files, and nothing ever
    removed them: an observed catalog had **9,116 entries, 9,113 of them dead
    pytest temp dirs**, dating back months. The switcher dropdown reads this file,
    so the leak eventually made the real UI unusable.

    ``viva_superpowers.workspace_catalog`` resolves its home as
    ``$VIVA_HOME or $PBG_HOME or ~/.pbg``, so setting an env var is the supported
    way to redirect it. Set in ``os.environ`` (not via monkeypatch, which is
    function-scoped) because ``dashboard_client`` passes ``os.environ.copy()`` to
    the server subprocess — the actual writer.

    Uses ``VIVA_HOME``, the canonical name, and every test that redirects the
    catalog for itself uses the same key — so an override is a plain replacement
    and precedence never enters into it. (``PBG_HOME`` is the deprecated alias and
    is intentionally unused in tests: a session default on one variable and
    per-test overrides on the *other* would mean the higher-precedence one wins
    regardless of who set it last.)

    Session-scoped rather than per-test to preserve today's behaviour exactly: one
    catalog accumulating across the run, just not in the user's home.
    """
    home = tmp_path_factory.mktemp("viva_home")
    previous = os.environ.get("VIVA_HOME")
    os.environ["VIVA_HOME"] = str(home)
    try:
        yield home
    finally:
        if previous is None:
            os.environ.pop("VIVA_HOME", None)
        else:
            os.environ["VIVA_HOME"] = previous


# ---------------------------------------------------------------------------
# Live-deployment isolation
# ---------------------------------------------------------------------------

#: Where an unconfigured viva-api client is pointed for the whole test run.
#: Port 1 is not bindable by an ordinary process, so a connection there is
#: refused immediately and deterministically — the same failure CI already
#: gets, just no longer contingent on what happens to be listening.
UNREACHABLE_VIVA_API_BASE = "http://127.0.0.1:1"


@pytest.fixture(scope="session", autouse=True)
def _isolate_viva_api_base():
    """Point the viva-api client at a closed port for the whole run.

    ``workspace_deps_views._sms_api_base()`` falls back to
    ``http://localhost:8080`` when neither ``VIVA_API_BASE`` nor
    ``SMS_API_BASE`` is set — and **8080 is exactly where a developer's SSM
    tunnel to the live dev deployment listens** (``sms-proxy.sh -s smsvpctest``,
    the documented way to reach it). So on a laptop with the tunnel up, any
    test that resolves a build talked to the real ``sms-api-stanford-test``.

    That is how ``test_study_run_baseline_pinned_deployment_409_over_real_http``
    came to fail locally while CI stayed green: its spawned server resolved a
    **real** simulator from the live deployment instead of degrading to "no
    remote build resolved", so the response was ``400 num_generations is
    required`` from a stage the test never meant to reach. The test's docstring
    had recorded "this test's spawned subprocess has no real sms-api to reach"
    as a property of the test; it was really a property of the machine.

    Read the near-miss, because it is the actual reason this fixture exists
    rather than a one-line fix in that test: the only thing that stopped the
    run from being **dispatched to real AWS Batch** was that this study
    declares no ``n_generations``/``n_seeds``, so ``remote_run_submit``
    refused at its own guard. A fixture study carrying those two knobs would
    have submitted a real, billable campaign from a unit test.

    Set in ``os.environ`` rather than via monkeypatch for the same reason as
    :func:`_isolate_viva_home`: ``dashboard_client`` hands
    ``os.environ.copy()`` to the server subprocess, which is the process that
    actually makes these calls. Both names are set because ``_sms_api_base``
    reads ``VIVA_API_BASE`` first and ``SMS_API_BASE`` as the legacy alias —
    setting only one leaves the other free to point somewhere real.

    Tests that want a specific base still ``monkeypatch.setenv`` it (function
    scope wins over this); tests that want the *unconfigured* default must
    delete **both** names, which is what "unconfigured" has always meant.
    """
    previous = {k: os.environ.get(k) for k in ("VIVA_API_BASE", "SMS_API_BASE")}
    for k in previous:
        os.environ[k] = UNREACHABLE_VIVA_API_BASE
    # The explicit name (``serve --backend-base-url``) outranks both aliases and opts a
    # process into running on that backend: a developer's exported value must never leak
    # into a test run, and tests that want it set it themselves (monkeypatch).
    previous["VIVARIUM_WORKBENCH_BACKEND_BASE_URL"] = os.environ.pop(
        "VIVARIUM_WORKBENCH_BACKEND_BASE_URL", None)
    try:
        yield UNREACHABLE_VIVA_API_BASE
    finally:
        for k, v in previous.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ---------------------------------------------------------------------------
# Shared dashboard_client fixture
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class _Response:
    def __init__(self, status_code: int, body: bytes, headers=None):
        self.status_code = status_code
        self._body = body
        # Case-insensitive header map (keys lowercased) so tests can assert
        # Content-Type / Content-Disposition regardless of the server's casing.
        self.headers = {str(k).lower(): v for k, v in (headers or {}).items()}

    def json(self):
        return json.loads(self._body.decode())

    @property
    def text(self):
        return self._body.decode()


class _Client:
    def __init__(self, base_url: str):
        self.base_url = base_url

    def _request(self, method: str, path: str, *, json_body=None):
        import urllib.request
        import urllib.error
        url = self.base_url + path
        data = json.dumps(json_body).encode() if json_body is not None else None
        headers = {"Content-Type": "application/json"} if data else {}
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return _Response(r.status, r.read(), headers=r.headers)
        except urllib.error.HTTPError as e:
            return _Response(e.code, e.read(), headers=e.headers)

    def get(self, path: str):
        return self._request("GET", path)

    def post(self, path: str, json=None):
        return self._request("POST", path, json_body=json)

    def delete(self, path: str, json=None):
        return self._request("DELETE", path, json_body=json)


@pytest.fixture
def dashboard_client():
    """Factory: dashboard_client(workspace=path) -> _Client.

    Spawns a subprocess server against the given workspace and tears it
    down at the end of the test.  Future endpoint tests (Tasks 8-11) reuse
    this fixture via conftest.py.
    """
    procs = []

    def _make(workspace: Path) -> _Client:
        workspace = Path(workspace)
        port = _free_port()
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join(
            [str(_REPO_ROOT), str(workspace), env.get("PYTHONPATH", "")])
        # Spawn the live FastAPI app (via the `serve` CLI -> startup.serve_fastapi,
        # which writes the .pbg/server/server-info readiness file this fixture waits
        # on). This exercises the production server, not the retired stdlib server.py.
        proc = subprocess.Popen(
            [sys.executable, "-m", "vivarium_workbench.cli", "serve",
             "--workspace", str(workspace), "--port", str(port)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env,
        )
        procs.append(proc)
        client = _Client(f"http://127.0.0.1:{port}")
        # serve_fastapi writes server-info before uvicorn binds the port, so wait
        # for the app to actually answer /health — not just for the file to exist.
        # 240 * 0.25s = 60s: CI cold-start + generator discovery can exceed the
        # old 15s budget, which caused flaky server-fixture TimeoutErrors.
        for _ in range(240):
            if proc.poll() is not None:  # process died during startup
                out, err = proc.communicate(timeout=2)
                pytest.fail(
                    f"server exited during startup (code {proc.returncode}):\n"
                    f"stdout:\n{out.decode()}\nstderr:\n{err.decode()}"
                )
            try:
                if client.get("/health").status_code == 200:
                    break
            except Exception:
                pass
            time.sleep(0.25)
        else:
            proc.terminate()
            out, err = proc.communicate(timeout=2)
            pytest.fail(
                f"server did not answer /health within 15s:\n"
                f"stdout:\n{out.decode()}\nstderr:\n{err.decode()}"
            )
        return client

    yield _make

    for p in procs:
        p.terminate()
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()


# ---------------------------------------------------------------------------
# Minimal workspace fixture for study-run tests (dry-run guard, CLI, etc.)
# ---------------------------------------------------------------------------

@pytest.fixture
def fixture_study_ws(tmp_path):
    """Return (ws_path, study_slug) for a minimal workspace with one baseline study.

    The workspace has:
      - workspace.yaml  (name + package_path)
      - studies/<slug>/study.yaml  (v4 schema, conditions.baseline.composite,
                                    params: {n_steps: 5}, one variant)
    The composite id is intentionally un-registered (tests that use dry_run
    never reach composite resolution; tests that need resolution must mock it).
    """
    import yaml as _yaml

    ws = tmp_path / "test_ws"
    slug = "demo-study"
    pkg = "pbg_demo"
    composite_id = f"{pkg}.composites.demo"

    # workspace.yaml
    (ws).mkdir(parents=True)
    (ws / "workspace.yaml").write_text(
        _yaml.safe_dump({"name": "demo", "package_path": pkg}),
        encoding="utf-8",
    )

    # studies/<slug>/study.yaml  (v4 shape with conditions block)
    study_dir = ws / "studies" / slug
    study_dir.mkdir(parents=True)
    (study_dir / "study.yaml").write_text(
        _yaml.safe_dump({
            "schema_version": 4,
            "name": slug,
            "question": "Does the demo composite run correctly?",
            "conditions": {
                "baseline": {
                    "composite": composite_id,
                    "params": {"n_steps": 5},
                },
                "variants": [
                    {
                        "name": "var-one",
                        "composite": composite_id,
                        "parameter_overrides": {"n_steps": 10},
                    }
                ],
            },
        }),
        encoding="utf-8",
    )

    return ws, slug


@pytest.fixture
def fixture_study_with_recorded_run(fixture_study_ws):
    """Return (ws_path, study_slug, run_id) with a recorded run in runs.db.

    Builds on fixture_study_ws and seeds the study's runs.db with one
    completed run via composite_runs helpers so find_run / list_study_runs
    can locate it without spinning up a real simulation.
    """
    import time
    from vivarium_workbench.lib import composite_runs as cr

    ws, slug = fixture_study_ws
    study_dir = ws / "studies" / slug
    db_file = str(study_dir / "runs.db")

    spec_id = "pbg_demo.composites.demo"
    run_id = cr.generate_run_id(spec_id, {"seed": 42})
    conn = cr.connect(db_file)
    try:
        cr.save_metadata(
            conn,
            spec_id=spec_id,
            run_id=run_id,
            params={"seed": 42},
            label="baseline",
            started_at=time.time(),
            n_steps=5,
        )
        cr.complete_metadata(conn, run_id=run_id, n_steps=5, status="complete")
    finally:
        conn.close()

    return ws, slug, run_id


# ---------------------------------------------------------------------------
# pbg-superpowers generator registry
# ---------------------------------------------------------------------------
# `process_bigraph.composite_generator._REGISTRY` used to be a plain dict. As of
# pbg-superpowers #168 ("shim composite front-ends onto process-bigraph
# CompositeSpec") it is a *view* over process-bigraph's global registry:
#
#   * assignment CONVERTS the value into a CompositeSpec, reading .name,
#     .description, .parameters, … — so a bare `object()` or a partial
#     SimpleNamespace no longer works as a dummy entry; and
#   * there is no `__delitem__`, so `monkeypatch.setitem(cg._REGISTRY, ...)`
#     raises AttributeError during teardown.
#
# Registration is also process-global now, so a test that registers a fake
# generator leaks it into every later test unless the backing registry is
# restored. `register_generator` handles the conversion; the autouse fixture
# below handles the restore.

def register_generator(spec_id, entry=None, **fields):
    """Register a generator under ``spec_id``, filling in required fields.

    Accepts a partial stand-in (anything with some of the GeneratorEntry
    attributes, including a bare ``object()``) so existing tests can keep
    expressing "just put *something* in the registry" without knowing the
    current CompositeSpec shape.
    """
    from process_bigraph.composite_generator import GeneratorEntry, _REGISTRY

    # CompositeSpec requires exactly one of `state` or `builder`, so `func`
    # must be callable even for a pure placeholder entry. Tests that care about
    # what building produces stub `build_generator` anyway.
    base = dict(id=spec_id, name=spec_id, description="", parameters={},
                func=lambda **kwargs: {}, module="", default_n_steps=None,
                visualizations=[], emitters=[], core_extensions=[])
    if entry is not None:
        for key in list(base):
            value = getattr(entry, key, None)
            if value is not None:
                base[key] = value
    base.update(fields)
    base["id"] = spec_id
    if not base.get("name"):
        base["name"] = spec_id          # CompositeSpec rejects an empty name
    _REGISTRY[spec_id] = GeneratorEntry(**base)
    return spec_id


@pytest.fixture(autouse=True)
def _restore_composite_spec_registry():
    """Snapshot/restore process-bigraph's global composite-spec registry.

    Autouse because registration is process-global: without this a single test
    that registers a fake generator changes what every subsequent test sees,
    and the resulting failures are order-dependent and miserable to trace.

    Restores only when the registry actually changed, so the common case costs
    one dict copy and a comparison.
    """
    try:
        from process_bigraph import composite_spec as cs
    except ImportError:
        yield
        return
    before = dict(cs.all_specs())
    yield
    after = cs.all_specs()
    if (after.keys() != before.keys()
            or any(after.get(k) is not v for k, v in before.items())):
        cs.clear_registry()
        for spec in before.values():
            cs.register(spec)
