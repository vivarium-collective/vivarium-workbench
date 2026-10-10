"""``vivarium-workbench smoke`` — a <1-minute local sanity check of the spine.

Spec: ``docs/dual-engine-comparison.md`` §5.4. Two modes:

* **Hermetic (default, no arguments).** Scaffolds a throwaway minimal workspace
  (a trivial ``IncreaseProcess`` package + one runnable composite — the same
  proven shape as ``tests/_fixtures/ws_increase_demo``) in a temp dir, then
  checks the whole local spine:

    1. ``workspace``  — the scaffold parses as a workspace;
    2. ``env-worker`` — the per-workspace env worker spawns and answers ``ping``;
    3. ``tiny-run``   — a real 3-step run executes through ``run_runner.execute``
       (the detached-run engine, in-process here) and lands ``completed`` with a
       manifest carrying ``environments: [{role: primary, …}]`` (dual-engine W1);
    4. ``server``     — ``vivarium-workbench serve`` boots and answers
       ``/health`` and ``/``.

  Never touches a user workspace; safe to run anywhere; exit 0/1 (CI-able).

* **``--workspace PATH``.** The **non-mutating** subset (1, 2, 4) against a real
  workspace — no run is written into anyone's ``runs.db``.

Every check runs even after an earlier one fails, so one report shows the whole
picture. Provenance fields inside the manifest degrade per ``build_run_manifest``
(a scaffold is not a git repo → ``commit`` is None); the smoke asserts structure,
not values.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# The hermetic scaffold (mirrors tests/_fixtures/ws_increase_demo, minimally)
# ---------------------------------------------------------------------------

_WORKSPACE_YAML = """\
schema_version: 2
name: smoke-ws
package_path: pbg_smoke
# sqlite emitter so the run trajectory is readable via composite-runs.db
# (matches the ws_increase_demo fixture rationale).
runtime:
  default_emitter: sqlite
phases: []
observables: []
visualizations: []
simulations: []
datasets: []
server:
  enabled: true
"""

_PROCESSES_PY = '''\
from process_bigraph import Process


class IncreaseProcess(Process):
    """Trivial linear-growth process for the smoke scaffold."""
    config_schema = {'rate': {'_type': 'float', '_default': 1.0}}

    def inputs(self):
        return {'level': 'float'}

    def outputs(self):
        return {'level': 'float'}

    def update(self, state, interval=1.0):
        rate = (self.config or {}).get('rate', 1.0)
        return {'level': state.get('level', 0.0) * rate}
'''

_CORE_PY = """\
from process_bigraph import allocate_core
from process_bigraph.emitter import RAMEmitter
from pbg_smoke.processes import IncreaseProcess


def build_core():
    core = allocate_core()
    core.register_link('IncreaseProcess', IncreaseProcess)
    core.register_link('RAMEmitter', RAMEmitter)
    return core
"""

_COMPOSITE_YAML = """\
name: increase-demo
description: "Trivial linear-growth composite for the smoke check."
requires:
  processes: [IncreaseProcess, RAMEmitter]
parameters:
  rate:
    type: float
    default: 2.0
  initial_level:
    type: float
    default: 1.0
state:
  increase:
    _type: process
    address: "local:IncreaseProcess"
    config:
      rate: "${rate}"
    inputs:
      level: ["stores", "level"]
    outputs:
      level: ["stores", "level"]
    interval: 1.0
  stores:
    level: "${initial_level}"
  emitter:
    _type: step
    address: "local:RAMEmitter"
    config:
      emit:
        level: "float"
    inputs:
      level: ["stores", "level"]
"""


def scaffold_workspace(dest: Path) -> Path:
    """Write the minimal runnable workspace under ``dest``; return its root."""
    ws = Path(dest) / "smoke-ws"
    pkg = ws / "pbg_smoke"
    comps = pkg / "composites"
    comps.mkdir(parents=True, exist_ok=True)
    (ws / "workspace.yaml").write_text(_WORKSPACE_YAML, encoding="utf-8")
    (pkg / "__init__.py").write_text(
        '"""Smoke-scaffold workspace package."""\n', encoding="utf-8")
    (pkg / "processes.py").write_text(_PROCESSES_PY, encoding="utf-8")
    (pkg / "core.py").write_text(_CORE_PY, encoding="utf-8")
    (comps / "__init__.py").write_text(
        '"""Smoke-scaffold composites."""\n', encoding="utf-8")
    (comps / "increase-demo.composite.yaml").write_text(
        _COMPOSITE_YAML, encoding="utf-8")
    return ws


# ---------------------------------------------------------------------------
# Checks — each returns (ok, detail) and must not raise
# ---------------------------------------------------------------------------

def _check_workspace(ws: Path) -> tuple[bool, str]:
    """workspace.yaml parses and names a package."""
    try:
        import yaml
        data = yaml.safe_load((ws / "workspace.yaml").read_text(encoding="utf-8")) or {}
        pkg = data.get("package_path") or data.get("name")
        return bool(pkg), f"name={data.get('name')!r} package={data.get('package_path')!r}"
    except Exception as e:  # noqa: BLE001 — a check reports, never raises
        return False, f"{type(e).__name__}: {e}"


def _check_env_worker(ws: Path) -> tuple[bool, str]:
    """The per-workspace env worker spawns and answers ``ping``."""
    try:
        from vivarium_workbench.lib.env_worker_pool import get_pool
        r = get_pool().call(ws, "ping")
        return isinstance(r, dict), f"ping -> {sorted(r)[:4] if isinstance(r, dict) else type(r).__name__}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def _check_tiny_run(ws: Path, steps: int = 3) -> tuple[bool, str]:
    """A real tiny run through the detached-run engine, with W1 provenance.

    Mirrors the POST handler flow the way ``tests/test_run_runner.py`` does:
    seed the ``runs_meta`` row (``workspace=`` → the #868 auto-manifest), write
    the request file, then drive ``run_runner.execute`` in-process (this IS the
    detached runner's body — workspace imports are legitimate here, unlike in
    the HTTP process).
    """
    try:
        from vivarium_workbench.lib.composite_runs import (
            connect, query_run_meta, save_metadata,
        )
        from vivarium_workbench.lib.run_runner import execute

        if str(ws) not in sys.path:
            sys.path.insert(0, str(ws))
        run_id = "smoke-run-1"
        run_dir = ws / ".pbg" / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        db_file = str(ws / ".pbg" / "composite-runs.db")
        request = {
            "run_id": run_id,
            "spec_id": "pbg_smoke.composites.increase-demo",
            "pkg": "pbg_smoke",
            "workspace": str(ws),
            "overrides": {},
            "steps": steps,
            "emit_paths": [],
            "db_file": db_file,
            "log_path": f".pbg/runs/{run_id}/run.log",
        }
        request_path = run_dir / "request.json"
        request_path.write_text(json.dumps(request), encoding="utf-8")
        conn = connect(db_file)
        save_metadata(conn, spec_id=request["spec_id"], run_id=run_id, params={},
                      label="smoke", started_at=time.time(), n_steps=steps,
                      log_path=request["log_path"], workspace=ws)
        conn.close()

        rc = execute(request_path)
        conn = connect(db_file)
        meta = query_run_meta(conn, run_id=run_id)
        conn.close()
        if rc != 0 or not meta or meta.get("status") != "completed":
            return False, f"rc={rc} status={meta.get('status') if meta else None}"

        manifest = json.loads(meta.get("manifest_json") or "{}")
        envs = manifest.get("environments") or []
        roles = [e.get("role") for e in envs]
        if "primary" not in roles:
            return False, f"manifest environments missing primary: roles={roles}"
        return True, f"completed steps={meta.get('progress_step')} environments={roles}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _check_loom_endpoint(base: str) -> tuple[bool, str]:
    """The ``/bigraph-loom/index.html`` contract: 200 (bundle built) or a guarded
    503 error page (bundle absent) — never a blank/empty 404 or a 500. Returns
    ``(ok, detail)``; the detail records which arm held so a 503-in-CI (no loom
    build) reads as expected degradation, not a failure."""
    url = base + "/bigraph-loom/index.html"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            body = r.read(4096)
            if r.status == 200 and body:
                return True, "loom bundle served (200)"
            return False, f"GET /bigraph-loom/index.html -> {r.status}, {len(body)}b"
    except urllib.error.HTTPError as e:
        body = b""
        try:
            body = e.read(4096)
        except Exception:  # noqa: BLE001
            pass
        if e.code == 503 and b"bigraph-loom" in body:
            return True, "loom bundle absent — guarded 503 page (expected without a loom build)"
        return False, f"GET /bigraph-loom/index.html -> {e.code} (not the guarded 503 page)"
    except Exception as e:  # noqa: BLE001
        return False, f"loom probe {type(e).__name__}: {e}"


def _check_server(ws: Path, timeout_s: float = 45.0) -> tuple[bool, str]:
    """``serve`` boots and answers ``/health`` and ``/`` (then is torn down)."""
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "vivarium_workbench.cli", "serve",
         "--workspace", str(ws), "--port", str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        env=dict(os.environ),
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                out = (proc.stdout.read() if proc.stdout else b"")[-400:]
                return False, f"server exited rc={proc.returncode}: …{out.decode(errors='replace')}"
            try:
                with urllib.request.urlopen(base + "/health", timeout=2) as r:
                    if r.status == 200:
                        break
            except Exception:  # noqa: BLE001 — still booting
                time.sleep(0.25)
        else:
            return False, f"/health not answering within {timeout_s:.0f}s"
        with urllib.request.urlopen(base + "/", timeout=10) as r:
            if r.status != 200:
                return False, f"GET / -> {r.status}"
        # The bigraph-loom embed contract: the endpoint the composite-card graph
        # view iframes must NEVER silently blank. Either the bundle is built and
        # serves 200, or it is absent and serves the guarded 503 error page — any
        # other outcome (empty 404, 500) is the blank-pane regression we guard.
        loom_ok, loom_detail = _check_loom_endpoint(base)
        if not loom_ok:
            return False, loom_detail
        return True, f"/health + / + loom OK on :{port} ({loom_detail})"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_smoke(workspace: "Path | None" = None) -> int:
    """Run the smoke checks; print a report; return 0 (all ok) or 1.

    ``workspace=None`` → hermetic mode (scaffold + all four checks, including
    the tiny run). A given workspace → the non-mutating subset (no run).
    """
    import tempfile

    checks: list[tuple[str, bool, str]] = []
    with tempfile.TemporaryDirectory(prefix="vwb-smoke-") as td:
        if workspace is None:
            ws = scaffold_workspace(Path(td))
            mode = "hermetic"
        else:
            ws = Path(workspace).resolve()
            mode = "workspace"
        print(f"vivarium-workbench smoke — {mode} mode — {ws}")

        ok, detail = _check_workspace(ws)
        checks.append(("workspace", ok, detail))
        ok, detail = _check_env_worker(ws)
        checks.append(("env-worker", ok, detail))
        if workspace is None:
            ok, detail = _check_tiny_run(ws)
            checks.append(("tiny-run", ok, detail))
        ok, detail = _check_server(ws)
        checks.append(("server", ok, detail))

    failed = [c for c in checks if not c[1]]
    for name, ok, detail in checks:
        print(f"  {'ok  ' if ok else 'FAIL'} {name:<11} {detail}")
    print(f"smoke: {len(checks) - len(failed)}/{len(checks)} checks passed"
          + ("" if not failed else f" — FAILED: {', '.join(c[0] for c in failed)}"))
    return 0 if not failed else 1
