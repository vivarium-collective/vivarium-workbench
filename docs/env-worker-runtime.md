# Env-worker runtime — how a workspace environment is obtained

Where the workbench gets a **runnable environment** for a workspace, and what the
lifecycle looks like end to end. Two mechanisms exist, chosen by *deployment
topology*, and both speak the identical protocol.

Companions: [`env-worker-protocol.md`](env-worker-protocol.md) is the wire
contract (§5 local transport, §5A dial-back); `REFACTOR-PLAN.md` **§2A.7** decided
the worker model and **§2A.8** decided that hosted runs the simulator's own image.
This doc is the *operational* view those two leave implicit.

> **Status (2026-08-28).** Both mechanisms are implemented, proven on
> `sms-api-stanford-test`, and now **wired into request paths** (#952, corrected
> by #954; deployed as 0.3.63–0.3.65). See [Current status](#current-status).
>
> **Update (2026-11-19).** The proxy transport (env-worker traffic relayed through
> viva-api, [`run-orchestration-consolidation.md`](run-orchestration-consolidation.md) §E)
> has since been built, and the hosted Stanford workbench runs it
> (`ENV_WORKER_PROXY_BASE=http://api:8000`). The table below names it; most of the
> rest of this doc still describes the dial-back runtime. The hosted launchers now
> come from the SMS backend provider (docs/backend-adapters.md, step P2).

---

## The two ways to get an environment

| | **Local** (a laptop) | **Hosted** (image-as-worker) |
|---|---|---|
| Where the worker runs | subprocess of the workbench | its own **Kubernetes Job** |
| Where the environment comes from | the workspace's own `.venv` | the simulator's **prebuilt image** for `(repo, commit)` |
| Transport | `socketpair` (protocol §5) | **proxy**: viva-api holds the socket and relays calls over HTTP (`ProxyWorkerLauncher`); or **dial-back TCP** (protocol §5A, `RemoteWorkerLauncher`) |
| Who creates it | `subprocess.Popen` | **viva-api** — the workbench has no cluster access (§2B.2) |
| Selected by | neither hosted variable set | `VIVARIUM_WORKBENCH_ENV_WORKER_PROXY_BASE` (proxy; it wins) or `VIVARIUM_WORKBENCH_ENV_WORKER_ADVERTISE_HOST` (dial-back) |

The hosted Stanford workbench uses the proxy (`ENV_WORKER_PROXY_BASE=http://api:8000`).

**The choice is structural, not a preference.** `env_worker_launcher.default_launcher()`
returns a hosted launcher **iff** the deployment asks for one, and the local one
otherwise — because a laptop has no cluster. One wiring decision at the
composition root (§5C.4); there is no per-call switch to cherry-pick, which is
what stops the implementations drifting into alternatives someone chooses between.

**The hosted launchers come from the backend provider** (docs/backend-adapters.md
§2.2, step P2): the core keeps `LocalWorkerLauncher`, and the SMS plugin's
`packages/vivarium-workbench-vivaapi/vivarium_workbench_sms/env_workers.py` supplies the proxy and dial-back launchers
through `Provider.worker_launcher()`. **A deployment that sets a hosted variable
when no provider supplies one is refused**: `serve` exits 2 at startup with the
reason, and a pool call raises `BackendConfigError`. It never quietly falls back to
a local subprocess.

Hosted **never builds an environment.** The image *is* the environment coordinate
(§2A.2): rebuilding it on the volume would be a second source of truth that can
drift from the one the science actually ran under.

---

## Components

```
  ┌───────────────────── workbench pod ─────────────────────┐
  │  HTTP process (imports NO workspace Python, §2A.7)      │
  │    get_pool() ── WorkerPool ── launcher ─┐              │
  │                   warm, LRU, idle-TTL    │              │
  │    DialBackListener  ◀───────────────────┼──────┐       │
  │      ephemeral port + one-time token     │      │       │
  └──────────────────────────────────────────┼──────┼───────┘
                     │ POST /env-worker/v1/workers  │ dial-back
                     ▼                              │ TCP + token
  ┌──────── viva-api ────────┐                      │
  │  EnvWorkerService        │  creates             │
  │  → K8sJobService         │────────────┐         │
  └──────────────────────────┘            ▼         │
                        ┌───────── worker pod (a Job) ───────┐
                        │ initContainer stage-worker-module  │
                        │   copies __init__.py + env_worker  │
                        │   + lib/  (6.1 MB of a 215 MB pkg) │
                        │            ↓ emptyDir              │
                        │ container: SIMULATOR IMAGE         │
                        │   python -m …env_worker            │
                        │   PYTHONPATH=/opt/env-worker  ─────┘
                        │   workspace = the image's checkout │
                        └────────────────────────────────────┘
```

Two `emptyDir` volumes, no PVC. The worker is **stateless with respect to the
scientific record** — specs travel in protocol messages (§2A.2's composite-code
boundary rule) — which is exactly what frees it from the workspace volume's
`ReadWriteOnce` single-node binding and lets it schedule anywhere.

---

## Lifecycle

```mermaid
sequenceDiagram
    participant WB as workbench (HTTP process)
    participant API as viva-api
    participant K8S as Kubernetes
    participant W as worker pod (simulator image)

    Note over WB: pool miss for (workspace, interpreter)
    WB->>WB: read .viv-build.json → commit<br/>(no stamp ⇒ refuse: not image-backed)
    WB->>WB: DialBackListener: bind :0, mint token
    WB->>API: POST /env-worker/v1/workers<br/>{commit, callback_host, callback_port, token}
    API->>API: validate (hex sha, bare host, port, alnum token)
    API->>K8S: create Job env-worker-<commit>-<rand><br/>image = ecr/<repo>:<commit>
    API-->>WB: {job_name, image}
    Note over WB: accept() — up to 300 s<br/>(pod schedule + image pull)

    K8S->>W: initContainer: stage worker module → emptyDir
    K8S->>W: container: python -m vivarium_workbench.env_worker<br/>--connect-to host:port
    W->>W: refuse if --workspace is not a directory
    W->>WB: TCP connect + first frame {token}
    WB->>WB: compare_digest — mismatch ⇒ close<br/>before the method catalog
    Note over WB,W: from here, protocol §§6–11 verbatim

    WB->>W: initialize
    W-->>WB: {protocol_version, workspace, python, capabilities}
    WB->>W: list_generators / resolve_composite_state / …
    W-->>WB: results

    Note over WB: session ends, idle-TTL, or LRU eviction
    WB->>W: shutdown
    WB->>API: DELETE /env-worker/v1/workers/{job_name}
    API->>K8S: delete Job (foreground)
    Note over K8S: TTL 3600 s is a BACKSTOP for<br/>crashes, not the normal path
```

---

## What runs where

- **The HTTP process imports no workspace Python** (§2A.7). Every environment
  fact comes through the protocol.
- **The worker answers interactive queries only** — `list_generators`,
  `resolve_composite_state`, light viz. Simulations and heavy analyses are
  **jobs**, not worker calls (protocol §12), which is why a worker pod is sized
  for interaction: `250m`/`512Mi` requests, `1`/`2Gi` limits.
- **The worker's workspace is the image's own checkout** (`/app/v2ecoli` by
  default), not the PVC. Under §2A.8 that copy *is* the environment; the PVC copy
  is the mutable record.

## Configuration

| Setting | Side | Meaning |
|---|---|---|
| `VIVARIUM_WORKBENCH_ENV_WORKER_PROXY_BASE` | workbench | viva-api's URL for the proxy transport (`1`/`true`/`yes` = the configured viva-api). **Setting it selects the proxy launcher**, and it wins over `ADVERTISE_HOST`; blank counts as unset. |
| `VIVARIUM_WORKBENCH_ENV_WORKER_ADVERTISE_HOST` | workbench | Where workers dial back — this pod's IP, via the Downward API. **Setting it (and not `PROXY_BASE`) selects the dial-back launcher**; blank counts as unset. |
| `ENV_WORKER_MODULE_IMAGE` | viva-api | The workbench image a Job stages its worker module from. **Must equal the deployment's `vivarium-workbench` tag** — same code, or a workbench speaks a protocol its own workers don't. |
| `ENV_WORKER_WORKSPACE_PATH` | viva-api | Workspace root inside the simulator image. Default `/app/v2ecoli`. |
| `ENV_WORKER_POOL_MAX` (8) · `ENV_WORKER_IDLE_TTL` (900 s) · `ENV_WORKER_CALL_TIMEOUT` (60 s) | workbench | Warm-pool size, idle eviction, per-call socket timeout (protocol §17). |

**Invariant:** every hosted served workspace must be **image-backed**. A workspace
with science code but no built image cannot answer environment questions; the
launcher refuses up front rather than failing inside a call.

## Failure modes, and what each looks like

| Symptom | Cause |
|---|---|
| `workspace … has no build stamp (.viv-build.json)` | not image-backed — refused before any Job is created |
| `env worker … did not connect` **with the worker's logs attached** | the pod never dialled back; the Job is deleted rather than left to TTL |
| worker exits 2, `unrecognized arguments: --connect-to` | `ENV_WORKER_MODULE_IMAGE` points at a workbench older than the dial-back transport |
| `--workspace … is not a directory` | `ENV_WORKER_WORKSPACE_PATH` wrong. **This guard exists because the alternative is silent:** `_list_generators()` falls back to a *global* scan when the workspace can't be imported, returning a populated, plausible, **wrong** list (measured: 53 generators vs the correct 33, 19 of them from an unrelated package) |
| HTTP 409 from `POST /workers` | a Job of that name exists or is still terminating |

**Health check that actually proves it works** — `initialize`/`ping` pass even
when the workspace is wrong:

```
list_generators against a sim82 workspace
  → workspace=/app/v2ecoli   generators=33   spatio_flux=0
```

A non-zero count of unrelated packages means the worker fell back to the global
scan. Run a **second** launch too — that is what catches Job-name collisions.

## Current status

Workstreams 1–3 and 5 of §2A.8 are done and deployed to `sms-api-stanford-test`.

**Workstream 8 step 1 is now done too** (#952, corrected by #954). `get_pool()`
consults `default_launcher()`, so **transport follows deployment topology for
every method** — the earlier note here, that `default_launcher()` was called by
nothing and all 25 call sites spawned local subprocesses, is superseded.

#952 first routed *per method* — interactive remote, job-class local — which
inverted this doc's own rule and could not work on a hosted deployment at all:
nothing there has a `.venv`, so every job-class call raised
`workspace has no .venv`. #954 corrected the axis.

**Step 2a** (#957, 0.3.64) makes run entrypoints honor
`backends.resolve_run_target`. **Step 2b**, the declared-scale precheck, is
**not started and is now load-bearing**: with transport no longer standing in for
a cost policy, nothing separates a small in-context run from one that must
dispatch.

Design: [`env-worker-routing.md`](env-worker-routing.md). Wider plan, including
the decision to proxy through sms-api:
[`run-orchestration-consolidation.md`](run-orchestration-consolidation.md).
