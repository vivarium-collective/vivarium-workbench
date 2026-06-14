# Analyses tab

The **Analyses** tab (`#page-visualizations`) is a gallery of *special, saved,
interactive visualizations* for the workspace — not a catalog of chart classes.
It currently surfaces two kinds of artifact:

1. **Embedded parsimony 3D viewers** — interactive whole-scene structural
   models, loaded from saved packs.
2. **A PTools card** — launches a study's PTools TSV exports in the Pathway
   Tools Omics Viewer.

The class/analysis *catalog* that this tab used to show (Visualization
subclasses + the `v2ecoli` `ANALYSIS_REGISTRY` analyses) now lives under
**Registry → Discovered → Visualizations / Analyses**.

## Parsimony 3D viewer

### Viewer assets

The 3D viewer is the bundle shipped inside the optional `pbg_parsimony`
package (`pbg_parsimony/viewer/{index.html,viewer.js,obj-worker.js}`). The
dashboard serves it under `/parsimony-viewer/*`, resolved at request time from
the installed package — the same pattern as `/bigraph-loom/*`. It is
**feature-detected**: `_parsimony_viewer_dir()` returns `None` when
`pbg_parsimony` is not installed, the route 404s, and the gallery hides the 3D
cards.

### Saved packs (how a 3D scene is discovered)

A saved 3D visualization is a packed scene stored as a workspace artifact:

```
studies/<study>/viz/3d/<name>.pack.json   # the packed scene (rounded/compact)
studies/<study>/viz/3d/<name>.meta.json   # ingredient display names + counts
studies/<study>/viz/3d/meshes/*.obj       # per-ingredient LOD meshes
```

`GET /api/saved-visualizations` scans `studies/*/viz/3d/*.pack.json` across the
workspace (flat and investigation-nested layouts) and returns, per pack:

```json
{
  "parsimony_available": true,
  "saved": [
    {
      "study": "ecoli-3d",
      "name": "ecoli_3d",
      "pack_url": "/studies/ecoli-3d/viz/3d/ecoli_3d.pack.json",
      "meta_url": "/studies/ecoli-3d/viz/3d/ecoli_3d.meta.json",
      "n_placed": 299320,
      "created": 1781414470
    }
  ],
  "ptools": { "configured": false, "studies": [] }
}
```

`n_placed` is summed from the meta sidecar's ingredient counts. The Analyses
gallery embeds each pack with:

```html
<iframe src="/parsimony-viewer/index.html?file=<pack_url>" loading="lazy"></iframe>
```

The viewer (`viewer.js`) reads the pack from `?file=` (or `window.PARSIMONY_PACK`),
loads the `.meta.json` sidecar automatically (it derives it from the pack name),
and fetches each mesh OBJ. **Mesh URLs in the pack must be rooted at the served
workspace tree** — the viewer prepends `/` to any non-absolute mesh URL, so a
pack served at `/studies/ecoli-3d/viz/3d/ecoli_3d.pack.json` must store mesh URLs
like `studies/ecoli-3d/viz/3d/meshes/<f>.obj`.

> Rendering ~370k instances is GPU-heavy. Verify the 3D scene in a normal
> GPU browser — never headless/software-GL.

### Producing a saved pack

A parsimony packing composite produces `pack.json` + `meta.json` + `meshes/`.
Write them into `studies/<study>/viz/3d/<name>/` (mesh URLs rooted at the
workspace tree as above), then revisit the Analyses tab — the 3D cell appears
embedded, with no further configuration.

## PTools card

The PTools card reuses the existing Omics-Viewer launch
(`GET /api/ptools-launch/<study>`). `GET /api/saved-visualizations` reports, in
its `ptools` block, which studies have `**/ptools/*.tsv` exports and whether
PTools is configured (`ui.ptools_server_url` in `workspace.yaml`). When
unconfigured, the card shows a "not configured" hint instead of launch buttons.

Relevant `workspace.yaml` keys (see `docs/ptools-launcher.md`):

```yaml
ui:
  ptools_server_url: "http://your-ptools-host"
  ptools_omics_url_template: "..."     # optional override
  dashboard_public_base_url: "..."     # so the PTools server can fetch the TSV
```
