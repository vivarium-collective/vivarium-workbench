# Managed session-binding lifecycle — how a materialized `(repo, ref)` becomes a session's workspace

Design proposal for the one piece deliberately deferred while building
materialization (materialization-lifecycle.md §11): **how a managed
`(repo, ref)` a session materializes actually becomes that session's active
workspace**, and what requests observe while it is being prepared.

Context: `docs/session-registry.md` (§5 states, §8 switch), `docs/materialization-lifecycle.md`
(§3 async, §4 MATERIALIZING, §11 decisions log). Ties them together.

Status: **proposed.** Not implemented — this resolves the routing/precedence
questions on paper so the hot-path change can be reviewed before it is written.

---

## 1. The gap this closes

There are **two** per-session binding mechanisms today, and they don't meet:

1. **`session_registry`** — `session_key → source_path`. This is what request
   routing actually reads: `workspace_context.resolve(key)` → `session_registry.get(key).source_path`
   (else the process default). `/api/source/switch[-build]` writes it via `rebind`.
   Everything on-disk and **in-place** (§2a) flows through here.
2. **`session_env`** — `session_key → materialization state` (in-place `ready`, or a
   managed job's `cloning → syncing → ready|failed` with the staged path +
   interpreter). This is **only** read by the status poll; **routing never
   consults it.**

So `POST /api/source/materialize-repo` provisions + caches a managed env, but
**nothing binds the session to the staged checkout** — the materialized workspace
is unreachable. Closing that is the last step that makes managed materialization
actually serve a workspace.

The deeper issue is the **split**: two mechanisms, one of which (materialization)
routing ignores. This proposal **unifies them** into one binding a request
resolves through.

## 2. Goals / non-goals

**Goals**
- A managed `(repo, ref)` that reaches `ready` becomes the session's active
  workspace — routing, env worker, and caches all follow — with **no HTTP
  request ever blocking** on the minutes-scale prepare (materialization §3).
- **Most-recent-switch-wins**: the session's workspace is whatever it last
  switched to (in-place or managed); no stale precedence between the two
  mechanisms.
- **A switch to a not-yet-ready managed source does not disrupt the session's
  current workspace** — the user keeps working until the new one is ready
  ("keep-prior"), then it flips.
- Resolution stays **side-effect-free** (safe on a GET) and **cheap** (it is on
  every request).

**Non-goals (v1)**
- Serving the half-materialized workspace (science-only reads off the staged tree
  after clone but before sync, materialization §4) — a later refinement (§6).
- Per-session worktree isolation (§5 of materialization-lifecycle) — staging is
  still keyed per `(repo, commit)` and shared; fine for read-mostly managed
  sources.
- Cancelling a superseded in-flight job — a superseded materialize simply runs to
  completion and caches its venv (harmless; GC reclaims it, §9d).

## 3. The unified binding — `committed` + `pending`

Replace the two mechanisms with **one per-session record** holding two slots:

```
SessionBinding {
  committed : LocalPath | None     # the workspace being SERVED right now
                                   #   (an in-place path, or a flipped managed
                                   #    staging path — both are just local dirs)
  pending   : ManagedRef | None    # a managed (repo, ref) being materialized,
                                   #   not yet serving. Carries its job coordinate.
}
```

- **`committed`** is what routing resolves to. It is always a ready, local
  directory (an in-place checkout, or a managed staging path *after* its venv is
  ready). `None` → the process default (UNBOUND / `serve --workspace`, unchanged).
- **`pending`** is a managed materialization in flight. It does **not** affect
  routing until it flips into `committed` (§4). At most one pending per session;
  a new managed switch replaces it.

This single record subsumes both of today's mechanisms: `session_registry`'s
`source_path` **is** `committed`; `session_env`'s managed state **is** `pending`
(its terminal `ready` is the flip).

## 4. Resolution — promote-on-ready, keep-prior

`workspace_context.resolve(key)` becomes (pull model, evaluated per request):

```
b = binding(key)
if b.pending and job(b.pending.coordinate).status == READY:
    b.committed = b.pending.staged_path     # FLIP (promote)
    b.pending   = None
return b.committed  (or the process default if None)
```

- **Flip-on-ready is derived at resolve time** — no background pusher, no GET
  that mutates on behalf of the client from the outside. The promotion is an
  idempotent state fold: the first request after the job reaches `ready` flips the
  binding; every request after sees `committed`. (The tiny mutation is *internal
  bookkeeping of already-observed job state*, not an externally-visible
  side-effect — a GET remains safe.)
- **Keep-prior falls out**: while `pending` is not ready, `committed` is
  unchanged — the session keeps serving whatever it was on (its prior workspace,
  or the default). The client's status poll shows `materializing`; on `ready` it
  reloads and the next request resolves to the new workspace.
- **Env worker + interpreter follow for free**: once `committed` is the staged
  path, `env_resolver.resolve_interpreter(staged_path)` already returns that
  path's coordinate-keyed managed venv (marker-complete, #518), so the worker pool
  spawns on the right interpreter with no special casing.

## 5. Switch flows

| action | effect on the binding |
|---|---|
| `switch` (in-place catalog path) | `committed = path`; `pending = None`. Immediate — in-place is `ready` at once (§2a). |
| `switch-managed (repo, ref)` | start/attach the materialization job; `pending = (repo, ref, coordinate)`. `committed` **unchanged** (keep-prior). Returns `materializing` (or `ready` immediately on a warm venv → flips at once). |
| managed job reaches `ready` | on the next `resolve`, `pending` promotes to `committed` (the flip). |
| managed job `failed` | `committed` unchanged (stay on prior); `pending` stays `failed` so the poll keeps reporting it, until a retry or another switch replaces it (§4 "not retried in a loop"). |
| a second switch before the first is ready | the newer switch wins: it sets `committed` (in-place) or replaces `pending` (managed). The superseded job runs to completion, caches its venv, and is otherwise ignored. |

**Endpoint shape.** `/api/source/materialize-repo` becomes the managed switch —
rename to **`/api/source/switch-managed`** for symmetry with `/api/source/switch`
(both "switch this session's source"; one in-place, one managed). It sets
`pending` and returns the `materialization` envelope. `GET
/api/source/materialization` is unchanged (it already reports the job state; it
now also reflects the flip once `committed` is the staged path → `ready`).

## 6. What requests see during MATERIALIZING (and the deferred refinement)

**v1 — keep-prior (this proposal).** During `cloning`/`syncing`, routing resolves
to `committed` (the prior workspace / default). Nothing serves the half-ready
source. Simple, safe, and matches "a user keeps their workspace selection while
the heavier work happens" (session-registry §5).

**Deferred refinement — science-reads-after-clone (materialization §4).** Once
phase 1 (clone) is done, science-only reads (listing studies, reading YAML) *could*
resolve to the staged tree while env queries still report `materializing`. This
needs a request-level **env-vs-science classification** (which routes touch the
compute env vs. only the science record) that does not exist yet. Deferred — v1
keep-prior is correct without it.

## 7. Concurrency & safety

- The binding store is read on **every** request → an in-memory dict behind a
  short lock (as `session_registry` is today). The per-request `resolve` does one
  dict get + (only when `pending`) one job-status read — negligible.
- The flip is a compare-and-set under the lock (promote only if `pending` is still
  the one that went ready), so two concurrent requests racing the flip converge on
  the same `committed`.
- Durability is unchanged from session-registry §6: `committed`'s *source
  selection* is the only thing worth persisting (a managed `committed` persists as
  its `(repo, ref)`, re-materialized lazily on restart — the venv is cached on
  disk by coordinate, so re-warm is usually instant). `pending` and job state are
  ephemeral (a restart mid-materialize re-starts it on next touch).

## 8. Migration — what the implementation changes

1. **New `lib/session_binding.py`** (or evolve `session_registry`): the
   `committed`/`pending` record + `set_committed(path)`, `set_pending(repo, ref)`,
   `resolve(key) -> path` (with the promote-on-ready fold), `status(key)`.
2. **`workspace_context.resolve`** reads `session_binding.resolve` instead of
   `session_registry.get`.
3. **`/api/source/switch[-build]`** → `set_committed` (replacing `rebind`).
4. **`/api/source/switch-managed`** (renamed from `materialize-repo`) →
   `set_pending` + start the job.
5. **`session_env`** folds into `session_binding` (its managed state *is*
   `pending`; its in-place `ready` *is* `set_committed`). The status endpoint
   reads the unified record.
6. `session_registry` retires (or becomes a thin shim during migration).

Each numbered item is a small, gated slice; 1–2 are behavior-preserving (the fold
of today's `session_registry` into `committed` with no `pending` in play).

## 9. Open questions (for review)

- **Keep-prior vs. drop-to-default** on a managed switch when the session has a
  prior in-place binding: keep-prior (proposed) is nicer but means a managed
  switch that later *fails* leaves the user on the old workspace (good) — confirm
  that is the desired UX vs. an explicit "you were switching to X, it failed"
  interstitial.
- **Rename `materialize-repo` → `switch-managed`?** (proposed) or keep them
  distinct (a "prepare without switching" *and* a "switch")? The two-verb split
  might be worth keeping for pre-warming.
- **Persisting a managed `committed`** across restart as `(repo, ref)` vs. the
  resolved commit — the commit is reproducible, the ref may have moved. Likely
  persist the ref *and* the last resolved commit; re-materialize the commit.
- **Per-session worktree isolation** (materialization §5) — if managed sources
  become editable in place, staging must be per-session, not per-`(repo, commit)`.
  Out of scope until managed sources are writable.
