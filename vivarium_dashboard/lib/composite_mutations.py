"""Composite POST mutation builders (investigation composite YAML sidecars).

Four endpoints manipulate an investigation's composite sidecar documents and
the study spec's ``composites`` / ``variants`` lists:

    (ws_root: Path, body: dict) -> tuple[dict, int]

File side-effects only — no HTTP, no server imports, no git operations.

Routes covered:
  - POST /api/investigation-composite-add  → ``add_investigation_composite``:
      clone a registered workspace composite (YAML source or
      ``@composite_generator``) into the study as a sidecar + spec
      ``composites`` entry.
  - POST /api/investigation-composite-perturb → ``perturb_investigation_composite``:
      derive a new composite from an existing sidecar by applying
      parameter/process overrides; upsert a v2 ``variants`` entry (replace
      in-place by name).
  - POST /api/composite-promote-to-catalog → ``promote_composite_to_catalog``:
      promote a variant's sidecar into the workspace composite catalog as
      ``<pkg>/composites/<target>.composite.yaml`` + mark the variant
      ``promoted``; returns ``name`` / ``path`` augmentation.
  - POST /api/investigation-composite-rebuild → ``rebuild_investigation_composite``:
      re-render a derived composite by re-applying the recipe overrides on the
      current parent document.

Each public builder is the FULL flow (validation + computation + mutation) that
the FastAPI route calls directly. The git-committing legacy server keeps its
``_commit_or_run`` wrapper and its heavy pre-wrapper computation verbatim,
delegating ONLY the file-writing mutation to the private ``_apply_*`` helpers
here (which take the already-computed values) — so the live shim's
pre/post-wrapper sections stay byte-identical to before this batch.

Batch 27 of the FastAPI strangler-fig migration (POST phase, Phase C).
"""
from __future__ import annotations

import copy
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

import yaml

from vivarium_dashboard.lib import study_spec as _study_spec
from vivarium_dashboard.lib.upload_mutations import _ws_add_to_sys_path
from vivarium_dashboard.lib.workspace_paths import WorkspacePaths


def _spec_path_for(inv_dir: Path) -> Path:
    """Replicate the server's ``study.yaml``-else-``spec.yaml`` selection EXACTLY.

    Mirrors the inline expression in every legacy composite handler:
    ``(inv_dir / "study.yaml") if (inv_dir / "study.yaml").is_file()
    else (inv_dir / "spec.yaml")``.
    """
    return (
        (inv_dir / "study.yaml")
        if (inv_dir / "study.yaml").is_file()
        else (inv_dir / "spec.yaml")
    )


# ---------------------------------------------------------------------------
# add_investigation_composite  (POST /api/investigation-composite-add)
# ---------------------------------------------------------------------------


def add_investigation_composite(ws_root: Path, body: dict[str, Any]) -> "tuple[dict, int]":
    """POST /api/investigation-composite-add {investigation, name, source}.

    Clone a registered workspace composite into the study. ``source`` resolves
    to a YAML source path on disk OR a registered ``@composite_generator`` (the
    latter is materialized to a concrete doc so the sidecar has something to
    dump). Writes the sidecar + appends a spec ``composites`` entry.

    Returns:
      200  {ok: True}
      400  validation / generator-not-serializable
      404  unknown source / investigation not found
      409  composite name already exists
    """
    inv_name = (body.get("investigation") or "").strip()
    comp_name = (body.get("name") or "").strip()
    source = (body.get("source") or "").strip()
    if not (inv_name and comp_name and source):
        return {"error": "investigation, name, source required"}, 400

    _ws_add_to_sys_path(ws_root)
    from vivarium_dashboard.lib.investigation_migrate import (
        _resolve_composite_source_or_generate,
        materialize_generator_doc,
    )
    try:
        source_path, is_generator, _stem = (
            _resolve_composite_source_or_generate(source, ws_root)
        )
    except (FileNotFoundError, ValueError) as e:
        return {"error": str(e)}, 404

    inv_dir = _study_spec.study_dir(ws_root, inv_name)
    spec_path = _spec_path_for(inv_dir)
    if not spec_path.is_file():
        return {"error": "investigation not found"}, 404
    composites_dir = inv_dir / "composites"
    composites_dir.mkdir(parents=True, exist_ok=True)
    sidecar = composites_dir / f"{comp_name}.yaml"
    if sidecar.is_file():
        return {"error": f"composite {comp_name!r} already exists"}, 409

    # For generator refs we materialize the doc now so the YAML sidecar
    # write below has something concrete to dump. Composites whose
    # state contains non-serializable objects (e.g. live Process
    # instances) will surface a clear error here.
    if is_generator:
        try:
            generator_doc = materialize_generator_doc(source)
        except Exception as e:  # noqa: BLE001
            return {
                "error": (
                    f"composite {source!r} can't be serialized as a "
                    f"YAML sidecar: {e}"
                )
            }, 400
    else:
        generator_doc = None

    _apply_add_investigation_composite(
        ws_root,
        sidecar=sidecar,
        source_path=source_path,
        generator_doc=generator_doc,
        spec_path=spec_path,
        comp_name=comp_name,
        source=source,
    )
    return {"ok": True}, 200


def _apply_add_investigation_composite(
    ws_root: Path,
    *,
    sidecar: Path,
    source_path: "Path | None",
    generator_doc: "dict | None",
    spec_path: Path,
    comp_name: str,
    source: str,
) -> None:
    """File writes for the composite-add flow (formerly the do_action() body)."""
    if source_path is not None:
        shutil.copy2(source_path, sidecar)
    else:
        sidecar.write_text(yaml.safe_dump(generator_doc, sort_keys=False), encoding="utf-8")
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
    composites = spec.setdefault('composites', [])
    composites.append({
        'name': comp_name,
        'source': source,
        'document': f'./composites/{comp_name}.yaml',
    })
    spec_path.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# perturb_investigation_composite  (POST /api/investigation-composite-perturb)
# ---------------------------------------------------------------------------


def perturb_investigation_composite(ws_root: Path, body: dict[str, Any]) -> "tuple[dict, int]":
    """POST /api/investigation-composite-perturb {investigation|study, name,
    extends, description?, parameter_overrides?, process_overrides?}.

    Derive a new composite from an existing sidecar by applying overrides, and
    register it as a v2 ``variants`` entry (intervention recipe nested). If a
    variant with ``name`` already exists it is REPLACED in-place. NO 409 — an
    existing-variant perturb means "edit this intervention".

    Returns:
      200  {ok: True}
      400  validation / override KeyError
      404  investigation / parent composite not found
      500  override failed (non-KeyError)
    """
    inv_name = (body.get("investigation") or body.get("study") or "").strip()
    comp_name = (body.get("name") or "").strip()
    extends = (body.get("extends") or "").strip()
    if not (inv_name and comp_name and extends):
        return {"error": "investigation, name, extends required"}, 400

    _ws_add_to_sys_path(ws_root)
    inv_dir = _study_spec.study_dir(ws_root, inv_name)
    spec_path = _spec_path_for(inv_dir)
    if not spec_path.is_file():
        return {"error": "investigation not found"}, 404

    parent = inv_dir / "composites" / f"{extends}.yaml"
    if not parent.is_file():
        return {"error": f"parent composite {extends!r} not found"}, 404

    composites_dir = inv_dir / "composites"
    derived = composites_dir / f"{comp_name}.yaml"
    # NB: do NOT 409 on existing — perturb of an existing variant means
    # "edit this intervention", which overwrites the sidecar in-place.

    from vivarium_dashboard.lib.composite_recipes import (
        apply_parameter_overrides, apply_process_overrides,
    )
    parent_doc = yaml.safe_load(parent.read_text(encoding="utf-8")) or {}
    derived_doc = copy.deepcopy(parent_doc)
    try:
        if body.get('parameter_overrides'):
            apply_parameter_overrides(derived_doc, body['parameter_overrides'])
        if body.get('process_overrides'):
            apply_process_overrides(derived_doc, body['process_overrides'])
    except KeyError as e:
        return {"error": f"override failed: {e}"}, 400
    except Exception as e:  # noqa: BLE001
        return {"error": f"override failed: {type(e).__name__}: {e}"}, 500

    _apply_perturb_investigation_composite(
        ws_root,
        derived=derived,
        derived_doc=derived_doc,
        spec_path=spec_path,
        comp_name=comp_name,
        extends=extends,
        body=body,
    )
    return {"ok": True}, 200


def _apply_perturb_investigation_composite(
    ws_root: Path,
    *,
    derived: Path,
    derived_doc: dict,
    spec_path: Path,
    comp_name: str,
    extends: str,
    body: dict[str, Any],
) -> None:
    """File writes for the composite-perturb flow (formerly the do_action() body)."""
    derived.write_text(yaml.safe_dump(derived_doc, sort_keys=False), encoding="utf-8")
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
    variants = spec.setdefault('variants', [])
    entry: dict[str, Any] = {'name': comp_name, 'extends': extends,
                             'document': f'./composites/{comp_name}.yaml'}
    intervention = {
        'description': body.get('description') if body.get('description') is not None else '',
    }
    if body.get('parameter_overrides'):
        intervention['parameter_overrides'] = body['parameter_overrides']
    if body.get('process_overrides'):
        intervention['process_overrides'] = body['process_overrides']
    # Only attach the intervention block if at least one override was
    # supplied; description-only on a derived variant would otherwise
    # carry an empty recipe.
    if intervention.get('parameter_overrides') or intervention.get('process_overrides'):
        entry['intervention'] = intervention
    existing_idx = next(
        (i for i, v in enumerate(variants) if v.get('name') == comp_name),
        None,
    )
    if existing_idx is not None:
        variants[existing_idx] = entry  # full replace
    else:
        variants.append(entry)
    spec_path.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# promote_composite_to_catalog  (POST /api/composite-promote-to-catalog)
# ---------------------------------------------------------------------------


def promote_composite_to_catalog(ws_root: Path, body: dict[str, Any]) -> "tuple[dict, int]":
    """POST /api/composite-promote-to-catalog {investigation, variant,
    target_name?, description?}.

    Promote an investigation variant's sidecar composite into the
    workspace-level composite catalog as a new
    ``<pkg>/composites/<target_name>.composite.yaml`` file (with the doc's
    ``name`` set to ``target_name`` and, if provided, ``description`` set) and
    mark the variant ``promoted: true`` in the spec. Non-destructive (409 on a
    pre-existing catalog entry).

    Returns:
      200  {ok: True, name: <target_name>, path: <relative path>}
      400  validation
      404  investigation / variant sidecar not found
      409  catalog entry already exists
      500  failed to read workspace.yaml
    """
    inv_name = (body.get("investigation") or "").strip()
    variant_name = (body.get("variant") or "").strip()
    target_name = (body.get("target_name") or variant_name).strip()
    description = body.get("description")
    if not (inv_name and variant_name):
        return {"error": "investigation, variant required"}, 400

    # Resolve workspace package path using the same pattern as other handlers.
    try:
        ws_data = yaml.safe_load((ws_root / "workspace.yaml").read_text(encoding="utf-8")) or {}
    except Exception as e:  # noqa: BLE001
        return {"error": f"failed to read workspace.yaml: {e}"}, 500
    pkg = ws_data.get("package_path") or (
        "pbg_" + (ws_data.get("name") or "").replace("-", "_")
    )
    catalog_dir = ws_root / pkg / "composites"

    # Source paths
    inv_dir = _study_spec.study_dir(ws_root, inv_name)
    spec_path = _spec_path_for(inv_dir)
    if not spec_path.is_file():
        return {"error": f"investigation {inv_name!r} not found"}, 404
    sidecar = inv_dir / "composites" / f"{variant_name}.yaml"
    if not sidecar.is_file():
        return {"error": f"variant {variant_name!r} sidecar not found"}, 404

    # Refuse if catalog already has this target
    target_path = catalog_dir / f"{target_name}.composite.yaml"
    if target_path.exists():
        return {"error": f"catalog entry {target_name!r} already exists"}, 409

    rel_path = str(target_path.relative_to(ws_root))

    _apply_promote_composite_to_catalog(
        ws_root,
        catalog_dir=catalog_dir,
        sidecar=sidecar,
        target_path=target_path,
        target_name=target_name,
        description=description,
        spec_path=spec_path,
        variant_name=variant_name,
    )
    # Mirror the legacy post-wrapper augmentation (resp["name"]/resp["path"]).
    return {"ok": True, "name": target_name, "path": rel_path}, 200


def _apply_promote_composite_to_catalog(
    ws_root: Path,
    *,
    catalog_dir: Path,
    sidecar: Path,
    target_path: Path,
    target_name: str,
    description: Any,
    spec_path: Path,
    variant_name: str,
) -> None:
    """File writes for the promote-to-catalog flow (formerly the do_action() body)."""
    catalog_dir.mkdir(parents=True, exist_ok=True)
    doc = yaml.safe_load(sidecar.read_text(encoding="utf-8")) or {}
    doc['name'] = target_name
    if description is not None:
        doc['description'] = description
    target_path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    # Mark variant promoted in spec.yaml
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
    for v in (spec.get('variants') or []):
        if v.get('name') == variant_name:
            v['promoted'] = True
            break
    spec_path.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# rebuild_investigation_composite  (POST /api/investigation-composite-rebuild)
# ---------------------------------------------------------------------------


def rebuild_investigation_composite(ws_root: Path, body: dict[str, Any]) -> "tuple[dict, int]":
    """POST /api/investigation-composite-rebuild {investigation, name}.

    Re-render a derived composite from its recipe (re-applies overrides on the
    current parent document).

    Returns:
      200  {ok: True}
      400  validation / not derived (no extends) / override KeyError
      404  investigation / composite / parent document not found
      500  rebuild failed (non-KeyError)
    """
    inv_name = (body.get("investigation") or "").strip()
    comp_name = (body.get("name") or "").strip()
    if not (inv_name and comp_name):
        return {"error": "investigation, name required"}, 400

    inv_dir = _study_spec.study_dir(ws_root, inv_name)
    spec_path = _spec_path_for(inv_dir)
    if not spec_path.is_file():
        return {"error": "investigation not found"}, 404
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
    entry = next((c for c in (spec.get('composites') or [])
                  if c.get('name') == comp_name), None)
    if entry is None:
        return {"error": f"composite {comp_name!r} not found"}, 404
    extends = entry.get('extends')
    if not extends:
        return {"error": f"composite {comp_name!r} is not derived (no extends)"}, 400
    parent_path = inv_dir / "composites" / f"{extends}.yaml"
    if not parent_path.is_file():
        return {"error": f"parent {extends!r} document missing"}, 404

    from vivarium_dashboard.lib.composite_recipes import (
        apply_parameter_overrides, apply_process_overrides,
    )
    parent_doc = yaml.safe_load(parent_path.read_text(encoding="utf-8")) or {}
    derived_doc = copy.deepcopy(parent_doc)
    try:
        if entry.get('parameter_overrides'):
            apply_parameter_overrides(derived_doc, entry['parameter_overrides'])
        if entry.get('process_overrides'):
            apply_process_overrides(derived_doc, entry['process_overrides'])
    except KeyError as e:
        return {"error": f"rebuild failed: {e}"}, 400
    except Exception as e:  # noqa: BLE001
        return {"error": f"rebuild failed: {type(e).__name__}: {e}"}, 500

    _apply_rebuild_investigation_composite(
        ws_root,
        inv_dir=inv_dir,
        derived_doc=derived_doc,
        comp_name=comp_name,
    )
    return {"ok": True}, 200


def _apply_rebuild_investigation_composite(
    ws_root: Path,
    *,
    inv_dir: Path,
    derived_doc: dict,
    comp_name: str,
) -> None:
    """File writes for the composite-rebuild flow (formerly the do_action() body)."""
    derived_path = inv_dir / "composites" / f"{comp_name}.yaml"
    derived_path.write_text(yaml.safe_dump(derived_doc, sort_keys=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# create_from_composite  (POST /api/study-create-from-composite)
# ---------------------------------------------------------------------------
#
# Batch 28: clone a workspace-catalog composite into a fresh study.
# The git-committing legacy server keeps its heavy pre-wrapper (catalog scan,
# match-by-name-then-id-stem, generator-vs-file resolution, uuid auto-name and
# 409 collision), its ``commit_msg``, its ``try/_commit_or_run/except`` and the
# ``{"name": auto_name}`` post-wrapper VERBATIM — delegating ONLY the file
# writes to ``_apply_create_from_composite`` (which receives the
# already-computed values). The public ``create_from_composite`` re-does the
# full validation+compute+_apply for the FastAPI path.


def create_from_composite(ws_root: Path, body: dict[str, Any]) -> "tuple[dict, int]":
    """POST /api/study-create-from-composite {composite_name}.

    Clone a workspace-catalog composite into a fresh study. The catalog
    is the union of the workspace's own ``pbg_<slug>/composites/`` and every
    installed ``pbg-*`` package's ``composites/`` directory. ``composite_name``
    is matched against the catalog record's ``name`` first, then the dotted-id
    stem (the bit after ``...composites.``).

    Creates ``studies/<auto-name>/`` with a v2-shape ``spec.yaml`` and copies
    the resolved source YAML to ``./composites/<composite_name>.yaml``.

    Returns:
      200  {name: <auto-name>}
      400  validation / generator build failed
      404  composite not in catalog / generator not registered / source missing
      409  auto-named investigation already exists (uuid collision)
      500  lookup unavailable / workspace.yaml read / catalog scan / generator dep
    """
    composite_name = (body.get("composite_name") or "").strip()
    if not composite_name:
        return {"error": "composite_name required"}, 400

    _ws_add_to_sys_path(ws_root)
    try:
        from vivarium_dashboard.lib.composite_lookup import discover_all_composites
        from vivarium_dashboard.lib.investigation_migrate import _resolve_composite_source
    except ImportError as e:
        return {"error": f"composite lookup unavailable: {e}"}, 500

    # Resolve composite_name → dotted source ref via the workspace catalog.
    try:
        ws_data = yaml.safe_load((ws_root / "workspace.yaml").read_text(encoding="utf-8")) or {}
    except Exception as e:  # noqa: BLE001
        return {"error": f"failed to read workspace.yaml: {e}"}, 500
    pkg = ws_data.get("package_path") or (
        "pbg_" + (ws_data.get("name") or "").replace("-", "_")
    )
    try:
        catalog = discover_all_composites(ws_root, pkg)
    except Exception as e:  # noqa: BLE001
        return {"error": f"catalog scan failed: {e}"}, 500

    # Match by YAML name first, then by id-stem (the bit after `.composites.`).
    match_rec = None
    for rec in catalog.values():
        if rec.get("name") == composite_name:
            match_rec = rec
            break
    if match_rec is None:
        for rec_id, rec in catalog.items():
            stem = rec_id.rsplit(".composites.", 1)[-1]
            if stem == composite_name:
                match_rec = rec
                break
    if match_rec is None:
        return {"error": f"composite {composite_name!r} not in workspace catalog"}, 404

    source_ref = match_rec["id"]  # e.g. pbg_testws.composites.foo
    is_generator = match_rec.get("kind") == "generator"
    generator_doc = None
    source_path = None
    if is_generator:
        # Generator-kind: build the doc now and write it as a frozen YAML
        # snapshot. The variant's `source` field still references the
        # generator id (provenance); from the study's POV the sidecar is
        # an ordinary spec.
        try:
            from pbg_superpowers.composite_generator import _REGISTRY, build_generator, discover_generators
        except ImportError as e:
            return {
                "error": f"pbg-superpowers unavailable for generator resolution: {e}"
            }, 500
        if not _REGISTRY:
            discover_generators()
        entry = _REGISTRY.get(source_ref)
        if entry is None:
            return {
                "error": f"generator {source_ref!r} not in registry — was it imported?"
            }, 404
        try:
            generator_doc = build_generator(entry)
        except Exception as e:  # noqa: BLE001
            return {"error": f"generator build failed: {e}"}, 400
    else:
        try:
            source_path, _stem = _resolve_composite_source(source_ref, ws_root)
        except (FileNotFoundError, ValueError) as e:
            # Catalog has it but source file is gone (or installed package outside
            # the workspace tree). Fall back to the catalog's recorded path.
            recorded = match_rec.get("_path")
            if recorded and Path(recorded).is_file():
                source_path = Path(recorded)
            else:
                return {"error": str(e)}, 404

    # Auto-name: study-<slug>-<6-char-hex>. Slugify: lowercase, replace
    # any non-[a-z0-9_-] with '-', collapse repeats.
    slug = re.sub(r"[^a-z0-9_-]+", "-", composite_name.lower()).strip("-") or "composite"
    slug = re.sub(r"-+", "-", slug)
    auto_name = f"study-{slug}-{uuid.uuid4().hex[:6]}"

    wp = WorkspacePaths.load(ws_root)
    inv_dir = wp.studies / auto_name
    if inv_dir.exists() or (wp.investigations / auto_name).exists():
        # Collision is astronomically unlikely with 24 bits of entropy, but
        # if it happens (e.g. a test seeds uuid), surface it rather than
        # silently overwriting.
        return {"error": f"investigation {auto_name!r} already exists"}, 409

    _apply_create_from_composite(
        ws_root,
        inv_dir=inv_dir,
        composite_name=composite_name,
        is_generator=is_generator,
        generator_doc=generator_doc,
        source_path=source_path,
        source_ref=source_ref,
        auto_name=auto_name,
    )
    return {"name": auto_name}, 200


def _apply_create_from_composite(
    ws_root: Path,
    *,
    inv_dir: Path,
    composite_name: str,
    is_generator: bool,
    generator_doc: "dict | None",
    source_path: "Path | None",
    source_ref: str,
    auto_name: str,
) -> None:
    """File writes for the create-from-composite flow (formerly do_action())."""
    composites_dir = inv_dir / "composites"
    composites_dir.mkdir(parents=True, exist_ok=True)
    sidecar = composites_dir / f"{composite_name}.yaml"
    if is_generator:
        sidecar.write_text(yaml.safe_dump(generator_doc, sort_keys=False), encoding="utf-8")
    else:
        assert source_path is not None  # non-generator → resolved source path
        shutil.copy2(source_path, sidecar)

    spec = {
        "name": auto_name,
        "baseline": composite_name,
        "variants": [{
            "name": composite_name,
            "source": source_ref,
            "document": f"./composites/{composite_name}.yaml",
        }],
        "comparisons": [],
        "conclusions": "",
        "question": "",
        "hypothesis": "",
        "status": "draft",
    }
    (inv_dir / "spec.yaml").write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
