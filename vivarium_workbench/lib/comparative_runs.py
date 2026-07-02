"""Investigation-level comparative-visualisation rendering (study-run engine
extraction, phase E5 — the final extraction).

``render_investigation_comparative_visualisations`` is the ``ws_root``-
parameterized post-run side-effect that walks each member study of an
investigation set and renders its declared ``comparative_visualizations`` (the
baseline-vs-variants overlays) to ``studies/<slug>/viz/comparative_<name>.html``.

It is invoked from the investigation run-unblocked worker; the legacy
``server.Handler._render_investigation_comparative_visualisations`` instance
method is now a one-line shim delegating here with ``WORKSPACE`` threaded as
``ws_root``, so the live path stays byte-identical.

This module does NOT import ``server``. It reuses already-extracted lib pieces:
``WorkspacePaths`` (workspace layout), ``study_run_state.zarr_store_for_sim``
(E2 — map a sim_name to its per-run zarr store), and
``comparative_viz.render_comparative_time_series`` (the Plotly renderer). The
``job`` progress sink is passed in by the caller (uses ``job.items`` +
``job.update_item`` — not a server dependency).
"""

from __future__ import annotations

import yaml as _yaml

from vivarium_workbench.lib import study_run_state
from vivarium_workbench.lib.comparative_viz import render_comparative_time_series
from vivarium_workbench.lib.workspace_paths import WorkspacePaths


def render_investigation_comparative_visualisations(
    ws_root, inv_slug: str, iset: dict, job
) -> None:
    """Walk each member study + render its ``comparative_visualizations``.

    Comparative viz now lives in the **study** yaml, not the
    investigation yaml — each comparison is between the study's own
    baseline + variants. Single ``studies/<slug>/runs.db`` is queried
    once per trace (filtered by simulation name), and output lands
    in ``studies/<slug>/viz/comparative_<name>.html`` so the
    per-study viz auto-discovery + the downloadable report's
    per-study section pick it up.

    Schema (optional, in each study.yaml):

        comparative_visualizations:
          - name: dnaa-atp-count-vs-time
            title: DnaA-ATP count over time (baseline vs variants)
            observable_path: listeners.itv2.dnaa_atp_count
            y_label: DnaA-ATP count
            runs:
              - {sim_name: dnaa-05-itv2-comparison-baseline, label: Baseline (ITv2)}
              - {sim_name: v2ecoli-baseline-default,         label: v2ecoli default}
              - {sim_name: v2ecoli-with-fxj-params,          label: v2ecoli + FXJ}

    ``sim_name`` matches the ``simulations.name`` column in the
    study's runs.db — the value pbg_runner writes as the run's
    label. For baselines this is typically ``<study-slug>-baseline``;
    for variants it's the variant's own name.
    """
    for member in (iset.get("studies") or []):
        study_slug = member if isinstance(member, str) else (member or {}).get("study")
        if not study_slug:
            continue
        spec_path = WorkspacePaths.load(ws_root).studies / study_slug / "study.yaml"
        if not spec_path.is_file():
            continue
        try:
            study_spec = _yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
        except _yaml.YAMLError:
            continue
        specs = study_spec.get("comparative_visualizations") or []
        if not specs:
            continue
        viz_dir = WorkspacePaths.load(ws_root).studies / study_slug / "viz"
        viz_dir.mkdir(parents=True, exist_ok=True)
        study_db = WorkspacePaths.load(ws_root).studies / study_slug / "runs.db"
        if not study_db.is_file():
            continue
        for cv in specs:
            if not isinstance(cv, dict) or not cv.get("name"):
                continue
            runs = []
            for r in cv.get("runs") or []:
                if not isinstance(r, dict):
                    continue
                sim_name = r.get("sim_name") or r.get("variant") or r.get("name")
                label = r.get("label") or sim_name or "?"
                # XArrayEmitter runs write per-run zarr stores alongside
                # the SQLite db (one zarr dir per run_id). When the sim's
                # most-recent completed run has a zarr store, point
                # comparative_viz at it via zarr_path; the zarr-read
                # adapter (PR #87) extracts the observable across
                # generations. Falls back to SQLite db_path otherwise
                # (legacy single-generation runs).
                zarr_path = study_run_state.zarr_store_for_sim(study_db, sim_name)
                if zarr_path is not None:
                    runs.append({
                        "label": label,
                        "zarr_path": zarr_path,
                        "sim_name": sim_name,
                    })
                else:
                    runs.append({
                        "label": label,
                        "db_path": study_db,
                        "sim_name": sim_name,
                    })
            if not runs:
                continue
            out_path = viz_dir / f"comparative_{cv['name']}.html"
            try:
                render_comparative_time_series(
                    runs=runs,
                    observable_path=cv.get("observable_path", ""),
                    title=cv.get("title", cv["name"]),
                    y_label=cv.get("y_label", ""),
                    output_path=out_path,
                    observable_index=cv.get("observable_index"),
                    target_band=cv.get("target_band"),
                    target_band_label=cv.get("target_band_label"),
                )
            except Exception as e:  # noqa: BLE001
                job.update_item(
                    len(job.items) - 1,
                    comparative_viz_warning=f"{study_slug}/{cv['name']}: {e}",
                )
