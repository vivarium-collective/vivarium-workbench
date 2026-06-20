"""Server-side data prep for the Analyses Data Explorer card.

Thin, pure-Python functions over a workspace's simulation runs. Reuses the
existing run-discovery and trace-extraction readers; adds no new deps.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from vivarium_dashboard.lib import simulations_index
from vivarium_dashboard.lib import comparative_viz


def list_runs(workspace: Path) -> list[dict]:
    """Runs for the explorer's run-picker, projected to a small public shape."""
    out = []
    for r in simulations_index.list_simulations(Path(workspace)):
        studies = r.get("studies") or []
        out.append({
            "run_id": r.get("run_id"),
            "label": r.get("label") or r.get("sim_name") or r.get("run_id"),
            "study": r.get("study_slug") or (studies[0] if studies else None),
            "investigation": r.get("investigation_slug"),
            "n_steps": r.get("n_steps"),
            "status": r.get("status"),
            "db_path": r.get("db_path"),
            "source": r.get("source"),
        })
    return out
