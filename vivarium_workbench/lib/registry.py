"""Build the process/type registry payload for a workspace.

Extracted from ``vivarium_workbench.server._get_registry_data`` so the
FastAPI seam (``api/app.py``) can call it without importing the stdlib server
module.  The single implementation is shared: ``server.py`` re-imports
``build_registry`` and keeps its old ``_get_registry_data`` name as a thin
wrapper.

The module-level ``_REGISTRY_CACHE`` is the live cache used by both paths.
Call ``clear_registry_cache()`` to invalidate it on workspace changes
(``server.py`` calls this wherever it previously wrote
``_REGISTRY_CACHE["data"] = None`` inline).
"""

from __future__ import annotations

import time
from pathlib import Path

from vivarium_workbench.lib import emitters


# ---------------------------------------------------------------------------
# Module-level registry cache (shared by server.py thin wrapper + FastAPI route)
# ---------------------------------------------------------------------------

# Keyed by ``str(ws_root)`` -> ``{"data": <payload>, "ts": <epoch>}`` (slice 3
# of the multi-workspace refactor). A single global slot served one session's
# registry catalog to another under multi-session; the catalog is workspace-
# specific (the workspace's own package + declared imports), so it must key on
# the workspace.
_REGISTRY_CACHE: dict = {}
_REGISTRY_TTL = 30.0  # seconds


def clear_registry_cache() -> None:
    """Invalidate the registry cache so the next call rebuilds from scratch."""
    _REGISTRY_CACHE.clear()


# ---------------------------------------------------------------------------
# workspace.yaml dashboard-block helpers
# ---------------------------------------------------------------------------

def _dashboard_config(ws_data: dict | None) -> dict:
    """Return the ``dashboard:`` block from workspace.yaml as a dict (or {}).

    The block is the single source for per-workspace dashboard customization::

        dashboard:
          name: "sms-ecoli dashboard"        # header/brand + <title>
          logo: assets/sms-ecoli-logo.png    # workspace-relative logo file
          registry:
            include: [pkg-a, pkg-b]           # display allow-list (by package)

    All keys optional; missing block → {} → current default behavior.
    """
    if not isinstance(ws_data, dict):
        return {}
    dash = ws_data.get("dashboard")
    return dash if isinstance(dash, dict) else {}


def _registry_modules_override(ws_data: dict | None) -> list | None:
    """Resolve ``dashboard.registry.modules`` to a list of entries, or ``None``.

    The ``modules`` block is the per-workspace catalog OVERRIDE: when present
    and non-empty it REPLACES pbg's default catalog (unlike ``include``, which
    only filters the default). Each entry is either:

      - a bare string  → the name of an entry in pbg's default catalog whose
        full metadata should be inherited (or a minimal stub if pbg doesn't
        ship it); or
      - a dict         → a custom catalog module that pbg doesn't ship
        (e.g. ``viva-munk``), used verbatim with missing display fields filled.

    Returns ``None`` when unset/not-a-list/empty → caller falls back to the
    default catalog + ``include`` filter (unchanged behavior).
    """
    dash = _dashboard_config(ws_data)
    reg = dash.get("registry")
    if not isinstance(reg, dict):
        return None
    modules = reg.get("modules")
    if not isinstance(modules, list) or not modules:
        return None
    return modules


def _modules_override_pkgs(ws_data: dict | None) -> set[str] | None:
    """Normalized top-level package names named by ``dashboard.registry.modules``.

    Used so the process-registry (``/api/registry``) filter shows the SAME set
    as the override catalog even when no explicit ``include`` is present. For a
    string entry the package is the name itself; for a dict entry the ``package``
    field (falling back to the snake_case ``name``). Returns ``None`` when no
    override is configured.
    """
    modules = _registry_modules_override(ws_data)
    if modules is None:
        return None

    def _norm(s) -> str:
        return str(s or "").strip().replace("-", "_").split(".")[0]

    pkgs: set[str] = set()
    for entry in modules:
        if isinstance(entry, str):
            n = _norm(entry)
            if n:
                pkgs.add(n)
        elif isinstance(entry, dict):
            pkg = entry.get("package") or entry.get("name")
            n = _norm(pkg)
            if n:
                pkgs.add(n)
    return pkgs or None


def _registry_include_pkgs(ws_data: dict | None) -> set[str] | None:
    """Resolve ``dashboard.registry.include`` to a set of normalized top-level
    package names (dashes → underscores), or ``None`` when unset.

    ``None`` means "no filter" (show everything — current behavior); an empty
    list also means no filter (treated as unset, to avoid an accidental
    blank registry).

    When ``dashboard.registry.modules`` (the catalog override) is present but
    no explicit ``include`` is given, the allow-list is DERIVED from the module
    names — so the process-registry class grid stays in sync with the override
    catalog (same set: workspace-self + each declared module).
    """
    dash = _dashboard_config(ws_data)
    reg = dash.get("registry")
    if not isinstance(reg, dict):
        return None
    include = reg.get("include")
    if not isinstance(include, list) or not include:
        # No explicit include: derive from the modules override (if any) so the
        # process registry matches the override catalog. The workspace's own
        # package is always allowed alongside the declared modules.
        derived = _modules_override_pkgs(ws_data)
        if derived is None:
            return None
        slug = str((ws_data or {}).get("name", "") or "").strip().replace("-", "_")
        pkg_path = str((ws_data or {}).get("package_path", "") or "").strip().replace("-", "_")
        for s in (slug, pkg_path):
            if s:
                derived.add(s)
        return derived or None
    pkgs = {
        str(p).strip().replace("-", "_").split(".")[0]
        for p in include
        if str(p).strip()
    }
    return pkgs or None


def _build_reexport_map(include: set[str]) -> dict[str, str]:
    """Map re-exported classes → the allow-listed package that re-exports them.

    For each allow-listed package, import it and scan its top-level namespace
    (``dir(mod)``) for classes whose ``__module__`` top-level segment is a
    DIFFERENT package. Those are re-exports: a class defined elsewhere (e.g.
    ``spatio_flux.visualizations.field_heatmap.FieldHeatmap``) that the
    allow-listed package surfaces as part of its own API (e.g. exposed as
    ``viva_munk.FieldHeatmap``).

    The returned map is keyed by the class's full definition address
    (``def_module + '.' + qualname``, e.g.
    ``spatio_flux.visualizations.field_heatmap.FieldHeatmap``) AND, as a
    looser fallback, by ``(def_top_pkg, class_name)`` joined as
    ``"<def_top_pkg>::<name>"``. The value is the re-exporting package's
    normalized name (e.g. ``viva_munk``).

    Imports are guarded with try/except — a single bad import never blanks the
    registry; the worst case is a class is not surfaced. The allow-listed set is
    small (a handful of packages) so importing them here is cheap.
    """
    import importlib
    import inspect

    # Framework infrastructure packages are intentionally hidden from the
    # filtered registry; do NOT resurrect them as re-exports just because an
    # allow-listed package re-imports e.g. process_bigraph.Composite into its
    # namespace. Mirrors _FRAMEWORK_PKGS in the registry subprocess.
    _FRAMEWORK_PKGS = {
        "process_bigraph", "bigraph_schema", "bigraph_viz",
        "pbg_superpowers", "vivarium_workbench",
    }

    reexports: dict[str, str] = {}
    for pkg in sorted(include):
        try:
            mod = importlib.import_module(pkg)
        except Exception:
            continue
        for attr in dir(mod):
            try:
                obj = getattr(mod, attr)
            except Exception:
                continue
            if not inspect.isclass(obj):
                continue
            def_mod = getattr(obj, "__module__", "") or ""
            def_top = def_mod.split(".")[0].replace("-", "_")
            if not def_top or def_top == pkg:
                continue  # defined in the re-exporting package itself → not a re-export
            if def_top in include:
                continue  # already surfaced by its own allow-listed package
            if def_top in _FRAMEWORK_PKGS:
                continue  # framework infra stays hidden; not a workspace re-export
            qualname = getattr(obj, "__qualname__", attr) or attr
            full_addr = f"{def_mod}.{qualname}"
            reexports[full_addr] = pkg
            reexports[f"{def_top}::{qualname}"] = pkg
    return reexports


# ---------------------------------------------------------------------------
# Registry post-processing helpers
# ---------------------------------------------------------------------------

def _mark_default_emitter(data: dict, ws_data: dict | None) -> None:
    """Set ``is_workspace_default: True`` on emitter entries that match
    ``ws_data['runtime']['default_emitter']``.

    The match is a case-insensitive substring check against the entry's
    ``name`` (e.g. ``'parquet'`` matches ``ParquetEmitter``). All emitter
    entries get the field set explicitly (True or False) so the frontend
    can render the badge consistently. No-op when ``ws_data`` is missing
    or has no runtime block.
    """
    if not isinstance(data, dict):
        return
    processes = data.get("processes") or []
    default_emitter = ""
    if isinstance(ws_data, dict):
        rt = ws_data.get("runtime") or {}
        if isinstance(rt, dict):
            # Normalize the declared emitter NAME via the broker (lowercase +
            # strip). Deliberately the name, NOT its output_kind — the badge
            # matches against class names (ParquetEmitter), so aliasing
            # xarray→zarr here would break the XArrayEmitter match.
            default_emitter = emitters.normalize_emitter_name(rt.get("default_emitter"))
    needle = default_emitter
    for p in processes:
        if not isinstance(p, dict):
            continue
        if p.get("kind") != "emitter":
            continue
        name = str(p.get("name") or "")
        p["is_workspace_default"] = bool(needle) and (needle in name.lower())
    # Expose the resolved value at the top level for convenience / debugging.
    data["default_emitter"] = default_emitter or None


def _registry_imports_meta(ws_data: dict | None) -> list[dict]:
    """Return per-imported-repository metadata from ``workspace.yaml::imports``.

    Each entry: ``{name, package, source, ref, description}`` where ``package``
    is the normalized top-level Python package (so the frontend can match it
    against each registry class's ``address`` prefix and list the
    processes/steps that repo contributes). Tolerates both the dict form
    (keyed by catalog name) and the list-of-dicts form. Never raises.
    """
    out: list[dict] = []
    imports_raw = (ws_data or {}).get("imports") or []
    items: list[tuple[str, dict]] = []
    if isinstance(imports_raw, dict):
        for cat_name, v in imports_raw.items():
            items.append((str(cat_name), v if isinstance(v, dict) else {}))
    elif isinstance(imports_raw, list):
        for entry in imports_raw:
            if isinstance(entry, dict):
                items.append((str(entry.get("name") or ""), entry))
            elif isinstance(entry, str):
                items.append((entry, {}))
    for cat_name, v in items:
        pkg = (v.get("package") or cat_name).replace("-", "_").split(".")[0]
        if not pkg:
            continue
        out.append({
            "name": cat_name or pkg,
            "package": pkg,
            "source": v.get("source"),
            "ref": v.get("ref"),
            "description": (v.get("description") or "").strip(),
        })
    out.sort(key=lambda e: e["name"].lower())
    return out


def _apply_registry_include_filter(data: dict, ws_data: dict | None) -> None:
    """Filter ``data['processes']`` to only classes from allow-listed packages.

    Display-only: matches each entry's originating top-level package (derived
    from its ``address`` = ``module.qualname``, falling back to the entry
    ``name`` if it is dotted) against the normalized
    ``dashboard.registry.include`` set. Dashes/underscores are normalized on
    both sides (``pbg-bioreactordesign`` ↔ ``pbg_bioreactordesign``).

    Re-exports are honored: a class DEFINED in a non-allow-listed package but
    RE-EXPORTED in an allow-listed package's top-level namespace (e.g.
    ``viva_munk.FieldHeatmap``, defined in ``spatio_flux``) survives the filter
    and is re-attributed to the re-exporting package — its ``source`` becomes
    ``in_workspace`` and its top-level package tag flips to the re-exporter, so
    the UI groups it under (e.g.) viva_munk rather than spatio_flux. The true
    definition module is preserved in ``aliases`` so the attribution is not
    misleading. Classes from a non-allow-listed package that are NOT re-exported
    stay filtered out.

    No-op when no include list is configured (current behavior: show all).
    Allow-listed packages surface regardless of in_workspace/framework/
    environment_only classification.
    """
    if not isinstance(data, dict):
        return
    include = _registry_include_pkgs(ws_data)
    if include is None:
        return

    def _top_pkg(entry: dict) -> str:
        addr = str(entry.get("address") or "")
        mod = addr
        # address is "module.path.ClassName"; the module is everything we have,
        # but the qualname tail is the class. The top-level package is just the
        # first dotted segment, so we can take it directly from the address.
        if not mod:
            mod = str(entry.get("name") or "")
        return mod.split(".")[0].replace("-", "_")

    # Build the re-export map (guarded so a bad import never blanks the grid).
    try:
        reexports = _build_reexport_map(include)
    except Exception:
        reexports = {}

    def _reexporter(entry: dict) -> str | None:
        """Return the allow-listed pkg that re-exports this entry, else None."""
        if not reexports:
            return None
        addr = str(entry.get("address") or "").strip()
        if addr and addr in reexports:
            return reexports[addr]
        # Looser match: definition top-level package + class name. The class
        # name is the last segment of the address (or the entry name).
        def_top = _top_pkg(entry)
        cls_name = addr.split(".")[-1] if addr else str(entry.get("name") or "")
        key = f"{def_top}::{cls_name}"
        return reexports.get(key)

    procs = data.get("processes") or []
    kept: list[dict] = []
    for p in procs:
        if not isinstance(p, dict):
            continue
        own_pkg = _top_pkg(p)
        if own_pkg in include:
            kept.append(p)
            continue
        # Always surface emitters regardless of the include allow-list. They are
        # the workspace's I/O backends (the configured runtime.default_emitter is
        # one of them) and live in framework/env packages (process_bigraph,
        # pbg_emitters) outside the include list — so a repo-scoped include like
        # [v2ecoli] would otherwise leave the Registry's Emitters section empty.
        if p.get("kind") == "emitter":
            kept.append(p)
            continue
        reexporter = _reexporter(p)
        if reexporter is not None:
            # Re-attribute to the re-exporting package: keep the true definition
            # module in aliases (so it is not misleading), flip the address's
            # top-level segment and source classification to the re-exporter.
            true_addr = str(p.get("address") or "")
            aliases = list(p.get("aliases") or [])
            if true_addr and true_addr not in aliases:
                aliases.append(true_addr)
            p["aliases"] = aliases
            p["reexported_from"] = own_pkg
            p["source"] = "in_workspace"
            # Re-tag the address's top-level package so _top_pkg / the UI group
            # it under the re-exporter. The class is re-exported as
            # ``<reexporter>.<ClassName>``.
            cls_name = true_addr.split(".")[-1] if true_addr else str(p.get("name") or "")
            p["address"] = f"{reexporter}.{cls_name}"
            kept.append(p)
    data["processes"] = kept
    # Record what was applied for debugging / frontend awareness.
    data["registry_include"] = sorted(include)


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_registry(ws_root: Path, *, bypass_cache: bool = False) -> dict:
    """Return registry data from build_core() subprocess, with 30s caching.

    Always returns ``{processes: [...], types: [...]}`` plus optional ``error``
    key.  Each process entry includes a ``source`` field:

      - ``"in_workspace"`` — class belongs to the workspace's own package or a
        declared import (workspace.yaml.imports).
      - ``"framework"`` — class is from the process-bigraph framework
        infrastructure (process_bigraph, bigraph_schema, bigraph_viz,
        pbg_superpowers, vivarium_workbench).
      - ``"environment_only"`` — discovered via allocate_core() entry-point scan
        but not declared in workspace.yaml. Installed in the Python env but not
        explicitly imported by this workspace.

    Never raises.  Parameterised on ``ws_root`` so the FastAPI route can pass
    the workspace path directly without touching the ``WORKSPACE`` global.

    Parameters
    ----------
    ws_root:
        Workspace root directory (must contain ``workspace.yaml``).
    bypass_cache:
        When ``True`` forces a fresh subprocess run even if the cache is warm.
    """
    now = time.time()
    _cache_key = str(ws_root)
    _slot = _REGISTRY_CACHE.get(_cache_key)
    if not bypass_cache and _slot is not None:
        if now - _slot["ts"] < _REGISTRY_TTL:
            return _slot["data"]

    try:
        import yaml

        ws_yaml = ws_root / "workspace.yaml"
        ws_data = yaml.safe_load(ws_yaml.read_text(encoding="utf-8"))
        # Query the pooled env worker for the raw {processes, types, workspace_pkgs}.
        # This was an embedded ``sys.executable`` subprocess running build_core +
        # introspection on EVERY call (15s timeout). The same introspection now lives
        # in ``env_worker._registry_catalog`` (ported verbatim, verified byte-equivalent
        # in #502) and runs in a WARM pooled worker — so build_core is amortized
        # (measured 8s cold -> 0s warm on v2ecoli) instead of paid per request. Same
        # interpreter (sys.executable) as the old subprocess; the per-workspace venv
        # interpreter arrives with EnvironmentResolver.
        from vivarium_workbench.lib.env_worker_pool import get_pool
        data = get_pool().call(ws_root, "registry_catalog")

        # Annotate emitter entries with is_workspace_default per
        # workspace.yaml::runtime.default_emitter. ws_data was loaded above;
        # treat the emitter-name match permissively (case-insensitive substring
        # against the class name, e.g. 'parquet' → ParquetEmitter).
        _mark_default_emitter(data, ws_data)
        # Optional display-only allow-list: workspace.yaml::dashboard.registry.include.
        # When set, the Registry tab shows ONLY classes whose originating package
        # is in the list (discovery is unchanged). No-op when unset → current
        # behavior (show everything).
        _apply_registry_include_filter(data, ws_data)
        # Imported-repositories metadata (workspace.yaml::imports): name, source
        # URL, ref, description — so the Registry can show each imported repo
        # alongside the processes/steps it contributes (grouped by package).
        data["imports"] = _registry_imports_meta(ws_data)
    except Exception as e:
        data = {"error": str(e), "processes": [], "types": []}

    _REGISTRY_CACHE[_cache_key] = {"data": data, "ts": now}
    return data


def clear_cache() -> None:
    """Reset the registry cache (data + ts) on a workspace switch.

    Mirrors the inline ``_REGISTRY_CACHE["data"]=None; ["ts"]=0.0`` that
    ``server._invalidate_workspace_caches`` previously did, so the registry is
    invalidated identically via active_workspace.invalidate(). Distinct from
    :func:`clear_registry_cache` (data-only), kept for its other call sites.
    """
    _REGISTRY_CACHE.clear()


# Register this module's cache-clear with the active-workspace registry so a
# workspace switch invalidates it via active_workspace.invalidate().
from . import active_workspace as _aw  # noqa: E402
_aw.register_clear_cb(clear_cache)
