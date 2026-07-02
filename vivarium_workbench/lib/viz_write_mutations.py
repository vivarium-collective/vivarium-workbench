"""Visualization file-write POST mutation builders.

Pure builders for the three visualization authoring write endpoints:

    (ws_root: Path, body: dict) -> tuple[dict, int]

File side-effects only — no HTTP, no server imports, no git operations.
These correspond to the simplest POST shape: plain extract→lib→route (Batch 23
of the FastAPI strangler-fig migration, POST phase).

Routes covered:
  - POST /api/visualization-create        → write .pbg/viz-requests/<name>.md
  - POST /api/visualization-add-to-project → copy viz-responses/<name>.py →
                                            visualizations-staged/<name>.py
  - POST /api/visualization-generate      → write new-contract viz-request file
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

import yaml

from vivarium_workbench.lib.workspace_paths import WorkspacePaths


# ---------------------------------------------------------------------------
# visualization_create
# ---------------------------------------------------------------------------


def visualization_create(ws_root: Path, body: dict[str, Any]) -> "tuple[dict, int]":
    """POST /api/visualization-create.

    Reads workspace.yaml to find the named visualization, checks it has a
    description, then writes a ``.pbg/viz-requests/<name>.md`` request file
    with workspace context.

    Returns:
      200  ``{ok, request_path, skill_command, instructions}``
      400  name invalid / empty description
      404  visualization not registered
    """
    name = (body.get("name") or "").strip()
    if not name or not re.match(r"^[a-zA-Z0-9_-]+$", name):
        return {"error": "invalid name"}, 400

    ws_data = yaml.safe_load((ws_root / "workspace.yaml").read_text(encoding="utf-8"))
    viz = next((v for v in (ws_data.get("visualizations") or []) if v.get("name") == name), None)
    if not viz:
        return {"error": f"visualization '{name}' not registered (Add it first)"}, 404

    description = viz.get("description") or ""
    if not description.strip():
        return {"error": "visualization has no description — edit it first"}, 400

    wp = WorkspacePaths.load(ws_root)
    req_dir = wp.pbg / "viz-requests"
    req_dir.mkdir(parents=True, exist_ok=True)
    req_path = req_dir / f"{name}.md"

    # Build context for the skill
    observables = ws_data.get("observables", []) or []
    simulations = ws_data.get("simulations", []) or []
    phases = ws_data.get("phases", []) or []
    pkg = ws_data.get("package_path") or ("pbg_" + ws_data.get("name", "").replace("-", "_"))

    obs_lines = "\n".join(
        f'  - `{o["name"]}` (path: `{o["store_path"]}`'
        + (f', units: {o["units"]}' if o.get("units") else "")
        + ")"
        for o in observables
    ) or "  (none)"
    sim_lines = "\n".join(
        f'  - `{s["name"]}`: t={s["t_start"]}→{s["t_end"]}'
        for s in simulations
    ) or "  (none)"
    phase_lines = "\n".join(
        f'  - {p["n"]}: {p["name"]} ({p.get("status","planned")})'
        for p in phases
    ) or "  (none)"

    content = f"""# Visualization request: {name}

## Description (from user)

{description}

## Workspace context

- Workspace package: `{pkg}`
- Available observables:
{obs_lines}
- Available simulations:
{sim_lines}
- Phases:
{phase_lines}

## Instructions for the agent

Write a Python function and save it to `.pbg/viz-responses/{name}.py`. The function:

- Should be named `visualize` (no name suffix — the file path identifies it)
- Takes one argument: `results: dict` — emitter output keyed by emitter path tuple, with values being lists of dicts `{{observable_name: value, ...}}`
- Returns: HTML string (Plotly preferred) OR a base64 PNG (matplotlib fallback)
- Must include a `_demo()` helper that returns the visualization run on synthetic data, so the dashboard preview can call it without real simulation results
- Should pick the visualization library that best fits the description (Plotly for interactive, matplotlib for static)

Output file structure:

```python
\"\"\"Generated visualization: {name}\"\"\"
import plotly.graph_objects as go  # or matplotlib.pyplot, etc.

def visualize(results: dict) -> str:
    # ... build figure from results ...
    return fig.to_html(full_html=False, include_plotlyjs='cdn')

def _demo() -> str:
    # Synthetic data matching the observable shape
    fake_results = {{('emitter',): [{{...}}, ...]}}
    return visualize(fake_results)

if __name__ == "__main__":
    import sys
    sys.stdout.write(_demo())
```
"""
    req_path.write_text(content, encoding="utf-8")

    return {
        "ok": True,
        "request_path": str(req_path.relative_to(ws_root)),
        "skill_command": f"/pbg-viz {name}",
        "instructions": (
            f"Open Claude Code in this workspace and run `/pbg-viz {name}`. "
            f"The skill will read {req_path.relative_to(ws_root)}, generate a function, "
            f"and save it to .pbg/viz-responses/{name}.py. "
            f"Click Refresh below when ready."
        ),
    }, 200


# ---------------------------------------------------------------------------
# visualization_add_to_project
# ---------------------------------------------------------------------------


def visualization_add_to_project(ws_root: Path, body: dict[str, Any]) -> "tuple[dict, int]":
    """POST /api/visualization-add-to-project.

    Copy ``.pbg/viz-responses/<name>.py`` to
    ``.pbg/visualizations-staged/<name>.py``.  Does NOT commit.

    Returns:
      200  ``{ok, staged_path}``
      400  name missing
      404  no skill response yet
    """
    name = (body.get("name") or "").strip()
    if not name:
        return {"error": "missing name"}, 400

    wp = WorkspacePaths.load(ws_root)
    src = wp.pbg / "viz-responses" / f"{name}.py"
    if not src.exists():
        return {"error": f"no skill response yet — run /pbg-viz {name} first"}, 404

    dest_dir = wp.pbg / "visualizations-staged"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{name}.py"
    shutil.copy2(src, dest)
    return {"ok": True, "staged_path": str(dest.relative_to(ws_root))}, 200


# ---------------------------------------------------------------------------
# visualization_generate
# ---------------------------------------------------------------------------


def visualization_generate(ws_root: Path, body: dict[str, Any]) -> "tuple[dict, int]":
    """POST /api/visualization-generate.

    Write a new-contract viz-request file at ``.pbg/viz-requests/<name>.md``.
    The /pbg-viz skill consumes the request and writes a decorated function
    to ``<workspace_pkg>/visualizations/<snake>.py``.

    Returns:
      200  ``{ok, request_path, target_file, skill_command, instructions}``
      400  name invalid or description missing
    """
    name = (body.get("name") or "").strip()
    if not name or not re.match(r"^[a-zA-Z0-9_-]+$", name):
        return {"error": "name must match ^[a-zA-Z0-9_-]+$"}, 400
    description = (body.get("description") or "").strip()
    if not description:
        return {"error": "description is required"}, 400

    snake = name.lower().replace("-", "_")
    ws_data = yaml.safe_load((ws_root / "workspace.yaml").read_text(encoding="utf-8")) or {}
    pkg = ws_data.get("package_path") or ("pbg_" + ws_data.get("name", "").replace("-", "_"))
    target = f"{pkg}/visualizations/{snake}.py"

    observables = ws_data.get("observables") or []
    simulations = ws_data.get("simulations") or []
    obs_lines = "\n".join(
        f'  - `{o.get("name")}` (path: `{o.get("store_path")}`'
        + (f', units: {o["units"]}' if o.get("units") else "")
        + ")"
        for o in observables if isinstance(o, dict)
    ) or "  (none)"
    sim_lines = "\n".join(
        f'  - `{s.get("name")}`: t={s.get("t_start")}->{s.get("t_end")}'
        for s in simulations if isinstance(s, dict)
    ) or "  (none)"

    body_md = (
        f"# Visualization request: {name}\n\n"
        f"## Description (from user)\n\n"
        f"{description}\n\n"
        f"## Workspace context\n\n"
        f"- Workspace package: `{pkg}`\n"
        f"- Available observables:\n{obs_lines}\n"
        f"- Available simulations:\n{sim_lines}\n\n"
        f"## Instructions for the agent\n\n"
        f"Write a single function decorated with `@as_visualization` and save it to "
        f"`{target}`.\n\n"
        f"Output file structure (the only thing this file should contain):\n\n"
        f"```python\n"
        f'"""<class-name> — one-line description.\n\n'
        f"Generated by /pbg-viz from request '{name}'.\n"
        f'"""\n'
        f"from __future__ import annotations\n"
        f"import html as _html, json\n"
        f"from pbg_superpowers.visualization import as_visualization\n\n\n"
        f"@as_visualization(\n"
        f"    inputs={{'<port>': '<bigraph-type>', ...}},  # typed input ports\n"
        f"    name='<ClassName>',\n"
        f"    demo={{...}},                                  # synthetic state for dashboard preview\n"
        f")\n"
        f"def update_{snake}(state):\n"
        f"    # ... build the Plotly figure from state ...\n"
        f"    return {{'html': '<...Plotly HTML...>'}}\n"
        f"```\n\n"
        f"Constraints:\n\n"
        f"- The function MUST be named `update_{snake}` (snake_case).\n"
        f"- `inputs` MUST use bigraph-schema type strings: `'list[float]'`, `'float'`, "
        f"`'list[list[float]]'`, `'string'`. For trajectory ports prefer `'list[float]'`.\n"
        f"- `demo` MUST be realistic synthetic state matching `inputs` so the dashboard "
        f"preview is meaningful.\n"
        f"- Do NOT define a class manually; the decorator synthesizes the Visualization "
        f"subclass.\n"
        f"- Do NOT edit `__init__.py` — `bigraph_schema.discover_packages()` walks the "
        f"package automatically.\n"
        f"- The file must be self-contained (only `pbg_superpowers`, `process_bigraph`, "
        f"`html`, `json`, and standard `plotly`/`matplotlib` imports allowed).\n"
    )

    wp = WorkspacePaths.load(ws_root)
    req_dir = wp.pbg / "viz-requests"
    req_dir.mkdir(parents=True, exist_ok=True)
    req_path = req_dir / f"{name}.md"
    req_path.write_text(body_md, encoding="utf-8")
    return {
        "ok": True,
        "request_path": str(req_path),
        "target_file": target,
        "skill_command": f"/pbg-viz {name}",
        "instructions": (
            "In your active Claude Code session, run `/pbg-viz "
            f"{name}`. The skill will read this request and write the "
            "decorated function to the target file. Click Accept here "
            "when it's done."
        ),
    }, 200
