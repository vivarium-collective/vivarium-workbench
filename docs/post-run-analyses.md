# Post-run Analysis Hook

After a study run completes (baseline or variant), the dashboard automatically
runs any Analysis Steps declared in the study's `analyses:` list over the run's
parquet emitter output.  This mirrors how `visualizations:` triggers HTML
rendering after each run.

## Study spec declaration

```yaml
analyses:
  - name: ptools_rna
    params:
      n_tp: 8
  - name: ptools_rxns
    params:
      n_tp: 8
  - name: central_carbon_metabolism_scatter
```

Each entry requires a `name` matching a registered v2ecoli Analysis class (i.e.
a key in `v2ecoli.workflow.analysis.ANALYSIS_REGISTRY`).  `params` is optional.
Absent `analyses:` (or an empty list) means no analyses run — fully backward-
compatible with existing specs.

## How it works

1. **After run success** — both `_post_study_run_baseline_for_test` and
   `_post_study_run_variant_for_test` call `_run_study_analyses(study_dir, spec,
   run_id, ws_root)` right after `_run_post_run_scripts`.

2. **analysis_options construction** — `_build_analysis_options(entries)` looks
   up each entry's `name` in `ANALYSIS_REGISTRY` to discover its `.scale`,
   producing `{scale: {name: params}}` for `v2ecoli.workflow.analysis_runner.run_analyses`.

3. **Parquet sweep dir** — `study_charts._latest_parquet_for_study(study_dir)`
   finds the most-recently-modified experiment dir under
   `<study>/parquet-runs/<exp>/history`.  The experiment dir (parent of `history/`)
   is used as `sweep_dir` for `run_analyses`, which globs
   `**/history/**/*.pq` under it.  If no parquet run exists an error is
   recorded and the hook returns early.

4. **sim_data resolution** — ptools analyses require the workspace's ParCa
   sim_data pickle.  The hook globs for `simData*.cPickle` / `sim_data*.cPickle`
   under `out/` in `ws_root` (then broader patterns).  If found, the path is
   passed to `run_analyses(..., sim_data_path=<path>)` — the explicit-path
   feature added in v2ecoli PR #165.  If none is found, `run_analyses` is still
   called with `sim_data_path=None`; analyses that do not need sim_data will run
   normally while ptools analyses will record a per-group error (they never crash
   the run handler).

5. **Outputs** — written under the experiment dir:
   - `ptools/*.tsv` — ptools TSV files (one per analysis × group)
   - `viz/*.html` — HTML views from View analyses
   - `analysis.json` — full nested results dict

   Paths are returned in `response["analysis_files"]`; per-group errors in
   `response["analysis_errors"]`.  The run handler never raises on analysis
   failure — all errors are surfaced in the response payload.

6. **Synchronous** — the hook runs before the HTTP response is sent, so outputs
   are on disk by the time the client refreshes the Launch-ptools control.

## Locating sim_data

The hook searches (in order):

```
<ws_root>/out/**/simData*.cPickle
<ws_root>/out/**/sim_data*.cPickle
<ws_root>/simData*.cPickle
<ws_root>/sim_data*.cPickle
<ws_root>/**/simData*.cPickle
<ws_root>/**/sim_data*.cPickle
```

The v2ecoli workflow typically writes the ParCa output to
`out/workflow/simData.cPickle`, which the first pattern matches.

## Concerns and limitations

- **Parquet emitter required** — analyses run over the hive-partitioned parquet
  produced by the `parquet` emitter.  SQLite-only runs produce no parquet, so
  the hook records an error and skips analyses.
- **Synchronous latency** — ptools analyses can take tens of seconds.  For long
  analyses consider adding them as `post_run_scripts` instead (subprocess) or
  wiring up an async job queue.
- **Single parquet run** — the hook uses the most-recently-modified experiment
  dir; multi-run sweeps accumulate separate parquet dirs.  Each baseline/variant
  run triggers the hook over its own latest parquet.
