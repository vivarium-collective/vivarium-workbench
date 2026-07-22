"""Build the catalog payload (``GET /api/catalog``) for a workspace.

Extracted from ``vivarium_workbench.server._catalog_data`` so the FastAPI
seam (``api/app.py``) can call it without importing the stdlib server module.
The single implementation is shared: ``server.py`` re-imports
``build_catalog`` and keeps its old ``_catalog_data`` name as a thin wrapper.

Helpers moved here from server.py:
  - ``_filter_catalog_modules``
  - ``_build_override_catalog``
  - ``_build_reexport_origin_modules``
  - ``_name_variants``          (was a nested function inside ``_catalog_data``)
  - ``_check_installed_module_sync``  (ws_root-parameterized; server.py keeps
                                       a thin WORKSPACE-forwarding wrapper)
  - ``_CATALOG_VENV_PROBE_SCRIPT``   (module-level constant)
  - ``_detect_workspace_venv_distributions``
  - ``_read_workspace_pyproject_deps``

``_registry_modules_override``, ``_registry_include_pkgs``,
``_build_reexport_map`` are shared with Task 3 — imported from
``lib.registry`` rather than duplicated here.
"""
from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

from vivarium_workbench.lib.registry import (
    _reexport_map_via_worker,
    _registry_include_pkgs,
    _registry_modules_override,
)
from vivarium_workbench.lib.workspace_paths import WorkspacePaths


# ---------------------------------------------------------------------------
# Catalog-module allow-list filter
# ---------------------------------------------------------------------------

def _filter_catalog_modules(modules: list, ws_data: dict | None) -> list:
    """Apply ``dashboard.registry.include`` to the package catalog (/api/catalog).

    Same allow-list, same normalization as the registry filter
    (``_apply_registry_include_filter``): dashes ↔ underscores, top-level
    package segment only. A catalog module's package identity is matched
    against any of its name variants — ``name`` (e.g. ``pbg-bioreactordesign``,
    ``spatio-flux``), ``pypi_name``, and ``package`` (the snake_case import
    name) — so e.g. ``viva-munk`` ↔ ``viva_munk`` and the workspace's own
    first-party module (``kind: "workspace"``, ``name`` = slug = ``v2ecoli``)
    all resolve correctly.

    No-op when no include list is configured (returns ``modules`` unchanged →
    current behavior: show the full catalog).
    """
    if not isinstance(modules, list):
        return modules
    include = _registry_include_pkgs(ws_data)
    if include is None:
        return modules

    def _norm(s) -> str:
        return str(s or "").strip().replace("-", "_").split(".")[0]

    def _allowed(m: dict) -> bool:
        if not isinstance(m, dict):
            return False
        variants = {_norm(m.get("name"))}
        if m.get("pypi_name"):
            variants.add(_norm(m.get("pypi_name")))
        # `package` may be absent; fall back to name→snake_case like elsewhere.
        pkg = m.get("package") or str(m.get("name") or "").replace("-", "_")
        variants.add(_norm(pkg))
        variants.discard("")
        return bool(variants & include)

    # Always keep modules that are actually INSTALLED in this workspace — the
    # catalog's job is to show what's active here, and "modules active in this
    # workspace appear at the top" is its stated contract. The include allow-list
    # only governs which *non-installed* (available-to-install) modules also
    # surface. (Without this, `registry.include: [v2ecoli]` hid the workspace's
    # own installed deps — pbg-emitters, viva-munk, … — leaving only v2ecoli.)
    return [m for m in modules if _allowed(m) or m.get("installed")]


# ---------------------------------------------------------------------------
# Override-catalog builder
# ---------------------------------------------------------------------------

def _build_override_catalog(override: list, default_modules: list) -> list:
    """Build a catalog from ``dashboard.registry.modules`` (the override).

    The override REPLACES pbg's default catalog. ``default_modules`` is pbg's
    default catalog (``load_registry`` + workspace overlay) used only to resolve
    bare-string entries by inheriting their full metadata.

    Resolution per entry:

      - **string** → look the name up in ``default_modules``; if found, deep-copy
        its full metadata dict; if NOT found, emit a minimal stub
        (``name`` + a short ``description`` note) so the row still renders.
      - **dict** → a custom module pbg doesn't ship; used verbatim with missing
        display fields filled with sensible defaults so the row renders and the
        Install/Uninstall button works (needs at least ``name``; ``package``
        defaults to the snake_case name; ``source``/``description``/``tags`` get
        placeholder fallbacks).

    Install-state is NOT set here — the caller's existing install-detection loop
    (imports / pyproject / venv probe) annotates each entry, so a custom entry
    whose ``package`` is importable in the venv (e.g. ``viva_munk``) is marked
    installed exactly like a default-catalog entry.

    Order is preserved from the override list. Unrecognized entry types are
    skipped.
    """
    by_name = {
        str(m.get("name")): m
        for m in (default_modules or [])
        if isinstance(m, dict) and m.get("name")
    }
    out: list[dict] = []
    seen: set[str] = set()

    for entry in override:
        if isinstance(entry, str):
            name = entry.strip()
            if not name or name in seen:
                continue
            seen.add(name)
            found = by_name.get(name)
            if found is not None:
                out.append(copy.deepcopy(found))
            else:
                # pbg doesn't ship this name — minimal stub so it still renders.
                out.append({
                    "name": name,
                    "package": name.replace("-", "_"),
                    "description": (
                        f"{name} — declared in this workspace's "
                        "dashboard.registry.modules but not found in the default "
                        "pbg catalog (no inherited metadata)."
                    ),
                    "tags": [],
                    "override_stub": True,
                })
        elif isinstance(entry, dict):
            name = str(entry.get("name") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            m = copy.deepcopy(entry)
            m.setdefault("package", name.replace("-", "_"))
            m.setdefault(
                "description",
                f"{name} — custom workspace catalog entry.",
            )
            m.setdefault("source", "")
            m.setdefault("tags", [])
            # Surface a single-tag category as a tag too (display convenience).
            cat = m.get("category")
            if cat and isinstance(m.get("tags"), list) and cat not in m["tags"]:
                m["tags"] = list(m["tags"]) + [cat]
            m["override_custom"] = True
            out.append(m)
        # else: unknown entry type → skip silently.

    return out


# ---------------------------------------------------------------------------
# Re-export origin modules builder
# ---------------------------------------------------------------------------

def _build_reexport_origin_modules(
    ws_root: Path, ws_data: dict | None, existing_modules: list
) -> list[dict]:
    """Synthesize catalog entries for re-export-ORIGIN packages.

    A re-export origin is a package that is (a) NOT in the registry allow-list
    itself, but (b) has ≥1 class re-exported by an allow-listed package (per
    ``_build_reexport_map``). The canonical example: ``spatio_flux`` is not
    allow-listed, but ``viva_munk`` re-exports 7 of its classes into its own
    top-level namespace — so spatio-flux is a genuine dependency of an
    allow-listed package and should be SHOWN in the catalog (tagged
    ``📦 via viva-munk``) rather than fully hidden.

    For each such origin package we emit one catalog entry stamped with
    ``install_source: "venv"`` + ``installed_via: [<allow-listed re-exporters>]``
    so the install-source badge renders ``📦 via <parents>`` and the UI shows
    "(remove parent to uninstall)" instead of an Install button. This
    install_source attribution is DELIBERATELY forced to the re-exporter(s)
    even when the package is also a direct pyproject dependency of the
    workspace (e.g. v2ecoli pins spatio-flux): the meaningful reason it appears
    in this filtered catalog is the re-export, per v2ecoli's own pyproject
    comment.

    Guarded: returns ``[]`` unless a registry allow-list is configured AND the
    re-export map yields at least one origin package. Origin packages already
    present in ``existing_modules`` (by name/package variant) are skipped so we
    never duplicate or shadow a primary catalog entry.
    """
    include = _registry_include_pkgs(ws_data)
    if include is None:
        return []
    try:
        # Via the env worker (imports the allow-listed packages there, not in the
        # HTTP process). Soft-degrades to {} on an unavailable worker.
        reexports = _reexport_map_via_worker(ws_root, include)
    except Exception:
        return []
    if not reexports:
        return []

    def _norm(s) -> str:
        return str(s or "").strip().replace("-", "_").split(".")[0]

    # Collect, per origin package, the set of allow-listed re-exporters.
    # Map keys are either ``def_module.qualname`` (full address) or
    # ``"<def_top_pkg>::<name>"``; the value is the re-exporting package. Only
    # the ``::`` keys cleanly expose the origin top-level package, so derive
    # origins from those.
    # Stdlib / builtin module names are NOT installable workspace packages —
    # an allow-listed package re-importing e.g. ``typing.TypeVar`` or
    # ``dataclasses.dataclass`` into its namespace must not surface a bogus
    # "typing" catalog row. Mirror the framework-pkg guard in _build_reexport_map
    # for the standard library.
    _stdlib = set(getattr(sys, "stdlib_module_names", ()))
    _builtins = set(sys.builtin_module_names)

    origins: dict[str, set[str]] = {}
    for key, reexporter in reexports.items():
        if "::" not in key:
            continue
        def_top = _norm(key.split("::", 1)[0])
        if not def_top or def_top in include:
            continue
        if def_top in _stdlib or def_top in _builtins:
            continue
        origins.setdefault(def_top, set()).add(reexporter)
    if not origins:
        return []

    # Don't duplicate a package that the override catalog already lists.
    existing: set[str] = set()
    for m in existing_modules or []:
        if not isinstance(m, dict):
            continue
        existing.add(_norm(m.get("name")))
        if m.get("pypi_name"):
            existing.add(_norm(m.get("pypi_name")))
        existing.add(_norm(m.get("package") or m.get("name")))
    existing.discard("")

    out: list[dict] = []
    for origin_pkg in sorted(origins):
        if origin_pkg in existing:
            continue
        # Re-exporters as their catalog display names (dash form for the badge).
        parents = sorted(p.replace("_", "-") for p in origins[origin_pkg])
        display_name = origin_pkg.replace("_", "-")
        out.append({
            "name": display_name,
            "package": origin_pkg,
            "description": (
                f"Re-exported by {', '.join(parents)} "
                "(particles + visualizations)."
            ),
            "tags": ["dependency", "re-export"],
            "category": "dependency",
            "installed": True,
            # Force the venv/via-parent attribution even if this package is also
            # a direct pyproject dep — the reason it's surfaced here is the
            # re-export, not the direct pin.
            "install_source": "venv",
            "installed_via": parents,
            "reexport_origin": True,
        })
    return out


# ---------------------------------------------------------------------------
# Name-variant helper (was nested inside _catalog_data)
# ---------------------------------------------------------------------------

def _name_variants(m: dict) -> list:
    """Return a list of lowercased name variants for a catalog module dict.

    Variants: the module's ``name``, its ``pypi_name`` (if any), and the
    ``package`` / snake_case fallback.  Used for matching against
    ``workspace.yaml.imports`` keys and pyproject / venv distribution names.
    """
    out: list = [m["name"].lower()]
    pn = m.get("pypi_name")
    if pn:
        out.append(pn.lower())
    pkg = m.get("package") or m["name"].replace("-", "_")
    out.append(pkg.lower())
    return out


# ---------------------------------------------------------------------------
# Module-sync check (ws_root-parameterized)
# ---------------------------------------------------------------------------

def _check_installed_module_sync(
    ws_root: Path, pkg_name: str, install_path: str | None
) -> str | None:
    """Return None if the module is consistently installed; else a one-line reason.

    Best-effort, fast: verifies the Python package is importable from the
    workspace venv. Surfaces drift between workspace.yaml.imports and the
    actual venv state (e.g., user pip-uninstalled a package without touching
    workspace.yaml).

    Swallows all errors (returns None on any unexpected failure) so callers
    never 500.
    """
    venv_py = ws_root / ".venv" / "bin" / "python3"
    if not venv_py.is_file():
        return None  # no venv to introspect; treat as consistent
    # The import name is derived from the display/dist name (hyphen→underscore),
    # which preserves case — but Python top-level packages are conventionally
    # lowercase (e.g. dist "Viva-munk" installs the package ``viva_munk``). Probe
    # the given name AND its lowercased form so a mixed-case dist name doesn't
    # read as a false "out of sync". Any candidate importing cleanly = in sync.
    candidates = []
    for c in (pkg_name, pkg_name.lower()):
        if c and c not in candidates:
            candidates.append(c)
    probe = "import importlib,sys\n" + "".join(
        f"try:\n importlib.import_module({c!r}); sys.exit(0)\nexcept Exception: pass\n"
        for c in candidates
    ) + "sys.exit(1)\n"
    try:
        result = subprocess.run(
            [str(venv_py), "-c", probe],
            cwd=ws_root, capture_output=True, text=True, timeout=3,
        )
        if result.returncode != 0:
            return f"Python import of '{pkg_name}' failed (was the venv updated?)"
    except subprocess.TimeoutExpired:
        return f"Python import of '{pkg_name}' timed out"
    except Exception as e:
        return f"Python import check errored: {e}"
    return None


# ---------------------------------------------------------------------------
# Venv distribution probe
# ---------------------------------------------------------------------------

_CATALOG_VENV_PROBE_SCRIPT = r'''
import importlib.metadata as md, json, re, sys
out = {}
for d in md.distributions():
    name = (d.metadata.get("Name") or "").lower()
    if not name:
        continue
    requires_raw = list(d.requires or [])
    requires_names = []
    for r in requires_raw:
        # Bare-name extract: strip version markers, extras, environment markers.
        bare = re.split(r"[\s;<>=!~\[]", r, 1)[0].strip().lower()
        if bare:
            requires_names.append(bare)
    out[name] = {"version": d.version, "requires": requires_names}
json.dump(out, sys.stdout)
'''


def _detect_workspace_venv_distributions(ws_root: Path) -> dict[str, dict]:
    """Single bulk venv probe — returns {package_name_lower: {version, requires, requires_by}}.

    Used by ``/api/catalog`` to detect packages that are installed in the
    workspace venv but NOT declared in workspace.yaml.imports — the
    transitive-dependency case (e.g., v2ecoli depends on viva-munk via
    pyproject.toml, viva-munk shows up in the venv but workspace.yaml has
    no entry for it).

    ``requires_by`` is the reverse index: for each package, which OTHER
    installed packages declared it as a dependency. Lets the UI show
    "transitive: brought in by X, Y" for venv-only-installed catalog
    entries.

    Returns {} if the venv is missing, probe times out, or JSON parse
    fails — caller should degrade gracefully (no transitive detection).
    """
    venv_py = ws_root / ".venv" / "bin" / "python3"
    if not venv_py.is_file():
        return {}
    try:
        result = subprocess.run(
            [str(venv_py), "-c", _CATALOG_VENV_PROBE_SCRIPT],
            cwd=ws_root, capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return {}
        data = json.loads(result.stdout)
    except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError):
        return {}
    # Build reverse index: requires_by[child] = [parent_pkgs]
    rev: dict[str, list[str]] = {}
    for name, info in data.items():
        for req in info.get("requires", []):
            rev.setdefault(req, []).append(name)
    for name, info in data.items():
        info["requires_by"] = sorted(rev.get(name, []))
    return data


# ---------------------------------------------------------------------------
# Pyproject dependency reader
# ---------------------------------------------------------------------------

def _read_workspace_pyproject_deps(ws_root: Path) -> set[str]:
    """Return the set of declared dependencies (bare package names, lowercased)
    from the workspace's pyproject.toml ``[project.dependencies]``.

    Used by ``/api/catalog`` to mark a catalog module as installed when the
    workspace's pyproject.toml declares it directly — even if
    workspace.yaml.imports has no entry. This is the SECOND of three
    install-source layers the dashboard now checks (after
    workspace.yaml.imports, before raw venv presence).

    Returns empty set on parse failure or missing file — degrades gracefully.
    """
    pyp = ws_root / "pyproject.toml"
    if not pyp.is_file():
        return set()
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib   # type: ignore
        except ImportError:
            return set()
    try:
        data = tomllib.loads(pyp.read_text(encoding="utf-8"))
    except Exception:
        return set()
    deps = ((data.get("project") or {}).get("dependencies") or [])
    out: set[str] = set()
    for d in deps:
        if not isinstance(d, str):
            continue
        # Strip version markers / extras / env markers — same regex as the
        # venv probe so the two sources can be compared directly.
        bare = re.split(r"[\s;<>=!~\[]", d, 1)[0].strip().lower()
        if bare:
            out.add(bare)
    return out


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_catalog(ws_root: Path) -> dict:
    """Pure data builder for ``GET /api/catalog`` — returns ``{"modules": [...]}`` dict.

    Called by the FastAPI seam (``api/app.py``) and, via the thin
    ``_catalog_data`` wrapper, by the stdlib handler and ``publish.build_bundle``.
    Takes ``ws_root`` explicitly — does NOT read the ``WORKSPACE`` global.

    Best-effort: helpers that inspect the venv / pyproject swallow errors and
    return empty results, so this function never raises / 500s.
    """
    ws_root = Path(ws_root)

    try:
        ws_data = yaml.safe_load(
            (ws_root / "workspace.yaml").read_text(encoding="utf-8")
        )
    except Exception as e:
        return {"modules": [], "error": f"workspace.yaml: {e}"}

    # Module registry (canonical pbg-superpowers list + per-workspace overlay)
    try:
        from pbg_superpowers.catalog import load_registry as _lr
        default_modules: list = _lr(ws_root)
    except Exception:
        _wp = WorkspacePaths.load(ws_root)
        legacy = _wp.scripts / "_catalog" / "modules.json"
        if legacy.is_file():
            try:
                default_modules = json.loads(legacy.read_text(encoding="utf-8"))
            except Exception:
                default_modules = []
        else:
            default_modules = []

    override = _registry_modules_override(ws_data)
    if override is not None:
        modules = _build_override_catalog(override, default_modules)
    else:
        modules = default_modules

    imports = (ws_data or {}).get("imports", {}) or {}
    pyproject_deps = _read_workspace_pyproject_deps(ws_root)
    venv_dists = _detect_workspace_venv_distributions(ws_root)

    # Normalized import lookup: key each declared import by its lowercased
    # dash- AND underscore-forms so a curated module named "pbg-oxidizeme"
    # matches an import declared as "pbg_oxidizeme" (the dash/underscore
    # mismatch otherwise leaves reference-mode imports marked not-installed
    # and hidden in the read-only Modules grid).
    _imports_norm: dict = {}
    if isinstance(imports, dict):
        for _k, _v in imports.items():
            kl = str(_k).lower()
            _imports_norm[kl] = _v
            _imports_norm[kl.replace("-", "_")] = _v
            _imports_norm[kl.replace("_", "-")] = _v

    for m in modules:
        variants = _name_variants(m)
        declared_imp = next(
            (_imports_norm[v] for v in variants if v in _imports_norm), None
        )
        declared = declared_imp is not None
        in_pyproject = any(v in pyproject_deps for v in variants)
        in_venv = any(v in venv_dists for v in variants)
        if declared:
            m["installed"] = True
            m["install_source"] = "imports"
            imp = declared_imp or {}
            for k in ("source", "ref", "path", "install_path", "package", "mode"):
                v = imp.get(k)
                if v is not None:
                    m[k] = v
        elif in_pyproject:
            m["installed"] = True
            m["install_source"] = "pyproject"
        elif in_venv:
            m["installed"] = True
            m["install_source"] = "venv"
            parents: list = []
            for v in variants:
                info = venv_dists.get(v)
                if info:
                    parents.extend(info.get("requires_by") or [])
                    break
            m["installed_via"] = sorted(set(parents))
        else:
            m["installed"] = False
        if m["installed"]:
            # `mode: reference` modules are declared for browsing only and are
            # not expected to be importable in the venv — never flag them.
            if (
                m.get("install_source") in ("imports", "pyproject")
                and str(m.get("mode") or "").lower() != "reference"
            ):
                pkg_name = m.get("package") or m["name"].replace("-", "_")
                sync_reason = _check_installed_module_sync(
                    ws_root, pkg_name, m.get("install_path")
                )
                if sync_reason:
                    m["out_of_sync"] = True
                    m["out_of_sync_reason"] = sync_reason

    # Surface workspace.yaml `imports` that aren't in the curated catalog
    # (e.g. pbg-ketchup / pbg-parsimony / pbg-torch / pbg-oxidizeme) so EVERY
    # declared import shows in the Modules grid as an installed module, not just
    # the curated ones. Without this, an imported pbg-* repo that pbg_superpowers'
    # catalog doesn't know about silently never appears here.
    if isinstance(imports, dict):
        _known_variants: set = set()
        for m in modules:
            for v in _name_variants(m):
                _known_variants.add(v)
        for imp_name, imp in imports.items():
            imp = imp or {}
            pkg = (imp.get("package") or imp_name).replace("-", "_")
            variants = {str(imp_name).lower(), pkg.lower()}
            if variants & _known_variants:
                continue  # already represented by a curated entry
            desc = (imp.get("description") or "").strip().split("\n")[0]
            # `mode: reference` imports are declared for BROWSING only (e.g. an
            # engine whose real solver can't be installed here) — they are not
            # expected to be importable in the venv, so they must not be sync-
            # checked or flagged "out of sync".
            mode = str(imp.get("mode") or "").lower()
            is_reference = mode == "reference"
            mod = {
                "name": imp_name,
                "package": pkg,
                "description": desc or f"Imported package {imp_name}.",
                "installed": True,
                "install_source": "imports",
                "mode": mode or None,
            }
            for k in ("source", "ref", "path", "install_path"):
                if imp.get(k) is not None:
                    mod[k] = imp[k]
            # Mirror the out-of-sync check curated installed modules get, so an
            # imported-but-unimportable package is flagged here too — but skip
            # reference-mode modules, which are browse-only by design.
            if not is_reference:
                sync_reason = _check_installed_module_sync(
                    ws_root, pkg, mod.get("install_path")
                )
                if sync_reason:
                    mod["out_of_sync"] = True
                    mod["out_of_sync_reason"] = sync_reason
            modules.append(mod)
            _known_variants |= variants

    reexport_origins = _build_reexport_origin_modules(ws_root, ws_data, modules)
    if reexport_origins:
        modules = modules + reexport_origins

    # Workspace self-module (mirrors Handler._workspace_self_module)
    slug = (ws_data or {}).get("name", "") or ""
    ws_pkg = (ws_data or {}).get("package_path")
    if not ws_pkg:
        ws_pkg = "pbg_" + slug.replace("-", "_") if slug else None
    if ws_pkg:
        pkg_dir = ws_root / ws_pkg
        if pkg_dir.is_dir():
            sync_reason = _check_installed_module_sync(ws_root, ws_pkg, ws_pkg)
            try:
                result = subprocess.run(
                    ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                    cwd=ws_root, capture_output=True, text=True, timeout=2,
                )
                ref = result.stdout.strip() or "—"
            except (subprocess.TimeoutExpired, OSError):
                ref = "—"
            ws_self: dict = {
                "kind":         "workspace",
                "name":         slug or ws_pkg,
                "package":      ws_pkg,
                "install_path": ws_pkg,
                "description":  "Workspace's own first-party package — provides the "
                                "Processes, Steps, Composites, and Types that "
                                "build_core() registers for this workspace.",
                "source":       "workspace",
                "ref":          ref,
                "tags":         ["workspace"],
                "installed":    True,
            }
            if sync_reason:
                ws_self["out_of_sync"] = True
                ws_self["out_of_sync_reason"] = sync_reason
            modules = [ws_self] + modules

    if override is None:
        kept_origins = [
            m for m in modules if isinstance(m, dict) and m.get("reexport_origin")
        ]
        modules = _filter_catalog_modules(modules, ws_data) + kept_origins

    return {"modules": modules}
