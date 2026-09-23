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
from vivarium_workbench.lib import env_compat


# ---------------------------------------------------------------------------
# Module-level registry cache (shared by server.py thin wrapper + FastAPI route)
# ---------------------------------------------------------------------------

# Keyed by ``str(ws_root)`` -> ``{"data": <payload>, "ts": <epoch>}`` (slice 3
# of the multi-workspace refactor). A single global slot served one session's
# registry catalog to another under multi-session; the catalog is workspace-
# specific (the workspace's own package + declared imports), so it must key on
# the workspace.
_REGISTRY_CACHE: dict = {}

# Default 1h: registry contents (processes/types/use-counts) only change when
# modules are installed/removed or the workspace's own source changes, both of
# which call ``clear_registry_cache()`` explicitly -- so the TTL only needs to
# bound staleness between those events, not double as the primary invalidation
# path. A short TTL (previously a hardcoded 30s) instead meant every page load
# more than 30s after the last one re-paid the full post-processing cost
# (``_annotate_use_counts`` + ``process_study_stats``), which on an NFS-backed
# workspace was itself minutes -- see workspace_walk.py. Overridable per
# deployment via ``VIVARIUM_WORKBENCH_REGISTRY_TTL`` (seconds).
_REGISTRY_TTL_DEFAULT = 3600.0


def _registry_ttl() -> float:
    """Current registry-cache TTL in seconds (env-overridable; see above)."""
    raw = env_compat.get_env("REGISTRY_TTL")
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    return _REGISTRY_TTL_DEFAULT


def clear_registry_cache(ws_root: "Path | str | None" = None) -> None:
    """Invalidate the registry cache so the next call rebuilds from scratch.

    Always clears the in-memory TTL cache (``_REGISTRY_CACHE``, every
    workspace). When ``ws_root`` is given, also clears that workspace's
    on-disk catalog cache (``lib.catalog_disk_cache``) — pass it from any
    call site that just changed what's installed (catalog install/uninstall,
    a generated visualization import-verify) so the next build re-imports
    instead of replaying a now-stale disk snapshot. Best-effort: a disk-cache
    failure here never raises.
    """
    _REGISTRY_CACHE.clear()
    if ws_root is not None:
        try:
            from vivarium_workbench.lib import catalog_disk_cache
            catalog_disk_cache.clear(ws_root)
        except Exception:
            pass


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


def _reexport_map_via_worker(ws_root: "Path", include: set) -> dict:
    """The re-export map via the env worker (imports the allow-listed packages
    there, not in the HTTP process). Soft-degrade to ``{}`` — a bad import or an
    unavailable worker never blanks the registry grid."""
    from vivarium_workbench.lib.env_worker_client import EnvWorkerUnavailable
    from vivarium_workbench.lib.env_worker_pool import get_pool
    try:
        r = get_pool().call(ws_root, "reexport_map", {"include": sorted(include)})
        return r.get("reexports", {}) if isinstance(r, dict) else {}
    except EnvWorkerUnavailable:
        return {}


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


def _annotate_use_counts(data: dict, ws_root: "Path") -> None:
    """Set ``use_count`` on each registry process/step: the number of this
    workspace's composite generators + runner scripts that reference the class
    (by full address or class name). A cheap source-scan proxy for "how used is
    this process" so the Registry can sort most-used first — no composite builds.

    Best-effort: never raises; a class we can't count just gets ``use_count`` 0.
    """
    import re as _re
    from pathlib import Path as _Path

    from vivarium_workbench.lib.workspace_walk import iter_workspace_files

    procs = data.get("processes") or []
    if not procs:
        return

    ws_root = _Path(ws_root)
    _SKIP = ("/.venv/", "/node_modules/", "/.git/", "/out/", "/build-cache/",
             "/__pycache__/", "/.pbg/")

    # The old code globbed with separate pattern sets for composite generator
    # sources (`*/composites/*.py`, `*/composites/**/*.py`, `**/composites/*.py`)
    # and study runner scripts (`scripts/**/*.py`, `workspace/**/scripts/*.py`,
    # `workspace/studies/**/*.py`, `studies/**/*.py`). Both pattern sets reduce
    # to the same shape: any `.py` file with a `composites` (resp. `scripts` /
    # `studies`) directory anywhere among its ancestor path components.
    # `iter_workspace_files` walks the tree once, never following symlinked
    # dirs (so a symlinked `.venv` is never entered) -- bucket by that
    # ancestor-dir check instead of re-globbing per pattern.
    composite_texts: list[str] = []
    study_texts: list[str] = []
    try:
        for f in iter_workspace_files(ws_root, suffixes=(".py",)):
            sp = str(f)
            if any(s in sp for s in _SKIP):
                continue
            rel_parts = set(f.relative_to(ws_root).parts[:-1])
            if not (rel_parts & {"composites", "scripts", "studies"}):
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if "composites" in rel_parts:
                composite_texts.append(text)
            if rel_parts & {"scripts", "studies"}:
                study_texts.append(text)
    except Exception:
        pass

    for p in procs:
        addr = (p.get("address") or "")
        cname = addr.rsplit(".", 1)[-1] if addr else (p.get("name") or "")
        namere = _re.compile(r"\b" + _re.escape(cname) + r"\b") if cname else None

        def _count(texts):
            n = 0
            for txt in texts:
                if (addr and addr in txt) or (namere and namere.search(txt)):
                    n += 1
            return n

        comp_uses = _count(composite_texts)
        study_uses = _count(study_texts)
        p["composite_uses"] = comp_uses
        p["study_uses"] = study_uses
        p["use_count"] = comp_uses + study_uses

    # Per-process cross-study track record: how many studies participate this
    # process (via the composites that contain it) + a pass/inconclusive/fail
    # tally of their outcomes, so the Registry can show studies + percent
    # success like the Composites page. Best-effort; never fails the build.
    try:
        from vivarium_workbench.lib.process_study_stats import process_study_stats
        _pstats = process_study_stats(ws_root, procs)
        for p in procs:
            s = _pstats.get(p.get("address") or "")
            if s:
                total = s.get("total", 0)
                pct = round(100.0 * s.get("pass", 0) / total) if total else None
                p["study_participation"] = {
                    "studies": s.get("studies", 0),
                    # Slugs of the studies participating this process, so the
                    # Registry popup can list + link each (clicking navigates
                    # to that study). Empty when unknown.
                    "study_list": s.get("study_slugs", []),
                    "pass": s.get("pass", 0),
                    "inconclusive": s.get("inconclusive", 0),
                    "fail": s.get("fail", 0),
                    "total": total,
                    "success_pct": pct,
                }
    except Exception:  # noqa: BLE001
        pass


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


def _apply_registry_include_filter(data: dict, ws_data: dict | None, ws_root: Path) -> None:
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

    # Build the re-export map in the env worker (importing the allow-listed
    # packages is workspace Python, kept out of the HTTP process). Guarded so a
    # bad import / unavailable worker never blanks the grid.
    reexports = _reexport_map_via_worker(ws_root, include)

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
        # viva_emitters) outside the include list — so a repo-scoped include like
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

def _annotate_run_commands(data: dict) -> None:
    """Attach a single-line ``run_command`` (``vwb run process <address>``) to
    each runnable registry entry.

    Only ``process``/``step`` kinds get one — emitters, visualizations, types,
    and report cards aren't things you "run" standalone. Canonical builder lives
    in ``lib.run_commands``. Best-effort per entry."""
    from vivarium_workbench.lib.run_commands import process_run_command
    for p in (data.get("processes") or []):
        if not isinstance(p, dict):
            continue
        if (p.get("kind") or "process") not in ("process", "step"):
            continue
        addr = p.get("address") or ""
        if not addr:
            continue
        cmd = process_run_command(addr)
        if cmd:
            p["run_command"] = cmd


def build_registry(ws_root: Path, *, bypass_cache: bool = False) -> dict:
    """Return registry data from build_core() subprocess, with 30s caching.

    Always returns ``{processes: [...], types: [...]}`` plus optional ``error``
    key.  Each process entry includes a ``source`` field:

      - ``"in_workspace"`` — class belongs to the workspace's own package or a
        declared import (workspace.yaml.imports).
      - ``"framework"`` — class is from the process-bigraph framework
        infrastructure (process_bigraph, bigraph_schema, bigraph_viz,
        viva_superpowers, vivarium_workbench).
      - ``"environment_only"`` — discovered via allocate_core() entry-point scan
        but not declared in workspace.yaml. Installed in the Python env but not
        explicitly imported by this workspace.

    Never raises.  Parameterised on ``ws_root`` so the FastAPI route can pass
    the workspace path directly without touching the ``WORKSPACE`` global.

    Two cache layers sit in front of the env-worker call: the 30s in-memory
    ``_REGISTRY_CACHE`` above, and underneath it a persistent on-disk cache
    (``lib.catalog_disk_cache``) keyed by a signature of the installed
    process-lib distributions + workspace package/config — so a cold
    worker/pod with an unchanged venv skips the import walk entirely instead
    of just the 30s in-memory window. See ``warm-catalog`` in ``cli.py`` to
    pre-populate it at image-build time.

    Parameters
    ----------
    ws_root:
        Workspace root directory (must contain ``workspace.yaml``).
    bypass_cache:
        When ``True`` forces a fresh worker call even if the in-memory OR
        disk cache is warm. The fresh result is still written to the disk
        cache (so ``warm-catalog --bypass`` semantics stay useful for
        re-baking after a change the signature happens not to catch).
    """
    now = time.time()
    _cache_key = str(ws_root)
    _slot = _REGISTRY_CACHE.get(_cache_key)
    if not bypass_cache and _slot is not None:
        if now - _slot["ts"] < _registry_ttl():
            return _slot["data"]

    try:
        import yaml

        from vivarium_workbench.lib import catalog_disk_cache

        ws_yaml = ws_root / "workspace.yaml"
        ws_data = yaml.safe_load(ws_yaml.read_text(encoding="utf-8"))

        # Persistent on-disk cache UNDER the 30s in-memory one: the worker call
        # below imports every installed process package (multi-minute on some
        # workspaces), and the in-memory cache only survives within one
        # worker/pod's lifetime. Keyed by a signature of "what could change the
        # catalog" (installed process-lib dists + source, workspace package +
        # workspace.yaml) so a cold worker/pod restart with an UNCHANGED venv
        # serves the disk snapshot instead of re-paying the import walk. See
        # ``lib/catalog_disk_cache.py``. Best-effort throughout: any failure
        # here degrades to the live worker call below, never breaks the panel.
        sig = catalog_disk_cache.catalog_signature(ws_root)
        raw = None if bypass_cache else catalog_disk_cache.load(ws_root, "registry", sig)

        if raw is not None:
            data = raw
        else:
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
            # Cache the RAW worker payload (pre-annotation) so the signature maps
            # to the stable, worker-produced output — annotation (below) always
            # re-runs on load, cached or not.
            catalog_disk_cache.store(ws_root, "registry", sig, data)

        # Annotate emitter entries with is_workspace_default per
        # workspace.yaml::runtime.default_emitter. ws_data was loaded above;
        # treat the emitter-name match permissively (case-insensitive substring
        # against the class name, e.g. 'parquet' → ParquetEmitter).
        _mark_default_emitter(data, ws_data)
        # Per-class use count: how many composite generators / runner scripts in
        # this workspace reference each process/step (so the Registry can sort
        # most-used first). Source-scan heuristic — cheap, no composite builds.
        _annotate_use_counts(data, ws_root)
        # Optional display-only allow-list: workspace.yaml::dashboard.registry.include.
        # When set, the Registry tab shows ONLY classes whose originating package
        # is in the list (discovery is unchanged). No-op when unset → current
        # behavior (show everything).
        _apply_registry_include_filter(data, ws_data, ws_root)
        # Per-entry "how to run this in your terminal" command (vwb run process).
        _annotate_run_commands(data)
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
