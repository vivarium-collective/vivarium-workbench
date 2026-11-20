"""Archive landing shared by every remote path (docs/backend-adapters.md §3).

``land_composite_results`` lands a compose-api / generic composite results archive into the run store;
``fold_analyses`` folds a landed tree's ``analysis.json`` files into ``.pbg/runs/<run_id>/analyses.json``;
``land_ptools_tsvs`` copies its PTools exports (the PTools writer, §3). The SMS store landing (``seed_NN`` and
native-lineage zarr, parquet, seed counts) is the plugin's: ``vivarium_workbench_sms.sms_landing`` (step P2,
Runs c1), which reads these helpers through this module.
"""

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

from vivarium_workbench.lib import composite_runs as cr


def _is_root_pber(name: str) -> bool:
    """A ``results_<date>.pber`` at the archive's root (compose-api zips ``output/`` as ``./<file>``)."""
    base = name[2:] if name.startswith("./") else name
    return "/" not in base and base.startswith("results_") and base.endswith(".pber")


def land_composite_results(archive: Path, db_file: Path, run_id: str, emitter: "str | None" = None) -> int:
    """Land a generic composite run's results archive into the run store; return the steps landed.

    A compose simulation that ran a document serves a zip: ``emitter_history.json`` (the emitter's states,
    ``{<emitter name>: [state per emitted step]}``), the event log and the final state. compose-api's runners
    (pbest, a workspace image's own runner) write the same mapping as ``results_<date>.pber`` at the archive's
    root instead (``gather_emitter_results`` as JSON, keyed by the emitter's path); either is read. The run store's
    ``history`` table is one row per step -- ``(simulation_id, step, global_time, state)``, the table every
    existing viewer reads -- so landing is that mapping: each state stored whole, its time taken from the
    state when it carries one. Landing a run again replaces its rows. An archive that is not exactly one
    emitter's history is refused before anything is written: which emitter a state came from cannot be
    guessed, and an unreadable result must not be recorded as a run. ``emitter`` names the one to land when the
    caller knows it (the emitter it added to the document), and the others are then left alone.
    """
    with zipfile.ZipFile(archive) as z:
        names = z.namelist()
        pber = sorted(n for n in names if _is_root_pber(n))
        if "emitter_history.json" in names:
            member = "emitter_history.json"
        elif len(pber) == 1:
            member = pber[0]
        elif pber:
            raise ValueError(f"{Path(archive).name} holds several results files ({', '.join(pber)}): which run is this?")
        else:
            raise ValueError(f"{Path(archive).name} has no emitter_history.json or results_*.pber: "
                             "not a composite run's results")
        history = json.loads(z.read(member))
    if emitter is not None and isinstance(history, dict) and isinstance(history.get(emitter), list):
        history = {emitter: history[emitter]}
    if not isinstance(history, dict) or len(history) != 1 or not isinstance(next(iter(history.values())), list):
        raise ValueError(
            f"{member.lstrip('./')} must hold exactly one emitter's list of states, got "
            f"{sorted(history) if isinstance(history, dict) else type(history).__name__}")
    (states,) = history.values()
    rows = [
        (run_id, step, state.get("global_time") if isinstance(state, dict)
         and isinstance(state.get("global_time"), (int, float)) else None, json.dumps(state))
        for step, state in enumerate(states)
    ]
    conn = cr.connect(db_file)
    try:
        cr.ensure_history_table(conn)
        with conn:
            conn.execute("DELETE FROM history WHERE simulation_id = ?", (run_id,))
            conn.executemany("INSERT INTO history (simulation_id, step, global_time, state) VALUES (?, ?, ?, ?)", rows)
    finally:
        conn.close()
    return len(rows)


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


def land_ptools_tsvs(
    extract_root: Path, ws_root: Path, run_id: str, study_dest: "Path | None" = None
) -> int:
    """Copy any PTools TSV exports (``**/ptools/*.tsv``) out of a landed remote-run
    tar into ``<ws>/.pbg/runs/<run_id>/ptools/`` (the run's own artifacts), and, when
    ``study_dest`` is given, ALSO into ``<study_dest>/ptools/``. Without this, a
    GovCloud run's ptools TSVs — present in the tar our backend-aware ``/results``
    route streams — would be extracted to a temp dir and discarded with everything
    but ``analyses.json``.

    Why the study copy: the Pathway Tools plugin (pbg-ptools) discovers
    ``<study>/**/ptools/*.tsv`` only, and its server fetches each TSV over HTTP from
    this workbench, which never serves a dot-segment such as ``.pbg``
    (``static_serving.is_servable``). The run-folder copy is therefore invisible to
    the viewer and unreachable for it; a landing INTO A STUDY leaves the exports
    under the study directory, in the run's own subfolder so two landed runs never
    collide.

    Returns the number of TSVs landed in the run folder. Best-effort / idempotent
    (overwrites); a copy failure is skipped, never raised, so an otherwise-completed
    run still lands.
    """
    tsvs = sorted(extract_root.glob("**/ptools/*.tsv"))
    if not tsvs:
        return 0
    from vivarium_workbench.lib.workspace_paths import WorkspacePaths

    dests = [WorkspacePaths.load(ws_root).pbg / "runs" / run_id / "ptools"]
    if study_dest is not None:
        dests.append(Path(study_dest) / "ptools")
    n = 0
    for i, dest in enumerate(dests):
        dest.mkdir(parents=True, exist_ok=True)
        for src in tsvs:
            try:
                shutil.copy2(src, dest / src.name)
                if i == 0:
                    n += 1
            except OSError:
                continue
    return n
