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
**vivarium-workbench**, not the workspace. The workbench launches it *on the
workspace's interpreter* and injects its own code onto the path:

```
<venv>/bin/python  <workbench>/env_worker/__main__.py  --socket-fd <n>  --workspace <staging_path>
```

The worker module imports **only** the standard library plus what is already
present in the workspace venv (`process_bigraph`, `pbg_superpowers`, the
workspace `pbg_<project>` package, `v2ecoli`). It never imports
`vivarium_workbench`. Consequences:

- The workspace venv carries **no** workbench dependency — `uv sync` of the
  workspace repo is untouched.
- Worker code is versioned **with the workbench**, so for the local adapter the
  protocol version is always in lockstep (§13 still matters for the cloud
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

1. **Spawn.** The `WorkspaceContext` (owning the session) asks
   `EnvironmentResolver` to start a worker for its `WorkspaceHandle`. The
   resolver materializes/locates the venv (clone + `uv sync`, §2A.7) and spawns
   the worker on `<venv>/bin/python` with the socketpair fd.
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
session's queries. A per-session worker **pool** is a later option if a single
serial worker becomes a bottleneck; v1 is one worker per session.

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
methods are additive under a minor protocol bump (§13). All results are
finite-safe JSON (§below).

| method | params | result | replaces (in-process site) |
|---|---|---|---|
| `initialize` | — | `{ protocol_version, workspace_id, source_version, python, packages, capabilities[] }` | startup binds |
| `ping` | — | `{ ok: true, uptime_s }` | (new; health) |
| `registry_catalog` | — | `{ processes[], types[] }` (source-tagged) | `registry.py` |
| `list_composites` | — | `[{ id, kind, module, … }]` | `composite_lookup.py` |
| `resolve_composite_state` | `{ ref, overrides? }` | `{ state, module }` | `composite_state_views.py`, `study_run_state.py` |
| `composite_document` | `{ ref, overrides? }` | `{ document }` | `pbg_export.py`, report resolution |
| `observables` | `{ ref }` | `{ paths[] }` | `observables_views.py` |
| `declared_emit_paths` | `{ ref }` | `{ paths[] }` | `composite_resolve.py` |
| `viz_classes` | — | `[{ name, address, … }]` | `visualization_classes.py` |
| `shutdown` | — | `{ ok: true }` | (new; lifecycle) |

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

## 13. Versioning

`initialize` returns `protocol_version` (semver). The workbench refuses a worker
whose **major** it doesn't speak. For the local adapter worker code ships with
the workbench, so this is always compatible; it earns its keep for the **cloud**
adapter, where sms-api's worker may be a different build. New methods / optional
params are **minor** bumps; a removed/changed method is **major**.

## 14. Security

Local: `socketpair` is same-process-tree, no network surface. The worker
executes the user's own workspace code (`build_core`, generators) — no new trust
boundary is crossed locally; it's the user's own environment. Cloud: sms-api
already sandboxes workspace code for runs; the query worker in the pod/container
must inherit the **same** sandbox (it runs the same arbitrary workspace Python).
Flag for the sms-api boundary owners when the cloud adapter lands.

## 15. A worked exchange

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

## 16. Open questions (deferred to implementation)

- **Idle eviction:** does a session's warm worker persist for the session's
  lifetime, or evict after an idle TTL and re-warm on next query? (Memory vs.
  cold-start trade; a warm-pool cap across many sessions.)
- **Registry-affecting mutations:** an authoring action that changes a generator
  (edit + save) must invalidate the worker's in-process registry. Restart the
  worker, or add an explicit `reload` method? (Restart is simplest and reuses the
  crash-recovery path; `reload` avoids re-`build_core`.)
- **Transport test coverage on both host OSes.** macOS and Linux are both
  supported backend hosts (§2), and the transport (socketpair, `pass_fds`
  inheritance, SIGKILL-to-restart) is the OS-touching part — it wants a test lane
  on each, not just the Ubuntu CI runner. Given macOS is a day-one demo host, a
  macOS transport smoke test belongs in the harness from the start.
- **Cloud transport specifics** (Phase 3): the sms-api endpoint shape, auth, and
  whether the container is one-per-session (warm) or request-routed — parked
  until the cloud adapter.
