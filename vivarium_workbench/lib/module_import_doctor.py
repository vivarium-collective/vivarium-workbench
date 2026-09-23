"""Workspace-module import doctor.

`dep_doctor` probes the fixed *framework* dependencies. This module probes the
*workspace's own catalog modules* — the packages a workspace declares in
``workspace.yaml``'s ``imports:`` (plus its own ``package_path``) that are
expected to register processes / composites / studies into the ``Core``.

It exists to surface a decoupling that costs real debugging time in deployed
workbenches: a catalog module can be reported "installed" from metadata
(``importlib.metadata`` presence, an ``imports:`` entry, a pyproject dep) while
it **fails to import at runtime** — most often a ``--no-deps`` install whose
transitive dependency is missing. When that happens, the bigraph-schema /
process-bigraph discovery layers silently swallow the ``ImportError`` and simply
skip the module, so its composites never register and a later run fails with the
opaque *"not a registered composite"*. This doctor makes that failure explicit:
it lists exactly which declared modules do not import, and why.

Each module is imported in an **isolated subprocess** (the same reason the
composite discovery runs out-of-process): a broken workspace module must not
partially import into — or crash — the HTTP server process. Pure + best-effort:
every probe is wrapped, so running the doctor never raises.
"""
from __future__ import annotations

import concurrent.futures
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import yaml

_PROBE_SRC = "import importlib, sys; importlib.import_module(sys.argv[1])"

# Cache the findings keyed on (workspace, workspace.yaml mtime). The declared set
# changes only when workspace.yaml does (an install adds an ``imports:`` entry,
# bumping mtime), so this is the right invalidation for the common case; a modest
# TTL bounds staleness on the rarer "a dependency got installed without editing
# workspace.yaml" axis. Without this, every /api/module-import-diagnostics hit
# re-spawns one subprocess PER declared module — minutes on a slow/large env,
# past a 180s ingress (504).
_DIAG_CACHE: dict = {}
_DIAG_TTL = 300.0  # seconds


def _module_name(dist_or_module: str, entry: Any) -> str:
    """The importable module name for an ``imports:`` entry.

    Prefer an explicit ``package:`` on the entry; otherwise normalize the
    distribution name to its conventional import form (``spatio-flux`` ->
    ``spatio_flux``).
    """
    if isinstance(entry, dict) and entry.get("package"):
        return str(entry["package"])
    return str(dist_or_module).replace("-", "_")


def _declared_modules(ws_root: Path) -> list[dict[str, str]]:
    """``[{name, module, source}]`` — the workspace-declared modules to probe.

    Sources: the workspace's own ``package_path`` and every ``imports:`` entry
    that is not ``mode: reference`` (reference modules are declared for browsing
    only and are not expected to import — mirrors ``catalog.build_catalog``).
    De-duplicated by import target.
    """
    try:
        data = yaml.safe_load((ws_root / "workspace.yaml").read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return []

    out: list[dict[str, str]] = []
    seen: set[str] = set()

    def _add(name: str, module: str, source: str) -> None:
        if not module or module in seen:
            return
        seen.add(module)
        out.append({"name": name, "module": module, "source": source})

    pkg = data.get("package_path")
    if pkg:
        _add(str(pkg), str(pkg), "package_path")

    imports = data.get("imports") or {}
    if isinstance(imports, dict):
        for k, v in imports.items():
            if isinstance(v, dict) and str(v.get("mode") or "").lower() == "reference":
                continue
            _add(str(k), _module_name(str(k), v), "imports")
    return out


def _probe(module: str, ws_root: Path, timeout: float) -> tuple[bool, str]:
    """Import ``module`` in an isolated subprocess. Returns ``(ok, detail)``.

    ``ws_root`` is prepended to ``PYTHONPATH`` so the workspace's own package is
    importable (mirrors the serve-time sys.path insert), and is the cwd.
    """
    existing = os.environ.get("PYTHONPATH")
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(ws_root), existing]) if existing else str(ws_root),
    }
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _PROBE_SRC, module],
            cwd=str(ws_root), env=env, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"import timed out after {timeout:.0f}s"
    except Exception as e:  # noqa: BLE001
        return False, f"probe failed to launch: {type(e).__name__}: {e}"
    if proc.returncode == 0:
        return True, "importable"
    err = (proc.stderr or "").strip().splitlines()
    # The last non-empty stderr line is the exception (e.g.
    # "ModuleNotFoundError: No module named 'polars'").
    detail = err[-1] if err else f"exited {proc.returncode}"
    return False, detail


def clear_diagnostics_cache() -> None:
    """Invalidate the module-import-diagnostics cache (workspace switch/tests)."""
    _DIAG_CACHE.clear()


def diagnose_module_imports(ws_root: Path, *, timeout: float = 30.0,
                            max_workers: int | None = None,
                            total_budget: float = 150.0,
                            use_cache: bool = True) -> list[dict[str, Any]]:
    """Probe every workspace-declared module for a runtime ImportError.

    Returns one finding per module::

        {ok: bool, name: str, module: str, source: str, detail: str}

    ``name`` is the ``imports:`` / distribution name; ``module`` is what was
    imported; ``source`` is ``package_path`` or ``imports``. Never raises.

    Hardened for large/slow deployments behind a request timeout:
    - probes run **concurrently** (each is an isolated subprocess, so a bounded
      thread pool runs them in parallel) instead of strictly serially;
    - a **total wall-clock budget** caps the whole call — a module that has not
      finished by then is reported as skipped rather than letting one hung import
      (``timeout`` each) sum to minutes and 504 the endpoint;
    - results are **cached** per (workspace, workspace.yaml mtime) for ``_DIAG_TTL``.
    """
    ws_root = Path(ws_root)
    modules = _declared_modules(ws_root)
    if not modules:
        return []

    try:
        mtime = (ws_root / "workspace.yaml").stat().st_mtime
    except OSError:
        mtime = None
    cache_key = (str(ws_root), mtime)
    now = time.monotonic()
    if use_cache and mtime is not None:
        slot = _DIAG_CACHE.get(cache_key)
        if slot is not None and now - slot["ts"] < _DIAG_TTL:
            return slot["data"]

    if max_workers is None:
        max_workers = min(len(modules),
                          int(os.environ.get("VIVARIUM_WORKBENCH_DIAG_WORKERS", "4") or 4))
    max_workers = max(1, max_workers)

    findings_by_module: dict[str, dict[str, Any]] = {}
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
    try:
        futs = {ex.submit(_probe, m["module"], ws_root, timeout): m for m in modules}
        try:
            for fut in concurrent.futures.as_completed(futs, timeout=total_budget):
                m = futs[fut]
                try:
                    ok, detail = fut.result()
                except Exception as e:  # noqa: BLE001 — a probe never should, but stay total
                    ok, detail = False, f"probe error: {type(e).__name__}: {e}"
                findings_by_module[m["module"]] = {
                    "ok": ok, "name": m["name"], "module": m["module"],
                    "source": m["source"], "detail": detail}
        except concurrent.futures.TimeoutError:
            pass  # budget elapsed; unfinished modules reported as skipped below
    finally:
        # Don't block the request on stragglers: cancel what hasn't started, and
        # let any still-running probe (bounded by its own ``timeout``) finish and
        # be GC'd — its result is simply ignored.
        ex.shutdown(wait=False, cancel_futures=True)

    # Preserve declared order; a module missing from the results (budget exceeded
    # or cancelled) is surfaced explicitly, never silently dropped.
    out: list[dict[str, Any]] = []
    for m in modules:
        f = findings_by_module.get(m["module"])
        if f is None:
            f = {"ok": False, "name": m["name"], "module": m["module"],
                 "source": m["source"],
                 "detail": f"probe skipped (diagnostic budget {total_budget:.0f}s exceeded)"}
        out.append(f)

    if use_cache and mtime is not None:
        _DIAG_CACHE[cache_key] = {"data": out, "ts": now}
    return out


def module_import_problems(
    findings: "list[dict[str, Any]] | None" = None, *, ws_root: "Path | None" = None,
) -> list[dict[str, Any]]:
    """The non-ok findings (empty = every declared module imports)."""
    if findings is None:
        if ws_root is None:
            raise ValueError("pass either findings or ws_root")
        findings = diagnose_module_imports(ws_root)
    return [f for f in findings if not f.get("ok")]


def format_report(
    findings: "list[dict[str, Any]] | None" = None, *, ws_root: "Path | None" = None,
) -> str:
    """Human-readable multi-line report (for the CLI / logs)."""
    if findings is None:
        if ws_root is None:
            raise ValueError("pass either findings or ws_root")
        findings = diagnose_module_imports(ws_root)
    lines = ["Workspace module import doctor:"]
    if not findings:
        lines.append("  (no catalog modules declared in workspace.yaml)")
        return "\n".join(lines)
    for f in findings:
        mark = "✓" if f.get("ok") else "✗"
        lines.append(f"  {mark} {f['module']} — {f['detail']}")
    probs = [f for f in findings if not f.get("ok")]
    if probs:
        lines.append(
            f"{len(probs)} declared module(s) fail to import — their processes/"
            "composites will NOT register (installed ≠ importable). Reinstall "
            "WITH dependencies (a --no-deps install leaves transitive deps missing)."
        )
    else:
        lines.append("All declared workspace modules import. ✓")
    return "\n".join(lines)
