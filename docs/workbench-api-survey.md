# The workbench API as it stands

**Snapshot taken 2026-08-27** against `main` at the merge of #962.
Client-side companion: [`ui-api-consumption-survey.md`](ui-api-consumption-survey.md)
— which of these routes the shipped UI actually calls, and which no live client
touches.

**This is a description, not a specification.** It records what the API *does*
today so refactoring can start from fact rather than memory. Where behavior looks
accidental it is written down as accidental. Nothing here should be cited as
intended design — for that, see [`REFACTOR-PLAN.md`](REFACTOR-PLAN.md),
[`env-worker-routing.md`](env-worker-routing.md) and
[`session-binding.md`](session-binding.md). Expect it to go stale; the
"How to re-derive" section at the end says how to regenerate each part.

---

## 1. Shape

`vivarium_workbench/api/app.py` is **7,906 lines** and registers **260 routes**
(135 POST, 119 GET, 7 DELETE, no WebSocket). Route handlers are thin: most
delegate to a `lib/*_views.py` builder returning `(body, status)`, which is what
makes them testable without HTTP.

| tag | routes | tag | routes |
|---|---:|---|---:|
| Studies | 62 | Registry & catalog | 11 |
| Investigations | 34 | Downloads | 11 |
| Composites | 27 | Visualizations | 10 |
| Data, inputs & references | 24 | Auth | 6 |
| Workspaces & sources | 14 | Static & shell | 5 |
| Runs | 13 | Analyses | 4 |
| Git & workstream | 13 | Simulations | 2 |
| Rigor & jobs | 12 | (untagged) | 1 |
| System | 11 | | |

Transports are HTTP plus **one SSE stream** (`GET /api/events`, workspace-state).
There are **no WebSocket endpoints** — relevant to the relay design in
[`run-orchestration-consolidation.md`](run-orchestration-consolidation.md), which
would introduce the first.

## 2. Request pipeline

Two HTTP middlewares, plus gzip:

1. **`_csrf_mw`** — same-origin guard over the whole POST/DELETE surface.
   Stateless, shared with the retired stdlib server via `lib/csrf.py`.
   Bypassed by `VIVARIUM_WORKBENCH_DISABLE_CSRF=1` and by a missing `Origin`
   (curl, `TestClient`).
2. **`_session_workspace_mw`** — resolves the request's session cookie to a
   workspace and sets it as a **request-scoped** root.
3. `GZipMiddleware` (min 1000 bytes).

**Base path.** Under `--base-path /workbench` the served HTML gets
`report.py::_base_path_shim` injected into `<head>`, which patches `fetch`,
`EventSource` and `XMLHttpRequest` to prefix root-absolute URLs. It does **not**
patch `window.open` or `window.location` — navigations must carry
`window.__BASE_PATH__` themselves. That gap produced a real bug (#960) where
switching workspace on a prefixed deployment opened the ALB root — PTools —
instead of the workbench; `tests/js/test_base_path_navigation.js` now guards the
class.

## 3. State

Four tiers, with very different durability. This is the part most worth knowing
before refactoring.

### 3a. Process-global, in-memory — **lost on restart**

| what | where |
|---|---|
| investigation run jobs | `run_jobs.manager` — a `RunJobManager()` module singleton; dict + one daemon thread per job |
| warm env workers | `env_worker_pool` `_pool` singleton, keyed `(workspace, env_key, launcher_kind)`, LRU cap + idle TTL |
| session → workspace bindings | `session_registry` (in-memory; its own docstring calls durable persistence "a later slice") |
| process default workspace | `_root._WS_ROOT` |

A pod roll loses every running job's tracking. Hosted pods roll on every deploy.

### 3b. Per-request / per-session

`_root._REQUEST_WS_ROOT` is a **`ContextVar`** set by the session middleware, so
concurrent sessions on different workspaces don't collide. When unset — a
serve-time render, the CLI, a detached run subprocess, any cookie-less client —
resolution falls through to the process default. This is what makes ~80 existing
`workspace_root()` reads per-request-correct without threading `ws_root` through
every call site.

Sessions are **per-tab and pinned for life**: picking a local workspace opens a
new tab rather than re-pointing the current one.

### 3c. Per-workspace, on disk — under `<ws>/.pbg/`

`runs/` (33 references), `composite-runs` (the SQLite runs DB), `state`,
`artifacts`, `viz-requests` / `viz-responses`. Research content sits outside
`.pbg` at paths resolved by `WorkspacePaths`: `studies/`, `investigations/`,
`reports/`, `references/`.

`composite-runs.db`'s `runs_meta` table is the durable record of a run and backs
the browser's polling. It uses an **additive migration** pattern
(`_NEW_COLUMNS` + `_migrate_runs_meta`, nullable columns only), which is the
established way to extend it.

### 3d. Machine-global — under `$VIVA_HOME` / `$PBG_HOME`, default `~/.pbg/`

`workspaces.json` (the switcher's catalog), `servers/<name>.json` (live-server
join for the switcher), `build-cache/` (materialized builds, and
`build-cache/sessions/<session>/<simN-commit>` for per-session clones).

Owned by `viva_superpowers.workspace_catalog`, not by this repo. Note the
resolution order is `VIVA_HOME or PBG_HOME or ~/.pbg` — a default set on one
variable and an override on the *other* does not compose, which is what made the
test-isolation fix in #955 non-obvious.

## 4. Configuration

All read through `lib/env_compat.get_env`, which prefers
`VIVARIUM_WORKBENCH_<NAME>` and falls back to the deprecated
`VIVARIUM_DASHBOARD_<NAME>`.

| variable | effect |
|---|---|
| `WORKSPACE` | process default workspace root |
| `READONLY` | **drops** authoring routes at registration (see §5) |
| `DISABLE_CSRF`, `TRUST_PROXY`, `ALLOWED_ORIGINS`, `ALLOWED_HOSTS` | request-guard tuning; `TRUST_PROXY` only behind a proxy you control; `ALLOWED_HOSTS` (comma-separated bare names) adds `Host` names a loopback-bound server accepts besides `localhost` / `127.0.0.1` / `[::1]` |
| `REMOTE_PINNED`, `REMOTE_REPO_URL`, `REMOTE_BRANCH` | pinned-run mode; **`REMOTE_PINNED` alone is not enough — `REMOTE_REPO_URL` must also be set** or `pinned_config()` returns `None` |
| `REMOTE_DEPLOYMENT` | Origin name for remote runs; defaults to `smsvpctest` |
| `ENV_WORKER_ADVERTISE_HOST` | **selects the remote launcher** — set ⇒ worker-as-image, unset ⇒ local subprocess |
| `REQUIRE_WORKSPACE_VENV` | strict mode: no venv ⇒ raise rather than fall back to the server's interpreter |
| `ENV_WORKER_POOL_MAX`, `ENV_WORKER_IDLE_TTL`, `ENV_WORKER_CALL_TIMEOUT` | warm-pool bounds |
| `BUILD_CACHE`, `REPO_STORE`, `VENV_STORE` | cache relocation |
| `GH_CLIENT_ID`, `GH_TOKEN` | GitHub device-flow auth |
| `DEPLOY_CONFIG` | path to `deploy.yaml` — the site's `ui.*` overlay (#471) |

Not prefixed: `VIVA_API_BASE` / `SMS_API_BASE` (viva-api base, default
`http://localhost:8080`), `VIVA_HOME` / `PBG_HOME`, and the run-scoped
`VIVARIUM_WORKBENCH_RUN_ID` / `RUN_DIR` / `SWEEP_DIR` exported into run children.

**Three-layer `ui.*` resolution** (#471): `workspace.yaml ui:` holds laptop
defaults; `deploy.yaml ui:` holds what the *site* substitutes; the resolver
overlays at key level and never raises, so a stale `DEPLOY_CONFIG` contributes
nothing rather than failing boot.

## 5. Local vs. deployment responsibilities

The same binary serves both. What differs is configuration, and the differences
are not centralized — they are spread across the variables above.

| concern | laptop | hosted (e.g. `sms-api-stanford-test`) |
|---|---|---|
| workspace source | a real git checkout the user owns | scaffold seeded by an initContainer; science arrives by switching to a build |
| env-worker transport | local subprocess, workspace's own `.venv` | worker-as-image (`ENV_WORKER_ADVERTISE_HOST` set) |
| interpreter for a venv-less workspace | borrows the base workspace's venv (#937 bridge) | n/a — the image *is* the environment |
| run target | `local`, unless the tab switched to a build | `deployment` for **every** workspace (the pin) |
| authoring | **this is where it happens** — GitHub, user's own credentials | runs and inspects; not where work originates |
| API surface | full | `READONLY` drops authoring routes, keeping an allowlist of *actions* |
| base path | root | `/workbench` behind a shared ALB |
| reachability | direct | SSM tunnel, outbound-only — nothing can dial *in* |

**The `READONLY` allowlist is the clearest statement of the hosted model** — a
reader/displayer of git-committed content that can still *trigger* things —
**but no overlay actually sets it** (checked across `kustomize/`, 2026-08-28), so
today it is a designed posture rather than an enforced one, and the hosted
deployment registers the full authoring surface. What the hosted overlay *does*
set is `--base-path /workbench`, `SMS_API_BASE=http://api:8000`,
`REMOTE_PINNED` + `REMOTE_REPO_URL`, and `ENV_WORKER_ADVERTISE_HOST`. The
allowlist
keeps run launches (`/api/investigation-run*`, `/api/study-run-*`,
`/api/composite-test-run`, the `remote-run-*` chain), the source-switch and
remote-build flow, GitHub auth, and two non-mutating translators. Everything else
mutating is simply not registered, so it 404s. Reversible by unsetting the flag.

## 6. Seams and asymmetries worth refactoring

Observed, not proposed — each is a place where two paths answer the same question
differently.

1. **Three investigation orchestrators disagree about where work runs.**
   `/api/investigation-run-unblocked` (via `run_jobs` → `study_runs`) honors the
   run target; `/api/investigation-run` has **zero** references to
   `resolve_run_target` and runs an inline subprocess; the pbg composite path
   refuses since #957. Plan:
   [`run-orchestration-consolidation.md`](run-orchestration-consolidation.md).
2. **Runs and env workers choose interpreters by different rules.**
   `composite_subprocess.run_composite_subprocess` spawns with `sys.executable`;
   env workers go through `env_resolver.resolve_interpreter`. On the slim image
   the former cannot import the workspace package.
3. **Durability is inconsistent across state of the same importance.** A run is in
   SQLite; the *job* that owns it is in a process dict.
4. **Two `ui.*`-shaped mechanisms** — `deploy.yaml` (ordered sources, never
   raises) and pydantic-settings app config (§G). The working position is that
   they stay distinct; §G is where a reader looks and currently says nothing.
5. **The base-path shim covers requests but not navigations** (§2) — a class of
   bug invisible at root hosting.
6. **`enumerate_unblocked` means a human gate, not a DAG.** Inter-study
   prerequisites live only on the pbg path
   (`investigation_execution._study_prereqs`).

## How to re-derive

- **Routes + tags:** parse `@app.<method>("<path>", … tags=[…], summary=…)` out of
  `api/app.py`; that is how the §1 table was produced.
- **Config surface:** `grep -rhoE 'get_env\("[A-Z_]+"' --include="*.py"` plus
  `_int_env` / `_float_env`, then the non-prefixed reads in `os.environ`.
- **State tiers:** module-level singletons (`^manager = `, `global `), the
  `_root` ContextVar, `WorkspacePaths` attributes, and `.pbg/<dir>` string
  references.
- **Readonly model:** `_READONLY_ALLOWED_MUTATIONS` in `api/app.py` — the
  allowlist is the model.
- **Local vs hosted:** compare `kustomize/base/workbench/workbench.yaml` (in
  `viva-api`) against a local `serve` invocation; the delta is the env block.
