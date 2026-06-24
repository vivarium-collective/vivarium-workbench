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


def discover_all_composites(ws_root: Path, package_path: str) -> dict[str, dict]:
    """Discover composites from the workspace + every installed pbg-* package.

    If the workspace's package is also pip-installed (e.g., `pip install -e .`),
    the installed scan would re-find the same specs; the workspace scan runs
    first so workspace-relative `source` paths win.

    Also merges in `@composite_generator`-decorated functions from installed
    bigraph-schema-dependent packages via
    :func:`pbg_superpowers.composite_discovery.discover_all`. Generator entries
    carry ``kind: "generator"`` and a ``module`` field; spec entries are tagged
    ``kind: "spec"`` and gain a derived ``module``. If pbg-superpowers is not
    importable the function falls back to spec-only behavior.
    """
    out: dict[str, dict] = {}
    out.update(discover_workspace_composites(ws_root, package_path))
    for spec_id, rec in discover_installed_pbg_composites().items():
        if spec_id not in out:
            out[spec_id] = rec

    # Tag every spec entry with kind + derived module (idempotent).
    for spec_id, rec in out.items():
        rec.setdefault("kind", "spec")
        if not rec.get("module"):
            rec["module"] = _derive_module_from_spec_id(spec_id)

    # Merge generator entries from pbg-superpowers, if available.
    try:
        from pbg_superpowers.composite_discovery import discover_all as _ps_discover_all
    except ImportError as e:
        import warnings
        warnings.warn(
            f"composite_lookup: pbg-superpowers not importable, "
            f"generator discovery disabled ({e})",
            stacklevel=2,
        )
        return out

    try:
        merged = _ps_discover_all()
    except Exception as e:  # noqa: BLE001 — be defensive; never break catalog
        import warnings
        warnings.warn(
            f"composite_lookup: discover_all raised {type(e).__name__}: {e}",
            stacklevel=2,
        )
        return out

    for gid, entry in merged.items():
        if entry.get("kind") != "generator":
            continue
        if gid in out:
            continue
        rec: dict = {
            "id": gid,
            "kind": "generator",
            "name": entry.get("name") or gid.rsplit(".", 1)[-1],
            "description": entry.get("description", ""),
            "tags": [],
            "parameters": entry.get("parameters") or {},
            "requires": {},
            "module": entry.get("module") or _derive_module_from_spec_id(gid),
        }
        # Generator entries always carry default_n_steps (int | None); emit it
        # unconditionally so callers can rely on the key being present.
        rec["default_n_steps"] = entry.get("default_n_steps")
        # Canonical visualizations declared on @composite_generator. Always a
        # list (empty when the generator omits the field). The dashboard's
        # study-run handlers merge these defaults into the Study's viz list
        # so callers inherit the composite's simulation-report panels.
        rec["visualizations"] = list(entry.get("visualizations") or [])
        out[gid] = rec
    return out


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


def known_composite_ids(ws_root: Path, package_path: str | None = None) -> set[str]:
    """All composite spec ids resolvable in this workspace.

    Unions the workspace's own ``.composite.yaml`` specs, installed ``pbg-*``
    package specs, AND the live ``@composite_generator`` registry. This is the
    "known set" the composite-resolution lint checks a study's declared refs
    against. Tolerant: returns whatever it can discover; never raises.
    """
    ws_root = Path(ws_root)
    if package_path is None:
        try:
            ws_data = yaml.safe_load((ws_root / "workspace.yaml").read_text(encoding="utf-8")) or {}
            package_path = ws_data.get("package_path") or (
                "pbg_" + str(ws_data.get("name", "")).replace("-", "_"))
        except Exception:  # noqa: BLE001
            package_path = ""
    ids: set[str] = set()
    try:
        ids.update(discover_all_composites(ws_root, package_path or "").keys())
    except Exception:  # noqa: BLE001
        pass
    # Generator registry (also merged by discover_all_composites, but prime it
    # directly in case discovery short-circuited before generators were loaded).
    try:
        from pbg_superpowers.composite_generator import _REGISTRY, discover_generators
        if not _REGISTRY:
            discover_generators()
        ids.update(_REGISTRY.keys())
    except Exception:  # noqa: BLE001
        pass
    return ids


def _study_composite_refs(spec: dict) -> list[str]:
    """Collect the composite refs a study DECLARES: ``baseline[].composite``,
    ``conditions.baseline.composite``, ``conditions.variants[].composite`` and
    ``simulation_set[].composite``. (Run records use short aliases and are NOT
    treated as canonical declarations.) Order-preserving, de-duplicated."""
    refs: list[str] = []

    def _add(r):
        if isinstance(r, str) and r.strip() and r not in refs:
            refs.append(r.strip())

    for b in (spec.get("baseline") or []):
        if isinstance(b, dict):
            _add(b.get("composite"))
    conds = spec.get("conditions")
    if isinstance(conds, dict):
        bl = conds.get("baseline")
        if isinstance(bl, dict):
            _add(bl.get("composite"))
        for v in (conds.get("variants") or []):
            if isinstance(v, dict):
                _add(v.get("composite"))
    for s in (spec.get("simulation_set") or []):
        if isinstance(s, dict):
            _add(s.get("composite"))
    return refs


def _ref_resolves(ref: str, known_ids: set[str]) -> bool:
    """A declared ref resolves if it's a known spec id, OR shares the trailing
    ``.composites.<slug>`` segment with one (so a short ``slug`` alias matches
    a dotted ``pkg.composites.slug`` id)."""
    if ref in known_ids:
        return True
    tail = ref.rsplit(".composites.", 1)[-1]
    for kid in known_ids:
        if kid == ref or kid.rsplit(".composites.", 1)[-1] == tail:
            return True
    return False


def unresolved_study_composite_refs(spec: dict, known_ids: set[str]) -> list[str]:
    """Return the study's declared composite refs that DON'T resolve to any
    registered composite id.

    Prefers ``pbg_superpowers.report_linter.unresolved_composite_refs`` (the
    canonical, spec-only contract) when available; falls back to the local
    extraction + last-segment match. Defensive: never raises.
    """
    try:
        from pbg_superpowers.report_linter import unresolved_composite_refs as _ps
        return list(_ps(spec, set(known_ids)))
    except Exception:  # noqa: BLE001 — older/absent pbg_superpowers → local fallback
        pass
    known = set(known_ids)
    return [r for r in _study_composite_refs(spec) if not _ref_resolves(r, known)]


def _dedupe_alias_composites(records: list) -> list:
    """Collapse a composite that's registered under more than one id.

    A ``@composite_generator(name="baseline")`` in a same-named module registers
    under the DOUBLED id ``v2ecoli.composites.baseline.baseline``; a workspace may
    add a clean-id alias ``v2ecoli.composites.baseline`` so short study refs
    resolve. Both then surface in discovery, listing the SAME composite twice.
    Collapse generator records that share (name, module), keeping the canonical
    id (the one equal to its module, else the shortest) so each composite appears
    once and the kept id is the resolvable/explorable one. Records without a
    module, or with a unique (name, module), pass through unchanged.

    Moved from ``vivarium_dashboard.server`` (Task 6) so it can be shared by
    ``server._composites_data`` (imported back) and ``lib.catalog`` without
    duplication.
    """
    def _rank(rec, mod):
        rid = rec.get("id") or ""
        return (0 if rid == mod else 1, len(rid))

    kept: dict = {}
    order: list = []
    out: list = []
    for rec in records:
        mod = rec.get("module") or ""
        if not mod or rec.get("kind") != "generator":
            out.append(rec)
            continue
        key = (rec.get("name"), mod)
        prev = kept.get(key)
        if prev is None:
            kept[key] = rec
            order.append(key)
            out.append(rec)
        elif _rank(rec, mod) < _rank(prev, mod):
            out[out.index(prev)] = rec
            kept[key] = rec
        # else: drop the non-canonical duplicate
    return out


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
