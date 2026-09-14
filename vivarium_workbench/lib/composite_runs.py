"""SQLite-backed persistence for Composite Explorer runs.

Owns `.pbg/composite-runs.db`. Bootstraps the `runs_meta` table (run-level
metadata) alongside the `history` table that `process_bigraph.emitter.SQLiteEmitter`
owns (per-step state rows, partitioned by `simulation_id`).

A run's `simulation_id` and our `run_id` are the same string by convention:
    `<spec_id>__<unix-epoch-int>__<6-hex-chars>`
"""
from __future__ import annotations
import hashlib
import json
import sqlite3
import time
from pathlib import Path

from vivarium_workbench.lib import run_log
from vivarium_workbench.lib import env_fingerprint
from vivarium_workbench.lib.agents0 import resolve_agents0_fallback


_SCHEMA_RUNS_META = """
CREATE TABLE IF NOT EXISTS runs_meta (
    run_id        TEXT PRIMARY KEY,
    spec_id       TEXT NOT NULL,
    label         TEXT,
    params_json   TEXT,
    started_at    REAL NOT NULL,
    completed_at  REAL,
    n_steps       INTEGER,
    status        TEXT NOT NULL,
    sim_name      TEXT
);
"""

_INDEX_RUNS_META = """
CREATE INDEX IF NOT EXISTS idx_runs_meta_spec ON runs_meta(spec_id);
"""


# Nullable columns added to runs_meta after the original 8-column schema.
# `connect()` ALTERs in any that a pre-existing DB is missing. `sim_name`
# predates the detached-runs rework but is migrated through the same path.
_NEW_COLUMNS = {
    "sim_name": "TEXT",
    "pid": "INTEGER",
    "progress_step": "INTEGER",
    "log_path": "TEXT",
    "heartbeat_at": "REAL",
    # Coordinated-generation provenance (expert-feedback A.2). Links this run
    # to one (git_sha, param_set, composite_versions) snapshot so the report
    # can flag panels from an older generation as stale. See
    # vivarium_workbench.lib.generation. Nullable: runs predating the model have NULL
    # and are treated as stale once any generation exists.
    "generation_id": "TEXT",
    # Analysis-tool capability tags derived from the run's emitted stores
    # (see lib/run_capabilities.derive_capabilities). Written best-effort on
    # finalize; a lazy backfill recovers any run that missed it. JSON list.
    "capabilities_json": "TEXT",
    # Native store location for parquet/zarr runs (mirrors the vendored
    # RUNS_META_DDL in run_registry.py, which has carried this column since
    # the dashboard phase). connect()'s migration hadn't been ALTERing it in,
    # so complete_metadata()'s `SELECT emitter_path` silently no-op'd via its
    # broad except — closing that gap here.
    "emitter_path": "TEXT",
    # Complete per-run replay manifest (reproducibility foundation for the
    # rerun feature — see docs/superpowers/specs/2026-07-25-rerun-capability-
    # design.md Part A). JSON-encoded ``build_run_manifest()`` dict: full
    # effective params (not the delta), n_steps, resolved emitter/emit_paths,
    # the study runtime block used, origin, study, pkg, generation_id, and a
    # best-effort code_version. Nullable: legacy runs have NULL and
    # ``rerun.resolve_rerun_target`` falls back to the delta params/n_steps.
    "manifest_json": "TEXT",
    # Sub-status ("phase") of a run that is still `status='running'`: the
    # detached executor advances it through simulate → rendering visualizations →
    # analysis flush so the UI can announce the current stage (the coarse
    # `status` stays "running" until the whole pipeline finishes). Nullable.
    "phase": "TEXT",
    # Reconstructable environment id (reproducible-rerun-spine Task 2 / G1):
    # a 16-hex sha256 digest of the run's ``manifest["env"]`` dict (see
    # lib/env_fingerprint.env_id) — workspace commit, sim package versions/
    # SHAs, uv.lock hash, python/platform, cache fingerprint. Lets two runs'
    # environments be compared with a single string equality check. Nullable:
    # runs predating this column, or whose manifest lacks an env, have NULL.
    "env_id": "TEXT",
    # sha256 digest of this run's declared ``fingerprint_fields`` (reproducible-
    # rerun-spine Task 3 / G4) — see lib/result_fingerprint.fingerprint_run.
    # Computed + stored best-effort at completion (run_runner.execute); NULL
    # for a run that predates this column or whose hashing failed.
    "result_fingerprint": "TEXT",
    # Provenance verdict set by lib/rerun.verify_reproduction when a rerun
    # sharing this run's env_id + seed produced a DIFFERENT result_fingerprint
    # — i.e. a confirmed non-reproduction, not merely "unverified". NULL means
    # "never checked" (not "reproducible"); the only non-NULL value in use is
    # "nondeterministic".
    "provenance_status": "TEXT",
}


def _migrate_runs_meta(conn: sqlite3.Connection) -> None:
    """Add any missing nullable columns to an existing runs_meta table."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(runs_meta)")}
    for name, sqltype in _NEW_COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE runs_meta ADD COLUMN {name} {sqltype}")
    conn.commit()


def write_run_capabilities(conn, run_id: str, tags) -> None:
    """Store a run's capability tags as JSON text in runs_meta."""
    import json
    conn.execute("UPDATE runs_meta SET capabilities_json=? WHERE run_id=?",
                 (json.dumps(list(tags)), run_id))
    conn.commit()


def connect(db_file: str | Path) -> sqlite3.Connection:
    """Open the runs DB, ensure schema + migrations, enable WAL."""
    db_file = Path(db_file)
    db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(_SCHEMA_RUNS_META)
    conn.execute(_INDEX_RUNS_META)
    _migrate_runs_meta(conn)
    conn.commit()
    return conn


def run_with_division(composite, steps: int, chunk: int = 100) -> int:
    """Run ``composite`` up to ``steps`` ticks, stopping cleanly at division.

    v2ecoli single-cell composites signal cell division in one of two ways:
    ``composite.run()`` raises, or ``agents['0']`` is removed from the state.
    The dashboard runs each study as a single generation, so either signal
    means the cell cycle finished — we stop and let the caller gather whatever
    the emitter captured up to that point.

    Running ``steps`` in one ``composite.run(steps)`` call (the old behaviour)
    instead crashed the whole run at division, so any run length that crossed
    the division point failed with a 502. Mirrors the chunked, division-aware
    loop in ``scripts/run_default_baseline.py``. Returns ticks actually run.
    """
    steps = int(steps)
    done = 0
    while done < steps:
        n = min(chunk, steps - done)
        try:
            composite.run(n)
        except Exception:
            break  # division — composite raised
        done += n
        agents = (getattr(composite, "state", None) or {}).get("agents") or {}
        if agents.get("0") is None:
            break  # division — parent agent removed
    return done


def generate_run_id(spec_id: str, params: dict | None = None,
                    now: float | None = None) -> str:
    """Build a deterministic-shape run id: `<spec_id>__<ts>__<hash6>`."""
    ts = int(now if now is not None else time.time())
    payload = json.dumps({"spec_id": spec_id, "params": params or {},
                          "ts": ts}, sort_keys=True)
    short = hashlib.sha1(payload.encode()).hexdigest()[:6]
    return f"{spec_id}__{ts}__{short}"


def _remote_url_to_repo_name(remote_url: str | None) -> str | None:
    """Short repo name from a git remote URL.

    Handles both SSH (``git@github.com:org/repo.git``) and HTTPS
    (``https://github.com/org/repo``) forms; strips a trailing ``.git``.
    Returns None for an empty/unparseable url.
    """
    if not remote_url:
        return None
    tail = remote_url.rstrip("/").rsplit("/", 1)[-1]
    if ":" in tail and "/" not in remote_url.rsplit(":", 1)[-1]:
        # SSH scp-form with no path slash: git@host:repo.git
        tail = tail.rsplit(":", 1)[-1]
    if tail.endswith(".git"):
        tail = tail[:-4]
    return tail or None


def git_repo_identity(ws_root) -> dict:
    """Best-effort ``{git_sha, repo, remote_url}`` for a workspace checkout.

    Every field independently degrades to ``None`` — a directory that isn't a
    git repo, has no ``origin`` remote, or where ``git`` isn't on PATH yields
    ``{"git_sha": None, "repo": <dir name>, "remote_url": None}``. This never
    raises: source provenance must never block a run from being recorded.

    ``repo`` prefers the remote's basename (e.g. ``v2ecoli``); when there's no
    remote it falls back to the checkout directory name so the column still
    shows *something* identifying for a local-only workspace.
    """
    out: dict = {"git_sha": None, "repo": None, "remote_url": None}
    if ws_root is None:
        return out
    import subprocess
    from pathlib import Path as _Path
    try:
        out["repo"] = _Path(str(ws_root)).name or None
    except Exception:  # noqa: BLE001 — best-effort, never fatal
        pass
    try:
        sha = subprocess.run(
            ["git", "-C", str(ws_root), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True, timeout=5,
        ).stdout.strip()
        out["git_sha"] = sha or None
    except Exception:  # noqa: BLE001 — best-effort provenance, never fatal
        pass
    try:
        url = subprocess.run(
            ["git", "-C", str(ws_root), "remote", "get-url", "origin"],
            capture_output=True, text=True, check=True, timeout=5,
        ).stdout.strip()
        if url:
            out["remote_url"] = url
            name = _remote_url_to_repo_name(url)
            if name:
                out["repo"] = name
    except Exception:  # noqa: BLE001 — best-effort provenance, never fatal
        pass
    return out


def build_run_manifest(*, spec_id, params, n_steps, emitter, emit_paths,
                       runtime, origin, study=None, pkg=None,
                       generation_id=None, ws_root=None,
                       cache_fingerprint=None, fingerprint_fields=None,
                       seed=None, declared_environment=None) -> dict:
    """Assemble the canonical per-run replay manifest (spec Part A).

    A complete, self-contained record of everything a rerun needs to
    reproduce this run *exactly*: the FULL effective ``params`` (baseline +
    overrides — NOT the delta), ``n_steps``, the resolved ``emitter``/
    ``emit_paths``, the study ``runtime`` block actually used, and
    provenance (``origin``/``study``/``pkg``/``generation_id``).

    ``code_version`` is best-effort: a git-HEAD lookup on ``ws_root`` and a
    ``pkg`` version lookup, each independently wrapped so a failure (no git
    repo, package not installed/importable, etc.) degrades to ``None``
    rather than raising — this must never block a run from being recorded.

    ``env`` (reproducible-rerun-spine Task 2) is a best-effort
    :func:`env_fingerprint.compute_env` snapshot — never raises, so a
    lookup failure never blocks a run from being recorded. ``cache_fingerprint``
    is threaded through explicitly when the caller already computed one (e.g.
    a bespoke runner script); otherwise it's sniffed from
    ``params["cache_fingerprint"]`` when that's a plain string (v2ecoli's
    ``run_condition_multigen_parquet.cache_fingerprint()`` short hash lands
    there), so callers don't have to pass it separately from ``params``.

    ``fingerprint_fields`` (reproducible-rerun-spine Task 3 / G4) is the
    resolved list of declared fields ``result_fingerprint`` will later hash
    (see ``lib/result_fingerprint.fingerprint_run``). Default, when the
    caller doesn't pass one explicitly: this run's own ``emit_paths`` — both
    call sites already resolve those from the study/composite's declared
    observables at launch (e.g. ``collect_emit_paths_from_spec``), so they
    are exactly "the study's declared observables" the spec calls for.

    ``seed`` (reproducible-rerun-spine Task 4) is the run's first-class
    replay seed. When the caller doesn't pass one explicitly, it's sniffed
    from ``params["seed"]`` (the pre-Task-4 convention — every existing
    caller already stashes the seed there as a regular generator param), so
    a manifest built by an un-updated call site still gets a non-null
    ``seed`` rather than silently regressing to the old null placeholder.

    ``environments`` (dual-engine comparison W1, docs/dual-engine-comparison.md
    §3.1) is the multi-entry environment-pin list. Entry shape:
    ``{role, repo, ref, commit, remote_url, lockfile_hash}``. The **primary**
    entry — the workspace env this run actually executed in — is always
    present, derived from the same best-effort repo identity as
    ``code_version`` plus the workspace's ``uv.lock`` hash. When the study
    condition DECLARES an environment (``declared_environment={repo, ref}``,
    see ``study_spec.condition_environment``), a second entry with role
    ``declared`` records the intent — ``commit``/``lockfile_hash`` are null
    until a dispatch path (W4/W5) resolves and executes it. A comparison's
    compare node will carry one entry per engine.
    """
    repo = git_repo_identity(ws_root)
    git_sha = repo.get("git_sha")

    pkg_version = None
    if pkg:
        try:
            from importlib.metadata import version as _pkg_version
            pkg_version = _pkg_version(pkg)
        except Exception:  # noqa: BLE001 — best-effort provenance, never fatal
            pkg_version = None

    cf = cache_fingerprint
    if cf is None:
        sniffed = (params or {}).get("cache_fingerprint")
        if isinstance(sniffed, str):
            cf = sniffed

    run_seed = seed
    if run_seed is None:
        run_seed = (params or {}).get("seed")
    try:
        env = env_fingerprint.compute_env(ws_root=ws_root, cache_fingerprint=cf)
    except Exception:  # noqa: BLE001 — best-effort provenance, never fatal
        env = None

    # Multi-entry environment pins (dual-engine W1). Primary = the workspace
    # env this run executes in; best-effort like every provenance field.
    lock_hash = None
    if ws_root is not None:
        try:
            from vivarium_workbench.lib.provenance_manifest import lockfile_hash
            lock_hash = lockfile_hash(Path(str(ws_root)))
        except Exception:  # noqa: BLE001 — best-effort provenance, never fatal
            lock_hash = None
    environments = [{
        "role": "primary",
        "repo": repo.get("repo"),
        "ref": None,
        "commit": git_sha,
        "remote_url": repo.get("remote_url"),
        "lockfile_hash": lock_hash,
    }]
    if declared_environment:
        environments.append({
            "role": "declared",
            "repo": declared_environment.get("repo"),
            "ref": declared_environment.get("ref"),
            # Filled when a W4 dispatch has resolved the declaration
            # (comparison_pinning.resolve_comparison_pair threads the resolved
            # commit + simulator_id through the caller's declared_environment);
            # null otherwise — honest that the run declared the env but ran in
            # `primary` unresolved.
            "commit": declared_environment.get("commit"),
            "simulator_id": declared_environment.get("simulator_id"),
            "remote_url": None,
            "lockfile_hash": None,
        })

    return {
        "version": 2,
        "spec_id": spec_id,
        "params": dict(params or {}),
        "n_steps": int(n_steps) if n_steps is not None else None,
        "emitter": emitter,
        "emit_paths": list(emit_paths or []),
        "runtime": dict(runtime or {}),
        "origin": origin,
        "study": study,
        "pkg": pkg,
        "generation_id": generation_id,
        "code_version": {
            "git_sha": git_sha,
            "package": pkg_version,
            # Repo identity of the workspace checkout the run launched from
            # (source-provenance). ``repo`` is a short human name (remote
            # basename, else the checkout dir name); ``remote_url`` is the
            # origin remote (None when the checkout has no remote). Both are
            # best-effort — a workspace that isn't a git repo yields None.
            "repo": repo.get("repo"),
            "remote_url": repo.get("remote_url"),
        },
        # v2 keys (reproducible-rerun-spine Task 1): filled in by later tasks
        # (Task 2 = env [now populated above], Task 3 = fingerprint_fields
        # [now populated below] + result_fingerprint [computed post-hoc at
        # completion, see run_runner.execute — stays null here since no
        # result exists yet at launch], Task 4 = first-class seed [now
        # populated above via ``run_seed``]). ``result_fingerprint`` is
        # present as null so a manifest's shape is stable across the
        # migration and consumers can rely on the key existing rather than
        # probing for it.
        "env": env,
        "seed": run_seed,
        # Multi-entry environment pins (dual-engine W1, additive like
        # code_version.repo in #868 — no manifest version bump; readers .get()).
        "environments": environments,
        "fingerprint_fields": (
            list(fingerprint_fields) if fingerprint_fields is not None
            else list(emit_paths or [])
        ),
        "result_fingerprint": None,
    }


def save_metadata(conn, *, spec_id, run_id, params, label, started_at,
                  n_steps, log_path=None, generation_id=None,
                  workspace=None, emitter=None, study_slug=None,
                  investigation_slug=None, origin="local", manifest=None):
    """Insert a run row (status='running') and, if ``workspace`` is given,
    append a 'started' event to the JSONL run log (durable metadata).

    ``manifest`` (optional) is the complete replay manifest built by
    :func:`build_run_manifest`; stored verbatim as JSON in ``manifest_json``
    so ``rerun.resolve_rerun_target`` can replay this run exactly rather than
    reconstructing it from the override-delta ``params_json``/``n_steps``.

    ``env_id`` (reproducible-rerun-spine Task 2) is derived from
    ``manifest["env"]`` via :func:`env_fingerprint.env_id` and stored
    alongside — best-effort: a manifest with no ``env`` (legacy caller) or a
    digest failure leaves the column ``NULL`` rather than raising.

    Source-provenance guarantee: when a caller passes ``workspace`` but no
    ``manifest`` (the remote-landing and ad-hoc-investigation paths), a
    best-effort manifest is built here from the args already in hand so EVERY
    recorded run carries ``code_version`` (repo + commit + package). This is
    the single choke point that makes the Runs table's Source column
    always-filled without every call site having to build a manifest itself.
    """
    if manifest is None and workspace is not None:
        try:
            manifest = build_run_manifest(
                spec_id=spec_id, params=params, n_steps=n_steps,
                emitter=emitter, emit_paths=[], runtime={}, origin=origin,
                study=study_slug, generation_id=generation_id,
                ws_root=workspace,
            )
        except Exception:  # noqa: BLE001 — best-effort, never block a run
            manifest = None
    env_id_val = None
    if manifest and manifest.get("env") is not None:
        try:
            env_id_val = env_fingerprint.env_id(manifest["env"])
        except Exception:  # noqa: BLE001 — best-effort, never block a run
            env_id_val = None
    conn.execute(
        "INSERT INTO runs_meta "
        "(run_id, spec_id, label, params_json, started_at, status, "
        " n_steps, log_path, progress_step, generation_id, manifest_json, env_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)",
        (run_id, spec_id, label, json.dumps(params or {}),
         started_at, "running", n_steps, log_path, generation_id,
         json.dumps(manifest) if manifest else None, env_id_val),
    )
    conn.commit()
    if workspace is not None:
        run_log.append_run_event(workspace, {
            "run_id": run_id, "event": "started", "spec_id": spec_id,
            "label": label, "started_at": started_at, "status": "running",
            "n_steps": n_steps, "emitter": emitter, "origin": origin,
            "study_slug": study_slug, "investigation_slug": investigation_slug,
            # Persist the run's generator params so the reproduction config
            # survives the JSONL fold (build_simulations_data) — previously it
            # lived only in runs_meta.params_json and was dropped by the fold.
            "params": params or {},
        })


def complete_metadata(conn, *, run_id, n_steps, status, workspace=None,
                      emitter_path=None):
    """Mark a run completed/failed; mirror the terminal event to the JSONL log.

    ``emitter_path`` (when the caller knows where this run emitted — the
    canonical ``<study>/runs.<run_id>.zarr`` store) is recorded into
    ``runs_meta.emitter_path`` at completion. Nothing else on the composite-run
    path wrote this column, so it stayed NULL and any reader needing the store
    (comparison_cards' run-store resolution, capability derivation below) had to
    rely on a lazy on-read backfill — which the SYNCHRONOUS per-study verdict
    path, reading immediately after completion, does not get. Recording it here
    makes the store discoverable eagerly.
    """
    completed_at = time.time()
    if emitter_path is not None:
        conn.execute(
            "UPDATE runs_meta "
            "SET completed_at=?, n_steps=?, status=?, emitter_path=? WHERE run_id=?",
            (completed_at, n_steps, status, str(emitter_path), run_id),
        )
    else:
        conn.execute(
            "UPDATE runs_meta "
            "SET completed_at=?, n_steps=?, status=? WHERE run_id=?",
            (completed_at, n_steps, status, run_id),
        )
    conn.commit()
    if status == "completed":
        try:
            from vivarium_workbench.lib.run_capabilities import derive_capabilities
            row = conn.execute(
                "SELECT emitter_path FROM runs_meta WHERE run_id=?", (run_id,)
            ).fetchone()
            store = row[0] if row else None
            if store:
                write_run_capabilities(conn, run_id, derive_capabilities(store, run_id))
        except Exception:  # noqa: BLE001 — best-effort; lazy backfill is the safety net
            pass
    if workspace is not None:
        run_log.append_run_event(workspace, {
            "run_id": run_id,
            "event": "completed" if status == "completed" else "failed",
            "completed_at": completed_at, "n_steps": n_steps, "status": status,
        })


def delete_run(conn: sqlite3.Connection, *, run_id: str) -> bool:
    """Explicitly delete a run's metadata row. Returns True if a row was removed.
    (Store artifacts under .pbg/runs/<run_id>/ are removed by the caller that
    knows the workspace root.)"""
    cur = conn.execute("DELETE FROM runs_meta WHERE run_id=?", (run_id,))
    conn.commit()
    return cur.rowcount > 0


def query_run_meta(conn: sqlite3.Connection, *, run_id: str) -> dict | None:
    """Return the runs_meta row for one run as a dict, or None if absent.

    Includes ``env_id``/``result_fingerprint``/``provenance_status``
    (reproducible-rerun-spine Task 2/3) so callers resolving a run purely by
    id (``cli_runs.find_run`` -> ``rerun.verify_reproduction``) can compare
    two runs' provenance without a bespoke SELECT.
    """
    row = conn.execute(
        "SELECT run_id, spec_id, label, params_json, started_at, completed_at, "
        "n_steps, status, pid, progress_step, log_path, heartbeat_at, "
        "generation_id, manifest_json, phase, env_id, result_fingerprint, "
        "provenance_status "
        "FROM runs_meta WHERE run_id=?",
        (run_id,),
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    try:
        d["params"] = json.loads(d.pop("params_json") or "{}")
    except json.JSONDecodeError:
        d["params"] = {}
    return d


def update_progress(conn: sqlite3.Connection, *, run_id: str,
                    progress_step: int, heartbeat_at: float) -> None:
    """Advance the live progress counter + heartbeat for a running run."""
    conn.execute(
        "UPDATE runs_meta SET progress_step=?, heartbeat_at=? WHERE run_id=?",
        (progress_step, heartbeat_at, run_id),
    )
    conn.commit()


def set_pid(conn: sqlite3.Connection, *, run_id: str, pid: int) -> None:
    """Record the detached child PID once it has been spawned."""
    conn.execute("UPDATE runs_meta SET pid=? WHERE run_id=?", (pid, run_id))
    conn.commit()


def set_phase(conn: sqlite3.Connection, *, run_id: str, phase: "str | None") -> None:
    """Set the running run's sub-status ("phase") + heartbeat, so a poller sees
    which stage (simulate / rendering visualizations / analysis flush) is live.
    Best-effort: a missing `phase` column (very old DB not yet migrated) is
    swallowed so a phase update never breaks a run."""
    try:
        conn.execute(
            "UPDATE runs_meta SET phase=?, heartbeat_at=? WHERE run_id=?",
            (phase, time.time(), run_id),
        )
        conn.commit()
    except Exception:  # noqa: BLE001 — phase is advisory; never fail the run
        pass


def set_result_fingerprint(conn: sqlite3.Connection, *, run_id: str,
                           fingerprint: "str | None") -> None:
    """Store this run's ``result_fingerprint`` (reproducible-rerun-spine
    Task 3 / G4). Best-effort: a missing column (very old, unmigrated DB) or
    any other write failure is swallowed — a hashing/storage problem must
    never fail an otherwise-completed run."""
    try:
        conn.execute(
            "UPDATE runs_meta SET result_fingerprint=? WHERE run_id=?",
            (fingerprint, run_id),
        )
        conn.commit()
    except Exception:  # noqa: BLE001 — best-effort, never fail the run
        pass


def set_provenance_status(conn: sqlite3.Connection, *, run_id: str,
                          status: "str | None") -> None:
    """Set this run's ``provenance_status`` (e.g. ``'nondeterministic'``),
    written by ``lib.rerun.verify_reproduction`` on a confirmed env+seed-
    matched fingerprint mismatch. Best-effort, same rationale as
    :func:`set_result_fingerprint`."""
    try:
        conn.execute(
            "UPDATE runs_meta SET provenance_status=? WHERE run_id=?",
            (status, run_id),
        )
        conn.commit()
    except Exception:  # noqa: BLE001 — best-effort, never fail the run
        pass


def query_all_runs(conn: sqlite3.Connection) -> list[dict]:
    """All runs in this DB, newest first (any spec_id)."""
    cur = conn.execute(
        "SELECT * FROM runs_meta ORDER BY started_at DESC")
    cols = [d[0] for d in cur.description]
    out = []
    for row in cur.fetchall():
        d = dict(zip(cols, row))
        try:
            d["params"] = json.loads(d.pop("params_json") or "{}")
        except json.JSONDecodeError:
            d["params"] = {}
        out.append(d)
    return out


def mark_orphaned(conn: sqlite3.Connection, *, run_id: str,
                  workspace=None) -> None:
    """Mark a run whose process died without writing a terminal status.

    Mirrors the terminal state to the JSONL log when ``workspace`` is given.
    Without that mirror the fold keeps the run's last logged status
    (``running``) and — since the JSONL wins over sqlite in
    ``simulations_index`` — a killed run reads "running" forever, surviving
    every restart because reconciliation only ever touched sqlite.
    """
    completed_at = time.time()
    conn.execute(
        "UPDATE runs_meta SET status='orphaned', completed_at=? WHERE run_id=?",
        (completed_at, run_id),
    )
    conn.commit()
    if workspace is not None:
        run_log.append_run_event(workspace, {
            "run_id": run_id, "event": "orphaned",
            "completed_at": completed_at, "status": "orphaned",
        })


def mark_cancelled(conn: sqlite3.Connection, *, run_id: str,
                   workspace=None) -> None:
    """Mark a run the user stopped from the UI (issue #754).

    Mirrors :func:`mark_orphaned` but records the deliberate terminal state
    ``cancelled`` (distinct from ``orphaned``, which reconciliation assigns to a
    run whose process died on its own). Mirrors the terminal event to the JSONL
    log when ``workspace`` is given, since the JSONL fold wins over sqlite in
    ``simulations_index`` — without the mirror a stopped run reads ``running``
    forever.
    """
    completed_at = time.time()
    conn.execute(
        "UPDATE runs_meta SET status='cancelled', completed_at=? WHERE run_id=?",
        (completed_at, run_id),
    )
    conn.commit()
    if workspace is not None:
        run_log.append_run_event(workspace, {
            "run_id": run_id, "event": "cancelled",
            "completed_at": completed_at, "status": "cancelled",
        })


PRUNE_KEEP = 20


def prune_runs(conn: sqlite3.Connection, *, spec_id: str,
               keep: int = PRUNE_KEEP) -> int:
    """Delete all but the newest ``keep`` runs for ``spec_id``.

    Removes both the runs_meta rows and their history rows. Returns the
    number of runs deleted.
    """
    rows = conn.execute(
        "SELECT run_id FROM runs_meta WHERE spec_id=? "
        "ORDER BY started_at DESC", (spec_id,),
    ).fetchall()
    stale = [r[0] for r in rows[keep:]]
    if not stale:
        return 0
    placeholders = ",".join("?" * len(stale))
    has_history = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='history'"
    ).fetchone()
    if has_history:
        conn.execute(
            f"DELETE FROM history WHERE simulation_id IN ({placeholders})",
            stale,
        )
    conn.execute(
        f"DELETE FROM runs_meta WHERE run_id IN ({placeholders})", stale,
    )
    conn.commit()
    return len(stale)


def query_runs(conn: sqlite3.Connection, *, spec_id: str) -> list[dict]:
    """List runs for one spec_id, newest first."""
    rows = conn.execute(
        "SELECT run_id, spec_id, label, params_json, started_at, "
        "completed_at, n_steps, status, generation_id FROM runs_meta "
        "WHERE spec_id=? ORDER BY started_at DESC",
        (spec_id,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["params"] = json.loads(d.pop("params_json") or "{}")
        except json.JSONDecodeError:
            d["params"] = {}
        out.append(d)
    return out


#: Hard cap on how many trajectory frames a single read returns. A run with more
#: steps is stride-decimated down to this many evenly-spaced frames (the final
#: frame is always kept). Bounds both the JSON-decode cost and the response size
#: so loading a long run's trajectory never blocks the loom — the scrubber does
#: not need every step's full state to be useful. Per-step reads (query_run_state)
#: remain exact for anyone who needs a specific frame.
DEFAULT_MAX_TRAJECTORY_ROWS = 1500


def query_run(conn: sqlite3.Connection, *, run_id: str,
              max_rows: int | None = DEFAULT_MAX_TRAJECTORY_ROWS) -> list[dict]:
    """Return the trajectory `[{step, time, state}, ...]` for one run.

    Reads from the `history` table owned by process_bigraph.emitter.SQLiteEmitter.
    If that table doesn't exist yet (no SQLiteEmitter has ever written to this
    DB), returns an empty list.

    When the run has more than ``max_rows`` steps the result is stride-decimated
    to that many evenly-spaced frames (final frame always included), so a very
    long run never forces a multi-GB, minutes-long read. Pass ``max_rows=None``
    for the full, undecimated trajectory.
    """
    has_history = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='history'"
    ).fetchone()
    if not has_history:
        return []
    # Phase 1: cheap — pull just the step ints to decide which frames to keep.
    steps = [r[0] for r in conn.execute(
        "SELECT step FROM history WHERE simulation_id=? ORDER BY step ASC", (run_id,)
    ).fetchall()]
    if not steps:
        return []
    if max_rows and len(steps) > max_rows:
        stride = (len(steps) + max_rows - 1) // max_rows   # ceil
        kept = steps[::stride]
        if kept[-1] != steps[-1]:
            kept.append(steps[-1])                          # always keep the final frame
    else:
        kept = steps
    # Phase 2: load full state ONLY for the kept frames (bounded json.loads).
    qmarks = ",".join("?" * len(kept))
    rows = conn.execute(
        f"SELECT step, global_time AS time, state FROM history "
        f"WHERE simulation_id=? AND step IN ({qmarks}) ORDER BY step ASC",
        (run_id, *kept),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["state"] = json.loads(d["state"]) if d["state"] else {}
        except json.JSONDecodeError:
            d["state"] = {}
        out.append(d)
    return out


def query_run_state(conn: sqlite3.Connection, *, run_id: str,
                    step: int) -> dict | None:
    """Return the single state dict at one step, or None if missing."""
    has_history = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='history'"
    ).fetchone()
    if not has_history:
        return None
    row = conn.execute(
        "SELECT state FROM history WHERE simulation_id=? AND step=?",
        (run_id, step),
    ).fetchone()
    if not row or not row["state"]:
        return None
    try:
        return json.loads(row["state"])
    except json.JSONDecodeError:
        return None


def auto_label(overrides: dict) -> str:
    """Build a short human-readable label from non-default override values.

    Returns ``'defaults'`` when *overrides* is empty, otherwise a
    comma-separated ``key=value`` string of the sorted items, truncated to 80
    characters so it fits neatly in the dashboard.
    """
    if not overrides:
        return "defaults"
    parts = [f"{k}={v}" for k, v in sorted(overrides.items())]
    return ", ".join(parts)[:80]


def inject_sqlite_emitter(state: dict, *, run_id: str,
                          db_file: str | Path, subsample: int = 1) -> dict:
    """Return a copy of `state` with a SQLiteEmitter step appended.

    The injected step consumes the same input ports declared by the first
    ``_type='step'`` entry whose ``address`` ends with ``Emitter`` (case-
    insensitive) — so the SQLiteEmitter captures the same observables the
    spec's primary emitter already declared.

    **2026-05-19 — empty-inputs fix (v2ecoli friction #1, deeper finding).**
    Spec-level ``_type`` does NOT control Step-vs-Process scheduling;
    ``find_instance_paths`` uses Python ``isinstance`` against the loaded
    class, and ``SQLiteEmitter`` extends ``Step``. A Step only re-fires when
    ``trigger_steps`` sees overlap between just-updated paths and the step's
    wired ``inputs``. With ``inputs={}`` the SQLiteEmitter fired exactly
    once at construction and never again, leaving ``runs.db`` with 1–2
    history rows per run no matter how long the sim ran (this broke every
    comparative visualization downstream).

    Fix: when the candidate-scan finds no spec emitter to mirror, default
    ``inputs`` to ``{"global_time": ["global_time"]}``. Every Process
    ``update`` advances ``global_time``, so that path lands in
    ``update_paths`` and ``trigger_steps`` re-enqueues the SQLiteEmitter
    once per composite apply (cadence ≈ composite tick rate). The state
    payload is whatever ``config.emit`` declares — empty by default, which
    still gives one history row per tick so callers can verify cadence.

    A future iteration may walk the composite recursively and inject the
    SQLiteEmitter as a sibling of every nested spec emitter — the higher-
    fidelity fix. Or upstream may grow ``SQLiteEmitterProcess(Process)`` so
    a periodic-interval emitter is expressible as a Process and the
    impedance mismatch goes away.

    Idempotent: a second call with the same run_id is a no-op.
    """
    db_file = Path(db_file)
    if "sqlite_emitter" in state:
        existing = state["sqlite_emitter"]
        cfg = existing.get("config", {})
        if (cfg.get("simulation_id") == run_id
                and cfg.get("file_path") == str(db_file.parent)
                and cfg.get("db_file") == db_file.name):
            return dict(state)

    emit_schema: dict = {}
    inputs: dict = {}
    # The user_emitter (added by inject_emitter_for_paths) carries the explicit
    # emit selection — prefer it over a composite's own emitter, which would
    # otherwise win simply by appearing earlier in iteration order.
    candidates = []
    if isinstance(state.get("user_emitter"), dict):
        candidates.append(state["user_emitter"])
    candidates.extend(v for k, v in state.items() if k != "user_emitter")
    for node in candidates:
        if not isinstance(node, dict):
            continue
        if node.get("_type") != "step":
            continue
        addr = node.get("address", "")
        # Match case-insensitively so kebab-case addresses register too —
        # `local:ram-emitter` should be picked up the same as
        # `local:RAMEmitter` (mem3dg-readdy friction #24). Case-sensitive
        # `endswith("Emitter")` silently skipped the workspace's RAM
        # emitter, the SQLiteEmitter then installed with empty emit:/inputs:,
        # and runs.db filled up with state={} rows that broke every viz.
        if not addr.lower().endswith("emitter"):
            continue
        emit_schema = dict((node.get("config") or {}).get("emit") or {})
        inputs = dict(node.get("inputs") or {})
        break

    # SQLiteEmitter joins file_path (directory) + db_file (filename) via
    # os.path.join, so we must split the absolute path accordingly.
    # (db_file is already a Path from the top of this function.)
    # v2ecoli friction #1 (deeper finding): a Step with empty `inputs`
    # never re-fires — `trigger_steps` has nothing to match against. When
    # the scan above found no spec emitter to mirror, fall back to
    # wiring `global_time` so every Process apply re-enqueues us.
    if not inputs:
        inputs = {"global_time": ["global_time"]}

    new_state = dict(state)
    new_state["sqlite_emitter"] = {
        # _type: "step" is the truth — SQLiteEmitter extends Step in
        # process-bigraph. Spec-level _type does not influence scheduling;
        # see the docstring for why `inputs` is the actual lever.
        "_type": "step",
        "address": "local:SQLiteEmitter",
        "config": {
            "emit": emit_schema,
            "file_path": str(db_file.parent),
            "db_file": db_file.name,
            "simulation_id": run_id,
            # Downsample the loom trajectory: persist one history row every
            # `subsample` ticks. 1 = every tick (unchanged); >1 keeps the DB
            # bounded on long whole-cell runs without losing the shape.
            "subsample": max(1, int(subsample)),
        },
        "inputs": inputs,
    }
    return new_state


def inject_declared_emitter(state: dict, *, spec_id: str, run_id: str,
                            out_dir: str | Path) -> tuple[dict, str | None]:
    """If the composite for ``spec_id`` declares a default emitter, append it
    to ``state`` as a ``declared_emitter`` step. Returns ``(new_state, kind)``
    where ``kind`` is e.g. ``"parquet"``, or ``(state, None)`` unchanged when
    nothing is declared (pure — never mutates the input ``state``).

    Resolution goes through ``viva_superpowers.composite_generator`` — the
    same module every other ``spec_id`` lookup in this codebase uses
    (``_REGISTRY.get(spec_id)`` for the generator entry; see
    ``run_runner.py``, ``composite_flush.py``, ``observables_views.py``).
    The declared ``emitters=[...]`` come from ``emitter_defaults(entry)``;
    node construction (emit-schema from ``paths``, parquet ``out_dir`` /
    ``partitioning_keys``/``metadata`` wiring) is delegated to
    ``install_default_emitters`` — the convention's own installer, already
    used for parquet by ``vivarium_workbench.lib.emitters.run_with_emitter``
    — so this stays in lockstep with the rest of the codebase instead of
    re-deriving that logic. Only the first declared emitter is honored
    (matches the single ``declared_emitter`` node this function produces);
    a composite declaring more than one default emitter would need a richer
    interface than this task's tuple return.
    """
    try:
        from process_bigraph.composite_generator import (
            _REGISTRY, discover_generators, install_default_emitters)
    except Exception:
        return state, None
    if not _REGISTRY:
        discover_generators()
    entry = _REGISTRY.get(spec_id)
    if entry is None:
        return state, None
    # install_default_emitters returns state unchanged (no "emitter" key)
    # when nothing is declared, so the node==None check below covers that
    # case without a separate emitter_defaults(entry) truthiness call.
    installed = install_default_emitters({}, entry, run_id=run_id, out_dir=out_dir)
    node = installed.get("emitter")
    if node is None:
        return state, None
    new_state = dict(state)
    new_state["declared_emitter"] = node
    address = node.get("address", "")
    if address.lower().endswith("parquetemitter"):
        kind = "parquet"
    else:
        kind = address.split(":")[-1].lower().replace("emitter", "") or "custom"
    return new_state, kind


def inject_emitter_for_paths(state: dict, explicit_paths: list[str]) -> dict:
    """Inject a RAMEmitter step that captures the user-selected store paths.

    ``explicit_paths`` is a list of '/'-joined path strings (e.g.
    ``['stores/level', 'stores/fields']``). For each explicit path, this
    function walks the state tree under that path and collects every
    leaf-ish store node (anything that isn't a dict with
    ``_type='process'`` or ``_type='step'``). The resulting set is used to
    build the emitter's ``config.emit`` schema and ``inputs`` wiring.

    The injected emitter is named ``user_emitter``; idempotent on re-call —
    a second call with the same path set is a no-op.

    Subsequent ``inject_sqlite_emitter()`` will then copy this emitter's
    schema + inputs onto the SQLiteEmitter for persistence.
    """
    if not explicit_paths:
        return state

    leaves = _collect_emit_leaves(state, explicit_paths)
    if not leaves:
        return state

    def _resolve(parts):
        node = state
        for p in parts:
            if not isinstance(node, dict):
                return None
            node = node.get(p)
        return node

    emit_schema: dict = {}
    inputs: dict = {}
    for path_parts in sorted(leaves, key=lambda p: tuple(p)):
        # Slug-safe port name from the path
        key = "_".join(path_parts) if path_parts else "root"
        # A place-graph subtree keeps its tree[node] type so the emitter
        # tree_copies the WHOLE structure each tick (topology preserved for a
        # stepping viewer). Everything else uses process-bigraph's permissive
        # "node" leaf type (see emitter.anyize_paths); "any" trips a
        # bigraph-schema bug in append_link_path that assumes a dict schema.
        node = _resolve(path_parts)
        emit_schema[key] = "tree[node]" if _is_node_tree(node) else "node"
        inputs[key] = list(path_parts)

    new_state = dict(state)
    new_state["user_emitter"] = {
        "_type": "step",
        "address": "local:RAMEmitter",
        "config": {"emit": emit_schema},
        "inputs": inputs,
    }
    return new_state


def inject_analysis_parquet_emitters(
    state: dict, *, run_id: str, out_dir: str,
    roots: tuple[str, ...] = ("bulk", "listeners"),
) -> dict:
    """Install one hive-partitioned ParquetEmitter PER AGENT for native-analysis
    consumption.

    The v2ecoli native analyses (``v2ecoli/workflow/analyses/``) read a DuckDB
    ``read_parquet(hive)`` history whose columns are agent-relative and
    ``__``-flattened (``listeners__mass__dry_mass``, ``bulk__count``) and whose
    hive layout carries the ``variant / lineage_seed / generation / agent_id``
    partition columns that ``analysis_runner.build_cell_records`` selects.

    The composite's *declared* top-level emit paths cannot produce that shape for
    a multi-agent composite (baseline nests every store under ``agents/<id>/``),
    so the declared ParquetEmitter captures only ``global_time``.  This installs
    a ParquetEmitter per agent, **rooted at that agent** (emit key ``listeners``
    wired to ``agents/<id>/listeners`` → column root ``listeners``, which the
    emitter flattens with ``__``), with the partition metadata synthesized.

    Single-run scoped: only agents present at run start get an emitter, and a
    one-off Composite Explorer run is ``variant=0 / lineage_seed=0 /
    generation=1``.  Replaces any prior ``analysis_parquet_*`` nodes so a re-call
    is idempotent.
    """
    agents = state.get("agents") or {}
    if not isinstance(agents, dict) or not agents:
        return state
    new_state = {
        k: v for k, v in state.items()
        if not (isinstance(k, str) and k.startswith("analysis_parquet_"))
    }
    for i, (agent_id, agent_state) in enumerate(agents.items()):
        if not isinstance(agent_state, dict):
            continue
        emit_schema: dict = {"global_time": "node"}
        inputs: dict = {"global_time": ["global_time"]}
        for r in roots:
            if r in agent_state:
                emit_schema[r] = "node"
                inputs[r] = ["agents", str(agent_id), r]
        new_state[f"analysis_parquet_{i}"] = {
            "_type": "step",
            "address": "local:ParquetEmitter",
            "config": {
                "out_dir": str(out_dir),
                "emit": emit_schema,
                "partitioning_keys": [
                    "experiment_id", "variant", "lineage_seed",
                    "generation", "agent_id",
                ],
                "metadata": {
                    "experiment_id": run_id,
                    "variant": 0,
                    "lineage_seed": 0,
                    "generation": 1,
                    "agent_id": str(agent_id),
                },
            },
            "inputs": inputs,
        }
    return new_state


def _readout_observables(rr) -> list[str]:
    """Underlying observable path(s) a resolved readout needs emitted.

    Adds the *parent* array/scalar for each readout kind, not the selected
    element — the whole vector is emitted (self-describing via #1's id-coord)
    and ``RunReader.select`` picks the element at read time:

      - ``scalar``      → the dotted observable path.
      - ``element``     → the parent array observable (``bulk`` for bulk_id,
                          ``listeners.monomer_counts`` for literal_index /
                          monomer_id / …); the index_by value is resolved later.
      - ``expression``  → each operand's observable: ``bulk`` for bulk_id
                          operands, the dotted path for scalar operands.

    ``rr`` is a ``ResolvedReadout``; only its public dataclass fields are read.
    """
    out: list[str] = []
    if rr.kind in ("scalar", "element"):
        if rr.observable:
            out.append(rr.observable)
    elif rr.kind == "expression":
        for op in (rr.operand_ids or []):
            ib = op.get("index_by") or {}
            if ib.get("type") == "bulk_id":
                out.append("bulk")
            else:  # scalar operand → the dotted path is the value/token
                out.append(ib.get("value") or op.get("token"))
    return out


def collect_emit_paths_from_spec(spec: dict) -> list[str]:
    """Collect observable paths declared by a v4 study yaml, for emitter setup.

    Threaded into ``inject_emitter_for_declared_paths`` so the injected emitter
    captures the study's biology, not just ``_tick``. Sources:
      - ``readouts[].store_path``                       — v2ecoli explicit
                                                          per-readout paths
      - ``readouts[]`` resolved via ``readout_resolver`` — canonical/legacy
                                                          ``identifier:`` /
                                                          ``index_by:`` readouts
                                                          (the real dnaa studies)
      - ``tests[].measure.path``                        — per-test observables
      - ``behavior_tests[].measure.path``               — legacy v3 fallback
      - ``visualizations[].inputs_map.*`` / ``.config.inputs_map.*``
      - ``comparative_visualizations[].observable_path`` — multi-run overlays

    Dotted paths are normalised to slash form. Each path is ALSO emitted in its
    per-agent form (``agents/0/<path>``): v2ecoli single-cell composites scope
    listener stores under ``agents.0.``, so the agent-scoped variant is the one
    that actually carries data; the literal variant is kept too for non-agent
    composites. Returns a sorted, deduped list.
    """
    def _norm(p):
        if isinstance(p, str):
            return p.replace(".", "/") if p else None
        if isinstance(p, (list, tuple)):
            joined = "/".join(str(x) for x in p if x is not None)
            return joined or None
        return None

    paths: set[str] = set()
    for r in (spec.get("readouts") or []):
        if not isinstance(r, dict):
            continue
        p = _norm(r.get("store_path"))
        if p:
            paths.add(p)
    # Canonical/legacy readouts (identifier: / index_by:) carry no usable
    # store_path, so the loop above misses every real dnaa study. Resolve them
    # to their underlying observables and add the array/scalar that must be
    # emitted so RunReader.select can pick the element at read time. Imported
    # defensively: an older viva_superpowers without the resolver simply yields
    # no readout-driven additions (the dashboard still works).
    try:
        from viva_superpowers.readout_resolver import (
            resolve_study_readouts, ResolvedReadout,
        )
    except ImportError:
        resolve_study_readouts = None
    if resolve_study_readouts is not None:
        for rr in resolve_study_readouts(spec).values():
            if not isinstance(rr, ResolvedReadout):
                continue  # UnresolvedReadout → never fabricate a path
            for obs in _readout_observables(rr):
                p = _norm(obs)
                if p:
                    paths.add(p)
    for t in (spec.get("tests") or []) + (spec.get("behavior_tests") or []):
        if not isinstance(t, dict):
            continue
        m = t.get("measure") or {}
        p = _norm(m.get("path"))
        if p:
            paths.add(p)
    for v in (spec.get("visualizations") or []):
        if not isinstance(v, dict):
            continue
        for im_loc in (v.get("inputs_map"),
                       (v.get("config") or {}).get("inputs_map")):
            if isinstance(im_loc, dict):
                for val in im_loc.values():
                    p = _norm(val)
                    if p:
                        paths.add(p)
    for cv in (spec.get("comparative_visualizations") or []):
        if not isinstance(cv, dict):
            continue
        p = _norm(cv.get("observable_path") or cv.get("path"))
        if p:
            paths.add(p)

    expanded = set(paths)
    for p in list(paths):
        if not p.startswith("agents/"):
            expanded.add(f"agents/0/{p}")
    return sorted(expanded)


def inject_emitter_for_declared_paths(state: dict,
                                      declared_paths: list[str]) -> dict:
    """Like :func:`inject_emitter_for_paths` but does NOT pre-validate paths
    against the initial state tree, and writes the captured state as a NESTED
    tree (mirroring the wire structure) rather than flat underscore keys.

    Why bypass validation:
      Many observable stores are created at composite-build/run time by process
      ``outputs`` wires and aren't present in the spec-time state. v2ecoli's
      listener Steps materialise ``agents/0/listeners/<...>`` only after the
      composite runs, so the walk-existing-state approach (_collect_emit_leaves)
      skips them. This variant trusts the declared paths.

    Why nested vs flat:
      Flat ``"_".join(path)`` port names produce flat JSON keys that
      ``json_extract(state, '$.<dotted>.<path>')`` (comparative_viz, study_charts)
      can't navigate. The nested form mirrors the path hierarchy so the readers
      resolve it.

    Always also wires ``global_time``: it advances every composite apply, so
    wiring it guarantees the emitter Step re-fires every tick (an emitter wired
    only to rarely-mutating listener paths — or to paths absent at init — fires
    just once, collapsing history to ~1-2 rows). It also supplies the
    history.global_time x-axis column.

    Idempotent on re-call with the same declared paths.
    """
    if not declared_paths:
        return state
    paths = list(declared_paths)
    if "global_time" not in paths:
        paths.append("global_time")

    wires: dict = {}
    for raw in paths:
        parts = [p for p in raw.split("/") if p]
        if not parts:
            continue
        node = wires
        for p in parts[:-1]:
            existing = node.get(p)
            if not isinstance(existing, dict):
                existing = {}
                node[p] = existing
            node = existing
        node[parts[-1]] = list(parts)
    if not wires:
        return state

    def _to_schema(node):
        if isinstance(node, dict):
            return {k: _to_schema(v) for k, v in node.items()}
        return "node"
    emit_schema = _to_schema(wires)

    new_state = dict(state)
    existing = state.get("user_emitter")
    if (isinstance(existing, dict)
            and (existing.get("config") or {}).get("emit") == emit_schema
            and existing.get("inputs") == wires):
        return new_state
    new_state["user_emitter"] = {
        "_type": "step",
        "address": "local:RAMEmitter",
        "config": {"emit": emit_schema},
        "inputs": wires,
    }
    return new_state


def all_store_paths(state: dict) -> list[str]:
    """Return every top-level store key in ``state``, skipping step/process
    nodes.

    Used as the Composite Explorer Run tab's default emit selection: when the
    user hasn't hand-picked stores in the wiring view, the run emits every
    store. The returned keys feed ``inject_emitter_for_paths``, which walks
    each into its leaf stores.
    """
    return [
        key for key, node in state.items()
        if not (isinstance(node, dict)
                and node.get("_type") in ("process", "step"))
    ]


def _collect_emit_leaves(state: dict,
                          explicit_paths: list[str]) -> list[list[str]]:
    """For each explicit_path (slash-joined), walk the state tree and return
    every leaf store path (path that doesn't lead to a dict with
    ``_type`` of ``process``/``step``).

    A path resolves into the state tree by indexing top-level keys
    recursively. If the path points to a leaf (non-dict or dict without
    ``_type``), the path itself is a leaf. If the path points to a
    subtree, walk it.
    """
    leaves: list[list[str]] = []
    for raw in explicit_paths:
        parts = [p for p in raw.split("/") if p]
        # v2ecoli single-cell composites scope every listener store under
        # agents/0/...; study observables are declared at the biology path
        # (e.g. listeners/dnaA_cycle/atp_fraction). If the literal path
        # doesn't resolve, retry under agents/0/.
        parts, node = resolve_agents0_fallback(state, parts)
        if node is None:
            # Path resolves nowhere (neither literal nor agents/0/ scoped).
            # This happens for listener outputs materialised only during the
            # run (e.g. listeners/replication_data/number_of_oric is absent at
            # init). We deliberately do NOT wire it: the SQLiteEmitter is a
            # Step that fires on input triggers, and an input path with no
            # store to trigger on leaves the step without a per-tick trigger —
            # it then emits ~once and the whole history collapses to 2 rows.
            # Dropping the unresolved path keeps per-tick capture of the rest.
            continue
        _walk_collect(node, parts, leaves)
    # Dedup while preserving order
    seen: set[tuple[str, ...]] = set()
    out: list[list[str]] = []
    for p in leaves:
        t = tuple(p)
        if t in seen:
            continue
        seen.add(t)
        out.append(p)
    return out


def _is_node_tree(node) -> bool:
    """True for a store that holds a place-graph SUBTREE whose structure can
    change at runtime (division, aggregation) — a ``tree[node]`` / ``node`` /
    ``map[node]`` store, or any dict carrying a Milner ``_control`` tag. Such a
    store must be emitted WHOLE (topology preserved) rather than flattened to
    scalar leaves, so a viewer can step the changing structure."""
    if not isinstance(node, dict):
        return False
    t = str(node.get("_type", ""))
    if t.startswith("tree[node") or t == "node" or t.startswith("map[node"):
        return True
    # a dict tagged with _control (or whose children are) is a topology subtree
    if "_control" in node:
        return True
    return any(isinstance(v, dict) and "_control" in v
               for k, v in node.items() if not k.startswith("_"))


def _walk_collect(node, path: list[str], out: list[list[str]]) -> None:
    # If node is a process or step, skip — we only emit store values.
    if isinstance(node, dict) and node.get("_type") in ("process", "step"):
        return
    # A place-graph subtree (tree[node] / _control-tagged) is emitted WHOLE so
    # its runtime topology changes survive — do NOT descend into it.
    if _is_node_tree(node):
        out.append(path)
        return
    # If node is a dict, treat its non-meta keys as child store paths and
    # recurse into each. Each key becomes its own leaf or sub-walk.
    if isinstance(node, dict):
        children = {k: v for k, v in node.items() if not k.startswith("_")}
        if children:
            for k, v in children.items():
                _walk_collect(v, path + [k], out)
            return
    # Otherwise it's a leaf store
    out.append(path)


def copy_run_to_new_db(src_db: Path, dst_db: Path, run_id: str) -> int:
    """Copy one run's metadata + history rows from src_db to dst_db.

    Both DBs use the same schema (runs_meta + history). Bootstraps dst_db's
    schema if missing. Returns the count of history rows copied.

    Raises KeyError if run_id is not found in src_db.
    """
    src = sqlite3.connect(str(src_db))
    src.row_factory = sqlite3.Row
    dst = connect(dst_db)  # bootstraps runs_meta + index
    try:
        # SQLiteEmitter creates the history table lazily on first write; do it eagerly here.
        dst.executescript("""
            CREATE TABLE IF NOT EXISTS history (
                simulation_id TEXT NOT NULL,
                step INTEGER NOT NULL,
                global_time REAL,
                state TEXT NOT NULL,
                PRIMARY KEY (simulation_id, step)
            );
        """)

        meta = src.execute(
            "SELECT * FROM runs_meta WHERE run_id = ?", (run_id,)
        ).fetchone()
        if meta is None:
            raise KeyError(run_id)

        dst.execute(
            "INSERT INTO runs_meta (run_id, spec_id, label, params_json, "
            "started_at, completed_at, n_steps, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (meta["run_id"], meta["spec_id"], meta["label"], meta["params_json"],
             meta["started_at"], meta["completed_at"], meta["n_steps"], meta["status"]),
        )

        rows = src.execute(
            "SELECT step, global_time, state FROM history WHERE simulation_id = ?",
            (run_id,),
        ).fetchall()
        dst.executemany(
            "INSERT INTO history (simulation_id, step, global_time, state) "
            "VALUES (?, ?, ?, ?)",
            [(run_id, r["step"], r["global_time"], r["state"]) for r in rows],
        )
        dst.commit()
        return len(rows)
    finally:
        src.close()
        dst.close()
