"""Goldens for the backend configuration step P3 C1 moves (docs/backend-adapters.md §2.3), recorded on the base.

For each environment of the matrix and each mode (providers on, providers off): the one-backend check (outcome and
message), the configured URL, the deployment name, the ``/api/source/remote-health`` body (and the URL it pinged),
and what ``serve`` prints at start-up (and the URL its breaker probe watches). Providers on must not change at all;
the providers-off changes are declared below, row by row.

No network: ``backend_config.ping``, the capability probe and the breaker probe are faked. The plugin is reached only
through the provider registry (no module-level plugin import), so the providers-off rows also hold without it.
"""
from __future__ import annotations

import pytest

from vivarium_workbench.lib import backends
from vivarium_workbench.lib.backends import provider as provider_mod

NAMED, VIVA, SMS = "VIVARIUM_WORKBENCH_BACKEND_BASE_URL", "VIVA_API_BASE", "SMS_API_BASE"
DEPLOY = "VIVARIUM_WORKBENCH_REMOTE_DEPLOYMENT"
OLD_NAMED, OLD_DEPLOY = "VIVARIUM_DASHBOARD_BACKEND_BASE_URL", "VIVARIUM_DASHBOARD_REMOTE_DEPLOYMENT"
_ALL = (NAMED, VIVA, SMS, DEPLOY, OLD_NAMED, OLD_DEPLOY)

ENVS = {
    "nothing": {},
    "named": {NAMED: "http://compose.test"},
    "sms-alias": {SMS: "http://api:8000"},
    "viva-alias": {VIVA: "http://viva.test:9000"},
    "both-aliases": {VIVA: "http://a.test", SMS: "http://b.test"},
    "named+same-alias": {NAMED: "http://API:8000/", SMS: "http://api:8000"},
    "named+other-alias": {NAMED: "http://compose.test", SMS: "http://api:8000"},
    "named+bad-alias": {NAMED: "http://compose.test", VIVA: "not a url"},
    "bad-named": {NAMED: "not a url"},
    "deployment+alias": {DEPLOY: "smscdk", SMS: "http://api:8000"},
    "dashboard-named": {OLD_NAMED: "http://dash.test"},  # the old prefix never named a backend
    "dashboard-deployment": {OLD_DEPLOY: "olddep"},      # ... but it does name the deployment (env_compat)
}


def _matrix(on: dict, off: dict) -> list:
    """Each case in both modes, case-major ids (``named-on``). An ``on`` case needs the plugin (``plugin`` mark: strict
    xfail when it is absent) unless its golden is the same as with providers off -- the plugin then changes nothing."""
    return [pytest.param(case, mode, id=f"{case}-{mode}",
                         marks=pytest.mark.plugin if mode == "on" and on[case] != off[case] else ())
            for case in on for mode in ("on", "off")]


@pytest.fixture
def env(monkeypatch):
    for v in _ALL:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.delenv(provider_mod.PROVIDERS_ENV, raising=False)
    backends.reset_registry()

    def apply(case: str, mode: str) -> None:
        for k, v in ENVS[case].items():
            monkeypatch.setenv(k, v)
        if mode == "off":
            monkeypatch.setenv(provider_mod.PROVIDERS_ENV, "none")
        backends.reset_registry()

    yield apply
    monkeypatch.delenv(provider_mod.PROVIDERS_ENV, raising=False)
    backends.reset_registry()


# --- the entry points (what the core calls) --------------------------------------------------------------------------

def _check() -> str:
    from vivarium_workbench.lib.backend_config import BackendConfigError
    from vivarium_workbench.lib.backends import config

    try:
        config.check_config()
    except BackendConfigError as e:
        return f"refused: {e}"
    return "ok"


def _url():
    from vivarium_workbench.lib.backends import config

    return config.configured_base_url()


def _deployment() -> str:
    from vivarium_workbench.lib.backends import config

    return config.deployment_name()


# --- the matrix ------------------------------------------------------------------------------------------------------

REFUSED_OTHER = ("refused: two remote backends are configured: the named backend http://compose.test and "
                 "SMS_API_BASE=http://api:8000. A workbench talks to one backend; unset SMS_API_BASE or name the "
                 "same URL.")
REFUSED_BAD_ALIAS = ("refused: VIVA_API_BASE: backend base URL must be http(s)://host[:port][/prefix], got "
                     "'not a url' (a backend is also named: http://compose.test)")
REFUSED_BAD_NAMED = ("refused: VIVARIUM_WORKBENCH_BACKEND_BASE_URL: backend base URL must be "
                     "http(s)://host[:port][/prefix], got 'not a url'")

# case -> (check, configured URL, deployment name)
ON = {
    "nothing": ("ok", "http://localhost:8080", "smsvpctest"),
    "named": ("ok", "http://compose.test", "compose.test"),
    "sms-alias": ("ok", "http://api:8000", "smsvpctest"),
    "viva-alias": ("ok", "http://viva.test:9000", "smsvpctest"),
    "both-aliases": ("ok", "http://a.test", "smsvpctest"),
    "named+same-alias": ("ok", "http://API:8000/", "api"),
    "named+other-alias": (REFUSED_OTHER, "http://compose.test", "compose.test"),
    "named+bad-alias": (REFUSED_BAD_ALIAS, "http://compose.test", "compose.test"),
    "bad-named": (REFUSED_BAD_NAMED, "not a url", "smsvpctest"),
    "deployment+alias": ("ok", "http://api:8000", "smscdk"),
    "dashboard-named": ("ok", "http://localhost:8080", "smsvpctest"),
    "dashboard-deployment": ("ok", "http://localhost:8080", "olddep"),
}
# DECLARED (P3 C1), providers off: an alias is ignored (no URL, no refusal beside a named backend), there is no
# tunnel default, and the deployment default is the core's neutral one.
OFF = {
    "nothing": ("ok", None, "default"),
    "named": ("ok", "http://compose.test", "compose.test"),
    "sms-alias": ("ok", None, "default"),
    "viva-alias": ("ok", None, "default"),
    "both-aliases": ("ok", None, "default"),
    "named+same-alias": ("ok", "http://API:8000/", "api"),
    "named+other-alias": ("ok", "http://compose.test", "compose.test"),
    "named+bad-alias": ("ok", "http://compose.test", "compose.test"),
    "bad-named": (REFUSED_BAD_NAMED, "not a url", "default"),
    "deployment+alias": ("ok", None, "smscdk"),
    "dashboard-named": ("ok", None, "default"),
    "dashboard-deployment": ("ok", None, "olddep"),
}


@pytest.mark.parametrize(("case", "mode"), _matrix(ON, OFF))
def test_check_url_and_deployment(env, case, mode):
    env(case, mode)
    assert (_check(), _url(), _deployment()) == (ON if mode == "on" else OFF)[case]


# --- /api/source/remote-health ----------------------------------------------------------------------------------------

def _health_ok(url):
    return {"configured": True, "base_url": url, "reachable": True, "version": "1.2.3", "error": None}


# case -> (body, pinged)
HEALTH_ON = {
    "nothing": ({**_health_ok("http://localhost:8080"), "configured": False}, ["http://localhost:8080"]),
    "named": (_health_ok("http://compose.test"), ["http://compose.test"]),
    "sms-alias": (_health_ok("http://api:8000"), ["http://api:8000"]),
    "viva-alias": (_health_ok("http://viva.test:9000"), ["http://viva.test:9000"]),
    "both-aliases": (_health_ok("http://a.test"), ["http://a.test"]),
    "dashboard-named": ({**_health_ok("http://localhost:8080"), "configured": False}, ["http://localhost:8080"]),
}
# DECLARED (P3 C1), providers off: with no backend named there is no URL to ask: nothing is pinged.
_NONE = {"configured": False, "base_url": None, "reachable": False, "version": None,
         "error": "no remote backend is configured"}
HEALTH_OFF = {
    "nothing": (_NONE, []),
    "named": HEALTH_ON["named"],
    "sms-alias": (_NONE, []),
    "viva-alias": (_NONE, []),
    "both-aliases": (_NONE, []),
    "dashboard-named": (_NONE, []),
}


@pytest.mark.parametrize(("case", "mode"), _matrix(HEALTH_ON, HEALTH_OFF))
def test_remote_health_body(env, case, mode, monkeypatch, tmp_path, inproc_client):
    from vivarium_workbench.lib import backend_config

    env(case, mode)
    pinged: list = []
    monkeypatch.setattr(backend_config, "ping", lambda url, *a, **k: pinged.append(url) or "1.2.3")
    (tmp_path / "workspace.yaml").write_text("name: ws\n", encoding="utf-8")
    r = inproc_client(tmp_path).get("/api/source/remote-health")
    assert r.status_code == 200
    assert (r.json(), pinged) == (HEALTH_ON if mode == "on" else HEALTH_OFF)[case]


# --- serve's start-up lines --------------------------------------------------------------------------------------------

class _Stop(BaseException):
    """Ends ``serve_fastapi`` right after the start-up probes (``except Exception`` there does not catch it)."""


def _serve(monkeypatch, tmp_path, capsys):
    from vivarium_workbench.lib import _root, backend_config, dep_doctor, server_capabilities as sc, startup

    started: list = []
    monkeypatch.setattr(backend_config, "start_probe", lambda url: started.append(url))
    monkeypatch.setattr(backend_config, "ping", lambda url, *a, **k: "1.2.3")
    monkeypatch.setattr(dep_doctor, "warn_lines", lambda: [])
    monkeypatch.setattr(sc, "_PROFILE_CACHE", {})
    monkeypatch.setattr(sc, "_probe_client", lambda base: base)
    monkeypatch.setattr(sc, "backend_profile", lambda client: {
        "reachable": True, "version": "9", "capabilities": [], "services": {}, "dispatch": "compose-api",
        "error": None})

    def stop(_ws):
        raise _Stop

    monkeypatch.setattr(_root, "set_workspace_root", stop)
    try:
        rc = startup.serve_fastapi(tmp_path, port=0)
    except _Stop:
        rc = None
    out = capsys.readouterr()
    return rc, out.out, out.err, started


_REACHABLE = "remote sms-api: {} — reachable ✓ (v1.2.3)\n"
_COMPOSE = "backend dispatch: compose-api (server v9)\n"

# case -> (return code (None: it went on to serve), stdout, stderr, breaker probe URLs)
SERVE_ON = {
    "nothing": (None, "", "", []),
    "named": (None, _COMPOSE + _REACHABLE.format("http://compose.test"), "", ["http://compose.test"]),
    "sms-alias": (None, _REACHABLE.format("http://api:8000"), "", ["http://api:8000"]),
    "viva-alias": (None, _REACHABLE.format("http://viva.test:9000"), "", ["http://viva.test:9000"]),
    "both-aliases": (None, _REACHABLE.format("http://a.test"), "", ["http://a.test"]),
    "named+same-alias": (None, _COMPOSE + _REACHABLE.format("http://API:8000/"), "", ["http://API:8000/"]),
    "named+other-alias": (2, "", "ERROR: " + REFUSED_OTHER.removeprefix("refused: ") + "\n", []),
    "named+bad-alias": (2, "", "ERROR: " + REFUSED_BAD_ALIAS.removeprefix("refused: ") + "\n", []),
    "bad-named": (2, "", "ERROR: " + REFUSED_BAD_NAMED.removeprefix("refused: ") + "\n", []),
    "deployment+alias": (None, _REACHABLE.format("http://api:8000"), "", ["http://api:8000"]),
    "dashboard-named": (None, "", "", []),
    "dashboard-deployment": (None, "", "", []),
}
# DECLARED (P3 C1), providers off: an alias is ignored with a warning (no probe, no breaker thread, no refusal
# beside a named backend); the named backend is served as with providers on.
_STRAY = "warning: {} is set but no backend provider is active; it is ignored\n"


def _with_err(row, err):
    return row[:2] + (err,) + row[3:]


SERVE_OFF = {
    "nothing": (None, "", "", []),
    "named": SERVE_ON["named"],
    "sms-alias": (None, "", _STRAY.format(SMS), []),
    "viva-alias": (None, "", _STRAY.format(VIVA), []),
    "both-aliases": (None, "", _STRAY.format(VIVA) + _STRAY.format(SMS), []),
    "named+same-alias": _with_err(SERVE_ON["named+same-alias"], _STRAY.format(SMS)),
    "named+other-alias": _with_err(SERVE_ON["named"], _STRAY.format(SMS)),
    "named+bad-alias": _with_err(SERVE_ON["named"], _STRAY.format(VIVA)),
    "bad-named": SERVE_ON["bad-named"],
    "deployment+alias": (None, "", _STRAY.format(SMS), []),
    "dashboard-named": (None, "", "", []),
    "dashboard-deployment": (None, "", "", []),
}


@pytest.mark.parametrize(("case", "mode"), _matrix(SERVE_ON, SERVE_OFF))
def test_serve_startup_lines(env, case, mode, monkeypatch, tmp_path, capsys):
    env(case, mode)
    assert _serve(monkeypatch, tmp_path, capsys) == (SERVE_ON if mode == "on" else SERVE_OFF)[case]


# --- the deployment names a pre-record row may carry (records' legacy guard) ----------------------------------------

def _names_now() -> set:
    from vivarium_workbench.lib.backends import config

    return config.deployment_names_now()


# case -> the names the current configuration could have written on a row before step 1c
NAMES_ON = {
    "nothing": {"smsvpctest"},
    "named": {"compose.test", "smsvpctest"},
    "sms-alias": {"api", "smsvpctest"},
    "viva-alias": {"viva.test", "smsvpctest"},
    "both-aliases": {"a.test", "b.test", "smsvpctest"},
    "named+same-alias": {"api", "smsvpctest"},
    "deployment+alias": {"smscdk", "api"},
    "dashboard-named": {"smsvpctest"},
    "dashboard-deployment": {"olddep", "smsvpctest"},  # the old prefix names the deployment, but is not "set" here
}
# DECLARED (P3 C1), providers off: no alias host and no provider default.
NAMES_OFF = {
    "nothing": {"default"},
    "named": {"compose.test"},
    "sms-alias": {"default"},
    "viva-alias": {"default"},
    "both-aliases": {"default"},
    "named+same-alias": {"api"},
    "deployment+alias": {"smscdk"},
    "dashboard-named": {"default"},
    "dashboard-deployment": {"olddep"},
}


@pytest.mark.parametrize(("case", "mode"), _matrix(NAMES_ON, NAMES_OFF))
def test_deployment_names_now(env, case, mode):
    env(case, mode)
    assert _names_now() == (NAMES_ON if mode == "on" else NAMES_OFF)[case]


@pytest.mark.plugin
@pytest.mark.parametrize("case", ["nothing", "named", "sms-alias"])
def test_an_smsvpctest_row_proceeds_with_providers_on(env, case, tmp_path):
    """A row written before step 1c by the historical default deployment is still this workbench's (providers on)."""
    from vivarium_workbench.lib import composite_runs as cr
    from vivarium_workbench.lib.backends import records

    env(case, "on")
    ws = tmp_path / "ws"
    (ws / "studies" / "s").mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    conn = cr.connect(ws / "studies" / "s" / "runs.db")
    try:
        cr.save_metadata(conn, spec_id="c", run_id="landed", params={"source": "smsvpctest", "simulation_id": 12, "backend": "ray"},
                         label=None, started_at=0.0, n_steps=0)
    finally:
        conn.close()
    res = records.resolve_existing(ws, "12")
    assert (res.kind, res.refusal, res.via) == ("sms-legacy", None, "legacy-guard")
