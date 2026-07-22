# Env-worker IPC protocol

Design spec for the **environment worker** — the per-session subprocess that
holds a workspace's compute environment (`build_core()`, the generator
`_REGISTRY`, the workspace package, `v2ecoli`) and answers the workbench's
interactive queries out-of-process.

Context and the decisions this realizes: `docs/REFACTOR-PLAN.md` **§2A.7**
(`EnvironmentResolver` as a per-session warm env worker) and **§2A.6**
(`WorkspaceContext` / `WorkspaceStore`, which owns the worker's lifecycle). This
doc specifies the wire contract those sections left open.

Status: **proposed** (spike). Not yet implemented.

---

## 1. Why a protocol at all

A code map (2026-07-21, §2A.7) found ~16 request-path sites where the HTTP
process imports workspace Python. `sys.modules` holds one version of
`v2ecoli` / `process_bigraph` / `pbg_<project>` per interpreter, and the
generator registry is a single process-global — so one HTTP process **cannot**
host two workspace environments. Process isolation is forced. The env worker is
that isolated process; this protocol is how the HTTP process talks to it.

The worker answers **interactive** queries only (list/resolve/render-preview).
Simulations and heavy analyses are **jobs** (`RunBackend`, AWS Batch / detached
local), never worker calls — see §12.

## 2. Goals / non-goals

**Goals**
- The HTTP process imports **no** workspace Python; every environment fact comes
  through this protocol.
- The **message schema is the contract**; the transport is an adapter. The same
  messages ride a local socket (local adapter) or HTTP-to-sms-api (cloud
  adapter) — so `EnvironmentResolver`'s local→cloud swap is a transport change,
  not a re-design.
- A broken workspace (bad `build_core`, a generator that raises or hangs) is a
  **handled, surfaced error**, never a hung or crashed HTTP worker.
- The workspace venv needs **no** `vivarium-workbench` dependency (§4).

**Backend host OS — macOS (dev/demo) and Linux (deploy), day one.** The worker is
always a **local subprocess co-located with the backend** — same host, same OS;
there is no cross-OS client/server split to support. "macOS support" means the
backend *and its workers* run on macOS for dev/demo, and on Linux in deployment.
Both must work from the start, not "Linux now, macOS later." The same-host design
is POSIX-clean on both, and two choices make it so: a `socketpair` (not a named
UDS) sidesteps macOS's shorter `sun_path` limit, and spawning a **fresh
interpreter via `subprocess`** (exec, not `fork`) sidesteps macOS's
fork-after-threads hazards (`OBJC_DISABLE_INITIALIZE_FORK_SAFETY` and friends) —
the worker never inherits a forked, half-initialized runtime.

**Windows — future, not immediate.** Local dev/usage on Windows is a wanted
target eventually, not now. It costs nothing to keep the door open because the
**message layer (§§6–11) is platform-independent** — Windows is a *transport
adapter*, not a redesign. The POSIX-specific pieces of the local transport that a
Windows adapter would swap: `socketpair` + `pass_fds` fd inheritance → a
**named pipe** or a connect-back **localhost TCP** socket (Python's `pass_fds`
is POSIX-only; Windows passes handles differently); and `SIGKILL`-to-restart
(§10) → `TerminateProcess` (`Popen.kill()`). The serial/main-thread model (§8) is
unchanged — Windows has even narrower signal support, but the protocol relies on
the socket + process-kill, not signals. Not designed now; just don't bake a
socketpair/`pass_fds`/`SIGKILL` assumption into the *message* layer (only into the
local-transport adapter), and Windows stays a later adapter. Note this only makes
the *protocol* Windows-ready; whether a given workspace's compute env
(`build_core`, `v2ecoli`, …) installs and runs on Windows is a separate, larger
question owned by those packages.

**Non-goals (v1)**
- Concurrent heavy queries within one worker (the worker is single-threaded on
  its main thread — §8).
- Streaming partial results / progress for queries (heavy, long-running work is a
  job, not a query — §12).
- Cross-host transport for the *local* adapter (same host only; cloud is a
  separate adapter).

## 3. Architecture — a transport-independent message layer

```
 HTTP process (orchestration, no workspace imports)
   └─ EnvironmentResolver (port)
        ├─ LocalWorkerTransport ── socketpair fd ──▶ env worker  (<venv>/bin/python)
        └─ SmsApiTransport ─────── HTTPS ──────────▶ sms-api ▶ container   (Phase 3)
                    │
             same JSON-RPC messages (§6), same method catalog (§11)
```

The **message layer** (§§5–11) is fixed. Two **transports** implement it:

- **Local (Phase 1, this spec's focus):** a `socket.socketpair()` whose child
  end is inherited by the worker as a numbered fd; length-prefixed JSON frames
  (§5). No filesystem socket path, no port, no cleanup — the socket dies with the
  process pair.
- **Cloud (Phase 3):** the same request/response objects tunneled as HTTPS
  request bodies to an sms-api endpoint backed by the `(repo, commit)` image. The
  method catalog is identical; only framing/addressing differ.

Keeping the contract at the message layer is the whole point — it's what makes
"local venv today, sms-api image later" one adapter swap (§2A.7).

## 4. Worker code provenance — shipped by the workbench, run by the venv

The worker **program** (the IPC server + method handlers) is part of
**vivarium-workbench**, not the workspace. It ships as a **single self-contained
file** (a `.py`, or a `.pyz` zipapp if it grows past one module) so the workbench
can run it *on the workspace's interpreter* by **path** — nothing is written into
the workspace, the venv, or the science record:

```
<venv>/bin/python  <workbench>/env_worker.pyz  --socket-fd <n>  --workspace <staging_path>
```

This is the one place the science-only-write rule could have needed an
exception — "the workbench must write the RPC server somewhere" — and it does
**not**: path-injection means the worker is workbench code the venv interpreter
merely *executes*, never a file the workbench persists into the workspace.
(For the **cloud** adapter the worker is baked into the `(repo, commit)` image at
build time — again not written into the science record.)

The worker module imports **only** the standard library plus what is already
present in the workspace venv (`process_bigraph`, `pbg_superpowers`, the
workspace `pbg_<project>` package, `v2ecoli`). It never imports
`vivarium_workbench`. Consequences:

- The workspace venv carries **no** workbench dependency — `uv sync` of the
  workspace repo is untouched.
- Worker code is versioned **with the workbench**, so for the local adapter the
  protocol version is always in lockstep (§14 still matters for the cloud
  adapter, whose worker may be a different build).
- Anything the worker needs from the workbench's own helpers (e.g. JSON
  sanitization, §10) must be **self-contained / stdlib-only** in the worker
  module, since the workbench package isn't importable there.

## 5. Transport & framing (local adapter)

- **Channel:** one `AF_UNIX` `socketpair()`. The parent keeps one end and passes
  the other to the child via `subprocess(..., pass_fds=[fd])` after
  `os.set_inheritable(fd, True)`; the child is told its number with
  `--socket-fd <n>` and reads that fd from argv (not a hardcoded `3` —
  `pass_fds` preserves the fd number, it does not renumber to 3). Full-duplex
  byte stream. Identical on macOS and Linux; `pass_fds` +
  `os.set_inheritable` are the portable POSIX path.
- **stdout/stderr are NOT the protocol.** `build_core()` and workspace imports
  `print()` and log freely; that noise flows to the worker's stdout/stderr,
  which the workbench redirects to a **per-worker log file**. The protocol
  channel (fd 3) stays clean. This is the single most important framing decision
  — reusing stdout for RPC is the classic footgun here.
- **Frame:** `uint32` big-endian length prefix + that many bytes of UTF-8 JSON.
  Length-prefixing (not newline-delimited) because composite-state payloads are
  large and may be pretty-printed elsewhere; read-exactly-N is robust.
- **Max frame size:** a configured cap (e.g. 64 MiB) → over-cap is a protocol
  error, not an OOM.

## 6. Message model — JSON-RPC 2.0 (subset)

Requests, responses, and notifications follow JSON-RPC 2.0.

**Request** (workbench → worker):
```json
{ "jsonrpc": "2.0", "id": 7, "method": "resolve_composite_state",
  "params": { "ref": "v2ecoli.composites.baseline", "overrides": {} } }
```
**Success response** (worker → workbench):
```json
{ "jsonrpc": "2.0", "id": 7, "result": { "state": { … }, "module": "v2ecoli.composites.baseline" } }
```
**Error response** (§9):
```json
{ "jsonrpc": "2.0", "id": 7,
  "error": { "code": 2001, "message": "generator raised during build",
             "data": { "ref": "…", "exc_type": "ValueError", "traceback_tail": "…" } } }
```
**Notification** (worker → workbench, no `id`): `log`, `ready` (§7). No response.

`id` correlates responses to requests (allows outstanding requests even though
the worker services them serially, §8). `id`s are workbench-assigned, monotonic.

## 7. Lifecycle

0. **Precondition — materialization.** The worker is spawned only once the
   session's venv is **ready**. Cloning the source + `uv sync` is a minutes-scale,
   asynchronous, out-of-band step with its own long timeout and a `MATERIALIZING`
   session state — NOT a query under the 60 s socket timeout below. See
   [`materialization-lifecycle.md`](materialization-lifecycle.md).
1. **Spawn.** The `WorkspaceContext` (owning the session) asks
   `EnvironmentResolver` to start a worker for its `WorkspaceHandle`, on the
   already-materialized `<venv>/bin/python`, with the socketpair fd.
2. **Initialize.** Workbench sends `initialize`. The worker runs `build_core()`
   and primes the registry, then replies with the handshake (§11 `initialize`):
   `{ protocol_version, workspace_id, source_version, python, packages,
   capabilities }`. **build_core failure here is an `initialize` error** with a
   structured cause — the session shows "environment failed to build: …" and the
   worker exits; the HTTP process never hangs.
3. **Serve.** Request/response over fd 3 (§6), serially (§8).
4. **Health.** `ping` (§11) for liveness; the workbench also treats socket EOF /
   child exit as death. A missed heartbeat within a timeout → treat as hung →
   kill + restart (§9).
5. **Shutdown.** `shutdown` request → worker flushes logs and exits `0`. On
   session end or a `switch` to a new source, the workbench kills the worker
   (SIGTERM, then SIGKILL after a grace period) and, for `switch`, spawns a new
   one for the new handle.

## 8. Concurrency — one worker, serial requests

Composite building calls `signal.signal()` (the reason these operations are
subprocesses today — signals only install on the main thread). So the worker
processes requests **serially on its main thread**, FIFO. The protocol permits
multiple outstanding `id`s, but the workbench-side `EnvironmentResolver`
**queues per worker** and expects FIFO completion.

This is adequate: a session is one user on one workspace — naturally low
concurrency — and the warm worker amortizes the ~1–3 s `build_core` across all a
session's queries. v1 is **one worker per session**; a per-session worker *pool*
(multiple workers for one session) is a later option only if a single serial
worker becomes a bottleneck. How many workers live **across** sessions, and when
they are evicted, is the pool policy in §17.

## 9. Error model & crash recovery

Three distinct failure classes:

| class | shape | workbench reaction |
|---|---|---|
| **Protocol error** | JSON-RPC std codes (`-32600` invalid request, `-32601` unknown method, `-32700` parse) | bug — log loudly; do not surface as workspace error |
| **Environment error** (expected) | app codes `2xxx` + `data:{exc_type, traceback_tail, ref}` — a generator raised, `build_core` failed, ref not found | surface to the session as "this composite/env failed: …"; worker stays alive |
| **Worker crash / hang** | socket EOF / non-zero exit / heartbeat timeout | fail the in-flight request with a `worker_unavailable` error; **restart** the worker (re-`initialize`), with exponential backoff and a **crash-loop cap** (N crashes in a window → mark the session's env broken, stop restarting) |

An environment error must **not** kill the worker — user workspaces raise
routinely; killing on every bad generator would thrash. Only a genuine crash or
a hang triggers restart.

## 10. Cancellation & timeouts

Because the worker is single-threaded on a main thread that may be blocked inside
workspace code (a generator with an infinite loop), **cooperative cancellation is
best-effort and often impossible**:

- **Soft:** `$/cancel { id }` notification. The worker honors it only if it can
  reach a checkpoint (rare for a blocked call). Cheap to send; don't rely on it.
- **Hard (the reliable path):** per-request timeout on the workbench side →
  **SIGKILL the worker and restart** (§9). A hung heavy query = kill + re-warm;
  the session sees a transient `worker_unavailable`. This is acceptable precisely
  because heavy work isn't supposed to be a query (§12) — a query that hangs is
  itself a signal the operation was mis-placed.

## 11. Method catalog (v1 core)

Derived from the ~16 in-process sites the map found. Small and stable; new
methods are additive under a minor protocol bump (§14). All results are
finite-safe JSON (§below).

**Composite params are `{ ref }` OR `{ document }`.** A composite reaches the
worker two ways, and this is the crux of the freshness model (§13): a
**generator** is named by `ref` (it lives in the worker's environment registry);
a **static spec** is passed **inline as `document`** — the workbench owns the
science record and hands the worker the just-read (or just-saved) YAML doc, so
the worker reads no science files and caches no science. Methods below taking a
composite accept either.

| method | params | result | replaces (in-process site) |
|---|---|---|---|
| `initialize` | — | `{ protocol_version, workspace_id, source_version, python, packages, capabilities[] }` | startup binds |
| `ping` | — | `{ ok: true, uptime_s }` | (new; health) |
| `registry_catalog` | — | `{ processes[], types[] }` (source-tagged) | `registry.py` |
| `list_generators` | — | `[{ id, module, … }]` — **generators only** | `composite_lookup.py` (registry half) |
| `resolve_composite_state` | `{ ref \| document, overrides? }` | `{ state, module }` | `composite_state_views.py`, `study_run_state.py` |
| `composite_document` | `{ ref, overrides? }` | `{ document }` — generators only (static specs the workbench already holds) | `pbg_export.py`, report resolution |
| `observables` | `{ ref \| document }` | `{ paths[] }` | `observables_views.py` |
| `declared_emit_paths` | `{ ref \| document }` | `{ paths[] }` | `composite_resolve.py` |
| `viz_classes` | — | `[{ name, address, … }]` | `visualization_classes.py` |
| `shutdown` | — | `{ ok: true }` | (new; lifecycle) |

`list_generators` returns **generators only** — the static-spec half of the old
`composite_lookup` union stays on the workbench, which lists `.composite.yaml`
files straight from the science record (fresh on every save; §13). The full
"known composites" set the UI shows is the workbench's static-spec listing ∪ this.

`resolve_composite_state` is the ~1–3 s call; the rest are cheap. Heavy viz
*render* is deliberately **absent** — light preview may be added later as a
query, heavy render moves into the job (§12, §2A.7 "viz straddles").

**Serialization contract.** Results are JSON with: non-finite floats
(`NaN`/`±Inf`) → `null` (browser `JSON.parse` rejects them — cf. `publish.py`);
numpy arrays/scalars → lists/plain numbers; dataclasses → dicts; version ids
opaque strings. The worker owns a **stdlib-only** sanitizer (it can't import the
workbench's `_json_default`, §4).

## 12. Relationship to jobs (`RunBackend`) — the worker is not a runner

The env worker answers *interactive authoring/rendering* queries. It does **not**
run simulations or heavy analyses — those are **jobs**: AWS Batch (cloud) or a
detached subprocess (local), producing durable artifacts read back via
`RunStore`. Both the worker and a local job run in the *same* per-workspace venv
(both interpreters resolved by `EnvironmentResolver` from the `(repo, ref)`
coordinate), but they are **different lifecycles**: the worker is warm and
long-lived and serial; a job is one-shot, detached, and may be remote. Keeping
heavy compute out of the worker is what keeps queries bounded (§10) and the pod
free of heavy analysis (§2A.7).

## 13. Freshness — the worker holds no science, so a save never invalidates it

The obvious worry — "an authoring **save** edits a composite; is the worker's
`_REGISTRY` / `build_core()` now stale, and how do we invalidate it?" —
**dissolves** once the science/environment boundary (§2A.2, §2A.4) is drawn at
the worker. It does not need a `reload` method or a save hook. Two facts, both
verified in the code, make this true:

1. **The workbench writes only *science*** (studies, investigations, decisions,
   references, and `.composite.yaml` **specs** — all YAML). It never writes
   *environment* code (`@composite_generator` functions, `build_core`, the
   `pbg_<project>` package, the lockfile). The one thing that could have been an
   exception — the RPC server itself — is path-injected, not written (§4).
2. **The worker's registry and `build_core` depend only on the environment.**
   `_REGISTRY` is populated by importing `@composite_generator`-decorated Python;
   `build_core()` builds the type core from registered *processes/types*. Neither
   depends on any `.composite.yaml` **spec** (a spec is a *document resolved
   against* the core, not part of it).

Therefore **no science save can stale the worker's registry or core.** The split
follows:

- **Generators (environment).** In the worker's registry. Changed only when the
  *environment* changes — i.e. a new `source_version` (a new commit / re-sync),
  which is a **new worker** via `WorkspaceStore` re-materialization (the `switch`
  path, §7), not an in-place reload. A save never touches them.
- **Static `.composite.yaml` specs (science).** Owned by the workbench / the
  science record. On save the workbench's own listing is fresh immediately (it
  re-reads the file). When it needs the *environment* to build state from a spec,
  it passes the doc **inline** (`resolve_composite_state{ document }`, §11) — the
  worker computes against its core and returns; it caches no spec, so there is
  nothing to invalidate.
- **`build_core` / the type core (environment).** Unaffected by any spec edit;
  re-materialized only on an environment change, same as generators.

So the worker is **pure environment**: it takes science (a spec doc, a generator
ref, overrides) as *input* and returns computed results, holding no science of
its own. A save changes science, which the workbench owns and re-reads; the next
worker query carries the fresh science as a parameter. **The mooted `reload`
method is dropped**; the only worker invalidation is environment re-materialization
(a new `source_version`), which is a worker restart, reusing §7's switch/§9's
crash-recovery path.

**Residual edge — an *environment* change made through the workbench.** If a
future authoring flow ever writes environment code (e.g. an AI skill that emits a
`@composite_generator`), that *is* an environment change and must go through
`source_version` re-materialization (new worker), never an in-place mutation of a
live worker's registry. Keeping such writes on the environment side of the
boundary (a distinct commit/version), not folded into a science save, is what
preserves this whole property. Today no such flow exists (fact 1); if one is
added, it re-materializes, it does not "reload."

## 14. Versioning

`initialize` returns `protocol_version` (semver). The workbench refuses a worker
whose **major** it doesn't speak. For the local adapter worker code ships with
the workbench, so this is always compatible; it earns its keep for the **cloud**
adapter, where sms-api's worker may be a different build. New methods / optional
params are **minor** bumps; a removed/changed method is **major**.

## 15. Security

Local: `socketpair` is same-process-tree, no network surface. The worker
executes the user's own workspace code (`build_core`, generators) — no new trust
boundary is crossed locally; it's the user's own environment. Cloud: sms-api
already sandboxes workspace code for runs; the query worker in the pod/container
must inherit the **same** sandbox (it runs the same arbitrary workspace Python).
Flag for the sms-api boundary owners when the cloud adapter lands.

## 16. A worked exchange

```
workbench                                   worker (<venv>/bin/python)
   │  spawn + fd3 ─────────────────────────▶│  build_core(); prime registry
   │◀───────────────── {ready} notification │  (or {error} → exit)
   │  → initialize ────────────────────────▶│
   │◀───── {result:{protocol_version:"1.0", │
   │        capabilities:[…]}}              │
   │  → resolve_composite_state{ref:baseline}│
   │                                        │  build_generator(entry)  (~1-3s)
   │◀───── {result:{state:{…},module:…}}    │
   │  → list_composites ───────────────────▶│
   │◀───── {result:[…]}                     │
   │  … session ends / switch …             │
   │  → shutdown ──────────────────────────▶│  flush; exit 0
```

## 17. Worker pool & eviction policy

How long a session's worker stays warm, and how many live at once. Two costs to
balance: a warm worker holds a **whole workspace environment in memory**
(`build_core` + the package + `_REGISTRY` + `v2ecoli` — for v2ecoli-scale, not
cheap), while a cold start pays interpreter launch + `build_core` (~1–3 s, §7).

- **Lazy spawn, never eager.** A worker starts on a session's **first env query**,
  not on session open. Most of the ~16 relocated sites are Explorer / composite /
  viz paths; a session doing only science authoring (studies, decisions) touches
  none of them and **pays for no worker at all**.
- **Warm while active; idle-TTL eviction.** The worker stays warm as the session
  queries it. After `T_idle` with no query it is **evicted** (SIGTERM→SIGKILL,
  §7); the next query re-warms — a cold start surfaced as a brief "preparing
  environment…" state. This reclaims memory from abandoned/idle sessions (a closed
  tab never sends an end signal) without touching active ones. Default `T_idle`
  ~15 min.
- **Global warm-pool cap `K`.** Independent of session count, at most `K` workers
  are live at once — the real backstop on a shared pod, because memory is bounded
  by `K × per-worker footprint`, **not** by how many sessions connect. `K` is
  sized from the memory budget, not the user count.
- **Eviction is LRU-idle; never kill a mid-query worker to admit another.** To
  admit worker `K+1`, evict the least-recently-used **idle** worker. If all `K`
  are mid-query (rare — queries are ~seconds and bursty), the new spawn **waits**
  (bounded) for one to finish rather than interrupting another session's in-flight
  work; on wait-timeout, surface a retryable "environment busy." Killing an
  in-flight query to make room would lose another user's result and force their
  re-warm — the pathology to avoid.
- **Eviction frees the process, not the venv.** Evicting a worker reclaims process
  memory but leaves the materialized venv on disk, so **re-warm is a re-spawn
  (~seconds), not a re-`uv sync` (minutes)**. The venv's own lifecycle — GC of
  abandoned per-workspace venvs on a much longer horizon — belongs to
  `WorkspaceStore` (§2A.6), not this pool. The two evictors are layered: this one
  is memory-pressure/idle on *processes*; that one is disk/staleness on *venvs*.
- **`switch` and session-end are immediate, not idle.** A `switch` tears the old
  worker down at once and lazily spawns for the new source (§7); an explicit
  session end does the same. Idle-TTL exists precisely for the sessions that
  *don't* signal end.

`T_idle` and `K` are typed **runtime config** (plan §G / pydantic-settings), not
constants: local single-user dev needs no meaningful cap (`K` effectively
unbounded, one worker persists); a shared pod sets `K` from its memory budget and
`T_idle` shorter if churn is high.

## 18. Open questions (deferred to implementation)

- ~~**Idle eviction / warm-pool policy**~~ — **resolved (§17):** lazy spawn,
  idle-TTL eviction, a global LRU-idle pool cap `K`, eviction frees the process
  (not the venv), `switch`/end are immediate. `T_idle`/`K` are runtime config.
- ~~**Registry-affecting mutations / invalidation on save**~~ — **resolved (§13):**
  the workbench writes only science, the worker holds only environment, so a save
  never stales the worker; there is no `reload` and no save hook. The only worker
  invalidation is `source_version` re-materialization (a new worker).
- **Transport test coverage on both host OSes.** macOS and Linux are both
  supported backend hosts (§2), and the transport (socketpair, `pass_fds`
  inheritance, SIGKILL-to-restart) is the OS-touching part — it wants a test lane
  on each, not just the Ubuntu CI runner. Given macOS is a day-one demo host, a
  macOS transport smoke test belongs in the harness from the start.
- **Cloud transport specifics** (Phase 3): the sms-api endpoint shape, auth, and
  whether the container is one-per-session (warm) or request-routed — parked
  until the cloud adapter.
