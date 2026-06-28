# Remote-sourced dashboard: make the PRIVATE plane usable & robust — Design

**Date:** 2026-06-28
**Status:** Design (brainstormed + empirically validated; implementation plan to follow)
**Author:** Eran Agmon (with Claude)

## 1. Context & goal

Goal: run the vivarium-dashboard **connected to sms-api on AWS GovCloud**, sourcing its
content from sms-api (the build's `repo@commit` + the deployment's runs) rather than a local
checkout — and make that path **robust enough to rely on**. In the language of the
three-plane architecture this is the **PRIVATE plane** (`dashboard → sms-api @ GovCloud`)
plus the "view remote → sync local" half of the core loop.

This spec does **not** re-design the architecture. It **builds on** approved prior designs and
defines the concrete, evidence-based work to close the gap between "the pieces exist" and "it
works end-to-end and survives a flaky tunnel."

### Builds on (do not duplicate)
- `2026-06-27-vivarium-server-three-plane-architecture-design.md` — LOCAL/PUBLIC/PRIVATE planes,
  one image + config, sms-api as swappable backend. **This spec enables the PRIVATE plane.**
- `2026-06-26-remote-run-thin-client-design.md` — collapse the dashboard's remote-run path to a
  thin client of sms-api (delete `RemoteRunManager`/daemon/`_poll()`; durability from sms-api's
  Postgres). **This spec adopts that decision as its robustness foundation (WS1).**
- `2026-06-23-commit-agnostic-remote-builds-design.md` / SP3 — `materialize_build`, `switch-build`,
  `.viv-build.json` stamp, remote-simulations merge.

## 2. What was empirically validated (2026-06-28)

A live PoC drove the full path: a real remote run on GovCloud + a dashboard served entirely off an
sms-api-materialized workspace (port 8013, build #66 = `v2ecoli@6ed3a74d`). Findings, **verified
against running code & a live tunnel**, not assumed:

| Surface | Result | Evidence |
|---|---|---|
| Full remote run (push→build→run→download→land) | ✅ succeeds, ~27.5 min | sim 199, run landed, no errors |
| Workspace / studies / investigations / cards off sms-api | ✅ works | 23 studies, 33 investigations via materialize |
| Committed static figures off sms-api | ✅ works | 24 figures in showcase-2 (in the tarball) |
| Run **metadata** landed from sms-api | ✅ works | run row + provenance |
| **Sim DB deployment runs** (remote_origin) | ✅ **works when `.viv-build.json` present** | sim 199 surfaced as `deployment: smsvpctest` after stamping |
| **Live charts from run data** | ❌ **0 rendered — local AND remote** | `live_count: 0` on both 8013 and 8011 |
| `materialize_build` download | ⚠️ works only with a raised timeout | build #66 took **224 s**; client default is **30 s** |

**Key correction during the PoC:** the Sim-DB "deployment runs invisible" symptom was **not** a
missing feature. `lib/remote_simulations.py` already fetches a build's deployment runs and merges
them, gated on a `.viv-build.json` stamp written by the `switch-build` route. The PoC bypassed
`switch-build` (called `materialize_build` directly), so the workspace wasn't marked as a remote
build. Stamping `.viv-build.json` (`{simulator_id, repo, commit, …}`) activated it immediately.

## 3. Problems to solve (narrow, evidence-based)

### WS1 — Remote-run robustness via the thin-client rewrite *(adopt approved design)*
The current `lib/remote_run_jobs.py` runs a 6-stage pipeline on a **daemon thread** that blocks
polling sms-api for up to an hour and stores progress in **non-durable in-process state** (lost on
dashboard restart). The approved fix is the thin-client rewrite (R1–R5 in the 2026-06-26 spec):
submit to sms-api and return its id; read `/status` on demand; land on demand. **Robustness — the
original ask ("survive tunnel drops, survive restart, reconnect to in-flight jobs") — falls out of
this for free**, because state lives in sms-api's Postgres and the client is stateless: a restart
just re-queries by sms-api id. *This supersedes the earlier "persist a JSON job journal + reconnect
sweep" idea from this session's first brainstorm — that solved a problem the thin-client deletes.*

What WS1 still must add for the chosen reliability bar:
- **Backend-reachability + auth surfacing.** The client must classify sms-api errors so the UI can
  distinguish *tunnel down* / *SSO 401 expired* / *4xx* / *5xx* and show an actionable message
  ("SSO expired — `aws sso login --profile stanford-sso`"). Today `SmsApiError` is an opaque string
  with no status/kind. Add `status: int|None` + `kind: "http"|"network"` and a 401 signal.
- **Per-operation timeouts.** The streaming downloads (`download_workspace`, `download_data`) need a
  much larger timeout than status polls; today everything shares one 30 s default. (See WS3.)

### WS2 — Live charts must render the XArray-emitter store *(the real blocker)*
A landed remote run shows **zero** live-rendered charts; the figures visible are committed static
PNGs that rode along in the repo tarball, **not** the simulation's data. Root cause, isolated (fails
**identically** on the local dashboard, so it is a render/layout incompatibility, not a remote-source
bug): the sms-api **XArray (Ray) emitter** writes a **zarr v3** store laid out
`experiment_id=…/variant=…/lineage_seed=…/<observable>/generation=N/…` (bare leaf names, `zarr.json`
metadata), whereas the dashboard's live chart reader expects the older
`…/generation=N/…/id_<leaf>` layout (v2-style). The reader must learn the XArray-emitter v3 layout
(or landing must normalize it). Without WS2, "results off sms-api" is hollow.

### WS3 — Materialize robustness + standalone-launch activation
- **Download timeout / progress.** `materialize_build` uses the client's 30 s default; a real
  workspace took 224 s → a default-config switch **hard-fails**. Raise/parameterize the download
  timeout, and surface progress (a 3–4 min silent hang reads as a freeze).
- **`.viv-build.json` on every materialize path.** Only the `switch-build` route stamps the marker;
  `materialize_build` itself doesn't. A standalone remote-sourced dashboard launched directly on a
  cache dir (`serve --workspace <cache>`) therefore won't activate the remote-simulations merge.
  Stamp the marker inside `materialize_build` (or add a documented launch flag) so the remote-sourced
  experience works however the workspace is brought up — not only via the in-UI switch.

### WS4 — Operability *(low effort, high daily value)*
- **Backend status indicator** in the rail: reachable / SSO-expired / tunnel-down, derived from a
  cheap `GET /version` probe — so you never discover the tunnel is down by launching.
- **Instance identity / sprawl.** During the PoC **six** dashboards ran on different ports/workspaces;
  a stale browser tab caused a false "this isn't remote-only" report. Make each dashboard state its
  identity (workspace + port + source) prominently; document a one-command way to see/stop instances.
- **Branch-push transparency.** `remote-run-start` silently `git push -u origin <current-branch>` and
  builds from HEAD (excluding uncommitted changes). Surface "about to push `<branch>@<sha>`; N
  uncommitted files excluded" before submit. (Folds naturally into WS1's thin start.)

## 4. Component boundaries (each independently testable)

- **`lib/sms_api_client.py`** — `SmsApiError{status, kind}`; wrap `json.loads`/`.decode`; per-call
  `timeout` (long default for the two downloads). Transport stays single-shot (no retry here).
- **`lib/remote_run_views.py` + route** — thin `remote-run-start` (ensure build → submit → return
  sms-api id) and on-demand `remote-run-status` (map sms-api `/status` → UI shape). Per WS1/the
  thin-client spec. Delete the pipeline/manager (R5).
- **`lib/study_charts` live reader (WS2)** — teach `_extract_paths_from_zarr` (or a sibling) the
  XArray-emitter v3 layout; pure function over a store path → renderable series. Headless-testable
  against a fixture store (the PoC's 336 K sim-199 zarr is a ready fixture).
- **`lib/remote_build_source.materialize_build` (WS3)** — long download timeout + progress callback;
  stamp `.viv-build.json`.
- **`lib/remote_simulations.py`** — unchanged logic; now reliably activated by WS3's stamp.
- **rail/status (WS4)** — `/version` probe → reachability pill; instance-identity banner.

## 5. Error handling (the deployment's failure modes)

| Failure | Behavior |
|---|---|
| Tunnel down (`URLError`/`OSError`) | client → `SmsApiError{kind:network}`; UI shows "tunnel down", status reads degrade to `[]`/last-known, never crash |
| SSO 401 | `SmsApiError{status:401}`; UI shows re-login hint; thin status re-queries fine after re-login (stateless) |
| sms-api 5xx | surfaced as transient with the message; on-demand status retried by the next client poll |
| Build/sim failed (terminal) | sms-api status `failed` → UI failed state with `error_message` |
| Partial/slow download | long timeout; `materialize` already extracts to staging + atomic `os.replace` (no half cache); `download_data` lands to a temp dir |
| Landing DB write fails | guard `save_metadata`; on failure remove the just-copied store so no orphan (today it orphans) |

## 6. Testing

Per the chosen approach (**code + fault-injection unit tests now; live E2E together later**):
- **Unit, fault-injected, no network** — monkeypatch the sms-api client: tunnel-drop → degrade not
  crash; 401 → re-login signal; thin start submits & returns id; thin status maps sms-api shape;
  materialize timeout/stamp; landing DB-failure cleanup.
- **WS2 fixture test** — render the PoC sim-199 v3 store → assert non-empty series (drives the layout
  fix; guards regression).
- **Live E2E (scheduled together)** — one real run over the tunnel after SSO + GitHub login: submit →
  status survives a deliberate tunnel blip → land → charts render. Tunnel: `ptools-proxy.sh -s
  smsvpctest`, `SMS_API_BASE`.
- Python-first, AI-free, **no new deps** (stdlib urllib). Tests never hit a real sms-api.

## 7. Scope boundaries

**In:** WS1–WS4 as above — enough to run the PRIVATE-plane remote-sourced dashboard reliably and see
real results.
**Out (explicitly):** tarball checksums; a general audit-logging subsystem; the PUBLIC-plane HPC
sms-api wiring (config-only, later, per three-plane non-goals); multi-seed landing beyond seed 0
(separate); the FastAPI three-plane deployment/IaC (its own spec). The earlier "JSON journal +
reconnect sweep" robustness design is **dropped** — superseded by WS1's thin client.

## 8. Open questions (resolve in planning)
1. **WS2:** fix in the *reader* (teach it the v3/XArray layout) or in *landing* (normalize the store
   to the legacy layout on land)? Reader-side is more general (works for deployment-S3 reads too,
   not just landed copies) and is the lean default — confirm during planning.
2. **WS1 status shape:** sms-api exposes build-status + run-status separately; does the UI keep a
   multi-step strip or collapse to two states? (Same open item as the thin-client spec's risk list.)
3. **WS3 stamp:** stamp inside `materialize_build` unconditionally, or only when invoked for a
   remote-sourced launch? (Unconditional is simpler and harmless — the marker is just provenance.)

## 9. Sequencing
WS3 (small, unblocks reliable bring-up) → WS2 (the results blocker) → WS1 (thin-client robustness,
larger; coordinate with the already-planned thin-client work) → WS4 (polish, anytime). WS2 and WS3
are independent of WS1 and can land first.
