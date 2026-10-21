"""Rerun a recorded/declared run at investigation / study / simulation level.
Thin orchestration over the run subsystem; never overwrites — always a new run."""
from __future__ import annotations

from pathlib import Path

from vivarium_workbench.lib import cli_runs, study_runs, env_fingerprint, study_spec
from vivarium_workbench.lib import run_index
from vivarium_workbench.lib.workspace_paths import WorkspacePaths
from vivarium_workbench.lib.investigation_members import investigation_member_slugs


def resolve_rerun_target(ws_root, run_id):
    """Resolve everything a replay of ``run_id`` needs: origin/study,
    spec_id, the effective (params, n_steps, seed) to launch with, and any
    recorded emitter/emit_paths/runtime.

    ``params``/``n_steps``/``seed`` come from ``run_index.replay_params``/
    ``run_index.row_seed`` — review round 1 (Finding 2) factored this
    derivation out of a byte-for-byte duplicate here so that what a replay
    actually launches with and what ``run_index.find_matching_run`` treats
    as "the same replay key" can never silently drift apart; they are
    LITERALLY the same function call now, not two hand-kept copies.
    """
    db_file, row = cli_runs.find_run(ws_root, run_id)
    if row is None:
        return None
    dbp = Path(db_file)
    wp = WorkspacePaths.load(Path(ws_root))
    studies_root = Path(wp.studies).resolve()
    origin, study = "composite", None
    # study runs.db lives at <studies_root>/<slug>/runs.db
    if dbp.name == "runs.db" and dbp.parent.parent.resolve() == studies_root:
        origin, study = "study", dbp.parent.name

    # Prefer the full replay manifest (Task 2) when present — it carries the
    # FULL effective params (not the delta) plus emitter/emit_paths/runtime,
    # so a rerun reproduces exactly what this run used instead of whatever
    # study.yaml/workspace.yaml currently say. Legacy runs (no manifest) fall
    # back to the delta params_json/n_steps — both handled uniformly by
    # replay_params/row_seed below.
    manifest = run_index._parse_manifest(row)
    params, n_steps = run_index.replay_params(row)
    seed = run_index.row_seed(row)

    if manifest:
        return {
            "run_id": run_id,
            "origin": manifest.get("origin") or origin,
            "study": manifest.get("study") if manifest.get("study") is not None else study,
            "spec_id": manifest.get("spec_id") or row.get("spec_id"),
            "params": params,
            "n_steps": n_steps,
            "emitter": manifest.get("emitter"),
            "emit_paths": manifest.get("emit_paths"),
            "runtime": manifest.get("runtime"),
            "seed": seed,
        }

    return {
        "run_id": run_id, "origin": origin, "study": study,
        "spec_id": row.get("spec_id"),
        "params": params,
        "n_steps": n_steps,
        # Uniform shape with the manifest branch above; legacy runs simply
        # have no recorded emitter/emit_paths/runtime to replay.
        "emitter": None, "emit_paths": None, "runtime": None,
        "seed": seed,
    }


def _sniff_cache_fingerprint(params):
    """Best-effort sniff of a run's ``cache_fingerprint`` from its params —
    the SAME source and contract ``composite_runs.build_run_manifest`` uses
    at stamp time (``params["cache_fingerprint"]`` when it's a plain
    string; ``None`` otherwise or on any failure). Recompute sites
    (``_current_env_id``, ``_flag_env_drift``) must sniff it the same way
    stamp time did, or the recomputed env_id silently diverges from the
    stamped one for any run whose params carry a cache_fingerprint (e.g. a
    v2ecoli/ParCa run) — see reproducible-rerun-spine final review."""
    try:
        sniffed = (params or {}).get("cache_fingerprint")
        if isinstance(sniffed, str):
            return sniffed
    except Exception:  # noqa: BLE001 — best-effort; degrade to no cache_fingerprint
        pass
    return None


def _current_env_id(ws_root, params=None):
    """Best-effort: the env_id a NEW launch would be stamped with right now
    — the same ``env_fingerprint.env_id(env_fingerprint.compute_env(...))``
    call ``_flag_env_drift`` makes post-hoc, computed here pre-launch for
    the retrieve-before-recompute check. Never raises; a failure degrades
    to ``None`` (retrieval is simply skipped — falls through to compute).

    ``params`` — the resolved rerun target's params — is sniffed for
    ``cache_fingerprint`` (see ``_sniff_cache_fingerprint``) and threaded
    into ``compute_env`` so this reconstructs the SAME env_id stamp time
    computed for the original run, rather than one missing the
    cache_fingerprint dimension entirely."""
    cf = _sniff_cache_fingerprint(params)
    try:
        return env_fingerprint.env_id(
            env_fingerprint.compute_env(ws_root=ws_root, cache_fingerprint=cf))
    except Exception:  # noqa: BLE001 — best-effort; no env_id -> skip retrieval
        return None


def run_rerun(ws_root, run_id):
    """Replay a recorded run as a brand-new run, routed by its origin — OR,
    when we already have one, serve a saved artifact instead of recomputing.

    Resolves the replay target (Task 2's ``resolve_rerun_target``, manifest-
    preferred). Before launching anything, checks (reproducible-rerun-spine
    Task 6 / G5) whether a completed run already exists matching the exact
    replay key (spec_id, config, seed) under the environment THIS replay
    would execute in right now (``_current_env_id``) — see
    ``run_index.find_matching_run``. A hit returns ``retrieved: True`` with
    the EXISTING run_id and launches nothing; this is not an overwrite, it's
    serving an already-produced result (the user's ask: "we hold onto
    simulation artifacts ... we may not have to rerun"). A miss (no match,
    the environment has drifted since any matching run completed, or a
    matched run's on-disk artifact was deleted) falls through to the normal
    compute path below, unchanged.

    The compute path forwards the resolved target's inputs VERBATIM to the
    matching launcher — a study-origin run replays via ``study_runs.
    launch_into_study`` (full manifest: spec_id/params/n_steps/seed/emitter/
    emit_paths/runtime), a composite-origin run replays via ``cli_runs.
    run_composite`` (detached subprocess). Never mutates the original run;
    always produces a new one.

    Study-origin replays also pass ``reran_from=run_id`` (the ORIGINAL run's
    id) so the new run's completion tail can call ``verify_reproduction``
    once both runs' ``result_fingerprint``s are stored (Task 4 — this is the
    producer T3 left unwired; see ``composite_subprocess.run_composite_subprocess``'s
    ``reran_from`` handling). Composite-origin replays don't set it yet —
    ``cli_runs.run_composite`` has no ``reran_from``/``seed`` seam; left as a
    follow-up (mirrors T3's own noted composite-origin gap).
    """
    t = resolve_rerun_target(ws_root, run_id)
    if t is None:
        return {"error": f"run not found: {run_id}"}, 404

    current_env_id = _current_env_id(ws_root, t.get("params"))
    if current_env_id:
        try:
            # review round 1 (Finding 1): scope the lookup to THIS run's own
            # owning DB (origin/study) — never a sibling study's, even if its
            # spec_id/config/seed/env_id happen to coincide.
            match = run_index.find_matching_run(
                ws_root, t["spec_id"], t["params"], t.get("seed"), current_env_id,
                origin=t["origin"], study=t.get("study"))
        except Exception:  # noqa: BLE001 — best-effort; a lookup failure -> recompute
            match = None
        if match:
            return {
                "simulation_id": match["run_id"],
                "run_id": match["run_id"],
                "origin": t["origin"],
                "reran": run_id,
                "retrieved": True,
            }, 200

    if t["origin"] == "study":
        resp, status = study_runs.launch_into_study(
            ws_root, t["study"], t["spec_id"], t["params"], t["n_steps"],
            seed=t.get("seed"),
            emitter=t.get("emitter"), emit_paths=t.get("emit_paths"), runtime=t.get("runtime"),
            reran_from=run_id)
    else:
        resp, status = cli_runs.run_composite(
            ws_root, t["spec_id"], steps=t["n_steps"], params=t["params"],
            emit_paths=t.get("emit_paths") or [], detach=True)

    new_run_id = (resp.get("simulation_id") or resp.get("run_id")) if isinstance(resp, dict) else None
    _flag_env_drift(ws_root, original_run_id=run_id, new_run_id=new_run_id, study=t.get("study"),
                     params=t.get("params"))

    if isinstance(resp, dict):
        resp = {**resp, "origin": t["origin"], "reran": run_id, "retrieved": False}
    return resp, status


def _pinned_env_for_study(ws_root, study) -> "str | None":
    """Best-effort read of a study's optional ``pinned_env:`` (Task 5 / G3).

    A ``study.yaml`` may declare ``pinned_env: <env_id>`` to accept a known
    environment drift (e.g. a deliberate package upgrade) without every
    subsequent Reproduce flagging ``env_stale``. Returns ``None`` for a
    composite-origin replay (no ``study``), a missing/unreadable
    ``study.yaml``, or an unset/blank field — never raises."""
    if not study:
        return None
    try:
        sd = study_spec.study_dir(Path(ws_root), study)
        spec_file = study_spec.study_spec_file(sd)
        if not spec_file.is_file():
            return None
        import yaml
        spec = yaml.safe_load(spec_file.read_text(encoding="utf-8")) or {}
        if not isinstance(spec, dict):
            return None
        pinned = spec.get("pinned_env")
        return pinned if isinstance(pinned, str) and pinned.strip() else None
    except Exception:  # noqa: BLE001 — best-effort, never block a rerun
        return None


def _flag_env_drift(ws_root, *, original_run_id, new_run_id, study=None, params=None) -> None:
    """Best-effort env-drift pre-check (reproducible-rerun-spine Task 5 / G3).

    Diffs the ORIGINAL run's recorded ``env_id`` against the CURRENT
    environment — freshly computed via ``env_fingerprint.compute_env`` +
    ``env_fingerprint.env_id``, i.e. the environment the replay just
    executed under. ``params`` (the resolved rerun target's params) is
    sniffed for ``cache_fingerprint`` the same way stamp time did (see
    ``_sniff_cache_fingerprint``) and threaded into ``compute_env`` so this
    reconstructs the SAME env_id stamp time would have computed, instead of
    one missing the cache_fingerprint dimension — otherwise any run whose
    params carry a cache_fingerprint would false-positive as drifted here
    even in an identical environment. When they differ, the new run's
    ``provenance_status`` is set to ``'env_stale'`` via the same
    ``composite_runs.set_provenance_status`` helper ``verify_reproduction``
    uses — UNLESS the study's ``study.yaml`` declares a ``pinned_env:``
    matching the ORIGINAL run's ``env_id`` (an accepted/pinned drift;
    suppressed).

    This is the pre-check counterpart to ``verify_reproduction`` (run later,
    on the completion tail): that comparison is only ever conclusive when
    ``env_id`` is IDENTICAL between the two runs, so the two never fire on
    the same run for the same reason (env differs -> env_stale here; env
    same but fingerprint differs -> nondeterministic there). As a defensive
    belt-and-suspenders measure this still refuses to overwrite an already-
    confirmed ``'nondeterministic'`` verdict.

    Best-effort throughout: a missing run, unreadable study.yaml, or digest
    failure degrades to a no-op — this must never block or fail a rerun.
    """
    if not new_run_id or not original_run_id:
        return
    try:
        from vivarium_workbench.lib import composite_runs as cr

        _orig_db, orig_row = cli_runs.find_run(ws_root, original_run_id)
        if orig_row is None:
            return
        orig_env = orig_row.get("env_id")
        if not orig_env:
            return  # pre-Task-2 run with no recorded env_id — nothing to diff

        cf = _sniff_cache_fingerprint(params)
        current_env_id = env_fingerprint.env_id(
            env_fingerprint.compute_env(ws_root=ws_root, cache_fingerprint=cf))
        if not current_env_id or current_env_id == orig_env:
            return  # no drift

        pinned = _pinned_env_for_study(ws_root, study)
        if pinned and pinned == orig_env:
            return  # drift accepted/pinned — do not stamp env_stale

        new_db, new_row = cli_runs.find_run(ws_root, new_run_id)
        if new_row is None:
            return
        if new_row.get("provenance_status") == "nondeterministic":
            return  # never clobber a confirmed nondeterministic verdict

        conn = cr.connect(new_db)
        try:
            cr.set_provenance_status(conn, run_id=new_run_id, status="env_stale")
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — best-effort; never block a rerun
        pass


def _row_seed(row):
    """A ``runs_meta`` row's first-class replay seed (reproducible-rerun-
    spine Task 4). Thin alias for ``run_index.row_seed`` — the single
    shared derivation (review round 1, Finding 2) also used by
    ``resolve_rerun_target`` and ``run_index.find_matching_run`` — kept
    under this name so existing callers/tests of ``rerun._row_seed`` (e.g.
    ``verify_reproduction`` below) don't need to change."""
    return run_index.row_seed(row)


def verify_reproduction(ws_root, original_run_id, new_run_id) -> dict:
    """Compare ``new_run_id``'s stored ``result_fingerprint`` against
    ``original_run_id``'s (reproducible-rerun-spine Task 3 / G4, Step 6).

    A fingerprint mismatch is only meaningful evidence of nondeterminism when
    the two runs executed under the SAME environment (``env_id``) with the
    SAME ``seed`` — otherwise a different result is expected and comparing
    them would be a false positive. So the comparison is gated:

      1. Both runs must be found (anywhere ``cli_runs.find_run`` looks:
         every study's ``runs.db`` + the workspace ``composite-runs.db``).
      2. Both must have a non-null, EQUAL ``env_id``.
      3. Both must have the same ``seed`` — read from the run's recorded
         manifest's first-class ``seed`` (reproducible-rerun-spine Task 4);
         a manifest-less/pre-Task-4 row falls back to ``params["seed"]``,
         the convention every run stored it under before Task 4 (see
         ``_row_seed`` below — mirrors ``build_run_manifest``'s own
         params-sniffing fallback so old and new rows compare consistently).
      4. Both must have a non-null ``result_fingerprint``.

    Only when all four hold is the comparison "conclusive": equal
    fingerprints -> ``match: True``; different fingerprints -> ``match:
    False`` AND ``new_run_id``'s ``provenance_status`` is set to
    ``'nondeterministic'`` (a confirmed non-reproduction, persisted so the UI
    can flag it later — not merely "we didn't check").

    Any gating failure (run not found, env/seed differ, fingerprint missing)
    returns ``match: None`` — inconclusive, NOT evidence either way — with a
    ``reason`` explaining which precondition failed. ``provenance_status`` is
    only ever touched on a REAL, gated mismatch.

    Returns ``{"match": bool | None, "reason": str}``.
    """
    from vivarium_workbench.lib import cli_runs, composite_runs as cr

    orig_db, orig_row = cli_runs.find_run(ws_root, original_run_id)
    new_db, new_row = cli_runs.find_run(ws_root, new_run_id)
    if orig_row is None or new_row is None:
        missing = original_run_id if orig_row is None else new_run_id
        return {"match": None, "reason": f"run not found: {missing}"}

    orig_env = orig_row.get("env_id")
    new_env = new_row.get("env_id")
    if not orig_env or not new_env or orig_env != new_env:
        return {"match": None,
                "reason": "env_id missing or differs — not a like-for-like "
                          "reproduction, comparison skipped"}

    orig_seed = _row_seed(orig_row)
    new_seed = _row_seed(new_row)
    if orig_seed != new_seed:
        return {"match": None,
                "reason": f"seed differs ({orig_seed!r} vs {new_seed!r}) — "
                          "not a like-for-like reproduction, comparison skipped"}

    orig_fp = orig_row.get("result_fingerprint")
    new_fp = new_row.get("result_fingerprint")
    if not orig_fp or not new_fp:
        return {"match": None,
                "reason": "result_fingerprint missing on one or both runs"}

    if orig_fp == new_fp:
        return {"match": True,
                "reason": "result_fingerprint matches under identical env_id + seed"}

    # Real, gated mismatch: same environment, same seed, different result.
    try:
        conn = cr.connect(new_db)
        try:
            cr.set_provenance_status(conn, run_id=new_run_id, status="nondeterministic")
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — best-effort; the verdict below still returns
        pass
    return {"match": False,
            "reason": "result_fingerprint differs under identical env_id + "
                      "seed — nondeterministic"}


def _investigation_studies(ws_root, investigation):
    """Return an investigation's declared member-study slugs.

    Reads ``investigations/<investigation>/investigation.yaml``'s
    ``studies:`` list directly (resolved via ``WorkspacePaths``) rather than
    ``investigations.load_spec`` — that loader is study-oriented (validates
    a *study* spec's schema), not the investigation-level ``studies:``
    roster. Mirrors ``run_unblocked_views.investigation_run_unblocked``'s
    member-name extraction: each entry is either a bare slug string or a
    dict with a ``study``/``name`` key. Missing file or empty list -> ``[]``.
    """
    import yaml

    wp = WorkspacePaths.load(Path(ws_root))
    inv_yaml = wp.investigations / str(investigation) / "investigation.yaml"
    if not inv_yaml.is_file():
        return []
    try:
        spec = yaml.safe_load(inv_yaml.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return []
    if not isinstance(spec, dict):
        return []
    out = []
    for member in investigation_member_slugs(spec):
        if isinstance(member, str):
            out.append(member)
        elif isinstance(member, dict):
            slug = member.get("study") or member.get("name")
            if slug:
                out.append(slug)
    return out


def _investigation_execution_order(ws_root, investigation, studies):
    """Topological execution order of ``studies`` (this investigation's declared
    members) over the canonical upstream relation — ``pipeline_gate.prerequisites``
    ∪ ``inputs[].from`` — so a member that depends on another is never launched
    before it.

    Delegates to :func:`run_jobs.order_slugs_by_upstreams`, the SAME slug-ordering
    the live "Run unblocked" path and the investigation-as-composite builder use,
    so an investigation can no longer be ordered one way by Run and a different
    way by rerun. (This previously read only the ``inputs.from`` DAG, silently
    dropping the ``pipeline_gate.prerequisites`` that builds like viva-genesis
    actually declare.) ``order_slugs_by_upstreams`` restricts edges to the given
    member set, keeps every member, and degrades to declared order on a cycle;
    this wrapper degrades the same way on any import/read failure — a toposort
    failure must never 500 an investigation rerun.
    """
    try:
        from vivarium_workbench.lib.run_jobs import order_slugs_by_upstreams

        return order_slugs_by_upstreams(list(studies), WorkspacePaths.load(Path(ws_root)))
    except Exception:  # noqa: BLE001 — best-effort; degrade to declared order
        return list(studies)


def _study_upstreams(ws_root, slug) -> list:
    """Study ``slug``'s upstream producer slugs — used for the rerun-batch gating
    in ``rerun_investigation`` below. Delegates to
    :func:`run_jobs.study_upstreams` (``pipeline_gate.prerequisites`` ∪
    ``inputs[].from``), the SAME relation the execution ordering uses, so gating
    and ordering can never disagree about what a study waits on. Never raises — a
    missing/malformed ``study.yaml`` degrades to no upstreams (nothing to gate
    on).
    """
    try:
        from vivarium_workbench.lib.run_jobs import study_upstreams

        return study_upstreams(WorkspacePaths.load(Path(ws_root)), slug)
    except Exception:  # noqa: BLE001 — best-effort; never block a rerun
        return []


def rerun_investigation(ws_root, investigation):
    """Rerun every member study of an investigation's baseline (force),
    executing them in TOPOLOGICAL order over the ``inputs.from`` DAG rather
    than a flat fan-out in declared order (reproducible-rerun-spine Task 7 /
    G2) — a study that consumes another member's output is never launched
    before its producer.

    Order comes from ``_investigation_execution_order`` (best-effort;
    degrades to the investigation's declared member order on any DAG/import
    failure — never 500s the rerun).

    GATING: before launching a study, if any of its declared ``inputs.from``
    upstreams FAILED or was itself SKIPPED earlier in this same batch, the
    study is NOT launched — it's recorded in ``skipped`` with the blocking
    upstream slug(s) instead (this also skips transitively: a study whose
    upstream was skipped is itself recorded as skipped). An upstream outside
    this batch — not a member, or a member this rerun never attempted —
    never blocks; gating only ever looks at what actually happened in this
    run, never at prior history.

    One bad study's exception or non-2xx response is recorded in ``errors``
    rather than aborting the rest of the batch (the original flat fan-out's
    per-study try/except, preserved unchanged).
    """
    studies = _investigation_studies(ws_root, investigation)
    order = _investigation_execution_order(ws_root, investigation, studies)

    launched, skipped, errors = [], [], []
    ok: dict = {}  # slug -> True (launched this batch) / False (failed or skipped)

    for s in order:
        blockers = [u for u in _study_upstreams(ws_root, s) if ok.get(u) is False]
        if blockers:
            skipped.append({"study": s, "blocked_by": blockers})
            ok[s] = False
            continue
        try:
            resp, status = study_runs.run_study_baseline(ws_root, {"study": s})
        except Exception as e:  # noqa: BLE001 — one bad study must not abort the batch
            errors.append({"study": s, "error": str(e)})
            ok[s] = False
            continue
        if status < 300 and (resp or {}).get("run_id"):
            launched.append({"study": s, "run_id": resp["run_id"]})
            ok[s] = True
        else:
            errors.append({"study": s, "error": (resp or {}).get("error", status)})
            ok[s] = False

    return {"investigation": investigation, "order": order, "launched": launched,
            "skipped": skipped, "errors": errors, "count": len(launched)}, 200
