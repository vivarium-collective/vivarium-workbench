"""The bare-id audit (step P1c, closed by P3 I1): with providers off, an integer run id that no record names reaches no
backend. Every caller of the existing-run router (``backends.for_existing`` and its wrappers) is listed here, each with
its providers-off answer to ``12``: the terminal 409 ``backend-not-installed``, or a documented absence.

``test_every_router_caller_is_audited`` keeps the table honest: a new caller of the router in the core fails it until
it is added here with its answer.

Core-only compliant: the plugin is never imported at module level (when it is installed, its SMS client is a tripwire).
"""
from __future__ import annotations

import ast
from pathlib import Path
from urllib.parse import urlencode

import pytest

from vivarium_workbench.lib import backends
from vivarium_workbench.lib.backends import provider as provider_mod

CORE = Path(__file__).resolve().parent.parent / "vivarium_workbench"
REFUSED = (409, "backend-not-installed", "backend-not-installed", True)

#: (label, method, path, payload). Each is one caller of the router, reached through its route with the bare id 12.
ROUTED = [
    ("status", "get", "/api/remote-run-poll", {"simulation_id": 12}),
    ("chain-progress", "get", "/api/remote-run-chain-progress", {"simulation_id": 12}),
    ("cancel", "post", "/api/remote-run-cancel", {"simulation_id": 12}),
    ("land", "post", "/api/remote-run-land", {"study": "s", "simulation_id": 12}),
    ("trace", "get", "/api/remote-run-trace", {"simulation_id": 12}),
    ("figures", "get", "/api/remote-analysis-figures", {"simulation_id": 12, "study": "s"}),
    ("figure", "get", "/api/remote-analysis-figure",
     {"simulation_id": 12, "analysis": "a", "path": "viz/x.png", "study": "s"}),
    ("re-run", "post", "/api/remote-run-analysis", {"simulation_id": 12}),
    ("land-artifacts", "post", "/api/remote-run-land-artifacts", {"simulation_id": 12, "run_id": "r"}),
    ("composite-run status", "get", "/api/composite-run/remote-sim-12/status", {}),
]

#: Every core function that calls the router directly, and the row (above) or documented absence that covers it.
#: ``composite_run_views.build_composite_run_status`` reaches it through ``remote_run_status`` (row "composite-run
#: status"). The remote-runs listing (``listing`` facet) never routes an existing id: with providers off nothing
#: offers it, so no remote row is listed.
CALLERS = {
    "lib/remote_run_views.py:remote_run_status": "status",
    "lib/remote_run_views.py:remote_run_chain_progress": "chain-progress",
    "lib/remote_run_views.py:remote_run_cancel": "cancel",
    "lib/remote_run_views.py:remote_run_land": "land",
    "lib/remote_trace.py:fetch_trace": "trace",
    "lib/remote_run_views.py:analysis_figures": "figures",
    "lib/remote_run_views.py:analysis_figure": "figure",
    "lib/remote_run_views.py:remote_run_analysis": "re-run",
    "lib/remote_run_views.py:remote_run_land_artifacts": "land-artifacts",
    # Absent: the store fetch answers (None, None) -- no store, nothing asked (test below); the route that reads it
    # then answers its own 404.
    "lib/simulations_index.py:resolve_or_fetch_store": "absent: store fetch",
}

#: The router and the wrappers whose callers are what is audited.
_ROUTERS = {"for_existing", "resolve_existing", "_owner", "_artifacts_of", "_analyses_of"}
_WRAPPERS = {"lib/backends/__init__.py:resolve", "lib/backends/__init__.py:for_existing",
             "lib/remote_run_views.py:_owner", "lib/remote_run_views.py:_artifacts_of",
             "lib/remote_run_views.py:_analyses_of"}


def _router_callers() -> "set[str]":
    found: set[str] = set()
    for path in CORE.rglob("*.py"):
        rel = path.relative_to(CORE).as_posix()
        for top in ast.parse(path.read_text(encoding="utf-8")).body:
            if not isinstance(top, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            for node in ast.walk(top):
                if not isinstance(node, ast.Call):
                    continue
                f = node.func
                name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else None
                via_resolve = (name == "resolve" and isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                               and f.value.id == "backends")
                if name in _ROUTERS or via_resolve:
                    found.add(f"{rel}:{top.name}")
    return found - _WRAPPERS


def test_every_router_caller_is_audited():
    assert _router_callers() == set(CALLERS)
    rows = {label for label, *_ in ROUTED}
    assert {v for v in CALLERS.values() if not v.startswith("absent:")} <= rows


@pytest.fixture
def providers_off(monkeypatch, tmp_path):
    monkeypatch.setenv(provider_mod.PROVIDERS_ENV, "none")
    backends.reset_registry()
    from vivarium_workbench.lib import server_capabilities as sc

    monkeypatch.setattr(sc, "explicit_backend_profile", lambda: None)
    try:  # the plugin, when installed, must not be reached
        from vivarium_workbench_sms import sms_api_client, sms_legacy
    except ImportError:
        pass
    else:
        monkeypatch.setattr(sms_legacy, "_run_auth_ok", lambda: pytest.fail("the SMS login check was consulted"))
        monkeypatch.setattr(sms_legacy, "SmsApiClient", lambda *a, **k: pytest.fail("SMS was asked"))
        monkeypatch.setattr(sms_api_client.SmsApiClient, "__init__", lambda *a, **k: pytest.fail("SMS was asked"))
    (tmp_path / "workspace.yaml").write_text("name: ws\n", encoding="utf-8")
    (tmp_path / "studies" / "s").mkdir(parents=True)
    yield tmp_path
    monkeypatch.delenv(provider_mod.PROVIDERS_ENV, raising=False)
    backends.reset_registry()


@pytest.mark.parametrize("label, method, path, payload", ROUTED, ids=[r[0] for r in ROUTED])
def test_a_bare_integer_id_is_refused_backend_not_installed(providers_off, inproc_client,
                                                            label, method, path, payload):
    client = inproc_client(providers_off)
    query = f"?{urlencode(payload)}" if payload else ""
    r = client.get(path + query) if method == "get" else client.post(path, json=payload)
    body = r.json()
    assert (r.status_code, body.get("reason"), body.get("phase"), body.get("terminal")) == REFUSED, (label, body)
    assert str(body["simulation_id"]) == "12"


def test_the_store_fetch_of_a_bare_integer_id_is_absent(providers_off):
    from vivarium_workbench.lib import simulations_index as si

    row = {"study_slug": "s", "remote_origin": {"simulation_id": 12, "deployment": "smsvpctest"}}
    assert si.resolve_or_fetch_store(providers_off, row) == (None, None)
