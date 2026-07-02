"""Upload / import POST mutation builders.

Pure builders for the three upload/import endpoints:

    (ws_root: Path, body: dict) -> tuple[dict, int]

File side-effects only — no HTTP, no server imports, no git operations.

Routes covered:
  - POST /api/dataset      → save a dataset (file/path/url) + register in
                             workspace.yaml or investigations/<inv>.yaml
  - POST /api/expert-doc   → save an expert doc + register in
                             workspace.yaml.expert_docs or investigation yaml
  - POST /api/import       → register an import in workspace.yaml.imports

These are ``_active_branch_action``-wrapped routes; the lib builder performs
validation + mutation (no git).  The server keeps ``_active_branch_action`` and
delegates the mutation to these builders from inside the ``action()`` closure.

Batch 25 of the FastAPI strangler-fig migration (POST phase, Phase C).
"""
from __future__ import annotations

import base64
import hashlib
import re
import shutil
import sys
from pathlib import Path
from typing import Any

import yaml

from vivarium_dashboard.lib import investigation_status as _invstatus
from vivarium_dashboard.lib.imports import register_import
from vivarium_dashboard.lib.study_spec import SLUG_RE as _SLUG_RE
from vivarium_dashboard.lib.workspace_yaml import (
    WorkspaceValidationError,
    load_workspace,
    save_workspace,
)


# ---------------------------------------------------------------------------
# Internal helpers (lib-local copies of the server's helpers; no server import)
# ---------------------------------------------------------------------------


def _ws_add_to_sys_path(ws_root: Path) -> None:
    """Ensure the workspace root is on ``sys.path`` so its package is importable."""
    ws = str(ws_root)
    if ws not in sys.path:
        sys.path.insert(0, ws)


def _safe_slug(s: str) -> str:
    """Convert a string to a safe branch name component."""
    s = re.sub(r"[^a-zA-Z0-9_-]", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:40]


def _save_upload(file_b64: str, target_path: Path) -> str:
    """Decode base64-encoded file content, write to target_path, return sha256."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    raw = base64.b64decode(file_b64)
    target_path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _investigation_yaml_path(ws_root: Path, inv: str) -> "Path | None":
    """Resolve investigations/<inv>/investigation.yaml, or None if missing."""
    for d in _invstatus.iter_iset_dirs(ws_root):
        if d.name == inv:
            p = d / "investigation.yaml"
            return p if p.is_file() else None
    return None


def _append_investigation_input(ws_root: Path, inv: str, category: str, entry: Any) -> bool:
    """Append ``entry`` to investigation.yaml's ``inputs.<category>`` list.

    ``category`` is one of ``datasets``, ``references``, ``expert_docs``.
    ``entry`` is a dict (datasets / expert_docs) or a bare bib-key string
    (references). Prefers ruamel for round-trip preservation, falling back to
    safe_dump. Returns True on success.
    """
    target = _investigation_yaml_path(ws_root, inv)
    if target is None:
        return False

    def _mutate(spec: dict) -> dict:
        block = spec.get("inputs")
        if not isinstance(block, dict):
            block = {}
            spec["inputs"] = block
        lst = block.get(category)
        if not isinstance(lst, list):
            lst = []
            block[category] = lst
        # De-dupe by name/path (dicts) or value (strings).
        if isinstance(entry, dict):
            ident = entry.get("path") or entry.get("name")
            for ex in lst:
                if isinstance(ex, dict) and (ex.get("path") == ident or ex.get("name") == entry.get("name")):
                    return spec
        else:
            if entry in lst:
                return spec
        lst.append(entry)
        return spec

    try:
        from ruamel.yaml import YAML as _RYAML
        _ry = _RYAML(); _ry.preserve_quotes = True; _ry.width = 4096
        spec = _ry.load(target.read_text(encoding="utf-8")) or {}
        spec = _mutate(spec)
        with target.open("w", encoding="utf-8") as _fh:
            _ry.dump(spec, _fh)
    except Exception:
        spec = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
        spec = _mutate(spec)
        target.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
    return True


# ---------------------------------------------------------------------------
# register_dataset
# ---------------------------------------------------------------------------


def register_dataset(ws_root: Path, body: dict[str, Any]) -> "tuple[dict, int]":
    """POST /api/dataset — save a dataset + register it.

    Body (one of file/path/url required):
      {name, claims?, file_b64?, filename?, path?, url?, sha256?, investigation?}

    Returns:
      200  {ok: True}
      400  validation failures (name / slug / filename / no-source)
      404  investigation not found
      409  dataset name already registered
    """
    name = (body.get("name") or "").strip()
    if not name:
        return {"error": "name is required"}, 400
    claims_raw = body.get("claims", "")
    if isinstance(claims_raw, str):
        claims = [c.strip() for c in claims_raw.split(",") if c.strip()]
    elif isinstance(claims_raw, list):
        claims = list(claims_raw)
    else:
        claims = []

    entry: dict = {"name": name, "claims": claims}

    file_b64 = body.get("file_b64", "").strip()
    filename = (body.get("filename") or "").strip()
    path = (body.get("path") or "").strip()
    url = (body.get("url") or "").strip()
    investigation = (body.get("investigation") or "").strip()
    if investigation and not _SLUG_RE.match(investigation):
        return {"error": f"invalid investigation slug: '{investigation}'"}, 400

    if file_b64:
        if not filename:
            return {"error": "filename is required when file_b64 is provided"}, 400
        if investigation:
            dest_rel = f"investigations/{investigation}/inputs/datasets/{_safe_slug(name)}/{filename}"
        else:
            dest_rel = f"datasets/{_safe_slug(name)}/{filename}"
        entry["path"] = dest_rel
    elif path:
        entry["path"] = path
    elif url:
        entry["url"] = url
        sha256 = body.get("sha256", "").strip()
        if sha256:
            entry["sha256"] = sha256
    else:
        return {"error": "either file_b64, path, or url is required"}, 400

    # --- mutation (formerly the action() closure) ---
    if file_b64:
        dest = ws_root / entry["path"]
        sha = _save_upload(file_b64, dest)
        entry["sha256"] = sha
    elif path and not file_b64:
        src = Path(path)
        if not src.is_absolute():
            src = ws_root / path
        if src.exists() and src.is_file():
            h = hashlib.sha256()
            with src.open("rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            entry["sha256"] = h.hexdigest()

    _ws_add_to_sys_path(ws_root)
    if investigation:
        # Investigation-scoped: append to investigations/<slug>/investigation.yaml.
        if not _append_investigation_input(ws_root, investigation, "datasets", entry):
            return {"error": f"investigation '{investigation}' not found"}, 404
        return {"ok": True}, 200
    ws_file = ws_root / "workspace.yaml"
    ws = load_workspace(ws_file)
    datasets = ws.setdefault("datasets", [])
    if datasets is None:
        datasets = []
        ws["datasets"] = datasets
    for existing in datasets:
        if isinstance(existing, dict) and existing.get("name") == name:
            return {"error": f"dataset '{name}' already registered"}, 409
    datasets.append(entry)
    save_workspace(ws_file, ws)
    return {"ok": True}, 200


# ---------------------------------------------------------------------------
# register_expert_doc
# ---------------------------------------------------------------------------


def register_expert_doc(ws_root: Path, body: dict[str, Any]) -> "tuple[dict, int]":
    """POST /api/expert-doc — save an expert document + register it.

    Body (one of file_b64+filename / source_path required):
      {name, file_b64?, filename?, source_path?, description?, contributor?,
       claims_supported?, investigation?}

    Returns:
      200  {ok: True}
      400  validation failures (slug / name / no-source / filename / source_path)
      404  investigation not found
      409  expert doc name already registered
    """
    name = (body.get("name") or "").strip()
    file_b64 = body.get("file_b64", "").strip()
    filename = (body.get("filename") or "").strip()
    source_path_raw = (body.get("source_path") or "").strip()
    description = (body.get("description") or "").strip() or None
    contributor = (body.get("contributor") or "").strip() or None
    claims_raw = body.get("claims_supported", [])

    investigation = (body.get("investigation") or "").strip()
    if investigation and not _SLUG_RE.match(investigation):
        return {"error": f"invalid investigation slug: '{investigation}'"}, 400

    if not name:
        return {"error": "name is required"}, 400
    if not file_b64 and not source_path_raw:
        return {"error": "either file_b64+filename or source_path is required"}, 400

    if isinstance(claims_raw, str):
        claims_supported = [c.strip() for c in claims_raw.split(",") if c.strip()]
    elif isinstance(claims_raw, list):
        claims_supported = list(claims_raw)
    else:
        claims_supported = []

    expert_dir = (f"investigations/{investigation}/inputs/expert"
                  if investigation else "references/expert")

    if file_b64:
        if not filename:
            return {"error": "filename is required when file_b64 is provided"}, 400
        ext = Path(filename).suffix if Path(filename).suffix else ".pdf"
        dest_rel = f"{expert_dir}/{_safe_slug(name)}{ext}"
        source_path = None
    else:
        source_path = Path(source_path_raw)
        if not source_path.is_absolute():
            source_path = ws_root / source_path
        if not source_path.exists():
            return {"error": f"source_path does not exist: {source_path}"}, 400
        if not source_path.is_file():
            return {"error": f"source_path is not a regular file: {source_path}"}, 400
        ext = source_path.suffix if source_path.suffix else ".pdf"
        dest_rel = f"{expert_dir}/{_safe_slug(name)}{ext}"

    # --- mutation (formerly the action() closure) ---
    dest = ws_root / dest_rel
    dest.parent.mkdir(parents=True, exist_ok=True)

    if file_b64:
        sha = _save_upload(file_b64, dest)
    else:
        shutil.copy2(str(source_path), str(dest))
        h = hashlib.sha256()
        with dest.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        sha = h.hexdigest()

    _ws_add_to_sys_path(ws_root)
    entry: dict = {"name": name, "path": dest_rel, "sha256": sha}
    if description:
        entry["description"] = description
    if contributor:
        entry["contributor"] = contributor
    if claims_supported:
        entry["claims_supported"] = claims_supported

    if investigation:
        if not _append_investigation_input(ws_root, investigation, "expert_docs", entry):
            return {"error": f"investigation '{investigation}' not found"}, 404
        return {"ok": True}, 200
    ws_file = ws_root / "workspace.yaml"
    ws = load_workspace(ws_file)
    expert_docs = ws.setdefault("expert_docs", [])
    if expert_docs is None:
        expert_docs = []
        ws["expert_docs"] = expert_docs
    for existing in expert_docs:
        if isinstance(existing, dict) and existing.get("name") == name:
            return {"error": f"expert doc '{name}' already registered"}, 409
    expert_docs.append(entry)
    save_workspace(ws_file, ws)
    return {"ok": True}, 200


# ---------------------------------------------------------------------------
# register_import_entry
# ---------------------------------------------------------------------------


def register_import_entry(ws_root: Path, body: dict[str, Any]) -> "tuple[dict, int]":
    """POST /api/import — register an import in workspace.yaml.imports.

    NOTE: git submodule add is NOT performed (requires terminal for
    network/auth); the response carries the exact command to run.

    Body: {name, source, ref, mode, description?}

    Returns:
      200  {ok: True, next_terminal_step, note}
      400  validation failures (required fields / mode / name chars)
      409  import name already registered
    """
    name = (body.get("name") or "").strip()
    source = (body.get("source") or "").strip()
    ref = (body.get("ref") or "").strip()
    mode = (body.get("mode") or "").strip()
    description = (body.get("description") or "").strip() or None

    if not all([name, source, ref, mode]):
        return {"error": "name, source, ref, mode are required"}, 400
    if mode not in ("reference", "fork-source", "in-place"):
        return {"error": "mode must be one of: reference, fork-source, in-place"}, 400
    if re.search(r'[^\w\-.]', name):
        return {"error": "name must contain only word chars, hyphens, dots"}, 400

    # --- mutation (formerly the action() closure) ---
    _ws_add_to_sys_path(ws_root)
    try:
        register_import(
            ws_root, name=name, source=source, ref=ref, mode=mode,
            description=description,
        )
    except WorkspaceValidationError as exc:
        return {"error": str(exc)}, 409

    resp: dict = {"ok": True}
    # Guidance about the submodule step (mirrors the live server shim shaping).
    if mode in ("reference",):
        resp["next_terminal_step"] = f"git submodule add {source} external/{name}"
    elif mode == "in-place":
        resp["next_terminal_step"] = f"git submodule add {source} external/{name}"
    else:
        resp["next_terminal_step"] = "(fork-source: no submodule needed)"
    resp["note"] = (
        "git submodule add is NOT performed by the server (requires terminal for network/auth). "
        "Run 'next_terminal_step' from your workspace root to complete the import."
    )
    return resp, 200
