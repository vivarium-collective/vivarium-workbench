"""Study lifecycle + feedback POST mutation builders.

Each builder is ws_root-parameterised and AI-free:

    (ws_root: Path, body: dict) -> tuple[dict, int]

Returns (response_dict, status_code). File side-effects only — no HTTP,
no server imports, no git operations.

The 6 lifecycle seams are moved here verbatim from server.py. The server
keeps name-shims for backward compatibility with test imports.

Note: ``_decide_proposed_input_for_test`` in server.py has a distinct
positional signature ``(ws_root, inv, item_id, decision)``; its name-shim
reconstructs a body dict and delegates here.

Batch 20 of the FastAPI strangler-fig migration (POST phase, Phase C).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

from vivarium_dashboard.lib import investigation_status as _invstatus
from vivarium_dashboard.lib.study_spec import SLUG_RE as _SLUG_RE


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _investigation_yaml_path(ws_root: Path, inv: str) -> "Path | None":
    """Resolve investigations/<inv>/investigation.yaml, or None if missing."""
    for d in _invstatus.iter_iset_dirs(ws_root):
        if d.name == inv:
            p = d / "investigation.yaml"
            return p if p.is_file() else None
    return None


def _sync_parent_investigation(ws_root: Any, study_dir: Any) -> None:
    """Best-effort SP1 hook: re-write the parent investigation's computed
    acceptance so the verdict on disk tracks the member study's new outcome.

    No-op when the study has no owning investigation, or when the installed
    pbg_superpowers predates ``sync_investigation``. Never raises — a record
    error must not fail a successful sync.
    """
    try:
        from pbg_superpowers import study_outcomes
        from vivarium_dashboard.lib.workspace_paths import WorkspacePaths

        sync_investigation = getattr(study_outcomes, "sync_investigation", None)
        if sync_investigation is None:
            return
        wp = WorkspacePaths.load(Path(ws_root))
        owner = wp.study_owner(Path(study_dir).name)
        if not owner:
            return
        inv_dir = wp.investigations / owner
        if (inv_dir / "investigation.yaml").is_file():
            sync_investigation(inv_dir, Path(ws_root))
    except Exception as exc:  # never fail a successful run on a record error
        print(f"[study_outcomes] sync_investigation failed: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def decide_proposed_input(ws_root: Path, body: dict) -> "tuple[dict, int]":
    """POST /api/proposed-input-decision: accept or decline a proposed input.

    Resolves the ``proposed_inputs.items[]`` entry with ``id == item_id`` in
    ``investigations/<inv>/investigation.yaml`` and applies the expert decision:

      * ``accept``  → set ``status: accepted``. For ``kind: reference`` also
        append the citation to ``inputs.references``.
      * ``decline`` → set ``status: declined``.

    Persists with ruamel (round-trip), falling back to safe_dump.
    Returns (response_dict, status_code).
    """
    inv = body.get("investigation") or ""
    item_id = str(body.get("item_id") or "")
    decision = (body.get("decision") or "").strip().lower()

    if not inv:
        return {"error": "investigation name required"}, 400
    if not item_id:
        return {"error": "item_id required"}, 400
    if decision not in ("accept", "decline"):
        return {"error": "decision must be 'accept' or 'decline'"}, 400

    target = _investigation_yaml_path(ws_root, inv)
    if target is None:
        return {"error": f"no investigation.yaml for {inv!r}"}, 404

    new_status = "accepted" if decision == "accept" else "declined"
    result: dict = {}

    def _mutate(spec: dict) -> "tuple[Any, tuple | None]":
        block = spec.get("proposed_inputs")
        if not isinstance(block, dict):
            return None, ("proposed_inputs block missing", 404)
        items = block.get("items")
        if not isinstance(items, list):
            return None, ("proposed_inputs.items missing", 404)
        match = None
        for it in items:
            if isinstance(it, dict) and str(it.get("id")) == str(item_id):
                match = it
                break
        if match is None:
            return None, (f"no proposed input with id {item_id!r}", 404)
        match["status"] = new_status
        kind = match.get("kind") or "reference"
        result["kind"] = kind
        result["status"] = new_status
        # On accept, promote a reference into the real provided-references list.
        if decision == "accept" and kind == "reference":
            inputs = spec.get("inputs")
            if not isinstance(inputs, dict):
                inputs = {}
                spec["inputs"] = inputs
            refs = inputs.get("references")
            if not isinstance(refs, list):
                refs = []
                inputs["references"] = refs
            # Prefer a bib-key style id; fall back to the citation text.
            ref_value = match.get("id") or match.get("citation")
            if ref_value and ref_value not in refs:
                refs.append(ref_value)
                result["added_reference"] = ref_value
        return spec, None

    try:
        from ruamel.yaml import YAML as _RYAML

        _ry = _RYAML()
        _ry.preserve_quotes = True
        _ry.width = 4096
        spec = _ry.load(target.read_text(encoding="utf-8")) or {}
        mutated, err = _mutate(spec)
        if err is not None:
            return {"error": err[0]}, err[1]
        with target.open("w", encoding="utf-8") as _fh:
            _ry.dump(mutated, _fh)
    except ImportError:
        spec = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
        mutated, err = _mutate(spec)
        if err is not None:
            return {"error": err[0]}, err[1]
        target.write_text(yaml.safe_dump(mutated, sort_keys=False), encoding="utf-8")

    return {"ok": True, "item_id": item_id, **result}, 200


def study_seed_followup(ws_root: Path, body: dict) -> "tuple[dict, int]":
    """POST /api/study-seed-followup: seed a child study from a parent.

    Routes the four unified followup field families through one entry:

    - ``finding_id`` → delegates to the shared pbg-superpowers seed mechanism
      via ``seed_followup_study``; seeds STANDALONE from a finding.next_action.
    - ``followup_idx`` / ``proposal_id`` / ``proposal_idx`` → the existing
      legacy / discovery_implications paths.

    The pbg import is lazy + tolerant: if pbg-superpowers isn't installed the
    finding path returns a 500 with a clear message rather than crashing.
    """
    from vivarium_dashboard.lib.study_seed import seed_followup_study

    parent = body.get("parent")
    finding_id = body.get("finding_id")
    proposal_id = body.get("proposal_id")
    proposal_idx = body.get("proposal_idx")
    # Wave 3a #19 — optional study_type (e.g. 'diagnostic' when the parent
    # failed) threaded to the pbg writer so the seeded child is typed.
    study_type = body.get("study_type") or None
    if proposal_idx is not None:
        try:
            proposal_idx = int(proposal_idx)
        except (TypeError, ValueError):
            return {"error": "proposal_idx must be an integer"}, 400
    try:
        if finding_id is not None and str(finding_id) != "":
            # Finding family — delegate to the shared pbg seed mechanism.
            new_name = seed_followup_study(
                ws_root,
                parent,
                finding_id=finding_id,
                proposal_id=proposal_id,
                study_type=study_type,
            )
        else:
            new_name = seed_followup_study(
                ws_root,
                parent,
                int(body.get("followup_idx", -1)),
                proposal_id=proposal_id,
                proposal_idx=proposal_idx,
                study_type=study_type,
            )
    except ImportError as e:
        return {"error": f"finding-seed requires pbg-superpowers: {e}"}, 500
    except FileNotFoundError as e:
        return {"error": str(e)}, 404
    except (ValueError, KeyError, IndexError) as e:
        return {"error": str(e)}, 400
    except Exception as e:
        return {"error": f"seed failed: {e}"}, 500
    return {"new_study_name": new_name, "new_slug": new_name}, 200


def feedback_apply_action(ws_root: Path, body: dict) -> "tuple[dict, int]":
    """POST /api/feedback-apply-action: apply a tracked feedback action.

    SP3b: the dashboard NEVER computes the action — it renders the
    ``study_feedback_actions`` data + applies via this primitive (AI-free).
    Lazy + tolerant pbg import. Body: ``{item_id}``.
    """
    item_id = body.get("item_id")
    if not item_id:
        return {"error": "item_id required"}, 400
    try:
        from pbg_superpowers.feedback_actions import apply_feedback_action
    except ImportError as e:
        return {"error": f"feedback-apply requires pbg-superpowers: {e}"}, 500
    try:
        result = apply_feedback_action(ws_root, item_id)
    except FileNotFoundError as e:
        return {"error": str(e)}, 404
    except Exception as e:  # noqa: BLE001
        return {"error": f"apply failed: {e}"}, 500
    # apply_feedback_action is best-effort: a not-found / bad-target case comes
    # back as {"error": ...} without applied=True. Surface that as a 400.
    if result.get("error") and not result.get("applied"):
        return result, 400
    return result, 200


def study_rename(ws_root: Path, body: dict) -> "tuple[dict, int]":
    """POST /api/study-rename: rename a study directory and update study.yaml."""
    name = (body.get("study") or "").strip()
    new_name = (body.get("new_name") or "").strip()
    if not name or not new_name:
        return {"error": "missing study or new_name"}, 400
    if not _SLUG_RE.match(new_name):
        return {"error": "new_name must be lowercase + dashes"}, 400
    src = ws_root / "studies" / name
    dst = ws_root / "studies" / new_name
    if not src.is_dir():
        return {"error": "study not found"}, 404
    if dst.exists():
        return {"error": f"study {new_name!r} already exists"}, 409
    src.rename(dst)
    sf = dst / "study.yaml"
    spec = yaml.safe_load(sf.read_text(encoding="utf-8")) or {}
    spec["name"] = new_name
    sf.write_text(yaml.safe_dump(spec, sort_keys=False))
    return {"ok": True, "name": new_name}, 200


def study_create_from_run(ws_root: Path, body: dict) -> "tuple[dict, int]":
    """POST /api/study-create-from-run: create a new Study from a scratchpad run."""
    import datetime
    import json as _json
    import tempfile

    from vivarium_dashboard.lib.composite_runs import copy_run_to_new_db

    name = (body.get("name") or "").strip()
    objective = body.get("objective") or ""
    description = body.get("description") or ""
    source_run_id = (body.get("source_run_id") or "").strip()

    if not name or not source_run_id:
        return {"error": "missing name or source_run_id"}, 400
    if not _SLUG_RE.match(name):
        return {"error": "name must be lowercase + dashes"}, 400

    studies_root = Path(ws_root) / "studies"
    studies_root.mkdir(parents=True, exist_ok=True)
    dst = studies_root / name
    if dst.exists():
        return {"error": f"study {name!r} already exists"}, 409

    scratch = Path(ws_root) / ".pbg" / "composite-runs.db"
    if not scratch.is_file():
        return {"error": "no scratchpad DB"}, 404

    # Read the source run's metadata once to populate baseline.
    import sqlite3 as _sqlite3

    src = _sqlite3.connect(str(scratch))
    src.row_factory = _sqlite3.Row
    meta = src.execute(
        "SELECT spec_id, params_json, n_steps FROM runs_meta WHERE run_id = ?",
        (source_run_id,),
    ).fetchone()
    src.close()
    if meta is None:
        return {"error": "source_run_id not in scratchpad"}, 404

    spec_id = meta["spec_id"]
    try:
        params = _json.loads(meta["params_json"] or "{}")
    except (TypeError, ValueError):
        params = {}
    n_steps = int(meta["n_steps"] or 0)
    if n_steps and "n_steps" not in params:
        params["n_steps"] = n_steps

    # Build the study atomically: write to a temp dir inside studies_root,
    # then rename. Using studies_root as the temp parent ensures same filesystem.
    tmp_dir = tempfile.mkdtemp(dir=str(studies_root))
    tmp_path = Path(tmp_dir) / "build"
    try:
        tmp_path.mkdir()
        (tmp_path / "composites").mkdir()
        (tmp_path / "viz").mkdir()

        # Copy the run history into the new DB.
        copy_run_to_new_db(scratch, tmp_path / "runs.db", source_run_id)

        spec = {
            "schema_version": 3,
            "name": name,
            "created": datetime.date.today().isoformat(),
            "status": "ran",
            "objective": objective,
            "description": description,
            "baseline": {"composite": spec_id, "params": params},
            "variants": [],
            "runs": [
                {
                    "run_id": source_run_id,
                    "variant": None,
                    "label": "promoted from scratchpad",
                    "status": "completed",
                }
            ],
            "visualizations": [],
            "conclusion": None,
            "parent_studies": [],
        }
        (tmp_path / "study.yaml").write_text(yaml.safe_dump(spec, sort_keys=False))

        # Atomic rename: tmp/build → studies/<name>.
        tmp_path.rename(dst)
    except Exception:
        import shutil

        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    else:
        # Clean up the now-empty temp dir (build/ was renamed out).
        import shutil

        shutil.rmtree(tmp_dir, ignore_errors=True)

    return {"study": name, "url": f"/studies/{name}"}, 200


def study_sync_runs(ws_root: Path, body: dict) -> "tuple[dict, int]":
    """POST /api/study-sync-runs: reconcile a study's runs.db into study.yaml runs[].

    Body: ``{study: <slug>}``
    """
    from pbg_superpowers import study_outcomes
    from vivarium_dashboard.lib.workspace_paths import WorkspacePaths

    slug = (body or {}).get("study")
    if not slug:
        return {"error": "study slug required"}, 400
    try:
        study_dir = WorkspacePaths.load(Path(ws_root)).study_dir(slug)
    except FileNotFoundError:
        return {"error": f"study not found: {slug}"}, 404
    summary = study_outcomes.sync(study_dir)  # record runs + compute outcomes
    _sync_parent_investigation(ws_root, study_dir)  # SP1: roll up to investigation
    return {"ok": True, "summary": summary}, 200
