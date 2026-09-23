"""Persistent on-disk cache for the Registry + Composites catalog builds.

Building the Registry (``lib.registry.build_registry``) and Composites
(``lib.composites_query.composites_via_subprocess``) panels means importing
every installed process package in the workspace's env worker — multiple
minutes on some workspaces. Both already carry a 30s in-memory TTL cache
(``_REGISTRY_CACHE`` / ``_COMPOSITES_CACHE``), but that only helps *within*
one worker/pod's lifetime: every restart (worker recycle, pod bounce,
autoscale) pays the full import walk again.

This module is the DISK-backed layer underneath those in-memory caches,
keyed by a signature of "what could change the catalog": the installed
process-library distributions + their source — reusing the same cache-key
machinery ``bigraph_schema.package.discover`` uses for its own lazy-discovery
index (``_installed_signature`` / ``_package_source_signature``), so this
cache invalidates on exactly the same signals the framework's own index cache
does — plus the workspace's own package path, ``workspace.yaml`` mtime, and
that package's source signature (belt-and-braces: the workspace package is
usually ALSO covered by ``_installed_signature`` as a process-library dist,
but this catches the case where it isn't, e.g. not pip-installed, or where
``workspace.yaml`` itself changed in a way that doesn't touch any dist).

Every function here is BEST-EFFORT: a cache failure (unwritable disk,
corrupt file, a framework whose signature helpers moved/renamed) must fall
back to the live build, never break the panel, and never serve a stale/wrong
catalog. When the signature cannot be computed at all, caching is disabled
outright (:data:`DISABLED`) rather than risk keying on a wrong/incomplete
signature.

Pre-baking: ``vivarium-workbench warm-catalog --workspace <ws>`` (see
``cli.py``) populates this cache at image-build time so a cold pod never pays
the import walk on its first request.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

# The framework's own discovery-index cache-key helpers. Reused so this cache
# invalidates on exactly the same signals bigraph-schema's own lazy-discovery
# index does — see bigraph_schema/package/discover.py::_load_or_build_index.
# Optional: an incompatible/renamed framework must degrade gracefully (see
# ``_fallback_installed_signature``), never crash the panel.
try:
    from bigraph_schema.package.discover import (
        _installed_signature as _bgs_installed_signature,
    )
    from bigraph_schema.package.discover import (
        _package_source_signature as _bgs_package_source_signature,
    )
except Exception:  # pragma: no cover - framework absent/incompatible
    _bgs_installed_signature = None
    _bgs_package_source_signature = None


#: Returned by :func:`catalog_signature` when the signature cannot be
#: trusted. ``load``/``store`` treat it as "caching disabled" for this call —
#: rebuilding live beats risking a stale/wrong catalog keyed on a bad sig.
DISABLED = "__disabled__"

_ENV_CACHE_DIR = "VIVARIUM_WORKBENCH_CATALOG_CACHE_DIR"

_KINDS = ("registry", "composites")


# ---------------------------------------------------------------------------
# Signature
# ---------------------------------------------------------------------------

def _lightweight_core() -> SimpleNamespace:
    """Cheap duck-typed stand-in for ``bigraph_schema.core.Core`` exposing
    just ``distributions_packages`` (dist name -> list of its import
    packages) — the only attribute ``_installed_signature`` /
    ``_process_lib_packages`` read. Mirrors ``Core.__init__``'s own
    construction of that mapping via
    ``importlib.metadata.packages_distributions()`` (a metadata scan, no
    package imports) WITHOUT paying for a full ``Core(BASE_TYPES)`` build.
    """
    packages_distributions = {
        key: list(set(values))
        for key, values in importlib.metadata.packages_distributions().items()
    }
    distributions_packages: dict[str, list[str]] = {}
    for package, dists in packages_distributions.items():
        for dist in dists:
            distributions_packages.setdefault(dist, []).append(package)
    return SimpleNamespace(distributions_packages=distributions_packages)


def _fallback_installed_signature() -> str:
    """Best-effort stand-in for ``_installed_signature`` when the framework
    helper isn't importable (renamed/moved/absent): sorted ``name==version``
    for every installed distribution that requires (or is) ``bigraph-schema``.
    No per-package source-mtime component (that needs
    ``_package_source_signature``'s ``find_spec`` walk, not reimplemented
    here) — still enough to invalidate on install/uninstall/upgrade.
    """
    tokens: list[str] = []
    for dist in importlib.metadata.distributions():
        try:
            name = dist.metadata["Name"]
        except Exception:
            continue
        if not name:
            continue
        is_bgs = name == "bigraph-schema"
        try:
            reqs = dist.requires or []
        except Exception:
            reqs = []
        if is_bgs or any("bigraph-schema" in r for r in reqs):
            try:
                version = dist.version
            except Exception:
                version = "?"
            tokens.append(f"{name}=={version}")
    return "|".join(sorted(tokens))


def _workspace_package_import_name(ws_root: Path) -> str | None:
    """The workspace's top-level import package name, read straight from
    ``workspace.yaml`` (``package_path``, falling back to the ``viva_<slug>``
    convention) via a raw YAML parse — no layout-map machinery, so this
    module stays independent of the rest of ``vivarium_workbench.lib``."""
    try:
        import yaml
    except Exception:
        return None
    ws_yaml = ws_root / "workspace.yaml"
    try:
        data = yaml.safe_load(ws_yaml.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    pkg = data.get("package_path")
    if isinstance(pkg, str) and pkg.strip():
        return pkg.strip()
    name = data.get("name")
    if isinstance(name, str) and name.strip():
        slug = name.strip().replace("-", "_")
        return f"viva_{slug}"
    return None


def _stable_content_signature(pairs: "list[tuple[str, Path]]") -> str:
    """Copy-stable content hash of ``(relpath, file)`` pairs — hashes each file's
    BYTES, never its mtime, so a file copied to a new path/time (e.g. an image
    seed copied into a user workspace) yields the SAME signature. A missing or
    unreadable file contributes a sentinel rather than raising."""
    h = hashlib.sha1()
    for rel, p in sorted(pairs, key=lambda t: t[0]):
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        try:
            h.update(hashlib.sha1(p.read_bytes()).digest())
        except OSError:
            h.update(b"<unreadable>")
        h.update(b"\n")
    return h.hexdigest()


def _workspace_source_signature(ws_root: Path, pkg_name: "str | None") -> str:
    """Copy-stable content signature of the workspace's OWN package source under
    ``ws_root`` — the part a per-user seed copy duplicates. Uses the symlink-safe
    walk so it never descends into ``.venv``. When the package is NOT under
    ``ws_root`` (e.g. installed into the image venv) it is already covered by the
    installed-distribution signature, so this returns a stable marker instead."""
    if not pkg_name:
        return "ws_src=none"
    pkg_dir = ws_root / pkg_name
    if not pkg_dir.is_dir():
        return "ws_src=not-in-ws"
    try:
        from vivarium_workbench.lib.workspace_walk import iter_workspace_files
        pairs = [
            (f.relative_to(pkg_dir).as_posix(), f)
            for f in iter_workspace_files(pkg_dir, suffixes=(".py",))
        ]
    except Exception:  # noqa: BLE001
        return "ws_src=error"
    return "ws_src=" + _stable_content_signature(pairs)


def catalog_signature(ws_root: Path | str) -> str:
    """A signature string identifying "what could change the catalog" for
    ``ws_root``: the installed process-library distributions + their source
    (reusing bigraph-schema's own discovery-index cache-key helpers) plus the
    workspace's own package path and the CONTENT hashes of its ``workspace.yaml``
    and package source. Content (not mtime) so a cache baked at image-build time
    still matches after the workspace is seed-copied into a user's pod.

    Best-effort: ANY failure returns :data:`DISABLED`, which callers treat as
    "never read or write the disk cache" — rebuilding live beats risking a
    stale/wrong catalog keyed on an incomplete/wrong signature.
    """
    try:
        ws_root = Path(ws_root)
        tokens: list[str] = [f"py={sys.version_info.major}.{sys.version_info.minor}"]

        if _bgs_installed_signature is not None:
            tokens.append(_bgs_installed_signature(_lightweight_core()))
        else:
            tokens.append(_fallback_installed_signature())

        # CONTENT hashes (not mtimes) for the per-user-copied workspace files, so
        # a cache baked at image-build time still HITS after the workspace is
        # seed-copied into a user's pod: a copy changes mtimes but not content,
        # and an mtime-keyed signature would silently miss (defeating the whole
        # point of the pre-baked cache).
        ws_yaml = ws_root / "workspace.yaml"
        try:
            ws_yaml_hash = hashlib.sha1(ws_yaml.read_bytes()).hexdigest()
        except OSError:
            ws_yaml_hash = "none"
        tokens.append(f"ws_yaml={ws_yaml_hash}")

        pkg_name = _workspace_package_import_name(ws_root)
        tokens.append(f"package_path={pkg_name}")
        tokens.append(_workspace_source_signature(ws_root, pkg_name))

        return hashlib.sha1("\n".join(tokens).encode("utf-8")).hexdigest()
    except Exception:
        return DISABLED


# ---------------------------------------------------------------------------
# Cache directory + file I/O
# ---------------------------------------------------------------------------

def cache_dir(ws_root: Path | str) -> Path:
    """``<ws>/.pbg/registry-catalog/`` (the workbench's writable home on the
    pod), overridable by ``VIVARIUM_WORKBENCH_CATALOG_CACHE_DIR`` (e.g. to
    point at a baked-image location distinct from the live workspace mount).
    Created best-effort; callers must tolerate a directory that still doesn't
    exist afterwards (read-only image, unwritable mount)."""
    override = os.environ.get(_ENV_CACHE_DIR)
    if override:
        d = Path(override)
    else:
        try:
            from vivarium_workbench.lib.workspace_paths import WorkspacePaths
            d = WorkspacePaths.load(Path(ws_root)).pbg / "registry-catalog"
        except Exception:
            d = Path(ws_root) / ".pbg" / "registry-catalog"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d


def _cache_path(ws_root: Path | str, kind: str, sig: str) -> Path:
    return cache_dir(ws_root) / f"{kind}-{sig}.json"


def load(ws_root: Path | str, kind: str, sig: str) -> dict[str, Any] | None:
    """Return the cached ``kind`` catalog dict for signature ``sig``, or
    ``None`` on any miss/failure (missing file, corrupt JSON, unwritable/
    unreadable dir, or a disabled signature). Never raises."""
    if not sig or sig == DISABLED or kind not in _KINDS:
        return None
    try:
        path = _cache_path(ws_root, kind, sig)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def store(ws_root: Path | str, kind: str, sig: str, data: dict[str, Any]) -> None:
    """Write ``data`` to the disk cache for ``kind``/``sig``. Atomic
    (``mkstemp`` + ``os.replace``, mirroring bigraph-schema's own
    ``_write_cache``) so a concurrent reader never sees a half-written file.
    Never raises — a read-only or unwritable cache dir must never break the
    live build."""
    if not sig or sig == DISABLED or kind not in _KINDS or not isinstance(data, dict):
        return
    try:
        d = cache_dir(ws_root)
        path = d / f"{kind}-{sig}.json"
        d.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(d), prefix=f"{kind}-", suffix=".json.tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
    except OSError:
        pass
    except Exception:
        pass


def clear(ws_root: Path | str) -> None:
    """Delete every cached catalog file (every kind, every signature) for
    this workspace. Never raises."""
    try:
        d = cache_dir(ws_root)
        for kind in _KINDS:
            for f in d.glob(f"{kind}-*.json"):
                try:
                    f.unlink()
                except OSError:
                    pass
    except Exception:
        pass
