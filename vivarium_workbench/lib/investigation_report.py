"""Deterministic, self-contained investigation-report generator.

Renders one investigation to a single self-contained HTML document — executive
band, per-study spine sections (Model · Simulation run · Visualizations · Tests ·
Decision), inline SVG figures, collapsible studies, and per-section feedback that
exports to a YAML feedback report.

Everything is read from EXISTING workspace files: ``investigation.yaml``, each
member ``study.yaml``, and any loop-trajectory JSON already present in the
workspace. There is no model call and no invented prose — ``build_report_data``
assembles a pure data dict that is embedded into a fixed template + client
renderer. The only new content a report ever carries is the feedback a reviewer
types, which is exported (never fabricated).
"""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path

import yaml

from vivarium_workbench.lib.path_safety import is_plain_name
from vivarium_workbench.lib.investigation_members import (
    investigation_member_slugs,
    member_slug,
)
from vivarium_workbench.lib.single_study_report import _load_study_spec
from vivarium_workbench.lib.workspace_paths import WorkspacePaths

_TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "investigation-report.html"
_TRAJ_SCHEMAS = ("agent_build_trajectory", "model_build_trajectory")

# study.yaml fields the renderer consumes (everything else is ignored so the
# report never carries content that isn't one of these declared fields)
_STUDY_KEEP = (
    "title", "confidence", "gate_status", "question", "claim", "biological_summary",
    "requires", "sourcing", "baseline", "behavior_tests", "runs", "conclusion",
    "loop_provenance", "purpose", "findings", "conclusion_logic", "limitations",
    "parent_studies", "pipeline_gate", "literature_anchors", "key_assumptions",
    # the four-part scientific framing (question → experiment → observable → learn)
    # the report leads with: the model experiment, the measured observable, and the
    # study's lifecycle/phase + maturity. Additive — each is optional per study.
    "objective", "experiment", "hypothesis", "result", "phase", "kind", "design_status",
    "readouts", "discovery_implications", "status",
)

# 5-state study verdict (mirrors viva_superpowers roll-up): each maps to a glyph
# + label the report legend enumerates.
_VERDICT_GLYPH = {
    "passed": ("✅", "passed"), "failing": ("⛔", "failing"), "blocked": ("⚠", "blocked"),
    "needs_calibration": ("🔄", "needs calibration"), "not_started": ("◽", "not evaluated"),
}
_VERDICT_MAP = {
    "passing": "passed", "passing-with-caveats": "passed", "passed": "passed", "pass": "passed",
    "failing": "failing", "failed": "failing", "fail": "failing", "blocked": "blocked",
    "needs_calibration": "needs_calibration", "not_started": "not_started",
}


def _study_verdict(spec, computed_gate) -> str:
    """The GATE axis — one of five computational-gate states
    (passed/failing/blocked/not_started) from the study's computed gate evaluator,
    else its gate_status, else run presence. This is DISTINCT from, and
    intentionally NOT merged with, the scientific-evidence verdict
    (``study_ontology.derive_verdict`` → supported/qualified/refuted/inconclusive,
    stashed as ``sci_verdict`` and used for the finding-first evidence grade): the
    DAG colours by whether the gate passed, the synthesis grades by what the
    evidence shows, and a study can pass its gate yet be only scientifically
    qualified. It already reuses the canonical computed gate_evaluator result when
    present; only the fallbacks are local."""
    res = (computed_gate or {}).get("result")
    if res and res in _VERDICT_MAP:
        return _VERDICT_MAP[res]
    gs = spec.get("gate_status")
    if gs in ("passed", "pass"):
        return "passed"
    if gs in ("failed", "fail"):
        return "failing"
    return "not_started" if not spec.get("runs") else "blocked"


def _outcome_counts(spec) -> dict:
    passed = skipped = failed = 0
    for r in (spec.get("runs") or []):
        for o in ((r.get("outcomes") or {}).values()):
            res = str((o or {}).get("result", "")).upper()
            if res == "PASS":
                passed += 1
            elif res in ("SKIP", "SKIPPED", "NA", "N/A"):
                skipped += 1
            elif res == "FAIL":
                failed += 1
    return {"passed": passed, "skipped": skipped, "failed": failed}


def _depths(slugs, edges) -> dict:
    """Longest-path depth per node (topological column for the DAG)."""
    incoming = {s: [] for s in slugs}
    for e in edges:
        if e["to"] in incoming and e["from"] in incoming:
            incoming[e["to"]].append(e["from"])
    depth = {s: 0 for s in slugs}
    for _ in range(len(slugs)):  # relax up to N times (DAG converges)
        changed = False
        for s in slugs:
            d = max((depth[p] + 1 for p in incoming[s]), default=0)
            if d != depth[s]:
                depth[s] = d
                changed = True
        if not changed:
            break
    return depth


def _build_spine(ws_root, inv_slug: str, studies: list) -> dict:
    """Computed spine presentation: per-study verdicts, the verdict-annotated
    study DAG, the acceptance roll-up, and the AC→study gating matrix. All from
    the existing server-side computations (build_iset_detail, ac_gating_matrix)."""
    detail = {}
    try:
        from vivarium_workbench.lib.report_views import build_iset_detail
        detail = build_iset_detail(ws_root, inv_slug) or {}
    except Exception:
        detail = {}
    cgv = {s.get("name"): s.get("computed_gate_verdict") for s in detail.get("studies", []) if s.get("name")}
    rcmap = {s.get("name"): s.get("run_commands") for s in detail.get("studies", []) if s.get("name")}
    ac_matrix = None
    try:
        from vivarium_workbench.lib import linkage_index
        ac_matrix = linkage_index.ac_gating_matrix(ws_root, inv_slug)
    except Exception:
        ac_matrix = None

    slugs = [s["slug"] for s in studies]
    edges = []
    for s in studies:
        gate = cgv.get(s["slug"])
        s["verdict"] = _study_verdict(s, gate)
        s["verdict_counts"] = _outcome_counts(s)
        if gate:
            s["computed_gate_verdict"] = gate
        rc = rcmap.get(s["slug"])
        if rc:
            s["run_commands"] = rc
        # Dependency edges mirror the workbench's _dagEdges precedence: prefer
        # pipeline_gate.prerequisites (the canonical Pass-A field), else the
        # legacy parent_studies. Reading only parent_studies missed studies that
        # declare prerequisites via pipeline_gate (og-02 → og-01), collapsing the
        # chain to a flat depth-0 row.
        pg = s.get("pipeline_gate") or {}
        raw_edges = (pg.get("prerequisites") if isinstance(pg, dict) and pg.get("prerequisites")
                     else s.get("parent_studies")) or []
        for p in raw_edges:
            src = p.get("study") if isinstance(p, dict) else p
            if src in slugs:
                edges.append({"from": src, "to": s["slug"]})
    depth = _depths(slugs, edges)
    nodes = [{"slug": s["slug"], "title": s["title"], "verdict": s["verdict"],
              "counts": s["verdict_counts"], "depth": depth.get(s["slug"], 0)} for s in studies]
    return {
        "verdict_dag": {"nodes": nodes, "edges": edges},
        "acceptance": detail.get("computed_acceptance"),
        "ac_matrix": ac_matrix,
        # The honest A→E gate ladder (met/qualified/refuted/off-spine), computed
        # exactly as the live investigation overview — surfaced so the report can
        # lead the findings with where the campaign stands against its own plan.
        "plan_board": detail.get("plan_board") or [],
    }

# inline-image budget: keep the self-contained report a sane size
_IMG_PER_FILE_MAX = 1_400_000     # skip a single figure larger than this
# The model loom is the study's most important figure and a rasterised loom of a
# large composite legitimately runs past the generic figure cap (e.g. an atlas
# loom ~1.4-2.5 MB), so it gets its own higher per-file ceiling.
_LOOM_IMG_PER_FILE_MAX = 3_000_000
_IMG_TOTAL_MAX = 16_000_000       # stop embedding once the report gets heavy
_IMG_MIME = {".svg": "image/svg+xml", ".png": "image/png", ".gif": "image/gif",
             ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def _humanize(slug: str) -> str:
    return re.sub(r"[-_]+", " ", str(slug)).strip().title()


def _embed_visualizations(study_dir: Path, viz_list, budget: list) -> list:
    """Resolve each ``visualizations[].address`` of the form ``image:<relpath>``
    to a data-URI so the report stays self-contained. Respects a size budget
    (``budget[0]`` is the running total); oversized/missing files are skipped
    with a note so the report never silently drops a figure it meant to show."""
    out = []
    for v in (viz_list or []):
        if not isinstance(v, dict):
            continue
        addr = str(v.get("address") or "")
        name = v.get("name") or ""
        if not addr.startswith("image:"):
            continue
        rel = addr[len("image:"):]
        f = (study_dir / rel).resolve()
        try:
            f.relative_to(study_dir.resolve())
        except ValueError:
            continue  # never read outside the study dir
        ext = f.suffix.lower()
        if not f.is_file() or ext not in _IMG_MIME:
            continue
        size = f.stat().st_size
        if size > _IMG_PER_FILE_MAX or budget[0] + size > _IMG_TOTAL_MAX:
            out.append({"name": name, "skipped": True, "reason": "too large to inline"})
            continue
        try:
            data = base64.b64encode(f.read_bytes()).decode("ascii")
        except OSError:
            continue
        budget[0] += size
        out.append({"name": name, "data_uri": f"data:{_IMG_MIME[ext]};base64,{data}"})
    return out


_HTML_PER_FILE_MAX = 2_500_000    # a single inlined HTML figure (post-processing)
_HTML_RAW_READ_MAX = 12_000_000   # never even read an on-disk figure larger than this
_HTML_FIG_DIRS = ("viz", "charts")
_HTML_FIG_SKIP = ("model-loom.html",)  # reserved for the Model section, not a result figure

# A per-figure Plotly.js loader: an external <script src="…plotly…"> the figure
# used to pull its own copy of the library. The report now provides ONE shared
# Plotly.js, so these are stripped when a figure is inlined interactively.
_PLOTLY_SRC_LOADER_RE = re.compile(
    r"<script\b[^>]*\bsrc\s*=\s*['\"][^'\"]*plotly[^'\"]*['\"][^>]*>\s*</script\s*>",
    re.IGNORECASE,
)
# An INLINE plotly bundle: a large <script> (no src) that carries the library
# itself (not the figure's own newPlot call). Stripped too, so a self-contained
# Plotly figure shrinks from ~4.6 MB to tens of KB and comfortably inlines.
_INLINE_SCRIPT_RE = re.compile(r"<script\b(?![^>]*\bsrc=)[^>]*>(.*?)</script\s*>",
                               re.IGNORECASE | re.DOTALL)
_BODY_RE = re.compile(r"<body[^>]*>(.*)</body\s*>", re.IGNORECASE | re.DOTALL)


def _strip_inline_plotly_bundle(frag: str) -> str:
    """Drop an inline <script> that is the Plotly LIBRARY itself (big, mentions
    plotly, but is not the figure's own ``Plotly.newPlot`` call)."""
    def _repl(m):
        body = m.group(1)
        low = body.lower()
        if len(body) > 200_000 and "plotly" in low and "newplot" not in low:
            return ""
        return m.group(0)
    return _INLINE_SCRIPT_RE.sub(_repl, frag)


def _prepare_interactive_figure(html: str) -> "str | None":
    """If ``html`` is a self-contained Plotly figure, return just its body
    fragment — the graph ``<div>`` plus its ``Plotly.newPlot`` script — with any
    per-figure Plotly.js loader (external ``<script src=…plotly…>`` or an inline
    library bundle) removed. The report supplies one shared Plotly.js and renders
    this fragment inline in the main document, so every figure uses that single
    copy. Returns None when the HTML is not a Plotly figure (the caller then keeps
    the isolated-iframe path for arbitrary HTML). Fully guarded by the caller."""
    if "Plotly.newPlot" not in html:
        return None
    m = _BODY_RE.search(html)
    frag = m.group(1) if m else html
    frag = _PLOTLY_SRC_LOADER_RE.sub("", frag)
    frag = _strip_inline_plotly_bundle(frag)
    return frag.strip()


def _humanize_stem(stem: str) -> str:
    return re.sub(r"[-_]+", " ", stem).strip().capitalize()


def _embed_html_figures(study_dir: Path, budget: list, skip_names: set) -> list:
    """Inline a study's on-disk HTML figures (``viz/*.html`` / ``charts/*.html``)
    as raw HTML so the self-contained report shows them.

    This is how the ``local:`` (live-rendered) visualizations every current
    workspace uses reach the downloadable report: the dashboard renders them
    live, and the study commits the rendered artifact next to its ``study.yaml``;
    here we inline that committed HTML. Complements :func:`_embed_visualizations`,
    which only inlines ``image:``-addressed raster/vector figures as data-URIs.

    Ordered by filename for determinism; respects the shared inline-size budget;
    ``skip_names`` (e.g. an already-embedded image basename) avoids duplicating a
    figure surfaced through another path. Fully guarded — an unreadable file is
    skipped, never fatal.
    """
    out: list = []
    seen: set = set()
    for sub in _HTML_FIG_DIRS:
        d = study_dir / sub
        if not d.is_dir():
            continue
        # Recurse: a consolidated study keeps each absorbed sub-study's rendered
        # viz under viz/<sub-study>/*.html, so a flat viz/*.html glob missed them
        # entirely (e.g. og-self-compartmentation's fatty-acid-vesicle-stability /
        # self-maintaining-protocell MD figures). rglob covers both the flat and
        # the nested layout; dedup on the path relative to the study dir (not the
        # bare basename) so same-named figures in different subdirs both survive.
        for f in sorted(d.rglob("*.html"), key=lambda p: p.as_posix()):
            base = f.name
            rel = f.relative_to(study_dir).as_posix()
            if base in _HTML_FIG_SKIP or base in skip_names or rel in seen:
                continue
            seen.add(rel)
            try:
                raw_size = f.stat().st_size
            except OSError:
                continue
            if raw_size > _HTML_RAW_READ_MAX:  # don't even read an enormous file
                out.append({"name": _humanize_stem(f.stem), "skipped": True,
                            "reason": "too large to inline"})
                continue
            try:
                html = f.read_text(encoding="utf-8")
            except OSError:
                continue
            # An interactive Plotly figure is inlined in the main document sharing
            # the report's single Plotly.js — so strip its own Plotly loader and
            # keep just the graph <div> + newPlot script. Any other HTML stays raw
            # and renders in its own isolated iframe. Guarded: a malformed figure
            # falls back to raw HTML, never fatal.
            interactive = False
            frag = html
            try:
                prepared = _prepare_interactive_figure(html)
                if prepared is not None:
                    frag, interactive = prepared, True
            except Exception:
                frag, interactive = html, False
            # budget against the POST-processing size (a stripped interactive
            # figure is tiny even if its on-disk file bundled a 4.6 MB Plotly.js)
            fsize = len(frag.encode("utf-8"))
            if fsize > _HTML_PER_FILE_MAX or budget[0] + fsize > _IMG_TOTAL_MAX:
                out.append({"name": _humanize_stem(f.stem), "skipped": True,
                            "reason": "too large to inline"})
                continue
            budget[0] += fsize
            rec = {"name": _humanize_stem(f.stem), "html": frag}
            if interactive:
                rec["interactive"] = True
            out.append(rec)
    return out


def _embed_native_gallery_panels(ws_root, slug: str, budget: list, skip_names: set) -> list:
    """Inline a study's NATIVE-gallery visualization panels — the declared
    ``visualizations[]`` rendered to the latest completed run's
    ``.pbg/runs/<run>/viz.json`` (in-memory HTML, never committed on disk).

    Modern studies render their RESULT figures here, not to committed
    ``viz/*.html`` files, so without this they were absent from the downloadable
    report (only ``image:`` figures + on-disk ``viz/*.html`` came through —
    ``_embed_visualizations`` / ``_embed_html_figures``). Each Plotly panel is
    stripped to share the report's single inlined Plotly.js, exactly like
    :func:`_embed_html_figures`. Fully guarded: a missing run / unreadable
    gallery yields ``[]`` so the report never breaks.
    """
    out: list = []
    try:
        from vivarium_workbench.lib.investigation_figures import study_output_html_panels
        panels = study_output_html_panels(ws_root, slug) or {}
    except Exception:
        return out
    for pname, html in panels.items():
        name = _humanize_stem(str(pname))
        if not html or name in skip_names:
            continue
        skip_names.add(name)
        frag, interactive = html, False
        try:
            prepared = _prepare_interactive_figure(html)
            if prepared is not None:
                frag, interactive = prepared, True
        except Exception:
            frag, interactive = html, False
        fsize = len(frag.encode("utf-8"))
        if fsize > _HTML_PER_FILE_MAX or budget[0] + fsize > _IMG_TOTAL_MAX:
            out.append({"name": name, "skipped": True, "reason": "too large to inline"})
            continue
        budget[0] += fsize
        rec = {"name": name, "html": frag}
        if interactive:
            rec["interactive"] = True
        out.append(rec)
    return out


_LOOM_IMG_CANDIDATES = (
    "viz/model-loom.png", "viz/model-loom.svg",
    "visualizations/model-loom.png", "visualizations/model-loom.svg",
)


def _model_loom_img(study_dir: Path, budget: list) -> "str | None":
    """Data-URI for a study's pre-rendered bigraph-loom image (Model section).

    Looks for a saved loom render at ``viz/model-loom.{png,svg}`` (produced by
    ``vivarium-workbench render-loom``). PNG is preferred — a rasterised loom is
    ~0.3 MB vs a 1-4 MB KaTeX/font-heavy vector SVG. Respects the total inline
    budget and its own higher per-file cap (``_LOOM_IMG_PER_FILE_MAX``); returns
    None when absent or oversized.
    """
    for rel in _LOOM_IMG_CANDIDATES:
        f = study_dir / rel
        if not f.is_file():
            continue
        ext = f.suffix.lower()
        if ext not in _IMG_MIME:
            continue
        size = f.stat().st_size
        if size > _LOOM_IMG_PER_FILE_MAX or budget[0] + size > _IMG_TOTAL_MAX:
            return None
        try:
            data = base64.b64encode(f.read_bytes()).decode("ascii")
        except OSError:
            return None
        budget[0] += size
        return f"data:{_IMG_MIME[ext]};base64,{data}"
    return None


def _norm_study(s) -> str:
    return re.sub(r"-(agent|policy)$", "", str(s or "").strip())


def _slim_traj(t: dict) -> dict:
    """Reduce a loop-trajectory doc to exactly what the figures need."""
    return {
        "driver": t.get("driver"),
        "contract": t.get("contract"),
        "tests": t.get("tests"),
        "result": t.get("result"),
        "iterations": [
            {
                "iteration": it.get("iteration"),
                "active": it.get("active"),
                "n_pass": it.get("n_pass"),
                "n_hard": it.get("n_hard"),
                "newly_fixed": it.get("newly_fixed"),
                "regressed": it.get("regressed"),
                "decision": it.get("agent_decision") or it.get("decision"),
                "observables": it.get("observables"),
                "tests": [
                    {
                        "id": x.get("id") or x.get("name"), "label": x.get("label"),
                        "verdict": x.get("verdict"), "margin": x.get("margin"),
                        "observed": x.get("observed"), "expected": x.get("expected"),
                    }
                    for x in (it.get("tests") or [])
                ],
            }
            for it in (t.get("iterations") or [])
        ],
    }


def _discover_trajectories(wp) -> list[dict]:
    """Every loop-trajectory JSON under ``investigations/``, tagged with the study
    it belongs to and whether it is an agent or a deterministic-policy run
    (agent_build_trajectory/* → agent; model_build_trajectory/* → policy)."""
    out: list[dict] = []
    root = wp.investigations
    if not root.is_dir():
        return out
    for p in sorted(root.rglob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict):
            continue
        sc = str(d.get("schema") or "")
        if not sc.startswith(_TRAJ_SCHEMAS):
            continue
        out.append({
            "study": _norm_study(d.get("study")),
            "stem": p.stem,
            "kind": "agent" if sc.startswith("agent_build") else "policy",
            "doc": d,
        })
    return out


def _match_traj(trajs, slug, kind):
    """Match a trajectory to a study slug. The ``study`` field is not always the
    slug (e.g. 'bounded-cell-agent', 'multicell' vs 'multicellular'), so accept a
    normalized exact match, a prefix match, or a filename-stem prefix."""
    for t in trajs:
        if t["kind"] != kind:
            continue
        st = t["study"]
        if st == slug or (len(st) >= 4 and slug.startswith(st)) or t["stem"].startswith(slug):
            return _slim_traj(t["doc"])
    return None


def _compact_bigraph(state: dict) -> dict:
    """Reduce a resolved composite ``state`` to a compact bigraph topology for
    the Model-section loom view: the nested store tree (name + type), the flat
    process list (name + address + ports + doc/contract), and the process↔store
    WIRING edges (grouped by the top-level store compartment) so the report can
    render an interactive bigraph without a running server."""
    procs: list = []
    edges: list = []

    def _port_paths(pm):
        # port -> path list; yields (compartment, leaf) for each wired store
        for path in (pm or {}).values():
            if isinstance(path, (list, tuple)) and len(path) >= 2:
                comp = path[1] if len(path) >= 3 else path[0]
                yield str(comp), str(path[-1])

    def walk(node: dict) -> list:
        out = []
        for k, v in node.items():
            if k.startswith("_") or not isinstance(v, dict):
                continue
            t = v.get("_type")
            if t in ("process", "step"):
                comps, leaves = set(), set()
                for pm in (v.get("inputs"), v.get("outputs")):
                    for comp, leaf in _port_paths(pm):
                        comps.add(comp); leaves.add(leaf)
                        edges.append({"p": k, "c": comp, "s": leaf})
                doc = v.get("doc") or ""
                procs.append({
                    "name": k, "kind": t, "address": v.get("address"),
                    "inputs": list((v.get("inputs") or {}).keys()),
                    "outputs": list((v.get("outputs") or {}).keys()),
                    # first line of the describe() doc = the concise contract summary
                    "doc": (doc.splitlines()[0] if doc else ""),
                    "compartments": sorted(comps),
                })
                continue
            out.append({"name": k, "type": t, "children": walk(v)})
        return out

    stores = walk(state)
    # dedupe edges (a process may touch a store via >1 port)
    seen = set(); uniq = []
    for e in edges:
        key = (e["p"], e["c"], e["s"])
        if key not in seen:
            seen.add(key); uniq.append(e)
    return {"processes": procs, "stores": stores, "edges": uniq}


def _baseline_composite_id(spec: dict) -> "str | None":
    """The study's baseline composite id, from either schema shape:

    - v4: ``conditions.baseline.composite`` (a single dict) — what current
      studies author, and what the dashboard's Model tab reads.
    - legacy v3: the first entry of the top-level ``baseline:`` list.

    Returns None when neither carries a composite. Reading only the legacy list
    is why v4 studies (every current one) rendered no Model figure in the
    downloadable report.
    """
    conditions = spec.get("conditions")
    if isinstance(conditions, dict):
        base = conditions.get("baseline")
        if isinstance(base, dict) and base.get("composite"):
            return str(base["composite"])
        # tolerate a list-shaped conditions.baseline too
        if isinstance(base, list):
            for b in base:
                if isinstance(b, dict) and b.get("composite"):
                    return str(b["composite"])
    for b in (spec.get("baseline") or []):
        if isinstance(b, dict) and b.get("composite"):
            return str(b["composite"])
    return None


def _composite_methods_fidelity(cid: "str | None") -> "tuple[list | None, str | None]":
    """Resolve ``(methods, fidelity)`` for a study's baseline composite off its
    registered ``GeneratorEntry`` — process-bigraph's
    ``composite_generator(methods=[...], fidelity="real|reproduction")`` fields,
    which live on the composite, NOT on ``study.yaml``. Fully guarded: an
    unimportable process-bigraph, an unregistered composite, or an entry without
    the fields all yield ``(None, None)`` so the Methods row + fidelity chip
    simply omit (data-safe). The caller checks the study spec's own
    ``methods``/``fidelity`` keys first, so an explicit study override wins."""
    if not cid:
        return None, None
    try:
        from vivarium_workbench.lib.investigation_run_one_views import _generator_entry
        entry = _generator_entry(cid)
        if entry is None and not cid.startswith("local:"):
            entry = _generator_entry("local:" + cid)
        if entry is None:
            return None, None
        methods = getattr(entry, "methods", None)
        fidelity = getattr(entry, "fidelity", None)
        return (methods or None), (fidelity or None)
    except Exception:
        return None, None


def _model_topology(ws_root, spec: dict) -> "dict | None":
    """Best-effort bigraph topology for the study's baseline composite, used to
    render the loom view in the Model section. Fully guarded: any
    resolution/import failure returns None so the report never breaks."""
    try:
        cid = _baseline_composite_id(spec)
        if not cid:
            return None
        from vivarium_workbench.lib.composite_resolve import resolve_composite
        data = resolve_composite(ws_root, cid)
        if not isinstance(data, dict):
            return None
        state = data["state"] if isinstance(data.get("state"), dict) else data
        if not isinstance(state, dict):
            return None
        topo = _compact_bigraph(state)
        if not topo["processes"] and not topo["stores"]:
            return None
        topo["composite"] = cid
        return topo
    except Exception:
        return None


# --- references / provenance -------------------------------------------------
# The studies cite papers by bibkey (literature_anchors[].cites,
# behavior_tests[].cites, findings[].expected.cites). The report turns each
# bibkey into a short "Author Year" chip linking to a References section, so a
# reader can trace any modeling decision back to its source. Resolved from the
# workspace's references/papers.bib (shared bib the Resources tab reads too).

_BIB_ENTRY_RE = re.compile(r"@\w+\s*\{\s*([^,\s]+)\s*,(.*?)\n\}", re.DOTALL)
_BIB_FIELD_RE = re.compile(r"(\w+)\s*=\s*[{\"](.*?)[}\"]\s*,?\s*$", re.MULTILINE | re.DOTALL)


def _bib_label(key: str, fields: dict) -> str:
    """A short human label for a citation chip: first-author surname + year."""
    author = re.sub(r"\s+", " ", fields.get("author", "")).strip()
    year = fields.get("year", "").strip()
    surname = ""
    if author:
        first = re.split(r"\s+and\s+", author)[0].strip()
        surname = first.split(",")[0].strip() if "," in first else first.split()[-1]
    if surname and year:
        return f"{surname} {year}"
    if surname:
        return surname
    return year or key


def _load_references(ws_root: Path, wp) -> dict:
    """Parse ``references/papers.bib`` → ``{bibkey: {label, author, year, title,
    url}}``. Best-effort: a missing/garbled bib yields ``{}`` so the report still
    renders (chips then fall back to the bare bibkey)."""
    out: dict = {}
    try:
        bib = wp.investigations.parent / "references" / "papers.bib"
        if not bib.is_file():
            return {}
        text = bib.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return {}
    for m in _BIB_ENTRY_RE.finditer(text):
        key = m.group(1).strip()
        fields = {fk.lower(): re.sub(r"\s+", " ", fv).strip()
                  for fk, fv in _BIB_FIELD_RE.findall(m.group(2))}
        # Decode BibTeX/LaTeX accent escapes to Unicode and drop grouping braces
        # so chips/labels read as the real name (e.g. Segr{\`e} -> Segrè),
        # matching the References cards (lib.report._absorb_kv). Without this the
        # raw escape leaks into every citation chip the report + study pages show.
        from vivarium_workbench.lib.report import _delatex
        fields = {k: re.sub(r"\s+", " ", _delatex(v).replace("{", "").replace("}", "")).strip()
                  for k, v in fields.items()}
        out[key] = {
            "label": _bib_label(key, fields),
            "author": fields.get("author", ""),
            "year": fields.get("year", ""),
            "title": fields.get("title", ""),
            "url": fields.get("url", "") or fields.get("doi", ""),
        }
    return out


# ---------------------------------------------------------------------------
# Deterministic cross-study SYNTHESIS (report upgrade). No model call, no
# invented prose: every element is DERIVED from existing per-study +
# investigation fields (verdicts, findings, edges, limitations, follow-ups) and
# composed into a finding-first narrative. An optional authored
# ``report_synthesis`` block in investigation.yaml overrides/enriches any
# section. Separates run status from evidence strength from scientific claim —
# a passed computational gate is never reported as a proven mechanism.
# ---------------------------------------------------------------------------
def _syn_run_status(s):
    """Run status — REUSES the canonical two-axis ontology
    (``study_ontology.derive_execution_status``) rather than re-deriving it. The
    canonical value (completed/running/failed/planned/proposed) is stashed on the
    study as ``execution_status`` in build_report_data; collapse it to ran|planned."""
    es = str(s.get("execution_status") or "").lower()
    if not es:
        try:
            from vivarium_workbench.lib.study_ontology import derive_execution_status
            es = str(derive_execution_status(s, str(s.get("status") or "")) or "").lower()
        except Exception:  # noqa: BLE001
            es = str(s.get("status") or "").lower()
    return "planned" if es in ("planned", "proposed", "not_started", "designing", "not_run") else "ran"


def _syn_grade(s):
    """Evidence grade — DISTINCT from run status. REUSES the canonical scientific
    verdict (``study_ontology.derive_verdict`` → supported/qualified/refuted/
    inconclusive, stashed as ``sci_verdict``); only when that is absent does it
    fall back to a gate_status/confidence mapping. Planned studies → pending."""
    if _syn_run_status(s) == "planned":
        return "pending"
    sv = str(s.get("sci_verdict") or "").lower()
    if not sv:
        try:
            from vivarium_workbench.lib.study_ontology import derive_verdict
            sv = str(derive_verdict(s) or "").lower()
        except Exception:  # noqa: BLE001
            sv = ""
    if sv in ("supported", "qualified", "refuted", "inconclusive"):
        return sv
    # Fallback (no derivable scientific verdict): the gate/confidence reduction.
    gs = str(s.get("gate_status") or "").lower()
    v = str(s.get("verdict") or "").lower()
    conf = str(s.get("confidence") or "").lower()
    if gs in ("failed", "failing") or v in ("refuted", "failed") or conf == "refuted":
        return "refuted"
    if gs in ("stale", "needs_calibration") or v in ("qualified", "inconclusive") \
            or conf in ("investigating", "qualified", "mixed"):
        return "qualified"
    if gs == "passed" or v in ("passed", "supported", "met") or conf in ("accepted", "supported"):
        return "supported"
    return "inconclusive"


def _syn_claim_status(grade):
    return {"supported": "supported", "qualified": "conditional",
            "refuted": "unsupported", "pending": "pending"}.get(grade, "inconclusive")


def _syn_stage_key(slug):
    m = re.match(r"og-([a-z0-9]+)", str(slug or ""))
    k = m.group(1) if m else str(slug or "")
    m2 = re.match(r"(\d+)[a-z]*$", k)   # collapse og-00b -> 00
    return m2.group(1) if m2 else k


_STEM_ORDER = {"cal": 50, "rtca": 51, "res": 60, "energy": 61, "growth": 62, "adv": 70}


def _syn_stage_sort(key):
    return (0, int(key), "") if key.isdigit() else (1, _STEM_ORDER.get(key, 99), key)


def _syn_is_variant(slug):
    s = str(slug or "")
    return ("-alt-" in s) or ("adversarial" in s) or s.endswith("-control")


def _syn_first_sentence(t, n=220):
    t = re.sub(r"\s+", " ", str(t or "")).strip()
    if not t:
        return ""
    m = re.match(r"^.*?[.!?](?=\s|$)", t)
    return (m.group(0) if m else t)[:n]


def _syn_oneline(s):
    c = str(s.get("claim") or "").strip()
    if c:
        return _syn_first_sentence(c)
    fs = s.get("findings") or []
    if fs:
        f0 = fs[0] or {}
        return _syn_first_sentence(f0.get("summary") or f0.get("statement") or "")
    return _syn_first_sentence(s.get("experiment") or s.get("objective") or s.get("question") or "")


def _build_synthesis(inv, studies, spine):
    spine = spine or {}
    recs = []
    for s in studies:
        grade = _syn_grade(s)
        recs.append({
            "slug": s.get("slug"), "title": s.get("title"),
            "stage": _syn_stage_key(s.get("slug")),
            "run_status": _syn_run_status(s), "grade": grade,
            "claim_status": _syn_claim_status(grade),
            "variant": _syn_is_variant(s.get("slug")),
            "one_line": _syn_oneline(s), "_s": s,
        })

    pub = ("slug", "title", "stage", "run_status", "grade", "claim_status", "variant", "one_line")
    studies_index = [{k: r[k] for k in pub} for r in recs]

    # STAGES — integrated findings grouped by scientific stage (not one per study)
    stages_map = {}
    for r in recs:
        stages_map.setdefault(r["stage"], []).append(r)
    stages = []
    for key in sorted(stages_map, key=_syn_stage_sort):
        members = stages_map[key]
        primary = next((m for m in members if not m["variant"] and m["run_status"] == "ran"), members[0])
        grades = {m["grade"] for m in members}
        model_dependence = ("supported" in grades) and bool(grades & {"refuted", "qualified"})
        fnds = []
        for m in members:
            for f in (m["_s"].get("findings") or [])[:3]:
                ev = (f or {}).get("evidence") or {}
                fnds.append({
                    "study": m["slug"], "title": m["title"], "grade": m["grade"],
                    "status": (f or {}).get("status"),
                    "statement": _syn_first_sentence((f or {}).get("statement") or (f or {}).get("summary") or "", 260),
                    "observed": ev.get("observed"), "units": ev.get("units"),
                })
        stages.append({
            "key": key, "label": primary["title"],
            "studies": [{k: m[k] for k in ("slug", "title", "grade", "claim_status", "variant", "one_line", "run_status")} for m in members],
            "model_dependence": model_dependence, "findings": fnds,
        })

    claim_evidence = [{
        "claim": _syn_first_sentence(r["_s"].get("claim"), 300), "status": r["claim_status"],
        "grade": r["grade"], "study": r["slug"], "study_title": r["title"],
    } for r in recs if str(r["_s"].get("claim") or "").strip()]

    # CONTRADICTIONS — model dependence, negative results, finding conflicts, frameworks
    contradictions = []
    for st in stages:
        if st["model_dependence"]:
            sup = [m for m in st["studies"] if m["grade"] == "supported"]
            neg = [m for m in st["studies"] if m["grade"] in ("refuted", "qualified")]
            if sup and neg:
                contradictions.append({
                    "kind": "model-dependence", "topic": st["label"],
                    "detail": "A result holds in one model class but not another at matched conditions — report as model dependence, not two independent outcomes.",
                    "studies": [{"slug": m["slug"], "title": m["title"], "grade": m["grade"]} for m in (sup + neg)],
                })
    for r in recs:
        if r["grade"] == "refuted":
            s = r["_s"]
            di = s.get("discovery_implications") or {}
            fps = di.get("followup_study_proposals") if isinstance(di, dict) else None
            contradictions.append({
                "kind": "negative-result", "topic": r["title"],
                "detail": _syn_first_sentence(s.get("result") or s.get("conclusion") or _syn_oneline(s), 300),
                "studies": [{"slug": r["slug"], "title": r["title"], "grade": "refuted"}],
                "discriminating": (fps[0] if isinstance(fps, list) and fps else None),
            })
    for r in recs:
        for f in (r["_s"].get("findings") or []):
            if str((f or {}).get("status") or "").lower() in ("contradicts", "refutes", "partial"):
                contradictions.append({
                    "kind": "finding-conflict", "topic": r["title"],
                    "detail": _syn_first_sentence((f or {}).get("statement") or (f or {}).get("summary") or "", 300),
                    "studies": [{"slug": r["slug"], "title": r["title"], "grade": r["grade"]}],
                })
    for cf in (inv.get("competing_frameworks") or []):
        if isinstance(cf, dict) and cf.get("name"):
            contradictions.append({
                "kind": "competing-framework", "topic": cf.get("name"),
                "detail": _syn_first_sentence(cf.get("relation") or "", 300), "studies": [],
            })

    edges = ((spine.get("verdict_dag") or {}).get("edges")) or []
    have_parents = {e.get("to") for e in edges}
    continuity = {
        "edges": [{"from": e.get("from"), "to": e.get("to")} for e in edges],
        "external_inputs": [{"slug": r["slug"], "title": r["title"]}
                            for r in recs if r["run_status"] == "ran" and r["slug"] not in have_parents],
    }

    quantitative = []
    for st in stages:
        rows = [{"study": f["study"], "statement": f["statement"], "observed": f["observed"], "units": f["units"]}
                for f in st["findings"] if f.get("observed")]
        if rows:
            quantitative.append({"stage": st["label"], "rows": rows})

    roadmap = []
    for r in recs:
        if r["run_status"] == "planned":
            roadmap.append({"priority": "P1", "item": r["title"],
                            "basis": _syn_first_sentence(r["_s"].get("experiment") or r["_s"].get("objective") or r["_s"].get("question") or "", 240),
                            "source": r["slug"]})
    for c in contradictions:
        if c["kind"] == "negative-result" and c.get("discriminating"):
            d = c["discriminating"]
            roadmap.insert(0, {"priority": "P0", "item": "Discriminate: " + str(c["topic"]),
                               "basis": _syn_first_sentence(str(d if not isinstance(d, dict) else (d.get("study") or d.get("title") or d)), 240),
                               "source": (c["studies"][0]["slug"] if c["studies"] else None)})
    for r in recs:
        di = r["_s"].get("discovery_implications") or {}
        fps = di.get("followup_study_proposals") if isinstance(di, dict) else None
        for fp in (fps or [])[:2]:
            txt = fp.get("study") or fp.get("title") or fp.get("proposal") if isinstance(fp, dict) else fp
            if txt:
                roadmap.append({"priority": "P2", "item": _syn_first_sentence(str(txt), 160), "basis": "", "source": r["slug"]})
    # Prioritized + bounded: keep all P0/P1, dedupe + cap P2, then order by priority.
    _seen, _dedup = set(), []
    for it in roadmap:
        if it["priority"] == "P2":
            k = it["item"].lower()[:80]
            if not it["item"] or k in _seen:
                continue
            _seen.add(k)
        _dedup.append(it)
    _p2 = [i for i in _dedup if i["priority"] == "P2"][:8]
    roadmap = sorted([i for i in _dedup if i["priority"] != "P2"] + _p2,
                     key=lambda i: {"P0": 0, "P1": 1, "P2": 2}.get(i["priority"], 3))

    # Figure shortlist — rank by scientific weight, then DIVERSIFY: at most the
    # single best figure per study, so the shortlist spans stages (not 6 from one).
    best_per_study = {}
    for r in recs:
        for f in (r["_s"].get("figures_embedded") or []):
            score = (3 if r["grade"] == "supported" else 2 if r["grade"] in ("qualified", "refuted") else 0)
            if (f or {}).get("caption") or (f or {}).get("description"):
                score += 1
            if not r["variant"]:
                score += 1
            cur = best_per_study.get(r["slug"])
            cand = {"slug": r["slug"], "study_title": r["title"], "name": (f or {}).get("name"),
                    "caption": (f or {}).get("caption") or (f or {}).get("description"), "score": score}
            if cur is None or score > cur["score"]:
                best_per_study[r["slug"]] = cand
    figures_ranked = sorted(best_per_study.values(), key=lambda x: -x["score"])[:6]

    headline = [{"kind": "gate", "id": g.get("id"), "name": g.get("name"), "status": g.get("status")}
                for g in (spine.get("plan_board") or [])]

    out = {
        "studies_index": studies_index, "stages": stages, "claim_evidence": claim_evidence,
        "contradictions": contradictions, "continuity": continuity, "quantitative": quantitative,
        "roadmap": roadmap, "figures_ranked": figures_ranked, "headline": headline,
        "counts": {
            "total": len(recs),
            "supported": sum(1 for r in recs if r["grade"] == "supported"),
            "qualified": sum(1 for r in recs if r["grade"] == "qualified"),
            "refuted": sum(1 for r in recs if r["grade"] == "refuted"),
            "planned": sum(1 for r in recs if r["run_status"] == "planned"),
        },
    }
    auth = inv.get("report_synthesis")
    if isinstance(auth, dict):
        for k, v in auth.items():
            if v:
                out[k] = v
    return out


def build_report_data(ws_root, inv_slug: str, *, loom_embed: "dict | None" = None) -> dict:
    """Assemble the pure report-data dict for one investigation from existing
    files only. Raises FileNotFoundError if the investigation is missing.

    ``loom_embed`` (set only by the publisher, alongside the loom bundle) is a
    ``{"loom": <loom index url>, "state": <composite-state dir url>}`` pair,
    both relative to the report, that turns the Model section into the REAL
    embedded bigraph-loom. Omitting it keeps the report fully self-contained
    (the Model section falls back to the inlined image / static topology)."""
    ws_root = Path(ws_root)
    wp = WorkspacePaths.load(ws_root)
    inv_path = wp.investigations / inv_slug / "investigation.yaml"
    if not inv_path.is_file():
        raise FileNotFoundError(f"investigation.yaml not found for {inv_slug!r}")
    inv = yaml.safe_load(inv_path.read_text(encoding="utf-8")) or {}
    trajs = _discover_trajectories(wp)

    studies = []
    img_budget = [0]
    # Read the member list via the shared helper so BOTH the pre-migration
    # `studies:` key and the post-migration `members:` key render — otherwise a
    # members-only investigation reports zero studies (the generated report's
    # study list comes solely from here). Normalize dict/bare-slug entries.
    for entry in investigation_member_slugs(inv):
        slug = member_slug(entry)
        if not is_plain_name(slug):  # malformed member: skip it (the resolver 400s on a non-plain name)
            continue
        try:
            spec = _load_study_spec(ws_root, slug)
        except (FileNotFoundError, ValueError):
            continue
        real_slug = spec.get("name") or slug
        s = {k: spec.get(k) for k in _STUDY_KEEP if spec.get(k) is not None}
        s["slug"] = real_slug
        s["title"] = spec.get("title") or _humanize(real_slug)   # title is optional in some workspaces
        # Canonical two-axis ontology (REUSED, not re-derived): execution status +
        # scientific verdict from study_ontology — the same functions the live
        # graph + build_iset_detail use. Stashed here on the raw spec BEFORE
        # _build_spine sets the gate-state `verdict` for the DAG, so the synthesis
        # grade reuses the canonical scientific verdict instead of re-mapping.
        try:
            from vivarium_workbench.lib import study_ontology as _onto
            s["execution_status"] = _onto.derive_execution_status(spec, str(spec.get("status") or ""))
            _sv = _onto.derive_verdict(spec)
            if _sv:
                s["sci_verdict"] = _sv
        except Exception:  # noqa: BLE001
            pass
        # "Methods · Simulations" node-card block (locked shared spec, matching the
        # live workbench graph): the baseline composite id + graded-test counts are
        # computed here. `methods`/`fidelity` come from process-bigraph's
        # composite_generator(methods=/fidelity=) field, which lives on the
        # baseline composite's GeneratorEntry (NOT study.yaml): an explicit study
        # override wins, otherwise we resolve off the registered composite. Both
        # absent → the Methods row + fidelity chip omit, data-safe.
        # runs/n_steps/status come straight off s.runs in the template.
        s["baseline_composite"] = _baseline_composite_id(spec)
        s["tests_counts"] = _outcome_counts(spec)
        methods = spec.get("methods")
        fidelity = spec.get("fidelity")
        if methods is None or fidelity is None:
            c_methods, c_fidelity = _composite_methods_fidelity(s["baseline_composite"])
            if methods is None:
                methods = c_methods
            if fidelity is None:
                fidelity = c_fidelity
        if methods is not None:
            s["methods"] = methods
        if fidelity is not None:
            s["fidelity"] = fidelity
        at = _match_traj(trajs, real_slug, "agent")
        pt = _match_traj(trajs, real_slug, "policy")
        if at:
            s["agent_trajectory"] = at
        if pt:
            s["policy_trajectory"] = pt
        # inline study visualizations so the self-contained report shows them:
        #   1. `image:`-addressed raster/vector figures → data-URIs
        #   2. on-disk viz/charts HTML (the committed render of every `local:`
        #      live visualization current workspaces use) → inlined HTML
        study_dir = wp.study_dir(real_slug)
        figs = []
        if spec.get("visualizations"):
            figs.extend(_embed_visualizations(study_dir, spec["visualizations"], img_budget))
        img_basenames = {
            Path(str(v.get("address", ""))[len("image:"):]).name
            for v in (spec.get("visualizations") or [])
            if isinstance(v, dict) and str(v.get("address", "")).startswith("image:")
        }
        figs.extend(_embed_html_figures(study_dir, img_budget, img_basenames))
        # Native Visualizations gallery — the declared visualizations[] rendered to
        # the latest run's .pbg/runs/<run>/viz.json (in-memory HTML, not committed
        # on disk). Modern studies render their RESULT figures here, so without this
        # they were missing from the report. Deduped against the figures already
        # embedded above (by display name).
        embedded_names = {f.get("name") for f in figs if isinstance(f, dict)}
        figs.extend(_embed_native_gallery_panels(ws_root, real_slug, img_budget, embedded_names))
        if figs:
            s["figures_embedded"] = figs
        # resolve the baseline composite's bigraph topology for the Model loom view
        topo = _model_topology(ws_root, spec)
        if topo:
            s["model_topology"] = topo
        # embed a pre-rendered loom image (offline-safe Model view) if the study
        # saved one via `vivarium-workbench render-loom`
        loom_img = _model_loom_img(study_dir, img_budget)
        if loom_img:
            s["model_loom"] = loom_img
        studies.append(s)

    spine = _build_spine(ws_root, inv_slug, studies)
    _synthesis = _build_synthesis(inv, studies, spine)
    # Reuse the EXISTING deterministic needs-attention scan as the report's
    # validation/consistency layer (invariant & conservation-invariant
    # regressions, verdict divergence, reproducibility env-stale/nondeterministic,
    # param drift, phantom observables, uncovered acceptance criteria). Build-free
    # + tolerant; empty when the scan is unavailable.
    try:
        from vivarium_workbench.lib.report_views import build_needs_attention
        _na, _ = build_needs_attention(ws_root, investigation=inv_slug)
        _synthesis["validation"] = _na if isinstance(_na, dict) else {}
    except Exception:  # noqa: BLE001
        _synthesis["validation"] = {"items": [], "summary": {"total": 0, "by_kind": {}, "by_severity": {}}}
    # Also fold in the EXISTING report-readiness linter (report_views.build_report_lint
    # → viva_superpowers.report_linter + the dashboard's composite-resolution,
    # readout-emit-plan, question/approach and visualization-gap checks). Same
    # deterministic infra the live report-lint endpoint uses — reused, not rebuilt.
    try:
        from vivarium_workbench.lib.report_views import build_report_lint
        _lint, _ = build_report_lint(ws_root)
        _synthesis["validation"]["report_lint"] = list((_lint or {}).get("findings", []) or [])
    except Exception:  # noqa: BLE001
        _synthesis["validation"]["report_lint"] = []
    references = _load_references(ws_root, wp)

    return {
        "slug": inv.get("name") or inv_slug,
        "title": inv.get("title") or inv_slug,
        "status": inv.get("status"),
        "question": inv.get("question"),
        "hypothesis": inv.get("hypothesis"),
        "lead": inv.get("lead"),
        "executive": inv.get("executive"),
        "scientific_argument": inv.get("scientific_argument"),
        # Narrative front-matter fields authored in investigation.yaml that the
        # report previously dropped. Each is optional and renders as a graceful
        # no-op when absent (other workspaces won't carry them): the Background /
        # "simulated world" prose, an accessibility glossary, reader guidelines,
        # and an explicit "how to read" note.
        "biological_story": inv.get("biological_story"),
        "glossary": inv.get("glossary"),
        "guidelines": inv.get("guidelines"),
        "how_to_read": inv.get("how_to_read"),
        "created": inv.get("created"),
        "key_claims": inv.get("key_claims"),
        "competing_frameworks": inv.get("competing_frameworks"),
        "at_a_glance": inv.get("at_a_glance"),
        "acceptance_criteria": inv.get("acceptance_criteria"),
        "spine": spine,
        # Deterministic cross-study synthesis (claim-evidence map, stage-grouped
        # integrated findings, contradictions, continuity, roadmap, figure ranking,
        # run-status/evidence/claim separation) — the finding-first report layer.
        # Also carries a `validation` block: the EXISTING deterministic
        # needs-attention scan (invariant/conservation regressions, verdict
        # divergence, reproducibility env-stale/nondeterministic, param drift,
        # phantom observables, uncovered ACs) reused from report_views — not a new
        # checker. Added after the dict literal (see just below).
        "synthesis": _synthesis,
        "references": references,
        "inv_references": inv.get("inputs", {}).get("references") if isinstance(inv.get("inputs"), dict) else None,
        "catalog": inv.get("catalog"),
        "workspace": ws_root.name,
        "provenance": (
            f"investigations/{inv_slug}/investigation.yaml · studies/*/study.yaml · loop-trajectory JSON"
        ),
        "studies": studies,
        **({"loom_embed": loom_embed} if loom_embed else {}),
    }


def _h(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_PLOTLY_PKG_CANDIDATES = ("package_data/plotly.min.js", "plotly.min.js")


def _find_plotly_js(ws_root=None) -> "str | None":
    """The Plotly.js source to inline ONCE in the report so every interactive
    figure shares a single copy (keeping the whole report ~5 MB, not 4.6 MB × N).

    Prefers the workspace's committed ``plotly.min.js`` (the exact build the
    figures were rendered against); falls back to the installed ``plotly``
    package's bundled ``plotly.min.js``. Returns None when neither is available —
    the report still renders, just without live interactive figures. Fully
    guarded: any read/import error yields None."""
    candidates = []
    if ws_root is not None:
        try:
            candidates.append(Path(ws_root) / "plotly.min.js")
        except Exception:
            pass
    try:
        import plotly  # noqa: PLC0415
        pkg = Path(plotly.__file__).resolve().parent
        for rel in _PLOTLY_PKG_CANDIDATES:
            candidates.append(pkg / rel)
    except Exception:
        pass
    for c in candidates:
        try:
            if c and c.is_file():
                return c.read_text(encoding="utf-8")
        except Exception:
            continue
    return None


def _has_interactive_figures(data: dict) -> bool:
    for s in (data.get("studies") or []):
        for f in (s.get("figures_embedded") or []):
            if isinstance(f, dict) and f.get("interactive"):
                return True
    return False


def render_html(data: dict, plotly_js: "str | None" = None) -> str:
    """Embed the report-data dict into the fixed template + renderer.

    ``plotly_js`` (when given) is inlined once as the report's single shared
    Plotly.js, powering every interactive figure. It is neutralized against
    breaking out of its ``<script>`` element."""
    tpl = _TEMPLATE.read_text(encoding="utf-8")
    # escape '<' so the JSON can never break out of the <script> element
    payload = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    title = f"{data.get('title', 'Investigation')} — report"
    html = tpl.replace("__TITLE__", _h(title)).replace("__DATA__", payload)
    pj = plotly_js or ""
    if pj:
        # a Plotly bundle can contain the literal "</script>" inside a string;
        # neutralize it so it can't close the wrapping <script> element early
        pj = pj.replace("</script", "<\\/script")
    # done last so the (large, opaque) bundle can't collide with other tokens
    return html.replace("__PLOTLY_JS__", pj)


class ReportPdfError(RuntimeError):
    """Raised when the PDF render can't run (Playwright/Chromium missing) or fails."""


def render_report_pdf(html: str) -> bytes:
    """Render the self-contained report HTML to a PDF via headless Chromium.

    The report is client-rendered (JS builds the document from the embedded data
    and draws the Plotly figures), so we load it in a real browser, call the
    report's ``__reportPrintPrep()`` hook (expand every study card + force-render
    all lazy figures/looms), let it settle, then capture ``page.pdf()``.

    Uses Playwright — the same optional ``loom-render`` extra ``render-loom``
    already relies on. Raises :class:`ReportPdfError` with install guidance when
    Playwright or its Chromium build is unavailable, so the route can surface a
    clear message instead of a 500.
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:  # noqa: BLE001
        raise ReportPdfError(
            "PDF export needs Playwright (the 'loom-render' extra):\n"
            "  pip install 'vivarium-workbench[loom-render]'\n"
            "  python -m playwright install chromium"
        ) from e
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                # A4-width viewport so the layout measured during __reportPrintPrep
                # (figure fit, graph scale) matches the A4 page.pdf() output — a
                # wide viewport would mis-measure and let figures overflow the page.
                page = browser.new_page(viewport={"width": 794, "height": 1123},
                                        device_scale_factor=2)
                # Self-contained HTML, no network — load (not networkidle, which
                # would wait forever with no requests in flight).
                page.set_content(html, wait_until="load", timeout=60000)
                try:
                    page.evaluate("() => (window.__reportPrintPrep ? window.__reportPrintPrep() : null)")
                except Exception:
                    pass
                page.wait_for_timeout(900)   # let any post-prep figure draws settle
                return page.pdf(
                    print_background=True,
                    prefer_css_page_size=False,
                    format="A4",
                    margin={"top": "14mm", "bottom": "14mm", "left": "12mm", "right": "12mm"},
                )
            finally:
                browser.close()
    except ReportPdfError:
        raise
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "Executable doesn't exist" in msg or "playwright install" in msg:
            raise ReportPdfError(
                "Chromium isn't installed for Playwright:\n"
                "  python -m playwright install chromium"
            ) from e
        raise ReportPdfError(f"PDF render failed: {type(e).__name__}: {msg}") from e


def render_investigation_report(ws_root, inv_slug: str, *, out_dir=None,
                                loom_embed: "dict | None" = None) -> Path:
    """Render one investigation to ``reports/investigation-<slug>.html`` (or
    ``out_dir``) and return the written path.

    ``loom_embed`` (see :func:`build_report_data`) is passed only by the
    publisher, to embed the real loom served alongside the bundle."""
    ws_root = Path(ws_root)
    data = build_report_data(ws_root, inv_slug, loom_embed=loom_embed)
    # inline one shared Plotly.js only when the report actually carries an
    # interactive figure — an image/SVG-only report stays lean
    plotly_js = _find_plotly_js(ws_root) if _has_interactive_figures(data) else None
    html = render_html(data, plotly_js)
    out_dir = Path(out_dir) if out_dir is not None else WorkspacePaths.load(ws_root).reports
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"investigation-{inv_slug}.html"
    out.write_text(html, encoding="utf-8")
    return out
