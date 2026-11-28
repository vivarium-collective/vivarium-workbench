"""Post-run analysis providers: the seam by which a workspace's simulator ships
its own post-run analysis pass (scale-resolution, execution, the output contract)
instead of the core hardcoding one engine's shapes.

The core serves ANY process-bigraph workspace, so it must not bake in one
simulator's analysis registry or output layout. A provider supplies them; a
workspace with none (the default) falls back to the generic behaviour the core
already has. Providers are discovered through the ``vivarium_workbench.analysis``
entry-point group, in the process that needs them -- the env worker runs in the
workspace's own venv, where the simulator (and so its provider) is installed.

This module is the discovery machinery + the :class:`AnalysisProvider` Protocol.
It is a deliberately-narrow sibling of ``lib/backends/provider.py`` (the backend
provider registry) and copies its absence/version/conflict semantics so an absent
provider (the package isn't installed) is a clean no-op while a *broken* one
raises. Today only :func:`output_layout` is consumed; the remaining methods are
the contract the coordinated follow-up (moving the v2ecoli analysis pass out of
core) implements.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

#: Set to ``none`` to turn every analysis provider off (mirrors the backend
#: registry's switch; used by the core-only test matrix).
PROVIDERS_ENV = "VIVARIUM_WORKBENCH_ANALYSIS_PROVIDERS"
#: The entry-point group a provider registers under
#: (``[project.entry-points."vivarium_workbench.analysis"]``).
ENTRY_POINT_GROUP = "vivarium_workbench.analysis"
#: The provider interface's major version. A provider declares the version it
#: targets as a literal ``api_version`` and is refused at load when the two differ.
ANALYSIS_PROVIDER_API_VERSION = 1

@dataclass(frozen=True)
class OutputLayout:
    """The post-run analysis output contract the landing helpers
    (``remote_run_landing.fold_analyses`` / ``land_ptools_tsvs``) assume. The
    defaults are what the core has always used (the v2ecoli-analyze shapes); a
    provider overrides any field via :meth:`AnalysisProvider.output_layout`, and a
    workspace with no provider lands exactly as before."""

    #: Glob (relative to a landed/extracted tree) that finds each analysis' result
    #: json -- v2ecoli-analyze writes ``analyses/<name>/analysis.json``.
    analysis_json_glob: str = "**/analyses/*/analysis.json"
    #: Subdirectories beside an ``analysis.json`` that hold its output files.
    fold_subdirs: "tuple[str, ...]" = ("viz", "ptools")
    #: The ``<name><sep><group>`` filename convention those files use.
    filename_sep: str = "__"
    #: Glob that finds PTools TSV exports to copy into the study's ptools dir.
    ptools_glob: str = "**/ptools/*.tsv"


#: The default contract (today's behaviour), applied when no provider overrides it.
DEFAULT_OUTPUT_LAYOUT = OutputLayout()


class ProviderVersionError(ImportError):
    """A provider targets another analysis-provider interface version than this
    workbench offers. An ``ImportError`` so the existing plugin-load reporters
    print it and exit."""


class AnalysisProvider(Protocol):
    """What a workspace's simulator registers to supply the post-run analysis pass.

    Only :meth:`output_layout` is consumed today (the landing contract); the rest
    is the contract the core will call once the v2ecoli analysis pass is moved out
    of core behind this seam. A provider declares ``name`` (== its entry-point
    name) and ``api_version`` (a literal == :data:`ANALYSIS_PROVIDER_API_VERSION`).
    """

    name: str
    api_version: int

    def output_layout(self) -> "dict[str, object]":
        """This provider's post-run output contract, as a (partial) override of
        :data:`DEFAULT_OUTPUT_LAYOUT`. Keys left out keep their default."""
        ...

    # -- the coordinated follow-up implements these (see module docstring) --
    def analysis_options(self, entries: "list[dict]", ws_root: Path) -> "tuple[dict, list[dict]]":
        """Translate a study's declared ``analyses`` entries into the engine's
        analysis options (``{scale: {name: params}}``) + per-entry errors."""
        ...

    def run_analyses(self, entries: "list[dict]", *, sweep_dir: str,
                     sim_data_path: "str | None", t_start: float) -> "tuple[list[str], list[dict]]":
        """Run the provider's own analyses for one completed run; return
        ``(written_files, errors)``."""
        ...

    def registry_classes(self) -> "list[dict]":
        """The analysis/report-card classes to surface in the Registry tab."""
        ...


_providers: "Optional[tuple[AnalysisProvider, ...]]" = None
_lock = threading.Lock()


def providers() -> "tuple[AnalysisProvider, ...]":
    """The active analysis providers (cached). Empty when turned off, or when no
    provider package is installed. An installed-but-broken provider raises."""
    global _providers
    with _lock:
        if _providers is None:
            _providers = _load()
        return _providers


def output_layout() -> OutputLayout:
    """The effective post-run output contract: the active provider's override
    applied to :data:`DEFAULT_OUTPUT_LAYOUT`, or the defaults when no provider is
    present. (The first provider that offers an ``output_layout`` wins; the common
    case is zero providers -> the defaults, i.e. today's behaviour exactly.)"""
    for provider in providers():
        try:
            override = provider.output_layout()
        except Exception:  # noqa: BLE001 — a provider must never break landing
            continue
        if isinstance(override, dict) and override:
            return _apply_override(override)
    return DEFAULT_OUTPUT_LAYOUT


def _apply_override(override: dict) -> OutputLayout:
    """Build an :class:`OutputLayout` from a provider's (partial) override dict,
    keeping each default for a key the provider omits (or gives a wrong-typed value
    for -- a provider must never break landing)."""
    d = DEFAULT_OUTPUT_LAYOUT
    sub = override.get("fold_subdirs")
    return OutputLayout(
        analysis_json_glob=_as_str(override.get("analysis_json_glob"), d.analysis_json_glob),
        fold_subdirs=(tuple(str(s) for s in sub) if isinstance(sub, (list, tuple)) else d.fold_subdirs),
        filename_sep=_as_str(override.get("filename_sep"), d.filename_sep),
        ptools_glob=_as_str(override.get("ptools_glob"), d.ptools_glob),
    )


def _as_str(value: object, default: str) -> str:
    return value if isinstance(value, str) and value else default


def _entry_points() -> "list[importlib.metadata.EntryPoint]":
    """The group's entry points, sorted by name (a seam for tests)."""
    return sorted(importlib.metadata.entry_points(group=ENTRY_POINT_GROUP),
                  key=lambda ep: (ep.name, ep.value))


def _real_package(name: str) -> bool:
    """Whether ``name`` imports as a real package (not a namespace left by a stray
    ``__pycache__``, not hidden by a finder)."""
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, ValueError):
        return False
    return (spec is not None and spec.origin not in (None, "namespace")
            and spec.submodule_search_locations is not None)


def _absent(e: ModuleNotFoundError, module: str) -> bool:
    """``e`` says the entry point's distribution is not installed: its TOP-LEVEL
    package cannot be found. A missing submodule of a present package is a broken
    provider, not an absent one."""
    if not e.name:
        return False
    top = module.split(".", 1)[0]
    if e.name == top:
        return True
    within = module == e.name or module.startswith(e.name + ".")
    return within and e.name.startswith(top + ".") and not _real_package(top)


def _load() -> "tuple[AnalysisProvider, ...]":
    if (os.environ.get(PROVIDERS_ENV) or "").strip().lower() == "none":
        return ()
    eps = _entry_points()
    values: "dict[str, set[str]]" = {}
    for ep in eps:
        values.setdefault(ep.name, set()).add(ep.value)
    clashing = sorted(name for name, vals in values.items() if len(vals) > 1)
    if clashing:
        detail = "; ".join(f"{n}: {sorted(values[n])}" for n in clashing)
        raise ImportError(f"more than one {ENTRY_POINT_GROUP} entry point has the same name ({detail})")
    found: "list[AnalysisProvider]" = []
    seen: "set[tuple[str, str]]" = set()
    for ep in eps:
        if (ep.name, ep.value) in seen:
            continue
        seen.add((ep.name, ep.value))
        try:
            obj = ep.load()
        except ModuleNotFoundError as e:
            if _absent(e, ep.module):
                continue
            raise
        except AttributeError as e:
            raise ImportError(f"{ENTRY_POINT_GROUP} entry point {ep.name!r} ({ep.value}): {e}") from e
        version = getattr(obj, "api_version", None)
        if version != ANALYSIS_PROVIDER_API_VERSION:
            raise ProviderVersionError(
                f"the analysis provider {ep.name!r} targets provider interface version {version!r}, "
                f"but this workbench offers version {ANALYSIS_PROVIDER_API_VERSION}; install matching versions")
        if getattr(obj, "name", None) != ep.name:
            raise ImportError(
                f"{ENTRY_POINT_GROUP} entry point {ep.name!r} ({ep.value}) loads a provider named "
                f"{getattr(obj, 'name', None)!r}")
        if any(p is obj for p in found):
            continue
        found.append(obj)
    return tuple(found)


def reset() -> None:
    """Forget the providers (tests; a changed ``PROVIDERS_ENV``)."""
    global _providers
    with _lock:
        _providers = None
