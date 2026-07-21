# Session registry & per-request workspace routing

Design spec for the **`SessionRegistry`** and the per-request **`WorkspaceContext`**
resolution it feeds — how a request from one of many concurrent frontend clients
is routed to *its* workspace, so the backend can serve many sessions on different
workspaces at once.

Context and the decisions this realizes: `docs/REFACTOR-PLAN.md` **§2A.6**
(session-multiplexed workspaces; `WorkspaceStore` / `WorkspaceContext`) and
**§2A.7** + [`env-worker-protocol.md`](env-worker-protocol.md) (the per-session
env worker this routing owns). This is the connective tissue both assume — they
talk about "the session's workspace / worker," and this spec defines how a
request *resolves* to one.

Status: **proposed** (spike). Not yet implemented.

---

## 1. The gap this closes

Today `lib/_root._WS_ROOT` is a single process-global `Path`; `/api/source/switch`
mutates it for the **whole process** and fires a **global** cache invalidation.
There is no per-request session concept at all. (The GitHub device-flow "session"
in `lib/github_auth` is unrelated — that is auth state, also process-global.)

Under §2A.6 the backend serves many concurrent sessions, each on its own
workspace. That is impossible with one global root: two requests for different
workspaces racing on `_WS_ROOT` corrupt each other **even in a single worker**
(§2A.6). This spec replaces the global with a **per-request** binding resolved
from a **session key**.

## 2. Goals / non-goals

**Goals**
- Every request resolves to exactly one `WorkspaceContext` — the session's
  workspace handle + its ports (`ScientificContent`, `EnvironmentResolver`, …).
- Sessions are **isolated across** workspaces (§2A.6): one session's `switch` or
  activity never disturbs another's.
- **Independent of auth.** The session key is a **routing token, not a
  `Principal`** (§2A.6). This lands *before* any auth front door.
- **Local dev stays one-command.** `serve --workspace /path` → open a browser →
  you are on that workspace, zero session friction (§9).
- A **restart** is transparent: a client's session survives it (re-warm is lazy).

**Non-goals (v1)**
- Authentication / authorization (a session ≠ identity; §8). Auth layers on later
  (§2B.4).
- Concurrent access to the **same** workspace by two sessions — out of scope per
  §2A.6 (isolation is across workspaces, not within one).
- A durable multi-node session store (RDS/Redis) — that arrives with multi-tenant
  (plan Phase 5); v1 is one backend process (or a sticky-routed pod).

## 3. What a session is

**A session = a browser, keyed by a cookie — shared across that browser's tabs,
not per-tab.** One user in one browser is one session on one workspace, which is
exactly "a user sees one workspace at a time" (§2A.6). A `switch` in one tab
rebinds the session, so all that browser's tabs move together — consistent by
construction. Different users / browsers are different sessions on different
workspaces.

A session is **not** a `Principal` and **not** an auth artifact. It answers only
"*which* workspace does this request target," never "*who* is asking." When auth
arrives, a session becomes *associated with* a `Principal` and authorization
layers on top — but the routing this spec defines is unchanged by that.

## 4. The session key

**Server-minted, opaque, carried as a cookie.**

- On the first request with no valid session cookie, the server mints a session
  key — a **CSPRNG token** (≥128 bits; `secrets.token_urlsafe`) — and sets it as
  an **`HttpOnly`, `SameSite=Lax`, `Secure`-in-prod** cookie. The browser returns
  it automatically on every request; no client code attaches a header.
- **Server-minted** (not client-supplied) so the key space is the server's — a
  client cannot assert another session's key by choosing it, and keys are
  unguessable. It is not auth, but a guessed/leaked key = reaching another
  session's *workspace binding*, so it must not be trivially forgeable even behind
  the perimeter (§11).
- **Interaction with the existing CSRF/origin guard.** The guard (every mutating
  route checks `Origin == Host`; no-Origin allowed for curl/CLI) is unchanged and
  still mitigates CSRF: a cross-site forged mutation carries a foreign `Origin`
  and is blocked *even though* the browser auto-sends the session cookie.
  `SameSite=Lax` is additional defense in depth. The session cookie is **not** a
  CSRF token and does not replace that check.

## 5. Session lifecycle

```
 (no cookie) ──mint key + Set-Cookie──▶ UNBOUND ──bind/switch(source)──▶ BOUND
                                          │                               │  ▲
                              (needs ws → "pick a workspace")   activity  │  │ switch(new source)
                                                                          ▼  │
                                                          idle > T_session ──▶ EXPIRED (entry dropped;
                                                          or explicit end        staging area handed
                                                                                 to WorkspaceStore GC)
```

- **UNBOUND** — a fresh session with no workspace yet. Requests that need a
  workspace return a structured "no workspace selected" state; the UI shows a
  workspace picker. (Local dev skips this — §9.)
- **BOUND** — `bind`/`switch(source)` resolves the source through `WorkspaceStore`
  (materialize `(repo, ref)` → a `WorkspaceHandle`, §2A.6) and stores it on the
  registry entry. The session is now routable.
- **EXPIRED** — after `T_session` idle (no request), or an explicit `end`, the
  entry is dropped. This is the **session** idle horizon — deliberately *longer*
  than the env-worker idle horizon (protocol §17), so a user who steps away keeps their
  workspace selection while the heavier worker process is reclaimed sooner. On
  expiry the session's staging area is handed to `WorkspaceStore` GC.

## 6. The `SessionRegistry`

Entry (conceptual):

```
SessionEntry {
  session_key   : str            # the cookie value (CSPRNG)
  source        : (repo, ref) | LOCAL_DEFAULT | None   # None while UNBOUND
  handle        : WorkspaceHandle | None               # from WorkspaceStore
  env_worker    : WorkerHandle | None                  # lazily spawned (protocol §17)
  last_seen     : timestamp                            # for T_session idle expiry
}
```

**Two tiers of state, deliberately split:**

- **Durable (survives restart):** `session_key → source`. A tiny append/replace
  map persisted on the volume (a JSONL or small DB, alongside — but distinct
  from — the `WorkspaceStore` manifest). This is *all* that must persist: which
  session chose which source.
- **Ephemeral (rebuilt lazily):** `handle`, `env_worker`, and the caches. On
  restart the registry reloads `session_key → source`; the `handle` is re-located
  or re-materialized from the `WorkspaceStore` manifest on the session's next
  request, and the env worker is **re-spawned lazily** on its first env query
  (protocol §17 "lazy spawn"). So a restart costs a client only a one-time
  re-warm, never a lost session.

This layering lines up the three lifetimes cleanly:

| state | owner | lifetime | reclaimed by |
|---|---|---|---|
| env worker (process) | this registry + protocol §17 | shortest (~min idle) | worker idle-evict / pool cap |
| session binding | this registry | medium (~hours idle, `T_session`) | session idle-expire |
| staging venv (disk) | `WorkspaceStore` (§2A.6) | longest | venv GC |

## 7. Per-request resolution

A FastAPI dependency (replacing today's `get_workspace` that reads the `_root`
global) does, on every request:

1. Read the session cookie → look up the `SessionRegistry` (mint + `Set-Cookie`
   if absent → UNBOUND).
2. Produce a `WorkspaceContext { handle, science, env, … }` — the session's
   handle plus its ports, bound to `handle.staging_path`
   (`for_workspace(handle)`, not a raw global path).
3. Inject it into the handler. Handlers stop calling `_root.workspace_root()`;
   they read `ctx.handle.staging_path` / `ctx.science` / `ctx.env`.

This is the threading §2A.6 describes: 95 lib modules already take `ws_root`
explicitly and are ready; the ~13 that still read the global `_root` are the
migration surface. `_root._WS_ROOT` / `active_workspace.switch_workspace`'s
global semantics retire once every path resolves through the context.

## 8. `switch` — per session, never global

`POST /api/source/switch { source }` (replacing today's global re-point):

1. Resolve/materialize `source` via `WorkspaceStore`.
2. Tear down **this session's** env worker (protocol §7 shutdown → kill).
3. Rebind **this session's** registry entry `handle`/`source`; persist the new
   `session_key → source`.
4. Invalidate **only this session's workspace-keyed caches** — never the global
   `active_workspace.invalidate()` sweep, which would trample every other live
   session (§2A.6).

Other sessions are untouched. The global `invalidate()` and the process-wide
`switch_workspace` are removed.

## 9. Local dev vs shared pod

One mechanism, two configurations — the code path is always cookie-based:

- **Local dev / demo (`serve --workspace /path`):** the CLI workspace is the
  **default source**. A fresh session **auto-binds** to it (skips UNBOUND / the
  picker), so `serve` → open browser → you're on the workspace, exactly as today.
  A local user can still `switch` to another source; usually there's one and they
  never notice a session exists. Essential for the macOS dev/demo flow.
- **Shared pod (cloud):** **no** default source. A fresh session is UNBOUND and
  the UI presents a workspace picker; `bind` selects the `(repo, ref)`. Sessions
  are isolated per §2A.6.

## 10. Concurrency & registry safety

The registry is read on **every** request → its lookup must be async/thread-safe
and cheap (an in-memory dict guarded for mutation). Mutations
(mint / bind / switch / expire) take a short lock. Within a session, concurrent
requests serialize at the (serial, single-worker) env layer anyway
(protocol §8); non-worker paths (reading science, listing) can proceed
concurrently. The durable `session_key → source` write on bind/switch uses the
same atomic-write discipline as the rest of the workspace (`lib/atomic_io`).

## 11. Security & the not-auth boundary

- The session key is a **routing token**, not identity. In the near-term
  single-tenant deployment behind the VPC/perimeter (§2B.4) the threat is low, but
  the key is still a CSPRNG value (§4) so it is not guessable — a guessed key
  reaches another session's *workspace binding*.
- **When auth arrives** (`Principal`, plan Phase 1 front door / Phase 5
  multi-tenant): a session becomes *scoped to* a `Principal`, and authorization
  (which principals may bind which sources) layers on top. The routing mechanism
  here is unchanged; it gains a `principal` field on the entry and an authz check
  at `bind`.
- The session cookie does not weaken the existing CSRF/origin posture (§4).

## 12. Open questions (deferred to implementation)

- **`T_session` default** and whether it is fixed or per-deployment config
  (pairs with the protocol's `T_idle`/`K`, plan §G).
- **Multi-node / HA:** v1 assumes one backend process (or a sticky-session-routed
  single pod). A horizontally-scaled fleet needs the durable `session_key →
  source` map in a shared store (RDS/Redis) and sticky routing or worker
  affinity — deferred to multi-tenant (Phase 5).
- **UNBOUND UX contract:** the exact "no workspace selected" payload shape the
  frontend renders as a picker (a small API-contract detail).
- **Cookie attributes across the ALB/OIDC front door** (Phase 1): `Secure`,
  domain/path scoping, and how the session cookie coexists with an auth cookie
  once the front door lands.
