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

from vivarium_workbench.lib.investigation_members import investigation_member_slugs

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


def _baseline_recipes(study: dict) -> list[dict]:
    """Reproduction recipes synthesized from ``study.yaml`` ``baseline[]`` — the
    fallback when a study has no ``runs.db`` (figure / reference studies). Same
    shape as :func:`_run_recipes`, so the parameters table + editable-composite
    cells render from the study's declared composite + parameters instead of
    being empty.
    """
    baseline = study.get("baseline")
    if not baseline:
        cond = study.get("conditions")
        if isinstance(cond, dict):
            baseline = cond.get("baseline")
    if isinstance(baseline, dict):
        baseline = [baseline]
    if not isinstance(baseline, list):
        return []
    out: list[dict] = []
    for b in baseline:
        if not isinstance(b, dict):
            continue
        spec_id = b.get("composite") or b.get("base_model") or b.get("spec_id")
        if not spec_id:
            continue
        params = b.get("params") if isinstance(b.get("params"), dict) else {}
        out.append(
            {
                "sim": b.get("name") or str(spec_id).rsplit(".", 1)[-1],
                "spec_id": str(spec_id),
                "n_steps": int(params.get("n_steps") or params.get("steps") or 0),
                "params": params,
                "status": "baseline (study.yaml)",
            }
        )
    return out


def _resolve_composite_path(composites_dir: Path, spec_id: str):
    """Locate a composite spec file for ``spec_id`` (a dotted id such as
    ``pkg.composites.sub.name`` OR a bare ``name``). Searches ``composites/``
    for ``<stem>.composite.{json,yaml,yml}``, flat first then recursively, so it
    works for both the ``.composite.json`` figure specs and nested layouts."""
    if not composites_dir.is_dir():
        return None
    stem = str(spec_id).rsplit(".", 1)[-1]
    for ext in ("json", "yaml", "yml"):
        flat = composites_dir / f"{stem}.composite.{ext}"
        if flat.is_file():
            return flat
        matches = sorted(composites_dir.rglob(f"{stem}.composite.{ext}"))
        if matches:
            return matches[0]
    return None


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


def _code(src: str, collapsed: bool = False) -> dict:
    # ``collapsed`` marks a cell as scaffolding: its input is folded away in
    # JupyterLab (metadata.jupyter.source_hidden) so the science stays in front,
    # while the cell still runs on "Run All" and keeps a hide-input tag for
    # nbconvert-based exports.
    return {"kind": "code", "src": src.rstrip(), "collapsed": bool(collapsed)}


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
    # Design intent only — question / objective / hypothesis / purpose / claim.
    # Result-bearing fields (verdict, key_metrics, conclusion prose) are
    # intentionally omitted: this notebook states *parameters* and lets the
    # coder produce the results.
    title = _truthy(study.get("title"))
    lines = [f"## Study: {title} (`{slug}`)" if title else f"## Study: `{slug}`"]
    if study.get("question"):
        lines += ["", f"**Question.** {_truthy(study['question'])}"]
    if study.get("objective"):
        lines += ["", f"**Objective.** {_truthy(study['objective'])}"]
    if study.get("hypothesis"):
        lines += ["", f"**Hypothesis.** {_truthy(study['hypothesis'])}"]
    # v2 study schema: `purpose` (str or {mechanism,...}) and one-line `claim`.
    purpose = study.get("purpose")
    if isinstance(purpose, dict):
        purpose = purpose.get("mechanism") or purpose.get("summary") or purpose.get("text")
    if _truthy(purpose):
        lines += ["", f"**Purpose.** {_truthy(purpose)}"]
    if study.get("claim"):
        lines += ["", f"**Claim.** {_truthy(study['claim'])}"]
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
        name = _truthy(t.get("name"))
        cls = _truthy(t.get("classification"))
        if cls:
            name = f"{name} _({cls})_"
        lines.append(f"| {name} | {measure_s} | {passif_s} |")
    return "\n".join(lines)


def _mechanisms_md(study: dict) -> str:
    """Proposed mechanisms + models/methods (design intent), from study.yaml."""
    mechs = study.get("proposed_mechanisms") or []
    methods = study.get("proposed_models_methods") or []
    if not mechs and not methods:
        return ""
    lines: list[str] = ["### Design"]
    if mechs:
        lines += ["", "**Proposed mechanisms**", ""]
        for m in mechs:
            lines.append(f"- {_truthy(m)}")
    if methods:
        lines += ["", "**Proposed models / methods**", ""]
        for m in methods:
            lines.append(f"- {_truthy(m)}")
    assumptions = study.get("key_assumptions") or []
    if assumptions:
        lines += ["", "**Key assumptions**", ""]
        for a in assumptions:
            lines.append(f"- {_truthy(a)}")
    return "\n".join(lines)


def _contract_md(study: dict) -> str:
    """Render the study's declared typed contract (``contract.face`` ports +
    ``contract.conditions`` predicates) — the design-time interface a built
    composite must satisfy. Pure markdown from study.yaml; present even when no
    composite has been built yet."""
    contract = study.get("contract")
    if not isinstance(contract, dict):
        return ""
    face = contract.get("face") if isinstance(contract.get("face"), dict) else {}
    conditions = contract.get("conditions") or []
    if not face and not conditions:
        return ""
    lines: list[str] = [
        "### Typed contract (declared interface)",
        "",
        "_The bigraph-schema typed ports + conservation predicates this study's "
        "composite must satisfy (audited in the workbench §Contract panel). "
        "Declared design — independent of whether a run exists yet._",
    ]

    def _port_rows(ports: dict) -> list[str]:
        rows = ["", "| port | type | units | bounds |", "| --- | --- | --- | --- |"]
        for pname, pdef in ports.items():
            if not isinstance(pdef, dict):
                rows.append(f"| `{pname}` | {_truthy(pdef)} | | |")
                continue
            typ = _truthy(pdef.get("_type"))
            units = _truthy(pdef.get("_units"))
            lo, hi = pdef.get("_min"), pdef.get("_max")
            if lo is not None and hi is not None:
                bounds = f"[{lo}, {hi}]"
            elif lo is not None:
                bounds = f">= {lo}"
            elif hi is not None:
                bounds = f"<= {hi}"
            else:
                bounds = ""
            rows.append(f"| `{pname}` | {typ} | {units} | {bounds} |")
        return rows

    inputs = face.get("inputs") if isinstance(face.get("inputs"), dict) else {}
    outputs = face.get("outputs") if isinstance(face.get("outputs"), dict) else {}
    if inputs:
        lines += ["", "**Inputs**"] + _port_rows(inputs)
    if outputs:
        lines += ["", "**Outputs**"] + _port_rows(outputs)
    if conditions:
        lines += ["", "**Conditions**", ""]
        for c in conditions:
            if isinstance(c, dict):
                name = _truthy(c.get("name"))
                kind = _truthy(c.get("kind"))
                expr = _truthy(c.get("expr") or c.get("detail"))
                tag = f"`{name}`" + (f" ({kind})" if kind else "")
                lines.append(f"- {tag}: `{expr}`" if expr else f"- {tag}")
            else:
                lines.append(f"- {_truthy(c)}")
    return "\n".join(lines)


def _literature_md(study: dict) -> str:
    """Literature anchors — the published values the study is measured against."""
    anchors = study.get("literature_anchors") or []
    if not anchors:
        return ""
    lines = [
        "### Literature anchors",
        "",
        "| expectation | model observable | source | status |",
        "| --- | --- | --- | --- |",
    ]
    for a in anchors:
        if not isinstance(a, dict):
            lines.append(f"| {_truthy(a)} | | | |")
            continue
        lines.append(
            f"| {_truthy(a.get('expectation'))} | {_truthy(a.get('model_observable'))} "
            f"| {_truthy(a.get('source'))} | {_truthy(a.get('status_in_workspace'))} |"
        )
    return "\n".join(lines)


def _frameworks_md(inv: dict) -> str:
    """Competing frameworks / scenarios from the investigation (context the
    study is evaluated within)."""
    if not isinstance(inv, dict):
        return ""
    frameworks = inv.get("competing_frameworks") or inv.get("frameworks") or []
    if not frameworks:
        return ""
    lines = ["**Competing frameworks**", ""]
    for f in frameworks:
        if isinstance(f, dict):
            name = _truthy(f.get("name") or f.get("label"))
            role = _truthy(f.get("role") or f.get("stance") or f.get("kind"))
            lines.append(f"- **{name}**" + (f" — {role}" if role else ""))
        else:
            lines.append(f"- {_truthy(f)}")
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
    "### The model & its parameters\n\n"
    "Each composite is a process-bigraph *document* — named processes wired by "
    "`inputs`/`outputs` ports over shared stores. Below, each composite shows "
    "what it does, then a **parameter cell** with its tunable knobs (the current "
    "default and what each one means). Edit any value, then re-run the **Run** "
    "section to rebuild and re-simulate with your changes."
)


def _py_ident(name: Any) -> str:
    """A safe Python identifier fragment from a spec/sim name."""
    import re

    s = re.sub(r"\W+", "_", str(name or "")).strip("_")
    if not s or s[0].isdigit():
        s = "x_" + s
    return s or "x"


def _spec_var(spec_id: str) -> str:
    return f"spec_{_py_ident(spec_id)}"


def _ov_var(spec_id: str) -> str:
    return f"ov_{_py_ident(spec_id)}"


def _generator_inspect_code(spec_id: str, var: str) -> str:
    """Materialize a @composite_generator into an editable payload + print it."""
    return (
        f"{var} = materialize_composite({spec_id!r})\n"
        f"describe_spec({var})"
    )


def _generator_panel_code(var: str, ov: str) -> str:
    """Runtime control panel for a generator composite: its tunable parameters
    as an editable dict (no on-disk spec file to read at export time)."""
    return (
        f"# === Edit parameters for this composite, then run the Run cell ===\n"
        f"# {ov} maps each tunable parameter to its current value (the generator's\n"
        f"# defaults). Edit any entry; the Run cell rebuilds the composite with it.\n"
        f"{ov} = dict({var}['_overrides'])\n"
        f"import json as _json; print(_json.dumps({ov}, indent=2, default=str))\n"
        f"# e.g.:  {ov}['<parameter>'] = <value>"
    )


def _introspect_generator(ws_root: Path, spec_id: str) -> dict | None:
    """Best-effort, export-time introspection of a ``@composite_generator``.

    Returns the composite-level ``description`` and its per-knob ``parameters``
    (each ``{type, default, description}``) so the notebook can surface the
    science and a guided, editable parameter cell with each knob's meaning +
    units. Returns ``None`` if the workspace package can't be imported here — the
    notebook then falls back to runtime materialization (``_generator_panel_code``).
    """
    import importlib
    import sys
    import warnings

    try:
        ws_s = str(ws_root)
        if ws_s not in sys.path:
            sys.path.insert(0, ws_s)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from process_bigraph.composite_generator import (  # type: ignore
                _REGISTRY, discover_generators)
            try:
                importlib.import_module(spec_id.rsplit(".", 1)[0])
            except Exception:
                pass
            try:
                discover_generators()
            except Exception:
                pass
            entry = _REGISTRY.get(spec_id)
        if entry is None:
            return None
        return {
            "description": str(getattr(entry, "description", "") or "").strip(),
            "parameters": dict(getattr(entry, "parameters", {}) or {}),
        }
    except Exception:
        return None


def _generator_param_cell(ov: str, params: dict) -> str:
    """A guided, editable override dict — one knob per line with its description
    (which carries units + the control semantics). Generator declaration order
    is preserved, so the headline/arm knob leads."""
    header = [
        "# === Parameters — edit any value, then re-run from the Run cell below ===",
        "# Each entry is the composite's current default; the comment explains it.",
    ]
    keys = list(params.keys())
    if not keys:
        return "\n".join(header + [f"{ov} = {{}}"])
    kw = max(len(repr(k)) for k in keys)
    body = [f"{ov} = {{"]
    for name, pdef in params.items():
        default = pdef.get("default") if isinstance(pdef, dict) else pdef
        desc = (pdef.get("description") if isinstance(pdef, dict) else "") or ""
        line = f"    {repr(name):<{kw}}: {default!r},"
        if desc:
            line += f"   # {desc}"
        body.append(line)
    body.append("}")
    return "\n".join(header + body)


def _load_inspect_code(comp_rel: str, var: str) -> str:
    return (
        "from viva_superpowers.composite_spec import load_spec\n"
        f"{var} = load_spec(REPO / {comp_rel!r})\n"
        f"describe_spec({var})"
    )


def _is_scalar(v: Any) -> bool:
    return isinstance(v, (str, int, float, bool)) or v is None


def _leaf_assignments(prefix: str, value: Any, out: list[str]) -> None:
    """Emit `<prefix> = <repr>` lines for every scalar leaf under ``value``.

    Scalar-only lists are emitted whole; nested dicts/lists recurse so the
    control panel reaches every editable knob (mirrors the cylindermodel
    notebook, which sets each parameter explicitly)."""
    if isinstance(value, dict):
        for k, v in value.items():
            _leaf_assignments(f"{prefix}[{k!r}]", v, out)
    elif isinstance(value, list):
        if all(_is_scalar(x) for x in value):
            out.append(f"{prefix} = {value!r}")
        else:
            for i, v in enumerate(value):
                _leaf_assignments(f"{prefix}[{i}]", v, out)
    else:
        out.append(f"{prefix} = {value!r}")


def _control_panel_code(var: str, spec: dict) -> str:
    """A flat, fully editable assignment list for one composite's parameters."""
    lines = [
        f"# === Edit parameters for composite {spec.get('name')!r} ===",
        "# Each line is the spec's CURRENT value — change any, then run the Run cell",
        "# below. The spec is a plain dict, so you may also add or remove keys.",
        "",
    ]
    params = spec.get("parameters") or {}
    if params:
        lines.append("# tunable parameters (filled into ${name} placeholders):")
        for p, pdef in params.items():
            if isinstance(pdef, dict) and "default" in pdef:
                lines.append(f"{var}['parameters'][{p!r}]['default'] = {pdef.get('default')!r}")
        lines.append("")
    for node, body in (spec.get("state") or {}).items():
        if not (isinstance(body, dict) and body.get("_type") == "process"):
            continue
        lines.append(f"# process {node!r}  ({body.get('address')})")
        iv = body.get("interval")
        if isinstance(iv, str) and iv.strip().startswith("${"):
            lines.append(
                f"# {var}['state'][{node!r}]['interval'] = 0.01"
                "   # pin this process's dt (else filled by INTERVAL below)"
            )
        elif iv is not None:
            lines.append(f"{var}['state'][{node!r}]['interval'] = {iv!r}")
        leaves: list[str] = []
        _leaf_assignments(f"{var}['state'][{node!r}]['config']", body.get("config") or {}, leaves)
        lines.extend(leaves)
        lines.append("")
    return "\n".join(lines).rstrip()


def _run_code(slug: str, studies_rel: str, recipes: list[dict],
              spec_vars: dict[str, str], ov_vars: dict[str, str],
              spec_kinds: dict[str, str], strat: dict, had_recorded: bool) -> str:
    """The Run cell: editable runtime knobs + build-and-run per sim, auto-detect.

    Each sim is built and run inside a ``try``: a composite whose module
    processes are not implemented yet reports the error rather than crashing the
    notebook. Generator composites rebuild from their edited ``ov_*`` parameters;
    file specs use the workspace runner (if any) or the generic build path.
    """
    lines = [
        f"# === Study: {slug} ===",
        f"STUDY = {slug!r}",
        f"STUDY_DIR = REPO / {studies_rel!r} / STUDY",
        'STUDY_YAML = str(STUDY_DIR / "study.yaml")',
        'RUNS_DB = str(STUDY_DIR / "runs.db")',
        "",
    ]
    if not recipes:
        lines.append('print("No baseline composite declared for this study — nothing to build.")')
        return "\n".join(lines)

    if had_recorded:
        lines.append("# Reproduce the recorded run(s). STEPS = composite steps.")
    else:
        lines += [
            "# This study has no *recorded* runs yet. RERUN builds its declared",
            "# composite(s) live and runs them; a composite whose module processes",
            "# are not implemented yet reports the error instead of crashing.",
        ]
    for r in recipes:
        sl = _py_ident(r["sim"])
        steps = int(r["n_steps"]) or 100
        lines.append(f"STEPS_{sl} = {steps}")
        lines.append(f"INTERVAL_{sl} = {r['params'].get('interval', 1.0)!r}")
    lines.append("")
    lines.append("if RERUN:")
    lines.append("    from process_bigraph import Composite")
    for r in recipes:
        sl = _py_ident(r["sim"])
        sid = r["spec_id"]
        var = spec_vars[sid]
        kind = spec_kinds.get(sid, "generator")
        lines.append("    try:")
        lines.append("        with quiet():  # keep per-step stdout out of the notebook")
        if kind == "generator":
            ov = ov_vars[sid]
            lines.append(
                f"            _ov = {ov} if {ov!r} in dir() else None  # edits from the panel cell, if any"
            )
            lines.append(f"            {var} = materialize_composite({sid!r}, _ov)")
            lines.append(
                f"            _comp = Composite({{'state': {var}['state']}}, core=core)"
            )
            lines.append(f"            _comp.run(STEPS_{sl})")
        elif strat["run_kind"] == "scripts":
            lines.append(
                f"            run_study(STUDY, {r['sim']!r}, {var}, STEPS_{sl}, INTERVAL_{sl})"
            )
        else:
            lines.append(
                "            from viva_superpowers.composite_spec import build_composite_from_spec"
            )
            lines.append(
                f"            _comp = build_composite_from_spec({var}, "
                f"{{'interval': INTERVAL_{sl}}}, core=core)"
            )
            lines.append(f"            _comp.run(STEPS_{sl})")
        lines.append(
            f"        print('ran', {r['sim']!r}, f'({sid}) for', STEPS_{sl}, 'steps')"
        )
        lines.append("    except Exception as _e:")
        lines.append(
            f"        print(f\"{r['sim']} not runnable yet: "
            f"{{type(_e).__name__}}: {{_e}}\")"
        )
    lines.append("else:")
    lines.append('    print("RERUN=False — not re-simulating.")')
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Build the block list for an investigation
# ---------------------------------------------------------------------------


def _setup_blocks(ws_root: Path, strat: dict) -> list[dict]:
    repo = str(ws_root)
    src = f'''"""Setup — environment & helpers (runs automatically; open only to inspect).

Generated by vivarium-workbench (notebook_export). This cell resolves the repo,
registers the workspace Core, and defines the render helpers; the science lives
in the sections below. Each study is re-run live with the workspace's own
process-bigraph protocol and its figures rendered from the resulting runs.db.
"""
import os
import sys
from pathlib import Path

# Resolve the repository root robustly so this notebook runs from a fresh clone
# at ANY path with no setup (no env var, no path editing). Priority:
#   1. $VIVARIUM_REPO, if it points at a real directory;
#   2. walk up from the notebook's working directory for the repo markers
#      (a directory holding both 'workspace/' and 'pyproject.toml') — Jupyter
#      starts in the notebook's dir, so a committed notebook finds its own root;
#   3. the absolute path it was generated for (back-compat for old layouts);
#   4. the current working directory (last resort).
def _find_repo_root(_start):
    for _cand in (_start, *_start.parents):
        if (_cand / "workspace").is_dir() and (_cand / "pyproject.toml").is_file():
            return _cand
    return None

REPO = None
_env = os.environ.get("VIVARIUM_REPO")
if _env and Path(_env).is_dir():
    REPO = Path(_env)
if REPO is None:
    REPO = _find_repo_root(Path.cwd().resolve())
if REPO is None and Path({repo!r}).is_dir():
    REPO = Path({repo!r})
if REPO is None:
    REPO = Path.cwd()
sys.path.insert(0, str(REPO))
# Composite specs use repo-root-relative paths (datasets, caches), and the
# workspace's runner/renderer assume cwd == repo root — so run from there.
os.chdir(REPO)

# Re-simulate from scratch? Set False to render the committed runs.db (fast).
RERUN = True

# Keep the output clean: composite discovery and the pydantic models emit
# benign UserWarnings (optional packages it skips, a field-name shadow) and the
# simulators emit DeprecationWarnings — none are actionable from a reproduction
# notebook, so silence those two categories. Remove this to see them.
import warnings as _warnings
_warnings.filterwarnings("ignore", category=UserWarning)
_warnings.filterwarnings("ignore", category=DeprecationWarning)

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
    src += (
        "\n\nimport html as _htmlmod\n"
        "def show_viz(_h, height=560):\n"
        '    """Display a visualization\'s HTML in an isolated iframe.\n\n'
        "    The figures embed their own scripts (e.g. Plotly); JupyterLab does not\n"
        "    execute <script> tags from display(HTML(...)), so an iframe srcdoc is\n"
        '    used instead — the browser runs the scripts inside the frame."""\n'
        "    display(HTML(\n"
        "        '<iframe srcdoc=\"{}\" style=\"width:100%;height:{}px;border:0\">'\n"
        "        '</iframe>'.format(_htmlmod.escape(_h, quote=True), height)\n"
        "    ))"
    )
    src += (
        "\n\nimport json as _json\n"
        "def describe_spec(spec):\n"
        '    """Print a composite spec\'s structure (parameters, processes, wiring)\n'
        "    then the full editable dict. The spec is plain data — assign to any\n"
        '    field (e.g. spec[\'state\'][proc][\'config\'][...]) before building."""\n'
        '    print("composite:", spec.get("name"))\n'
        '    if spec.get("description"):\n'
        '        print("description:", str(spec["description"]).strip())\n'
        '    _params = spec.get("parameters") or {}\n'
        "    if _params:\n"
        '        print("\\nparameters (filled into ${name} placeholders):")\n'
        "        for _p, _pdef in _params.items():\n"
        "            print(f\"  {_p}: default={_pdef.get('default')!r}  type={_pdef.get('type')}\")\n"
        '    print("\\nprocesses (node -> address):")\n'
        '    for _node, _body in (spec.get("state") or {}).items():\n'
        '        if not (isinstance(_body, dict) and _body.get("_type") == "process"):\n'
        "            continue\n"
        "        print(f\"  {_node}  ->  {_body.get('address')}   interval={_body.get('interval')!r}\")\n"
        '        for _port in ("inputs", "outputs"):\n'
        "            if _body.get(_port):\n"
        '                print(f"      {_port} ports: {_body[_port]}")\n'
        '    print("\\nfull editable spec dict:")\n'
        "    print(_json.dumps(spec, indent=2, default=str))"
    )
    # Composite materialization (generator-aware) — reuses the exact
    # process-bigraph registry path the workbench env-worker uses, so a
    # @composite_generator function (no on-disk .composite.yaml) still becomes
    # an editable spec payload. Always emitted (needed regardless of render_kind).
    src += (
        "\n\ndef materialize_composite(spec_id, overrides=None):\n"
        '    """Build a composite into an editable payload {name, description,\n'
        "    parameters, state, _overrides, _id}. Works for @composite_generator\n"
        "    functions via the process-bigraph registry (the common case); ``state``\n"
        '    is the built document (process nodes at top level)."""\n'
        "    import importlib\n"
        "    from process_bigraph.composite_generator import (\n"
        "        _REGISTRY, apply_core_extensions, build_generator)\n"
        "    try:\n"
        "        from process_bigraph.composite_generator import discover_generators\n"
        "        discover_generators()\n"
        "    except Exception:\n"
        "        pass\n"
        "    entry = _REGISTRY.get(spec_id)\n"
        "    if entry is None:\n"
        "        # editable installs are invisible to packages_distributions, so\n"
        "        # discover alone can miss them — prime the module the id names.\n"
        "        try:\n"
        "            importlib.import_module(spec_id.rsplit('.', 1)[0])\n"
        "            from process_bigraph.composite_generator import discover_generators as _dg\n"
        "            _dg()\n"
        "        except Exception:\n"
        "            pass\n"
        "        entry = _REGISTRY.get(spec_id)\n"
        "    if entry is None:\n"
        "        raise KeyError('composite not registered: ' + repr(spec_id))\n"
        "    params = dict(entry.parameters or {})\n"
        "    ov = {k: (v.get('default') if isinstance(v, dict) else None)\n"
        "          for k, v in params.items()}\n"
        "    if overrides:\n"
        "        ov.update({k: val for k, val in overrides.items() if k in params})\n"
        "    _c = apply_core_extensions(entry, core)\n"
        "    try:\n"
        "        doc = build_generator(entry, ov or None, core=_c)\n"
        "    except TypeError:\n"
        "        doc = entry.func(_c, **(ov or {}))\n"
        "    return {'name': entry.name, 'description': entry.description,\n"
        "            'parameters': params, 'state': doc, '_overrides': ov, '_id': spec_id}"
    )
    # Generic figure renderer — used when the workspace ships no
    # scripts/render_study_viz.py (so `render_import` is empty and nothing
    # defines _render_one). Resolves an ``image:<relpath>`` visualization to
    # displayable HTML relative to the study dir; other schemes degrade to a
    # note instead of crashing the notebook.
    if strat.get("render_kind") == "generic":
        src += (
            "\n\nimport base64 as _b64, pathlib as _pl\n"
            "_STATIC_VIZ_SCHEMES = ('image:', 'file:', 'gif:', 'png:', 'svg:', 'jpg:', 'jpeg:')\n"
            "def _render_viz_process(address, config):\n"
            '    """Render a Visualization process/step (e.g. ``local:<Class>``) to\n'
            "    HTML: wrap it as a one-step composite and run it once — the viz writes\n"
            "    Plotly HTML to its ``html`` output port. Mirrors the workbench's own\n"
            '    env-worker viz path. Self-running vizes need no run data."""\n'
            "    from process_bigraph import Composite\n"
            "    _doc = {\n"
            "        'inputs_store': {}, 'output_store': '',\n"
            "        'visualization': {\n"
            "            '_type': 'step', 'address': address,\n"
            "            'config': {k: v for k, v in (config or {}).items()\n"
            "                       if k not in ('inputs_map', 'sources')},\n"
            "            'inputs': {}, 'outputs': {'html': ['output_store']},\n"
            "        },\n"
            "    }\n"
            "    _comp = Composite({'state': _doc}, core=core)\n"
            "    _comp.run(1)\n"
            "    _html = _comp.state.get('output_store')\n"
            "    if isinstance(_html, dict):\n"
            "        _html = _html.get('value') or _html.get('_value') or ''\n"
            "    return _html if isinstance(_html, str) else ''\n"
            "def _render_one(address, config, runs_db, study_yaml, overrides=None):\n"
            '    """Generic figure renderer (no workspace render_study_viz.py).\n'
            "    A static ``image:``/``file:`` (or bare-path) address embeds the file;\n"
            "    any other address (``local:<VizProcess>``) is run as a Visualization\n"
            "    and its HTML returned. ``overrides`` (the composite's edited parameter\n"
            "    dict) is merged into the viz config first so a self-running figure\n"
            "    reflects edited parameters; a viz that does not accept those keys\n"
            '    falls back to the base config."""\n'
            "    addr = str(address or '')\n"
            "    _head = addr.split('/', 1)[0]\n"
            "    _is_static = (not addr) or (':' not in _head) or addr.startswith(_STATIC_VIZ_SCHEMES)\n"
            "    if not _is_static:\n"
            "        if core is None:\n"
            "            return f'<p style=\"color:#b91c1c\">no core to render {address}</p>'\n"
            "        _base = {k: v for k, v in (config or {}).items()}\n"
            "        _tries = ([{**_base, **overrides}] if overrides else []) + [_base]\n"
            "        _err = ''\n"
            "        for _cfg in _tries:\n"
            "            try:\n"
            "                _h = _render_viz_process(addr, _cfg)\n"
            "            except Exception as _e:\n"
            "                _err = (f'<p style=\"color:#b91c1c\">visualization {address} not "
            "renderable yet: '\n"
            "                        f'{type(_e).__name__}: {_e}</p>')\n"
            "                continue\n"
            "            return _h or f'<p style=\"color:#6b7280\">{address} produced no HTML</p>'\n"
            "        return _err\n"
            "    for _scheme in _STATIC_VIZ_SCHEMES:\n"
            "        if addr.startswith(_scheme):\n"
            "            addr = addr[len(_scheme):]; break\n"
            "    _p = _pl.Path(addr)\n"
            "    if not _p.is_absolute():\n"
            "        _p = _pl.Path(study_yaml).resolve().parent / _p\n"
            "    if not _p.is_file():\n"
            "        return f'<p style=\"color:#b91c1c\">figure not found: {address}</p>'\n"
            "    _suffix = _p.suffix.lower()\n"
            "    if _suffix == '.svg':\n"
            "        return _p.read_text(encoding='utf-8', errors='replace')\n"
            "    if _suffix in ('.png', '.jpg', '.jpeg', '.gif', '.webp'):\n"
            "        _mime = 'jpeg' if _suffix in ('.jpg', '.jpeg') else _suffix[1:]\n"
            "        _data = _b64.b64encode(_p.read_bytes()).decode('ascii')\n"
            "        return f'<img src=\"data:image/{_mime};base64,{_data}\" style=\"max-width:100%\"/>'\n"
            "    if _suffix in ('.html', '.htm'):\n"
            "        return _p.read_text(encoding='utf-8', errors='replace')\n"
            "    return f'<p style=\"color:#6b7280\">unsupported figure type: {address}</p>'"
        )
    return [_code(src, collapsed=True)]


def _study_blocks(ws_root: Path, layout: dict, slug: str, strat: dict) -> list[dict]:
    study = _load_study(ws_root, layout, slug)
    if study is None:
        return [_md(f"## Study: `{slug}`\n\n_study.yaml not found — skipped._")]

    studies_rel = layout.get("studies") or "studies"
    package = strat["package"]
    study_dir = _studies_dir(ws_root, layout) / slug
    run_recipes = _run_recipes(study_dir / "runs.db")
    # Params + editable-composite cells fall back to the study.yaml `baseline`
    # when there is no runs.db, so figure/reference studies still expose their
    # composite + parameters. The Run cell keeps using `run_recipes` only (no
    # recorded runs → nothing to reproduce), so exposing params never turns into
    # an attempt to re-simulate a static figure.
    display_recipes = run_recipes or _baseline_recipes(study)

    blocks: list[dict] = [_md(_study_header_md(study, slug))]

    # --- design intent (proposed mechanisms / methods / assumptions) ---
    mech = _mechanisms_md(study)
    if mech:
        blocks.append(_md(mech))

    # --- parameters (no results) ---
    blocks.append(_md(_parameters_md(study, display_recipes, package)))

    # --- editable spec: one load/inspect + control-panel cell per composite.
    # A composite is materialized from its @composite_generator (the common
    # case — no on-disk spec file) or loaded from a static .composite.{yaml,json}
    # file when one exists. spec_kinds routes the Run cell accordingly.
    seen_specs: list[str] = []
    for r in display_recipes:
        if r["spec_id"] not in seen_specs:
            seen_specs.append(r["spec_id"])
    spec_vars = {sid: _spec_var(sid) for sid in seen_specs}
    ov_vars = {sid: _ov_var(sid) for sid in seen_specs}
    spec_kinds: dict[str, str] = {}
    composites_dir = ws_root / package / "composites"
    if seen_specs:
        blocks.append(_md(_COMPOSITE_INTRO_MD))
        for spec_id in seen_specs:
            var = spec_vars[spec_id]
            comp_path = _resolve_composite_path(composites_dir, spec_id)
            if comp_path is not None:
                spec_kinds[spec_id] = "file"
                comp_rel = comp_path.relative_to(ws_root).as_posix()
                blocks.append(_md(
                    f"**Composite `{spec_id}`** — `{var}` (static spec file, editable dict)"))
                blocks.append(_code(_load_inspect_code(comp_rel, var)))
                blocks.append(_code(_control_panel_code(var, _load_yaml(comp_path))))
            else:
                spec_kinds[spec_id] = "generator"
                # Prefer export-time introspection: surface the composite's own
                # science (its description) + a guided, editable parameter cell
                # (each knob with its meaning + units). Fall back to the runtime
                # materialize + dump when the package can't be imported here.
                meta = _introspect_generator(ws_root, spec_id)
                if meta and meta.get("parameters"):
                    hdr = f"**The model — `{spec_id}`**"
                    if meta["description"]:
                        hdr += "\n\n" + meta["description"]
                    blocks.append(_md(hdr))
                    blocks.append(_code(
                        _generator_param_cell(ov_vars[spec_id], meta["parameters"])))
                else:
                    blocks.append(_md(
                        f"**Composite `{spec_id}`** — `{var}` "
                        "(materialized from its `@composite_generator`)"))
                    blocks.append(_code(_generator_inspect_code(spec_id, var)))
                    blocks.append(_code(_generator_panel_code(var, ov_vars[spec_id])))

    # --- typed contract (declared interface) ---
    contract = _contract_md(study)
    if contract:
        blocks.append(_md(contract))

    # --- run section: editable runtime, build & run the (edited) composite ---
    blocks.append(
        _md(
            "### Run\n\n_Set `STEPS`, then run. Each composite is built live from "
            "its (edited) spec above and run; a composite whose module processes "
            "aren't implemented yet reports the error instead of crashing. Set "
            "`RERUN = False` to skip re-simulating._"
        )
    )
    blocks.append(_code(_run_code(
        slug, studies_rel, display_recipes, spec_vars, ov_vars, spec_kinds,
        strat, bool(run_recipes))))

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
        # If the viz self-runs a generator composite (config.composite), tie it
        # to that composite's editable `ov_*` dict so edited params flow into the
        # figure. Only for generator specs (file specs have no ov_* var).
        viz_comp = config.get("composite") if isinstance(config, dict) else None
        ov_var = (ov_vars.get(viz_comp)
                  if viz_comp and spec_kinds.get(viz_comp) == "generator" else None)
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
                "ov_var": ov_var,
            }
        )

    # --- acceptance criteria (pre-registered; no outcomes) ---
    acc = _acceptance_md(study)
    if acc:
        blocks.append(_md(acc))

    # --- literature anchors (published values the study is measured against) ---
    lit = _literature_md(study)
    if lit:
        blocks.append(_md(lit))

    return blocks


def _intro_blocks(inv: dict, slug: str) -> list[dict]:
    title = _truthy(inv.get("title")) or slug
    lines = [f"# {title}", "", f"_Investigation `{slug}` — coder reproduction notebook._"]
    if inv.get("question"):
        lines += ["", f"**Question.** {_truthy(inv['question'])}"]
    # `executive` may be a structured dict (v2 spine) OR a plain prose string
    # (older investigations). Handle both — a string is the what-is-this blurb.
    execu = inv.get("executive")
    if isinstance(execu, str):
        if _truthy(execu):
            lines += ["", _truthy(execu)]
    elif isinstance(execu, dict) and execu.get("what_is_this"):
        lines += ["", _truthy(execu["what_is_this"])]
    lines += [
        "",
        "---",
        "",
        "**How to use this notebook**",
        "",
        "1. **Run ▸ Run All Cells** to reproduce every study end to end.",
        "2. To explore, edit a study's **Parameters** cell, then re-run from its "
        "**Run** section — the figures regenerate with your values.",
        "3. The **Setup** cell at the top is collapsed scaffolding (environment + "
        "render helpers); expand it only to inspect. Set `RERUN = False` there to "
        "render the committed `runs.db` without re-simulating.",
        "",
        "The prose states the **question and parameters**; the figures each run "
        "produces are the results.",
    ]
    return [_md("\n".join(lines))]


def _outro_blocks(inv: dict) -> list[dict]:
    execu = inv.get("executive")
    execu = execu if isinstance(execu, dict) else {}   # may be a prose string
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
    fw = _frameworks_md(inv)
    if fw:
        blocks.append(_md(fw))
    blocks += _setup_blocks(ws_root, strat)
    for study_slug in investigation_member_slugs(inv):
        blocks += _study_blocks(ws_root, layout, study_slug, strat)
    blocks += _outro_blocks(inv)
    return blocks


def _study_intro_blocks(study: dict, slug: str, inv_slug: str) -> list[dict]:
    """Front matter for a SINGLE-study notebook (vs the investigation intro)."""
    title = _truthy(study.get("title")) or slug
    origin = f" — investigation `{inv_slug}`" if inv_slug else ""
    lines = [f"# {title}", "", f"_Study `{slug}`{origin} — coder reproduction notebook._"]
    if study.get("question"):
        lines += ["", f"**Question.** {_truthy(study['question'])}"]
    lines += [
        "",
        "---",
        "",
        "**How to use this notebook**",
        "",
        "1. **Run ▸ Run All Cells** to reproduce the study end to end.",
        "2. To explore, edit the **Parameters** cell below, then re-run from the "
        "**Run** section — the figures regenerate with your values.",
        "3. The **Setup** cell at the top is collapsed scaffolding (environment + "
        "render helpers); expand it only to inspect. Set `RERUN = False` there to "
        "render the committed results without re-simulating.",
        "",
        "The prose states the **question and parameters**; the figures each run "
        "produces are the results.",
    ]
    return [_md("\n".join(lines))]


def _build_study_blocks(ws_root: Path, layout: dict, study: dict, slug: str, strat: dict) -> list[dict]:
    inv_slug = _truthy(study.get("investigation"))
    inv: dict = {}
    if inv_slug:
        try:
            inv = _load_investigation(ws_root, layout, inv_slug)
        except FileNotFoundError:
            inv = {}
    blocks = _study_intro_blocks(study, slug, inv_slug)
    fw = _frameworks_md(inv)
    if fw:
        blocks.append(_md(fw))
    blocks += _setup_blocks(ws_root, strat)
    blocks += _study_blocks(ws_root, layout, slug, strat)
    return blocks


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------


def _viz_render_expr(b: dict) -> str:
    """The ``_render_one(...)`` call for a viz block. When the viz is tied to a
    generator composite (and the generic renderer is in use), pass that
    composite's edited parameter dict as ``overrides`` so a self-running figure
    reflects edits; guarded by ``in dir()`` so it works even if the panel cell
    wasn't run (and a viz that ignores the keys falls back to the base config)."""
    ov = b.get("ov_var")
    if ov and b.get("render_kind") == "generic":
        ov_arg = f", overrides=({ov} if {ov!r} in dir() else None)"
    else:
        ov_arg = ""
    return (
        f"_render_one({b['address']!r}, {b['config']!r}, "
        f"RUNS_DB, STUDY_YAML{ov_arg})"
    )


def _viz_code_for_notebook(b: dict) -> str:
    return (
        f"# {b['name']}\n"
        f"show_viz({_viz_render_expr(b)})"
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
            meta: dict = {}
            if b.get("collapsed"):
                # Fold the input in JupyterLab (source_hidden) and tag it for
                # nbconvert exports; the cell still executes on Run All.
                meta = {"jupyter": {"source_hidden": True}, "tags": ["hide-input"]}
            cells.append(
                {
                    "cell_type": "code",
                    "execution_count": None,
                    "metadata": meta,
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
                f"_save_viz({b['study']!r}, {b['slug']!r}, {_viz_render_expr(b)})"
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


def export_study_notebook(
    ws_root: Path | str, slug: str, *, out_dir: Path | str | None = None
) -> dict:
    """Generate ``<slug>.ipynb`` + ``<slug>.py`` for a SINGLE study.

    Same setup/params/composite/visualization cells as the study's section in
    its investigation notebook, but standalone (study intro instead of the
    investigation intro; no sibling studies). Raises ``FileNotFoundError`` when
    the study is absent. Returns ``{"ipynb": Path, "py": Path}``.
    """
    ws_root = Path(ws_root).resolve()
    ws, layout, package = _workspace_layout(ws_root)
    study = _load_study(ws_root, layout, slug)
    if study is None:
        raise FileNotFoundError(f"no study {slug!r}")
    strat = _discover_strategy(ws_root, ws, package)

    if out_dir is None:
        out_dir = _reports_dir(ws_root, layout) / "notebooks"
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    blocks = _build_study_blocks(ws_root, layout, study, slug, strat)

    ipynb_path = out_dir / f"{slug}.ipynb"
    py_path = out_dir / f"{slug}.py"

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
