"""Workspace-wide simulations index: aggregate across SQLite DBs.

A *simulation* is one row in a ``runs_meta`` table written by an emitter
(today: ``SQLiteEmitter``). Rows live in two kinds of DBs:

- ``<workspace>/.pbg/composite-runs.db`` — Composite Explorer scratch runs.
- ``<workspace>/studies/<name>/runs.db`` — one per Study (baseline + variants).

``list_simulations`` walks both, cross-references each ``run_id`` against
every ``study.yaml``'s ``runs[]`` (Studies-association), and returns one
sorted list. ``delete_simulation`` performs the full-delete pass.
"""
from __future__ import annotations

import datetime as _dt
import json
import shutil
import sqlite3
import tempfile
import threading
import warnings
from pathlib import Path
from urllib.parse import quote as _urlquote

import yaml
from pydantic import ValidationError

from vivarium_workbench.lib import composite_runs as cr
from vivarium_workbench.lib import emitters
from vivarium_workbench.lib import run_capabilities
from vivarium_workbench.lib import _root
from vivarium_workbench.lib import run_log
from vivarium_workbench.lib import run_store
from vivarium_workbench.lib.models import SimRow
from vivarium_workbench.lib.workspace_paths import WorkspacePaths


class RunNotFound(Exception):
    """Raised by ``delete_simulation`` when ``run_id`` is in no known DB."""


def _study_slug_from_db_path(db_path_str: str) -> str | None:
    """A runs.db lives at .../studies/<slug>/runs.db — return <slug> (last 'studies/' segment)."""
    parts = str(db_path_str).replace("\\", "/").split("/")
    if "studies" in parts:
        i = len(parts) - 1 - parts[::-1].index("studies")  # last 'studies'
        if i + 1 < len(parts):
            return parts[i + 1]
    return None


def _iter_all_study_dirs(workspace: Path):
    """Yield ``(study_dir, investigation_slug, rel_prefix)`` for every study
    directory in the workspace.

    Covers BOTH layouts:
      * root        — ``studies/<slug>/``  (investigation_slug ``None``,
        rel_prefix ``studies/<slug>``)
      * nested      — ``investigations/<inv>/studies/<slug>/``
        (investigation_slug ``<inv>``, rel_prefix
        ``investigations/<inv>/studies/<slug>``)

    De-dupes by study slug, choosing the BEST candidate per slug: root layout
    beats nested, and within a layer a dir that actually holds runs (runs.db /
    parquet / zarr) beats a bare study.yaml, which beats an empty dir (skipped).
    So a stale placeholder like ``investigations/inv/studies/basal`` (no runs.db,
    no study.yaml) never shadows the real ``investigations/<inv>/studies/basal``
    that holds the run — the bug that made basal / colonies invisible. The
    Simulations DB MUST scan both layouts: nested-layout investigations keep
    their ``runs.db`` / ``parquet-runs`` / ``study.yaml`` runs under
    ``investigations/<inv>/studies/<slug>/``.
    """
    wp = WorkspacePaths.load(workspace)

    def _score(sdir: Path) -> int:
        """0 = not a real study dir (skip); higher = more authoritative."""
        return (
            (4 if (sdir / "runs.db").exists() else 0)
            + (2 if ((sdir / "parquet-runs").exists() or any(sdir.glob("*.zarr"))) else 0)
            + (1 if (sdir / "study.yaml").exists() else 0)
        )

    # slug -> (score, sdir, inv_name, rel); keep the highest-scoring candidate.
    best: dict[str, tuple] = {}

    def _consider(sdir: Path, inv_name, rel: str, layer_bonus: int) -> None:
        if not sdir.is_dir():
            return
        score = _score(sdir)
        if score == 0:
            return  # empty placeholder / not a study — never yield or claim the slug
        score += layer_bonus
        prev = best.get(sdir.name)
        if prev is None or score > prev[0]:
            best[sdir.name] = (score, sdir, inv_name, rel)

    # rel_prefix MUST be relative to the workspace ROOT (so read_source's
    # `Path(workspace) / db_path` resolves) — NOT a hardcoded "studies/..." which
    # ignores a `layout:` remap (e.g. viva-biofilm's `studies: workspace/studies`
    # nests studies under workspace/, and the hardcoded prefix pointed the Data
    # Explorer at the wrong path → 0 runs). Mirrors the parquet-hive db_path,
    # which already uses `hive_dir.relative_to(workspace)`.
    ws_abs = Path(workspace).resolve()

    def _rel(sdir: Path) -> str:
        # Resolve both so a relative `workspace` (".") and an absolute study dir
        # (WorkspacePaths may resolve to absolute) still yield a workspace-root-
        # relative path that read_source's `Path(workspace) / db_path` re-joins.
        return sdir.resolve().relative_to(ws_abs).as_posix()

    root = wp.studies
    if root.is_dir():
        for sdir in sorted(root.iterdir()):
            _consider(sdir, None, _rel(sdir), layer_bonus=8)  # root precedence
    invs = wp.investigations
    if invs.is_dir():
        for inv in sorted(invs.iterdir()):
            nested = inv / "studies"
            if not (inv.is_dir() and nested.is_dir()):
                continue
            for sdir in sorted(nested.iterdir()):
                _consider(sdir, inv.name, _rel(sdir), layer_bonus=0)

    for slug in sorted(best):
        _, sdir, inv_name, rel = best[slug]
        yield sdir, inv_name, rel


def _discover_dbs(workspace: Path) -> list[tuple[Path, str]]:
    """Return list of (db_path, workspace_relative_str) for every runs DB.

    Skips missing files. Order: workspace-level DBs first (composite-runs
    + default-baseline), then per-study (root then nested investigations) in
    alphabetical order (deterministic for tests).

    .pbg/default-baseline/runs.db is produced by ``scripts/run_default_baseline.py``
    (workspace.yaml:default_baseline). It's the "before any study runs"
    reference state — surfaces in the Simulations DB tab so evaluators can
    open it, and is read as a fallback when a study has no runs of its own.
    """
    wp = WorkspacePaths.load(workspace)
    dbs: list[tuple[Path, str]] = []
    scratch = wp.pbg / "composite-runs.db"
    if scratch.is_file():
        dbs.append((scratch, ".pbg/composite-runs.db"))
    default_baseline = wp.pbg / "default-baseline" / "runs.db"
    if default_baseline.is_file():
        dbs.append((default_baseline, ".pbg/default-baseline/runs.db"))
    for sdir, _inv, rel_prefix in _iter_all_study_dirs(workspace):
        db = sdir / "runs.db"
        if db.is_file():
            dbs.append((db, f"{rel_prefix}/runs.db"))
    return dbs


def discover_default_baseline_db(workspace: Path) -> Path | None:
    """Return the path to the workspace's default-baseline runs.db, or None.

    Used by viz pre-fill: when a study has no runs of its own, callers
    fall back to this db so the evaluator sees the cell's baseline
    behaviour against the study's measure paths.
    """
    p = WorkspacePaths.load(workspace).pbg / "default-baseline" / "runs.db"
    return p if p.is_file() else None


def _study_pack_capabilities(study_slug) -> list[str]:
    """Study-level pack capabilities that don't depend on a run store.

    A study that materialized an HRA atlas pack (``viz/atlas/atlas.json``)
    advertises ``atlas_pack`` — the run's output IS the pack an atlas viewer
    consumes as input — so the viewer matches the study's run even when the run
    emitted no observable store (e.g. a bare Step that writes a pack)."""
    if not study_slug:
        return []
    try:
        ws = _root.workspace_root()
    except Exception:  # noqa: BLE001
        ws = None
    if not ws:
        return []
    caps: list[str] = []
    try:
        studies = WorkspacePaths.load(ws).studies
        if (studies / str(study_slug) / "viz" / "atlas" / "atlas.json").is_file():
            caps.append("atlas_pack")
    except Exception:  # noqa: BLE001 — best-effort; never break capability derivation
        pass
    # A Simularium trajectory (viz/**/*.simularium) is a study-level artifact its
    # runs produced — advertise it per-run so the Simularium Viewer tool matches
    # the run in the Runs table + a study's Simulation table. Nested-aware via
    # the same scanner the tool uses.
    try:
        from vivarium_workbench.lib.analysis_tools_simularium import (
            studies_with_simularium)
        if any(s["study"] == str(study_slug) for s in studies_with_simularium(ws)):
            caps.append("simularium")
    except Exception:  # noqa: BLE001
        pass
    return caps


def _capabilities_for_row(row: dict, conn=None) -> list[str]:
    """Capabilities for one runs_meta row: cached value, else derive.

    A completed run's NON-EMPTY tag set is cached back; empties are NOT cached
    (and a cached empty re-derives), so a run whose store was transiently
    unreadable — or derived before the workspace root was resolvable — is never
    permanently frozen at ``[]``. In-progress runs derive fresh every call and
    are never cached (a still-emitting run's tag set isn't final)."""
    # Study-level pack capabilities are independent of any run store — a run that
    # only wrote a pack/trajectory (no observable store) still advertises them.
    # They are DYNAMIC (the artifact can appear after the run), so they are merged
    # even into a cached (store-derived) capability set rather than frozen out.
    pack_caps = _study_pack_capabilities(row.get("study_slug"))

    def _with_pack(base: list[str]) -> list[str]:
        out = list(base)
        for c in pack_caps:
            if c not in out:
                out.append(c)
        return out

    cached = row.get("capabilities_json")
    if cached:
        try:
            parsed = list(json.loads(cached))
            if parsed:  # only trust a non-empty cache; re-derive an empty one
                return _with_pack(parsed)
        except Exception:  # noqa: BLE001
            pass
    # `row` may carry any of these three; store_path/emitter_path are absent
    # on plain runs_meta dicts unless the caller added them. `store_path` is
    # not an actual runs_meta column (it's a value some callers synthesize
    # from provenance JSON) — emitter_path is the real DB column for
    # parquet/zarr runs; db_path is the runs.db itself (used by SQLite
    # emitters, and as the last-resort fallback).
    store = row.get("store_path") or row.get("emitter_path") or row.get("db_path")
    if not store:
        return pack_caps
    # Store paths in runs_meta are workspace-RELATIVE (e.g. ``.pbg/runs/<id>``),
    # so the reader needs the workspace root to resolve them — without it every
    # run derives ``[]``. Use the effective (per-request or process-default)
    # root; best-effort so tests/contexts without one still work.
    try:
        ws = _root.workspace_root()
    except Exception:  # noqa: BLE001 — no root set (e.g. unit test) -> None
        ws = None
    tags = run_capabilities.derive_capabilities(store, row.get("run_id"), ws)
    if tags and row.get("status") == "completed" and conn is not None:
        try:
            from vivarium_workbench.lib.composite_runs import write_run_capabilities
            write_run_capabilities(conn, row["run_id"], tags)
        except Exception:  # noqa: BLE001 — caching is best-effort
            pass
    # Preserve the derived tag order (tests + UI rely on it); append any pack
    # capabilities not already present.
    out = list(tags)
    for c in pack_caps:
        if c not in out:
            out.append(c)
    return out


def _commit_url(remote_url: str | None, commit: str | None) -> str | None:
    """Build a browsable commit URL from a git remote + sha.

    Recognises GitHub SSH (``git@github.com:org/repo.git``) and HTTPS remotes;
    returns None for a non-GitHub or unparseable remote (the UI then shows the
    sha as plain text rather than a dead link).
    """
    if not remote_url or not commit:
        return None
    u = remote_url.strip()
    host_path = None
    if u.startswith("git@") and ":" in u:
        host_path = u.split("@", 1)[1].replace(":", "/", 1)
    elif u.startswith("http://") or u.startswith("https://"):
        host_path = u.split("://", 1)[1]
    elif u.startswith("ssh://"):
        host_path = u.split("://", 1)[1].split("@", 1)[-1]
    if not host_path:
        return None
    if host_path.endswith(".git"):
        host_path = host_path[:-4]
    if "github.com" not in host_path:
        return None
    return "https://" + host_path.rstrip("/") + "/commit/" + commit


def _source_from_manifest(manifest_json: str | None) -> dict | None:
    """Extract the run's source-provenance ``{repo, commit, …}`` from its
    stored manifest ``code_version`` block. Returns None when the run has no
    manifest or no code_version (a legacy manifest-less row) — the caller
    then falls back to the inferred workspace source.
    """
    if not manifest_json:
        return None
    try:
        m = json.loads(manifest_json) or {}
    except (TypeError, ValueError):
        return None
    cv = m.get("code_version")
    if not isinstance(cv, dict):
        return None
    commit = cv.get("git_sha")
    repo = cv.get("repo")
    remote_url = cv.get("remote_url")
    if not (commit or repo):
        return None
    return {
        "repo": repo,
        "commit": commit,
        "commit_short": (commit[:7] if isinstance(commit, str) else None),
        "remote_url": remote_url,
        "commit_url": _commit_url(remote_url, commit),
        "package": cv.get("package"),
        # A manifest written by backfill_source_provenance carries the
        # workspace's HEAD at backfill time, not the run's original commit —
        # flag it so the UI shows it as approximate, same as the read-time
        # inferred fallback.
        "inferred": bool(cv.get("backfilled")),
    }


def _inferred_workspace_source(workspace) -> dict | None:
    """The inferred source (repo + current HEAD) for a workspace checkout.

    Flagged ``inferred: True`` — it's the workspace's CURRENT commit, used to
    backfill any row whose recording path didn't stamp source provenance, so
    the Runs table's Source column is never blank. None when the workspace
    isn't a resolvable checkout (no git_sha and no repo name).
    """
    ident = cr.git_repo_identity(workspace)
    commit = ident.get("git_sha")
    if not (commit or ident.get("repo")):
        return None
    return {
        "repo": ident.get("repo"),
        "commit": commit,
        "commit_short": (commit[:7] if isinstance(commit, str) else None),
        "remote_url": ident.get("remote_url"),
        "commit_url": _commit_url(ident.get("remote_url"), commit),
        "package": None,
        "inferred": True,
    }


def _row_to_dict(row, db_path_str: str) -> dict:
    """Convert a runs_meta SELECT row to the public dict shape."""
    # Parse provenance JSON (may be absent in legacy DBs or None).
    prov: dict = {}
    try:
        raw_json = row["params_json"]
        if raw_json:
            prov = json.loads(raw_json) or {}
    except (KeyError, TypeError, ValueError):
        prov = {}
    # Detect remote run: must have both `source` (non-empty) and `simulation_id`.
    if prov.get("source") and prov.get("simulation_id") is not None:
        remote_origin = {
            "deployment": prov.get("source"),
            "simulation_id": prov.get("simulation_id"),
            "experiment_id": prov.get("experiment_id"),
            "backend": prov.get("backend"),
            "s3_uri": prov.get("s3_uri"),
        }
    else:
        remote_origin = None
    # A remote run lands its native store next to runs.db (a .zarr or parquet-runs
    # dir), so its emitter type must come from that store_path — NOT from db_path,
    # which is always the runs.db SQLite metadata file (would mislabel it "SQLite").
    # Emitter kind is inferred from the native store path (NOT db_path, which is
    # always the runs.db SQLite metadata file). Detection is centralized in
    # run_store.detect_kind; map its canonical kind to this view's display label.
    emitter: str | None = {"zarr": "xarray", "parquet": "parquet"}.get(
        run_store.detect_kind(prov.get("store_path"))
    )
    raw = {
        "run_id": row["run_id"],
        "spec_id": row["spec_id"],
        "sim_name": row["sim_name"],
        "label": row["label"],
        "status": row["status"],
        "n_steps": row["n_steps"],
        "progress_step": row["progress_step"],
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
        "db_path": db_path_str,
        # Native data store: a .zarr/parquet dir (local) or an s3:// uri (remote);
        # None means the run's data lives in the runs.db SQLite at db_path.
        "store_path": (prov.get("store_path") or remote_origin and remote_origin.get("s3_uri")) or None,
        "emitter": emitter,  # store-derived (xarray/parquet) for remote runs; None → falls back to db_path
        # The exact reproduction config: the generator params saved at run time
        # (params_json), minus the transport-provenance keys captured separately
        # in remote_origin/store_path. None when no params were recorded.
        "config": ({k: v for k, v in prov.items() if k not in _RUN_PROVENANCE_KEYS}
                   or None),
        "studies": [],  # filled in by _annotate_studies
        # Match the SQLiteEmitter shape so JS consumers can rely on the
        # keys existing regardless of which emitter wrote the row.
        "study_slug": _study_slug_from_db_path(db_path_str),
        "investigation_slug": None,
        "remote_origin": remote_origin,
        # Source provenance (repo + commit the run launched from), read from the
        # manifest's code_version. None here for a manifest-less legacy row —
        # list_simulations() then backfills the inferred workspace source so the
        # Runs table's Source column is never blank. Named ``source_ref`` (not
        # ``source``) to avoid colliding with the row's origin-bucket ``source``
        # ("runs_meta"/"sqlite_emitter"/…) used by the dedup pass.
        "source_ref": _source_from_manifest(
            row["manifest_json"] if "manifest_json" in row.keys() else None),
    }
    # Validate/normalize through the typed model (single source of truth). The
    # dumped dict is identical to `raw` for well-formed rows; on an unexpected
    # row we keep serving the legacy dict and surface the drift as a warning
    # rather than 500-ing the whole simulations index.
    try:
        return SimRow.model_validate(raw).model_dump()
    except ValidationError as e:
        warnings.warn(
            f"simulations_index: row {raw.get('run_id')!r} failed SimRow "
            f"validation: {e}"
        )
        return raw


def _read_sqlite_emitter(db_path: Path, db_path_str: str) -> list[dict]:
    """Read process_bigraph.emitter.SQLiteEmitter's `simulations` + `history`
    tables and translate to the dashboard's run-dict shape. Returns [] when
    the DB doesn't have a `simulations` table (this isn't an emitter DB)."""
    try:
        raw = sqlite3.connect(str(db_path))
        raw.row_factory = sqlite3.Row
    except sqlite3.OperationalError as e:
        warnings.warn(f"simulations_index: skipping {db_path_str}: {e}")
        return []
    try:
        tbls = {r[0] for r in raw.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        if "simulations" not in tbls or "history" not in tbls:
            return []
        # study_slug + investigation_slug are added by v2ecoli's
        # sqlite_emitter() helper (PR: fix/workspace-shared-sim-db). Older
        # DBs may not have the columns yet — detect and skip in that case.
        sim_cols = {row[1] for row in raw.execute(
            "PRAGMA table_info(simulations)"
        ).fetchall()}
        has_study_slug = 'study_slug' in sim_cols
        has_investigation_slug = 'investigation_slug' in sim_cols
        select_extras = ''
        if has_study_slug:
            select_extras += ', s.study_slug'
        if has_investigation_slug:
            select_extras += ', s.investigation_slug'
        rows = raw.execute(
            "SELECT s.simulation_id, s.name, s.started_at, s.completed_at, "
            "       s.elapsed_seconds, "
            "       (SELECT COUNT(*) FROM history h WHERE h.simulation_id = s.simulation_id) AS n_rows, "
            "       (SELECT MAX(step) FROM history h WHERE h.simulation_id = s.simulation_id) AS max_step"
            + select_extras +
            " FROM simulations s ORDER BY s.started_at DESC"
        ).fetchall()
    except sqlite3.OperationalError as e:
        warnings.warn(f"simulations_index: sqlite-emitter read failed for {db_path_str}: {e}")
        return []
    finally:
        raw.close()
    out = []
    for r in rows:
        n_rows = r["n_rows"] or 0
        out.append({
            "run_id":             r["simulation_id"],
            "spec_id":            "",
            "sim_name":           r["name"] or "",
            "label":              r["name"] or "",
            "status":             "completed" if r["completed_at"] else "running",
            "n_steps":            (r["max_step"] + 1) if r["max_step"] is not None else n_rows,
            "progress_step":      r["max_step"] if r["max_step"] is not None else n_rows,
            "started_at":         r["started_at"],
            "completed_at":       r["completed_at"],
            "db_path":            db_path_str,
            "source":             "sqlite_emitter",
            "studies":            [],
            "study_slug":         r["study_slug"] if has_study_slug else None,
            "investigation_slug": r["investigation_slug"] if has_investigation_slug else None,
        })
    return out


def _read_runs_meta(db_path: Path, db_path_str: str) -> list[dict]:
    """SELECT every runs_meta row in a DB. Tolerates lock/timeout by returning []."""
    try:
        conn = cr.connect(db_path)
    except sqlite3.OperationalError as e:
        warnings.warn(f"simulations_index: skipping {db_path_str}: {e}")
        return []
    try:
        # Skip if no runs_meta table (DB is SQLiteEmitter-format, not dashboard-format).
        tbls = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        if "runs_meta" not in tbls:
            return []
        rows = conn.execute(
            "SELECT run_id, spec_id, sim_name, label, status, n_steps, "
            "progress_step, started_at, completed_at, params_json, "
            "emitter_path, capabilities_json, manifest_json "
            "FROM runs_meta ORDER BY started_at DESC"
        ).fetchall()
        # Attach capabilities WHILE conn is still open, so a completed run
        # missing a cached value can be backfilled + written back in the same
        # pass (see _capabilities_for_row). Rows are already materialized
        # (.fetchall() above), so committing mid-loop is safe.
        out = []
        for r in rows:
            d = _row_to_dict(r, db_path_str)
            rd = dict(r)  # sqlite3.Row -> dict (row_factory is sqlite3.Row)
            rd["db_path"] = db_path_str
            # runs_meta carries no study_slug column; carry the one _row_to_dict
            # derived from the db path so study-level pack caps (simularium /
            # atlas_pack) resolve.
            rd["study_slug"] = d.get("study_slug")
            d["capabilities"] = _capabilities_for_row(rd, conn=conn)
            out.append(d)
        return out
    except sqlite3.OperationalError as e:
        warnings.warn(f"simulations_index: skipping {db_path_str}: {e}")
        return []
    finally:
        conn.close()


def _study_has_run_store(study_dir: "Path") -> bool:
    """True if a study directory has its own run store (runs.db / parquet / zarr).
    Such a study records real runs by their global id in each run's ``name``, so
    those must not be study-prefixed (they merge with the store rows)."""
    try:
        study_dir = Path(study_dir)
        return ((study_dir / "runs.db").exists()
                or (study_dir / "parquet-runs").is_dir()
                or (study_dir / "runs").is_dir())
    except Exception:  # noqa: BLE001
        return False


def _study_run_uid(study_slug: str, entry, has_store: bool = False) -> "str | None":
    """Workspace-unique run id for a study.yaml ``runs[]`` entry.

    A DICT entry that only *defines* a study-local run by ``name`` (e.g.
    ``{name: baseline, status: completed}``) in a STORE-LESS study is unique
    within its study but not across the workspace, so different studies'
    name-runs would collide in the run-id-keyed index and collapse to one.
    Prefix those with the study slug (``<slug>:<name>``).

    Everything else is a *reference* to a global run id — used verbatim so it
    still merges/shares: a bare STRING entry (``runs: ["shared"]``), an explicit
    ``run_id``/``simulation_id``, or ANY entry in a study that has its own run
    store (``has_store``), where the runner records real runs by their global id
    in ``name`` (prefixing those would duplicate them against the store rows).
    """
    if isinstance(entry, str):
        return entry or None
    if not isinstance(entry, dict):
        return None
    uid = entry.get("run_id") or entry.get("simulation_id")
    if isinstance(uid, str) and uid:
        return uid
    name = entry.get("name") or entry.get("simulation")
    if not (isinstance(name, str) and name):
        return None
    return name if has_store else f"{study_slug}:{name}"


# Run kinds that do NOT execute a composite — cross-study/meta aggregations. The
# study-declared-composite fallback is not applied to these (attributing the
# study's composite to them would misrepresent what ran). Extend if new pure-meta
# kinds appear; simulation/analysis/ensemble/parameter_screen/cross_validation/
# sensitivity_analysis all execute the composite and are intentionally NOT here.
_NON_COMPOSITE_RUN_KINDS = frozenset({"synthesis"})

# Keys in a runs_meta ``params_json`` that are transport/remote provenance, NOT
# composite generator config — surfaced separately as remote_origin/store_path,
# so they're stripped from the reproduction ``config`` view.
_RUN_PROVENANCE_KEYS = frozenset({
    "source", "simulation_id", "experiment_id", "backend", "s3_uri", "store_path",
})


def _study_declared_composite(data: dict) -> str | None:
    """The composite a study declares at the DESIGN level, for runs whose own
    entry omits ``composite:``.

    mbp-style studies declare the composite once under ``conditions.baseline``
    (or another condition), or as a top-level ``composite``/``baseline``, rather
    than repeating it on every run entry. Resolution order: ``conditions.baseline
    .composite`` → first ``conditions.*.composite`` → top-level ``composite`` →
    ``baseline`` (string, or dict with ``composite``). Returns None if the study
    declares no composite anywhere. Defensive: never raises on odd shapes.
    """
    if not isinstance(data, dict):
        return None
    conditions = data.get("conditions")
    if isinstance(conditions, dict):
        base = conditions.get("baseline")
        if isinstance(base, dict) and isinstance(base.get("composite"), str):
            return base["composite"]
        for cond in conditions.values():
            if isinstance(cond, dict) and isinstance(cond.get("composite"), str):
                return cond["composite"]
    comp = data.get("composite")
    if isinstance(comp, str) and comp:
        return comp
    baseline = data.get("baseline")
    if isinstance(baseline, str) and baseline:
        return baseline
    if isinstance(baseline, dict) and isinstance(baseline.get("composite"), str):
        return baseline["composite"]
    # ``baseline`` may be a list of variant dicts ([{name, composite, params}]);
    # take the first that declares a composite.
    if isinstance(baseline, list):
        for variant in baseline:
            if isinstance(variant, dict) and isinstance(variant.get("composite"), str):
                return variant["composite"]
        # A bare Step/Process baseline (baseline.step / baseline.process) has no
        # composite; use its address as the run's spec_id so step/process-baseline
        # study runs still surface in the Simulations DB.
        for variant in baseline:
            if isinstance(variant, dict):
                ref = variant.get("step") or variant.get("process")
                if isinstance(ref, str) and ref:
                    return ref
    return None


def _study_declared_params(data: dict) -> dict:
    """The generator params a study declares at the DESIGN level (mirrors
    :func:`_study_declared_composite`'s resolution). Returns {} if none.

    These are the base config a run inherits (e.g. ``{condition: with_aa}``);
    a run entry's own params override them.
    """
    if not isinstance(data, dict):
        return {}
    conditions = data.get("conditions")
    if isinstance(conditions, dict):
        base = conditions.get("baseline")
        if isinstance(base, dict) and isinstance(base.get("params"), dict):
            return dict(base["params"])
        for cond in conditions.values():
            if isinstance(cond, dict) and isinstance(cond.get("params"), dict):
                return dict(cond["params"])
    baseline = data.get("baseline")
    if isinstance(baseline, dict) and isinstance(baseline.get("params"), dict):
        return dict(baseline["params"])
    if isinstance(baseline, list):
        for variant in baseline:
            if isinstance(variant, dict) and isinstance(variant.get("params"), dict):
                return dict(variant["params"])
    return {}


def _run_entry_config(entry: dict, declared_params: dict) -> dict | None:
    """Assemble the exact config for a study.yaml run: the study's declared
    params overlaid with the run entry's own params/seed/trajectory_params/
    n_steps. Returns None when nothing was captured.
    """
    config = dict(declared_params)
    if isinstance(entry.get("params"), dict):
        config.update(entry["params"])
    for key in ("seed", "trajectory_params"):
        if entry.get(key) is not None:
            config[key] = entry[key]
    if entry.get("n_steps") is not None:
        config.setdefault("n_steps", entry["n_steps"])
    return config or None


def _read_study_yaml_runs(workspace: Path) -> list[dict]:
    """Surface runs recorded only in ``study.yaml`` ``runs:`` as first-class
    simulation rows.

    Emitter-less workspaces (numpy investigations like pbg-autopoiesis) persist
    each run in the spec rather than a per-step ``runs.db`` / parquet hive / zarr
    store. Without this they never appear in the Simulations DB even though they
    ran. Rows are shaped like the DB sources so ``list_simulations``'s merge
    treats them uniformly; a real DB row wins on ``run_id`` collision (the DB is
    authoritative where it exists). ``source='study_yaml'``.
    """
    out: list[dict] = []
    for sdir, inv_slug, _rel in _iter_all_study_dirs(workspace):
        yml = sdir / "study.yaml"
        if not yml.is_file():
            continue
        try:
            data = yaml.safe_load(yml.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            warnings.warn(f"simulations_index: malformed yaml at {yml}")
            continue
        runs = data.get("runs")
        if not isinstance(runs, list):
            continue
        # Study-level fallback for runs whose own entry omits `composite:`
        # (mbp-style studies declare it once under conditions.baseline).
        declared_composite = _study_declared_composite(data)
        declared_params = _study_declared_params(data)
        has_store = _study_has_run_store(sdir)
        for entry in runs:
            if not isinstance(entry, dict):
                continue
            rid = _study_run_uid(sdir.name, entry, has_store)
            if not rid:
                continue
            _name = entry.get("name") or entry.get("simulation")
            # Apply the study-declared composite ONLY when the run executes one.
            # `synthesis` runs are cross-study aggregations that run no composite,
            # so we must not attribute the study's composite to them. (simulation/
            # analysis/ensemble/parameter_screen/cross_validation/sensitivity_
            # analysis all DO execute the composite and get the fallback.)
            _entry_composite = entry.get("composite")
            _fallback = None if entry.get("kind") in _NON_COMPOSITE_RUN_KINDS else declared_composite
            out.append({
                "run_id": rid,
                # Study-level pack capabilities (e.g. atlas_pack) apply even to a
                # store-less study.yaml run, so a viewer that consumes the study's
                # pack matches this run in the Tools column.
                "capabilities": _study_pack_capabilities(sdir.name),
                "spec_id": _entry_composite or _fallback,
                "config": _run_entry_config(entry, declared_params),
                "sim_name": _name or rid,
                "label": _name or rid,
                "status": entry.get("status") or "completed",
                "n_steps": entry.get("n_steps"),
                "progress_step": entry.get("n_steps") or 0,
                # `record_runs` (viva_superpowers.study_outcomes) writes the run's
                # completion time as `timestamp` (= db completed_at or started_at),
                # NOT as started_at/completed_at — so without this fallback the
                # Simulations DB Time column stayed blank for every study.yaml-
                # recorded run even though the time was recorded.
                "started_at": entry.get("started_at") or entry.get("timestamp"),
                "completed_at": (entry.get("completed_at")
                                 or entry.get("started_at")
                                 or entry.get("timestamp")),
                "db_path": None,
                # Native store recorded in the spec (parquet/zarr dir); the run's
                # data lives here even though there's no per-step runs.db.
                "store_path": (entry.get("parquet") or entry.get("store_path")
                               or entry.get("zarr") or None),
                "studies": [sdir.name],
                "study_slug": sdir.name,
                "investigation_slug": inv_slug,
                "emitter": entry.get("emitter"),  # declared in the spec, if any
                "source": "study_yaml",
            })
    return out


def _build_run_to_studies_map(workspace: Path) -> dict[str, list[str]]:
    """Return ``{run_id: [study_name, ...]}`` across every study.yaml."""
    result: dict[str, list[str]] = {}
    for sdir, _inv, _rel in _iter_all_study_dirs(workspace):
        yml = sdir / "study.yaml"
        if not yml.is_file():
            continue
        try:
            data = yaml.safe_load(yml.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        has_store = _study_has_run_store(sdir)
        for entry in (data.get("runs") or []) if isinstance(data, dict) else []:
            # Same workspace-unique id the rows use, so the association matches
            # after dedup (name-runs are study-scoped, not global).
            rid = _study_run_uid(sdir.name, entry, has_store)
            if rid:
                result.setdefault(rid, []).append(sdir.name)
    return result


# ---------------------------------------------------------------------------
# Parquet hive discovery (workspace-default emitter per 2026-05-27 migration)
# ---------------------------------------------------------------------------
#
# ParquetEmitter runs land at
#   studies/<study_slug>/parquet-runs/<experiment_id>/{configuration,history,success}/...
# with the configuration/ subdir holding the per-generation metadata
# (study_slug, investigation_slug, generation, agent_id) the runner passed
# at construction. We discover them by walking the per-study parquet-runs/
# dirs and build the same dict shape sqlite_emitter readers produce, so
# the merge logic in list_simulations doesn't have to special-case parquet.
# ---------------------------------------------------------------------------


def _find_hive_dir(run_dir: Path, max_depth: int = 3) -> Path | None:
    """Shallowest directory at/under ``run_dir`` that looks like a
    ParquetEmitter hive — i.e. has both ``configuration/`` and ``history/``
    children. Returns None if none is found within ``max_depth`` levels.

    Why a search instead of a fixed path: the runner/sweep wrappers nest the
    emitter hive below the run dir the user launched, at variable depth and
    under variably-named intermediate dirs, e.g.::

        parquet-runs/<run>/                                   (flat — hive IS the run dir)
        parquet-runs/<run>/parquet/<experiment_id>/           (sweep output)
        parquet-runs/<run>/<inner>/                           (single repro run)

    BFS so the closest hive wins; the partition dirs themselves
    (history/configuration/success) are never descended into.
    """
    from collections import deque
    queue: deque[tuple[Path, int]] = deque([(run_dir, 0)])
    while queue:
        d, depth = queue.popleft()
        if (d / "history").is_dir() and (d / "configuration").is_dir():
            return d
        if depth >= max_depth:
            continue
        try:
            children = sorted(c for c in d.iterdir()
                              if c.is_dir()
                              and c.name not in ("history", "configuration", "success"))
        except OSError:
            continue
        for c in children:
            queue.append((c, depth + 1))
    return None


def _discover_parquet_hives(workspace: Path) -> list[tuple[Path, Path, str]]:
    """Yield ``(hive_dir, run_dir, study_slug)`` for every per-study parquet
    run under ``studies/*/parquet-runs/<run>/``.

    ``run_dir`` is the directory the user launched (the unique, meaningful run
    key); ``hive_dir`` is the ParquetEmitter hive located beneath it (see
    :func:`_find_hive_dir` — it may be the run dir itself for the flat shape,
    or nested for sweep output). Run dirs without any hive (in-progress writes
    whose first emit hasn't flushed yet) are skipped. Deterministic order:
    study slug first, then run dir mtime descending.
    """
    out: list[tuple[Path, Path, str]] = []
    for sdir, _inv, _rel in _iter_all_study_dirs(workspace):
        parquet_runs = sdir / "parquet-runs"
        if not parquet_runs.is_dir():
            continue
        run_dirs = [p for p in parquet_runs.iterdir() if p.is_dir()]
        run_dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for run_dir in run_dirs:
            hive = _find_hive_dir(run_dir)
            if hive is not None:
                out.append((hive, run_dir, sdir.name))
    return out


def _parquet_sim_name_from_yaml(yaml_path: Path, experiment_id: str) -> str | None:
    """Look up the human-readable sim name from the study.yaml's runs[]
    by matching ``simulation_id``. Returns None when the yaml is missing
    (e.g. cross-investigation pseudo-studies whose parquet-runs/ dir
    exists without a study.yaml), unreadable, or has no matching entry.
    """
    if not yaml_path.is_file():
        return None
    try:
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError):
        return None
    for entry in data.get("runs") or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("simulation_id") == experiment_id:
            return entry.get("simulation") or None
    return None


def _read_parquet_hive(
    hive_dir: Path, run_dir: Path, study_slug: str, workspace: Path
) -> dict | None:
    """Read one parquet run's metadata + counts and return the dashboard's
    standard run-dict shape. Returns None on read failure so a broken hive
    doesn't take down the whole simulations index.

    ``run_dir`` is the directory the user launched (the unique run key);
    ``hive_dir`` is the ParquetEmitter hive located beneath it, which holds
    the ``configuration/`` / ``history/`` / ``success/`` partitions. For the
    flat layout the two are the same dir.
    """
    try:
        import polars as pl  # lazy; not every dashboard install has it
    except ImportError:
        warnings.warn(
            "simulations_index: polars unavailable; "
            f"skipping parquet hive at {hive_dir}"
        )
        return None

    experiment_id = hive_dir.name
    run_id = run_dir.name
    history = hive_dir / "history"
    config = hive_dir / "configuration"
    success = hive_dir / "success"

    # Pull metadata from the configuration parquet (one row per generation;
    # use the first for study/investigation slugs since both rows carry the
    # same values for those columns by construction).
    investigation_slug = None
    metadata_slug = None
    try:
        if config.is_dir():
            cfg_df = pl.read_parquet(str(config / "**" / "*.pq"))
            if cfg_df.height > 0:
                first = cfg_df.row(0, named=True)
                metadata_slug = first.get("study_slug") or None
                investigation_slug = first.get("investigation_slug") or None
    except Exception as e:  # noqa: BLE001
        warnings.warn(
            f"simulations_index: parquet config read failed at {hive_dir}: {e}"
        )

    # Fall back to the study's owning investigation when the parquet metadata
    # didn't carry investigation_slug (older runner output, pre-convention).
    if not investigation_slug and study_slug:
        try:
            investigation_slug = WorkspacePaths.load(workspace).study_owner(study_slug)
        except Exception:  # noqa: BLE001
            pass

    # Row count = total emit count across all generations.
    try:
        n_rows = int(
            pl.scan_parquet(str(history / "**" / "*.pq"))
              .select(pl.len())
              .collect()
              .item()
        )
    except Exception:
        n_rows = 0

    # Status: success/ subdir means ParquetEmitter.close(success=True) ran.
    status = "completed" if success.is_dir() else "running"

    # Timestamps come from the filesystem since ParquetEmitter doesn't
    # stamp them itself. configuration/ is written at construction;
    # success/ at clean close.
    try:
        started_at = config.stat().st_mtime if config.is_dir() else run_dir.stat().st_mtime
    except OSError:
        started_at = None
    try:
        completed_at = success.stat().st_mtime if success.is_dir() else None
    except OSError:
        completed_at = None

    # sim_name: try the study.yaml first (gives the human-readable label) —
    # matched on the run id, then the inner experiment id — and finally fall
    # back to the run id (meaningful; the inner experiment id may be a shared
    # label reused across runs, so it's the worse default).
    yaml_path = WorkspacePaths.load(workspace).studies / study_slug / "study.yaml"
    sim_name = (
        _parquet_sim_name_from_yaml(yaml_path, run_id)
        or _parquet_sim_name_from_yaml(yaml_path, experiment_id)
        or run_id
    )

    # Workspace-relative path for the dashboard's UI (drill-down links) — the
    # hive dir, where configuration/ + history/ live.
    db_path = str(hive_dir.relative_to(workspace))

    return {
        "run_id":             run_id,
        "spec_id":            "",
        "sim_name":           sim_name,
        "label":              sim_name,
        "status":             status,
        "n_steps":            n_rows,
        "progress_step":      max(n_rows - 1, 0),
        "started_at":         started_at,
        "completed_at":       completed_at,
        "db_path":            db_path,
        "source":             "parquet",
        "studies":            [],
        # Prefer the slug recorded in metadata (authoritative for cross-
        # investigation reference runs whose path-slug is a pseudo-study);
        # fall back to the path slug for older hives.
        "study_slug":         metadata_slug or study_slug,
        "investigation_slug": investigation_slug,
    }


def _read_parquet_hives(workspace: Path) -> list[dict]:
    """Discover + read every parquet experiment under the workspace.
    Skips broken hives (returns the rest) so one bad write doesn't blank
    the Simulations tab."""
    out: list[dict] = []
    for hive_dir, run_dir, study_slug in _discover_parquet_hives(workspace):
        row = _read_parquet_hive(hive_dir, run_dir, study_slug, workspace)
        if row is not None:
            out.append(row)
    return out


# ---------------------------------------------------------------------------
# XArray (zarr) run discovery — the XArrayEmitter writes zarr stores under
# ``.pbg/runs/<run_id>/[seed_NN/]store.zarr`` (the PDMP investigation's default
# emitter). These never register a runs_meta row unless backfilled, so the
# Simulations DB must also discover them on disk to be XArray-aware.
# ---------------------------------------------------------------------------

def _read_zarr_provenance(zarr_path: Path) -> dict | None:
    """Read a zarr store's ``provenance`` attribute — the self-describing run
    provenance the XArrayEmitter writes ({composite, config, run_id}).

    The emitter writes it at its ROOT group, which in the partitioned layout is
    a NESTED group (``experiment_id=…/variant=…/lineage_seed=…``), not the
    top-level ``store.zarr`` dir. So we check the top root, then descend
    (bounded) to the first group carrying a ``provenance`` attr. Returns the
    dict, or None for legacy attr-less stores / unopenable zarr. Never raises.
    """
    try:
        import zarr  # optional dep; lazy so the index never hard-requires it
        root = zarr.open_group(str(zarr_path), mode="r")
        prov = dict(root.attrs).get("provenance")
        if isinstance(prov, dict) and prov:
            return prov
        for _name, member in root.members(max_depth=4):
            mprov = dict(getattr(member, "attrs", {})).get("provenance")
            if isinstance(mprov, dict) and mprov:
                return mprov
        return None
    except Exception:  # noqa: BLE001 — missing dep / unreadable / attr-less legacy store
        return None


def _discover_xarray_runs(workspace: Path) -> list[dict]:
    """Yield one row per ``.pbg/runs/<run_id>/`` dir that contains a
    ``store.zarr`` (directly or under ``seed_*/``). Shaped like the other
    readers (source/emitter = 'xarray') so the merge logic treats them
    uniformly. Metadata is recovered from the filesystem: ``run_id`` from the
    dir name, ``n_steps`` from the ``emitstep_gen=*`` partition count, and
    timestamps from mtime. Status is ``completed`` (a persisted zarr store is
    a finished write)."""
    runs_dir = Path(workspace) / ".pbg" / "runs"
    if not runs_dir.is_dir():
        return []
    out: list[dict] = []
    for run_dir in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
        # XArray stores nest at variable depth: <run>/store.zarr,
        # <run>/seed_NN/store.zarr (ensembles), or
        # <run>/<param-combo>/seed_NN/store.zarr (param-sweep ensembles).
        zarrs = (list(run_dir.glob("store.zarr"))
                 + list(run_dir.glob("*/store.zarr"))
                 + list(run_dir.glob("*/*/store.zarr")))
        if not zarrs:
            continue
        run_id = run_dir.name
        # Ensemble size = number of leaf zarr stores (seeds × param combos).
        n_leaves = len(zarrs)
        n_steps = None
        try:
            mtime = run_dir.stat().st_mtime
        except OSError:
            mtime = None
        # Self-describing provenance: a store written by the XArrayEmitter carries
        # {composite, config, run_id} in its root attrs, so a disk-discovered run
        # still shows its composite + config (legacy attr-less stores → None).
        prov = _read_zarr_provenance(zarrs[0])
        row = {
            "run_id": run_id,
            "spec_id": (prov or {}).get("composite"),
            "sim_name": run_id,
            "label": run_id,
            "status": "completed",
            "n_steps": n_steps,
            "progress_step": 0,
            "ensemble_size": n_leaves,   # # of leaf zarr stores (seeds × params)
            "started_at": mtime,
            "completed_at": mtime,
            "db_path": str(run_dir.relative_to(workspace)),
            "studies": [],
            "study_slug": None,
            "investigation_slug": None,
            "source": "xarray",
        }
        if isinstance((prov or {}).get("config"), dict) and prov["config"]:
            row["config"] = prov["config"]
        out.append(row)
    return out


def _emitter_for_row(workspace: Path, row: dict) -> str:
    """Resolve the emitter that persisted a row: 'parquet' / 'xarray' / 'sqlite'.

    Thin back-compat wrapper over :func:`emitters.label_for_run` (the broker is
    now the single locus for emitter dispatch). parquet/xarray are known from
    their source tag; for SQLite-table rows we still disk-probe for a backfilled
    zarr store before defaulting to 'sqlite'."""
    return emitters.label_for_run(row, workspace)


def _discover_ce_store_path(workspace: Path, run_id: str) -> str | None:
    """Native store for a Composite Explorer run that was recorded without a
    ``store_path``. The Explorer writes its per-agent parquet sweep to
    ``<ws>/.pbg/runs/<run_id>/parquet/<run_id>`` (and, for xarray runs, a
    ``store.zarr`` under the same run dir), but runs_meta carries no store_path —
    so ``Location`` fell back to the sqlite runs.db. Return the workspace-relative
    store path when it exists on disk, else ``None``."""
    if not run_id:
        return None
    base = Path(workspace) / ".pbg" / "runs" / str(run_id)
    for cand in (base / "parquet" / str(run_id), base / "store.zarr"):
        if cand.is_dir():
            try:
                return cand.relative_to(workspace).as_posix()
            except ValueError:
                return str(cand)
    return None


def list_simulations(workspace: Path) -> list[dict]:
    """Return every persisted simulation in ``workspace``, newest first.

    Each dict contains: run_id, spec_id, sim_name, label, status, n_steps,
    progress_step, started_at, completed_at, db_path (workspace-relative),
    studies (list of study names that reference this run_id).

    When the same ``run_id`` is present in both the ``runs_meta`` and the
    SQLiteEmitter ``simulations`` table (the common case for runs created
    via ``pbg_runner``), the rows are MERGED into one entry — runs_meta
    fields (spec_id, status, n_steps) take priority over the emitter's,
    and the started_at is normalised to a float (unix epoch). Without
    this merge the dashboard's frontend JS sees mixed ISO-string /
    float started_at values and trips ``new Date(string * 1000)``,
    halting the table render.
    """
    def _to_float_ts(v):
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            try:
                return _dt.datetime.fromisoformat(
                    v.replace("Z", "+00:00")).timestamp()
            except ValueError:
                return None
        return None

    workspace = Path(workspace)
    rows: list[dict] = []
    for db_path, db_rel in _discover_dbs(workspace):
        # Try both schemas; each returns [] when its table is absent so the
        # two are non-overlapping per DB.
        rows.extend(_read_runs_meta(db_path, db_rel))
        rows.extend(_read_sqlite_emitter(db_path, db_rel))
    # Workspace-default emitter (post-2026-05-27 parquet migration). Each
    # row is shaped exactly like a sqlite_emitter row so the merge logic
    # below treats them uniformly. Source-tag "parquet" lets the frontend
    # render an emitter badge.
    rows.extend(_read_parquet_hives(workspace))
    # XArray (zarr) runs persisted under .pbg/runs/<id>/store.zarr — the PDMP
    # investigation's default emitter. Surfaced live so they show even when no
    # runs_meta row was recorded; dedup below merges with any backfilled row.
    rows.extend(_discover_xarray_runs(workspace))
    # Runs recorded only in study.yaml `runs:` (emitter-less workspaces — numpy
    # investigations like pbg-autopoiesis). Added last so a real DB row for the
    # same run_id wins in the dedup below; study.yaml-only runs still surface.
    rows.extend(_read_study_yaml_runs(workspace))

    # Deduplicate by run_id, preferring runs_meta over sqlite_emitter (so
    # spec_id / status / n_steps come from the canonical bookkeeping table).
    # Normalise started_at + completed_at to floats while we're here.
    merged: dict[str, dict] = {}
    for r in rows:
        rid = r.get("run_id")
        if not rid:
            continue
        r = dict(r)  # don't mutate the source
        r["started_at"]   = _to_float_ts(r.get("started_at"))
        r["completed_at"] = _to_float_ts(r.get("completed_at"))
        existing = merged.get(rid)
        if existing is None:
            merged[rid] = r
            continue
        # Prefer runs_meta on collision (it has spec_id and authoritative status).
        if existing.get("source") == "runs_meta":
            # Keep existing; fill in any None fields from the new row.
            for k, v in r.items():
                if existing.get(k) in (None, "", []) and v not in (None, "", []):
                    existing[k] = v
        else:
            # Existing was sqlite_emitter; r is preferred if it's runs_meta.
            if r.get("source") == "runs_meta":
                for k, v in existing.items():
                    if r.get(k) in (None, "", []) and v not in (None, "", []):
                        r[k] = v
                merged[rid] = r
            else:
                # Both sqlite_emitter — just fill in missing fields.
                for k, v in r.items():
                    if existing.get(k) in (None, "", []) and v not in (None, "", []):
                        existing[k] = v
    rows = list(merged.values())

    def _ts(r):
        # After dedupe above, started_at is already normalised to float
        # (or None). Trivial sort key here.
        v = r.get("started_at")
        return float(v) if isinstance(v, (int, float)) else 0.0
    rows.sort(key=_ts, reverse=True)

    run_to_studies = _build_run_to_studies_map(workspace)
    _wp = WorkspacePaths.load(workspace)
    # Inferred workspace source (repo + current HEAD), computed once. Used to
    # backfill any row whose manifest didn't record source provenance
    # (legacy/manifest-less runs, emitter-discovered runs) so the Runs table's
    # Source column is never blank. Flagged ``inferred: True`` (see
    # _inferred_workspace_source) — the workspace's CURRENT commit, not
    # necessarily the exact commit an old run executed under.
    _ws_source = _inferred_workspace_source(workspace)
    # SQLiteEmitter runs are study-scoped by path (studies/<name>/runs.db),
    # so derive the study name from db_path when no explicit study.yaml
    # cross-reference exists.
    for r in rows:
        # Explicit cross-reference takes priority
        explicit = list(run_to_studies.get(r["run_id"], []))
        r["studies"] = explicit
        if not explicit and r.get("source") == "sqlite_emitter":
            p = (r.get("db_path") or "")
            if p.startswith("studies/") and p.endswith("/runs.db"):
                study = p[len("studies/"):-len("/runs.db")]
                r["studies"] = [study]
        # Fall back to path-derived study_slug for legacy per-study DBs
        # written before sqlite_emitter() stamped the column.
        if not r.get("study_slug") and r.get("studies"):
            r["study_slug"] = r["studies"][0]
        # Derive the owning investigation from the study when the run record
        # didn't carry it (study.yaml-declared runs, and legacy DBs without the
        # investigation_slug column) — the workspace knows every study's owner.
        if not r.get("investigation_slug") and r.get("study_slug"):
            try:
                r["investigation_slug"] = _wp.study_owner(r["study_slug"]) or None
            except Exception:
                pass
        # Surface the Composite Explorer run's on-disk parquet sweep as the store
        # location (runs_meta didn't record it → store_path was None → Location
        # showed the sqlite db).
        _ce_store = False
        if not r.get("store_path") and r.get("run_id"):
            _sp = _discover_ce_store_path(workspace, r["run_id"])
            if _sp:
                r["store_path"] = _sp
                _ce_store = True
        # Emitter-awareness: tag each row with the emitter that persisted it
        # (xarray / parquet / sqlite) so the Simulations DB can show a column.
        r["emitter"] = _emitter_for_row(workspace, r)
        # A CE run writes BOTH a sqlite history sidecar and its declared
        # parquet/zarr sweep; when we found the native sweep, classify the row by
        # it (matches the Location) instead of the sqlite sidecar.
        if _ce_store:
            _k = {"zarr": "xarray", "parquet": "parquet"}.get(
                run_store.detect_kind(r["store_path"]))
            if _k:
                r["emitter"] = _k
        # Backfill source provenance for any row the recording path didn't stamp
        # (manifest-less runs_meta rows, emitter-discovered runs) with the
        # inferred workspace source, so the Source column is always populated.
        if not r.get("source_ref") and _ws_source is not None:
            r["source_ref"] = dict(_ws_source)
    return rows


def backfill_source_provenance(workspace: Path) -> int:
    """Stamp a source-provenance manifest onto existing manifest-less rows.

    One-time, idempotent backfill (called on server startup): every
    ``runs_meta`` row across the workspace's runs DBs that has no
    ``manifest_json`` gets a best-effort manifest whose ``code_version``
    records the workspace's repo + CURRENT HEAD, flagged ``backfilled: True``
    (surfaced as ``inferred`` in the row — the commit is approximate, not the
    run's original one). Rows that already have a manifest are left untouched,
    so re-runs are no-ops and accurate per-run provenance is never clobbered.

    Returns the number of rows stamped. Never raises — a locked/legacy DB is
    skipped with a warning; provenance backfill must not block server startup.
    """
    ident = cr.git_repo_identity(workspace)
    if not (ident.get("git_sha") or ident.get("repo")):
        return 0  # not a resolvable checkout — nothing meaningful to stamp
    stamped = 0
    for db_path, db_rel in _discover_dbs(workspace):
        try:
            conn = cr.connect(db_path)
        except sqlite3.OperationalError as e:
            warnings.warn(f"simulations_index: backfill skipping {db_rel}: {e}")
            continue
        try:
            tbls = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            if "runs_meta" not in tbls:
                continue
            rows = conn.execute(
                "SELECT run_id, spec_id, params_json, n_steps FROM runs_meta "
                "WHERE manifest_json IS NULL"
            ).fetchall()
            for r in rows:
                try:
                    params = json.loads(r["params_json"]) if r["params_json"] else {}
                    if not isinstance(params, dict):
                        params = {}
                except (TypeError, ValueError):
                    params = {}
                try:
                    manifest = cr.build_run_manifest(
                        spec_id=r["spec_id"], params=params, n_steps=r["n_steps"],
                        emitter=None, emit_paths=[], runtime={}, origin="local",
                        ws_root=workspace,
                    )
                    manifest["code_version"]["backfilled"] = True
                    conn.execute(
                        "UPDATE runs_meta SET manifest_json=? WHERE run_id=?",
                        (json.dumps(manifest), r["run_id"]),
                    )
                    stamped += 1
                except Exception as e:  # noqa: BLE001 — never block startup
                    warnings.warn(
                        f"simulations_index: backfill failed for {r['run_id']}: {e}")
            conn.commit()
        except sqlite3.OperationalError as e:
            warnings.warn(f"simulations_index: backfill skipping {db_rel}: {e}")
        finally:
            conn.close()
    return stamped


def _find_db_for_run(workspace: Path, run_id: str) -> tuple[Path, str] | None:
    """Locate which runs DB owns ``run_id``. Returns (path, rel) or None."""
    for db_path, db_rel in _discover_dbs(workspace):
        try:
            conn = cr.connect(db_path)
        except sqlite3.OperationalError:
            continue
        try:
            row = conn.execute(
                "SELECT 1 FROM runs_meta WHERE run_id=? LIMIT 1", (run_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            row = None
        finally:
            conn.close()
        if row is not None:
            return db_path, db_rel
    return None


def _delete_db_rows(db_path: Path, run_id: str) -> tuple[int, int]:
    """Delete a run's rows across EVERY table it may live in. One transaction.

    Three tables can hold rows for one run: ``runs_meta`` (the run's metadata),
    ``history`` (the SQLiteEmitter's per-step trajectory, keyed
    ``simulation_id``), and ``simulations`` (the SQLiteEmitter's own run index,
    also keyed ``simulation_id``). Clearing only the first two left orphaned
    ``simulations`` rows that the Sim-DB fold re-surfaced as phantom "running"
    entries a user could not delete — the exact reappearance this delete path is
    meant to prevent. Each table is dropped only if present (a run may predate
    one, or use a different emitter).

    Returns (rows_deleted, history_rows_deleted); the ``simulations`` count is
    folded into neither — it is bookkeeping the caller's summary doesn't expose.
    """
    def _has_table(conn, name: str) -> bool:
        return conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone() is not None

    conn = cr.connect(db_path)
    try:
        history_rows = 0
        if _has_table(conn, "history"):
            history_rows = conn.execute(
                "DELETE FROM history WHERE simulation_id=?", (run_id,)
            ).rowcount or 0
        if _has_table(conn, "simulations"):
            conn.execute(
                "DELETE FROM simulations WHERE simulation_id=?", (run_id,))
        meta_rows = conn.execute(
            "DELETE FROM runs_meta WHERE run_id=?", (run_id,)
        ).rowcount or 0
        conn.commit()
        return meta_rows, history_rows
    finally:
        conn.close()


def _rewrite_study_yaml_without(yaml_path: Path, run_id: str) -> bool:
    """Rewrite ``yaml_path``'s runs[] entry without ``run_id``.

    Atomic: write-then-rename through a sibling temp file. Returns True if
    a runs[] entry was removed, False if nothing changed. Raises OSError
    on write failure (caller catches per-file).
    """
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        return False
    runs = data.get("runs") or []
    if not isinstance(runs, list):
        return False
    study_slug = yaml_path.parent.name
    has_store = _study_has_run_store(yaml_path.parent)
    new_runs: list = []
    changed = False
    for entry in runs:
        # Match on the same workspace-unique id the index exposes (name-runs are
        # prefixed <slug>:<name>; explicit run_id/simulation_id runs are verbatim).
        if _study_run_uid(study_slug, entry, has_store) == run_id:
            changed = True
            continue
        new_runs.append(entry)
    if not changed:
        return False
    data["runs"] = new_runs
    from .atomic_io import atomic_write_text
    atomic_write_text(yaml_path, yaml.safe_dump(data, sort_keys=False))
    return True


def delete_simulation(workspace: Path, run_id: str) -> dict:
    """Full delete of a simulation: DB rows + history + run dir + study refs.

    Returns a summary dict::

        {
          "deleted_rows": int,            # 1 on success
          "deleted_history": int,         # rows removed from history
          "removed_dir": bool,            # True if .pbg/runs/<id>/ existed and was removed
          "unlinked_studies": [str],      # study names whose study.yaml lost a ref
          "errors": [str],                # one entry per per-file failure
        }

    Raises ``RunNotFound`` if ``run_id`` is in no known DB.
    """
    workspace = Path(workspace)
    located = _find_db_for_run(workspace, run_id)
    if located is None:
        raise RunNotFound(run_id)
    db_path, _ = located

    wp = WorkspacePaths.load(workspace)
    errors: list[str] = []
    deleted_rows, deleted_history = _delete_db_rows(db_path, run_id)

    run_dir = wp.pbg / "runs" / run_id
    removed_dir = run_dir.exists()
    if removed_dir:
        shutil.rmtree(run_dir, ignore_errors=True)
        # If ignore_errors didn't fully remove it, surface that:
        if run_dir.exists():
            errors.append(f"run dir {run_dir.relative_to(workspace)}: partial removal")
            removed_dir = False

    unlinked: list[str] = []
    studies_root = wp.studies
    if studies_root.is_dir():
        for sdir in sorted(studies_root.iterdir()):
            if not sdir.is_dir():
                continue
            yml = sdir / "study.yaml"
            if not yml.is_file():
                continue
            try:
                if _rewrite_study_yaml_without(yml, run_id):
                    unlinked.append(sdir.name)
            except (yaml.YAMLError, OSError) as e:
                errors.append(f"{sdir.name}: {type(e).__name__}: {e}")

    # Tombstone the run in the append-only JSONL log. Without this the next
    # fold re-synthesises the row from its surviving `started` event and the
    # run reappears — undeletable through the UI. Best-effort: a log failure
    # must not turn an otherwise-successful delete into a 500.
    try:
        run_log.append_deleted_event(workspace, run_id)
    except OSError as e:
        errors.append(f"run log tombstone: {type(e).__name__}: {e}")

    return {
        "deleted_rows": deleted_rows,
        "deleted_history": deleted_history,
        "removed_dir": removed_dir,
        "unlinked_studies": unlinked,
        "errors": errors,
    }


def _emitter_tag(emitter) -> str:
    """Normalise a row's ``emitter`` field to a lowercase string tag.

    The value may be a plain string ("parquet"), a structured dict
    ({"kind": "parquet", "store": ...}) declared in a study.yaml ``runs:``
    entry, or None. A dict reaching ``.lower()`` used to raise AttributeError
    inside the emitter_type loop — silently swallowed — which blanked every
    row's emitter_type and made the UI default the pill to "SQLite".
    """
    if isinstance(emitter, dict):
        emitter = emitter.get("kind")
    return emitter.lower() if isinstance(emitter, str) else ""


def _append_remote_simulations(sims: list, ws_root: Path) -> list:
    """Append the active remote build's server-side runs (scoped to the build's
    commit/repo) to the local Simulations-DB rows. No-op for local workspaces
    or when sms-api is unreachable — single source for the local+remote merge,
    shared by ``build_simulations_data`` and the ``/api/simulations`` handler."""
    try:
        from vivarium_workbench.lib.remote_simulations import list_remote_simulations
        remote = list_remote_simulations(ws_root)
    except Exception:
        remote = []
    if not remote:
        return sims
    # Dedup: a landed remote run is already a local row (same run_id/experiment_id)
    # — don't list it twice. Keep the local (richer, has store/analyses) copy.
    have = {s.get("run_id") for s in sims if isinstance(s, dict)}
    extra = [r for r in remote if r.get("run_id") not in have]
    return list(sims) + extra if extra else sims


# Shared emitter-kind -> display-label map (one place to add a kind's label).
_EMITTER_LABEL = {"sqlite": "SQLite", "parquet": "Parquet", "xarray": "XArray",
                  "ram": "RAM", "none": "—"}


def backfill_index_into_jsonl(ws_root: Path) -> int:
    """Idempotently migrate every run discoverable in the LEGACY stores (sqlite
    ``runs.db``, ``study.yaml`` runs, parquet/zarr hives) into the append-only
    JSONL run log, so the Simulations DB can be built from JSONL alone.

    Reuses :func:`list_simulations`, which already resolves ``store_path`` /
    ``db_path`` / ``emitter`` uniformly for EVERY emitter type — so the log ends
    up carrying each run's data-store location and retrieval keeps working
    across SQLite / XArray / Parquet / remote. Appends a ``backfill`` event when
    a run is absent from the fold, OR when the folded row lacks any data-store
    location (``store_path`` and ``db_path``) that the legacy store can supply
    (this self-heals JSONL-native ``started`` events logged before the store
    existed). Returns the number of events appended.
    """
    try:
        rows = list_simulations(ws_root)
    except Exception:
        return 0
    folded = run_log.fold_runs_jsonl(Path(ws_root))
    tombstoned = run_log.tombstoned_run_ids(Path(ws_root))
    appended = 0
    for row in rows:
        rid = row.get("run_id")
        if not rid or rid in tombstoned:
            continue  # never resurrect a deleted run from its lingering sqlite row
        prev = folded.get(rid)
        has_store = bool(row.get("store_path") or row.get("db_path"))
        prev_has_store = bool(prev and (prev.get("store_path") or prev.get("db_path")))
        # Enforcement (every simulation must map to one registered composite):
        # self-heal the composite too. A run migrated before its study declared
        # a composite has ``spec_id=None`` in the log; once the study.yaml/DB
        # supplies one, re-backfill so the log record carries it.
        has_composite = bool(row.get("spec_id"))
        prev_has_composite = bool(prev and prev.get("spec_id"))
        # Same self-heal for capability tags: a run logged before capabilities
        # existed (or before its lazy backfill ran) has none in the fold; once
        # list_simulations resolves them (cached or freshly derived), re-backfill
        # so /api/simulations rows carry them without waiting on a fresh run.
        #
        # Unlike store_path/spec_id, capabilities are NOT invariant once any
        # value is folded: a running run's tag set is a live, uncached,
        # possibly-partial preview (_capabilities_for_row derives it fresh
        # every call and never writes it to runs_meta), and only becomes the
        # authoritative, finalized value once the run completes (cached into
        # runs_meta.capabilities_json). A presence-only check (as store_path/
        # spec_id use) would let a partial mid-run snapshot, once folded,
        # permanently shadow the final tag set after completion — status
        # alone can't gate this either, since `complete_metadata` folds its
        # own "completed" status event independently of (and before) this
        # backfill pass, so `prev["status"]` is already "completed" by the
        # time a LATER call finally resolves+caches the final capabilities.
        # So compare VALUES: only treat capabilities as "already known" when
        # the prior fold's value equals what `row` carries right now (which,
        # for a completed run, is the authoritative cached-or-just-derived
        # final answer — see _capabilities_for_row). This also correctly
        # resyncs when the final set is `[]` (a real, complete answer, not
        # "nothing to add").
        capabilities = row.get("capabilities")
        has_capabilities = capabilities is not None
        prev_has_capabilities = (
            prev is not None
            and prev.get("capabilities") is not None
            and prev.get("capabilities") == capabilities
        )
        # Same self-heal for the reproduction config: a run migrated before its
        # config was derivable has none in the log; once study.yaml/params_json
        # supplies it, re-backfill so the log record carries it.
        has_config = bool(row.get("config"))
        prev_has_config = bool(prev and prev.get("config"))
        # Same self-heal for source provenance (repo@commit): a run migrated
        # before a manifest recorded its code_version has none in the log; once
        # list_simulations resolves one (manifest or inferred workspace source),
        # re-backfill so the Source column populates. Presence-only (like config)
        # so an already-folded value doesn't re-append on every build.
        has_source = bool(row.get("source_ref"))
        prev_has_source = bool(prev and prev.get("source_ref"))
        # Skip when already represented AND its store location is known (or the
        # legacy store has none to add) AND its composite is known AND its
        # capabilities are known AND its config is known (or the legacy store has
        # none to add) — keeps this idempotent across builds.
        if (prev is not None
                and (prev_has_store or not has_store)
                and (prev_has_composite or not has_composite)
                and (prev_has_capabilities or not has_capabilities)
                and (prev_has_config or not has_config)
                and (prev_has_source or not has_source)):
            continue
        ev = {"run_id": rid, "event": "backfill"}
        for k in ("spec_id", "sim_name", "label", "status", "n_steps",
                  "progress_step", "started_at", "completed_at", "db_path",
                  "store_path", "emitter", "study_slug", "investigation_slug",
                  "remote_origin", "capabilities", "config", "source_ref"):
            v = row.get(k)
            if v is not None:
                ev[k] = v
        run_log.append_run_event(Path(ws_root), ev)
        appended += 1
    return appended


def _rec_to_simrow(run_id: str, rec: dict) -> dict:
    """Turn one folded JSONL record into a validated SimRow dict.

    Classifies ``emitter_type`` from the recorded emitter kind, falling back to
    the store path's extension (``.zarr`` -> XArray, ``.parquet`` -> Parquet,
    runs.db -> SQLite) so every emitter is labelled correctly; an
    emitter-less/store-less row shows ``—``.
    """
    from vivarium_workbench.lib.runs_index import emitter_type_of

    row: dict = {"run_id": run_id}
    for k in ("spec_id", "sim_name", "label", "status", "n_steps",
              "progress_step", "started_at", "completed_at", "db_path",
              "store_path", "study_slug", "investigation_slug", "capabilities",
              "source_ref"):
        if rec.get(k) is not None:
            row[k] = rec[k]

    # Reproduction config: prefer an explicit config on the record (backfilled
    # from study.yaml / runs_meta); else derive from the run's saved generator
    # params (the 'started' event's ``params``), minus transport-provenance keys.
    if isinstance(rec.get("config"), dict) and rec["config"]:
        row["config"] = rec["config"]
    elif isinstance(rec.get("params"), dict) and rec["params"]:
        _cfg = {k: v for k, v in rec["params"].items()
                if k not in _RUN_PROVENANCE_KEYS}
        if _cfg:
            row["config"] = _cfg

    emitter = rec.get("emitter")
    tag = _emitter_tag(emitter)
    if tag:
        row["emitter"] = tag
        # Fall back to the raw tag (not a title-cased variant) for unknown kinds,
        # matching the pre-JSONL classifier's pill text.
        row["emitter_type"] = _EMITTER_LABEL.get(tag, tag)
    else:
        path = rec.get("store_path") or rec.get("db_path")
        row["emitter_type"] = emitter_type_of(path) if path else "—"

    # remote_origin: a genuine RemoteOrigin mapping (never the "local" origin
    # string save_metadata stamps — that fails SimRow validation and 500s).
    ro = rec.get("remote_origin")
    if not (isinstance(ro, dict) and ro):
        origin = rec.get("origin")
        ro = origin if isinstance(origin, dict) and origin else None
    if ro:
        row["remote_origin"] = ro

    try:
        dumped = SimRow.model_validate(row).model_dump()
    except ValidationError as e:
        warnings.warn(
            f"simulations_index: JSONL row {run_id!r} failed SimRow validation: {e}"
        )
        return row
    # Preserve the raw remote_origin mapping — SimRow's RemoteOrigin model would
    # otherwise pad it with None fields, changing its shape for JS consumers.
    if ro:
        dumped["remote_origin"] = ro
    return dumped


# ---------------------------------------------------------------------------
# Compatible-tools annotation — Simulations DB "launch into tool" affordance.
#
# Each row's `capabilities` list already drives the Analysis Tools tab's
# capability matching (lib/analysis_tools.py: `set(requires) <= set(caps)`).
# Here we run that same rule the other way: for one row, which of the
# workspace's installed tools (built-ins + repo-contributed viewers) match it,
# and — for the ones that do — what URL actuates them. Attaching this directly
# to each Simulations-DB row lets the frontend render a launch chip with no
# further round-trip.
# ---------------------------------------------------------------------------

# Reentrancy guard. `analysis_tools.build_analysis_tools()` computes its run
# candidates via `_run_candidates()`, which itself calls
# `build_simulations_data()` (see lib/analysis_tools.py) — so calling
# `build_analysis_tools` FROM `build_simulations_data` would recurse forever
# without this. Thread-local because each request's synchronous call chain
# runs on one worker thread from uvicorn's threadpool; a nested (inner) call
# on the SAME thread just skips re-attaching tools (they're irrelevant to that
# inner call, which only wants the plain sim rows).
_tools_reentry = threading.local()


def _launch_url_for_matched_tool(tool: dict, row: dict) -> "str | None":
    """The actuation URL for one tool matched to one Simulations-DB row.

    Mirrors the launch resolution in static/walkthrough.js's `_openTool` /
    `_build3dSrc` / `_launchViewer`, so a chip built from this URL opens the
    exact same place a user would reach from the Analysis Tools tab:

    - ``embed-3d`` (built-in Parsimony Viewer): a per-STUDY match — a study
      either has a 3D pack or it doesn't, so the row's ``study_slug`` selects
      the tool's matched pack candidate; its hosted ``viewer_url`` wins, else
      the bundled viewer is pointed at the study's 3D models manifest.
    - ``launcher`` (repo-contributed viewer): the generic actuation endpoint
      (``GET /api/analysis-viewer/{uid}/launch``), scoped to this row's study
      + run — the frontend fetches it and opens the returned ``{"url": ...}``.

    A bare contributed ``embed`` viewer (a custom mounted mini-app with no
    href/launch semantics) can't be reduced to one URL — returns ``None``, the
    same as any other tool this can't resolve. Never raises.
    """
    kind = tool.get("kind") or "launcher"
    run_id = row.get("run_id")
    study = row.get("study_slug")

    if kind == "embed-3d":
        matched = tool.get("matched") or []
        cand = next((m for m in matched if m.get("ref") == study), None) if study else None
        if cand and cand.get("viewer_url"):
            return cand["viewer_url"]
        # Only deep-link when this study is actually one of the tool's matched
        # candidates (or there's no `matched` list to check at all) — a study
        # with no 3D pack must not get a dead models.json link.
        if study and (cand is not None or not matched):
            models_url = f"/api/study/{_urlquote(str(study), safe='')}/3d/models.json"
            return f"/parsimony-viewer/index.html?models={_urlquote(models_url, safe='')}"
        return None

    if kind == "embed-simularium":
        # Per-study tool: find this run's study among the tool's matched
        # candidates and open its trajectory in the bundled Simularium viewer.
        matched = tool.get("matched") or []
        cand = next((m for m in matched if m.get("ref") == study), None) if study else None
        trajs = (cand or {}).get("trajectories") or []
        if trajs and trajs[0].get("url"):
            return f"/simularium-viewer.html?traj={_urlquote(str(trajs[0]['url']), safe='')}"
        return None

    if kind != "launcher":
        return None  # unresolvable contributed "embed" viewer — omit
    # A launcher whose contributed target is a self-contained static page (it
    # carries an ``href``) deep-links straight to that page — which works in the
    # read-only snapshot too, where the /api/.../launch endpoint has no live
    # server. Prefer a target scoped to this row's study (global viewers may
    # carry a single study-less target).
    _tgts = tool.get("targets") or []
    _href = next((t.get("href") for t in _tgts
                  if isinstance(t, dict) and t.get("href")
                  and (t.get("study") in (None, "", study))), None)
    if _href:
        return str(_href)
    uid = tool.get("uid") or tool.get("id")
    if not uid:
        return None
    params = []
    if study:
        params.append(f"study={_urlquote(str(study), safe='')}")
    if run_id:
        params.append(f"run={_urlquote(str(run_id), safe='')}")
    qs = ("?" + "&".join(params)) if params else ""
    return f"/api/analysis-viewer/{_urlquote(str(uid), safe='')}/launch{qs}"


def _matched_tool_entry(tool: dict, row: dict) -> "dict | None":
    """Reduce a matched tool + row pair to the small dict the frontend needs.
    Returns None (omit) when no concrete launch URL can be resolved."""
    url = _launch_url_for_matched_tool(tool, row)
    if not url:
        return None
    kind = tool.get("kind") or "launcher"
    # A static page URL (not the /api resolve endpoint) is a direct deep-link the
    # frontend opens as a plain link — so a launcher that resolved to its static
    # target page renders (and works) as a deep-link, including in the snapshot.
    if kind == "launcher" and not url.startswith("/api/"):
        kind = "deep-link"
    return {
        "id": tool.get("uid") or tool.get("id"),
        "label": tool.get("title") or tool.get("label") or tool.get("id") or "Tool",
        "kind": kind,
        "launch_url": url,
    }


def _attach_matched_tools(rows: list[dict], ws_root: Path) -> None:
    """Best-effort: attach ``row["matched_tools"]`` to every row in ``rows``.

    A row's matched tools are the installed analysis tools (built-in +
    repo-contributed, from ``analysis_tools.build_analysis_tools``) whose
    ``requires`` is a subset of the row's ``capabilities`` — the same rule
    ``analysis_tools.match`` applies runs-to-a-tool, inverted here to
    tools-to-a-run. Mutates ``rows`` in place. Never raises: any failure
    (a broken tool/viewer, a workspace with no installed tools at all) leaves
    every row's ``matched_tools`` at ``[]``, so a tool-matching bug can never
    take down the Simulations DB.
    """
    for row in rows:
        row.setdefault("matched_tools", [])

    if getattr(_tools_reentry, "active", False):
        return  # nested call building the tools list itself (see guard above)

    try:
        _tools_reentry.active = True
        from vivarium_workbench.lib import analysis_tools as _analysis_tools
        tools = _analysis_tools.build_analysis_tools(ws_root)
    except Exception:  # noqa: BLE001 — a broken tool/viewer must never break the sim list
        return
    finally:
        _tools_reentry.active = False

    if not tools:
        return

    for row in rows:
        caps = set(row.get("capabilities") or [])
        study_slug = row.get("study_slug")
        try:
            matched = []
            for tool in tools:
                requires = tool.get("requires") or []
                caps_ok = bool(requires) and set(requires) <= caps
                # embed-3d (Parsimony Viewer) is a per-STUDY tool: 3d_pack is a
                # property of the run's STUDY (it has viz/3d packs), not of the
                # run's emitter — so a run never carries the 3d_pack capability
                # and the capability-subset rule above would never match it.
                # Match it to any run whose study owns a 3D pack (the tool's
                # matched candidates are keyed by study `ref`); the launch-url
                # resolver then selects that study's hosted viewer_url. This is
                # what lets a run's Simulations-DB row link straight to its 3D
                # viewer, even when the run itself has no emitter capabilities.
                study_3d_ok = (
                    tool.get("kind") == "embed-3d"
                    and "3d_pack" in requires
                    and bool(study_slug)
                    and any(c.get("ref") == study_slug
                            for c in (tool.get("matched") or []))
                )
                if not (caps_ok or study_3d_ok):
                    continue
                entry = _matched_tool_entry(tool, row)
                if entry is not None:
                    matched.append(entry)
            row["matched_tools"] = matched
        except Exception:  # noqa: BLE001 — one bad row must not blank the rest
            row["matched_tools"] = []


def build_simulations_data(ws_root: Path, include_remote: bool = True) -> dict:
    """Data builder for GET /api/simulations — the ``list_simulations`` rows
    enriched with emitter_type labels + active remote build runs + current slug.

    Returns ``{"simulations": [...], "current": <slug|None>}``.  Tolerates
    missing DB / import errors → returns an empty list.  Relocated verbatim from
    the retired ``server._simulations_data`` so publish.build_bundle and the
    ``/api/simulations`` seam share one implementation.

    ``include_remote=False`` skips the (slow, ~tens-of-seconds) sms-api fetch of
    remote runs so the local index returns fast — the Runs tab loads local runs
    first, then fetches remote in a second call to merge them in.
    """
    ws = str(ws_root)
    import sys as _sys
    if ws not in _sys.path:
        _sys.path.insert(0, ws)
    # Capability derivation (run stores + study pack detection) resolves paths
    # against the global workspace root; the publish/CLI seam may not have set it
    # yet, in which case every run backfilled below would freeze at capabilities
    # []. Set it here so the snapshot carries capabilities (and matched Tools).
    try:
        _root.set_workspace_root(Path(ws_root))
    except Exception:  # noqa: BLE001 — best-effort; never break the data build
        pass

    # Pure-JSONL Simulations DB: migrate any runs still living only in the legacy
    # stores (sqlite runs.db / study.yaml / parquet-zarr hives) into the
    # append-only JSONL run log (idempotent), then build every row from the log
    # fold alone. The append-only log is the single source of truth; each row
    # carries its data-store location so retrieval works across all emitters.
    try:
        backfill_index_into_jsonl(Path(ws_root))
    except Exception:
        pass
    try:
        folded = run_log.fold_runs_jsonl(Path(ws_root))
    except Exception:
        folded = {}
    sims = [_rec_to_simrow(rid, rec) for rid, rec in folded.items()]

    # Newest-first. Prefers completed_at over started_at (a completed run's
    # "newest" instant is its completion); missing timestamps sort last.
    sims.sort(key=lambda r: (r.get("completed_at") or r.get("started_at") or 0),
              reverse=True)

    if include_remote:
        sims = _append_remote_simulations(sims, ws_root)

    # Capability-matched analysis tools + their launch URLs, per row (Simulations
    # DB "launch into tool" affordance). Best-effort at every layer already
    # (_attach_matched_tools never raises), but belt-and-suspenders here too —
    # this index must never 500 on a tool-matching failure.
    try:
        _attach_matched_tools(sims, ws_root)
    except Exception:  # noqa: BLE001
        pass

    # Always-filled Source column: stamp the inferred workspace source onto any
    # row that still lacks source provenance — including live remote sims added
    # by _append_remote_simulations, which never pass through list_simulations /
    # the JSONL fold and so carry no manifest-derived source_ref.
    _ws_source = _inferred_workspace_source(ws_root)
    if _ws_source is not None:
        for s in sims:
            if not s.get("source_ref"):
                s["source_ref"] = dict(_ws_source)

    from vivarium_workbench.lib.investigation_status import current_branch_slug
    return {"simulations": sims, "current": current_branch_slug(ws_root)}


def resolve_or_fetch_store(workspace: Path, row: dict) -> "tuple[Path | None, tempfile.TemporaryDirectory | None]":
    """Resolve a run's native store, falling back to sms-api when local
    resolution fails and the row's ``remote_origin`` carries a
    ``simulation_id`` — the same S3-streaming fetch
    :func:`build_simulation_run_zip` uses for Results, reused here so
    Viz/Report can render real data for a run whose local materialization
    is gone.

    Returns ``(store_path, tmp_handle)``. ``tmp_handle`` is a
    ``tempfile.TemporaryDirectory`` when a remote fetch happened (the caller
    MUST call ``tmp_handle.cleanup()`` once done with ``store_path``) or
    ``None`` when the store resolved locally (nothing to clean up) or
    couldn't be resolved at all (``store_path`` is also ``None`` in that case).
    """
    import tarfile

    workspace = Path(workspace)

    def _resolve(p: "str | None") -> "Path | None":
        if not p or str(p).startswith(("s3://", "http://", "https://")):
            return None
        pp = Path(p)
        pp = pp if pp.is_absolute() else (workspace / pp)
        try:
            pp = pp.resolve()
        except OSError:
            return None
        return pp if pp.exists() else None

    target = _resolve(row.get("store_path")) or _resolve(row.get("db_path"))
    if target is not None:
        return target, None

    remote_origin = row.get("remote_origin") or {}
    sim_id = remote_origin.get("simulation_id")
    if sim_id is None:
        return None, None

    from vivarium_workbench.lib.sms_api_client import SmsApiClient, SmsApiError
    from vivarium_workbench.lib.workspace_deps_views import _sms_api_base
    tmp = tempfile.TemporaryDirectory()
    try:
        tar_path = SmsApiClient(_sms_api_base()).download_data(int(sim_id), Path(tmp.name))
    except SmsApiError:
        tmp.cleanup()
        return None, None
    extract_dir = Path(tmp.name) / "extracted"
    extract_dir.mkdir()
    with tarfile.open(tar_path) as tf:
        tf.extractall(extract_dir, filter="data")  # noqa: S202 — sms-api's own tar, not user input
    return extract_dir, tmp


def build_simulation_run_zip(workspace: Path, run_id: str) -> "tuple[bytes, str, int]":
    """Zip a run's RAW EMITTER DATA for download (GET /api/simulation-run-download).

    Resolves the run's on-disk store from ``run_id`` via the workspace scan, so
    NO filesystem path is trusted from the client. Prefers the native
    zarr/parquet store directory; falls back to the SQLite ``runs.db`` that holds
    the run's rows. Remote (``s3://``) stores can't be zipped as-is, so those
    fall back to fetching the run's native store from sms-api (which already
    streams it out of S3 with no disk touch on its side) via the run's recorded
    ``remote_origin.simulation_id`` — the same call the remote-run-land flow
    already makes at dispatch time (:mod:`lib.remote_run_views`), reused here
    for an already-landed run whose local materialization no longer exists
    (e.g. the session/build directory it ran in didn't survive a pod restart).

    Returns ``(zip_bytes, filename, status)``:
      200 — zip built;  404 — run not found, and no local or remote store to fetch it from.
    """
    import io
    import re as _re
    import shutil
    import time as _time
    import zipfile

    # DOS/ZIP timestamps can't represent anything before 1980-01-01 —
    # zipfile.ZipInfo.from_file (what zf.write uses internally) raises
    # ValueError outright if a file's mtime predates it. Files landed from a
    # remote (S3) run commonly carry an epoch-0 mtime (S3 has no native mtime
    # concept; nothing sets one on download), which crashed every download of
    # a remote-dispatched run's results with a bare 500. Clamp to "now" only
    # for the handful of files that actually need it — untouched files keep
    # their real mtime.
    _ZIP_EPOCH = 315532800.0  # 1980-01-01T00:00:00Z

    workspace = Path(workspace)
    row = next(
        (r for r in list_simulations(workspace) if r.get("run_id") == run_id),
        None,
    )
    if row is None:
        return b"", "", 404

    target, remote_tmp = resolve_or_fetch_store(workspace, row)
    if target is None:
        return b"", "", 404

    def _write(zf: "zipfile.ZipFile", f: Path, arcname: Path) -> None:
        mtime = f.stat().st_mtime
        if mtime >= _ZIP_EPOCH:
            zf.write(f, arcname)
            return
        # Streamed (not read fully into memory) so this stays fine for large
        # emitter files too.
        zinfo = zipfile.ZipInfo(str(arcname), date_time=_time.localtime(_time.time())[:6])
        zinfo.compress_type = zipfile.ZIP_DEFLATED
        with open(f, "rb") as src, zf.open(zinfo, "w") as dst:
            shutil.copyfileobj(src, dst)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if target.is_dir():
            base = target.parent
            for f in sorted(target.rglob("*")):
                if f.is_file():
                    _write(zf, f, f.relative_to(base))
        else:
            _write(zf, target, Path(target.name))

    if remote_tmp is not None:
        remote_tmp.cleanup()

    safe = _re.sub(r"[^A-Za-z0-9._-]+", "_", str(run_id)).strip("_") or "run"
    return buf.getvalue(), f"{safe}_emitter.zip", 200
