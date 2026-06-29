"""Unified run core: the shared prelude every dashboard run goes through —
generate the run_id and resolve WHERE it executes (local subprocess vs the
deployment). Callers keep their own launch + persistence policy; this owns the
id + the routing seam SP-D's deployment execution plugs into."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from vivarium_dashboard.lib import composite_runs


class RunTargetUnavailable(RuntimeError):
    """The resolved execution target can't run this here yet (e.g. a remote
    build needs deployment-side execution — SP-D)."""


def run_target_for(workspace: Path) -> str:
    """A materialized remote build (WS3's .viv-build.json stamp) runs on the
    deployment; a plain local workspace runs locally."""
    return "deployment" if (Path(workspace) / ".viv-build.json").is_file() else "local"


@dataclass
class RunPlan:
    run_id: str
    spec_id: str
    db_path: Path
    config: dict
    label: str | None
    n_steps: int | None
    target: str


def invoke_run(workspace, *, spec_id, config, db_path,
               label=None, n_steps=None, target=None) -> RunPlan:
    target = target or run_target_for(Path(workspace))
    if target == "deployment":
        raise RunTargetUnavailable(
            "this composite's source is a remote build — deployment-side "
            "execution is not available yet (SP-D). Run from a local workspace.")
    run_id = composite_runs.generate_run_id(spec_id, config)
    return RunPlan(run_id=run_id, spec_id=spec_id, db_path=Path(db_path),
                   config=dict(config or {}), label=label, n_steps=n_steps, target=target)
