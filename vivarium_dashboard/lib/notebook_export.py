"""Export an investigation as a self-contained Jupyter notebook + Python script.

The HTML reports (``report.py`` / ``single_study_report.py``) are the
biologist-facing view of an investigation. This module is the *coder*-facing
complement: it emits, for one investigation, a runnable ``.ipynb`` and a
matching ``.py`` that

  * re-run each study live, via the **same process-bigraph protocol** the
    workspace itself uses (``build_core`` → composite → run), and
  * **import from the repo the notebook was generated for** — the workspace's
    own runner and visualization renderer — so the artifact reproduces the
    study figures rather than re-deriving them, and
  * weave in the study narrative (question, acceptance tests, findings,
    verdict) as Markdown, mirroring the HTML report's content.

Reproduction recipe per study comes from two authoritative sources already on
disk (no AI, fully deterministic):

  * ``study.yaml``  — question/findings/tests/verdict + the ``visualizations``
    list (address + config) to render.
  * ``runs.db``     — ``runs_meta`` rows give the exact run recipe for each
    simulation: composite (``spec_id``), label (``sim_name``), ``n_steps`` and
    the ``params_json`` (e.g. ``interval``).

Run / render entry points are discovered by convention so the generator stays
workspace-agnostic:

  * run    — ``scripts/run_study_sims.py:run_study`` if present, else a generic
             ``build_composite_from_spec`` + ``Composite.run`` snippet.
  * render — ``scripts/render_study_viz.py:_render_one`` if present, else the
             framework ``refresh_viz`` path.

A workspace can override either via a ``notebook_export:`` block in
``workspace.yaml`` (keys ``run_import`` / ``render_import`` / ``setup``).

Public API
----------
``export_investigation_notebook(ws_root, inv_slug, *, out_dir=None) -> dict``
    Returns ``{"ipynb": Path, "py": Path}``.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Workspace + spec loading (light; stdlib + yaml only)
# ---------------------------------------------------------------------------


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text()) or {}


def _workspace_layout(ws_root: Path) -> tuple[dict, dict, str]:
    """Return (workspace_dict, layout_dict, package_path)."""
    ws = _load_yaml(ws_root / "workspace.yaml")
    layout = ws.get("layout") or {}
    package = ws.get("package_path") or ws.get("name") or ""
    return ws, layout, package


def _studies_dir(ws_root: Path, layout: dict) -> Path:
    return ws_root / (layout.get("studies") or "studies")


def _investigations_dir(ws_root: Path, layout: dict) -> Path:
    return ws_root / (layout.get("investigations") or "investigations")


def _reports_dir(ws_root: Path, layout: dict) -> Path:
    return ws_root / (layout.get("reports") or "reports")


def _load_investigation(ws_root: Path, layout: dict, slug: str) -> dict:
    # Accept the v2 `investigation.yaml` and the legacy `spec.yaml` filename.
    inv_dir = _investigations_dir(ws_root, layout) / slug
    for fname in ("investigation.yaml", "spec.yaml"):
        path = inv_dir / fname
        if path.is_file():
            return _load_yaml(path)
    raise FileNotFoundError(
        f"investigation not found: {inv_dir}/(investigation|spec).yaml"
    )


def _load_study(ws_root: Path, layout: dict, slug: str) -> dict | None:
    path = _studies_dir(ws_root, layout) / slug / "study.yaml"
    if not path.is_file():
        return None
    return _load_yaml(path)


def _run_recipes(runs_db: Path) -> list[dict]:
    """Read runs_meta → the per-simulation reproduction recipe.

    Each recipe: {sim, spec_id, n_steps, params:{...}, status}.
    """
    if not runs_db.is_file():
        return []
    out: list[dict] = []
    # Read-only. If a concurrent writer (e.g. a live notebook re-run) holds the
    # lock, fall back to an immutable read so generation never silently drops a
    # study's run recipe.
    conn = None
    for uri in (f"file:{runs_db}?mode=ro", f"file:{runs_db}?mode=ro&immutable=1"):
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=2.0)
            break
        except sqlite3.OperationalError:
            conn = None
    if conn is None:
        return []
    try:
        cur = conn.execute(
            "SELECT sim_name, spec_id, n_steps, params_json, status FROM runs_meta"
        )
        for sim, spec_id, n_steps, params_json, status in cur.fetchall():
            try:
                params = json.loads(params_json) if params_json else {}
            except (ValueError, TypeError):
                params = {}
            out.append(
                {
                    "sim": sim or spec_id,
                    "spec_id": spec_id,
                    "n_steps": int(n_steps or params.get("steps") or 0),
                    "params": params,
                    "status": status,
                }
            )
    except sqlite3.OperationalError:
        pass
    finally:
        conn.close()
    # Stable order: baseline-ish specs first, then by sim name.
    out.sort(key=lambda r: (r["spec_id"] != "epithelium_2d", str(r["sim"])))
    return out


# ---------------------------------------------------------------------------
# Run / render strategy discovery
# ---------------------------------------------------------------------------


def _discover_strategy(ws_root: Path, ws: dict, package: str) -> dict:
    """Decide how the generated notebook runs studies and renders viz.

    Convention, lowest precedence first:
      1. generic process-bigraph protocol (always available as fallback)
      2. workspace's bespoke scripts/ entry points (if present)
      3. explicit workspace.yaml ``notebook_export:`` overrides
    """
    override = ws.get("notebook_export") or {}
    has_runner = (ws_root / "scripts" / "run_study_sims.py").is_file()
    has_renderer = (ws_root / "scripts" / "render_study_viz.py").is_file()

    strat: dict[str, Any] = {
        "package": package,
        "setup": override.get("setup")
        or (f"from {package}.core import build_core\ncore = build_core()" if package else "core = None"),
        "run_kind": "scripts" if has_runner else "generic",
        "render_kind": "scripts" if has_renderer else "generic",
        "run_import": override.get("run_import")
        or ("from scripts.run_study_sims import run_study" if has_runner else ""),
        "render_import": override.get("render_import")
        or ("from scripts.render_study_viz import _render_one" if has_renderer else ""),
    }
    if override.get("run_kind"):
        strat["run_kind"] = override["run_kind"]
    if override.get("render_kind"):
        strat["render_kind"] = override["render_kind"]
    return strat


# ---------------------------------------------------------------------------
# Block model — one neutral representation, two serializers
# ---------------------------------------------------------------------------
# A block is a dict with "kind" in {"md", "code", "viz"}.
#   md:   {"kind":"md",   "text": str}
#   code: {"kind":"code", "src": str}
#   viz:  {"kind":"viz",  "study": slug, "name": str, "slug": str,
#          "address": str, "config": dict}


def _md(text: str) -> dict:
    return {"kind": "md", "text": text.rstrip() + "\n"}


def _code(src: str) -> dict:
    return {"kind": "code", "src": src.rstrip()}


def _viz_slug(name: str) -> str:
    import re

    s = re.sub(r"[^A-Za-z0-9._-]+", "_", (name or "").strip())
    return s.strip("_") or "viz"


# ---------------------------------------------------------------------------
# Markdown content from study.yaml (mirrors the HTML report sections)
# ---------------------------------------------------------------------------


def _truthy(v: Any) -> str:
    return "" if v is None else str(v).strip()


def _study_header_md(study: dict, slug: str) -> str:
    # Design intent only — question / objective / hypothesis. Result-bearing
    # fields (verdict, key_metrics, conclusion prose) are intentionally omitted:
    # this notebook states *parameters* and lets the coder produce the results.
    lines = [f"## Study: `{slug}`"]
    if study.get("question"):
        lines += ["", f"**Question.** {_truthy(study['question'])}"]
    if study.get("objective"):
        lines += ["", f"**Objective.** {_truthy(study['objective'])}"]
    if study.get("hypothesis"):
        lines += ["", f"**Hypothesis.** {_truthy(study['hypothesis'])}"]
    return "\n".join(lines)


def _acceptance_md(study: dict) -> str:
    # Acceptance *criteria* (pre-registered), not outcomes — no result column.
    tests = study.get("behavior_tests") or study.get("tests") or []
    if not tests:
        return ""
    lines = [
        "### Acceptance criteria",
        "",
        "_Pre-registered checks (criteria/thresholds only — run the cells above to evaluate them)._",
        "",
        "| test | measures | passes if |",
        "| --- | --- | --- |",
    ]
    for t in tests:
        if not isinstance(t, dict):
            continue
        measure = t.get("measure") or {}
        if isinstance(measure, dict):
            measure_s = " ".join(f"{k}={v}" for k, v in measure.items())
        else:
            measure_s = _truthy(measure)
        passif = t.get("pass_if") or {}
        if isinstance(passif, dict):
            passif_s = " ".join(f"{k} {v}" for k, v in passif.items())
        else:
            passif_s = _truthy(passif)
        lines.append(f"| {_truthy(t.get('name'))} | {measure_s} | {passif_s} |")
    return "\n".join(lines)


def _parameters_md(study: dict, recipes: list[dict], package: str) -> str:
    """Run configuration — parameters only (no results)."""
    lines = ["### Parameters"]
    if recipes:
        lines += [
            "",
            "| simulation | composite | steps | params |",
            "| --- | --- | --- | --- |",
        ]
        for r in recipes:
            params = {k: v for k, v in (r["params"] or {}).items() if k != "steps"}
            params_s = ", ".join(f"{k}={v}" for k, v in params.items()) or "—"
            lines.append(
                f"| `{r['sim']}` | `{r['spec_id']}` | {r['n_steps']} | {params_s} |"
            )
    variants = study.get("variants") or []
    declared = [v for v in variants if isinstance(v, dict) and v.get("params")]
    if declared:
        lines += ["", "Declared parameter sets (`study.yaml` variants):", ""]
        for v in declared:
            params_s = ", ".join(f"`{k}={val}`" for k, val in v["params"].items())
            lines.append(f"- **{_truthy(v.get('name'))}** — {params_s}")
    return "\n".join(lines)


_COMPOSITE_INTRO_MD = (
    "### Composite structure (process-bigraph)\n\n"
    "Each composite is a process-bigraph *document*: named processes "
    "(`_type: process`) bound to an `address`, wired by `inputs`/`outputs` "
    "ports over shared stores. The cell below loads the spec, lists its "
    "processes, configuration parameters and wiring, then realizes it as a "
    "live `Composite` and shows the resulting store tree."
)


def _composite_code(package: str, spec_id: str) -> str:
    comp_rel = f"{package}/composites/{spec_id}.composite.yaml"
    return f'''from pbg_superpowers.composite_spec import load_spec, build_composite_from_spec

spec = load_spec(REPO / {comp_rel!r})
print("composite:", spec.get("name"))
if spec.get("description"):
    print("description:", spec["description"])

print("\\nparameters (overridable):")
for _p, _pdef in (spec.get("parameters") or {{}}).items():
    print(f"  {{_p}}: default={{_pdef.get('default')!r}}  type={{_pdef.get('type')}}")

print("\\nprocesses  (node -> address):")
for _node, _body in (spec.get("state") or {{}}).items():
    if not (isinstance(_body, dict) and _body.get("_type") == "process"):
        continue
    print(f"  {{_node}}  ->  {{_body.get('address')}}")
    for _k, _v in (_body.get("config") or {{}}).items():
        _vs = _v if not isinstance(_v, (dict, list)) else f"<{{type(_v).__name__}}, {{len(_v)}} entries>"
        print(f"      config.{{_k}} = {{_vs}}")
    for _port in ("inputs", "outputs"):
        if _body.get(_port):
            print(f"      {{_port}} ports: {{_body[_port]}}")

# Realize the document as a live process-bigraph Composite.
with quiet():  # building loads the simulators, which can be chatty
    _comp = build_composite_from_spec(spec, core=core)
print("\\nrealized composite — top-level stores:", list(_comp.state))'''


# ---------------------------------------------------------------------------
# Build the block list for an investigation
# ---------------------------------------------------------------------------


def _setup_blocks(ws_root: Path, strat: dict) -> list[dict]:
    repo = str(ws_root)
    src = f'''"""Self-contained reproduction of this investigation.

Generated by vivarium-dashboard (notebook_export). Each study below is re-run
live with the workspace's own process-bigraph protocol and its figures are
rendered from the resulting runs.db.
"""
import os
import sys
from pathlib import Path

# The repository this notebook was generated for. Falls back to $VIVARIUM_REPO
# or the current directory, so a downloaded notebook still works when the repo
# is cloned at a different path than the one it was generated on.
REPO = Path(os.environ.get("VIVARIUM_REPO") or {repo!r})
if not REPO.is_dir():
    REPO = Path.cwd()
sys.path.insert(0, str(REPO))
# Composite specs use repo-root-relative paths (datasets, caches), and the
# workspace's runner/renderer assume cwd == repo root — so run from there.
os.chdir(REPO)

# Re-simulate from scratch? Set False to render the committed runs.db (fast).
RERUN = True

# --- standard process-bigraph protocol: register the workspace's Core ---
{strat["setup"]}

# --- imported from the repo this notebook was generated for ---'''
    imports = []
    if strat.get("run_import"):
        imports.append(strat["run_import"])
    if strat.get("render_import"):
        imports.append(strat["render_import"])
    if imports:
        src += "\n" + "\n".join(imports)
    src += "\n\nfrom IPython.display import HTML, display"
    src += (
        "\n\nimport contextlib as _contextlib, io as _io\n"
        "@_contextlib.contextmanager\n"
        "def quiet():\n"
        '    """Silence the simulator\'s verbose per-step stdout so the notebook\n'
        '    output stays readable (the figures below are the results)."""\n'
        "    with _contextlib.redirect_stdout(_io.StringIO()):\n"
        "        yield"
    )
    return [_code(src)]


def _study_blocks(ws_root: Path, layout: dict, slug: str, strat: dict) -> list[dict]:
    study = _load_study(ws_root, layout, slug)
    if study is None:
        return [_md(f"## Study: `{slug}`\n\n_study.yaml not found — skipped._")]

    studies_rel = layout.get("studies") or "studies"
    package = strat["package"]
    study_dir = _studies_dir(ws_root, layout) / slug
    recipes = _run_recipes(study_dir / "runs.db")

    blocks: list[dict] = [_md(_study_header_md(study, slug))]

    # --- parameters (no results) ---
    blocks.append(_md(_parameters_md(study, recipes, package)))

    # --- process-bigraph composite structure (one cell per unique composite) ---
    seen_specs: list[str] = []
    for r in recipes:
        if r["spec_id"] not in seen_specs:
            seen_specs.append(r["spec_id"])
    if seen_specs:
        blocks.append(_md(_COMPOSITE_INTRO_MD))
        for spec_id in seen_specs:
            blocks.append(_md(f"**Composite `{spec_id}`**"))
            blocks.append(_code(_composite_code(package, spec_id)))

    # --- per-study path constants + run cell ---
    run_lines = [
        f'# === Study: {slug} ===',
        f'STUDY = {slug!r}',
        f'STUDY_DIR = REPO / {studies_rel!r} / STUDY',
        'STUDY_YAML = str(STUDY_DIR / "study.yaml")',
        'RUNS_DB = str(STUDY_DIR / "runs.db")',
        '',
    ]
    if recipes and strat["run_kind"] == "scripts":
        run_lines.append("if RERUN:")
        run_lines.append("    with quiet():  # the sim prints per-step progress; keep it out of the notebook")
        for r in recipes:
            spec_id = r["spec_id"]
            sim = r["sim"]
            steps = r["n_steps"]
            interval = r["params"].get("interval", 0.1)
            comp_rel = f"{package}/composites/{spec_id}.composite.yaml"
            run_lines.append(
                f"        # sim {sim!r}: composite {spec_id!r}, {steps} steps, interval {interval}"
            )
            run_lines.append(
                f"        run_study(STUDY, {sim!r}, str(REPO / {comp_rel!r}), {steps}, {interval})"
            )
        run_lines.append(f"    print(f'ran {len(recipes)} simulation(s) -> {{RUNS_DB}}')")
        run_lines.append("else:")
        run_lines.append('    print("RERUN=False — rendering committed", RUNS_DB)')
    elif recipes and strat["run_kind"] == "generic":
        run_lines.append("# Generic process-bigraph protocol (no workspace runner detected):")
        run_lines.append("from pbg_superpowers.composite_spec import load_spec, build_composite_from_spec")
        run_lines.append("if RERUN:")
        run_lines.append("    with quiet():  # the sim prints per-step progress; keep it out of the notebook")
        for r in recipes:
            spec_id = r["spec_id"]
            steps = r["n_steps"]
            params = r["params"]
            comp_rel = f"{package}/composites/{spec_id}.composite.yaml"
            overrides = {k: v for k, v in params.items() if k != "steps"}
            run_lines.append(
                f"        spec = load_spec(REPO / {comp_rel!r})"
            )
            run_lines.append(
                f"        comp = build_composite_from_spec(spec, {overrides!r}, core=core)"
            )
            run_lines.append(f"        comp.run({steps})  # writes the composite's declared emitter")
        run_lines.append(f"    print(f'ran {len(recipes)} simulation(s) -> {{RUNS_DB}}')")
        run_lines.append("else:")
        run_lines.append('    print("RERUN=False — rendering committed", RUNS_DB)')
    else:
        run_lines.append('print("No recorded runs for this study; nothing to reproduce.")')

    blocks.append(_code("\n".join(run_lines)))

    # --- per-visualization render blocks ---
    # The figures ARE the results — text stays parameter-only, so the authored
    # (result-bearing) captions are not echoed into Markdown; just the name.
    vizzes = study.get("visualizations") or []
    if vizzes:
        blocks.append(_md("### Visualizations\n\n_Results are shown by the figures below, produced by the run above._"))
    for v in vizzes:
        if not isinstance(v, dict) or not v.get("name"):
            continue
        name = _truthy(v.get("name"))
        config = v.get("config") or {}
        blocks.append(_md(f"**{name}**"))
        blocks.append(
            {
                "kind": "viz",
                "study": slug,
                "name": name,
                "slug": _viz_slug(name),
                "address": _truthy(v.get("address")),
                "config": config,
                "render_kind": strat["render_kind"],
            }
        )

    # --- acceptance criteria (pre-registered; no outcomes) ---
    acc = _acceptance_md(study)
    if acc:
        blocks.append(_md(acc))

    return blocks


def _intro_blocks(inv: dict, slug: str) -> list[dict]:
    title = _truthy(inv.get("title")) or slug
    lines = [f"# {title}", "", f"_Investigation `{slug}` — coder reproduction notebook._"]
    if inv.get("question"):
        lines += ["", f"**Question.** {_truthy(inv['question'])}"]
    execu = inv.get("executive") or {}
    if execu.get("what_is_this"):
        lines += ["", _truthy(execu["what_is_this"])]
    lines += [
        "",
        "---",
        "",
        "This notebook re-runs each study with the workspace's own process-bigraph "
        "protocol and renders its figures. The text states the **question and "
        "parameters** only — the figures produced by each run are the results. "
        "Set `RERUN = False` in the setup cell to render the committed `runs.db` "
        "without re-simulating.",
    ]
    return [_md("\n".join(lines))]


def _outro_blocks(inv: dict) -> list[dict]:
    execu = inv.get("executive") or {}
    decisions = execu.get("decisions_needed") or []
    if not decisions:
        return []
    lines = ["## Open decisions"]
    for d in decisions:
        if isinstance(d, dict):
            lines.append(f"- {_truthy(d.get('question') or d.get('context'))}")
        else:
            lines.append(f"- {_truthy(d)}")
    return [_md("\n".join(lines))]


def _build_blocks(ws_root: Path, layout: dict, inv: dict, slug: str, strat: dict) -> list[dict]:
    blocks = _intro_blocks(inv, slug)
    blocks += _setup_blocks(ws_root, strat)
    for study_slug in inv.get("studies") or []:
        blocks += _study_blocks(ws_root, layout, study_slug, strat)
    blocks += _outro_blocks(inv)
    return blocks


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------


def _viz_code_for_notebook(b: dict) -> str:
    return (
        f"# {b['name']}\n"
        f"display(HTML(_render_one({b['address']!r}, {b['config']!r}, RUNS_DB, STUDY_YAML)))"
    )


def _ipynb(blocks: list[dict]) -> dict:
    cells = []
    for b in blocks:
        if b["kind"] == "md":
            cells.append(
                {
                    "cell_type": "markdown",
                    "metadata": {},
                    "source": _split_keepends(b["text"]),
                }
            )
        else:
            if b["kind"] == "viz":
                src = _viz_code_for_notebook(b)
            else:
                src = b["src"]
            cells.append(
                {
                    "cell_type": "code",
                    "execution_count": None,
                    "metadata": {},
                    "outputs": [],
                    "source": _split_keepends(src),
                }
            )
    return {
        "cells": cells,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def _split_keepends(text: str) -> list[str]:
    """nbformat 'source' is a list of lines, each (except last) ending in \\n."""
    if text == "":
        return []
    return text.splitlines(keepends=True)


_PY_HEADER = '''#!/usr/bin/env python
"""Self-contained reproduction script — generated by vivarium-dashboard.

Run from anywhere with the workspace's virtualenv, e.g.:
    .venv/bin/python {script_name}

Figures are written to:  {fig_dir}
Set RERUN = False (below) to render the committed runs.db without re-simulating.
"""
import os as _os
import sys as _sys

# YAML / study text is UTF-8; force UTF-8 mode so file reads don't depend on the
# shell locale (a bare-CLI run under an ASCII locale otherwise crashes on non-ASCII).
if _os.environ.get("PYTHONUTF8") != "1":
    _os.environ["PYTHONUTF8"] = "1"
    _os.execv(_sys.executable, [_sys.executable, *_sys.argv])
'''


def _py(blocks: list[dict], fig_dir_rel: str, script_name: str) -> str:
    out: list[str] = [_PY_HEADER.format(fig_dir=fig_dir_rel, script_name=script_name)]
    # Helper for saving viz HTML (no inline display in a plain script).
    helper = (
        "def _save_viz(study, slug, html):\n"
        f"    d = REPO / {fig_dir_rel!r} / study\n"
        "    d.mkdir(parents=True, exist_ok=True)\n"
        "    out = d / (slug + '.html')\n"
        "    out.write_text(html, encoding='utf-8')\n"
        "    print('  wrote', out)\n"
    )
    helper_emitted = False
    for b in blocks:
        if b["kind"] == "md":
            out.append(_as_comment(b["text"]))
        elif b["kind"] == "viz":
            if not helper_emitted:
                out.append(helper)
                helper_emitted = True
            out.append(
                f"# {b['name']}\n"
                f"_save_viz({b['study']!r}, {b['slug']!r}, "
                f"_render_one({b['address']!r}, {b['config']!r}, RUNS_DB, STUDY_YAML))"
            )
        else:
            src = b["src"]
            # In a plain script there's no IPython display; drop that import line.
            src = "\n".join(
                ln for ln in src.splitlines() if "from IPython.display" not in ln
            )
            out.append(src)
    return "\n\n".join(x for x in out if x.strip()) + "\n"


def _as_comment(md_text: str) -> str:
    lines = md_text.rstrip().splitlines() or [""]
    return "\n".join(f"# {ln}" if ln else "#" for ln in lines)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def export_investigation_notebook(
    ws_root: Path | str, inv_slug: str, *, out_dir: Path | str | None = None
) -> dict:
    """Generate ``<inv_slug>.ipynb`` + ``<inv_slug>.py`` for one investigation.

    Returns ``{"ipynb": Path, "py": Path}``.
    """
    ws_root = Path(ws_root).resolve()
    ws, layout, package = _workspace_layout(ws_root)
    inv = _load_investigation(ws_root, layout, inv_slug)
    strat = _discover_strategy(ws_root, ws, package)

    if out_dir is None:
        out_dir = _reports_dir(ws_root, layout) / "notebooks"
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    blocks = _build_blocks(ws_root, layout, inv, inv_slug, strat)

    ipynb_path = out_dir / f"{inv_slug}.ipynb"
    py_path = out_dir / f"{inv_slug}.py"

    ipynb_path.write_text(json.dumps(_ipynb(blocks), indent=1) + "\n", encoding="utf-8")

    try:
        fig_dir_rel = str((out_dir / "figures").relative_to(ws_root))
    except ValueError:
        fig_dir_rel = "reports/notebooks/figures"
    py_path.write_text(_py(blocks, fig_dir_rel, py_path.name), encoding="utf-8")

    return {"ipynb": ipynb_path, "py": py_path}


def _main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("ws_root", help="workspace root (dir containing workspace.yaml)")
    ap.add_argument("investigation", help="investigation slug")
    ap.add_argument("--out-dir", default=None, help="output dir (default: <reports>/notebooks)")
    args = ap.parse_args(argv)

    paths = export_investigation_notebook(args.ws_root, args.investigation, out_dir=args.out_dir)
    print("wrote", paths["ipynb"])
    print("wrote", paths["py"])
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
