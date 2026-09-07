"""Land a remote simulation's NATIVE store into a study's run directory.

Mirror-the-store-format: extract the run's `/data` tar.gz and place the native
store unmodified where the dashboard's native chart reader expects it
(`<study>/runs.<run_id>.zarr` for zarr; `<study>/parquet-runs/<experiment_id>/`
for parquet), then record a runs_meta row. No reconstruction of leaf data — a
remote `seed_NN/store.zarr` is internally identical to the dashboard's expected
`runs.<run_id>.zarr`; only the path differs.

A multi-seed dispatch ships ONE `seed_NN/store.zarr` per seed in the same tar,
each independently rooted (confirmed against real GovCloud/Ray output: every
seed writes its own top-level zarr group). Landing merges every seed found
into the same destination root — each seed's `experiment_id=*-seedNN`
partition is already uniquely named per seed, so a plain union of top-level
children is safe there.

A second, DIFFERENT zarr convention is also landed here: BatchBaselineRunner /
LineageProcess's own native per-lineage store, named
`<experiment_id>_v<variant>_s<seed>.zarr` at the filesystem level (see
v2ecoli.steps.batch_baseline_runner._lineage_store_path) — the real shape a
batch produces when dispatched through the generic run_pbg.py runner (backlog
items 26/27), since that path never restructures its own output the way
scripts/run_batch_baseline_ray.py used to. Unlike the seed_NN convention,
EVERY lineage in one batch shares the SAME experiment_id, so uniqueness only
appears two levels inside each store (`experiment_id=X/variant=Y/lineage_seed=Z`
— confirmed against a real fixture, `pbg_emitters`' XArrayEmitter always nests
this 3-level partition path). A plain top-level union would keep only the
first-processed lineage and silently drop every other seed's data — the exact
shape of bug #674, recurring one convention later. `_merge_zarr_tree` recurses
past whatever depth a collision actually occurs at, so both conventions land
correctly through the same merge logic without hardcoding either one's depth.
"""

from __future__ import annotations

import json
import shutil
import tarfile
import tempfile
import time as _time
from pathlib import Path

from vivarium_workbench.lib import composite_runs as cr


class RemoteRunSeedCountMismatch(RuntimeError):
    """The number of seed/lineage stores actually found in a landed tar does
    not match what was requested/expected for the dispatch (P1-11, audit
    §3.9). Raised BEFORE anything is written to ``study_dir`` or ``runs.db``
    -- a partially-arrived campaign must never be recorded as a completed,
    trustworthy run."""


def _fold_one_analysis_json(analysis_json_path: Path, extract_root: Path) -> list[dict]:
    """Map ONE ``v2ecoli-analyze`` ``analysis.json`` into per-analysis fold entries
    ``{name, written, errors}`` -- the shape composite_flush's local dispatch
    produces (``written`` is a list of output-file paths, NOT a bool).

    ``analysis.json`` shape (written by v2ecoli.workflow.analysis_runner.main at
    the config's ``out_dir``): the RESULTS are keyed ``<scale>/<name>/<group>``;
    per-analysis status is ``summary[<scale>][<name>] = "ok"|"partial"|"error"|
    "missing_column"``; structural errors (declared-but-unregistered analyses)
    are a top-level ``errors`` list; a failed group is inline as
    ``<scale>/<name>/<group> == {"error": "..."}``. There is NO ``written`` key
    and NO ``_manifest.json`` -- v2ecoli never writes one (an earlier landing
    globbed a phantom filename; only manual-flush runs ever produced it).

    ``written`` is derived from the output files the analysis actually landed
    next to ``analysis.json`` -- ``viz/<name>__*.html`` and ``ptools/<name>__*.tsv``
    (the ``{name}__{group}`` filename convention) -- recorded relative to the
    landed tree so the paths stay meaningful after the temp extract is cleaned up.
    """
    data = json.loads(analysis_json_path.read_text(encoding="utf-8"))
    out_dir = analysis_json_path.parent
    summary = data.get("summary")
    top_errors = data.get("errors") or []
    entries: list[dict] = []
    if not isinstance(summary, dict):
        return entries
    for scale, name_status in summary.items():
        if not isinstance(name_status, dict):
            continue
        scale_results = data.get(scale) if isinstance(data.get(scale), dict) else {}
        for name in name_status:
            written = []
            for sub in ("viz", "ptools"):
                subdir = out_dir / sub
                if subdir.is_dir():
                    written.extend(
                        p.relative_to(extract_root).as_posix()
                        for p in sorted(subdir.glob(f"{name}__*"))
                        if p.is_file()
                    )
            # Structural errors naming this analysis, plus any inline failed group.
            errors = [e for e in top_errors if isinstance(e, dict) and e.get("name") == name]
            analysis_results = scale_results.get(name) if isinstance(scale_results, dict) else None
            if isinstance(analysis_results, dict):
                for group, payload in analysis_results.items():
                    if isinstance(payload, dict) and "error" in payload:
                        errors.append({"scale": scale, "name": name,
                                       "group": group, "error": payload["error"]})
            entries.append({"name": name, "written": written, "errors": errors})
    return entries


def fold_analyses(extract_root: Path, ws_root: Path, run_id: str) -> None:
    """Fold any standalone-analysis output already present in the landed tar into
    ``.pbg/runs/<run_id>/analyses.json`` -- the same local artifact contract
    composite_flush.run_flush's local (non-remote) analyses dispatch already
    produces, so the existing Analyses button needs no changes to render it.

    The real output ``v2ecoli-analyze`` lands at each analysis' ``out_dir`` is
    ``analysis.json`` (see ``_fold_one_analysis_json``); this globs those, not
    the phantom ``_manifest.json`` a prior version looked for (which v2ecoli
    never writes).

    There is no status/poll endpoint for the K8s analysis job (see
    SmsApiClient.run_analysis), so completion is detected the same way the rest
    of this pipeline detects it: by finding the output already landed, here,
    rather than by polling sms-api. If the job hasn't finished by the time this
    run is landed, this is a no-op -- landing again later (once the analysis
    has actually completed) will pick it up.

    Public (not module-private) since Task 5c gave it a second call site:
    ``run_runner._execute_remote`` folds the composite deployment path's own
    ``run_id`` directly, without going through the study-shaped
    ``land_remote_run`` (which mints its own, unrelated run_id).
    """
    results = sorted(extract_root.glob("**/analyses/*/analysis.json"))
    if not results:
        return
    from vivarium_workbench.lib.workspace_paths import WorkspacePaths

    entries: list[dict] = []
    for result_path in results:
        entries.extend(_fold_one_analysis_json(result_path, extract_root))
    if not entries:
        return
    entries.sort(key=lambda e: str(e.get("name")))
    run_dir = WorkspacePaths.load(ws_root).pbg / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "analyses.json").write_text(json.dumps(entries, indent=2), encoding="utf-8")


def _detect_and_locate_all(extract_root: Path) -> tuple[str, list[Path]]:
    """Find every native store under an extracted tar. Returns (kind, source_paths):
    one entry per lineage/seed for zarr, or a single entry for parquet.

    Two zarr layouts are recognized (see module docstring): the restructured
    `seed_NN/store.zarr` shape, and BatchBaselineRunner/LineageProcess's own
    native per-lineage naming (`<experiment_id>_v<variant>_s<seed>.zarr`).
    """
    seed_stores = sorted(extract_root.glob("**/seed_*/store.zarr"))
    if seed_stores:
        return "zarr", [s for s in seed_stores if s.is_dir()]
    native_stores = sorted(p for p in extract_root.glob("**/*_v*_s*.zarr") if p.is_dir())
    if native_stores:
        return "zarr", native_stores
    # parquet: locate the experiment dir that contains a history/ subtree of .pq files
    pq = next(extract_root.glob("**/history/**/*.pq"), None)
    if pq is not None:
        # the experiment root is the parent of the `history` dir
        for parent in pq.parents:
            if parent.name == "history":
                return "parquet", [parent.parent]
    raise FileNotFoundError(
        "no zarr (seed_NN/store.zarr or <experiment_id>_v<variant>_s<seed>.zarr) "
        f"or parquet (history/**/*.pq) store in {extract_root}"
    )


def _merge_zarr_tree(src: Path, dest: Path) -> None:
    """Merge src's children into dest, recursing into a name collision instead of
    skipping it outright.

    The fast path (no collision) is an untouched bulk `shutil.copytree`/`copy2` —
    identical to the old shallow union, and what every level of the seed_NN/store.zarr
    convention always takes, since its top-level partition names never collide across
    seeds. A collision only arises for a convention whose uniqueness lives deeper than
    the top level (the native per-lineage store's shared `experiment_id=X/variant=Y`
    prefix — see module docstring) — recursing past it, rather than skipping the whole
    subtree, is what actually merges every lineage's data instead of keeping only the
    first one processed. A genuine leaf-level collision (both sides are the SAME file,
    e.g. the trivial shared zarr group marker) is left as first-wins, matching a745f899.
    """
    dest.mkdir(parents=True, exist_ok=True)
    for child in src.iterdir():
        target = dest / child.name
        if not target.exists():
            if child.is_dir():
                shutil.copytree(child, target)
            else:
                shutil.copy2(child, target)
        elif child.is_dir() and target.is_dir():
            _merge_zarr_tree(child, target)
        # else: both exist and at least one is a file -- dest's version wins.


def _count_parquet_rows(source_dir: Path) -> int | None:
    """Best-effort REAL step count for a landed parquet store: the row count
    of its own ``history/**/*.pq`` files (mirrors the same technique already
    used by ``explorer_data.list_runs`` for locally-discovered parquet runs).
    This is ground truth from the data that actually landed, not a guess —
    returns ``None`` (never 0) when pyarrow isn't installed or no history
    files are found, so a genuinely-unknown count isn't confused with a real
    zero-step run."""
    try:
        import pyarrow.parquet as pq
    except ImportError:
        return None
    total = 0
    found = False
    for f in sorted(source_dir.rglob("*.pq")):
        try:
            total += pq.read_metadata(str(f)).num_rows
            found = True
        except Exception:  # noqa: BLE001 — best-effort, never block landing
            continue
    return total if found else None


def land_remote_run(
    study_dir: Path,
    *,
    spec_id: str,
    simulation_id: int,
    experiment_id: str,
    commit: str,
    tar_path: Path,
    ws_root: Path | None = None,
    label: str | None = None,
    s3_uri: str | None = None,
    image: str | None = None,
    composite_doc_hash: str | None = None,
    merged_config_hash: str | None = None,
    n_steps: int | None = None,
    expected_seeds: int | None = None,
) -> str:
    """Extract tar_path, place the native store(s) in study_dir, record runs_meta; return run_id.

    ``ws_root`` is optional (defaults to no analyses-folding) so existing callers
    that only land simulation output, with no analysis ever triggered for that
    run, are unaffected.

    Provenance (P1-11, audit §3.9) — every one of these must be sourced from
    sms-api (the server that actually executed the run), NEVER inferred from
    the landing laptop's own checkout:

    ``commit``
        The commit sms-api actually built and ran (e.g. resolved from the
        simulation's ``simulator_id`` against sms-api's own simulator
        registry) — not the workspace's local HEAD. Recording the laptop's
        HEAD here was the found bug: the two can differ any time the operator
        has switched branches locally since dispatching.
    ``image``
        The tag/digest of the container image sms-api actually ran (e.g. the
        commit-derived ECR tag sms-api registered for this ``simulator_id``).
    ``composite_doc_hash`` / ``merged_config_hash``
        Hashes of the composite document and the fully-merged run config
        sms-api resolved server-side, when the caller has them (e.g. hashing
        the ``config`` sms-api's own ``GET /simulations/{id}`` returns). Pass
        ``None`` rather than a value recomputed from a local/possibly-stale
        copy — an absent hash is honest; a wrong one is not.
    ``n_steps``
        The real number of steps executed. When the caller doesn't know it
        (sms-api doesn't track it as a scalar today — see follow-up in
        PR description), a parquet-landed run's real count is derived here
        from the landed data itself (``_count_parquet_rows``); a zarr-landed
        run without deps to read it back stays ``None`` rather than a
        fabricated 0.
    ``expected_seeds``
        The seed count requested/expected for this dispatch. Checked against
        the number of seed/lineage stores actually found in the tar; a
        mismatch raises :class:`RemoteRunSeedCountMismatch` before anything
        is written, so an incomplete campaign is never recorded as a
        trustworthy, completed run.
    """
    study_dir = Path(study_dir)
    study_dir.mkdir(parents=True, exist_ok=True)

    from vivarium_workbench.lib.remote_pinned import remote_deployment_name
    deployment = remote_deployment_name()

    provenance = {
        "simulation_id": simulation_id,
        "experiment_id": experiment_id,
        "commit": commit,
        "image": image,
        "composite_doc_hash": composite_doc_hash,
        "merged_config_hash": merged_config_hash,
        "backend": "ray",
        "source": deployment,
        "s3_uri": s3_uri,
    }
    run_id = cr.generate_run_id(spec_id, params=provenance)

    with tempfile.TemporaryDirectory() as td:
        extract_root = Path(td)
        with tarfile.open(tar_path, "r:gz") as tar:
            tar.extractall(extract_root, filter="data")  # noqa: S202 — trusted internal artifact from our own API
        kind, sources = _detect_and_locate_all(extract_root)

        landed_seeds = len(sources)
        provenance["landed_seeds"] = landed_seeds
        if expected_seeds is not None and landed_seeds != expected_seeds:
            raise RemoteRunSeedCountMismatch(
                f"simulation {simulation_id}: landed {landed_seeds} seed(s)/"
                f"lineage(s) but {expected_seeds} were requested — refusing "
                "to record this as a completed run (sms-api may still be "
                "writing output, or a seed's dispatch failed silently)"
            )

        if kind == "zarr":
            dest = study_dir / f"runs.{run_id}.zarr"
            if dest.exists():
                shutil.rmtree(dest)
            dest.mkdir(parents=True)
            # Merge every seed/lineage's store into one root -- see _merge_zarr_tree
            # for why this recurses into collisions rather than skipping them.
            for src in sources:
                _merge_zarr_tree(src, dest)
        else:
            dest = study_dir / "parquet-runs" / experiment_id
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(sources[0], dest)
            if n_steps is None:
                n_steps = _count_parquet_rows(dest)

        if ws_root is not None:
            fold_analyses(extract_root, ws_root, run_id)

    provenance["store_path"] = str(dest)
    resolved_n_steps = n_steps if n_steps is not None else 0
    started = _time.time()

    # Build the replay manifest OURSELVES, with ws_root=None -- a remote run
    # never executed in the landing laptop's workspace checkout, so its
    # code_version/environments must never be inferred from that checkout
    # (the "partly wrong" half of the found bug). Passing this manifest
    # explicitly to save_metadata also SKIPS its own auto-build-from-
    # workspace fallback, which is what previously overwrote a correct
    # `commit` with the local HEAD.
    manifest = cr.build_run_manifest(
        spec_id=spec_id, params=provenance, n_steps=resolved_n_steps,
        emitter=kind, emit_paths=[], runtime={}, origin="remote",
        study=None, generation_id=None, ws_root=None,
    )
    manifest["code_version"] = {
        "git_sha": commit or None,
        "package": None,
        "repo": None,
        "remote_url": None,
        "image": image,
    }
    manifest["environments"] = [{
        "role": "primary",
        "repo": None,
        "ref": None,
        "commit": commit or None,
        "remote_url": None,
        "lockfile_hash": None,
        "image": image,
    }]

    conn = cr.connect(study_dir / "runs.db")
    try:
        cr.save_metadata(
            conn,
            spec_id=spec_id,
            run_id=run_id,
            params=provenance,
            label=label or f"Remote run ({deployment})",
            started_at=started,
            n_steps=resolved_n_steps,
            # Still pass the workspace (for the durable JSONL run-log append
            # only) — `manifest` above is what makes save_metadata skip its
            # own local-checkout-inferring auto-build.
            workspace=ws_root,
            manifest=manifest,
        )
        cr.complete_metadata(conn, run_id=run_id, n_steps=resolved_n_steps, status="completed")
        # item 84: remove the dispatch-time pending placeholder (remote_run_
        # submit's own `remote-pending-<simulation_id>` row, written so the
        # Runs tab shows the campaign immediately instead of only once landed)
        # now that this real row supersedes it. Safe to call unconditionally —
        # deleting a run_id that was never written (any simulation landed via
        # an older/other path, or a simulation never dispatched through
        # remote_run_submit at all) is a silent no-op.
        cr.delete_run(conn, run_id=f"remote-pending-{simulation_id}")
    finally:
        conn.close()

    return run_id
