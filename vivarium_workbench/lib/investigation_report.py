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
    "parent_studies", "pipeline_gate",
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
    """Reduce a study to one of the five verdict states from its computed gate
    evaluator, else its gate_status, else run presence."""
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
        for p in (s.get("parent_studies") or []):
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
        for f in sorted(d.glob("*.html")):
            base = f.name
            if base in _HTML_FIG_SKIP or base in skip_names or base in seen:
                continue
            seen.add(base)
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


def build_report_data(ws_root, inv_slug: str) -> dict:
    """Assemble the pure report-data dict for one investigation from existing
    files only. Raises FileNotFoundError if the investigation is missing."""
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
        if not slug:
            continue
        try:
            spec = _load_study_spec(ws_root, slug)
        except (FileNotFoundError, ValueError):
            continue
        real_slug = spec.get("name") or slug
        s = {k: spec.get(k) for k in _STUDY_KEEP if spec.get(k) is not None}
        s["slug"] = real_slug
        s["title"] = spec.get("title") or _humanize(real_slug)   # title is optional in some workspaces
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
        study_dir = wp.studies / real_slug
        figs = []
        if spec.get("visualizations"):
            figs.extend(_embed_visualizations(study_dir, spec["visualizations"], img_budget))
        img_basenames = {
            Path(str(v.get("address", ""))[len("image:"):]).name
            for v in (spec.get("visualizations") or [])
            if isinstance(v, dict) and str(v.get("address", "")).startswith("image:")
        }
        figs.extend(_embed_html_figures(study_dir, img_budget, img_basenames))
        if figs:
            s["figures_embedded"] = figs
        # resolve the baseline composite's bigraph topology for the Model loom view
        topo = _model_topology(ws_root, spec)
        if topo:
            s["model_topology"] = topo
        # embed a pre-rendered loom image (offline-safe Model view) if the study
        # saved one via `vivarium-workbench render-loom`
        loom_img = _model_loom_img(wp.studies / real_slug, img_budget)
        if loom_img:
            s["model_loom"] = loom_img
        studies.append(s)

    spine = _build_spine(ws_root, inv_slug, studies)

    return {
        "slug": inv.get("name") or inv_slug,
        "title": inv.get("title") or inv_slug,
        "status": inv.get("status"),
        "question": inv.get("question"),
        "hypothesis": inv.get("hypothesis"),
        "lead": inv.get("lead"),
        "executive": inv.get("executive"),
        "at_a_glance": inv.get("at_a_glance"),
        "acceptance_criteria": inv.get("acceptance_criteria"),
        "spine": spine,
        "catalog": inv.get("catalog"),
        "workspace": ws_root.name,
        "provenance": (
            f"investigations/{inv_slug}/investigation.yaml · studies/*/study.yaml · loop-trajectory JSON"
        ),
        "studies": studies,
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


def render_investigation_report(ws_root, inv_slug: str, *, out_dir=None) -> Path:
    """Render one investigation to ``reports/investigation-<slug>.html`` (or
    ``out_dir``) and return the written path."""
    ws_root = Path(ws_root)
    data = build_report_data(ws_root, inv_slug)
    # inline one shared Plotly.js only when the report actually carries an
    # interactive figure — an image/SVG-only report stays lean
    plotly_js = _find_plotly_js(ws_root) if _has_interactive_figures(data) else None
    html = render_html(data, plotly_js)
    out_dir = Path(out_dir) if out_dir is not None else WorkspacePaths.load(ws_root).reports
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"investigation-{inv_slug}.html"
    out.write_text(html, encoding="utf-8")
    return out
