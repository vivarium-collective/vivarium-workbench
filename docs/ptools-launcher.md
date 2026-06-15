# PTools Omics Viewer Launcher

The dashboard can launch the [Pathway Tools Omics Viewer](https://bioinformatics.ai.sri.com/ptools/) directly from a study run, overlaying per-run simulation data onto the metabolic network.

## Data flow

```
simulation run
  └─ analyses write
       studies/<name>/ptools/<analysis>__<partition>.tsv   (frame-ID × timepoint)
            │
            │  served at  /<relpath>  by the generic static handler
            │             (MIME: text/tab-separated-values)
            ▼
       dashboard HTTP server
            │
            │  "Launch ptools" button → GET /api/ptools-launch/<study>?run=…
            │  → resolves TSV URL → builds Omics Viewer URL → window.open()
            ▼
       Pathway Tools Omics Viewer
            │  fetches the TSV over HTTP from the dashboard
            ▼
       Cellular Overview painted with simulation data
```

## Configuration

Add an `ui:` block to `workspace.yaml`:

```yaml
ui:
  # Required: URL of the running Pathway Tools server (no trailing slash).
  ptools_server_url: "http://ptools.mylab.org"

  # Optional: URL template for the Omics Viewer endpoint.
  # Placeholders: {server}, {orgid}, {tsv_url}.
  # DEFAULT — TBD: finalize against the live server (see "Finalization" below).
  ptools_omics_url_template: "{server}/overviewsWeb/celOv.shtml?orgid={orgid}&data-file={tsv_url}"

  # Optional: externally-reachable base URL of this dashboard, used when the
  # PTools server is on a different host from the browser.
  # If unset the dashboard infers it from the browser's Host header.
  dashboard_public_base_url: "http://dashboard.mylab.org:8771"
```

### Finalization step (TBD)

The exact Omics Viewer endpoint varies by PTools version.  To capture the correct URL:

1. Open the Cellular Overview in your Pathway Tools instance.
2. Choose **Operations → Generate Bookmark for Current Cellular Overview**.
3. Copy the resulting URL — it shows the exact endpoint and `data-file=` (or equivalent) parameter name.
4. Set `ui.ptools_omics_url_template` in `workspace.yaml` with `{server}`, `{orgid}`, and `{tsv_url}` replacing the hostname, organism ID, and file URL respectively.

## Reachability requirement

The Pathway Tools server fetches the TSV file over HTTP from the URL the
dashboard provides.  That URL must be reachable **from the PTools server**,
not just from the browser:

- If browser, dashboard, and PTools all run on the same machine, `localhost` works.
- If PTools runs on a different host (common in lab environments), set
  `ui.dashboard_public_base_url` to the dashboard's externally-reachable address
  so the PTools server can fetch the file.

## UI

A **"Launch ptools"** button appears in the Runs table of each study.  Clicking it:

1. Calls `GET /api/ptools-launch/<study>?run=<run_id>`.
2. If a TSV is found and `ptools_server_url` is configured, opens the Omics Viewer in a new tab.
3. If `ptools_server_url` is not set, shows an inline message prompting configuration.
4. If no TSVs exist for the run, shows a message to run the ptools analyses first.

## TSV format

Each TSV file is a frame-ID × timepoint table (rows = metabolites/genes, columns = time points), matching the Pathway Tools Omics Viewer input format.  Files are written to `studies/<name>/ptools/<analysis>__<partition>.tsv` by the simulation's analysis pipeline.

## Filesystem mode (PTools reads the file from disk)

Some Pathway Tools builds — including **sms-ptools 0.8.2** — load omics data via
`overview-expression-load-omics-from-server`, which reads the data file from the
**PTools server's own filesystem** and does *not* fetch the URL over HTTP. For
these, set `ui.ptools_data_dir` to the container-side path of the dashboard's
`--workspace` root and mount the workspace into the container:

```yaml
ui:
  ptools_server_url: "http://localhost:1555"
  ptools_data_dir: "/ptools-data"
```

```bash
docker run -d --name sms-ptools -p 1555:1555 \
  -v /path/to/v2ecoli/workspace:/ptools-data/workspace:ro \
  ghcr.io/vivarium-collective/sms-ptools:0.8.2
```

The launcher then passes `url=<ptools_data_dir>/<rel>` (a server-local path)
instead of an HTTP URL. When `ptools_data_dir` is unset, the HTTP-fetch behavior
(via `dashboard_public_base_url`) is used.
