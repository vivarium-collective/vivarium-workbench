# Local Authoring + Remote Compute (sms-api) — Companion Walkthrough

**Sibling to**: `WALKTHROUGH.md` Appendix G (Local Dev, offline). This doc is
the *other* local-serve variant: **local dashboard, remote compute.** You run
`vivarium-workbench serve` on your laptop against a real `v2ecoli` checkout,
but simulations execute on GovCloud (`smscdk`) via `sms-api` — the same
compute backend `WALKTHROUGH.md` Segment 6 uses from the fully in-cluster
demo. This is what `NEXT_STEPS.md` calls **"Path B"**.

Use this flow when you want to:
- author investigations/studies/branches locally (fast iteration, your own editor/git tooling),
- but run the actual E. coli ensembles on GovCloud instead of your laptop.

It is **not** the presenter demo (`WALKTHROUGH.md` main flow, which serves
everything in-cluster) and **not** fully-offline dev (`WALKTHROUGH.md`
Appendix G, which runs the local process-bigraph engine with no sms-api
calls at all).

---

## 0. Prerequisites

Same AWS/tunnel prerequisites as `WALKTHROUGH.md` §0.1–0.2:
- `stanford` shell function in `~/.zshrc` (AWS SSO for the `smscdk` stack).
- `~/sms/sms-cdk` cloned (holds `sms-proxy.sh`).

Plus, for this flow specifically:
- A local clone of `v2ecoli` with push access (or a fork), since sms-api
  builds a commit by cloning it from GitHub — **only pushed commits can be
  registered as a build.**
- This repo (`vivarium-workbench`) checked out, with `uv sync` run at least
  once (`uv run` picks up its venv for `serve` and for the helper script
  below).

---

## 1. Terminal A — the tunnel

```bash
stanford                                    # AWS SSO for smscdk (unparameterized form)
cd ~/sms/sms-cdk/scripts
./sms-proxy.sh -s smscdk                    # -> localhost:8080, stays open
```

Confirm the proxy banner lists `http://localhost:8080/docs` (the SMS API) —
that's the endpoint this flow talks to. Keep this terminal open for the whole
session.

## 2. Terminal B — local serve, pointed at the tunnel

```bash
cd $VIVARIUM_WORKBENCH_DIR
SMS_API_BASE=http://localhost:8080 uv run vivarium-workbench serve \
  --port 8888 --workspace $V2ECOLI_DIR
```

`SMS_API_BASE` is the one addition versus a plain local `serve` — it's what
makes the dashboard's remote-run code path (`lib/sms_api_client.py`,
`lib/remote_run_views.py`) reach `smscdk` through the tunnel instead of a
non-existent local sms-api. It defaults to `http://localhost:8080` if unset
(see `tests/test_remote_run_endpoints.py`), but set it explicitly — don't rely
on the default when it matters.

Do **not** set `VIVARIUM_WORKBENCH_REMOTE_PINNED=1` here. Pinned mode locks
the UI to one fixed `repo@branch` (used to strip build UI from the presenter
demo's in-cluster deployment). You want the opposite: register whatever
commit you're iterating on, on demand.

Open `http://localhost:8888`.

---

## 3. Author locally (unchanged mechanism)

Create/edit investigations and studies through the dashboard UI exactly as in
any local session — every mutating action commits to a workstream branch in
`$V2ECOLI_DIR`'s git history (`lib/work_state.py: active_branch_action`). See
`docs/ARCHITECTURE.md` §6 if you want the mechanics. Nothing about this step
changes when `SMS_API_BASE` is set — it only affects the Simulations DB /
remote-run UI paths.

---

## 4. Push the commit you want to simulate

sms-api builds by cloning `repo_url@branch@commit` itself — it never sees your
local working tree. Push the branch/commit first:

```bash
cd $V2ECOLI_DIR
git push origin <your-branch>
```

(Or use the dashboard's **Branch** tab, which does the same push through the
GitHub device-flow auth in `lib/github_auth.py`.)

---

## 5. Build + run against that commit

Two ways to do this — pick one per situation.

### 5a. Through the UI (interactive)

Open a study → its run card → **Run on remote**. This exercises the exact
sequence `lib/remote_run_views.py` implements:
`remote_run_build_start` (push + register) → poll `remote_run_status` →
`remote_run_submit` (parameterize: generations, seeds, run ParCa,
observables) → poll again → **Land results locally** (`remote_run_land`).
Same mechanics as `WALKTHROUGH.md` Segment 6 Part B, just against a commit
*you* just authored instead of the seeded pinned build.

### 5b. Through the helper script (scriptable / CI-able)

`demos/v2ecoli/scripts/remote_commit_run.py` does the same three sms-api
calls directly — useful for a headless run, a batch of parameter sweeps, or
when you don't want to babysit the UI's polling.

```bash
# Register+build the live tip of your branch, run 2 generations x 3 seeds,
# and land the result into a study's runs.db:
uv run demos/v2ecoli/scripts/remote_commit_run.py \
  --repo-url https://github.com/<you>/v2ecoli --branch <your-branch> \
  --generations 2 --seeds 3 \
  --workspace $V2ECOLI_DIR --study showcase-2-baseline-figures
```

```bash
# Just build+run against a specific already-pushed commit, download raw
# results without landing (e.g. to inspect before committing to a study):
uv run demos/v2ecoli/scripts/remote_commit_run.py \
  --commit 70b5ec3a... --generations 1 --seeds 1
```

```bash
# Build already known-good (e.g. from a prior run) — skip straight to submit:
uv run demos/v2ecoli/scripts/remote_commit_run.py --simulator-id 69 \
  --generations 5 --seeds 10 --workspace $V2ECOLI_DIR --study my-sweep
```

Run `uv run demos/v2ecoli/scripts/remote_commit_run.py --help` for the full
flag list (poll intervals, timeouts, `--observables`, `--experiment-id`,
`--description`).

> Note the script is more of a generalization of
> `scripts/ensure_latest_main_build.sh` (which only *gates* the pinned
> `v2ecoli@main` build) — it registers+polls a build for **any** repo@commit,
> then also submits+polls a parameterized run and optionally lands it. It
> reuses `SmsApiClient` and `land_remote_run` directly rather than
> reimplementing the sms-api JSON contract, so it can't drift from what the
> UI does.

---

## 6. Iterate

Landed runs appear in **Simulations DB** with a remote ☁️ origin and full
provenance (`deployment`, `simulation_id`, `backend: ray`), same as
`WALKTHROUGH.md` §6 step 10. Re-render charts/gate against the new data,
commit, push the next commit, repeat from step 4.

---

## Troubleshooting

Same table as `WALKTHROUGH.md` Appendix E applies for tunnel/auth issues.
Two additions specific to this local+remote flow:

| Symptom | Likely Cause | Fix |
|---|---|---|
| `remote_commit_run.py` fails to import `vivarium_workbench.lib.*` | Not run via `uv run` from this repo, or venv not synced | `cd $VIVARIUM_WORKBENCH_DIR && uv sync`, then re-run with `uv run demos/v2ecoli/scripts/remote_commit_run.py ...` |
| "could not resolve pinned build" / build never appears in Source panel | You set `VIVARIUM_WORKBENCH_REMOTE_PINNED=1` | Unset it — this flow uses the dynamic register/switch path, not pinned mode |
| `register_simulator`/build never finds your commit | Commit not pushed, or pushed to a fork sms-api can't reach | Confirm `git ls-remote <repo-url> <branch>` shows the commit from the machine running the tunnel-side check |
| `remote_commit_run.py` run submit gets `simulator_id is required` equivalent from sms-api | Build phase failed silently or was skipped with a bad `--simulator-id` | Check `GET {SMS_API_BASE}/core/v1/simulator/status?simulator_id=<id>` directly |
