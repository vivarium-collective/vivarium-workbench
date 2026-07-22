# Session-per-tab — one workspace per browser tab

Design proposal for the session/workspace binding model: **each browser tab is
its own session on its own workspace.** A new workspace opens a new tab, born
"preparing" (an hourglass), that you switch to once it is ready. This is the
deferred materialization-binding piece (materialization-lifecycle.md §11), now
shaped by Eran's request for one-tab-per-session.

Context: `docs/session-registry.md` (§3 defines today's *per-browser* model — this
proposal **reverses** it), `docs/materialization-lifecycle.md` (§3 async, §4
MATERIALIZING, §9c jobs). Ties them together.

Status: **proposed.** Not implemented. **Supersedes** this doc's earlier
`committed`/`pending`/keep-prior draft — per-tab makes that machinery unnecessary
(§4).

---

## 1. The shift

Two things move together here:

1. **Bind materialization to a session.** Routing reads `session_registry`
   (`session_key → source_path`); the materialization state (`session_env`) is
   read only by the status poll. So materializing a `(repo, ref)` builds the env
   but never binds a session to it. This proposal closes that.
2. **Session = tab, not session = browser.** Today (session-registry §3) a session
   is a *browser* (the `vw_session` cookie, shared across tabs), deliberately so
   "all tabs move together — consistent by construction." Eran wants the opposite:
   **each tab is an independent session on its own workspace**, so you can have
   several workspaces open side by side. This is a deliberate reversal (§9).

The happy consequence: per-tab **removes** the need for the earlier draft's
`committed`/`pending`/keep-prior/flip machinery. That complexity existed only
because a switch *reused one session* and had to avoid disrupting its current
workspace while the new one materialized. If a new workspace is a **new tab**, you
never re-point a live workspace — each session is born on one workspace and keeps
it. The lifecycle collapses (§4).

## 2. Goals / non-goals

**Goals**
- Each browser **tab** is its own session on its own workspace — independent,
  concurrent, different workspaces side by side.
- Opening a workspace opens a **new tab**, born `preparing` (hourglass favicon);
  you switch to it when it is `ready`. No HTTP request blocks on the minutes-scale
  prepare (materialization §3).
- The session lifecycle is **one workspace, simple states** (§4) — no cross-session
  flip.
- **Back-compatible** for non-browser clients (curl, CLI, tests): they keep
  resolving to the process default workspace exactly as today (§3).

**Non-goals (v1)**
- Serving a half-materialized workspace (science-only reads off the staged tree
  after clone but before sync, materialization §4) — the frontend just shows
  "preparing" until `ready`.
- Cross-tab synchronization — the whole point is that tabs are independent.
- Auth — the per-tab id is a routing token, not identity (§3), same as the cookie
  it replaces.

## 3. Per-tab identity — `sessionStorage` + an `X-VW-Session` header

The blocker: a **cookie is per-browser** (shared across tabs), so it cannot
distinguish tabs. **`sessionStorage` is per-tab** — unique per tab, survives a
reload, dies when the tab closes. So the session id moves from a cookie to
sessionStorage, carried as a request header:

- **Server-minted, header-carried.** On a request with **no** `X-VW-Session`
  header, the middleware mints a CSPRNG key (as it does for the cookie today) and
  returns it in an **`X-VW-Session` response header**. The client stores it in
  `sessionStorage` and sends it as the `X-VW-Session` **request** header on every
  subsequent call. Server-minted keeps the property that a client can't assert
  another session's key by choosing it (session-registry §4).
- **Client wiring is tiny.** There is no central fetch wrapper in `static/`, so a
  one-time `window.fetch` override (~10 lines) reads the stored id (attaching it as
  a request header) and captures a minted id from the response header into
  `sessionStorage`. No call site changes.
- **Back-compat / fallback order** in the middleware: `X-VW-Session` header →
  (legacy) `vw_session` cookie → mint. A header-less client (curl, CLI, the test
  harness) has neither → unbound → process default workspace, **unchanged**.
- **Security.** Still a CSPRNG *routing* token, not auth; the CSRF/origin guard is
  untouched. A header (unlike a cookie) is not auto-sent cross-site, a mild CSRF
  improvement, but we keep the origin check regardless. Same-origin fetch can read
  the response header freely (no CORS exposure needed).

Everything **downstream is unchanged** — `session_registry`, `workspace_context.resolve`,
the env-worker pool (keyed by `(workspace, interpreter)`, already deduping N tabs
on one workspace) all key off `session_key`; only its *source* changes from cookie
to header.

**Edge — tab duplication.** "Duplicate tab" copies `sessionStorage`, so the copy
shares the original's session (same workspace). Opening a new tab via link/`⌘-click`
does not. Acceptable; a collision could be re-minted if it ever matters.

## 4. The lifecycle — one workspace per session

Because a new workspace is a new tab/session, a session is **born on one
workspace and keeps it** for its life. The states (session-registry §5, simplified):

```
 (fresh tab) ── no session id ──▶ UNBOUND ──pick a workspace──▶
                                    │
             in-place (§2a): READY at once ─────────────────────▶ READY
             managed (repo,ref): PREPARING (cloning→syncing) ────▶ READY
                                    │  └─ (clone/sync error) ─────▶ FAILED
                                    ▼
                              (tab closes / idle) ─▶ session ends
```

- **UNBOUND** — a fresh tab with no workspace; the UI shows the workspace picker.
  (Local `serve --workspace` auto-binds the default, unchanged — session-registry §9.)
- **PREPARING** — this tab's managed workspace is materializing (the async job of
  §9c). The tab shows an hourglass; it does not load workspace content yet.
- **READY** — the workspace (in-place path, or the managed staged checkout with its
  built venv) is serving. Requests resolve to it normally; the env worker runs on
  its interpreter (`env_resolver` picks the staged venv, #518).
- **FAILED** — clone/sync failed (materialization §6); the tab shows the error +
  the `uv`/git tail, with a retry.

**No `committed`/`pending`, no keep-prior, no flip.** "Your current work" is never
disrupted by preparing a new workspace, because that work is in *another tab*. The
hourglass **is** the `MATERIALIZING` state of *this* tab's one session. Whether
`resolve` returns the workspace during PREPARING doesn't matter for correctness —
the frontend gates on the status and simply doesn't issue workspace/env requests
until `READY`.

## 5. Opening a workspace = opening a tab

- From a fresh (UNBOUND) tab, picking a source binds **this** tab's session:
  - an **in-place** catalog entry (§2a) → `READY` at once;
  - a **managed** `(repo, ref)` → start the materialization job → `PREPARING`.
- **"Open in a new tab"** = `window.open` a new tab → fresh `sessionStorage` → new
  UNBOUND session → pick → prepare → ready. This is the primary way to get a second
  workspace.
- **Favicon = status** (Eran's hourglass): PREPARING → hourglass, READY → the
  normal icon, FAILED → an error badge. Driven by polling `GET /api/source/materialization`.
  The tab **title** can carry the workspace name + state.
- **Re-pointing a live tab** (optional): a tab *may* switch its own source; since a
  session has only one workspace, this is a plain rebind — the tab enters
  `PREPARING` for the new source (no keep-prior; the user's other work is in other
  tabs). The expected flow is new-tab-per-workspace, so re-point is secondary
  (open question, §10).

## 6. Managed workspace storage — worktree-per-session, on a branch

Today the clone seam (`repo_source.py`) checks a managed source out with
`git worktree add --detach` — a **detached HEAD, no branch**, shared per
`(repo, commit)`. That is right for *read-only* env-building, but the dashboard's
audit model commits **every action to a branch** (CLAUDE.md), which a detached HEAD
has nowhere to record.

So once a per-tab managed workspace is **editable**, the clone seam should use:

```
git worktree add -b <session-branch> <staging> <commit>
```

— a **per-session worktree on its own branch**. One move buys three things:
- **per-session isolation** (materialization §5, otherwise deferred),
- the **audit-trail branch** the write model needs, and
- it **resolves the same-workspace-in-two-tabs race** — two tabs on the same
  `(repo, ref)` each get their own worktree + branch, so they cannot clobber each
  other.

Cost: a worktree per tab (disk), so pair it with the store GC (§9d). A purely
**read-only** managed view could keep the cheaper shared detached worktree; a tab
that writes gets its own branch. (Decision, §10.)

## 7. What is already built for this

The multi-workspace backend from this session was built for exactly this shape:

- **Env-worker pool** keyed by `(workspace, interpreter)` with dedup — N tabs on
  one workspace share a worker; different workspaces get their own. Concurrent
  per-tab workspaces already work.
- **Per-request workspace routing** (`workspace_context.resolve` + the
  `_root` request ContextVar) — each request already resolves to *its* session's
  workspace.
- **Materialization job** (§9c) — the per-tab "preparing" driver; `GET
  /api/source/materialization` is the poll the favicon reads.

So the backend delta is small (§8, item 1); the work is mostly frontend.

## 8. Migration slices

1. **Backend — header identity.** Middleware prefers `X-VW-Session` (header) over
   the cookie; mints + returns it when absent. Behavior-preserving (no header →
   cookie → default). *Small.*
2. **Frontend — the enabler.** `sessionStorage` id + the `window.fetch` override
   (attach request header, capture minted response header). *Small.*
3. **Frontend — the tab UX.** Favicon-by-status, tab title, the workspace picker,
   "open in new tab". *The bulk of the work.*
4. **Managed bind + preparing gate.** Bind a tab to a managed source on pick; the
   frontend shows "preparing" until `READY` (polling the status). *Moderate.*
5. **Storage — worktree-per-session branch.** `worktree add -b` for editable
   managed workspaces, paired with GC. *Moderate; can follow.*

## 9. What we give up (the reversal)

session-registry §3 chose *per-browser* deliberately: all of a browser's tabs share
one workspace and "move together — consistent by construction." Per-tab **reverses**
that — tabs are independent. We lose cross-tab consistency (two tabs no longer
auto-track one workspace); we gain multiple workspaces open at once, which is what
the multi-workspace refactor was for. This is the one point to confirm with the
team before building. (session-registry §3 should be updated to match once agreed.)

## 10. Open questions (for review)

- **Re-point a live tab, or always-new-tab only?** Proposed: allow re-point as a
  plain rebind (enters PREPARING), but treat new-tab-per-workspace as the primary
  flow. Or forbid re-point (a tab is pinned to its workspace for its life) for
  maximum simplicity.
- **Detached (shared, read-only) vs. branch-per-session (editable) worktrees — or
  both?** Proposed: read-only view keeps the shared detached checkout; a writing
  tab gets its own `-b` branch. Confirm this is the same-workspace-twice resolution.
- **Server-minted-via-response-header vs. client-minted CSPRNG** for the
  sessionStorage id. Proposed: server-minted (keeps §4's property); the response-
  header handshake is the small extra step over the cookie flow.
- **Tab duplication** shares `sessionStorage` (same session) — leave it, or
  re-mint on a detected duplicate?
- **Idle/close lifecycle.** A tab close can't be reliably signaled to the server;
  sessions idle-expire (session-registry §5 `T_session`) and their worker idle-evicts
  (protocol §17) — confirm the horizons feel right for the per-tab cadence (many
  short-lived tabs).
