# Responses for the boss's HPC questions (drafted 2026-05-28)

Cross-references: `info/hpc.md` (boss's own HPC reference), `todo.md` §19 (just-landed
compute-backend selector), `todo.md` §20–§21 (new — auto-pullback + investigation HPC).

> Tone reminder while reading these: end-users (you, your team) only interact with
> the project through `vivarium-dashboard serve` and a browser. None of these
> answers should be acted on with raw `curl` — every capability is either
> already-in-the-UI or queued as a UI feature.

---

## 0. Composite Explorer's "Run" button — already wired (your premise is stale)

Your first ask ("Composite Explorer's Run button, so users can choose hpc:ccam
alongside the current local-subprocess runner") **is what todo #19 just landed.**

The Run tab inside the loom-explore iframe now has a persistent
**Compute backend** dropdown in the top nav (right-aligned, with a status pill
+ Test button). Selecting `hpc:ccam` swaps the Run-tab body from the existing
local-subprocess runner to a ParCa+Colony dispatch UI that POSTs to
`/api/hpc/<workload>/run`. Configure and Results tabs swap their bodies the
same way. The legacy HPC tab panel that lived as a sibling below loom-explore
(and the "HPC: CCAM" rail link in the sidenav) are gone — the experience is
fully owned by loom-explore.

Cross-repo branches to consume (both pushed to origin, PRs not yet opened):

- `vivarium-collective/vivarium-dashboard` @ `feat/hpc-backend-integration`,
  HEAD `7b6dc17`
- `vivarium-collective/bigraph-loom-explore` @ `feat/compute-backend-selector`,
  HEAD `1ce26cd`

The dashboard repo's `vivarium_dashboard/static/loom-explore/` ships the
rebuilt React bundle (`index-8uhj0maU.js`), so once you flip the v2ecoli
pyproject to consume the feature branch, **no separate loom-explore
checkout/build is needed** to demo. todo.md §19 Phase I has the demo recipe
that walks through this end-to-end against the unmerged branches.

What this means for items 1–5 below: every workflow answer assumes the user
is clicking through the dashboard UI, never running `curl` or `gh` directly.

---

## 1. GHCR auto-sync — your probe is wrong, not the package

You ran:
```
curl -sI https://ghcr.io/v2/vivarium-collective/v2ecoli/manifests/latest
```
and got 401, then concluded "still private."

That's not a visibility verdict. It's the Docker Registry HTTP API v2 auth
challenge — **every GHCR manifest endpoint returns 401 to an unauthenticated
client**, public or private. Your own `info/hpc.md` says exactly this in the
"GHCR troubleshooting" section (around line 62):

> `curl -sI https://ghcr.io/v2/<org>/<repo>/manifests/<tag>` returning `401`
> is **not** a visibility verdict — it's the Docker Registry HTTP API v2
> auth challenge, which every GHCR image (public or private) returns to
> unauthenticated clients. Always complete the bearer-token dance before
> drawing any conclusion.

**The right probe** (one of these):

1. **`docker pull` from a logged-out daemon** — if it succeeds, public:
   ```
   docker logout ghcr.io
   docker pull ghcr.io/vivarium-collective/v2ecoli:latest
   ```
2. **Anonymous bearer-token dance** (what `info/hpc.md` documents). You hit
   `/token?scope=repository:...:pull`, get back a JSON `{token:...}`, then
   replay the manifest request with `Authorization: Bearer <that token>`.
   A 200 here = public; a 401 = actually private.

If after the right probe it really IS still private, then yes — flip it
through the Package settings UI on github.com (Package → Settings → Change
visibility → Public). But before doing that, also check **whether the
visibility-sync workflow step actually ran**. The workflow at
`build-and-push.yml` emits a `::notice::` and bails when it can't sync —
look at the most recent Actions run log for that notice. For org-owned
packages like `vivarium-collective/v2ecoli`, auto-sync should fire as long
as the workflow has `packages: write`, the package is registered to the
repo, and the workflow ran without permission errors.

**No code change needed on the dashboard side** — this is a GitHub-settings
or workflow-run debugging question.

---

## 2. `/api/hpc/<backend>/run` — already accepts arbitrary commands

You don't need to change anything to make this generic. The handler at
`server.py:8368` (`_post_hpc_run`) reads `command` straight off the request
body — it isn't parsed, validated against a v2ecoli-shaped grammar, or
anything else. POST body shape:

```json
{
  "command": "vivarium-dashboard run-composite --request /workspace/.pbg/composite-runs/...",
  "cpus": 4,
  "mem_gb": 16,
  "time_min": 60,
  "composite_id": "pbg_ws_increase_demo.composites.increase-demo"
}
```

The cluster is resolved server-side from `workspace/.pbg/hpc.env` —
`<backend>` in the URL is just a workload-image tag (e.g. `v2ecoli`), not a
cluster selector. The body's `composite_id` is optional metadata used to
file the run record so the Composite Explorer Results tab can find it.

**Only mildly v2ecoli-aware thing in the dispatch path:** the post-guard at
`vivarium_dashboard/lib/hpc_dispatch.py:499`:

```
if [ ! "$(ls -A "$results_dir" 2>/dev/null)" ] \
   && [ ! "$(ls -A "$parca_out_dir" 2>/dev/null)" ]; then
    echo "Neither $results_dir nor $parca_out_dir has output — job likely failed."
    exit 1
fi
```

That checks for output in either the generic `results/` directory **or** the
ParCa-specific `out/sim_data/`. A generic composite that writes to
`results/` passes the guard cleanly; only a composite that writes nowhere
fails it. Not a blocker for the Composite Explorer path.

**Per the UI-only rule, you shouldn't POST this manually.** The Composite
Explorer Run tab does it for you when you select `hpc:ccam` from the
dropdown. The fact that the endpoint is shaped generically is what made
todo #19's `HpcRunPanel` possible — but the surface you actually use is the
Run tab button.

---

## 3. Results pull-back — queued as todo #20 (production-grade, UI-first)

Yes, manual `rsync` is rough. I've added **todo #20** to fix this properly.
Short version of the design:

- New `rsync_workspace_back(settings, ws_name, dest)` in `hpc_dispatch.py`.
- The polling path that already detects `COMPLETED` jobs (in
  `HpcRunPanel`/`HpcResultsPanel`) triggers an automatic pull-back into
  the local `<workspace>/out/` and `<workspace>/results/` directories.
- A new endpoint `POST /api/hpc/<backend>/run/<run_id>/pullback` (UI-only
  callers — never invoked by humans with `curl`).
- The Composite Explorer Results-tab "Run X COMPLETED" card gets a small
  progress chip during the pull and an error chip on partial failure;
  the chip resolves to a "Results pulled into out/, results/" confirmation.
- Partial pulls: rsync resumes are safe (`--partial --inplace`); if rsync
  exits non-zero, the chip flips red and the user can click "Retry pull"
  on the same card.

It's exactly the `rsync_workspace_back(job_id)` you offered to add, just
also wired into the polling-on-completion path and surfaced in the UI so
users never touch a terminal.

Full plan + acceptance criteria in `todo.md` §20. Gate it on the literal
word "proceed" per the project's planning protocol before I implement.

---

## 4. Investigation runs on HPC — queued as todo #21 (SLURM job arrays for sweeps)

Currently `_post_investigation_run` (`server.py:7811`) is a local-subprocess
loop — it iterates `run_one_composite(...)` Python subprocesses on the host.
Right call to flag it; it can't reach HPC.

I've added **todo #21**, summarised:

- Investigation Detail page gets a "Compute backend" dropdown alongside
  the existing Run button. Same selector component, same context as the
  Composite Explorer one — backend choice persists in localStorage.
- `local` backend keeps the existing subprocess loop (no regression).
- `hpc:ccam` backend dispatches via **SLURM job arrays**, not N individual
  jobs. For an N-sweep `colonies-01` with `N ∈ {1, 2, 4, 8}`, one
  `sbatch --array=0-3` submission is friendlier on the scheduler and
  gives a single `slurm_job_array_id` to poll instead of four. Each task
  in the array reads its parameter from the array index. This is the
  production-grade path for parametric sweeps and matches SLURM's
  intended use.
- New `submit_investigation_array_job(settings, ws_name, command_template,
  param_values, resources)` in `hpc_dispatch.py`.
- Aggregated progress in the Investigation Detail page Runs section:
  "3 / 4 tasks COMPLETED" + per-task expand for log tail. Same
  `HpcResultsPanel` machinery from todo #19, generalised to handle a
  list-of-tasks rather than a single run.
- Auto-pullback (todo #20) kicks in on each array task's completion.

Full plan + acceptance criteria in `todo.md` §21. Depends on todo #20
landing first (auto-pullback). Gate on "proceed" before I implement.

---

## 5. When is the dispatch API stable? — Now

The HTTP API consumed by loom-explore and (going forward) by your v2ecoli
composites is fully stable as of the cross-repo PR pair listed in §0 above.
Concretely, **these contracts will not move under your feet without a
deprecation:**

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/compute-backends` | List backends + their kinds |
| GET | `/api/compute-backends/<id>/status` | Probe one backend |
| GET | `/api/composite/<id>/hpc-config` | Read per-composite HPC config |
| POST | `/api/composite/<id>/hpc-config` | Save (server merges, doesn't replace) |
| POST | `/api/hpc/<workload>/run` | Submit SLURM job (arbitrary command) |
| GET | `/api/hpc/<workload>/run/<slurm_id>` | Status (PENDING / RUNNING / COMPLETED / ...) |
| GET | `/api/hpc/<workload>/run/<run_id>/log` | Tail sbatch output log |
| GET | `/api/hpc/<workload>/runs` | List all HPC runs in this workspace |
| GET | `/api/composite/<id>/runs` | List HPC runs filed against this composite |
| GET | `/api/composite/<id>/runs/<run_id>/summary` | Terminal-state JSON from log tail |

These survived 9 phases of todo #19 refactor without API breakage —
phases C–H are 100% loom-explore (frontend) and template cleanup.

todo #20 will **add** `/api/hpc/<backend>/run/<run_id>/pullback` and a
sidecar field on the existing `run-<id>.meta.json` for pullback state. It
won't break any of the above.

todo #21 will **add** `/api/investigation-run` support for a
`compute_backend` body field plus a sidecar for array-job tracking; the
existing local-subprocess shape stays as the default branch. It won't
break any of the above.

**Consume against `vivarium-dashboard@7b6dc17` + `bigraph-loom-explore@1ce26cd`.**
Both branches are pushed to origin. Per the project's PR-review protocol
([[feedback_pr_review_required]]) these branches won't be force-pushed
without flagging — if review feedback ever demands a force-push, you'll
get a heads-up and the new SHA before the existing one disappears.

---

## Branch-state note: yes, flip v2ecoli to the feature branch

Both of your branch-state steps are the right path:

1. In `v2ecoli`'s `pyproject.toml`, flip the dashboard source from
   `branch=main` to `branch=feat/hpc-backend-integration`:

   ```toml
   [tool.uv.sources]
   vivarium-dashboard = { git = "https://github.com/vivarium-collective/vivarium-dashboard.git", branch = "feat/hpc-backend-integration" }
   ```

2. Cherry-pick `Singularity.def` and `compute_backend: hpc:ccam` (in
   `workspace.yaml`) onto the colonies branch. That's just branch
   maintenance — no risk.

When the dashboard PR merges to `main`, flip step (1) back to
`branch=main`. The cherry-picked Singularity.def + compute_backend will
end up landing on `main` via your colonies branch's own PR cycle, so
no rework needed on step (2).

If you want the safest path while the dashboard PR is in review, pin to
the commit SHA instead of the branch:

```toml
vivarium-dashboard = { git = "...", rev = "7b6dc17" }
```

That way an inadvertent force-push on the feature branch can't move the
target out from under you. Drop the `rev` pin when the PR merges.



## Where we left off

``` 
⏺ Done. Summary of what just happened:

  Files written (none staged — both on the do-not-commit list):
  - EXPLAIN.md (new, ~280 lines) — ready-to-paste responses to the boss covering items 1, 2, 5, the branch-state question, and the "Composite
  Explorer Run button" overall ask (which is already done in todo #19). 
  - todo.md (modified, +~200 lines) — two new e2e UI-first todos: 
    - #20 — Auto-pullback of HPC run results. Fires on COMPLETED-edge in HpcResultsPanel, populates local <workspace>/results/ and out/, shows a
  chip + retry. Adds rsync_workspace_back to hpc_dispatch.py + 2 endpoints + UI extension.
    - #21 — Investigation runs on HPC via SLURM job arrays. One sbatch --array=0-N-1 per parametric sweep, not N individual jobs. Adds
  BackendSelector to the Investigation Detail page; depends on #20 for per-task pullback.

  Memory updates:
  - New: feedback_dashboard_ui_only.md — permanent rule (end-users never touch curl/shell; every feature must be in the dashboard UI).
  - feedback_do_not_commit.md — added EXPLAIN.md to the no-stage list.
  - MEMORY.md — both indexed; todo #19 entry updated to "all 9 phases A–I landed; PRs not yet opened".
  
  Outstanding work (your call on order):
  1. Read EXPLAIN.md, edit any tone/details you want, send to boss.
  2. Open the todo #19 PR pair (we were mid-flight on this when the boss message came in). Both branches are pushed; ready when you are.
  3. Decide on todo #20 / #21 sequencing. Per the planning protocol, type proceed for whichever one you want to start (#20 should land before #21
  since #21 depends on it).
  
  Per [[feedback_dashboard_ui_only]] I'll permanently keep the UX-first framing on these — no curl recipes for the boss, ever.
```