"""Investigation-as-plan: an ordered chain of Studies forming a research plan.

On-disk: ``investigations/<slug>/investigation.yaml`` (schema_version: 1).
API prefix: ``/api/plan*`` (the name "Investigation" is overloaded with the
legacy Study endpoints; the new endpoints use "plan" to disambiguate).
"""
from __future__ import annotations
import os, sqlite3
from pathlib import Path
import yaml


class InvestigationPlanError(ValueError):
    """Raised on structural problems in investigation.yaml."""


_VALID_GATES = {None, "tests-pass"}
_VALID_STATUS_OVERRIDES = {None, "planned", "in-progress", "blocked", "complete"}


def load_plan(path: Path) -> dict:
    """Parse + validate investigations/<slug>/investigation.yaml."""
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        raise InvestigationPlanError(f"malformed YAML: {e}") from e
    _validate_plan(data)
    return data


def save_plan(path: Path, data: dict) -> None:
    _validate_plan(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(data, sort_keys=False))
    os.replace(tmp, path)


def _validate_plan(data: dict) -> None:
    if not isinstance(data, dict):
        raise InvestigationPlanError("plan must be a YAML mapping")
    if data.get("schema_version") != 1:
        raise InvestigationPlanError("schema_version must be 1")
    if not data.get("name"):
        raise InvestigationPlanError("missing required field: name")
    studies = data.get("studies", [])
    if not isinstance(studies, list):
        raise InvestigationPlanError("studies must be a list")
    seen = set()
    for i, entry in enumerate(studies):
        if not isinstance(entry, dict) or not entry.get("study"):
            raise InvestigationPlanError(f"studies[{i}] must be a mapping with a 'study' key")
        slug = entry["study"]
        if slug in seen:
            raise InvestigationPlanError(f"studies[{i}] duplicate study slug: {slug}")
        seen.add(slug)
        gate = entry.get("gate")
        if gate not in _VALID_GATES:
            raise InvestigationPlanError(f"studies[{i}].gate must be one of {_VALID_GATES}, got {gate!r}")
        ov = entry.get("status_override")
        if ov not in _VALID_STATUS_OVERRIDES:
            raise InvestigationPlanError(
                f"studies[{i}].status_override must be one of {_VALID_STATUS_OVERRIDES}, got {ov!r}"
            )
    refs = data.get("references", [])
    if not isinstance(refs, list):
        raise InvestigationPlanError("references must be a list")


def derive_study_status(workspace: Path, slug: str, *, prev_satisfied_gate: bool) -> str:
    """Compute the live status of a study in an investigation plan.

    Inputs:
    - workspace: workspace root.
    - slug: study slug under workspace/studies/.
    - prev_satisfied_gate: whether the previous gate-required study has satisfied
      its gate (or there was no previous gate). False => this entry is blocked.

    Returns one of: ``planned`` | ``in-progress`` | ``blocked`` | ``complete``.
    """
    if not prev_satisfied_gate:
        return "blocked"
    spec_path = workspace / "studies" / slug / "study.yaml"
    if not spec_path.exists():
        return "blocked"  # missing study
    spec = yaml.safe_load(spec_path.read_text()) or {}
    lr = (spec.get("tests") or {}).get("last_results") or None
    runs_db = workspace / "studies" / slug / "runs.db"
    has_run = runs_db.exists() and _runs_count(runs_db) > 0

    tests_pass = bool(lr) and lr.get("failed", 0) == 0 and (lr.get("passed", 0) > 0)
    if tests_pass and has_run:
        return "complete"
    if has_run or (lr is not None):
        return "in-progress"
    return "planned"


def _runs_count(db: Path) -> int:
    try:
        conn = sqlite3.connect(db)
        try:
            return conn.execute("SELECT COUNT(*) FROM runs_meta").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        return 0


def gate_satisfied(workspace: Path, entry: dict) -> bool:
    """Whether this entry's gate is satisfied. For ``gate: tests-pass``,
    requires complete status; for no gate, always True."""
    if entry.get("gate") != "tests-pass":
        return True
    status = derive_study_status(workspace, entry["study"], prev_satisfied_gate=True)
    return status == "complete"


def list_plans(workspace: Path) -> list[dict]:
    """Return a summary list of all investigations/<slug>/investigation.yaml."""
    inv_dir = Path(workspace) / "investigations"
    if not inv_dir.is_dir():
        return []
    out = []
    for slug_dir in sorted(inv_dir.iterdir()):
        plan_path = slug_dir / "investigation.yaml"
        if not plan_path.exists():
            continue
        try:
            plan = load_plan(plan_path)
        except InvestigationPlanError:
            continue
        out.append({
            "slug": slug_dir.name,
            "name": plan.get("name", slug_dir.name),
            "objective": plan.get("objective", ""),
            "status": plan.get("status", "planned"),
            "n_studies": len(plan.get("studies", [])),
        })
    return out


def get_plan_detail(workspace: Path, slug: str) -> dict | None:
    """Return a plan with per-study derived status and gate-satisfaction info."""
    plan_path = Path(workspace) / "investigations" / slug / "investigation.yaml"
    if not plan_path.exists():
        return None
    plan = load_plan(plan_path)

    prev_satisfied = True
    enriched_studies = []
    for entry in plan.get("studies", []):
        if entry.get("status_override"):
            status = entry["status_override"]
        else:
            status = derive_study_status(workspace, entry["study"], prev_satisfied_gate=prev_satisfied)
        enriched = dict(entry)
        enriched["derived_status"] = status
        enriched_studies.append(enriched)
        if entry.get("gate") == "tests-pass":
            prev_satisfied = (status == "complete")

    plan["studies"] = enriched_studies
    return plan
