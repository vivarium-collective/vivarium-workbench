# bigraph-loom-explore

Read-only React Flow viewer for process-bigraph composites. Reuses node
renderers and layout from [bigraph-loom](https://github.com/vivarium-collective/bigraph-loom),
stripped of all edit affordances. Designed to be embedded via `<iframe>`
+ `postMessage`.

## Build

    npm install
    npm run build      # → dist/

## Embed

    <iframe src="/path/to/dist/index.html"></iframe>

After load, send the composite state:

    iframe.contentWindow.postMessage({
      type: 'composite:load',
      state: { /* composite state dict */ },
    }, '*');

## Tests

    npm test            # vitest unit tests
    npm run test:e2e    # playwright smoke
