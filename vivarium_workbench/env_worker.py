"""Env worker — the per-session subprocess that holds a workspace's compute
environment out of the HTTP process.

Full contract: ``docs/env-worker-protocol.md``. This is the **worker program**
(spec §4): a single self-contained file, shipped by the workbench but run on the
workspace's interpreter by path, importing **only the standard library** (plus,
in later slices, what the workspace venv already has). It never imports
``vivarium_workbench``.

**Scope so far:** the transport + lifecycle (``initialize`` / ``ping`` /
``shutdown``) and the environment queries ``list_generators``,
``registry_catalog``, and ``viz_classes`` — each imports the workspace's own
package (and, for the latter two, calls ``build_core``) **in this process**, so
the imports the HTTP process must not do live here instead. The remaining
``build_core``-backed methods (``resolve_composite_state``, ``observables`` …)
land in later slices. These import pbg_superpowers + the workspace package (both
workspace-venv deps, spec §4); everything else is stdlib.

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
    # default=str coerces the odd non-JSON leaf (numpy scalar, Path, …) that
    # survives a state doc, matching the old composite subprocess's
    # json.dumps(default=str). Cheap for the string-only methods (never fires).
    body = json.dumps(obj, default=str).encode("utf-8")
    sock.sendall(struct.pack(">I", len(body)) + body)


_CAPABILITIES = ["initialize", "ping", "list_generators", "registry_catalog",
                 "viz_classes", "resolve_composite_state", "observables",
                 "study_readout_check", "shutdown"]

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


def _list_visualizations() -> dict:
    """Registered Visualization / Analysis classes for this environment (spec §11).

    A faithful in-worker port of ``visualization_classes.list_visualization_classes``
    — build the workspace core, snapshot its ``link_registry``, inject the default
    ``pbg_superpowers`` viz classes + any workspace-local ``<pkg>.visualizations``
    submodules, filter to ``Visualization`` subclasses, and append the v2ecoli
    ``Analysis`` steps. Returns the JSON ``{"classes": [...]}`` (the live classes
    can't cross the socket, so this introspection runs where they live). Tolerant:
    a build_core / import failure degrades to the classes still discoverable."""
    from pathlib import Path

    import yaml

    if _workspace and _workspace not in sys.path:
        sys.path.insert(0, _workspace)

    # Build the class registry from the workspace's core module (tolerant).
    try:
        ws_data = (
            yaml.safe_load((Path(_workspace) / "workspace.yaml").read_text(encoding="utf-8")) or {}
        )
        pkg = ws_data.get("package_path") or ("pbg_" + str(ws_data.get("name", "")).replace("-", "_"))
        core_module = __import__(f"{pkg}.core", fromlist=["build_core"])
        core = core_module.build_core()
        registry: dict = dict(core.link_registry)
    except Exception:  # noqa: BLE001 — a broken core still yields the defaults below
        registry = {}
        ws_data = {}

    # Inject the standard pbg-superpowers visualization classes.
    try:
        from pbg_superpowers.visualizations import (
            Distribution, Heatmap, ParamVsObservable, PhaseSpace, TimeSeriesPlot,
        )
        for cls in [TimeSeriesPlot, ParamVsObservable, Distribution, PhaseSpace, Heatmap]:
            registry[cls.__name__] = cls
    except ImportError:
        pass

    # Inject workspace-local viz classes (non-pip-installed) from <pkg>.visualizations.
    try:
        import importlib as _importlib
        import pkgutil as _pkgutil

        from pbg_superpowers.visualization import Visualization as _VizBase
        _pkg_name = ws_data.get("package_path") or (
            "pbg_" + str(ws_data.get("name", "")).replace("-", "_"))
        viz_pkg = _importlib.import_module(f"{_pkg_name}.visualizations")
        for _, modname, _ in _pkgutil.iter_modules(viz_pkg.__path__):
            try:
                mod = _importlib.import_module(f"{_pkg_name}.visualizations.{modname}")
                for attr_val in vars(mod).values():
                    if not isinstance(attr_val, type):
                        continue
                    if attr_val is _VizBase:
                        continue
                    if issubclass(attr_val, _VizBase):
                        registry[attr_val.__name__] = attr_val
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass

    try:
        from pbg_superpowers.visualization import Visualization as _VB
    except ImportError:
        _VB = None

    def _is_viz(cls):
        if _VB is not None and cls is _VB:
            return False
        marker = getattr(cls, "is_visualization", None)
        if callable(marker):
            try:
                if marker() is True:
                    return True
            except Exception:  # noqa: BLE001
                pass
        if _VB is not None:
            try:
                if isinstance(cls, type) and issubclass(cls, _VB):
                    return True
            except TypeError:
                pass
        return False

    per_cls: dict = {}
    for name, cls in registry.items():
        if not _is_viz(cls) or name == "Visualization":
            continue
        existing = per_cls.get(id(cls))
        if existing is None or len(name) < len(existing[0]):
            per_cls[id(cls)] = (name, cls)

    out = []
    for name, cls in sorted(per_cls.values(), key=lambda kv: kv[0]):
        try:
            doc = (cls.__doc__ or "").strip().split("\n", 1)[0] if cls.__doc__ else ""
        except Exception:  # noqa: BLE001
            doc = ""
        out.append({"address": f"local:{name}", "name": name, "doc": doc, "kind": "visualization"})

    # Append Analysis classes (process-bigraph Steps) from v2ecoli, if installed.
    try:
        import v2ecoli.workflow.analyses  # noqa: F401  (import-time registration)
        from v2ecoli.workflow.analysis import ANALYSIS_REGISTRY, Analysis
        for _name, _cls in sorted(ANALYSIS_REGISTRY.items()):
            if isinstance(_cls, type) and issubclass(_cls, Analysis):
                try:
                    _doc = (_cls.__doc__ or "").strip().split("\n")[0]
                except Exception:  # noqa: BLE001
                    _doc = ""
                out.append({
                    "address": f"local:{_cls.__module__}.{_cls.__qualname__}",
                    "name": _name, "doc": _doc, "kind": "analysis",
                })
    except Exception:  # noqa: BLE001
        pass

    return {"classes": out}


# --- process-doc decoration (ported from lib/process_docs.py; the worker can't
#     import vivarium_workbench, and these must run where the workspace classes +
#     the built doc's numpy values live, i.e. in this process) ------------------
def _pd_describe_class(cls) -> str:
    """Formal description for a process/step class via ``Edge.describe()`` on an
    uninitialized instance, falling back to ``description`` / ``__doc__``."""
    try:
        inst = cls.__new__(cls)  # uninitialized — skips __init__/core requirement
        describe = getattr(inst, "describe", None)
        if callable(describe):
            text = describe()
            if isinstance(text, str) and text.strip():
                return text.strip()
    except Exception:  # noqa: BLE001
        pass
    desc = getattr(cls, "description", "")
    if isinstance(desc, str) and desc.strip():
        return desc.strip()
    doc = getattr(cls, "__doc__", None)
    return doc.strip() if isinstance(doc, str) else ""


def _pd_doc_for_address(address: str) -> str:
    """Formal description for a ``local:<dotted.path>`` address, or ''."""
    import importlib
    if not isinstance(address, str) or not address:
        return ""
    addr = address.split(":", 1)[1] if ":" in address else address
    if "." not in addr:
        return ""  # bare registry name — can't import a dotted path
    module_path, _, cls_name = addr.rpartition(".")
    try:
        mod = importlib.import_module(module_path)
        cls = getattr(mod, cls_name, None)
        return _pd_describe_class(cls) if cls is not None else ""
    except Exception:  # noqa: BLE001
        return ""


def _summarize_large_values(node, max_list: int = 40, max_str: int = 2000):
    """Copy of a composite-state doc with large leaf VALUES summarized — the
    multi-MB numpy ``bulk`` store becomes ``⟨N items⟩`` so the response stays
    small. Pure; must run here (numpy can't cross the socket)."""
    if isinstance(node, dict):
        return {k: _summarize_large_values(v, max_list, max_str) for k, v in node.items()}
    if isinstance(node, (list, tuple)):
        if len(node) > max_list:
            return f"⟨{len(node)} items⟩"
        return [_summarize_large_values(v, max_list, max_str) for v in node]
    if isinstance(node, str):
        return node[:max_str] + "…" if len(node) > max_str else node
    if isinstance(node, (bytes, bytearray)):
        return f"⟨{len(node)} bytes⟩"
    try:
        n = len(node)
    except TypeError:
        return node
    return f"⟨{n} items⟩" if n > max_list else node


def _attach_process_docs(doc):
    """Walk a composite-state doc in place, setting ``node['doc']`` for each
    process/step from its address's class description. All failures swallowed."""
    _cache: dict = {}

    def walk(node):
        if isinstance(node, dict):
            if node.get("_type") in ("process", "step") and "doc" not in node:
                addr = node.get("address", "")
                if addr not in _cache:
                    _cache[addr] = _pd_doc_for_address(addr)
                d = _cache[addr]
                if d:
                    node["doc"] = d
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    try:
        walk(doc)
    except Exception:  # noqa: BLE001
        pass
    return doc


def _resolve_composite_state(params: dict) -> dict:
    """Build a ``@composite_generator``'s state (spec §11), summarized +
    doc-decorated. A faithful in-worker port of
    ``composite_state_views.composite_state_via_subprocess``'s embedded script —
    which ran under ``sys.executable``; now it runs on the workspace's own
    interpreter. Returns ``{state, module, emitters}`` on success,
    ``{__build_error__, emitters}`` if the build raised, ``{__not_registered__}``
    if ``ref`` is not a registered generator."""
    ref = (params or {}).get("ref")
    if _workspace and _workspace not in sys.path:
        sys.path.insert(0, _workspace)
    # Import the workspace's own package so its @composite_generators register
    # (discover_generators alone won't import a non-installed workspace package —
    # same priming _list_generators does).
    _import_workspace_package(_workspace)
    out: dict = {"__not_registered__": True}
    try:
        from pbg_superpowers.composite_generator import (
            _REGISTRY, build_generator, discover_generators, emitter_defaults,
        )
        if not _REGISTRY:
            discover_generators()
        entry = _REGISTRY.get(ref)
        if entry is not None:
            try:
                declared_emitters = emitter_defaults(entry)
            except Exception:  # noqa: BLE001
                declared_emitters = []
            try:
                doc = build_generator(entry)
                doc = _summarize_large_values(doc)
                _attach_process_docs(doc)
                out = {"state": doc, "module": getattr(entry, "module", None),
                       "emitters": declared_emitters}
            except Exception as e:  # noqa: BLE001
                out = {"__build_error__": str(e), "emitters": declared_emitters}
    except Exception as e:  # noqa: BLE001
        out = {"__build_error__": str(e)}
    return out


# --- observables (spec §11): build + available_observables + validate, all
#     in-worker (available_observables/validate_readouts need the live core +
#     polars, which live here). The workbench owns spec-file resolution and hands
#     us either a generator `ref` or an inline resolved `{state, schema}`. --------
_OBS_LINEAGE_AGENT_RE = None  # compiled lazily (re import kept local to workers)


def _obs_resolve_registry_ref(ref: str, keys):
    """Resolve a short composite ``ref`` to a canonical registry key by matching
    the trailing ``.composites.<slug>`` segment (else the last dotted segment),
    preferring the shortest match. Port of
    observables_views._resolve_registry_ref."""
    keys = list(keys)
    if ref in keys:
        return ref
    def tail(k):
        return k.rsplit(".composites.", 1)[-1] if ".composites." in k else k.rsplit(".", 1)[-1]
    rt = tail(ref)
    matches = [k for k in keys if tail(k) == rt]
    return min(matches, key=len) if matches else None


def _obs_augment_lineage_aliases(available: dict) -> dict:
    """Strip a leading ``agents.<n>.`` from every leaf/catalog key and add the
    remainder as an alias (whole-cell composites nest the cell under
    ``agents.<n>.`` but studies author bare single-cell paths). Only a leading
    ``agents.<n>.`` is stripped, never an arbitrary suffix, so a genuinely-absent
    observable still fails to match. Port of
    observables_views.augment_lineage_aliases (the agent-structure convention
    lives in the dashboard worker, not the general validator)."""
    import re
    global _OBS_LINEAGE_AGENT_RE
    if _OBS_LINEAGE_AGENT_RE is None:
        _OBS_LINEAGE_AGENT_RE = re.compile(r"^agents\.\d+\.(.+)$")
    leaves = list(available.get("leaves", []) or [])
    catalogs = dict(available.get("catalogs", {}) or {})
    seen = set(leaves)
    extra = []
    for leaf in leaves:
        m = _OBS_LINEAGE_AGENT_RE.match(leaf)
        if m and m.group(1) not in seen:
            extra.append(m.group(1))
            seen.add(m.group(1))
    for key, val in list(catalogs.items()):
        m = _OBS_LINEAGE_AGENT_RE.match(key)
        if m:
            catalogs.setdefault(m.group(1), val)
    return {"leaves": leaves + extra, "catalogs": catalogs}


def _obs_build_core():
    """Best-effort workspace ``build_core()`` for LabeledArray catalog resolution
    — tolerated if it fails (None; only static catalogs degrade)."""
    try:
        package_name, _pkgs, _ws = _workspace_meta(_workspace)
        mod = __import__(f"{package_name}.core", fromlist=["build_core"])
        return mod.build_core()
    except Exception:  # noqa: BLE001
        return None


def _obs_available(params: dict) -> dict:
    """Compute ``available_observables`` for a composite named by ``ref`` (a
    registered generator) OR given inline as ``{state, schema}`` (a resolved spec
    doc the workbench parsed). Returns ``{leaves, catalogs}`` on success, else a
    sentinel: ``{__no_validator__}`` / ``{__not_registered__}`` / ``{__build_error__}``
    / ``{__introspect_error__}``. Faithful to
    observables_views.build_composite_state_for_observables + available_observables."""
    if _workspace and _workspace not in sys.path:
        sys.path.insert(0, _workspace)
    _import_workspace_package(_workspace)
    try:
        from pbg_superpowers.readout_validation import available_observables
    except Exception as e:  # noqa: BLE001
        return {"__no_validator__": str(e)}

    core = _obs_build_core()
    ref = (params or {}).get("ref")
    if ref is not None:
        # Generator branch: resolve via the live registry (+ short-ref alias).
        entry = None
        apply_core_extensions = None
        build_generator = None
        try:
            from pbg_superpowers.composite_generator import (
                _REGISTRY,
                apply_core_extensions as _ace,
                build_generator as _bg,
                discover_generators,
            )
            apply_core_extensions, build_generator = _ace, _bg
            if not _REGISTRY:
                try:
                    discover_generators()
                except Exception:  # noqa: BLE001
                    pass
            entry = _REGISTRY.get(ref)
            if entry is None:
                canon = _obs_resolve_registry_ref(ref, _REGISTRY.keys())
                if canon is not None:
                    entry = _REGISTRY.get(canon)
        except ImportError:
            entry = None
        if entry is None:
            return {"__not_registered__": True}
        if core is not None and apply_core_extensions is not None:
            try:
                core = apply_core_extensions(entry, core)
            except Exception:  # noqa: BLE001
                pass
        try:
            doc = build_generator(entry, core=core)
        except Exception as e:  # noqa: BLE001
            return {"__build_error__": f"generator build failed: {e}"}
        if isinstance(doc, dict) and isinstance(doc.get("state"), dict):
            state, schema = doc["state"], doc.get("schema")
        else:
            state, schema = doc, None
    else:
        # Static branch: the workbench already resolved the spec file.
        state = (params or {}).get("state")
        schema = (params or {}).get("schema")

    try:
        available = available_observables(core, state, schema)
    except Exception as e:  # noqa: BLE001
        return {"__introspect_error__": f"observable introspection failed: {e}"}
    return {"leaves": available.get("leaves", []) or [],
            "catalogs": available.get("catalogs", {}) or {}}


def _observables(params: dict) -> dict:
    """``{ref}`` or ``{state, schema}`` → ``{leaves, catalogs}`` (or a sentinel)."""
    return _obs_available(params)


def _study_readout_check(params: dict) -> dict:
    """Validate a study's readouts against its composite's real structure
    (never-fabricate guard). Params carry the study ``spec`` inline plus the
    composite as ``ref`` or ``{state, schema}``. Returns ``{readouts}`` on
    success, else a sentinel (``__not_registered__`` / ``__build_error__`` /
    ``__no_validator__`` / ``__validate_error__``)."""
    spec = (params or {}).get("spec") or {}
    avail = _obs_available(params)
    if any(k.startswith("__") for k in avail):
        return avail  # not_registered / build_error / no_validator / introspect_error
    try:
        from pbg_superpowers.readout_validation import validate_readouts
    except Exception as e:  # noqa: BLE001
        return {"__no_validator__": str(e)}
    try:
        augmented = _obs_augment_lineage_aliases(avail)
        results = validate_readouts(spec, available=augmented)
    except Exception as e:  # noqa: BLE001
        return {"__validate_error__": f"readout validation failed: {e}"}
    return {"readouts": results}


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
    if method == "viz_classes":
        return _list_visualizations()
    if method == "resolve_composite_state":
        return _resolve_composite_state(params)
    if method == "observables":
        return _observables(params)
    if method == "study_readout_check":
        return _study_readout_check(params)
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
