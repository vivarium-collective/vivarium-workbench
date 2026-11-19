"""The core imports without the SMS plugin (docs/backend-adapters.md §2.5, step P2).

The hosted env-worker pod ships the core (``vivarium_workbench``: ``env_worker.py``, ``lib/``) WITHOUT
``vivarium_workbench_sms``, so every core module the worker can reach must import without the plugin. Step P1b moved
whole SMS modules into the plugin, and a core module that still imports one at module level fails there with
``ModuleNotFoundError`` (``lib.study_runs`` did, and the worker's study-run methods import it).

``STILL_NEED_PLUGIN`` is a ratchet: the core modules that still cannot be imported without the plugin. Each P2 PR
removes the entries it fixes, none may add one, and a stale entry fails too, so the list can only shrink. It is empty
when P2 ends.
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
    "vivarium_workbench.lib.server_capabilities",
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
