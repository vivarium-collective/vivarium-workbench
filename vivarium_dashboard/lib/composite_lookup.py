"""Workspace-local + installed-package composite discovery.

Mirrors pbg_superpowers.composite_spec + composite_discovery for the dashboard's
use. Self-contained: no dependency on pbg-superpowers (which is a Claude Code
plugin, not always pip-installable in workspace venvs).

Discovery sources:
  1. The workspace's own pbg_<slug>/composites/ directory.
  2. Every installed distribution whose dist-name starts with `pbg-`, scanned
     for a top-level `composites/` package alongside its other modules.

The latter is what makes `pbg-caspule`, `pbg-tellurium`, etc. surface their
demo composites in any workspace that has them installed.
"""
from __future__ import annotations
import importlib.metadata as metadata
import importlib.util
import json
import re
from pathlib import Path
from typing import Any

import yaml


_FULL_PLACEHOLDER = re.compile(r"^\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}$")
_INLINE_PLACEHOLDER = re.compile(r"\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def load_spec(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(text)
    return yaml.safe_load(text)


def _spec_record(spec: dict, package: str, stem: str, path: Path,
                 ws_root: Path | None) -> dict | None:
    """Validate + shape one discovered spec into the dict the API returns."""
    if not isinstance(spec, dict) or "state" not in spec or "name" not in spec:
        return None
    try:
        rel = str(path.relative_to(ws_root)) if ws_root else str(path)
    except ValueError:
        rel = str(path)
    return {
        "id": f"{package}.composites.{stem}",
        "name": spec.get("name"),
        "description": spec.get("description", ""),
        "tags": spec.get("tags") or [],
        "parameters": spec.get("parameters") or {},
        "requires": spec.get("requires") or {},
        "source": rel,
        "_state": spec.get("state"),
        "_path": str(path),
    }


def _stem(path: Path) -> str:
    name = path.name
    for suffix in (".composite.yaml", ".composite.yml", ".composite.json"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _scan_composites_dir(composites_dir: Path, package: str,
                         ws_root: Path | None) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not composites_dir.is_dir():
        return out
    for pattern in ("*.composite.yaml", "*.composite.yml", "*.composite.json"):
        for path in composites_dir.glob(pattern):
            stem = _stem(path)
            try:
                rec = _spec_record(load_spec(path), package, stem, path, ws_root)
            except Exception:
                continue
            if rec is not None:
                out[rec["id"]] = rec
    return out


def discover_workspace_composites(ws_root: Path, package_path: str) -> dict[str, dict]:
    """Scan the workspace's own pbg_<slug>/composites/; return {id: spec}."""
    return _scan_composites_dir(ws_root / package_path / "composites",
                                package_path, ws_root)


def discover_installed_pbg_composites() -> dict[str, dict]:
    """Scan every installed pbg-* distribution's <package>/composites/ directory.

    Strategy: enumerate installed distributions whose Name starts with `pbg-`,
    derive the canonical Python package name (`pbg-foo` → `pbg_foo`), then
    `importlib.util.find_spec` to resolve the on-disk package directory. The
    `dist.files` shape varies between regular and editable installs, so name
    derivation is more robust.
    """
    out: dict[str, dict] = {}
    seen_pkgs: set[str] = set()
    for dist in metadata.distributions():
        name = (dist.metadata.get("Name") or "").strip()
        if not name.startswith("pbg-"):
            continue
        pkg_name = name.replace("-", "_")
        if pkg_name in seen_pkgs:
            continue  # Same package may appear twice (regular + editable shim)
        seen_pkgs.add(pkg_name)
        try:
            spec = importlib.util.find_spec(pkg_name)
        except (ImportError, ValueError):
            continue
        if not spec or not spec.submodule_search_locations:
            continue
        for loc in spec.submodule_search_locations:
            out.update(_scan_composites_dir(Path(loc) / "composites", pkg_name, None))
    return out


def _derive_module_from_spec_id(spec_id: str) -> str:
    """Best-effort friendly module name from a spec id.

    `pkg.composites.foo` -> `pkg.composites`, otherwise the bit before the
    last dot (or the whole id if no dot).
    """
    if ".composites." in spec_id:
        return spec_id.split(".composites.", 1)[0] + ".composites"
    if "." in spec_id:
        return spec_id.rsplit(".", 1)[0]
    return spec_id


def discover_all_composites(
    ws_root: Path,
    package_path: str,
    *,
    bypass_cache: bool = False,
) -> dict[str, dict]:
    """Discover composites from the workspace + every installed pbg-* package.

    File-spec composites (``.composite.{json,yaml}``) are discovered in
    the dashboard's own process via ``discover_workspace_composites``.
    ``@composite_generator``-decorated builders, however, require
    importing the workspace's package — which usually depends on
    scientific stacks (wholecell, viva_munk, …) that the dashboard's
    venv lacks.  For those we delegate to
    :func:`discover_via_workspace_subprocess`, which runs inside the
    workspace's venv.

    Results are cached per workspace root with a 60s TTL and an mtime
    check on ``<ws>/pyproject.toml`` (so editing deps invalidates).
    ``bypass_cache=True`` forces a refresh — wire this to a
    ``?refresh=1`` query param on the HTTP endpoint.
    """
    cached = _composites_cache_get(ws_root, package_path)
    if cached is not None and not bypass_cache:
        return cached

    out: dict[str, dict] = {}
    # File-spec composites: dashboard-process scan (no workspace deps needed).
    out.update(discover_workspace_composites(ws_root, package_path))
    for spec_id, rec in discover_installed_pbg_composites().items():
        if spec_id not in out:
            out[spec_id] = rec

    # Tag every spec entry with kind + derived module (idempotent).
    for spec_id, rec in out.items():
        rec.setdefault("kind", "spec")
        if not rec.get("module"):
            rec["module"] = _derive_module_from_spec_id(spec_id)

    # @composite_generator-decorated builders: subprocess-isolated against
    # the workspace's venv.  Producer/consumer split — the dashboard never
    # imports workspace-specific code in-process.
    try:
        generators = discover_via_workspace_subprocess(
            ws_root, package_path, bypass_cache=bypass_cache,
        )
    except CompositeDiscoveryError as e:
        import warnings
        warnings.warn(
            f"composite_lookup: workspace discovery failed ({e}); "
            f"@composite_generator-decorated builders will not appear "
            f"in the catalog until the cause is resolved.",
            stacklevel=2,
        )
        generators = []

    for entry in generators:
        gid = entry.get("id")
        if not gid:
            continue
        if gid in out and entry.get("kind") != "generator":
            # Spec from in-process scan wins for non-generator ids; only
            # generator entries supplement (file-spec composites already
            # surface their parameters via the spec).
            continue
        if gid in out and out[gid].get("kind") == "generator":
            # Already added (shouldn't happen normally; defensive).
            continue
        # Trust the runner's payload shape — it already populated every
        # field the catalog UI expects.
        out[gid] = entry

    _composites_cache_put(ws_root, package_path, out)
    return out


# ────────────────────────────────────────────────────────────────────────
# Composite-discovery subprocess + cache
# ────────────────────────────────────────────────────────────────────────


class CompositeDiscoveryError(RuntimeError):
    """Subprocess discovery failed in a way the caller should surface."""


# Cache keyed by absolute workspace path string.  Each entry:
#   {
#       "data":           dict[str, dict],   # the discovered composites
#       "ts":             float,             # time.monotonic() at fill
#       "pyproject_mtime": float | None,     # invalidate when this advances
#       "package_path":   str,               # in case the workspace's
#                                             # package_path changes
#   }
_COMPOSITES_CACHE: dict[str, dict] = {}
_COMPOSITES_CACHE_TTL_SEC = 60.0

# Per-workspace locks so concurrent /api/composites calls during a cold
# cache share a single subprocess invocation.  Without this, N parallel
# HTTP requests would spawn N runner subprocesses and serialize at the
# uv-venv-resolution layer.
_COMPOSITES_LOCKS: dict[str, "_Lock"] = {}

# Imported lazily to keep module-load time small (threading is stdlib,
# but the type ref lives below the lock-cache for clarity).
def _Lock():
    import threading
    return threading.Lock()


def _composites_cache_get(ws_root: Path, package_path: str):
    """Return cached composites for ``ws_root`` if fresh, else ``None``."""
    import time

    key = str(ws_root.resolve())
    entry = _COMPOSITES_CACHE.get(key)
    if entry is None:
        return None
    if entry.get("package_path") != package_path:
        return None
    # TTL gate
    if (time.monotonic() - entry["ts"]) >= _COMPOSITES_CACHE_TTL_SEC:
        return None
    # mtime gate on the workspace's pyproject.toml — captures the most
    # common "deps changed; re-scan" case without filesystem walks.
    pj_mtime = entry.get("pyproject_mtime")
    actual_mtime = _pyproject_mtime(ws_root)
    if pj_mtime is not None and actual_mtime is not None and actual_mtime > pj_mtime:
        return None
    return entry["data"]


def _composites_cache_put(
    ws_root: Path, package_path: str, data: dict[str, dict],
) -> None:
    import time

    key = str(ws_root.resolve())
    _COMPOSITES_CACHE[key] = {
        "data": data,
        "ts": time.monotonic(),
        "pyproject_mtime": _pyproject_mtime(ws_root),
        "package_path": package_path,
    }


def _pyproject_mtime(ws_root: Path) -> float | None:
    try:
        return (ws_root / "pyproject.toml").stat().st_mtime
    except (OSError, AttributeError):
        return None


def discover_via_workspace_subprocess(
    ws_root: Path,
    package_path: str,
    *,
    extra_packages: list[str] | None = None,
    timeout_s: int = 60,
    bypass_cache: bool = False,
) -> list[dict]:
    """Run :mod:`discover_composites_runner` inside the workspace's venv.

    The dashboard's venv lacks workspace-specific deps (wholecell, etc.),
    so the runner is invoked via ``uv run --directory <ws>`` (or the
    workspace's ``.venv/bin/python`` as fallback) where those deps are
    importable.  PYTHONPATH is injected with the running dashboard's
    source root so the subprocess imports the *current* dashboard code
    regardless of what's installed in the workspace's venv.

    Returns the list of composite entries.  Raises
    :class:`CompositeDiscoveryError` on a failure that the caller should
    surface (vs. an empty list when the runner ran but found nothing).
    """
    import json
    import os
    import subprocess
    import uuid

    # Per-workspace lock so concurrent calls share one subprocess.
    key = str(ws_root.resolve())
    lock = _COMPOSITES_LOCKS.setdefault(key, _Lock())
    with lock:
        # Second check inside the lock — when bypass_cache is False, a
        # concurrent waiter may have populated the cache while we were
        # blocked, and we should return its result rather than running
        # the subprocess again.  When bypass_cache is True, the caller
        # explicitly asked for a fresh read; honour that.
        if not bypass_cache:
            cached = _composites_cache_get(ws_root, package_path)
            if cached is not None:
                return [
                    rec for rec in cached.values()
                    if rec.get("kind") == "generator"
                ]

        request_id = uuid.uuid4().hex
        scratch = ws_root / ".pbg" / "discover-composites"
        scratch.mkdir(parents=True, exist_ok=True)
        resp_path = scratch / f"{request_id}.resp.json"

        # PYTHONPATH injection — same pattern as run_render_viz.
        import vivarium_dashboard as _vd
        dashboard_src_root = str(Path(_vd.__file__).resolve().parent.parent)
        env = os.environ.copy()
        existing_pp = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            dashboard_src_root
            + (os.pathsep + existing_pp if existing_pp else "")
        )

        extra_str = ",".join(extra_packages) if extra_packages else ""
        runner_args = [
            "python", "-m", "vivarium_dashboard.lib.discover_composites_runner",
            "--workspace", str(ws_root),
            "--pkg", package_path,
            "--response", str(resp_path),
        ]
        if extra_str:
            runner_args += ["--extra-packages", extra_str]
        cmd_via_uv = ["uv", "run", "--directory", str(ws_root), *runner_args]
        ws_venv_python = ws_root / ".venv" / "bin" / "python"
        cmd_via_venv = [
            str(ws_venv_python), "-m",
            "vivarium_dashboard.lib.discover_composites_runner",
            "--workspace", str(ws_root),
            "--pkg", package_path,
            "--response", str(resp_path),
        ]
        if extra_str:
            cmd_via_venv += ["--extra-packages", extra_str]

        try:
            try:
                proc = subprocess.run(
                    cmd_via_uv, capture_output=True, text=True,
                    timeout=timeout_s, cwd=str(ws_root), env=env,
                )
            except FileNotFoundError:
                if not ws_venv_python.is_file():
                    raise CompositeDiscoveryError(
                        "no workspace runner available: neither `uv` "
                        "nor a workspace .venv/bin/python was found"
                    )
                proc = subprocess.run(
                    cmd_via_venv, capture_output=True, text=True,
                    timeout=timeout_s, cwd=str(ws_root), env=env,
                )
        except subprocess.TimeoutExpired:
            raise CompositeDiscoveryError(
                f"composite-discovery subprocess timed out after {timeout_s}s"
            )
        except FileNotFoundError as e:
            raise CompositeDiscoveryError(
                f"failed to launch discover subprocess: {e}"
            )

        if not resp_path.is_file():
            tail = (proc.stderr or proc.stdout or "(no output)").strip()[-800:]
            raise CompositeDiscoveryError(
                f"discover subprocess produced no response file "
                f"(exit={proc.returncode}). stderr tail: {tail}"
            )

        try:
            response = json.loads(resp_path.read_text())
        except (OSError, json.JSONDecodeError) as e:
            raise CompositeDiscoveryError(
                f"discover response file unreadable: {e}"
            )

        # Clean up on success (response file is workspace-internal
        # scratch).  Retain on failure for debug.
        if response.get("ok"):
            try:
                resp_path.unlink()
            except OSError:
                pass
            entries = response.get("composites") or []
            # Filter generators only — the caller's spec scan already
            # has the file-spec composites.
            return [e for e in entries if e.get("kind") == "generator"]

        # Controlled failure — surface verbatim, retain artefacts for
        # post-mortem.
        raise CompositeDiscoveryError(
            f"{response.get('error') or 'discover reported failure'} "
            f"(response retained at {resp_path})"
        )


def find_composite_path(ws_root: Path, package_path: str, spec_id: str) -> Path | None:
    """Resolve a composite spec id back to its on-disk path.

    Looks first in the workspace, then in installed pbg-* packages.
    """
    parts = spec_id.split(".composites.")
    if len(parts) != 2:
        return None
    pkg, stem = parts
    # Workspace package first
    for suffix in (".composite.yaml", ".composite.yml", ".composite.json"):
        candidate = ws_root / pkg / "composites" / f"{stem}{suffix}"
        if candidate.is_file():
            return candidate
    # Installed packages
    specs = discover_installed_pbg_composites()
    rec = specs.get(spec_id)
    if rec and rec.get("_path"):
        p = Path(rec["_path"])
        if p.is_file():
            return p
    return None


def _cast(value: Any, declared_type: str | None) -> Any:
    if declared_type == "float":
        return float(value)
    if declared_type == "int":
        return int(value)
    if declared_type in ("string", "str"):
        return str(value)
    if declared_type == "bool":
        if isinstance(value, str):
            return value.strip().lower() in ("true", "1", "yes")
        return bool(value)
    return value


def substitute_parameters(state: Any, params: dict, overrides: dict | None = None) -> Any:
    overrides = overrides or {}
    if isinstance(state, dict):
        return {k: substitute_parameters(v, params, overrides) for k, v in state.items()}
    if isinstance(state, list):
        return [substitute_parameters(v, params, overrides) for v in state]
    if isinstance(state, str):
        m = _FULL_PLACEHOLDER.match(state)
        if m:
            pname = m.group(1)
            pdef = params.get(pname, {})
            raw = overrides.get(pname, pdef.get("default"))
            return _cast(raw, pdef.get("type"))
        if _INLINE_PLACEHOLDER.search(state):
            return _INLINE_PLACEHOLDER.sub(
                lambda mm: str(overrides.get(mm.group(1), params.get(mm.group(1), {}).get("default", ""))),
                state,
            )
    return state
