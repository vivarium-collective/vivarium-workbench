"""Env worker — the per-session subprocess that holds a workspace's compute
environment out of the HTTP process.

Full contract: ``docs/env-worker-protocol.md``. This is the **worker program**
(spec §4): a single self-contained file, shipped by the workbench but run on the
workspace's interpreter by path, importing **only the standard library** (plus,
in later slices, what the workspace venv already has). It never imports
``vivarium_workbench``.

**Scope so far:** the transport + lifecycle (``initialize`` / ``ping`` /
``shutdown``) and the first environment query, ``list_generators`` — which
imports the workspace's own package + the generator registry **in this process**
(so the imports the HTTP process must not do live here instead). The heavier
``build_core``-backed methods (``registry_catalog``, ``resolve_composite_state`` …)
land in later slices. ``list_generators`` imports pbg_superpowers + the workspace
package (both workspace-venv deps, spec §4); everything else is stdlib.

Invocation (spec §4/§5)::

    <python> <path>/env_worker.py --socket-fd <n> --workspace <dir>

``--socket-fd`` is the inherited end of a ``socket.socketpair()`` (passed via
``subprocess(pass_fds=...)``); ``stdout``/``stderr`` are for logs, never the
protocol (spec §5).
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import struct
import sys
import time
import traceback

PROTOCOL_VERSION = "1.0"
_MAX_FRAME = 64 * 1024 * 1024  # 64 MiB cap (spec §5) — over-cap is an error, not an OOM
_started = time.monotonic()
_workspace = ""


class _MethodError(Exception):
    """A structured JSON-RPC error (spec §9)."""

    def __init__(self, code: int, message: str, data=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def _recv_exact(sock: socket.socket, n: int) -> "bytes | None":
    """Read exactly ``n`` bytes, or ``None`` on EOF (the parent went away)."""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def _read_frame(sock: socket.socket) -> "dict | None":
    """One length-prefixed JSON frame (uint32 BE length + UTF-8 JSON), or None on EOF."""
    hdr = _recv_exact(sock, 4)
    if hdr is None:
        return None
    (n,) = struct.unpack(">I", hdr)
    if n > _MAX_FRAME:
        raise _MethodError(-32600, f"frame too large: {n} bytes")
    body = _recv_exact(sock, n)
    if body is None:
        return None
    return json.loads(body.decode("utf-8"))


def _write_frame(sock: socket.socket, obj: dict) -> None:
    body = json.dumps(obj).encode("utf-8")
    sock.sendall(struct.pack(">I", len(body)) + body)


_CAPABILITIES = ["initialize", "ping", "list_generators", "registry_catalog", "shutdown"]

_FRAMEWORK_PKGS = {
    "process_bigraph", "bigraph_schema", "bigraph_viz",
    "pbg_superpowers", "vivarium_workbench", "pbg_emitters",
}


def _workspace_meta(workspace: str):
    """``(package_name, workspace_pkgs_set, ws_data)`` from ``workspace.yaml`` —
    faithful to ``registry.build_registry``'s pre-script computation (both
    ``imports:`` shapes: dict keyed by catalog name, or list of dicts/strings)."""
    from pathlib import Path

    import yaml
    ws_data = yaml.safe_load((Path(workspace) / "workspace.yaml").read_text(encoding="utf-8")) or {}
    slug = ws_data.get("name", "")
    package_name = ws_data.get("package_path") or ("pbg_" + str(slug).replace("-", "_"))
    imports_raw = ws_data.get("imports") or []
    pkgs: list = []
    if isinstance(imports_raw, dict):
        for cat_name, imp_val in imports_raw.items():
            pkg = (imp_val.get("package") if isinstance(imp_val, dict) else None) \
                or cat_name.replace("-", "_")
            pkgs.append(pkg.split(".")[0])
    elif isinstance(imports_raw, list):
        for entry in imports_raw:
            if isinstance(entry, dict):
                pkg = entry.get("package") or (entry.get("name") or "").replace("-", "_")
            elif isinstance(entry, str):
                pkg = entry.replace("-", "_")
            else:
                continue
            if pkg:
                pkgs.append(pkg.split(".")[0])
    pkgs.append(package_name.split(".")[0])
    return package_name, set(dict.fromkeys(pkgs)), ws_data


def _registry_catalog() -> dict:
    """Build the workspace's core and introspect its registered processes/types
    (spec §11). A faithful in-worker port of ``registry.build_registry``'s
    embedded subprocess script — the ``core`` object can't cross the socket, so
    the introspection must run where the core lives. Returns the RAW
    ``{processes, types, workspace_pkgs}`` (the workbench applies its emitter
    ``is_workspace_default`` post-processing on top)."""
    import inspect as _inspect
    import json as _json

    if _workspace and _workspace not in sys.path:
        sys.path.insert(0, _workspace)
    try:
        package_name, workspace_pkgs, _ws_data = _workspace_meta(_workspace)
    except Exception as e:  # noqa: BLE001
        return {"error": f"workspace.yaml unreadable: {e}", "processes": [], "types": []}

    try:
        mod = __import__(f"{package_name}.core", fromlist=["build_core"])
        core = mod.build_core()
    except ImportError as e:
        return {"error": f"could not import {package_name}.core: {e}", "processes": [], "types": []}
    except Exception as e:  # noqa: BLE001
        return {"error": f"build_core() failed: {e}", "processes": [], "types": []}

    import process_bigraph as _pb
    EMITTER_CLS = getattr(_pb, "Emitter", None)
    try:
        from pbg_superpowers.visualization import Visualization as VISUALIZATION_CLS
    except ImportError:
        VISUALIZATION_CLS = None

    def _classify_source(cls):
        try:
            top_pkg = cls.__module__.split(".")[0]
        except Exception:
            return "environment_only"
        if top_pkg in workspace_pkgs:
            return "in_workspace"
        if top_pkg in _FRAMEWORK_PKGS:
            return "framework"
        return "environment_only"

    processes: list = []
    seen_classes: dict = {}
    link_reg = getattr(core, "link_registry", {}) or {}
    for name, cls in link_reg.items():
        cls_id = id(cls)
        is_qualified = "." in name
        if cls_id in seen_classes:
            existing = seen_classes[cls_id]
            if not is_qualified and "." in processes[existing]["name"]:
                processes[existing]["aliases"].append(processes[existing]["name"])
                processes[existing]["name"] = name
            else:
                processes[existing]["aliases"].append(name)
            continue
        try:
            addr = f"{cls.__module__}.{cls.__qualname__}"
        except Exception:
            addr = str(cls)
        kind = "other"
        if isinstance(cls, type):
            if EMITTER_CLS is not None and issubclass(cls, EMITTER_CLS) and cls is not EMITTER_CLS:
                kind = "emitter"
            elif VISUALIZATION_CLS is not None and issubclass(cls, VISUALIZATION_CLS) and cls is not VISUALIZATION_CLS:
                kind = "visualization"
            elif hasattr(cls, "__mro__"):
                for ancestor in cls.__mro__:
                    if ancestor.__name__ in ("Process", "ProcessEnsemble"):
                        kind = "process"
                        break
                    if ancestor.__name__ == "Step":
                        kind = "step"
                        break
        schema_preview = ""
        if hasattr(cls, "config_schema"):
            try:
                schema_preview = _json.dumps(cls.config_schema, default=str)[:400]
            except Exception:
                schema_preview = "<unserializable>"
        source = _classify_source(cls)
        # Framework hygiene: hide process_bigraph's OWN built-in process/step/other
        # classes from every workspace's registry (emitters + visualizations kept).
        _topmod = (getattr(cls, "__module__", "") or "").split(".")[0]
        if _topmod == "process_bigraph" and kind in ("process", "step", "other"):
            continue
        try:
            if isinstance(cls, type) and _inspect.isabstract(cls):
                continue
        except Exception:
            pass
        seen_classes[cls_id] = len(processes)
        processes.append({
            "name": name, "address": addr, "kind": kind,
            "schema_preview": schema_preview, "aliases": [], "source": source,
        })
    _source_order = {"in_workspace": 0, "framework": 1, "environment_only": 2}
    processes.sort(key=lambda p: (
        _source_order.get(p.get("source", "environment_only"), 2),
        "." in p["name"], p["name"]))

    types: list = []
    type_reg = getattr(core, "registry", {}) or {}
    for name in sorted(type_reg.keys()):
        try:
            td = core.access(name)
            preview = str(td)[:200] if td is not None else ""
        except Exception as e:  # noqa: BLE001
            preview = f"<error: {e}>"
        types.append({"name": name, "schema_preview": preview})

    return {"processes": processes, "types": types, "workspace_pkgs": list(workspace_pkgs)}


def _import_workspace_package(workspace: str) -> None:
    """Import the workspace's own package so its ``@composite_generator``s register
    into *this worker's* process registry. Best-effort — a workspace without a
    package (or an unparseable ``workspace.yaml``) just yields no workspace-local
    generators. Uses pyyaml (a workspace-venv dep, spec §4); falls back to a
    minimal ``package_path:`` scan if pyyaml is unavailable."""
    import importlib
    from pathlib import Path

    ws_yaml = Path(workspace) / "workspace.yaml"
    if not ws_yaml.is_file():
        return
    text = ws_yaml.read_text(encoding="utf-8")
    pkg = None
    try:
        import yaml
        data = yaml.safe_load(text) or {}
        pkg = data.get("package_path") or (
            "pbg_" + str(data.get("name", "")).replace("-", "_") if data.get("name") else None)
    except Exception:  # pyyaml absent / parse error → cheap line scan for package_path
        for line in text.splitlines():
            if line.strip().startswith("package_path:"):
                pkg = line.split(":", 1)[1].strip().strip("'\"") or None
                break
    if pkg:
        try:
            importlib.import_module(pkg)
        except Exception:  # noqa: BLE001 — a broken workspace package must not crash the worker
            pass


def _list_generators() -> dict:
    """Registry keys for this worker's environment (spec §11) — the workspace's
    own ``@composite_generator``s plus installed bigraph-package generators, held
    in THIS process (isolated from the HTTP process and from other sessions)."""
    if _workspace and _workspace not in sys.path:
        sys.path.insert(0, _workspace)
    _import_workspace_package(_workspace)
    from pbg_superpowers.composite_generator import _REGISTRY, discover_generators
    try:
        if not _REGISTRY:
            discover_generators()
    except Exception:  # noqa: BLE001 — best-effort; return whatever registered
        pass
    return {"generators": sorted(_REGISTRY.keys())}


def _handle(method: str, params: dict) -> dict:
    """Dispatch one method (spec §11)."""
    if method == "ping":
        return {"ok": True, "uptime_s": time.monotonic() - _started}
    if method == "initialize":
        # Handshake. build_core is deferred to the environment methods (warm on
        # first query, protocol §17), keeping initialize cheap.
        return {
            "protocol_version": PROTOCOL_VERSION,
            "workspace": _workspace,
            "python": sys.version.split()[0],
            "pid": os.getpid(),
            "capabilities": _CAPABILITIES,
        }
    if method == "list_generators":
        return _list_generators()
    if method == "registry_catalog":
        return _registry_catalog()
    if method == "shutdown":
        return {"ok": True}
    raise _MethodError(-32601, f"unknown method: {method!r}")


def _serve(sock: socket.socket) -> None:
    """Serial request loop (spec §8): one request at a time, FIFO."""
    while True:
        req = _read_frame(sock)
        if req is None:  # parent closed the connection
            return
        rid = req.get("id")
        method = req.get("method")
        try:
            result = _handle(method, req.get("params") or {})
            _write_frame(sock, {"jsonrpc": "2.0", "id": rid, "result": result})
            if method == "shutdown":
                return
        except _MethodError as e:
            _write_frame(sock, {"jsonrpc": "2.0", "id": rid, "error": {
                "code": e.code, "message": e.message, "data": e.data}})
        except Exception as e:  # noqa: BLE001 — surface as a structured env error (spec §9)
            _write_frame(sock, {"jsonrpc": "2.0", "id": rid, "error": {
                "code": 2000, "message": str(e),
                "data": {"exc_type": type(e).__name__,
                         "traceback_tail": traceback.format_exc()[-2000:]}}})


def main(argv=None) -> int:
    global _workspace
    parser = argparse.ArgumentParser(prog="env_worker")
    parser.add_argument("--socket-fd", type=int, required=True)
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args(argv)
    _workspace = args.workspace

    # Wrap the inherited fd as an AF_UNIX stream socket (the socketpair peer).
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM, fileno=args.socket_fd)
    try:
        _serve(sock)
    finally:
        try:
            sock.close()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
