# bigraph-loom

Read-only [React Flow](https://reactflow.dev) viewer for
[process-bigraph](https://github.com/vivarium-collective/process-bigraph)
composites: stores, processes, wiring, and a per-node inspector (config / input
schema / output schema / process description). Designed to be embedded via
`<iframe>` + `postMessage`, or served as a standalone page.

This repo ships **two artifacts from one source**:

- a **Vite/React app** (`src/`, `index.html`) — the viewer itself, and
- a thin **Python package** (`bigraph_loom/`) that bundles the built front-end
  as package data so host apps can depend on it and serve it directly.

## Python package — how hosts consume it

`npm run build` compiles the app into `bigraph_loom/_dist/`. The Python package
exposes that directory:

```python
import bigraph_loom
bigraph_loom.asset_dir()    # → .../bigraph_loom/_dist  (index.html + assets/)
bigraph_loom.index_html()   # → .../bigraph_loom/_dist/index.html
```

A host application (e.g. **vivarium-dashboard**) adds `bigraph-loom` as a
dependency and serves the static bundle from `asset_dir()` at its own URL prefix
(the dashboard mounts it at `/bigraph-loom`) — no vendored copy of the build.

Install editable for development:

    uv pip install -e .        # or: pip install -e .

## Build

    npm install
    npm run build              # → bigraph_loom/_dist/

The bundle is committed to git so an editable install works without a JS
toolchain present. Editing `src/` + re-running `npm run build` refreshes
`_dist/` in place; an editable install (and any host serving from `asset_dir()`)
picks the new bundle up immediately.

## Embed (iframe + postMessage)

    <iframe src="/bigraph-loom/index.html"></iframe>

After load, send the composite state:

    iframe.contentWindow.postMessage({
      type: 'composite:load',
      state: { /* composite state dict */ },
    }, '*');

Or open the standalone page with `?id=<composite-ref>`, in which case it
self-fetches `/api/composite-state?ref=<id>` from the host.

## Tests

    npm test                   # vitest unit tests
    npm run test:e2e           # playwright smoke
