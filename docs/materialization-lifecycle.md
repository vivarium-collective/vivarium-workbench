# Materialization lifecycle — preparing a workspace before it can serve

How a session's workspace gets **ready to use**: cloning the source, `uv sync`-ing
the per-workspace environment, and only then spawning the env worker. This is the
concern that ties together `WorkspaceStore` (the staging area), `EnvironmentResolver`
(the venv), the `SessionRegistry` (session state), and the env worker (spec §7).

Context: `docs/workspace-store.md`, `docs/env-worker-protocol.md`,
`docs/session-registry.md`, `docs/REFACTOR-PLAN.md` §2A.6/§2A.7.

Status: **proposed** (spike). Not yet implemented — and it is the piece that must
land *before* the venv/`uv sync` step is built, because getting it wrong makes the
first real workspace hang.

---

## 1. The gap this closes

The env-worker query path has a **60 s** socket timeout (`env_worker_client.py`) —
right for a `build_core`-scale query (~1–3 s). But **`uv sync` on a v2ecoli-scale
repo is minutes, not seconds.** If materialization were a synchronous worker query
under that 60 s timeout it would simply **time out and fail** on the first real
workspace. Materialization is a different cost class from a query and needs its
own lifecycle: **asynchronous, out-of-band, with a long timeout, progress, caching,
and honest failure surfacing** — this doc.

Placing it among the system's timeouts:

| operation | scale | timeout | owner |
|---|---|---|---|
| env-worker query (`build_core`, resolve, list) | ~1–3 s | 60 s (socket) | env-worker §10 |
| a run (simulate + analyze) | seconds–minutes | 1800 s (`MAX_RUNTIME_SEC`) | RunBackend |
| **materialization (clone + `uv sync`)** | **minutes** | **long, separate (e.g. 15 min, config)** | **this doc** |

## 2. Two phases

Materializing a `(repo, ref)` source into a usable workspace is two steps, both
**before** the env worker spawns:

1. **Staging area** (`WorkspaceStore`, workspace-store §6) — resolve `ref` →
   `source_version`, `git worktree add` off the per-repo bare mirror →
   `staging_path`. Usually seconds; the **first** bare-mirror clone of a large repo
   can itself be minutes. **Source of the clone:** the origin (GitHub) today. The
   per-repo bare mirror is a **per-pod** cache — repeated materializes of the same
   repo within a pod only `fetch`, never re-clone; but a *fresh* pod, and every
   *other* consumer of the repo (notably sms-api, §5a), pulls from GitHub
   independently.
2. **Environment** (`EnvironmentResolver`, workspace-store §8) — `uv sync` a venv
   from the staging area's lockfile. **The minutes-scale step.** Cached by
   `source_version` (§5): built once, shared read-only across every session/handle
   on that version.

The env worker is spawned on the venv's interpreter only after **both** complete.

## 2a. The local / in-place path — find, don't clone (READY immediately)

Not every workspace is materialized from GitHub. **Running locally, the workspaces
already exist on disk** — a developer keeps several local git checkouts and
*switches among them* (Eran's flow today); re-cloning them from GitHub would be pure
friction. This is the **in-place-local** adapter (workspace-store §7) and it is the
common local-dev path.

**Decision:** local dev **uses existing local checkouts in place; it does not
re-clone from GitHub.** Only the **managed** path — a session picking a `(repo, ref)`
it hasn't checked out (the multi-session / cloud case) — clones + materializes (§2).

For an in-place local workspace the two phases degenerate:

- **Discovery, not clone (phase 1).** The workspace is *found*, not fetched — from
  the **local workspace catalog** (`pbg_superpowers.workspace_catalog` — a
  `workspaces.json` registry of registered local checkout paths, already what
  `/api/source/switch` validates against). `serve --workspace <path>` is the
  degenerate one-entry case (session-registry §9, the local default-bind). The
  checkout **is** the staging area, edited in place.
- **Use the existing environment (phase 2).** The checkout already has its own
  environment — its `.venv` from the developer's own `uv sync`, or the active
  interpreter. `EnvironmentResolver`'s in-place adapter **uses it** (the checkout's
  `.venv/bin/python`, else the running interpreter — today's `sys.executable`
  behavior); it does **not** force a `uv sync` on a dev checkout the user maintains.
- **Materialization is a no-op → READY immediately.** Nothing to fetch or build, so
  an in-place `bind`/`switch` **skips `MATERIALIZING`** (§4) and goes straight to
  READY. The whole clone + `uv sync` + progress apparatus (§§2–7) is the **managed**
  path only.

So the **lifecycle model is one and the same** — the session states, the
env-worker-spawns-on-READY precondition (env-worker §7), the per-session routing —
and the *in-place adapter simply short-circuits the expensive parts*. Local dev
stays instant; the cloud/managed path pays the materialize cost. Because both go
through the same `RepoSource`/venv seams (§5a), a future "pull a not-checked-out
repo on demand *locally* too" is an adapter choice, not a redesign — but the
**default local experience is find + use, never re-clone**.

## 3. Asynchronous and out-of-band — the HTTP worker never blocks

Materialization runs as a **detached job**, not inside the HTTP request — the same
discipline as `RunBackend` (submit + poll durable state, run-backend.md §6/§8):

- A `bind`/`switch` to a not-yet-materialized `source_version` **kicks off**
  materialization and **returns immediately** with a `materializing` status +
  a materialization id.
- The client **polls** a status endpoint (like run polling) for progress until
  `ready` or `failed`.
- The detached materializer writes progress/terminal state to a **durable** record
  (so it survives a restart and any session can observe it), exactly as runs do.

The HTTP process is pure orchestration here too: it starts the job and reads state;
it never runs `uv sync` itself.

## 4. Session states + progress

The `SessionRegistry` lifecycle (session-registry §5) gains a `MATERIALIZING`
state and a terminal `FAILED`:

```
 UNBOUND ──bind/switch(source)──▶ (in-place local, or env already cached?)
                                      │ yes → READY (bound; worker spawns lazily)
                                      │ no  → MATERIALIZING ──▶ READY
                                                     │
                                                     └─ (clone/sync error | timeout) ──▶ FAILED
```

An **in-place local** workspace (§2a) is always the top branch — it is already on
disk, so it binds straight to READY, never entering MATERIALIZING.

- **MATERIALIZING** — the workspace is being prepared. Requests that need the
  environment return a structured `{status: "materializing", phase, progress}` the
  UI renders as "preparing environment…"; science-only reads that don't need the
  env can still proceed against the staging area once phase 1 is done.
- **Progress** is coarse and phase-level: `queued → cloning → syncing → ready`
  (plus, for `syncing`, a tail of `uv` output for a broken-lock diagnosis).
- **FAILED** — carries the cause (§6); the session stays FAILED (not retried in a
  loop) until the user retries or picks another source.

## 5. Caching & dedup — build once per environment, not per session

- **The venv is keyed by the environment coordinate** — `source_version`, or more
  precisely a hash of the resolved **lockfile** (`uv.lock`), since the venv is a
  pure function of the lock. If a venv for that key already exists, `bind` is the
  **fast path**: no `uv sync`, straight to READY (workspace-store §8: the venv is
  shared read-only across handles on that version).
- **Concurrent materialize of the same coordinate is deduplicated** — a
  materialization registry keyed by the coordinate, with a lock: the first request
  materializes; others **attach to the same in-flight job** and poll it, rather
  than launching N parallel `uv sync`s of the same env.
- **The staging worktree is per-session** (isolated, cheap — off the shared mirror);
  **the venv is per-coordinate** (shared). So five sessions on the same
  `source_version` = five cheap worktrees + **one** `uv sync` + five worker
  processes.

## 5a. Repo source & the double-download — a future S3 optimization (deferred)

The clone in phase 1 pulls from **GitHub**, and so does sms-api — **independently**.
Confirmed in the sms-api tree: the Ray/Batch build does
`git clone --branch <ref> --single-branch <CLONE_URL> /build/v2ecoli`
(`simulation_service_ray.py`), and the compose path pip-installs
`git+https://github.com/vivarium-collective/v2ecoli.git`. So for the **same
`(repo, commit)`**, a workbench materialize (its venv/staging) **and** an sms-api
run each fetch the repo from GitHub separately — **downloaded twice** (sms-api
reportedly discards its copy per run, so it re-pulls every time). At v2ecoli scale
that is real egress, latency, and GitHub rate-limit exposure — on the same commit
sms-api has *already* built and keyed its ParCa cache by (the F′ north-star in
Alex's #486 review: the runner-image commit and the resolved pip commit should be
one source of truth, not two).

**Future optimization (deferred — needs sms-api coordination):** a **shared repo
cache in S3** — the `(repo, commit)` tree (or bare mirror) synced to an S3 bucket
once, and **both** the workbench materialize and sms-api pull from S3 instead of
GitHub. Benefits: one fetch per commit instead of N; no GitHub rate-limit/egress on
the hot path; byte-identical source across both sides. It is **not** in scope now —
it is a cross-service change (S3 layout + who writes the cache + auth) that couples
the workbench and sms-api materialization paths, and the local `uv sync`/venv work
must land first. Captured here so the phase-1 clone is written against a `RepoSource`
seam (GitHub now, S3 later) rather than a hardcoded `git clone <github-url>`.

## 6. Failure surfacing — expected, not a crash

Materialization failures are **normal** (a workspace can have an unreachable repo,
a bad ref, or an unresolvable lockfile) and must surface as a handled session state,
never a hang or a 500:

| failure | phase | surfaced as |
|---|---|---|
| repo unreachable / auth | cloning | `FAILED` — "could not reach `<repo>`" |
| ref not found | cloning | `FAILED` — "ref `<ref>` not found" |
| `uv sync` resolution/build error | syncing | `FAILED` — "environment build failed" + the `uv` error tail |
| exceeded the materialize timeout | any | `FAILED` — "environment build timed out after `<N>` min" |

The failing `uv` output tail is the actionable part — it's what a user needs to fix
their lockfile — so it is captured and returned, not swallowed.

## 7. Where it runs, and restart reconciliation

- A detached materializer process (or a bounded worker pool) does the clone +
  `uv sync`, writing phase/progress/terminal state to a durable record keyed by the
  environment coordinate (alongside the `WorkspaceStore` manifest, workspace-store §9).
- **On restart:** in-flight materializations whose process is gone reconcile to
  `FAILED` (like `RunBackend`'s dead-pid reconcile) so a session never observes a
  permanent `MATERIALIZING`; already-completed venvs are re-found from the manifest
  and are immediately `READY`.
- **GC:** an abandoned venv (no live session on its coordinate) is reclaimed by the
  `WorkspaceStore` disk-budget/TTL sweep — the longest of the eviction horizons
  (env worker ≪ session ≪ staging/venv, workspace-store §9). Never GC a venv with a
  materialization in flight.

## 8. Relationship to the ports (what each must add)

- **`SessionRegistry`** — the `MATERIALIZING`/`FAILED` states + the progress
  payload (§4); a `bind` becomes "resolve coordinate → cached? READY : start
  materialize → MATERIALIZING".
- **`WorkspaceStore.materialize`** — becomes **async** and two-phase; returns a
  materialization handle, not a ready path. Phase 1 (worktree) is its part.
- **`EnvironmentResolver`** — owns phase 2 (the venv / `uv sync`), the
  coordinate-keyed cache, and the dedup lock. Its `resolve(handle)` returns a venv
  interpreter **only when READY**; otherwise it reports the materialization state.
- **env worker (spec §7)** — gains an explicit **precondition**: it is spawned only
  after materialization is READY (on the resolved venv interpreter). Its 60 s query
  timeout is unchanged and unrelated to the materialize timeout.

## 9. Sequencing

This design lands **before** the venv/`uv sync` implementation slice. The current
env worker runs on `sys.executable` (no venv, no `uv sync`) precisely because this
lifecycle isn't built — so today there is no minutes-scale materialize to mishandle.
The build order once this is agreed: (a) `EnvironmentResolver.resolve` returns
`sys.executable` unchanged behind the new interface (behavior-preserving); (b) add
the coordinate-keyed venv cache + a synchronous `uv sync` *with the long timeout*
for a single local workspace; (c) make it async + the `MATERIALIZING` session state
+ progress polling; (d) dedup + restart reconcile + GC. Each is a slice.

## 10. Open questions (deferred to implementation)

- **The materialize timeout value** — one number, or per-phase (clone vs sync)? and
  config vs fixed (plan §G).
- **Lockfile-hash vs `source_version` as the venv cache key** — the lock is the
  truer key (two commits with an identical lock share a venv), but `source_version`
  is simpler; pick per the cost of a redundant `uv sync`.
- **Eager (on `bind`) vs lazy (on first env query) materialize** — eager prepares
  while the user navigates; lazy avoids preparing an env a science-only session
  never touches. Likely: eager on an explicit `switch`, lazy on default-bind.
- **`uv sync` concurrency cap** on a shared pod (N parallel syncs are heavy) — a
  materializer pool size, paired with the dedup so same-coordinate requests don't
  count against it.
- **Cloud parity** — in the cloud adapter the "venv" is the `(repo, commit)` image
  built by sms-api; the same lifecycle states apply, but phase 2 is "image ready"
  (poll sms-api) rather than a local `uv sync`.
- **Shared S3 repo cache (§5a)** — pull the repo from S3, not GitHub, so the
  workbench and sms-api don't each clone the same commit. Deferred (cross-service,
  needs sms-api coordination); phase 1 should still expose a `RepoSource` seam now
  so GitHub→S3 is later a swap, not a rewrite.
