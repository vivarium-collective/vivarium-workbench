"""Discovery-engine cycle trace — the data behind the *Discovery Engine* panel.

The discovery engine has no single function that chains its phases: ``run_cycle``
is the pure core projection, and the A–F / N+2 / growth-loop phases are separate
functions layered on top of its output (each is also its own ``discover`` CLI verb).
This module declares the **canonical ordered step graph** that the CLI only expresses
implicitly, and runs it as one traceable pass: load inputs once, then run each step
in dependency order, timing it and capturing its return value (or error) so the SPA
can show where the cycle is, what ran, and each step's result.

Every step here is a **pure projection** of ``inv_spec`` + ``studies`` (+ the Phase-A
evidence state for the steps that consume it) — none writes to disk. The one mutating
phase, execute/advance (Phase E), runs only via :func:`sandbox_advance`, which copies
the investigation to a throwaway workspace first so the real files are never touched.

discovery_engine is imported lazily inside the functions (it is a git dependency that
may predate the engine on an old install, and the import allowlist expects the lazy
pattern); a missing engine degrades to ``available: False`` rather than raising.
"""
from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# The step graph. Order is the canonical cycle order; `depends_on` records the
# data dependency (a step consumes a prior step's OUTPUT, never a mutation).
# `kind`: "pure" = instant, re-runnable, never writes; "mutating" = runs real
# work + writes (sandboxed only). `phase` groups nodes for the diagram.
# ---------------------------------------------------------------------------
STEPS: list[dict[str, Any]] = [
    {"id": "load_inputs", "label": "Load inputs", "phase": "Inputs",
     "module": "discover", "func": "load_cycle_inputs", "kind": "pure", "depends_on": [],
     "desc": "Reads the investigation + its member studies from disk (YAML only, no database).",
     "gate": ""},
    {"id": "run_cycle", "label": "Core projection", "phase": "Core",
     "module": "discover", "func": "run_cycle", "kind": "pure", "depends_on": ["load_inputs"],
     "desc": "The pure core: derives the argument, verdicts, narrative, gates and frontier "
             "from the authored specs + recorded run outcomes. Generates no prose, writes nothing.",
     "gate": "Authored values win over derived; a plain run never writes to disk."},
    {"id": "propose_viz", "label": "Propose visualizations", "phase": "Core",
     "module": "viz_proposals", "func": "propose_visualizations", "kind": "pure",
     "depends_on": ["run_cycle"],
     "desc": "Proposes figures for gates, discriminating observables and model disagreements "
             "that no declared visualization covers (typed, content-addressed proposals).",
     "gate": "Propose-only: nothing is declared or rendered until a human accepts, with a "
             "rationale. Non-blocking."},
    {"id": "reconcile_evidence", "label": "Reconcile evidence", "phase": "Evidence",
     "module": "evidence_integrity", "func": "reconcile_evidence_state", "kind": "pure",
     "depends_on": ["load_inputs"],
     "desc": "Classifies each study finding by provenance into verified / provisional / "
             "contradicted / unsupported, using the study's runs.db run-ids.",
     "gate": "Verified = a numeric value that cites a registered run-id and passes its own "
             "pass_if band (it does not re-check the run produced that value)."},
    {"id": "plan", "label": "Plan candidate actions", "phase": "Plan",
     "module": "candidate_action", "func": "plan_candidate_actions", "kind": "pure",
     "depends_on": ["run_cycle", "reconcile_evidence"],
     "desc": "Re-casts the open frontier into ranked candidate actions "
             "(repair / resolve / extend) with a heuristic value score.",
     "gate": "A RESOLVE needs ≥2 competing hypotheses; a REPAIR is ordered first when "
             "any evidence is still unverified."},
    {"id": "design", "label": "Design experiment", "phase": "Experiment design",
     "module": "experiment_design", "func": "design_discriminating_study", "kind": "pure",
     "depends_on": ["load_inputs"],
     "desc": "For each study with competing hypotheses, lays out a discriminating "
             "experiment: arms, controls, and a pre-committed decision rule.",
     "gate": "Returns a design only when a study has ≥2 hypotheses whose numeric "
             "prediction bands are disjoint."},
    {"id": "build_model", "label": "Model architect", "phase": "Models",
     "module": "model_architect", "func": "build_catalog", "kind": "pure",
     "depends_on": ["load_inputs"],
     "desc": "Inventories the distinct composite paths the studies' arms cite, with a "
             "capability signature per component.",
     "gate": "Lexical only — it lists cited composite paths; it does not verify a "
             "composite exists or imports."},
    {"id": "evaluate", "label": "Evaluate hypotheses", "phase": "Evaluation",
     "module": "evaluation", "func": "compare_hypotheses", "kind": "pure",
     "depends_on": ["run_cycle", "reconcile_evidence"],
     "desc": "Scores each study's hypotheses from findings + prose and assigns a status "
             "(supported / contested / unresolved / gated).",
     "gate": "'supported' requires net support > 0 AND the study's evidence verified in "
             "Phase A; otherwise 'provisionally-supported'."},
    {"id": "delta", "label": "Discovery delta", "phase": "Evaluation",
     "module": "evaluation", "func": "discovery_delta", "kind": "pure",
     "depends_on": ["evaluate"],
     "desc": "Lists the currently supported/qualified claims, model disagreements, and "
             "anomalies.",
     "gate": "Measured against an empty baseline — a snapshot of current state, not a "
             "change since the last cycle."},
    {"id": "transition", "label": "Transition model", "phase": "Composed model",
     "module": "transition_model", "func": "transition_phase_report", "kind": "pure",
     "depends_on": ["build_model"],
     "desc": "Domain-specific (endosymbiosis): sweeps a hard-coded rule table over "
             "proximity × pump × heredity and tallies the outcome.",
     "gate": "Not a generic step and runs no simulation — the cells are a classification "
             "of a would-be run."},
    {"id": "propose", "label": "Propose studies", "phase": "Growth",
     "module": "discover", "func": "suggest_studies", "kind": "pure",
     "depends_on": ["run_cycle"],
     "desc": "Gathers next-study proposals from followups, audit gaps, open decisions "
             "and divergences.",
     "gate": "Divergence / open-question proposals are held 'awaiting expert'; they carry "
             "no spec until seeded."},
    {"id": "select", "label": "Select for launch", "phase": "Growth",
     "module": "study_seeding", "func": "select_for_launch", "kind": "pure",
     "depends_on": ["propose"],
     "desc": "Applies the autonomy policy to decide which proposals launch vs. are held.",
     "gate": "Default human-in-the-loop holds everything unless explicitly approved or "
             "running autonomously."},
    {"id": "seed", "label": "Seed study (preview)", "phase": "Growth",
     "module": "study_seeding", "func": "build_seeded_study", "kind": "pure",
     "depends_on": ["select"],
     "desc": "Previews a new child study spec from a parent + proposal.",
     "gate": "Preview only — written to disk solely via `seed-study --write`, and only "
             "when run-ready."},
    {"id": "refine", "label": "Refine study (preview)", "phase": "Growth",
     "module": "study_refinement", "func": "refine_study", "kind": "pure",
     "depends_on": ["load_inputs"],
     "desc": "Previews refinements to a study — replicates, parameter sweeps, higher "
             "fidelity.",
     "gate": "Preview only — written to disk solely via `refine-study --write`."},
    {"id": "advance", "label": "Execute / advance", "phase": "Execute",
     "module": "discover", "func": "advance_cycle", "kind": "mutating",
     "depends_on": ["plan"],
     "desc": "The one mutating phase: runs run-ready studies toward closure. In the panel "
             "it runs against a sandboxed copy, never your real files.",
     "gate": "Only 'test' ops on never-run, run-ready studies; dry (reports a would-run) "
             "unless advancing autonomously."},
]

# Steps the analytical trace runs automatically (every pure step). The mutating
# `advance` step is excluded — it runs only through sandbox_advance().
_AUTO = [s["id"] for s in STEPS if s["kind"] == "pure"]
_STEP_BY_ID = {s["id"]: s for s in STEPS}


def engine_available() -> bool:
    try:
        import discovery_engine  # noqa: F401
        from discovery_engine import discover  # noqa: F401
        return True
    except Exception:
        return False


def _n_figures(vc: Any) -> int:
    """Figure count from a ``viz_census`` (scalar buckets only; ``proposals`` is a sub-dict)."""
    if not isinstance(vc, dict):
        return 0
    return sum(int(v or 0) for v in vc.values() if isinstance(v, (int, float)))


def _summarize(step_id: str, result: Any, viz_census: Any = None) -> str:
    """One-line human summary for a node badge, per step shape."""
    try:
        if result is None:
            return "—"
        if step_id == "load_inputs":
            return f"{result.get('n_studies', 0)} studies"
        if step_id == "run_cycle":
            arg = (result.get("argument") or {})
            out = (f"{len(arg.get('claims') or [])} claims · "
                   f"{len(result.get('validations') or [])} validators")
            vc = viz_census or (result.get("cycle_state") or {}).get("viz_census")
            n_fig = _n_figures(vc)
            if n_fig:
                out += f" · {n_fig} figures"
            return out
        if step_id == "propose_viz":
            return f"{len(result.get('proposals') or [])} proposed"
        if step_id == "reconcile_evidence":
            s = result.get("summary") or {}
            return " · ".join(f"{k}:{v}" for k, v in s.items()) or "no evidence"
        if step_id == "plan":
            return f"{len(result.get('actions') or [])} actions"
        if step_id == "design":
            return f"{len(result.get('designs') or [])} designs"
        if step_id == "build_model":
            return f"{len(result.get('catalog') or [])} components"
        if step_id == "evaluate":
            return f"{len(result.get('by_study') or {})} studies evaluated"
        if step_id == "delta":
            return (f"+{len(result.get('added_or_qualified') or [])} · "
                    f"{len(result.get('model_disagreements') or [])} disagreements")
        if step_id == "transition":
            return " · ".join(f"{k}:{v}" for k, v in (result.get("tally") or {}).items()) or "—"
        if step_id == "propose":
            return f"{len(result.get('suggestions') or [])} proposals"
        if step_id == "select":
            return f"{len(result.get('launch') or [])} launch · {len(result.get('held') or [])} held"
        if step_id == "seed":
            return f"{len(result.get('seeded') or [])} seeded"
        if step_id == "refine":
            return f"{len(result.get('refined') or [])} refinable"
        if step_id == "advance":
            return f"{len(result.get('runs') or [])} runs"
    except Exception:
        pass
    return "ok"


def _run_one(step_id: str, ctx: dict, M: Any) -> Any:
    """Execute a single step against the accumulated context. Returns its result
    dict; may raise (the caller records the error on that node)."""
    inv_spec = ctx["inv_spec"]
    studies = ctx["studies"]

    if step_id == "load_inputs":
        return {"inv_slug": ctx["inv_slug"], "n_studies": len(studies),
                "study_slugs": list(studies)}

    if step_id == "run_cycle":
        kw = {}
        if ctx.get("viz_state") is not None:
            kw = {"viz_state": ctx["viz_state"], "viz_catalog": ctx.get("viz_catalog") or []}
        cyc = M.discover.run_cycle(inv_spec, studies, **kw)
        ctx["cyc"] = cyc
        try:  # viz_census lives in build_cycle_state, not the run_cycle bundle
            ctx["viz_census"] = (M.discover.build_cycle_state(cyc, inv_spec) or {}).get("viz_census")
        except Exception:
            ctx["viz_census"] = None
        return cyc

    if step_id == "propose_viz":
        return {"proposals": list((ctx.get("cyc") or {}).get("viz_proposals") or []),
                "viz_catalog_size": len(ctx.get("viz_catalog") or [])}

    if step_id == "reconcile_evidence":
        ev = M.evidence_integrity.reconcile_evidence_state(
            inv_spec, studies, ctx.get("runs_by_study"))
        ctx["ev_state"] = ev
        return ev

    if step_id == "plan":
        actions = M.candidate_action.plan_candidate_actions(
            ctx.get("cyc") or {}, inv_spec, ctx.get("ev_state") or {})
        portfolio = M.candidate_action.select_portfolio(actions)
        return {"actions": actions, "portfolio": portfolio}

    if step_id == "design":
        designs = []
        for slug in studies:
            try:
                d = M.experiment_design.design_discriminating_study(inv_spec, slug)
            except Exception:
                d = None
            if d:
                designs.append(d)
        return {"designs": designs}

    if step_id == "build_model":
        return {"catalog": M.model_architect.build_catalog(studies)}

    if step_id == "evaluate":
        ev_state = ctx.get("ev_state")
        by_study = M.evaluation.compare_hypotheses(inv_spec, studies, ev_state)
        disagree = M.evaluation.model_disagreement_map(inv_spec, studies)
        return {"by_study": by_study, "model_disagreements": disagree}

    if step_id == "delta":
        return M.evaluation.discovery_delta(inv_spec, studies, ctx.get("ev_state"))

    if step_id == "transition":
        return M.transition_model.transition_phase_report(inv_spec, studies)

    if step_id == "propose":
        sugg = M.discover.suggest_studies(ctx.get("cyc") or {}, inv_spec, studies)
        ctx["suggestions"] = sugg
        return {"suggestions": sugg}

    if step_id == "select":
        sel = M.study_seeding.select_for_launch(ctx.get("suggestions") or [])
        ctx["selection"] = sel
        return sel

    if step_id == "seed":
        seeded = []
        sel = ctx.get("selection") or {}
        for i, prop in enumerate(sel.get("launch") or []):
            parent = studies.get(prop.get("parent_study")) or {}
            try:
                spec = M.study_seeding.build_seeded_study(
                    parent, inv_spec, new_slug=f"{prop.get('id', 'seed')}-{i}",
                    proposal=prop)
                seeded.append({"proposal": prop.get("id"),
                               "run_ready_gaps": M.study_seeding.is_run_ready(spec),
                               "spec": spec})
            except Exception as e:  # a proposal missing a usable parent is informative
                seeded.append({"proposal": prop.get("id"), "error": str(e)})
        return {"seeded": seeded, "note": (None if seeded else
                "nothing selected for launch (held under the autonomy policy)")}

    if step_id == "refine":
        refined = []
        for slug, spec in studies.items():
            needs = M.study_refinement.refinement_needs(inv_spec, spec)
            if needs:
                preview, changes = M.study_refinement.refine_study(spec, needs=needs)
                refined.append({"study": slug, "needs": needs, "changes": changes})
        return {"refined": refined}

    raise ValueError(f"unknown step {step_id!r}")


class _Modules:
    """Lazily-imported discovery_engine submodules (all on the import allowlist)."""

    def __init__(self) -> None:
        import importlib
        from discovery_engine import discover
        from discovery_engine import discovery_engine as de
        from discovery_engine import evidence_integrity

        class _Missing:  # a not-yet-shipped engine module: its step errors, others run
            def __init__(self, n): self._n = n
            def __getattr__(self, a):
                raise ImportError(f"discovery_engine.{self._n} unavailable")

        def _opt(n):
            try:
                return importlib.import_module(f"discovery_engine.{n}")
            except ImportError:
                return _Missing(n)
        candidate_action, experiment_design, model_architect, evaluation, \
            transition_model, study_seeding, study_refinement = (
                _opt(n) for n in ("candidate_action", "experiment_design",
                                  "model_architect", "evaluation",
                                  "transition_model", "study_seeding",
                                  "study_refinement"))
        self.discover = discover
        self.de = de
        self.evidence_integrity = evidence_integrity
        self.candidate_action = candidate_action
        self.experiment_design = experiment_design
        self.model_architect = model_architect
        self.evaluation = evaluation
        self.transition_model = transition_model
        self.study_seeding = study_seeding
        self.study_refinement = study_refinement


_CATALOG_CACHE: dict = {}
_CATALOG_TTL_S = 120.0
_CATALOG_TIMEOUT_S = 6.0


def _viz_catalog(ws_root) -> list:
    """Cached (TTL) wrapper: the env worker can be slow or absent, and the trace is
    rebuilt after every panel action, so neither a catalog nor a failure is re-fetched
    on each call."""
    key = str(ws_root)
    hit = _CATALOG_CACHE.get(key)
    if hit and time.monotonic() - hit[0] < _CATALOG_TTL_S:
        return hit[1]
    cat = _fetch_viz_catalog(ws_root)
    _CATALOG_CACHE[key] = (time.monotonic(), cat)
    return cat


def _fetch_viz_catalog(ws_root) -> list:
    """The workspace's registered visualization classes as the engine's ``viz_catalog``:
    ``[{address, name, inputs}]`` (``local:<Class>`` + declared input ports), from the env
    worker's ``viz_classes`` + ``viz_class_inputs``. Any failure (no worker, no workspace
    package) degrades to ``[]`` -> every proposal is ``needs_author``."""
    try:
        from vivarium_workbench.lib.env_worker_pool import get_pool
        pool = get_pool()
        classes = (pool.call(Path(ws_root), "viz_classes", timeout=_CATALOG_TIMEOUT_S) or {}).get("classes") or []
        inputs = (pool.call(Path(ws_root), "viz_class_inputs", timeout=_CATALOG_TIMEOUT_S) or {}).get("inputs") or {}
    except Exception:
        return []
    out = []
    for c in classes:
        if not isinstance(c, dict) or not c.get("address"):
            continue
        name = c.get("name") or str(c["address"]).split(":", 1)[-1]
        out.append({"address": c["address"], "name": name,
                    "inputs": inputs.get(name) or {}})
    return out


def _load_context(ws_root, inv_slug: str, M: _Modules) -> dict:
    _, inv_spec, studies = M.discover.load_cycle_inputs(ws_root, inv_slug)
    runs_by_study: dict = {}
    try:
        runs_by_study = M.discover._load_runs_by_study(Path(ws_root), inv_slug, studies)
    except Exception:
        pass
    viz_state = None
    try:
        viz_state = M.discover.viz_inventory(Path(ws_root), inv_slug, studies)
    except Exception:
        pass  # older engine / unreadable disk: cycle still runs without viz items
    return {"inv_slug": inv_slug, "inv_spec": inv_spec, "studies": studies,
            "runs_by_study": runs_by_study, "viz_state": viz_state,
            "viz_catalog": _viz_catalog(ws_root) if viz_state is not None else []}


def list_cycle_investigations(ws_root) -> list[dict]:
    """The investigations the cycle can actually run on: those with an
    ``investigation.yaml`` on disk (not study-granularity rows). ``[{slug, title}]``,
    sorted by title. Degrades to ``[]`` when the engine/layout can't be resolved."""
    try:
        from vivarium_workbench.lib.workspace_paths import WorkspacePaths
        import yaml
        inv_dir = WorkspacePaths.load(Path(ws_root)).investigations
    except Exception:
        return []
    out: list[dict] = []
    if inv_dir.is_dir():
        for d in sorted(p for p in inv_dir.iterdir() if p.is_dir()):
            yf = d / "investigation.yaml"
            if not yf.is_file():
                continue
            title = d.name
            try:
                spec = yaml.safe_load(yf.read_text(encoding="utf-8")) or {}
                title = spec.get("title") or spec.get("question") or spec.get("name") or d.name
            except Exception:
                pass
            out.append({"slug": d.name, "title": str(title)})
    out.sort(key=lambda r: r["title"].lower())
    return out


def _blocked_trace(inv_slug: str, err: str) -> dict:
    """A trace for an investigation whose inputs couldn't be loaded: the load step
    errors with the reason, the rest are blocked. Never raises — the panel shows
    *why* instead of a blank graph or a 500."""
    nodes: list[dict] = []
    for meta in STEPS:
        if meta["id"] == "load_inputs":
            nodes.append({**meta, "status": "error", "duration_ms": 0,
                          "summary": err, "result": None, "error": err})
        else:
            nodes.append({**meta, "status": "blocked", "duration_ms": 0,
                          "summary": "blocked — inputs failed to load"})
    return {"available": True, "inv_slug": inv_slug, "error": err, "steps": nodes}


def build_cycle_trace(ws_root, inv_slug: str, *, only: list[str] | None = None) -> dict:
    """Run the pure analytical cycle as one traced pass and return per-step results.

    ``only`` restricts which steps RETURN their result (used by single-step re-run);
    the full dependency chain still runs so the restricted step has real upstream
    inputs. Returns ``{available, inv_slug, steps:[...]}`` where each step has
    ``{id,label,phase,module,func,kind,depends_on,status,duration_ms,summary,result,error}``.
    ``status`` is ok | empty | error | blocked.
    """
    if not engine_available():
        return {"available": False, "inv_slug": inv_slug, "steps": []}
    M = _Modules()
    try:
        ctx = _load_context(ws_root, inv_slug, M)
    except FileNotFoundError:
        return _blocked_trace(
            inv_slug, f"no investigation.yaml for {inv_slug!r} "
                      "(this row isn't a cycle-runnable investigation)")
    except Exception as e:
        return _blocked_trace(inv_slug, f"{type(e).__name__}: {e}")
    want = set(only) if only else None
    out_steps: list[dict] = []
    for step_id in _AUTO:
        meta = _STEP_BY_ID[step_id]
        t0 = time.perf_counter()
        status, result, error = "ok", None, None
        try:
            result = _run_one(step_id, ctx, M)  # always run (fills ctx for dependents)
            empty = result is None or (isinstance(result, (list, dict)) and not result)
            # a dict whose only values are empty containers is still "empty" to the eye
            if isinstance(result, dict) and result:
                empty = all(not v for v in result.values()
                            if isinstance(v, (list, dict))) and not any(
                    not isinstance(v, (list, dict)) and v for v in result.values())
            status = "empty" if empty else "ok"
        except Exception as e:
            status, error = "error", f"{type(e).__name__}: {e}"
        dt = round((time.perf_counter() - t0) * 1000, 2)
        node = {**meta, "status": status, "duration_ms": dt,
                "summary": _summarize(step_id, result, ctx.get("viz_census")) if status != "error" else error}
        if want is None or step_id in want:
            node["result"] = result
            node["error"] = error
        out_steps.append(node)
    # The mutating advance node is shown in the graph but never auto-run.
    adv = _STEP_BY_ID["advance"]
    out_steps.append({**adv, "status": "idle", "duration_ms": 0,
                      "summary": "run via Run continuously (sandboxed)"})
    return {"available": True, "inv_slug": inv_slug,
            "summary": _cycle_summary(ctx, M), "steps": out_steps}


# The three frontier-item kinds that block closure (DE `_CLOSURE_BLOCKING`); kept
# as a local copy so this module does not depend on a private engine name.
_BLOCKING_KINDS = frozenset({"unaddressed_criterion", "narrative_gap", "open_challenge"})

# Human-readable one-liners for each frontier-item kind, so the gate inspector can
# say what an open item IS without the reader knowing the engine's vocabulary.
_KIND_LABEL = {
    "unaddressed_criterion": "Acceptance criterion not met",
    "open_challenge": "Unadjudicated challenge",
    "narrative_gap": "Unsupported narrative transition",
    "open_decision": "Decision needed",
    "stale_validation": "Stale validation snapshot",
    "divergence": "Authored verdict diverges from derived",
    "validation_finding": "Validator finding",
    "pending_gate": "Verification debt — verdict met, behavior not run",
    "open_question": "Open question — claim not yet assessed",
    "viz_broken": "Figure declared but not renderable",
    "viz_missing": "Figure not yet rendered",
    "viz_stale": "Figure stale vs latest run",
    "viz_proposed": "Visualization proposed",
}

# The frontier-item kinds the engine lets a human/AI discharge with a resolution
# (mirrors discover.RESOLUTION_KINDS; local copy avoids importing a private-ish name).
_RESOLVABLE_KINDS = frozenset({
    "unaddressed_criterion", "open_challenge", "narrative_gap", "open_decision",
    "divergence", "open_question", "validation_finding", "stale_validation", "pending_gate",
    # viz kinds are actionable in the panel (Build / Apply-fix / Accept-Reject)
    "viz_broken", "viz_missing", "viz_stale", "viz_proposed",
})

# Which study tab a given frontier-item kind is best inspected in, so a jump-in link
# lands the reader where the gate actually lives.
_KIND_TAB = {
    "open_decision": "conclusions", "open_challenge": "conclusions",
    "divergence": "conclusions", "open_question": "conclusions",
    "unaddressed_criterion": "audit", "pending_gate": "audit",
    "validation_finding": "overview", "stale_validation": "overview",
    "narrative_gap": "overview",
    "viz_broken": "visualizations", "viz_missing": "visualizations",
    "viz_stale": "visualizations", "viz_proposed": "visualizations",
}


def _study_label(slug: str) -> str:
    """Campaign studies are referred to as S1..Sn; show that short label on a link."""
    import re
    m = re.match(r"^s(\d+)[-_]", slug or "")
    return ("S" + m.group(1)) if m else (slug or "")


def _studies_for_item(kind: str, target: str, text: str, members: list) -> list:
    """Best-effort: which member studies a frontier item points at, for a jump-in link.
    Direct for ``claim:<slug>`` / study-slug targets; for decisions (whose target is the
    question text) scan the text for member slugs and S-number tokens (the campaign
    refers to members as S1..Sn). Returns ``[{slug, label, tab}]`` (possibly empty)."""
    import re
    found: list = []
    t = str(target or "")
    cand = t[len("claim:"):] if t.startswith("claim:") else t
    if cand in members:
        found.append(cand)
    blob = (t + " " + str(text or "")).lower()
    for m in members:
        if m not in found and m.lower() in blob:
            found.append(m)
    for num in set(re.findall(r"\bs(\d+)\b", blob)):
        for m in members:
            if m not in found and re.match(rf"^s{num}[-_]", m.lower()):
                found.append(m)
    tab = _KIND_TAB.get(kind, "overview")
    return [{"slug": s, "label": _study_label(s), "tab": tab} for s in found]


def _cycle_summary(ctx: dict, M: _Modules) -> dict:
    """The engine's headline verdict + gate state for the panel.

    Answers, from the live engine: is the investigation closed, how big is the open
    frontier and WHAT is on it (each item's kind, the op that would resolve it, and
    its consequence tier — i.e. whether a machine may run it or it needs a human),
    what blocks closure, and how much of the plan is machine-runnable vs. human-only.
    This is what answers 'I ran the cycle, why didn't anything advance?': when
    ``runnable`` is 0 and ``human_decisions`` > 0, there is nothing for Advance to
    execute. Wrapped in try/except so any engine-shape change degrades to an empty
    summary (the panel hides the banner) rather than 500-ing the trace."""
    cyc = ctx.get("cyc") or {}
    inv_spec = ctx["inv_spec"]
    try:
        clo = M.de.closure(cyc, inv_spec)
        na = M.de.next_action(cyc, inv_spec)
        plan = M.de.execute_plan(cyc, inv_spec) if hasattr(M.de, "execute_plan") else []
        runnable = [p for p in plan if (p.get("consequence") or "") != "human-only"]
        consequence = plan[0].get("consequence") if plan else None
        # Join each open frontier item to its execution consequence (execute_plan's
        # `source`/`action_type` == the item's `target`/`op`), so the gate inspector
        # shows, per open item: what it is, what would resolve it, and whether that
        # is machine-runnable or human-only.
        cons_by: dict = {}
        for p in plan:
            cons_by[(p.get("action_type"), p.get("source"))] = p
        front_rows: list[dict] = []
        try:
            items = M.de.frontier(cyc, inv_spec) if hasattr(M.de, "frontier") else []
        except Exception:
            items = []
        members = list(ctx.get("studies") or {})
        props_by = {p.get("id"): p for p in (cyc.get("viz_proposals") or []) if isinstance(p, dict)}
        for p in (inv_spec.get("viz_proposals") or []):
            if isinstance(p, dict):
                props_by.setdefault(p.get("id"), p)
        # viz records -> derivable_fix (an address inferred from a matching file on disk)
        fix_by: dict = {}
        for _r in (cyc.get("validations") or []):
            if isinstance(_r, dict) and _r.get("type") == "visual" and _r.get("scope") == "figure":
                for _e in (_r.get("evidence") or []):
                    if isinstance(_e, dict) and _e.get("derivable_fix"):
                        fix_by[_r.get("target")] = _e["derivable_fix"]
        # authored context for each open decision (executive.decisions_needed),
        # keyed by the question text (== the frontier item's target). This is the
        # reasoning behind the decision — the grounding the AI steer prompt needs.
        dn_ctx: dict = {}
        for d in ((inv_spec.get("executive") or {}).get("decisions_needed") or []):
            if isinstance(d, dict):
                q = d.get("question") or d.get("title") or ""
                if q:
                    dn_ctx[q] = str(d.get("context") or d.get("detail") or "")
        for it in items[:60]:
            kind = it.get("kind")
            p = cons_by.get((it.get("op"), it.get("target"))) or {}
            rationale = str(it.get("rationale") or "")
            front_rows.append({
                "kind": kind,
                "kind_label": _KIND_LABEL.get(kind, kind),
                "target": str(it.get("target") or "")[:200],
                "op": it.get("op"),
                "priority": it.get("priority"),
                "blocking": kind in _BLOCKING_KINDS,
                "consequence": p.get("consequence"),
                "human": (p.get("consequence") or "") == "human-only",
                "rationale": rationale[:320],
                "resolvable": kind in _RESOLVABLE_KINDS,
                "decision": kind == "open_decision",
                "buildable": kind in ("viz_missing", "viz_stale"),
                "declarable": kind == "viz_broken",
                "derivable_fix": fix_by.get(it.get("target")) if kind == "viz_broken" else None,
                "acceptable": kind == "viz_proposed",
                "proposal_id": it.get("target") if kind == "viz_proposed" else None,
                "needs_author": (bool(((props_by.get(it.get("target")) or {}).get("suggested") or {})
                                      .get("needs_author", True)) if kind == "viz_proposed" else False),
                "suggested": ((props_by.get(it.get("target")) or {}).get("suggested")
                              if kind == "viz_proposed" else None),
                "studies": _studies_for_item(kind, it.get("target"), rationale, members),
                "context": (dn_ctx.get(it.get("target"), "")[:500] if kind == "open_decision" else ""),
            })
        return {
            "closed": clo.get("closed"),
            "frontier_size": clo.get("open_items"),
            "next_action": ({"op": na.get("op"), "kind": na.get("kind"),
                             "target": na.get("target"), "consequence": consequence}
                            if na else None),
            "runnable": len(runnable),
            "human_decisions": len(plan) - len(runnable),
            "blocking": {
                "unaddressed_criteria": clo.get("unaddressed_criteria") or [],
                "open_gaps": clo.get("open_gaps") or 0,
                "open_challenges": clo.get("open_challenges") or 0,
            },
            "frontier": front_rows,
        }
    except Exception:
        return {}


def rerun_step(ws_root, inv_slug: str, step_id: str) -> dict:
    """Re-run one step (its dependency chain is recomputed so the result is fresh
    and consistent). Returns the single step node, result included."""
    if step_id not in _STEP_BY_ID:
        return {"error": f"unknown step {step_id!r}"}
    if step_id == "advance":
        return {"error": "advance is mutating; use sandbox_advance (Run continuously)"}
    trace = build_cycle_trace(ws_root, inv_slug, only=[step_id])
    if not trace.get("available"):
        return {"available": False, "error": "discovery_engine not available"}
    for s in trace["steps"]:
        if s["id"] == step_id:
            return {"available": True, "inv_slug": inv_slug, "step": s}
    return {"error": f"step {step_id!r} not produced"}


def _copy_sandbox(ws_root: Path, inv_slug: str, M: _Modules) -> Path:
    """Copy just this investigation + its member studies into a throwaway workspace
    so a mutating advance never touches the real files."""
    from vivarium_workbench.lib.workspace_paths import WorkspacePaths
    ws_root = Path(ws_root)
    wp = WorkspacePaths.load(ws_root)
    sandbox = Path(tempfile.mkdtemp(prefix=f"cycle-sandbox-{inv_slug}-"))
    # workspace.yaml (layout map) so WorkspacePaths resolves the same way in the copy
    for name in ("workspace.yaml",):
        src = ws_root / name
        if src.is_file():
            shutil.copy2(src, sandbox / name)
    # investigation + member studies (incl. runs.db) via the canonical layout
    inv_dir = wp.investigations / inv_slug
    if inv_dir.is_dir():
        dst = sandbox / inv_dir.relative_to(ws_root)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(inv_dir, dst)
    _, _, studies = M.discover.load_cycle_inputs(ws_root, inv_slug)
    for slug in studies:
        sdir = wp.studies / slug
        if sdir.is_dir():
            dst = sandbox / sdir.relative_to(ws_root)
            if not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(sdir, dst)
    return sandbox


def sandbox_advance(ws_root, inv_slug: str, *, max_runs: int = 4) -> dict:
    """Drive the cycle toward closure in a **sandboxed copy** of the investigation —
    the real workspace is never mutated. Advances up to ``max_runs`` run-ready studies
    (Phase E), then reports the frontier before/after and the new closure state.

    Honest about depth: in an environment without the simulation runner, advance_cycle
    reports what it would run (``needs_build``) rather than executing — that is surfaced
    in the result, not faked.
    """
    if not engine_available():
        return {"available": False}
    M = _Modules()
    sandbox = _copy_sandbox(Path(ws_root), inv_slug, M)
    try:
        before = build_cycle_trace(sandbox, inv_slug)
        closed_before = _closed_of(before)
        adv: dict = {}
        adv_error = None
        try:
            adv = M.discover.advance_cycle(sandbox, inv_slug, autonomous=True,
                                           max_runs=max_runs,
                                           render_hook=_render_hook_for(sandbox)) or {}
        except Exception as e:
            adv_error = f"{type(e).__name__}: {e}"
        after = build_cycle_trace(sandbox, inv_slug)
        return {
            "available": True, "inv_slug": inv_slug, "sandboxed": True,
            "max_runs": max_runs,
            "runs": adv.get("runs") or [],
            "needs_build": adv.get("needs_build") or [],
            "frontier_before": adv.get("frontier_before"),
            "frontier_after": adv.get("frontier_after"),
            "closed_before": closed_before,
            "closed_after": _closed_of(after),
            "advance_error": adv_error,
            "after": after,
        }
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


# Engine evaluation verdict (compare_hypotheses) -> the authored study verdict
# vocab (discover.VERDICT_VOCAB). Conservative: only decisive verdicts are written;
# unresolved/gated leave the study's authored verdict untouched (value None = skip).
_VERDICT_MAP = {
    "supported": "supported",
    "provisionally-supported": "qualified",
    "contested": "inconclusive",
    "unresolved": None,
    "gated": None,
}


def _study_path(wp, inv_slug: str, slug: str):
    """Resolve a member study's study.yaml: flat ``studies/<slug>/`` first, then the
    nested ``investigations/<inv>/studies/<slug>/`` layout (mirrors load_cycle_inputs)."""
    flat = wp.studies / slug / "study.yaml"
    if flat.is_file():
        return flat
    nested = wp.investigations / inv_slug / "studies" / slug / "study.yaml"
    if nested.is_file():
        return nested
    return None


def _git_commit(ws_root: Path, dirs: list, message: str) -> dict:
    """Commit the changed research files under ``dirs`` to the workspace git, so an
    apply is auditable and revertible. Best-effort: a non-repo / nothing-staged /
    failure is reported, never raised (the files are written regardless)."""
    import subprocess
    rels = [str(d) for d in dirs if d]
    if not rels:
        return {"committed": False, "reason": "nothing to commit"}
    try:
        subprocess.run(["git", "-C", str(ws_root), "add", "--"] + rels,
                       check=True, capture_output=True, timeout=30)
        st = subprocess.run(["git", "-C", str(ws_root), "status", "--porcelain", "--"] + rels,
                            capture_output=True, text=True, timeout=30)
        if not st.stdout.strip():
            return {"committed": False, "reason": "no changes to commit"}
        subprocess.run(["git", "-C", str(ws_root), "commit", "-m", message, "--"] + rels,
                       check=True, capture_output=True, timeout=30)
        sha = subprocess.run(["git", "-C", str(ws_root), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=30).stdout.strip()
        return {"committed": True, "sha": sha}
    except Exception as e:
        return {"committed": False, "reason": f"{type(e).__name__}: {e}"}


def apply_cycle(ws_root, inv_slug: str, *, scope: str = "all") -> dict:
    """Write the cycle's findings back to the REAL investigation + studies, then commit
    the changed files to the workspace git. This is the opposite of a read-only run —
    it mutates research artifacts, so it is only reached via the panel's explicit Apply.

    Writes, by ``scope``:
      - verdicts: each study's engine-evaluated verdict -> study.yaml (decisive only)
      - edges   (scope 'all'): connect_investigation enriches the builds-on edges
      - state   : persist the cycle_state/next-actions -> investigation.yaml

    Open decisions the engine marks ``human-only`` (steer) are NEVER auto-resolved —
    they are surfaced for the human. Returns a report of exactly what changed + the
    refreshed trace.
    """
    if not engine_available():
        return {"available": False}
    import yaml
    from vivarium_workbench.lib.workspace_paths import WorkspacePaths
    M = _Modules()
    ws_root = Path(ws_root)
    wp = WorkspacePaths.load(ws_root)
    inv_path = wp.investigations / inv_slug / "investigation.yaml"
    if not inv_path.is_file():
        return {"available": True, "inv_slug": inv_slug,
                "error": f"no investigation.yaml for {inv_slug!r}", "report": {}}

    _, inv_spec, studies = M.discover.load_cycle_inputs(ws_root, inv_slug)
    try:
        _vs = M.discover.viz_inventory(ws_root, inv_slug, studies)
    except Exception:
        _vs = None
    cyc = (M.discover.run_cycle(inv_spec, studies, viz_state=_vs, viz_catalog=_viz_catalog(ws_root))
           if _vs is not None else M.discover.run_cycle(inv_spec, studies))
    runs_by: dict = {}
    try:
        runs_by = M.discover._load_runs_by_study(ws_root, inv_slug, studies)
    except Exception:
        pass
    ev_state = M.evidence_integrity.reconcile_evidence_state(inv_spec, studies, runs_by)
    evald = M.evaluation.compare_hypotheses(inv_spec, studies, ev_state)

    report: dict = {"verdicts": [], "edges": {}, "cycle_state": False,
                    "resolutions": [], "skipped": []}

    # 1. verdicts — decisive engine verdicts onto the study cards
    for slug, c in (evald or {}).items():
        status = c.get("status")
        verdict = _VERDICT_MAP.get(status)
        sp = _study_path(wp, inv_slug, slug)
        if verdict is None:
            report["skipped"].append({"study": slug, "reason": f"engine verdict '{status}' not decisive"})
            continue
        if sp is None:
            report["skipped"].append({"study": slug, "reason": "no study.yaml on disk"})
            continue
        try:
            cur = (yaml.safe_load(sp.read_text(encoding="utf-8")) or {}).get("verdict")
        except Exception:
            cur = None
        if cur == verdict:
            report["skipped"].append({"study": slug, "reason": f"already '{verdict}'"})
            continue
        rationale = "discovery cycle: " + status + (
            f" (favored {c.get('favored')})" if c.get("favored") else "")
        try:
            M.discover.set_verdict(sp, verdict, rationale)
            report["verdicts"].append({"study": slug, "from": cur, "to": verdict})
        except Exception as e:
            report["skipped"].append({"study": slug, "reason": f"{type(e).__name__}: {e}"})

    # 2. edges — enrich the builds-on backbone (scope 'all')
    if scope == "all":
        try:
            conn = M.discover.connect_investigation(ws_root, inv_slug)
            report["edges"] = {"enriched": conn.get("enriched", {}),
                               "needs_relate": conn.get("needs_relate", {})}
        except Exception as e:
            report["edges"] = {"error": f"{type(e).__name__}: {e}"}

    # 3. resolutions — only ever machine-resolvable ones; the open decisions here are
    # human-only (steer), which we deliberately leave for the human to resolve.
    try:
        plan = M.de.execute_plan(cyc, inv_spec) if hasattr(M.de, "execute_plan") else []
        human_only = [p for p in plan if (p.get("consequence") or "") == "human-only"]
        report["resolutions"] = {"auto_resolved": 0,
                                 "left_for_human": len(human_only)}
    except Exception:
        report["resolutions"] = {"auto_resolved": 0}

    # 4. persist cycle_state onto the investigation
    if scope in ("all", "state"):
        try:
            M.discover.persist_cycle(inv_path, inv_spec, cyc)
            report["cycle_state"] = True
        except Exception as e:
            report["cycle_state"] = f"error: {type(e).__name__}: {e}"

    # 5. commit just the touched research dirs to the workspace git
    try:
        inv_rel = wp.investigations.relative_to(ws_root)
        stu_rel = wp.studies.relative_to(ws_root)
        commit_dirs = sorted({inv_rel, stu_rel})
    except Exception:
        commit_dirs = [wp.investigations, wp.studies]
    report["commit"] = _git_commit(
        ws_root, commit_dirs, f"discovery cycle: apply findings to {inv_slug}")

    return {"available": True, "inv_slug": inv_slug, "report": report,
            "after": build_cycle_trace(ws_root, inv_slug)}


def _inv_commit_dirs(ws_root: Path) -> list:
    """The investigations dir, workspace-relative when possible, for a git commit."""
    from vivarium_workbench.lib.workspace_paths import WorkspacePaths
    inv = WorkspacePaths.load(ws_root).investigations
    try:
        return [inv.relative_to(ws_root)]
    except Exception:
        return [inv]


def resolve_frontier_item(ws_root, inv_slug: str, kind: str, target: str,
                          rationale: str, *, by: str = "human") -> dict:
    """Discharge ONE open frontier item by recording a resolution onto the REAL
    investigation.yaml, then commit it (auditable + revertible) and return the
    refreshed trace. This is how the panel 'takes a decision' from the menu: the
    item stops being raised on the next cycle. Only the engine's resolvable kinds are
    accepted and a rationale is required (a resolution documents *why*). Never raises —
    a bad kind / missing rationale / write error comes back as ``ok: False``."""
    if not engine_available():
        return {"available": False}
    from vivarium_workbench.lib.workspace_paths import WorkspacePaths
    M = _Modules()
    ws_root = Path(ws_root)
    if kind not in _RESOLVABLE_KINDS:
        return {"available": True, "ok": False, "detail": f"{kind!r} is not a resolvable item"}
    if not (target or "").strip():
        return {"available": True, "ok": False, "detail": "target is required"}
    if not (rationale or "").strip():
        return {"available": True, "ok": False, "detail": "a rationale is required"}
    inv_path = WorkspacePaths.load(ws_root).investigations / inv_slug / "investigation.yaml"
    if not inv_path.is_file():
        return {"available": True, "ok": False, "detail": f"no investigation.yaml for {inv_slug!r}"}
    try:
        rec = M.discover.record_resolution(inv_path, kind, target, rationale, by=by)
    except Exception as e:
        return {"available": True, "ok": False, "detail": f"{type(e).__name__}: {e}"}
    commit = _git_commit(ws_root, _inv_commit_dirs(ws_root),
                         f"discovery cycle: resolve {kind} in {inv_slug}")
    return {"available": True, "ok": True, "resolution": rec, "commit": commit,
            "after": build_cycle_trace(ws_root, inv_slug)}


def _render_hook_for(root):
    """Engine ``render_hook`` that renders one figure in-process via ``render_one``."""
    from vivarium_workbench.lib.viz_build import render_one

    def hook(study_dir, entry, latest):
        return render_one(root, Path(study_dir).name, entry["name"])
    return hook


def build_viz(ws_root, inv_slug: str, target: str) -> dict:
    """Render ONE ``fig:<study>/<name>`` figure through the engine's render branch
    (``execute_action(... render_hook=render_one)``), so the engine's guards apply
    (autonomous-only, resolvable-only, never launders a stale figure as fresh). Never
    raises; returns the action record + refreshed trace."""
    if not engine_available():
        return {"available": False}
    M = _Modules()
    ws_root = Path(ws_root)
    if not str(target or "").startswith("fig:") or "/" not in str(target):
        return {"available": True, "ok": False, "detail": "target must be fig:<study>/<name>"}
    try:
        rec = M.discover.execute_action(ws_root, inv_slug, autonomous=True, target=target,
                                        render_hook=_render_hook_for(ws_root))
    except Exception as e:
        return {"available": True, "ok": False, "detail": f"{type(e).__name__}: {e}"}
    out = (rec or {}).get("outcome") or {}
    ok = (rec or {}).get("status") == "executed"
    return {"available": True, "ok": ok, "action": rec,
            "detail": out.get("detail") or (rec or {}).get("status"),
            "after": build_cycle_trace(ws_root, inv_slug)}


def declare_viz_fix(ws_root, inv_slug: str, target: str, declare: dict, rationale: str) -> dict:
    """Apply a ``viz_broken`` fix (``discover viz --declare``): set address/render/requires
    on the broken entry with a required rationale, then commit. Never raises."""
    if not engine_available():
        return {"available": False}
    M = _Modules()
    ws_root = Path(ws_root)
    try:
        rec = M.discover.declare_viz(ws_root, inv_slug, target, dict(declare or {}), rationale)
    except Exception as e:
        return {"available": True, "ok": False, "detail": f"{type(e).__name__}: {e}"}
    commit = _git_commit(ws_root, _inv_commit_dirs(ws_root),
                         f"discovery cycle: declare {target} in {inv_slug}")
    return {"available": True, "ok": True, "resolution": rec, "commit": commit,
            "after": build_cycle_trace(ws_root, inv_slug)}


def _ensure_viz_proposal_persisted(ws_root: Path, inv_slug: str, vp: str, M: Any) -> None:
    """Proposals are derived (pure) until a cycle persists them; the engine's accept/reject
    act on the ledger. If ``vp`` is not on disk yet, persist the cycle exactly as
    ``discover cycle`` would (engine ``persist_cycle``; the ledger is content-addressed)."""
    inv_path, inv_spec, studies = M.discover.load_cycle_inputs(ws_root, inv_slug)
    if any(isinstance(p, dict) and p.get("id") == vp for p in inv_spec.get("viz_proposals") or []):
        return
    vs = M.discover.viz_inventory(ws_root, inv_slug, studies)
    cyc = M.discover.run_cycle(inv_spec, studies, viz_state=vs, viz_catalog=_viz_catalog(ws_root))
    M.discover.persist_cycle(inv_path, inv_spec, cyc, studies=studies, viz_state=vs)


def _decide_viz_proposal(ws_root, inv_slug: str, proposal: str, rationale: str,
                         verb: str, **kw) -> dict:
    if not engine_available():
        return {"available": False}
    M = _Modules()
    ws_root = Path(ws_root)
    if not str(proposal or "").startswith("vp:"):
        return {"available": True, "ok": False, "detail": "proposal must be a vp:<study>:<hash> id"}
    if not (rationale or "").strip():
        return {"available": True, "ok": False, "detail": "a rationale is required"}
    try:
        _ensure_viz_proposal_persisted(ws_root, inv_slug, proposal, M)
        rec = getattr(M.discover, f"{verb}_viz_proposal")(
            ws_root, inv_slug, proposal, rationale=rationale, **kw)
    except Exception as e:
        return {"available": True, "ok": False, "detail": f"{type(e).__name__}: {e}"}
    commit = _git_commit(ws_root, _viz_commit_dirs(ws_root),
                         f"discovery cycle: {verb} {proposal} in {inv_slug}")
    return {"available": True, "ok": True, "proposal": rec, "commit": commit,
            "after": build_cycle_trace(ws_root, inv_slug)}


def _viz_commit_dirs(ws_root: Path) -> list:
    """Accepting edits a study.yaml as well as investigation.yaml."""
    from vivarium_workbench.lib.workspace_paths import WorkspacePaths
    dirs = list(_inv_commit_dirs(ws_root))
    try:
        st = WorkspacePaths.load(ws_root).studies
        dirs.append(st.relative_to(ws_root) if st.is_relative_to(ws_root) else st)
    except Exception:
        pass
    return dirs


def accept_viz_proposal(ws_root, inv_slug: str, proposal: str, rationale: str,
                        address: str | None = None) -> dict:
    """Accept ONE ``vp:`` proposal (engine ``accept_viz_proposal``): declares a
    ``visualizations[]`` entry (origin viz_proposal) -> next cycle it is ``viz_missing``.
    Rationale required (and an address for a ``needs_author`` proposal). Commits."""
    return _decide_viz_proposal(ws_root, inv_slug, proposal, rationale, "accept",
                                address=(address or "").strip() or None)


def reject_viz_proposal(ws_root, inv_slug: str, proposal: str, rationale: str) -> dict:
    """Terminally reject ONE ``vp:`` proposal (engine ``reject_viz_proposal``). Commits."""
    return _decide_viz_proposal(ws_root, inv_slug, proposal, rationale, "reject")


def ai_steer_decision(ws_root, inv_slug: str, target: str) -> dict:
    """Let the AI drive one open decision via the engine's decider path
    (``execute_action`` with a decider + ``autonomous=True``), which records an
    ai-driven resolution (``by='discovery-ai'``) with the chosen option + rationale.
    The engine stays LLM-free: the AI is an external agent command in
    ``$VIVA_DECIDER_CMD``. When that is not configured this degrades with a clear
    message rather than pretending to decide. Returns the action record + refreshed
    trace. Never raises."""
    if not engine_available():
        return {"available": False}
    M = _Modules()
    ws_root = Path(ws_root)
    if not (target or "").strip():
        return {"available": True, "ok": False, "detail": "target is required"}
    try:
        rec = M.discover.execute_action(ws_root, inv_slug, autonomous=True,
                                        decider=M.discover._env_decider, target=target)
    except Exception as e:
        return {"available": True, "ok": False, "needs_config": True,
                "detail": ("AI steering needs an AI decider configured: set "
                           "$VIVA_DECIDER_CMD to an agent that reads {target, detail} JSON "
                           "and returns {choice, rationale} JSON. "
                           f"({type(e).__name__}: {e})")}
    ok = (rec or {}).get("status") == "executed"
    return {"available": True, "ok": ok, "action": rec,
            "after": build_cycle_trace(ws_root, inv_slug)}


def _closed_of(trace: dict) -> bool | None:
    """Pull the closure flag out of a trace's run_cycle result if present."""
    for s in trace.get("steps", []):
        if s["id"] == "run_cycle" and isinstance(s.get("result"), dict):
            # run_cycle's own bundle doesn't carry closure; recompute cheaply is
            # avoided here — closure is reported by sandbox_advance's adv frontier.
            return None
    return None


# ---------------------------------------------------------------------------
# Investigation top-tab freshness (Verdict + Narrative). Freshness is DERIVED by
# the engine; the only writes are the accept-gated refresh verbs below.
# ---------------------------------------------------------------------------
def investigation_freshness(ws_root, inv_slug: str) -> dict:
    """Per-tab freshness (``{verdict, decisions, narrative, any_expired}``) computed live
    by the engine from the on-disk investigation + member studies. Never raises."""
    if not engine_available():
        return {"available": False}
    try:
        M = _Modules()
        _p, spec, studies = M.discover.load_cycle_inputs(Path(ws_root), inv_slug)
        from discovery_engine.narrative_freshness import investigation_freshness as _fr
        cv = (spec.get("executive") or {}).get("computed_verdict_status")
        return {"available": True, "freshness": _fr(spec, studies, computed_verdict_status=cv)}
    except FileNotFoundError:
        return {"available": True, "freshness": None}
    except Exception as e:
        return {"available": True, "freshness": None, "detail": f"{type(e).__name__}: {e}"}


def _refresh_tab(ws_root, inv_slug: str, verb: str, rationale: str) -> dict:
    if not engine_available():
        return {"available": False, "ok": False, "detail": "discovery engine unavailable"}
    if not (rationale or "").strip():
        return {"available": True, "ok": False, "detail": "a rationale is required"}
    M = _Modules()
    ws_root = Path(ws_root)
    try:
        getattr(M.discover, verb)(ws_root, inv_slug, rationale=rationale)
    except Exception as e:
        return {"available": True, "ok": False, "detail": f"{type(e).__name__}: {e}"}
    commit = _git_commit(ws_root, _inv_commit_dirs(ws_root),
                         f"investigation: {verb} in {inv_slug}")
    return {"available": True, "ok": True, "commit": commit,
            **investigation_freshness(ws_root, inv_slug)}


def accept_verdict(ws_root, inv_slug: str, rationale: str) -> dict:
    """Accept the engine-computed verdict (engine ``accept_verdict``). Rationale required."""
    return _refresh_tab(ws_root, inv_slug, "accept_verdict", rationale)


def refresh_narrative(ws_root, inv_slug: str, rationale: str) -> dict:
    """Re-stamp the Narrative basis (engine ``refresh_narrative``). Rationale required."""
    return _refresh_tab(ws_root, inv_slug, "refresh_narrative", rationale)
