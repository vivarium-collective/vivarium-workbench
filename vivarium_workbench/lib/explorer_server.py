"""Launch-on-demand manager for the v2ecoli molecule Explorer.

The Explorer (``v2ecoli.browser``) is a standalone single-page app with its own
HTTP server. The workbench embeds it in the "Molecules" rail tab via an
``<iframe>`` whose ``src`` comes from :func:`ensure_explorer`. This module launches
the Explorer as a detached process for a workspace (adopting an already-running one),
pointed at that workspace's ParCa cache and run store so its built-in run picker
lists the workspace's sims.

Mirrors the dashboard's own detached-serve pattern in ``cli.py`` (``_serve_detached``
+ the ``_pid_alive`` / ``_http_ok`` helpers) but writes its state under
``<ws>/.pbg/explorer/`` and uses the WORKSPACE interpreter (``env_resolver`` —
``v2ecoli`` lives in the workspace venv, not the workbench's).
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import psutil

from vivarium_workbench.lib.env_resolver import resolve_interpreter


def _explorer_dir(ws: Path) -> Path:
    return ws / ".pbg" / "explorer"


def _info_path(ws: Path) -> Path:
    return _explorer_dir(ws) / "explorer-info"


def _read_info(ws: Path) -> dict | None:
    try:
        return json.loads(_info_path(ws).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        return psutil.pid_exists(int(pid))
    except (OSError, ValueError):
        return False


def _http_ok(url: str, timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 (loopback)
            return 200 <= getattr(r, "status", 200) < 500
    except Exception:  # noqa: BLE001
        return False


def _pick_free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _cache_dir(ws: Path) -> Path:
    # Convention: <ws>/out/cache (lib/ensemble_config.py default cache_dir).
    return ws / "out" / "cache"


def _runs_dir(ws: Path) -> Path:
    # Scan the whole out/ tree so the Explorer's discover_runs picks up multigen
    # lineage outputs (out/<run_name>/) as well as recorded ledger runs.
    return ws / "out"


def ensure_explorer(ws: Path) -> dict:
    """Return the molecule Explorer URL for ``ws``, launching it if needed.

    Result shape (always a dict the frontend can branch on):
      {"status": "running", "url": "http://127.0.0.1:<port>", "port": int, "pid": int}
      {"status": "no-cache", "url": None, "message": "..."}  — no ParCa cache yet
      {"status": "error",    "url": None, "message": "..."}  — launch failed
    """
    ws = Path(ws)
    edir = _explorer_dir(ws)

    # Adopt an already-running Explorer for this workspace.
    existing = _read_info(ws)
    if (
        existing
        and existing.get("url")
        and _pid_alive(existing.get("pid"))
        and _http_ok(existing["url"])
    ):
        return {
            "status": "running",
            "url": existing["url"],
            "port": existing.get("port"),
            "pid": existing.get("pid"),
        }

    # The Explorer requires a ParCa cache (simData.cPickle) to build its index.
    cache = _cache_dir(ws)
    if not (cache / "simData.cPickle").is_file():
        return {
            "status": "no-cache",
            "url": None,
            "message": (
                f"No ParCa cache at {cache} (needs simData.cPickle). Build one "
                f"(e.g. python scripts/build_cache.py) and reopen the Molecules tab."
            ),
        }

    edir.mkdir(parents=True, exist_ok=True)
    # Clear stale state from a dead instance.
    try:
        _info_path(ws).unlink()
    except FileNotFoundError:
        pass

    interp = resolve_interpreter(ws) or sys.executable
    runs = _runs_dir(ws)
    port = _pick_free_port()
    url = f"http://127.0.0.1:{port}"
    log_file = edir / "explorer.log"

    cmd = [
        interp, "-m", "v2ecoli.browser",
        "--cache", str(cache),
        "--runs-dir", str(runs),
        "--host", "127.0.0.1",
        "--port", str(port),
    ]

    with open(log_file, "wb") as log:
        proc = subprocess.Popen(  # noqa: S603
            cmd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            start_new_session=True, cwd=str(ws),
        )

    # Poll for readiness (~20s; the Explorer builds a name index from simData on boot).
    ready = False
    for _ in range(200):
        if not _pid_alive(proc.pid):
            return {
                "status": "error",
                "url": None,
                "message": f"Explorer exited during startup — see {log_file}",
            }
        if _http_ok(url):
            ready = True
            break
        time.sleep(0.1)

    info = {
        "status": "running" if ready else "starting",
        "url": url,
        "port": port,
        "pid": proc.pid,
        "cache_dir": str(cache),
        "runs_dir": str(runs),
        "log_file": str(log_file),
    }
    try:
        _info_path(ws).write_text(json.dumps(info), encoding="utf-8")
    except OSError:
        pass

    if not ready:
        info["message"] = f"Explorer did not report ready in time — check {log_file}"
    return info
