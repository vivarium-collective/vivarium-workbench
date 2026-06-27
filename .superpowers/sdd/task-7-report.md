# Task 7 Report: Round-trip pipeline documentation

## Status: DONE

**Commit:** `765a4cb` — `docs: document the round-trip pipeline (manifest, pull-sync, push-promote)`

## Doc created

`docs/round-trip-pipeline.md` — 50 lines covering:
- Manifest shape (`GET /api/source/manifest`)
- Pull direction (Sync to local button → CLI → clone@commit + lockfile verify + uv sync + catalog register)
- Push direction (`build-remote` → `switch-build`)
- Symmetry section

## Accuracy verification

All details in the brief's draft matched the actual implementation:

| Detail | Brief says | Code confirms |
|--------|-----------|---------------|
| Manifest endpoint | `GET /api/source/manifest` | `vivarium_dashboard/api/app.py:2088` |
| Lockfile format | `uv.lock@<sha256[:12]>` | `provenance_manifest.py:45` returns `f"uv.lock@{digest}"` |
| Fidelity gate | aborts on mismatch | `sync_materialize.py:37` returns `409` on mismatch |
| Button command | `vivarium-dashboard sync <dashboard-url>` | `branch-source.js:250` emits exactly this |
| run_post_sync | off by default | `sync_workspace.py:30` `run_post_sync: bool = False` |
| Push endpoints | `build-remote` / `switch-build` | `server.py` route table |

**No corrections needed.** Brief was accurate.

---

## Final-review fixes: I1 + I2

### I1 — `--run-post-sync` doc accuracy (`docs/round-trip-pipeline.md`)

Updated the Pull section's description of `--run-post-sync` to state explicitly
that `GET /api/source/manifest` never emits a `post_sync` field by design, so
syncing from a dashboard URL is always a no-op for this flag. `post_sync` only
takes effect with a hand-authored manifest file. Added that the flag stays
default-off as the intentional security boundary preventing remote command
injection.

### I2 — Build-workspace lockfile coupling (`vivarium_dashboard/lib/provenance_manifest.py` + docs)

Added a block comment in `build_manifest` (the `isinstance(build_meta, dict)`
branch) noting that `lockfile` is always taken from the on-disk `uv.lock`, not
from the lockfile at `repo@commit`; this is correct for freshly materialized
builds but a locally re-synced build workspace could diverge and trigger a
false 409. No logic changed.

Also added one sentence to the manifest description in `docs/round-trip-pipeline.md`
conveying the same assumption for build-derived manifests.

### Test run

```
PYTHONPATH=/Users/eranagmon/code/vdash-ro \
  /Users/eranagmon/code/v2ecoli/.venv/bin/python \
  -m pytest tests/test_provenance_manifest.py -v
```

Result: **4 passed in 1.55s** — all green, no regressions.
