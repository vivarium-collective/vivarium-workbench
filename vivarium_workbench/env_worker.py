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
                 "study_readout_check", "attach_process_docs", "discover_composites",
                 "validate_generated_visualization", "run_study_analyses", "viz_class_inputs", "render_viz_doc", "report_core_snapshot", "reexport_map", "shutdown"]

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


def _attach_process_docs_method(params: dict) -> dict:
    """Attach per-process docstrings to an already-resolved state ``document``
    passed inline (spec §11 ``{document}``). Read-shaped: the workbench owns the
    science file, hands us the doc, and we import the process classes to read
    their descriptions — so the HTTP process imports no workspace Python for the
    composite-state static-fallback / spec branches."""
    if _workspace and _workspace not in sys.path:
        sys.path.insert(0, _workspace)
    return {"document": _attach_process_docs((params or {}).get("document"))}


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


def _discover_composites() -> dict:
    """Generator composite entries for this environment (spec §11).

    Imports the workspace package + runs pbg_superpowers generator discovery in
    THIS process, returning the raw ``{gid: entry}`` **generator** half as JSON
    (the workbench keeps its pure FS/YAML spec scan + dedup and merges these in).
    So the HTTP process no longer imports/executes ``@composite_generator``
    modules to build `discover_all_composites` / `known_composite_ids`."""
    if _workspace and _workspace not in sys.path:
        sys.path.insert(0, _workspace)
    _import_workspace_package(_workspace)

    reg_keys: list = []
    try:
        from pbg_superpowers.composite_generator import _REGISTRY, discover_generators
        if not _REGISTRY:
            try:
                discover_generators()
            except Exception:  # noqa: BLE001
                pass
        reg_keys = list(_REGISTRY.keys())
    except Exception:  # noqa: BLE001
        pass

    out: dict = {}
    try:
        from pbg_superpowers.composite_discovery import discover_all
        merged = discover_all() or {}
    except Exception:  # noqa: BLE001 — no generator discovery available → spec-only
        merged = {}
    for gid, entry in merged.items():
        if isinstance(entry, dict) and entry.get("kind") == "generator":
            out[gid] = {
                "name": entry.get("name"),
                "description": entry.get("description", ""),
                "parameters": entry.get("parameters") or {},
                "module": entry.get("module"),
                "default_n_steps": entry.get("default_n_steps"),
                "visualizations": list(entry.get("visualizations") or []),
            }
    # Belt-and-suspenders: any registry key discover_all missed (mirrors the old
    # known_composite_ids direct-registry union).
    for gid in reg_keys:
        out.setdefault(gid, {"name": None, "description": "", "parameters": {},
                             "module": None, "default_n_steps": None, "visualizations": []})
    return {"generators": out}


def _validate_generated_visualization(params: dict) -> dict:
    """Smoke-test a just-accepted generated visualization module (spec §11), in
    the workspace's env (import-verify → `build_core()` → class discovery) — the
    write-path equivalent of the old in-process ``visualization_accept`` verify.
    A warm worker may already hold the module, so **reload** it (picks up an edit).
    Returns ``{"ok": True}`` or a structured ``{"error", "code"}`` (import_failed /
    build_core_failed / class_not_found) — the workbench maps ``error`` to a 500."""
    import importlib

    pkg = (params or {}).get("pkg") or ""
    snake = (params or {}).get("module") or ""
    class_name = (params or {}).get("class_name") or ""
    if _workspace and _workspace not in sys.path:
        sys.path.insert(0, _workspace)

    mod_name = f"{pkg}.visualizations.{snake}"
    try:
        if mod_name in sys.modules:
            importlib.reload(sys.modules[mod_name])
        else:
            __import__(mod_name)
        pkg_viz_mod = f"{pkg}.visualizations"
        if pkg_viz_mod in sys.modules:
            importlib.reload(sys.modules[pkg_viz_mod])
    except Exception as e:  # noqa: BLE001
        return {"error": f"generated file failed to import: {type(e).__name__}: {e}",
                "code": "import_failed"}

    # Invalidate the cached base core so build_core re-walks the new module.
    try:
        import bigraph_schema.core as _bsc
        _bsc._cached_base_core = None
    except Exception:  # noqa: BLE001
        pass
    try:
        core_module = __import__(f"{pkg}.core", fromlist=["build_core"])
        core_module.build_core()
    except Exception as e:  # noqa: BLE001
        return {"error": ("workspace build_core() failed after importing the generated "
                          f"file: {type(e).__name__}: {e}"), "code": "build_core_failed"}

    if class_name:
        found = False
        mod = sys.modules.get(mod_name)
        if mod is not None:
            for attr_val in vars(mod).values():
                if not isinstance(attr_val, type):
                    continue
                if getattr(attr_val, "__name__", None) != class_name:
                    continue
                marker = getattr(attr_val, "is_visualization", None)
                if callable(marker):
                    try:
                        if marker() is True:
                            found = True
                            break
                    except Exception:  # noqa: BLE001
                        pass
                if not found:
                    try:
                        from pbg_superpowers.visualization import Visualization as _VizBase
                        if issubclass(attr_val, _VizBase) and attr_val is not _VizBase:
                            found = True
                            break
                    except ImportError:
                        pass
        if not found:
            return {"error": (f"class {class_name!r} not found in generated file after "
                              "import; check the @as_visualization name= argument matches"),
                    "code": "class_not_found"}
    return {"ok": True}


def _run_study_analyses(params: dict) -> dict:
    """Run a study's ``spec.analyses`` over its parquet output, in the workspace
    env (v2ecoli ``ANALYSIS_REGISTRY`` scale lookup + ``run_analyses``). Returns
    ``{"written": [paths], "errors": [dicts]}`` — never raises. Faithful port of
    ``study_run_post.build_analysis_options`` + the v2ecoli half of
    ``run_study_analyses``; the workbench keeps the parquet/sim_data path
    resolution."""
    import time
    import traceback
    from pathlib import Path

    p = params or {}
    entries = list(p.get("entries") or [])
    sweep_dir = p.get("sweep_dir")
    sim_data_path = p.get("sim_data_path")
    if _workspace and _workspace not in sys.path:
        sys.path.insert(0, _workspace)
    _import_workspace_package(_workspace)
    if not entries:
        return {"written": [], "errors": []}

    # 1. build_analysis_options: map entries → {scale: {name: params}} via the registry.
    try:
        from v2ecoli.workflow.analysis import ANALYSIS_REGISTRY
    except ImportError:
        return {"written": [], "errors": [
            {"error": "v2ecoli not installed; cannot resolve analysis scales"}]}
    analysis_options: dict = {}
    build_errors: list = []
    for entry in entries:
        name = entry.get("name")
        if not name:
            continue
        step_cls = ANALYSIS_REGISTRY.get(name)
        if step_cls is None:
            build_errors.append({"analysis": name,
                                 "error": f"unknown analysis {name!r} (not in ANALYSIS_REGISTRY)"})
            continue
        scale = getattr(step_cls, "scale", None)
        if not scale:
            build_errors.append({"analysis": name,
                                 "error": f"analysis {name!r} has no scale attribute"})
            continue
        analysis_options.setdefault(scale, {})[name] = entry.get("params") or {}
    if not analysis_options:
        return {"written": [], "errors": build_errors}

    # 2. Run the analyses + collect written files (mtime newer than call start).
    try:
        import v2ecoli.workflow.analyses  # noqa: F401 — register analysis ports
        from v2ecoli.workflow.analysis_runner import run_analyses
        t_start = time.time()
        results = run_analyses(str(sweep_dir), analysis_options, sim_data_path=sim_data_path)
        written: list = []
        sd = Path(sweep_dir)
        for sub in ("ptools", "viz"):
            sub_dir = sd / sub
            if sub_dir.is_dir():
                for f in sub_dir.iterdir():
                    if f.is_file() and f.stat().st_mtime >= t_start:
                        written.append(str(f))
        analysis_json = sd / "analysis.json"
        if analysis_json.is_file() and analysis_json.stat().st_mtime >= t_start:
            written.append(str(analysis_json))
        errors = list(build_errors)
        for scale_results in results.values():
            for aname, groups in (scale_results or {}).items():
                for gstr, val in (groups or {}).items():
                    if isinstance(val, dict) and "error" in val:
                        errors.append({"analysis": aname, "group": gstr, "error": val["error"]})
        return {"written": written, "errors": errors}
    except Exception as exc:  # noqa: BLE001 — never crash the run
        return {"written": [], "errors": [
            {"error": f"_run_study_analyses failed: {type(exc).__name__}: {exc}",
             "traceback": traceback.format_exc()}]}


# --- viz rendering (spec §11): build_core + viz-class registration + Composite.run
#     all in-worker (live viz classes + core can't cross the socket). Cached per
#     worker — build_core is ~15s and every viz render reuses it. --------------
_VIZ_CORE = None  # (core, registry) once built


def _build_viz_core():
    """Build the workspace core + register every Visualization class onto it
    (pbg_superpowers defaults + the whole Visualization subclass tree), cached per
    worker. Faithful port of study_run_post.render_study_visualizations' in-process
    core+registry build. Returns ``(core, registry_dict)``."""
    global _VIZ_CORE
    if _VIZ_CORE is not None:
        return _VIZ_CORE
    if _workspace and _workspace not in sys.path:
        sys.path.insert(0, _workspace)
    package_name, _pkgs, _ws = _workspace_meta(_workspace)
    core_module = __import__(f"{package_name}.core", fromlist=["build_core"])
    core = core_module.build_core()
    registry = dict(core.link_registry)

    try:
        from pbg_superpowers.visualizations import (
            Distribution, Heatmap, ParamVsObservable, PhaseSpace, TimeSeriesPlot,
        )
        for cls in (TimeSeriesPlot, ParamVsObservable, Distribution, PhaseSpace, Heatmap):
            core.register_link(cls.__name__, cls)
            registry[cls.__name__] = cls
    except ImportError:
        pass

    try:
        from pbg_superpowers.composite_generator import discover_generators
        from pbg_superpowers.visualization import Visualization
        discover_generators()  # force-load packages so @Visualization classes appear

        def _walk(cls):
            for sub in cls.__subclasses__():
                yield sub
                yield from _walk(sub)
        for sub in _walk(Visualization):
            if sub.__name__ in registry:
                continue
            try:
                core.register_link(sub.__name__, sub)
                registry[sub.__name__] = sub
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001 — discovery is best-effort
        pass

    _VIZ_CORE = (core, registry)
    return _VIZ_CORE


def _viz_class_inputs() -> dict:
    """``{class_name: declared_inputs}`` for every registered class (spec §11), so
    the workbench's ``build_viz_composite`` can assemble viz docs without holding
    the live class objects. Presence in the map == 'registered'."""
    _core, registry = _build_viz_core()
    out: dict = {}
    for name, cls in registry.items():
        try:
            inp = cls.__new__(cls).inputs()
            out[name] = inp if isinstance(inp, dict) else {}
        except Exception:  # noqa: BLE001
            out[name] = {}
    return {"inputs": out}


def _render_viz_doc(params: dict) -> dict:
    """Render ONE viz composite doc → HTML: ``Composite({'state': doc}, core).run(1)``
    against the cached viz core, extracting ``output_store`` (spec §11). Faithful
    port of the old in-process ``build_and_run`` hook."""
    viz_doc = (params or {}).get("viz_doc")
    core, _registry = _build_viz_core()
    from process_bigraph import Composite
    composite = Composite({"state": viz_doc}, core=core)
    composite.run(1)
    state = composite.state
    html = state.get("output_store")
    if isinstance(html, dict):
        html = html.get("value") or html.get("_value") or ""
    return {"html": html if isinstance(html, str) else ""}


def _report_core_snapshot(params: dict) -> dict:
    """Registry snapshot (process/type names) + the workspace document for the
    report render (spec §11) — imports ``<pkg>.core`` (registry_snapshot) +
    ``<pkg>.document`` (build_document) in the worker. Faithful port of
    report._load_registry + _load_document. Returns finite JSON."""
    package_path = (params or {}).get("package_path")
    if _workspace and _workspace not in sys.path:
        sys.path.insert(0, _workspace)

    registry = {"processes": [], "types": []}
    warning = None
    if package_path:
        try:
            core = __import__(f"{package_path}.core", fromlist=["build_core"])
            build_core = getattr(core, "build_core", None)
            registry_snapshot = getattr(core, "registry_snapshot", None)
            if build_core is None or registry_snapshot is None:
                warning = (f"{package_path}.core imported but missing build_core() or "
                           "registry_snapshot().")
            else:
                build_core()
                snap = registry_snapshot()

                def _names(items):
                    if not items:
                        return []
                    if isinstance(items[0], str):
                        return list(items)
                    return [it.get("name", str(it)) for it in items]
                registry = {"processes": _names(snap.get("processes", [])),
                            "types": _names(snap.get("types", []))}
        except ModuleNotFoundError:
            warning = (f"Package '{package_path}' is not importable — registry shown as "
                       "empty. Install it in the workspace venv or run /pbg-pull-processes.")
        except Exception as exc:  # noqa: BLE001
            warning = f"{package_path}.core raised {type(exc).__name__}: {exc}"

    document: dict = {}
    if package_path:
        try:
            doc_mod = __import__(f"{package_path}.document", fromlist=["build_document"])
            build_document = getattr(doc_mod, "build_document", None)
            if build_document is not None:
                document = build_document() or {}
        except Exception:  # noqa: BLE001
            document = {}

    return {"registry": registry, "registry_warning": warning, "document": document}


def _reexport_map(params: dict) -> dict:
    """Map re-exported classes → the allow-listed package that re-exports them
    (spec §11) — imports each allow-listed package + scans its namespace in the
    worker. Faithful port of registry._build_reexport_map."""
    import importlib
    import inspect

    include = set((params or {}).get("include") or [])
    if _workspace and _workspace not in sys.path:
        sys.path.insert(0, _workspace)
    framework = {"process_bigraph", "bigraph_schema", "bigraph_viz",
                 "pbg_superpowers", "vivarium_workbench"}
    reexports: dict = {}
    for pkg in sorted(include):
        try:
            mod = importlib.import_module(pkg)
        except Exception:  # noqa: BLE001
            continue
        for attr in dir(mod):
            try:
                obj = getattr(mod, attr)
            except Exception:  # noqa: BLE001
                continue
            if not inspect.isclass(obj):
                continue
            def_mod = getattr(obj, "__module__", "") or ""
            def_top = def_mod.split(".")[0].replace("-", "_")
            if not def_top or def_top == pkg:
                continue
            if def_top in include or def_top in framework:
                continue
            qualname = getattr(obj, "__qualname__", attr) or attr
            reexports[f"{def_mod}.{qualname}"] = pkg
            reexports[f"{def_top}::{qualname}"] = pkg
    return {"reexports": reexports}


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
    if method == "attach_process_docs":
        return _attach_process_docs_method(params)
    if method == "discover_composites":
        return _discover_composites()
    if method == "validate_generated_visualization":
        return _validate_generated_visualization(params)
    if method == "run_study_analyses":
        return _run_study_analyses(params)
    if method == "viz_class_inputs":
        return _viz_class_inputs()
    if method == "render_viz_doc":
        return _render_viz_doc(params)
    if method == "report_core_snapshot":
        return _report_core_snapshot(params)
    if method == "reexport_map":
        return _reexport_map(params)
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
