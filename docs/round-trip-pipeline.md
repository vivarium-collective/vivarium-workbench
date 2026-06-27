# Round-trip pipeline: view → sync → run → extend → commit → push

The round-trip loop lets you reproduce a remote dashboard locally, extend it,
and promote it back — all keyed on one **provenance manifest**.

## The manifest (`GET /api/source/manifest`)

```json
{
  "repo": "https://github.com/vivarium-collective/v2ecoli",
  "commit": "<full sha>",
  "branch": "feat/x",
  "workspace": "v2ecoli",
  "lockfile": "uv.lock@<sha256[:12]>",
  "results": { "runs": ["runs.<id>.zarr", "..."] },
  "simulator_id": 42
}
```

Code + deps are pinned (`commit` + `lockfile`); result data is *referenced*,
fetched lazily on view. The manifest drives both directions below. For
build-derived manifests (workspace created from a remote build), `lockfile` is
the hash of the *on-disk* `uv.lock`; this is assumed to match the lockfile at
`repo@commit` — true for a freshly materialized build, but a locally re-synced
build workspace could diverge and cause a false 409 on a subsequent sync.

## Pull — reproduce locally

1. On any dashboard (public read-only included), click **Sync to local** in the
   Source panel → it shows the command:
   `vivarium-dashboard sync <dashboard-url>`
2. Run it locally. `sync` does: clone `repo@commit` → **verify the cloned
   uv.lock hash equals the manifest's** (fidelity gate; aborts on mismatch) →
   `uv sync` → register in the workspace catalog. Optional `--run-post-sync`
   runs cache-rebuild commands declared in a `post_sync` list — **but
   `GET /api/source/manifest` never emits a `post_sync` field by design**, so
   syncing from a dashboard URL is always a no-op for this flag. `post_sync`
   only takes effect when syncing from a *hand-authored* manifest file you
   control. This is intentional: it prevents a remote dashboard from injecting
   shell commands. The flag stays default-off.
3. Open the synced workspace from the switcher and run it. Same commit + same
   lockfile ⇒ same behavior.

## Push — promote back (already built)

1. Extend the synced workspace; commit; `git push` your branch.
2. `POST /api/source/build-remote {repo, branch}` → sms-api builds `repo@commit`,
   returns `simulator_id` + `commit`.
3. `POST /api/source/switch-build {simulator_id}` → the remote dashboard
   materializes that build and switches to it.

## Symmetry

`sync-to-local` is the inverse of `build-via-sms-api`: the former materializes a
workspace on your laptop from `repo@commit`; the latter materializes a build on
the remote from `repo@commit`. Both consume the same manifest — `repo` +
`branch` feed `build-remote`, `commit` + `lockfile` guarantee local fidelity.
