"""Workspace-local + installed-package composite discovery.

Mirrors viva_superpowers.composite_spec + composite_discovery for the dashboard's
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
import difflib
import importlib.metadata as metadata
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable, Optional

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


def _discover_installed_composites(
    name_predicate: Callable[[str], bool] | None = None,
) -> dict[str, dict]:
    """Scan installed distributions' <package>/composites/ directory.

    Strategy: enumerate installed distributions (optionally filtered by
    `name_predicate` on the dist's raw `Name`), derive the canonical Python
    package name (`pbg-foo` → `pbg_foo`), then `importlib.util.find_spec` to
    resolve the on-disk package directory. The `dist.files` shape varies
    between regular and editable installs, so name derivation is more robust.

    Best-effort: any single distribution that errors during resolution/scan
    is skipped, never raising.
    """
    out: dict[str, dict] = {}
    seen_pkgs: set[str] = set()
    for dist in metadata.distributions():
        try:
            name = (dist.metadata.get("Name") or "").strip()
            if not name:
                continue
            if name_predicate is not None and not name_predicate(name):
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
        except Exception:
            continue
    return out


def discover_installed_pbg_composites() -> dict[str, dict]:
    """Scan every installed pbg-* distribution's <package>/composites/ directory.

    Public, pbg-only behavior preserved for existing callers (the Composites
    tab / composite resolution). See :func:`discover_installed_composites_all`
    for the generalized (all-distributions) variant used by module content
    stats, which need to count packaged composites for ANY wheel-installed
    module, not just the `pbg-` naming convention.
    """
    return _discover_installed_composites(lambda n: n.startswith("pbg-"))


def discover_installed_composites_all() -> dict[str, dict]:
    """Scan EVERY installed distribution's <package>/composites/ directory.

    Generalization of :func:`discover_installed_pbg_composites` with no name
    filter -- so wheel-installed modules that don't follow the `pbg-` naming
    convention (or the post-rebrand `viva-` one) still surface their packaged
    composites. Best-effort throughout (skips a distribution on any error;
    never raises) since this runs over the whole installed environment.
    """
    return _discover_installed_composites(None)


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


def _discover_generators_via_worker(ws_root: Path) -> dict:
    """``{gid: entry}`` generator composites from the workspace's env worker
    (so the HTTP process never imports ``@composite_generator`` modules). Soft-
    degrades to ``{}`` when the worker is unavailable — the same spec-only fallback
    as when pbg-superpowers isn't importable."""
    from vivarium_workbench.lib.env_worker_client import EnvWorkerUnavailable
    from vivarium_workbench.lib.env_worker_pool import get_pool
    try:
        r = get_pool().call(ws_root, "discover_composites")
        return r.get("generators", {}) if isinstance(r, dict) else {}
    except EnvWorkerUnavailable:
        return {}


def discover_all_composites(ws_root: Path, package_path: str) -> dict[str, dict]:
    """Discover composites from the workspace + every installed pbg-* package.

    If the workspace's package is also pip-installed (e.g., `pip install -e .`),
    the installed scan would re-find the same specs; the workspace scan runs
    first so workspace-relative `source` paths win.

    Also merges in `@composite_generator`-decorated functions from installed
    bigraph-schema-dependent packages via
    :func:`viva_superpowers.composite_discovery.discover_all`. Generator entries
    carry ``kind: "generator"`` and a ``module`` field; spec entries are tagged
    ``kind: "spec"`` and gain a derived ``module``. If pbg-superpowers is not
    importable the function falls back to spec-only behavior.
    """
    out: dict[str, dict] = {}
    out.update(discover_workspace_composites(ws_root, package_path))
    for spec_id, rec in discover_installed_pbg_composites().items():
        if spec_id not in out:
            out[spec_id] = rec

    # Federated composites from linked workspaces under external/ (read-only).
    from vivarium_workbench.lib import federation as _fed
    for spec_id, rec in _fed.federated_composites(ws_root).items():
        out.setdefault(spec_id, rec)

    # Tag every spec entry with kind + derived module (idempotent).
    for spec_id, rec in out.items():
        rec.setdefault("kind", "spec")
        if not rec.get("module"):
            rec["module"] = _derive_module_from_spec_id(spec_id)

    # Merge @composite_generator entries — discovered in the env worker (importing
    # generator modules is workspace Python, kept out of the HTTP process). The
    # workbench keeps the pure FS/YAML spec scan above + the shaping below.
    for gid, entry in _discover_generators_via_worker(ws_root).items():
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

    # Tag origin: own/installed/generator composites get origin_repo None
    # unless already set (federated recs already carry a truthy origin_repo).
    # Runs last so it covers every record, including generator entries merged
    # above.
    for rec in out.values():
        rec.setdefault("origin_repo", None)
        rec.setdefault("read_only", False)

    return out


def find_composite_path(ws_root: Path, package_path: str, spec_id: str) -> Path | None:
    """Resolve a composite spec id back to its on-disk path.

    Looks first in the workspace, then in installed pbg-* packages.
    """
    parts = spec_id.split(".composites.")
    if len(parts) != 2:
        return None
    pkg, stem = parts
    comp_dir = ws_root / pkg / "composites"
    # Workspace package first — the id's last segment as a filename stem.
    for suffix in (".composite.yaml", ".composite.yml", ".composite.json"):
        candidate = comp_dir / f"{stem}{suffix}"
        if candidate.is_file():
            return candidate
    # Fallback: the id's last segment may be the composite's `name` (with spaces
    # — e.g. "Interaction Modalities") rather than the filename stem
    # ("fig04a-interaction-modalities"). Scan for a spec whose name matches, so a
    # name-derived id still resolves to run (matches how resolve found it).
    if comp_dir.is_dir():
        for f in sorted(comp_dir.glob("*.composite.*")):
            try:
                text = f.read_text(encoding="utf-8")
                doc = (json.loads(text) if f.suffix.lower() == ".json"
                       else __import__("yaml").safe_load(text))
            except Exception:
                continue
            if isinstance(doc, dict) and doc.get("name") == stem:
                return f
    # Installed & federated packages. The workspace scan above resolves the
    # workspace's OWN package; this fallback resolves a spec that lives in an
    # installed distribution or a linked (federated) workspace under external/.
    # Historically only `pbg-*` distributions were scanned here, so a
    # wheel-installed package that does not follow that naming convention
    # (e.g. `spatio-flux`, or the post-rebrand `viva-*` packages) was invisible
    # and its composites failed to resolve with "not a registered composite" --
    # even though the LISTING path (discover_all_composites) already found them
    # via discover_installed_composites_all + federation. Consult the same
    # broadened set so resolve is consistent with list. pbg-* is tried first as
    # the fast path; each source is best-effort.
    def _federated_specs() -> dict[str, dict]:
        from vivarium_workbench.lib import federation as _fed  # noqa: PLC0415
        return _fed.federated_composites(ws_root)

    for _source in (
        discover_installed_pbg_composites,
        discover_installed_composites_all,
        _federated_specs,
    ):
        try:
            rec = _source().get(spec_id)
        except Exception:  # noqa: BLE001
            continue
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
        # discover_all_composites now includes the generator registry (via the
        # env worker's discover_composites, belt-and-suspenders over discover_all),
        # so no separate in-process _REGISTRY union is needed here.
        ids.update(discover_all_composites(ws_root, package_path or "").keys())
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


def composite_steps_index(ws_root: Path, package_path: str | None = None) -> dict[str, int]:
    """Map composite id / trailing-name / ``name`` → positive ``default_n_steps``.

    Best-effort (empty dict on any discovery failure). Powers the ``--steps N``
    hint on a study's/investigation's run command so a study stays "runnable
    like a composite" — N is its baseline composite's natural run length.
    """
    ws_root = Path(ws_root)
    if package_path is None:
        try:
            ws_data = yaml.safe_load((ws_root / "workspace.yaml").read_text(encoding="utf-8")) or {}
            package_path = ws_data.get("package_path") or (
                "pbg_" + str(ws_data.get("name", "")).replace("-", "_"))
        except Exception:  # noqa: BLE001
            package_path = ""
    idx: dict[str, int] = {}
    try:
        for cid, rec in discover_all_composites(ws_root, package_path).items():
            n = rec.get("default_n_steps")
            if isinstance(n, bool) or not isinstance(n, int) or n <= 0:
                continue
            idx[cid] = n
            idx[cid.split(".")[-1]] = n
            nm = rec.get("name")
            if isinstance(nm, str) and nm:
                idx[nm] = n
    except Exception:  # noqa: BLE001
        pass
    return idx


def baseline_steps_for_study(spec: dict, steps_index: dict) -> "int | None":
    """The study baseline composite's ``default_n_steps`` (or ``None``).

    Matches the study's first declared composite ref (baseline first, per
    :func:`_study_composite_refs`) against ``steps_index`` by full ref, the part
    after a ``local:``-style protocol, and the trailing name segment.
    """
    if not steps_index:
        return None
    for ref in _study_composite_refs(spec):
        for key in (ref, ref.split(":")[-1], ref.split(".")[-1]):
            n = steps_index.get(key)
            if n:
                return n
    return None


def _ref_resolves(ref: str, known_ids: set[str]) -> bool:
    """A declared ref resolves if it's a known spec id, OR shares the trailing
    ``.composites.<slug>`` segment with one (so a short ``slug`` alias matches
    a dotted ``pkg.composites.slug`` id), OR its final dotted segment is a
    UNIQUE match against a known id's final dotted segment.

    The last rule exists because generator ids have the shape
    ``<pkg>.composites.<module>.<name>`` — a bare/short ref (e.g. just the
    generator ``name``, or ``pkg.composites.name`` without the module) only
    has that trailing ``<name>`` in common with the registered id, not a full
    ``.composites.`` tail. It's deliberately unique-only: if two or more
    known ids share the same final segment, resolving on that basis would be
    a guess, so we leave it unresolved (ambiguous) rather than risk a false
    positive.
    """
    if ref in known_ids:
        return True
    tail = ref.rsplit(".composites.", 1)[-1]
    for kid in known_ids:
        if kid == ref or kid.rsplit(".composites.", 1)[-1] == tail:
            return True
    final = ref.rsplit(".", 1)[-1]
    matches = [kid for kid in known_ids if kid.rsplit(".", 1)[-1] == final]
    return len(matches) == 1


def suggest_composite_ref(ref: str, known_ids) -> Optional[str]:
    """Return the single closest known composite id to a non-resolving
    ``ref``, or ``None`` if nothing is reasonably close.

    Tries a full-string :func:`difflib.get_close_matches` first (catches
    near-typos of a full id). When that comes up empty, falls back to
    matching on the final dotted segment only (catches e.g. a Python
    function name — ``build_glucose_biomodel_do`` — copied in place of the
    registered generator ``name`` — ``glucose-biomodel-do``, which the
    full-string comparison scores too low to surface). Deterministic
    (inputs sorted); never raises.
    """
    try:
        ids_sorted = sorted(known_ids)
        matches = difflib.get_close_matches(ref, ids_sorted, n=1, cutoff=0.5)
        if matches:
            return matches[0]
        final = ref.rsplit(".", 1)[-1]
        # Map each known id's final segment back to one id (first, in sorted
        # order, wins) so the fallback match is deterministic even when
        # several ids share a final segment.
        final_to_id: dict[str, str] = {}
        for kid in ids_sorted:
            key = kid.rsplit(".", 1)[-1]
            final_to_id.setdefault(key, kid)
        final_matches = difflib.get_close_matches(
            final, sorted(final_to_id), n=1, cutoff=0.5)
        if final_matches:
            return final_to_id[final_matches[0]]
        return None
    except Exception:  # noqa: BLE001 — suggestion is best-effort, never fatal
        return None


def annotate_composite_registered(sims: list[dict], known_ids: set[str]) -> None:
    """Set ``row['composite_registered']`` on each Simulations-DB row in place.

    Enforcement: every simulation must map to exactly one registered composite.
    A row's ``spec_id`` is registered when it resolves against ``known_ids``
    ALIAS-TOLERANTLY (:func:`_ref_resolves`) — so a run recorded with the short
    ``baseline`` alias (or the doubled ``…baseline.baseline`` id) is recognised
    as the registered dotted ``v2ecoli.composites.baseline``, not falsely flagged
    as unregistered. A missing/empty ``spec_id`` is False (no composite).
    """
    for s in sims:
        cid = s.get("spec_id")
        s["composite_registered"] = bool(cid and _ref_resolves(cid, known_ids))


def unresolved_study_composite_refs(spec: dict, known_ids: set[str]) -> list[str]:
    """Return the study's declared composite refs that DON'T resolve to any
    registered composite id.

    Prefers ``viva_superpowers.report_linter.unresolved_composite_refs`` (the
    canonical, spec-only contract) when available; falls back to the local
    extraction + last-segment match. Defensive: never raises.

    The canonical linter is a STRICT membership test (``ref not in known_ids``)
    with no registry knowledge, so it can't resolve a short slug alias — e.g. a
    study declaring ``composite: baseline`` against the registered dotted id
    ``v2ecoli.composites.baseline``. The dashboard owns that alias semantics
    (see :func:`_ref_resolves`), so we keep it: a ref the strict linter flags is
    only reported when the local alias match ALSO can't resolve it. Without this
    every study using the short ``baseline`` alias false-flags as "composite not
    found in registry".
    """
    known = set(known_ids)
    try:
        from viva_superpowers.report_linter import unresolved_composite_refs as _ps
        flagged = list(_ps(spec, known))
    except Exception:  # noqa: BLE001 — older/absent viva_superpowers → local fallback
        return [r for r in _study_composite_refs(spec) if not _ref_resolves(r, known)]
    return [r for r in flagged if not _ref_resolves(r, known)]


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

    Moved from ``vivarium_workbench.server`` (Task 6) so it can be shared by
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


def composites_data(ws_root: Path) -> dict:
    """Pure data builder for GET /api/composites — returns ``{"composites": [...]}``.

    Discovers every composite visible to *ws_root* (workspace-local + installed
    ``pbg-*`` packages), applies the per-workspace registry allow-list, and
    collapses alias duplicates. Called by ``lib.composites_query`` (in a fresh
    subprocess) and by ``publish.build_bundle``.

    Moved from ``vivarium_workbench.server._composites_data`` so it has no
    dependency on the retired stdlib server or a module-global ``WORKSPACE``.
    """
    import importlib as _importlib

    ws_root = Path(ws_root)
    ws = str(ws_root)
    if ws not in sys.path:
        sys.path.insert(0, ws)
    try:
        from vivarium_workbench.lib.workspace_manifest_views import filter_composites
    except ImportError as e:
        return {"composites": [], "error": str(e)}

    try:
        ws_data = yaml.safe_load((ws_root / "workspace.yaml").read_text(encoding="utf-8"))
        pkg = ws_data.get("package_path") or ("pbg_" + ws_data.get("name", "").replace("-", "_"))
        try:
            _importlib.import_module(pkg)
        except Exception:
            pass
        specs = discover_all_composites(ws_root, pkg)
        ws_prefix_dot = pkg + "."
        out: list = []
        for s in specs.values():
            rec = {k: v for k, v in s.items() if not k.startswith("_")}
            rec.setdefault("kind", "spec")
            rec.setdefault("module", "")
            if "default_n_steps" not in rec:
                rec["default_n_steps"] = None
            mod = rec.get("module") or ""
            rec["workspace_local"] = bool(mod == pkg or mod.startswith(ws_prefix_dot))
            out.append(rec)
        out = filter_composites(out, ws_data)
        out = _dedupe_alias_composites(out)
        return {"composites": out, "workspace_package": pkg}
    except Exception as e:
        return {"composites": [], "error": str(e)}


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
