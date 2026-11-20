"""``remote_run.run_remote`` is a backend dispatcher (docs/backend-adapters.md §2.1/§2.3, step P2 Runs b).

The detached runner's deployment run goes to the backend ``backends.select`` picks (``runs.run_composite``), else to
the backend that answers unrouted requests (``unrouted.run_composite``), else it is a ``RuntimeError``. The SMS run
itself is the plugin's ``deployment_run.run``. These tests pin the dispatch table with providers on and off, the two
signatures, and the two rules that keep every test patch live (no SMS seam left in the core; the plugin reads core
names through the module).
"""
from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

import pytest

from vivarium_workbench.lib import backends, remote_run, remote_run_compose
from vivarium_workbench.lib import server_capabilities as sc
from vivarium_workbench.lib.backends import provider as provider_mod
from vivarium_workbench.lib.backend_config import BACKEND_BASE_ENV_VARS

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "vivarium_workbench_sms"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    for var in BACKEND_BASE_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
        monkeypatch.delenv(var.replace("VIVARIUM_WORKBENCH_", "VIVARIUM_DASHBOARD_"), raising=False)
    backends.reset_registry()
    yield
    backends.reset_registry()


@pytest.fixture
def providers_off(monkeypatch):
    monkeypatch.setenv(provider_mod.PROVIDERS_ENV, "none")
    backends.reset_registry()
    yield
    monkeypatch.delenv(provider_mod.PROVIDERS_ENV, raising=False)
    backends.reset_registry()


@pytest.fixture
def calls(monkeypatch):
    """Record what reaches compose-api's run and the plugin's SMS run (neither runs)."""
    from vivarium_workbench_sms import deployment_run

    seen: list = []

    def compose(*a, **k):
        seen.append(("compose", a, k))
        return Path(k["dest"]) / "results.zip"

    def sms(*a, **k):
        seen.append(("sms", a, k))
        return Path(k["dest"]) / "results.tar.gz"

    monkeypatch.setattr(remote_run_compose, "run_composite", compose)
    monkeypatch.setattr(deployment_run, "run", sms)
    return seen


def _select(monkeypatch, which: str) -> None:
    chosen = {"compose": lambda: backends.COMPOSE, "viva": lambda: backends.VIVA_CORE,
              "legacy": lambda: backends.SMS_LEGACY, "none": lambda: None}[which]()
    monkeypatch.setattr(backends, "select", lambda ws: chosen)


KW = dict(dest=None, n_steps=7, overrides={"a": 1}, emit_paths=["stores/x"], poll_interval=0.5, poll_timeout=9.0)


# --- providers on: the dispatch table -------------------------------------------------------------------------------

def test_a_named_compose_api_gets_exactly_its_arguments_and_the_analyses_note(tmp_path, monkeypatch, calls, capsys):
    _select(monkeypatch, "compose")
    out = remote_run.run_remote(tmp_path, "c", **{**KW, "dest": tmp_path / "o"},
                                analysis_options={"single": {"x": {}}}, build_ref={"commit": "abc"})
    ((who, args, kw),) = calls
    assert who == "compose" and args == (tmp_path.resolve(), "c")
    assert kw == {"dest": tmp_path / "o", "n_steps": 7, "overrides": {"a": 1}, "emit_paths": ["stores/x"],
                  "poll_interval": 0.5, "poll_timeout": 9.0}
    assert out == tmp_path / "o" / "results.zip"
    assert "compose-api does not run declared analyses; not run: single" in capsys.readouterr().out


def test_without_analyses_compose_api_prints_no_note(tmp_path, monkeypatch, calls, capsys):
    _select(monkeypatch, "compose")
    remote_run.run_remote(tmp_path, "c")
    assert "declared analyses" not in capsys.readouterr().out
    assert calls[0][2]["dest"] == tmp_path.resolve() / ".pbg" / "remote-results"


@pytest.mark.parametrize("which, via_viva_v1", [("viva", True), ("legacy", False), ("none", False)])
def test_sms_backends_get_the_plugin_run(tmp_path, monkeypatch, calls, which, via_viva_v1):
    """viva-core: a document run. A session build or pin (sms-legacy) and a deployment run nothing selected (the
    unrouted facet: the opt-in compose path) keep /compose/v1. ``analysis_options`` and ``build_ref`` are forwarded
    unread."""
    _select(monkeypatch, which)
    remote_run.run_remote(tmp_path, "c", **{**KW, "dest": tmp_path / "o"},
                          analysis_options={"single": {}}, build_ref={"commit": "abc", "repo_url": "r"})
    ((who, args, kw),) = calls
    assert who == "sms" and args == (tmp_path.resolve(), "c")
    expected = {"dest": tmp_path / "o", "n_steps": 7, "overrides": {"a": 1}, "emit_paths": ["stores/x"],
                "poll_interval": 0.5, "poll_timeout": 9.0, "analysis_options": {"single": {}},
                "build_ref": {"commit": "abc", "repo_url": "r"}}
    if via_viva_v1:
        expected["via_viva_v1"] = True
    assert kw == expected


# --- providers off ----------------------------------------------------------------------------------------------------

def test_providers_off_with_nothing_named_is_a_runtime_error_and_never_reaches_sms(providers_off, tmp_path,
                                                                                   monkeypatch):
    from vivarium_workbench_sms import sms_api_client as sac

    def no(*a, **k):
        raise AssertionError("SMS reached with providers off")

    monkeypatch.setattr(sac.SmsApiClient, "__init__", no)
    with pytest.raises(RuntimeError, match="no remote backend is named"):
        remote_run.run_remote(tmp_path, "c")


def test_providers_off_a_named_compose_api_runs(providers_off, tmp_path, monkeypatch, calls):
    monkeypatch.setattr(sc, "explicit_backend_profile", lambda: {"dispatch": sc.DISPATCH_COMPOSE})
    remote_run.run_remote(tmp_path, "c")
    assert [c[0] for c in calls] == ["compose"]


def test_providers_off_the_runner_fails_the_run_naming_why(providers_off, tmp_path):
    from vivarium_workbench.lib import run_runner
    from vivarium_workbench.lib.composite_runs import connect, query_run_meta, save_metadata

    ws = tmp_path / "ws"
    run_dir = ws / ".pbg" / "runs" / "r1"
    run_dir.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    db = ws / ".pbg" / "composite-runs.db"
    req = {"run_id": "r1", "spec_id": "pkg.composites.x", "pkg": "pkg", "workspace": str(ws), "overrides": {},
           "steps": 2, "emit_paths": ["stores/x"], "db_file": str(db), "log_path": ".pbg/runs/r1/run.log",
           "target": "deployment"}
    path = run_dir / "request.json"
    path.write_text(json.dumps(req))
    conn = connect(str(db))
    save_metadata(conn, spec_id=req["spec_id"], run_id="r1", params={}, label="", started_at=0.0, n_steps=2,
                  log_path=req["log_path"])
    conn.close()

    assert run_runner.execute(path) == 1
    log = (run_dir / "run.log").read_text()
    assert log.rstrip().endswith("REMOTE RUN FAILED: RuntimeError: this run targets a deployment, but no remote "
                                 "backend is named (serve --backend-base-url) or provided by an installed backend "
                                 "plugin")
    conn = connect(str(db))
    assert query_run_meta(conn, run_id="r1")["status"] == "failed"
    conn.close()


def test_providers_off_the_opt_in_compose_path_is_refused_at_launch(providers_off, tmp_path, monkeypatch):
    """The Composites tab's opt-in compose path (``ALLOW_COMPOSE_DISPATCH``) would reach a runner with no backend:
    refused when the run is launched, as the remote-run views refuse it (no detached run is spawned)."""
    from vivarium_workbench.lib import composite_test_run_views as v
    from vivarium_workbench.lib import run_registry

    (tmp_path / ".pbg").mkdir()
    (tmp_path / "workspace.yaml").write_text("name: ws\n", encoding="utf-8")
    monkeypatch.setenv("VIVARIUM_WORKBENCH_ALLOW_COMPOSE_DISPATCH", "1")
    monkeypatch.setattr(run_registry, "count_running", lambda db_file: 0)
    monkeypatch.setattr(run_registry, "spawn_detached",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("a run with no backend was spawned")))
    monkeypatch.setattr(remote_run, "remote_dispatch_preflight",
                        lambda ws: (_ for _ in ()).throw(AssertionError("refused before the git preflight")))
    body, status = v.composite_test_run(tmp_path, {"id": "pkg.composites.x", "steps": 3,
                                                   "run_target": "deployment"})
    assert status == 409, body
    assert body["reason"] == "no-backend" and body["run_target"] == "deployment"


# --- signatures -------------------------------------------------------------------------------------------------------

def test_the_dispatcher_takes_no_client_and_is_keyword_only():
    params = inspect.signature(remote_run.run_remote).parameters
    assert "client" not in params and "skip_preflight" not in params and "expected_variant_count" not in params
    names = list(params)
    assert names[:2] == ["ws_root", "composite_id"]
    assert all(params[n].kind is inspect.Parameter.KEYWORD_ONLY for n in names[2:])


def test_the_plugin_run_keeps_the_old_run_remote_parameters():
    """``deployment_run.run`` is the old ``remote_run.run_remote`` (names, order, defaults), plus ``via_viva_v1``, so
    a caller that supplies a client only changed which function it calls."""
    from vivarium_workbench_sms import deployment_run

    params = inspect.signature(deployment_run.run).parameters
    old = [("ws_root", inspect.Parameter.empty), ("composite_id", inspect.Parameter.empty), ("client", None),
           ("poll_interval", 10.0), ("dest", None), ("n_steps", 1), ("overrides", None), ("poll_timeout", 7200.0),
           ("skip_preflight", False), ("expected_variant_count", None), ("analysis_options", None),
           ("build_ref", None), ("emit_paths", None)]
    assert [(n, p.default) for n, p in params.items()] == [*old, ("via_viva_v1", False)]
    assert params["via_viva_v1"].kind is inspect.Parameter.KEYWORD_ONLY


# --- permanent anti-silent-patch rules ----------------------------------------------------------------------------

def test_remote_run_binds_no_sms_seam():
    """The SMS run left the core whole: no name a test used to patch on ``remote_run`` for it is still bound there,
    so a stale ``setattr(remote_run, ...)`` raises instead of patching a dead copy."""
    for name in ("_poll_until_terminal", "_TERMINAL_STATUSES", "_MAX_CONSECUTIVE_POLL_ERRORS", "compose_simulation_id",
                 "viva_v1_document_options", "_SIMULATION_LOOKUP_BATCH", "_SIMULATION_LOOKUP_SLACK",
                 "export_composite_pbg", "time", "json", "tempfile",
                 # step P2 Builds B2: viva-core's environment grammar moved to remote_run_viva_v1
                 "backend_environment", "DEFAULT_BACKEND_ENVIRONMENT"):
        assert not hasattr(remote_run, name), name


def test_plugin_reads_core_remote_run_through_the_module():
    """Plugin code reads ``git_pip_url``, ``workspace_pinned_deps`` and ``remote_dispatch_preflight`` as
    ``remote_run.<name>`` at call time, never from-imports them: tests patch them on ``remote_run``, and a
    from-imported copy would make those patches silently dead. ``deployment_run`` reads the client the same way
    (``sms_api_client.<name>``). (``backend_environment`` was one of them until step P2 Builds B2 moved it into
    ``remote_run_viva_v1``.)"""
    offenders = []
    for path in sorted(PLUGIN_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "vivarium_workbench.lib.remote_run":
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, f"from-imports of vivarium_workbench.lib.remote_run in the plugin: {offenders}"

    tree = ast.parse((PLUGIN_DIR / "deployment_run.py").read_text(encoding="utf-8"))
    sac = [f"deployment_run.py:{n.lineno}" for n in ast.walk(tree)
           if isinstance(n, ast.ImportFrom) and n.module == "vivarium_workbench_sms.sms_api_client"]
    assert not sac, f"deployment_run from-imports sms_api_client names (read them through the module): {sac}"
