"""The core imports without the SMS plugin (docs/backend-adapters.md §2.5, step P2).

The hosted env-worker pod ships the core (``vivarium_workbench``: ``env_worker.py``, ``lib/``) WITHOUT
``vivarium_workbench_sms``, so every core module the worker can reach must import without the plugin. Step P1b moved
whole SMS modules into the plugin, and a core module that still imports one at module level fails there with
``ModuleNotFoundError`` (``lib.study_runs`` did, and the worker's study-run methods import it).

``STILL_NEED_PLUGIN`` is a ratchet: the core modules that still cannot be imported without the plugin. Each P2 PR
removes the entries it fixes, none may add one, and a stale entry fails too, so the list can only shrink. It is empty
when P2 ends.

Importing is not running. A study run IN the worker resolves its target at call time (``study_runs.run_study_*`` ->
``backends.resolve_run_target`` -> ``backends.named`` -> ``server_capabilities``); since step P2's Client area that path
no longer imports the plugin either, and ``test_run_target_answers_without_the_plugin`` keeps it that way.
"""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap


STILL_NEED_PLUGIN = frozenset({
    "vivarium_workbench.api.app",
    "vivarium_workbench.lib.remote_run_views",
    "vivarium_workbench.lib.remote_trace",
})

# Modules allowed to fail for a reason other than the plugin: optional extras that may be absent (the ``chat`` extra).
# Anything else failing to import would hide a plugin import behind it.
OPTIONAL_EXTRA_MODULES = frozenset({
    "vivarium_workbench.lib.ai_chat",
    "vivarium_workbench.lib.ai_claude_code",
    "vivarium_workbench.lib.ai_tools",
})

# Must import without the plugin: the env worker itself, and what its study-run methods import.
MUST_IMPORT = ("vivarium_workbench.env_worker", "vivarium_workbench.lib.study_runs")

_PROBE = textwrap.dedent('''
    import importlib, json, pkgutil, sys

    class _NoPlugin:
        """Every vivarium_workbench_sms import fails, as in the hosted env-worker pod."""
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "vivarium_workbench_sms" or fullname.startswith("vivarium_workbench_sms."):
                raise ModuleNotFoundError(f"No module named {fullname!r}", name=fullname)
            return None

    sys.meta_path.insert(0, _NoPlugin())
    for name in [n for n in sys.modules if n == "vivarium_workbench_sms" or n.startswith("vivarium_workbench_sms.")]:
        del sys.modules[name]

    must = {}
    for name in MUST_IMPORT:
        try:
            importlib.import_module(name)
            must[name] = None
        except BaseException as e:  # noqa: BLE001 -- report, never crash the probe
            must[name] = f"{type(e).__name__}: {e}"

    import vivarium_workbench
    need = set()
    other = {}

    def _onerror(name):
        pass

    for info in pkgutil.walk_packages(vivarium_workbench.__path__, "vivarium_workbench.", onerror=_onerror):
        name = info.name
        parts = name.split(".")
        if "loom" in parts or parts[-1] == "__main__":
            continue
        try:
            importlib.import_module(name)
        except ModuleNotFoundError as e:
            if (e.name or "").startswith("vivarium_workbench_sms"):
                need.add(name)
            else:
                other[name] = f"ModuleNotFoundError: {e}"
        except BaseException as e:  # noqa: BLE001
            other[name] = f"{type(e).__name__}: {e}"
    print("@@RESULT@@" + json.dumps({"must": must, "need": sorted(need), "other": other}))
''')


def _probe() -> dict:
    code = f"MUST_IMPORT = {MUST_IMPORT!r}\n" + _PROBE
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=600)
    marker = [ln for ln in proc.stdout.splitlines() if ln.startswith("@@RESULT@@")]
    assert marker, f"probe crashed (exit {proc.returncode}):\n{proc.stdout[-3000:]}\n{proc.stderr[-3000:]}"
    return json.loads(marker[-1][len("@@RESULT@@"):])


def test_core_modules_import_without_the_plugin():
    result = _probe()
    assert result["must"] == {name: None for name in MUST_IMPORT}, result["must"]
    need = frozenset(result["need"])
    new = sorted(need - STILL_NEED_PLUGIN)
    stale = sorted(STILL_NEED_PLUGIN - need)
    assert not new, f"core modules that newly need the SMS plugin to import (make the import lazy): {new}"
    assert not stale, f"these import without the plugin now; remove them from STILL_NEED_PLUGIN: {stale}"
    unexpected = sorted(set(result["other"]) - OPTIONAL_EXTRA_MODULES)
    assert not unexpected, f"core modules failing to import for another reason: {result['other']}"


_CALL_PROBE = textwrap.dedent('''
    import sys, tempfile

    class _NoPlugin:
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "vivarium_workbench_sms" or fullname.startswith("vivarium_workbench_sms."):
                raise ModuleNotFoundError(f"No module named {fullname!r}", name=fullname)
            return None

    sys.meta_path.insert(0, _NoPlugin())
    from vivarium_workbench.lib import backends
    print("@@RESULT@@" + backends.resolve_run_target(tempfile.mkdtemp()))
''')


def test_run_target_answers_without_the_plugin(monkeypatch):
    from vivarium_workbench.lib.backend_config import BACKEND_BASE_ENV_VARS

    for var in BACKEND_BASE_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
        monkeypatch.delenv(var.replace("VIVARIUM_WORKBENCH_", "VIVARIUM_DASHBOARD_"), raising=False)
    proc = subprocess.run([sys.executable, "-c", _CALL_PROBE], capture_output=True, text=True, timeout=300)
    assert "@@RESULT@@local" in proc.stdout, proc.stderr[-2000:]


_LANDING_PROBE = textwrap.dedent('''
    import json, sys, tempfile
    from pathlib import Path

    class _NoPlugin:
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "vivarium_workbench_sms" or fullname.startswith("vivarium_workbench_sms."):
                raise ModuleNotFoundError(f"No module named {fullname!r}", name=fullname)
            return None

    sys.meta_path.insert(0, _NoPlugin())
    from vivarium_workbench.lib import remote_run_landing

    root = Path(tempfile.mkdtemp())
    tree, ws = root / "x", root / "ws"
    a = tree / "analyses" / "a"
    (a / "ptools").mkdir(parents=True)
    (a / "ptools" / "ptools_rna__all.tsv").write_text("id t0")
    (a / "analysis.json").write_text(json.dumps({"summary": {"m": {"ptools_rna": "ok"}}}))
    remote_run_landing.fold_analyses(tree, ws, "r1")
    n = remote_run_landing.land_ptools_tsvs(tree, ws, "r1", study_dest=root / "study" / "e")
    ok = ((ws / ".pbg" / "runs" / "r1" / "analyses.json").is_file()
          and (root / "study" / "e" / "ptools" / "ptools_rna__all.tsv").is_file())
    plugin = [m for m in sys.modules if m.startswith("vivarium_workbench_sms")]
    print("@@RESULT@@" + json.dumps({"n": n, "ok": ok, "plugin": plugin}))
''')


def test_the_archive_landing_runs_without_the_plugin():
    """Step P2 Runs c1: the archive landing the core keeps (``fold_analyses``, ``land_ptools_tsvs``: the PTools
    writer) runs with the plugin absent and never reaches for it."""
    proc = subprocess.run([sys.executable, "-c", _LANDING_PROBE], capture_output=True, text=True, timeout=300)
    marker = [ln for ln in proc.stdout.splitlines() if ln.startswith("@@RESULT@@")]
    assert marker, proc.stderr[-2000:]
    assert json.loads(marker[-1][len("@@RESULT@@"):]) == {"n": 1, "ok": True, "plugin": []}


_CLI_PROBE = textwrap.dedent('''
    import argparse, sys

    class _NoPlugin:
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "vivarium_workbench_sms" or fullname.startswith("vivarium_workbench_sms."):
                raise ModuleNotFoundError(f"No module named {fullname!r}", name=fullname)
            return None

    sys.meta_path.insert(0, _NoPlugin())
    import vivarium_workbench.lib.server_capabilities, vivarium_workbench.lib.startup
    from vivarium_workbench import cli

    action = next(a for a in cli._build_parser()._actions if isinstance(a, argparse._SubParsersAction))
    assert "run-remote" not in action.choices, sorted(action.choices)
    try:
        cli.main(["gen-readme", "--help"])
    except SystemExit as e:
        print("@@RESULT@@" + str(e.code))
''')


def test_the_cli_and_startup_work_without_the_plugin():
    """Step P2 CLI: ``cli``, ``startup`` and ``server_capabilities`` import with the plugin absent; the CLI parses and
    simply has no ``run-remote``."""
    proc = subprocess.run([sys.executable, "-c", _CLI_PROBE], capture_output=True, text=True, timeout=300)
    assert "@@RESULT@@0" in proc.stdout, proc.stderr[-2000:]
    assert "warning" not in proc.stderr  # an absent plugin is not a broken one


_REFRESH_PROBE = textwrap.dedent('''
    import sys

    class _NoPlugin:
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "vivarium_workbench_sms" or fullname.startswith("vivarium_workbench_sms."):
                raise ModuleNotFoundError(f"No module named {fullname!r}", name=fullname)
            return None

    sys.meta_path.insert(0, _NoPlugin())
    from vivarium_workbench.lib import remote_run_compose, run_jobs

    remote_run_compose.status = lambda run_id: ({"phase": "done", "raw_status": "completed", "error": None}, 200)
    job = run_jobs.RunJob("inv", [{"status": "submitted", "simulation_id": 4},
                                  {"status": "submitted", "simulation_id": "compose-1"}])
    run_jobs.refresh_submitted(job)
    loaded = any(n == "vivarium_workbench_sms" or n.startswith("vivarium_workbench_sms.") for n in sys.modules)
    print("@@RESULT@@" + ",".join(it["status"] for it in job.to_dict()["items"]) + f";plugin={loaded}")
''')


def test_refresh_submitted_answers_without_the_plugin():
    """Step P2 Runs c2: with the plugin absent, an integer (SMS) item is owned by no backend and stays ``submitted``
    without SMS being asked; a compose-api item still settles."""
    proc = subprocess.run([sys.executable, "-c", _REFRESH_PROBE], capture_output=True, text=True, timeout=300)
    assert "@@RESULT@@submitted,done;plugin=False" in proc.stdout, proc.stdout[-2000:] + proc.stderr[-2000:]


_RUN_PROBE = textwrap.dedent('''
    import sys, tempfile
    from pathlib import Path

    class _NoPlugin:
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "vivarium_workbench_sms" or fullname.startswith("vivarium_workbench_sms."):
                raise ModuleNotFoundError(f"No module named {fullname!r}", name=fullname)
            return None

    sys.meta_path.insert(0, _NoPlugin())
    from vivarium_workbench.lib import remote_run, remote_run_compose
    from vivarium_workbench.lib import server_capabilities as sc

    ws = tempfile.mkdtemp()
    sc.explicit_backend_profile = lambda: None          # nothing named
    try:
        remote_run.run_remote(ws, "c")
        print("@@NONE@@returned")
    except BaseException as e:  # noqa: BLE001
        print("@@NONE@@" + type(e).__name__ + ": " + str(e))

    sc.explicit_backend_profile = lambda: {"dispatch": sc.DISPATCH_COMPOSE}   # compose-api named
    remote_run_compose.run_composite = lambda *a, **k: Path(k["dest"]) / "results.zip"
    print("@@COMPOSE@@" + remote_run.run_remote(ws, "c").name)
''')


def test_a_deployment_run_works_or_refuses_cleanly_without_the_plugin(monkeypatch):
    """Step P2 Runs b: the runner's dispatcher no longer imports the plugin before choosing a backend. With nothing
    named it is a ``RuntimeError`` naming no backend (never ``ModuleNotFoundError``); a named compose-api runs."""
    from vivarium_workbench.lib.backend_config import BACKEND_BASE_ENV_VARS

    for var in BACKEND_BASE_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
        monkeypatch.delenv(var.replace("VIVARIUM_WORKBENCH_", "VIVARIUM_DASHBOARD_"), raising=False)
    proc = subprocess.run([sys.executable, "-c", _RUN_PROBE], capture_output=True, text=True, timeout=300)
    assert "@@NONE@@RuntimeError: this run targets a deployment, but no remote backend is named" in proc.stdout, (
        proc.stdout[-2000:], proc.stderr[-2000:])
    assert "@@COMPOSE@@results.zip" in proc.stdout, (proc.stdout[-2000:], proc.stderr[-2000:])


_SESSION_BUILD_PROBE = textwrap.dedent('''
    import pathlib, sys, tempfile

    class _NoPlugin:
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "vivarium_workbench_sms" or fullname.startswith("vivarium_workbench_sms."):
                raise ModuleNotFoundError(f"No module named {fullname!r}", name=fullname)
            return None

    sys.meta_path.insert(0, _NoPlugin())
    from vivarium_workbench.lib import backends, run_core

    ws = pathlib.Path(tempfile.mkdtemp())
    (ws / ".viv-build.json").write_text('{"simulator_id": 42, "commit": "abc"}')
    print("@@RESULT@@" + repr((backends.session_build(ws), run_core.run_target_for(ws))))
''')


def test_the_session_build_answers_without_the_plugin():
    """Step P2 Builds B1: with the plugin absent a stamped workspace is no session build and runs locally (before B1,
    ``composite_resolve``'s preview raised ``ModuleNotFoundError`` on any workspace)."""
    proc = subprocess.run([sys.executable, "-c", _SESSION_BUILD_PROBE], capture_output=True, text=True, timeout=300)
    assert "@@RESULT@@(None, 'local')" in proc.stdout, proc.stderr[-2000:]
