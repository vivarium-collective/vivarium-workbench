# WorkspaceStore — working-area lifecycle

Design spec for the **`WorkspaceStore`** port: how an immutable source
coordinate `(repo, ref)` becomes a mutable **staging area** a session edits, how
those areas are tracked and reclaimed, and (later) how edits are **persisted**
back as a durable artifact.

Context and the decisions this realizes: `docs/REFACTOR-PLAN.md` **§2A.6**
(session-multiplexed workspaces). Sits between two neighbours it must divide
labour with cleanly:
- [`session-registry.md`](session-registry.md) — a session binds to a
  `WorkspaceHandle` this store produces.
- [`env-worker-protocol.md`](env-worker-protocol.md) / §2A.7
  (`EnvironmentResolver`) — the **venv** (environment) is *its* concern; this
  store owns the **working tree** (science).

Status: **proposed** (spike). Not yet implemented.

---

## 1. The model

A workspace decomposes into an immutable **source** and a mutable **staging
area** — the split today's "point `--workspace` at a hand-checked-out
`../other-repo`" fuses into a single path.

- **source** — an immutable coordinate `(repo, ref)`. At materialize time the
  `ref` is resolved to a concrete commit → **`source_version`** (the durable
  identity, pinned even if the branch later moves; this is the reproducibility
  anchor of §2A.2).
- **staging area** — a mutable working tree *derived from* `source_version`,
  where a session's uncommitted science edits accumulate (commit-model **(a)**,
  §5A / §13 of the env-worker spec). Each session gets **its own** staging area,
  even for the same source (§2A.6 non-goal: no concurrent access to *one* area).

## 2. Goals / non-goals

**Goals**
- `(repo, ref)` → an isolated, editable staging area, cheaply, at v2ecoli scale.
- Every staging area carries its **provenance** (`derived-from source_version`),
  recorded by the store at materialize time — not reconstructable after the fact.
- **Restart-durable:** a manifest lets the backend re-find existing staging areas
  after a restart (no re-clone), reconciling with the session's binding.
- **Local dev unchanged:** `serve --workspace /path` edits that repo **in place**,
  no clone into a managed dir (§7).

**Non-goals (v1)**
- Concurrent writers on one staging area (§2A.6) — each session has its own.
- The **venv / environment** — `EnvironmentResolver`'s concern (§8).
- `persist` beyond a sketch — it's the Phase-3 durable-artifact story (§10).

## 3. `WorkspaceHandle`

```
WorkspaceHandle {
  id             : str          # per-materialization (a session's own area)
  source         : (repo, ref)
  source_version : str          # resolved commit — opaque durable id
  staging_path   : Path         # the working tree; ScientificContent binds here
}
```

`for_workspace(handle)` binds `ScientificContent` (and the rest) to
`handle.staging_path` — nearly today's `for_workspace(ws_root)`, just fed by the
store rather than a raw global path.

## 4. Interface

```
materialize(source) -> WorkspaceHandle     # (repo, ref) -> an isolated staging area  [ASYNC — see materialization-lifecycle.md]
list()              -> [WorkspaceHandle]   # from the manifest
discard(handle)     -> None                # remove the staging area; GC (§9)
persist(handle)     -> artifact_version    # staging -> durable artifact  (Phase 3, §10)
```

`materialize` is **per session bind** (§ session-registry): two sessions picking
the same source get two handles / two working trees, sharing only the immutable
git objects (§6) and — read-only — the venv (§8).

## 5. Adapters

| adapter | working tree | phase |
|---|---|---|
| **managed-local** | git worktree off a per-repo **bare mirror**, under `~/.vivarium-workbench/workspaces/<id>` | Phase 1 |
| **in-place-local** | the `--workspace` path itself; no clone, edit in place (§7) | Phase 1 |
| **cloud** | a working tree on the **sms-api PVC**; `persist` via its artifact store | Phase 3 (§11) |

## 6. Managed-local mechanics — one bare mirror, cheap worktrees

The disk-efficiency crux at v2ecoli scale:

- **One bare mirror per `repo`** (`git clone --bare` / `fetch`), shared by every
  handle of every ref of that repo — a single object store, fetched/updated when
  a new ref is first materialized.
- **`materialize`** = resolve `ref` → `source_version` (SHA), `git worktree add`
  a checkout of that SHA at `<staging_root>/<id>` → `staging_path`. A worktree
  shares the mirror's objects, so N materializations cost N cheap checkouts, not
  N clones.
- **Edits stay in the worktree** (uncommitted), isolated per handle. Worktrees
  are always off a *committed* `source_version`, so the immutable-source contract
  holds while the working copy is freely mutable.
- **`discard`** = `git worktree remove` (+ prune). The mirror persists (other
  handles may share it); the mirror itself is pruned on a longer horizon (§9).

## 7. In-place vs managed — keeping local dev one-command

Two local modes, chosen by how the workspace was named:

- **In-place** (`serve --workspace /path`): the given path **is** the staging
  area. No mirror, no worktree — you edit that repo directly, exactly as today.
  `source_version` = its current `HEAD` (or a sentinel "working"). This is the
  local dev/demo default and preserves the macOS `serve → browser → editing` flow
  with zero change.
- **Managed** (a session picks a `(repo, ref)` it hasn't checked out — the
  multi-session / cloud path): materialize a worktree off the bare mirror (§6).

The `SessionRegistry`'s local default-auto-bind (§ session-registry §9) binds a
fresh local session to the in-place handle; a shared pod uses managed
materialization.

## 8. Division with `EnvironmentResolver` — working tree vs venv

Cleanly split, because they have different sharing rules:

- **This store owns the working tree** (the science staging area) — **per
  handle**, isolated, mutable.
- **`EnvironmentResolver` owns the venv** (the environment) — keyed by
  `source_version`, **shared read-only across all handles on that version**, and
  built once (`uv sync`) per version rather than per session. Process isolation
  (the env worker) is still per session (env-worker spec §1), but the **on-disk
  venv it runs from is shared**.

This is consistent under the science-only-write rule (§13 of the env-worker
spec): a handle's `pyproject`/lockfile equal `source_version`'s (the workbench
never edits environment code), so one venv per `source_version` is valid for
every handle derived from it. An environment change is a *new* `source_version` →
a new venv, never an in-place mutation.

## 9. The manifest, restart recovery, and GC

**Manifest** — the store's durable state, on the volume (distinct from the
session store):

```
handle_id -> { source, source_version, staging_path, owner_session, last_used }
```

- **Restart recovery.** The `SessionRegistry` persists `session_key → source`
  (§ session-registry §6); on restart, for each session the store finds the
  manifest entry with that `owner_session` + source and **reuses the on-disk
  worktree** (no re-materialize). If the worktree is gone (GC'd since), it
  re-materializes. So a restart reuses working trees, not just re-clones.
- **GC.** Staging areas outlive their session's env worker (which idle-evicts in
  minutes) and even the session binding (idle-expires in hours). The staging
  area is the **longest** of the three horizons (§ session-registry §6 table) —
  discarded when its owning session expires, or by an LRU/disk-budget sweep of
  abandoned handles. `discard` removes the worktree; a separate, rarer sweep
  prunes bare mirrors with no remaining worktrees. Uncommitted edits in a
  discarded area are lost by design (they were never `persist`ed) — the GC policy
  must therefore be conservative (long TTL) and, ideally, warn/persist-prompt
  before reclaiming an area with uncommitted changes.

**Three-layer eviction, restated** (the through-line across the three specs):

| layer | owner | horizon |
|---|---|---|
| env worker (process) | `EnvironmentResolver` + protocol §17 | minutes idle |
| session binding | `SessionRegistry` | hours idle |
| **staging area (disk)** | **this store** | **longest; disk-budget / session-expiry** |

## 10. `persist` — sketch (Phase 3)

`persist(handle)` turns a staging area into a durable artifact. It is the natural
home for the §2A.4 science/environment **boundary**: it snapshots only the
**science paths** into a new artifact whose parent is `source_version`, so a
commit-all (model (a)) can't sweep in `pyproject.toml`. It **delegates the science
snapshot to `ScientificContent.snapshot()`** (the write core, decided (a)-until-
Phase-3, §5A) and adds the provenance/artifact framing (parent = `source_version`,
opaque `artifact_version` out). Local: a commit on the bare mirror → a new
`version_id`. Cloud: a push to the sms-api artifact store (§11). Full design lands
with the ScientificContent write core + Phase 3.

## 11. Cloud adapter — sketch (Phase 3)

The working tree lives on the **sms-api persistent volume**; `materialize`
checks out into a pod-mounted folder; `persist` writes to sms-api's artifact
store. `EnvironmentResolver`'s cloud adapter supplies the environment as the
`(repo, commit)` **image** instead of a venv (env-worker spec §3). Same
`WorkspaceStore` interface; only the working-tree backing and `persist` target
differ.

## 12. Open questions (deferred to implementation)

- **GC policy specifics:** the disk budget, the TTL, and the
  uncommitted-changes-before-discard guard (warn? auto-persist to a scratch
  ref?).
- **Bare-mirror freshness:** when to `fetch` a repo's mirror (on every
  materialize of a new ref? a TTL?) vs. serving a stale ref list.
- **Worktree portability** on the deploy FS (a shared PVC): `git worktree` across
  a network/overlay filesystem — verify, or fall back to per-handle shallow
  clones if worktrees misbehave there.
- **`source` naming for private repos / auth** to reach the mirror (credentials
  are the `Principal`/deploy-config layer, not this store).
