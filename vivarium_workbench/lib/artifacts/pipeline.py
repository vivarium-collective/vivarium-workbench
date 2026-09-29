"""Deterministic pull-or-compute pipeline resolver.

``resolve_study`` recurses into a study's declared input producers first —
each producer's own ``resolve_study`` call IS the pull-or-compute for that
producer, so a store hit there means no recompute — then resolves this
study's own output artifact, keyed by ``(composite, config, resolved input
ids, workspace commit)`` via ``hashing.artifact_id``. A matching id already in
the ``ArtifactStore`` is pulled (no compute); otherwise ``compute_fn`` (or the
best-effort real-engine adapter, ``_default_compute``) is called exactly once
to produce it, and the result is stored.

Hard constraint: NO ``datetime.now()`` / RNG / wall-clock anywhere in this
file. The whole point of content-addressing is that identical inputs always
resolve to the identical artifact id, so the resolve path must be pure with
respect to everything except the artifact store's on-disk contents.
"""
from __future__ import annotations

import graphlib
import shutil
import sqlite3
import tempfile
from pathlib import Path

import yaml

from vivarium_workbench.lib.artifacts.hashing import artifact_id
from vivarium_workbench.lib.artifacts.store import ArtifactStore
from vivarium_workbench.lib.composite_runs import collect_emit_paths_from_spec
from vivarium_workbench.lib.investigation_members import (
    investigation_member_slugs,
    member_slug,
)
from vivarium_workbench.lib.study_spec import study_interface
from vivarium_workbench.lib.workspace_paths import WorkspacePaths


def _workspace_commit(ws_root) -> str:
    """Current git HEAD of the workspace, or "" when not a git checkout.

    A tmp/non-git workspace (as used by the unit tests) always yields ""
    within a given test — deterministic, not wall-clock-derived.
    """
    import subprocess
    try:
        r = subprocess.run(
            ["git", "-C", str(ws_root), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


class CyclicDependencyError(Exception):
    """Raised when a study's ``inputs[].from`` chain cycles back on itself."""


def _load_study_spec(ws_root: Path, slug: str) -> dict:
    """Load ``studies/<slug>/study.yaml`` (nested-first via WorkspacePaths).

    Mirrors the resolution pattern in
    ``investigation_graph_views.build_investigation_graph``: resolve the
    study dir through ``WorkspacePaths.study_dir`` (which raises
    ``FileNotFoundError`` for an unknown slug — left to propagate, since an
    unresolvable input producer is an authoring error the resolver should
    surface, not swallow).
    """
    wp = WorkspacePaths.load(ws_root)
    spec_path = wp.study_dir(slug, must_exist=True) / "study.yaml"
    return yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}


def _load_investigation_spec(ws_root: Path, inv_slug: str) -> dict:
    """Load ``investigations/<inv_slug>/investigation.yaml``.

    Split out as its own (monkeypatchable) seam — mirrors ``_load_study_spec``
    — so ``resolve_investigation`` tests can fake an investigation's member
    list without needing a real ``investigations/`` dir on disk.
    """
    wp = WorkspacePaths.load(ws_root)
    spec_path = wp.investigations / inv_slug / "investigation.yaml"
    return yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}


def _record_pointer(runs_db: Path, stage: str, oid: str) -> None:
    """Upsert ``(stage, artifact_id)`` into ``runs.db``'s ``artifact_pointers``.

    Additive table only — never touches any existing runs.db table/schema.
    Best-effort: a locked or otherwise misbehaving db must never crash a
    resolve, so any failure here is swallowed.
    """
    try:
        conn = sqlite3.connect(str(runs_db))
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS artifact_pointers ("
                "stage TEXT PRIMARY KEY, artifact_id TEXT NOT NULL)"
            )
            conn.execute(
                "INSERT INTO artifact_pointers (stage, artifact_id) "
                "VALUES (?, ?) "
                "ON CONFLICT(stage) DO UPDATE SET artifact_id=excluded.artifact_id",
                (stage, oid),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — pointer bookkeeping is best-effort
        pass


def _maybe_record_verdict_pointer(db_path: Path, out_dir: Path) -> None:
    """Content-address a run's computed ``verdict.json`` (Phase 2c).

    When ``out_dir/verdict.json`` exists (written by
    ``composite_flush.run_flush``), record a ``("verdict", <content-hash>)``
    pointer into the run's ``runs.db`` ``artifact_pointers`` table. Hashes the
    verdict bytes directly (``hashing.artifact_id`` keys on composite/config,
    not on a produced file's content — there is no content-hash primitive to
    reuse, so a plain sha256 of the bytes is the content id here). No
    ``verdict.json`` -> no-op, and it must NOT create the db (mirrors the
    best-effort posture of ``_record_pointer``).
    """
    vf = Path(out_dir) / "verdict.json"
    if not vf.is_file():
        return
    import hashlib
    try:
        vid = hashlib.sha256(vf.read_bytes()).hexdigest()[:16]
    except Exception:  # noqa: BLE001 — best-effort
        return
    _record_pointer(Path(db_path), "verdict", vid)


def resolve_study(
    ws_root, slug: str, *, compute_fn=None, force: bool = False, _in_progress=None,
) -> dict:
    """Pull-or-compute this study's output artifact, recursing into input
    producers first.

    Returns:
      {
        "slug": slug,
        "output": <str>,          # output artifact name = interface.outputs[0] if present else slug
        "artifact_id": <str>,     # 16-char id of THIS study's output artifact
        "cached": <bool>,         # True => store already had it (compute_fn NOT called for this study)
        "inputs": { <from_slug>: <input_artifact_id>, ... },  # resolved producer output ids
      }

    Args:
      force: when True, bypass the ``store.has(oid)`` short-circuit for THIS
        study (compute_fn always runs; ``cached`` is reported False). Does
        NOT propagate to producer resolution — a caller that wants a whole
        subtree forced (e.g. ``resolve_investigation``) resolves every node
        explicitly with ``force=True`` in topological order, so a producer
        is already force-recomputed by the time a dependent's internal
        recursive call reads it back (a cheap store hit on the same oid).

    Raises:
      CyclicDependencyError: a study's ``inputs[].from`` chain re-enters a
        slug already on the current recursion stack (e.g. a -> b -> a).
        ``_in_progress`` is the private threading mechanism for that guard —
        callers should never pass it themselves.
    """
    in_progress = set() if _in_progress is None else _in_progress
    if slug in in_progress:
        raise CyclicDependencyError(" -> ".join([*in_progress, slug]))
    in_progress.add(slug)
    try:
        ws_root = Path(ws_root)
        wp = WorkspacePaths.load(ws_root)
        spec = _load_study_spec(ws_root, slug)
        iface = study_interface(spec)
        output_name = iface["outputs"][0] if iface["outputs"] else slug

        # Resolve inputs first — recursion IS the pull-or-compute for producers.
        inputs_map: dict[str, str] = {}
        store = ArtifactStore(ws_root)
        resolved_inputs: dict[str, dict] = {}
        for inp in iface["inputs"]:
            child = resolve_study(
                ws_root, inp["from"], compute_fn=compute_fn, _in_progress=in_progress,
            )
            inputs_map[inp["from"]] = child["artifact_id"]
            resolved_inputs[inp["artifact"]] = {
                "path": str(store.path(child["artifact_id"])),
                "into": inp.get("into") or None,
            }

        commit = _workspace_commit(ws_root)
        oid = artifact_id(
            composite_id=iface["composite"] or slug,
            config=iface["config"],
            input_ids=sorted(inputs_map.values()),
            commit=commit,
        )

        if not force and store.has(oid):
            cached = True
        else:
            cached = False
            # Each compute attempt gets its OWN unique scratch dir (never just
            # `oid`) so two concurrent resolves that both miss the same `oid`
            # (e.g. two dependents of the same producer, or a double-clicked
            # rerun in the request-serving dashboard) can't stomp each other —
            # a shared `oid`-named dir would let one writer's pre-compute
            # `rmtree` delete another writer's in-flight scratch mid-compute.
            # The dir name is transient filesystem isolation only: it never
            # enters `artifact_id` and never affects stored content, so this
            # stays fully deterministic — `store.put` is idempotent by default,
            # so if two attempts race, the first to `put` wins and the second
            # is a no-op store hit (force=True passes overwrite=True below,
            # which trades that race-safety for actually refreshing a forced
            # recompute's content instead of discarding it).
            scratch_root = wp.pbg / "_scratch"
            scratch_root.mkdir(parents=True, exist_ok=True)
            scratch = Path(tempfile.mkdtemp(prefix=f"{oid}-", dir=scratch_root))
            try:
                fn = compute_fn or _default_compute
                produced = fn(
                    ws_root, slug,
                    artifact_id=oid,
                    composite=iface["composite"],
                    config=iface["config"],
                    input_ids=sorted(inputs_map.values()),
                    out_dir=scratch,
                    resolved_inputs=resolved_inputs,
                )
                # Record what the address was computed FROM, not just what
                # the artifact is. `meta.json` used to carry `{slug, stage}`
                # only, which is not enough to recompute an address — so when
                # the address formula changed, the store could not re-key
                # itself and a migration had to re-derive every config from
                # the workspace at its current commit (and orphan anything
                # produced at an older one). Storing the address inputs makes
                # the store self-describing: a future formula change is a
                # rehash of recorded values, not an archaeology exercise.
                store.put(
                    oid,
                    produced,
                    {
                        "slug": slug,
                        "stage": output_name,
                        "address_inputs": {
                            "composite_id": iface["composite"] or slug,
                            "config": iface["config"],
                            "input_ids": sorted(inputs_map.values()),
                            "commit": commit,
                        },
                    },
                    overwrite=force,
                )
            finally:
                shutil.rmtree(scratch, ignore_errors=True)

        _record_pointer(wp.study_dir(slug, must_exist=True) / "runs.db", output_name, oid)

        return {
            "slug": slug,
            "output": output_name,
            "artifact_id": oid,
            "cached": cached,
            "inputs": inputs_map,
        }
    finally:
        in_progress.discard(slug)


# Study-config keys that are run-control (carried by the RunRequest), not
# generator parameters — stripped from the generator overrides so
# build_generator does not reject them as unknown parameters.
_RUN_CONTROL_KEYS = ("n_steps",)


def _default_compute(
    ws_root, slug, *, artifact_id, composite, config, input_ids, out_dir,
    resolved_inputs: dict | None = None,
):
    """Real-engine adapter (Spec-1 Global Constraint: reuse run_core.invoke_run /
    run_runner.execute — do NOT reimplement running). This seam is exercised
    end-to-end in Task 8; Task 5's unit tests inject a stub compute_fn instead.

    Best-effort wiring: build a run-request the same shape
    ``run_runner.RunRequest.from_file`` expects, invoke ``run_core.invoke_run``
    to plan it, then hand it to ``run_runner.execute`` to actually run, and
    return ``out_dir`` (which now also holds the run's ``runs.db`` + log +
    any rendered viz) as the artifact payload. Imports are lazy so importing
    this module never pulls in the run subsystem, and unit tests (which
    always inject their own ``compute_fn``) never exercise this path.

    ``resolved_inputs`` (artifact name -> ``{"path": producer store path,
    "into": consumer config key | None}``, from ``resolve_study``, sourced
    from the input edge's declared ``into:`` in ``study.yaml``) is merged
    into ``overrides`` before the request is built: ``overrides[f"{artifact}
    _path"] = path`` for every entry (generic, always set), and
    ``overrides[into] = path`` too when ``into`` is set. ``into`` is the
    general mechanism now — a study author declares e.g. ``into: cache_dir``
    on the input edge to route a producer's path into whatever config key its
    generator expects. When an entry has no ``into`` at all AND its artifact
    is named ``sim_data``, ``cache_dir`` is used as a DOCUMENTED BACK-COMPAT
    DEFAULT (the v2ecoli convention: ``ecoli_baseline`` reads ParCa sim_data
    via ``cache_dir``) — removable once workspaces declare ``into: cache_dir``
    explicitly on that edge. The caller's ``config`` dict is never mutated in
    place.
    """
    import json

    from vivarium_workbench.lib import run_core
    from vivarium_workbench.lib import run_runner

    ws_root = Path(ws_root)
    wp = WorkspacePaths.load(ws_root)
    out_dir = Path(out_dir)
    db_path = out_dir / "runs.db"

    plan = run_core.invoke_run(
        ws_root, spec_id=composite or slug, config=config, db_path=db_path,
    )

    # Load study spec and collect emit_paths from declared observables
    # (readouts, tests, visualizations, etc). Fall back to [] only when
    # the study declares no observables (run_runner then expands [] to all-store).
    spec = _load_study_spec(ws_root, slug)
    emit_paths = collect_emit_paths_from_spec(spec) or []

    # Forward producer artifact paths into this run's overrides — never
    # mutate the caller's config dict in place.
    overrides = dict(config or {})
    # Run-control keys are carried by the RunRequest itself (e.g. n_steps -> the
    # `steps` field below), NOT by the generator. Strip them from the generator
    # overrides so build_generator does not reject them as unknown parameters.
    for _run_control_key in _RUN_CONTROL_KEYS:
        overrides.pop(_run_control_key, None)
    for artifact, info in (resolved_inputs or {}).items():
        path = info["path"]
        overrides[f"{artifact}_path"] = str(path)
        target = info.get("into") or ("cache_dir" if artifact == "sim_data" else None)
        if target:
            overrides[target] = str(path)

    # NOTE (Task 8 integration): the run-request shape below is the best
    # inference available from run_runner.RunRequest — n_steps/emit_paths
    # are now wired from the study spec (Task 4), so `steps` defaults
    # from config (or a placeholder) and `emit_paths` is collected above.
    request = {
        "run_id": plan.run_id,
        "spec_id": plan.spec_id,
        "pkg": wp.package.name,
        "workspace": str(ws_root),
        "overrides": overrides,
        "steps": int((config or {}).get("n_steps") or 1),
        "emit_paths": emit_paths,
        "db_file": str(db_path),
        "log_path": str(out_dir / "run.log"),
        "target": plan.target,
    }
    request_path = out_dir / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    rc = run_runner.execute(request_path)
    if rc != 0:
        raise RuntimeError(
            f"study {slug!r} run failed (exit {rc}); see {out_dir / 'run.log'}"
        )

    # Belt-and-suspenders: `run_runner.execute` returning 0 is the primary
    # success signal, but a run can still leave its own `runs_meta.status`
    # non-`completed` (e.g. a post-run step that caught its own exception and
    # wrote a terminal `failed`/`orphaned` status without propagating a
    # non-zero return). Read the SAME `db_path` this run's request pointed
    # `db_file` at (NOT `lib/composite_run_views.build_composite_run_status`'s
    # `.pbg/composite-runs.db` — that's the workspace-wide db for
    # dashboard-launched runs; this compute's run writes its own scratch
    # `runs.db`, which is what ends up bundled into the returned artifact) —
    # best-effort: an unreadable db / missing table / no matching row is
    # INCONCLUSIVE (never crashes this check), only a definitively
    # non-completed status raises.
    status = _run_terminal_status(db_path, plan.run_id)
    if status is not None and status != "completed":
        raise RuntimeError(
            f"study {slug!r} run failed (status={status!r}); "
            f"see {out_dir / 'run.log'}"
        )
    # Phase 2c: content-address the computed verdict the flush wrote (if any).
    _maybe_record_verdict_pointer(db_path, out_dir)
    return out_dir


def _run_terminal_status(db_path: Path, run_id: str) -> str | None:
    """Best-effort read of ``runs_meta.status`` for ``run_id`` from ``db_path``.

    Returns the status string, or ``None`` when it can't be determined (db
    file / table / matching row absent, or any read error) — this probe must
    never itself raise; ``_default_compute`` only raises on a definitively
    non-``completed`` status, never on ``None`` (inconclusive).
    """
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
        try:
            row = conn.execute(
                "SELECT status FROM runs_meta WHERE run_id = ?", (run_id,)
            ).fetchone()
        finally:
            conn.close()
        return row[0] if row else None
    except Exception:  # noqa: BLE001 — best-effort status probe
        return None


def resolve_investigation(
    ws_root, inv_slug: str, *, compute_fn=None, force: bool = False,
) -> dict:
    """Topological pull-or-compute over an investigation's member DAG.

    Loads ``investigations/<inv_slug>/investigation.yaml``, reads its member
    list via ``investigation_member_slugs``, and builds a producer DAG from
    each member's ``inputs[].from`` (``study_interface``/``_load_study_spec``
    — the same building blocks ``resolve_study`` uses). A ``from`` producer
    that isn't itself a declared member (e.g. a shared upstream study like
    ``parca``) still becomes a node: ``graphlib.TopologicalSorter.add(node,
    *predecessors)`` implicitly adds any predecessor never explicitly added
    as a dependency-free leaf, so it naturally slots into the order ahead of
    its dependents. Each node in the resulting ``static_order()`` is then
    resolved via ``resolve_study`` in order (NOT recursively re-derived here
    — ``resolve_study`` already recurses into producers on its own; walking
    the topo order here just gives each node an explicit, independently
    reported status).

    Returns:
      {
        "order": [slug, ...],      # topological order (members + producers)
        "nodes": [{"slug", "artifact_id": <id>|None,
                   "status": "cached"|"computed"|"skipped"|"failed",
                   "inputs": [from_slug, ...]}, ...],
        "error": None | str,       # set (only) on a cyclic member DAG
      }

    Status rules:
      - Any upstream (``inputs[].from``) already ``failed``/``skipped`` ->
        this node is ``skipped`` without calling ``resolve_study``.
      - ``resolve_study`` raising for this node -> ``failed`` (caught here,
        never propagates), and its descendants become ``skipped``.
      - A cycle in the member DAG -> ``graphlib.CycleError`` is caught,
        ``error`` is set, and no nodes are resolved (mirrors
        ``resolve_study``'s own ``CyclicDependencyError`` guard, but this
        one is over MEMBERS rather than a single study's producer chain).

    ``force=True`` is passed straight through to every ``resolve_study`` call
    so every node in the DAG bypasses its cache and recomputes (see
    ``resolve_study``'s docstring for why this doesn't need to propagate
    into ``resolve_study``'s own internal producer recursion).
    """
    ws_root = Path(ws_root)
    result: dict = {"order": [], "nodes": [], "error": None}

    try:
        inv_spec = _load_investigation_spec(ws_root, inv_slug)
    except Exception as exc:  # noqa: BLE001 — surfaced via result["error"]
        result["error"] = f"cannot load investigation {inv_slug!r}: {exc}"
        return result

    # Discover every node (members + any producer they transitively pull
    # in, even if that producer isn't itself a declared member) and its own
    # `inputs[].from`, building the sorter as we go. The whole discovery
    # pass (including `investigation_member_slugs` itself) is guarded so an
    # unexpected error here becomes `result["error"]`, never a raise —
    # consistent with the "never raises" contract this function documents.
    inputs_by_slug: dict[str, list[str]] = {}
    ts: graphlib.TopologicalSorter = graphlib.TopologicalSorter()
    seen: set[str] = set()
    try:
        member_slugs = investigation_member_slugs(inv_spec)
        queue = [s for s in (member_slug(m) for m in member_slugs) if s]
        while queue:
            slug = queue.pop()
            if slug in seen:
                continue
            seen.add(slug)
            try:
                spec = _load_study_spec(ws_root, slug)
                froms = [inp["from"] for inp in study_interface(spec)["inputs"]]
            except Exception:  # noqa: BLE001 — unresolvable producer; resolve_study handles it
                froms = []
            inputs_by_slug[slug] = froms
            ts.add(slug, *froms)
            queue.extend(froms)

        order = list(ts.static_order())
    except graphlib.CycleError as exc:
        result["error"] = f"cyclic member dependency in investigation {inv_slug!r}: {exc}"
        return result
    except Exception as exc:  # noqa: BLE001 — discovery must never raise out of here
        result["error"] = f"cannot resolve member DAG for investigation {inv_slug!r}: {exc}"
        return result

    result["order"] = order

    failed_or_skipped: set[str] = set()
    for slug in order:
        froms = inputs_by_slug.get(slug, [])
        if any(f in failed_or_skipped for f in froms):
            failed_or_skipped.add(slug)
            result["nodes"].append(
                {"slug": slug, "artifact_id": None, "status": "skipped", "inputs": froms}
            )
            continue
        try:
            r = resolve_study(ws_root, slug, compute_fn=compute_fn, force=force)
        except Exception:  # noqa: BLE001 — per-node failure isolation
            failed_or_skipped.add(slug)
            result["nodes"].append(
                {"slug": slug, "artifact_id": None, "status": "failed", "inputs": froms}
            )
            continue
        status = "cached" if r["cached"] else "computed"
        result["nodes"].append(
            {"slug": slug, "artifact_id": r["artifact_id"], "status": status, "inputs": froms}
        )

    return result
