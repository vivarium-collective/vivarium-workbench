# Running v2ecoli Colony Simulations on CCAM via the Vivarium Dashboard

> **Primary audience:** Researchers who want to run whole-cell *E. coli* colony
> simulations on the CCAM HPC cluster using the Vivarium Dashboard UI.
>
> **Primary client:** The Vivarium Dashboard — every step described here is
> performed through the browser UI at `http://localhost:<port>`.
> No manual SSH sessions or command-line SLURM calls are required.

---

## Quick-start jump table

| Where you are | Jump to |
|---|---|
| First time — need to set up credentials and build the container | [§ Prerequisites](#prerequisites--one-time-setup) |
| Container already built, need to run ParCa before colony | [§ Phase 2 — Run ParCa](#phase-2--run-parca-generate-parameter-cache-one-time-per-simulation-configuration) |
| ParCa cache already exists, just want to run or re-run colony | [§ Phase 3 — Run Colony](#phase-3--run-colony-simulation-repeatable) |
| Something looks wrong / job stuck | [§ Troubleshooting](#troubleshooting) |

---

## Overview

The v2ecoli whole-cell model uses a two-step compute pipeline that must be run
in order:

```
git push → GitHub Actions builds Docker image → pushes to ghcr.io/vivarium-collective/v2ecoli
                                                                     │
Phase 1 ──► Build SIF image        (one-time per code change, ~5-10 min —
                │                   pulls from GHCR, no local Docker needed)
Phase 2 ──► Run ParCa              (one-time per parameter configuration,
                │                   generates out/cache used by colony)
                ▼
Phase 3 ──► Run Colony simulation  (repeatable — reuses SIF and ParCa cache)
```

Phases 1 and 2 produce persistent artifacts on the cluster. Once they have
completed successfully, **you can run Phase 3 as many times as you like without
repeating Phases 1 or 2** (unless the model code or parameter configuration
changes).

All three phases are submitted from the **HPC page** in the Vivarium Dashboard.

---

## Prerequisites — one-time setup

### 1. Obtain the SSH key for `svc_vivarium`

The dashboard connects to CCAM as the shared service account `svc_vivarium`.
You need the private key for this account. Obtain it from your team lead
(it is stored in the sms-api secrets vault — never committed to any repo).

Save the key to a local path, e.g.:

```
~/.ssh/sms_id_rsa
```

Protect the permissions:

```bash
chmod 600 ~/.ssh/sms_id_rsa
```

### 2. Generate a known_hosts file for the CCAM login node

```bash
ssh-keyscan login.hpc.cam.uchc.edu > ~/.pbg/hpc/ccam_known_hosts
```

This fingerprints the cluster host so the dashboard's SSH client can verify
it without an interactive prompt.

### 3. Create `.pbg/hpc.env` in your v2ecoli workspace

The `.pbg/` directory is **gitignored** — these credentials never enter version
control. Create the file:

```bash
# From your v2ecoli workspace root:
mkdir -p .pbg

cat > .pbg/hpc.env << 'EOF'
# CCAM HPC credentials — gitignored, never commit this file.

SLURM_SUBMIT_HOST=login.hpc.cam.uchc.edu
SLURM_SUBMIT_USER=svc_vivarium
SLURM_SUBMIT_KEY_PATH=/path/to/your/sms_id_rsa        # ← update to your key location
SLURM_SUBMIT_KNOWN_HOSTS=/path/to/your/ccam_known_hosts  # ← path from step 2

SLURM_PARTITION=vcell
SLURM_QOS=vcell-services

HPC_IMAGE_BASE_PATH=/projects/SMS/sms_api/prod/images
HPC_REPO_BASE_PATH=/projects/SMS/sms_api/prod/repos
HPC_SIM_BASE_PATH=/projects/SMS/sms_api/prod/sims
HPC_LOG_BASE_PATH=/projects/SMS/sms_api/prod/htclogs

SINGULARITY_CMD=apptainer
APPTAINER_TMPDIR=/tmp/apptainer
EOF
```

> **Only `SLURM_SUBMIT_KEY_PATH` and `SLURM_SUBMIT_KNOWN_HOSTS` differ
> between users** — everything else is shared cluster configuration.

### 4. Connect to the VPN

The CCAM login node (`login.hpc.cam.uchc.edu`) requires the UConn Health VPN.
Connect before launching the dashboard. The connectivity chip on the HPC page
will remain red until the VPN is active.

### 5. Launch the Vivarium Dashboard

```bash
vivarium-dashboard serve --workspace /path/to/your/v2ecoli --port 9863
```

Open `http://localhost:9863` in your browser. You should see the main
dashboard with a **HPC: CCAM** link in the left nav rail (it appears
automatically when `compute_backend: hpc:ccam` is set in `workspace.yaml`).

Click **HPC: CCAM** — or navigate directly to `http://localhost:9863/hpc/ccam`.

### 6. Verify the connection

The **connection chip** in the top-right of the HPC page shows the cluster
status at page load:

| Chip colour | Meaning | Action |
|---|---|---|
| 🟢 **reachable** | SSH works, `apptainer` found on cluster | Proceed |
| 🟡 **not configured** | `.pbg/hpc.env` missing or incomplete | Check step 3 above; reload page |
| 🔴 **unreachable** | SSH timed out | Check VPN; check key path; reload page |

---

## Phase 1 — Build the Container Image *(one-time per code change, ~5–10 min)*

> **Skip this phase** if someone has already built the image for the current
> version of the v2ecoli code. The image lives at
> `/projects/SMS/sms_api/prod/images/v2ecoli.sif` on the cluster.
> Ask your team if unsure — no need to rebuild if your changes are already
> reflected in the current image.

> **Prerequisite:** Your code changes must be **pushed to GitHub** before
> clicking Start Build. GitHub Actions automatically builds and pushes the
> Docker image to GHCR on every push — the dashboard build job pulls from
> there. If your push is in progress, wait for the
> [Actions CI run](https://github.com/vivarium-collective/v2ecoli/actions)
> to complete (typically 10–20 min for a fresh Python build, faster with
> GitHub's layer cache).

### What this does under the hood

1. Submits an `sbatch` build job to SLURM — **no local Docker needed,
   no `--fakeroot` required on the cluster**
2. The job pulls the pre-built Docker image from GHCR using Apptainer's
   built-in OCI registry client (no Docker daemon involved):
   ```bash
   apptainer build docker://ghcr.io/vivarium-collective/v2ecoli:sha-<hash> v2ecoli.sif
   ```
   Falls back to `:latest` if the sha-pinned tag isn't available yet.
3. The dashboard polls the job state every 5 seconds and streams the build log

### Steps in the dashboard

1. Push your latest v2ecoli code to GitHub (if you have local changes)
2. Navigate to **`http://localhost:9863/hpc/ccam`**
3. In the **Container Build** panel, click **Start Build**
4. The button changes to **Building…** and the log viewer appears below it.
   You will see Apptainer layer-pull progress (`Copying blob`, `Writing manifest`, etc.)
5. Wait for the status box to turn **green: `COMPLETED`**

   A build takes **5–10 minutes** — the Docker layers are pre-built by CI;
   Apptainer just pulls and packs them into SIF format.

6. If the build fails (`FAILED` / red status), check the log for the GHCR
   pull error. Common causes: image not yet pushed (CI still running),
   or GHCR package visibility is private (see Troubleshooting below).

> **You only need to rebuild when the v2ecoli code or its dependencies
> change *and* those changes have been pushed to GitHub.** The `.sif` file is
> cached on the cluster and reused by all subsequent ParCa and Colony runs.

---

## Phase 2 — Run ParCa: Generate Parameter Cache *(one-time per simulation configuration)*

> **Skip this phase** if `out/cache` already exists on the cluster from a
> previous run (same container version, same parameter configuration).
> Jump directly to [§ Phase 3](#phase-3--run-colony-simulation-repeatable).

ParCa ("Parameter Calculator") generates a compiled parameter cache in
`out/cache/`. Colony simulations **will fail immediately** if this cache is
absent — ParCa must complete successfully before any colony run.

The cache is persistent on the cluster filesystem (bind-mounted as
`{remote_workspace}/out:/app/out`) and is reused across all colony runs until
you delete it or change the model parameters that affect it.

### Steps in the dashboard

1. Navigate to **`http://localhost:9863/hpc/ccam`**

2. In the **Run Simulation** panel, fill in the **Command** field:

   ```
   uv run v2ecoli-parca --cache-dir out/cache
   ```

3. Set resources appropriate for ParCa — these are reasonable defaults:

   | Field | Recommended value | Notes |
   |---|---|---|
   | CPUs | `8` | ParCa is moderately parallelised |
   | Memory (GB) | `16` | Large parameter matrices; more is safer |
   | Time (min) | `120` | Typically completes in 30–60 min |

4. Click **Submit Job**

5. The submission line below the form shows:
   ```
   Submitted: SLURM job 2142778
   ```
   The job appears immediately in the **Recent Jobs** list below the form.

6. The job state polls automatically every 10 seconds. Wait for it to reach
   **COMPLETED** (green text in the state column).

   Typical ParCa wall-time: **30–60 minutes** depending on model scale.

7. Once COMPLETED, the `out/cache/` directory exists on the cluster and is
   ready for colony runs.

> **Note:** If ParCa fails (state: `FAILED`), retrieve the SLURM job ID from
> the Recent Jobs list and check the log on the cluster:
> ```bash
> ssh svc_vivarium@login.hpc.cam.uchc.edu \
>   "cat /projects/SMS/sms_api/prod/htclogs/v2ecoli/*-<JOB_ID>.out"
> ```
> Fix the issue, then re-submit.

---

## Phase 3 — Run Colony Simulation *(repeatable)*

> **Prerequisite check:**
> - ✅ Phase 1 complete: `v2ecoli.sif` exists on the cluster
> - ✅ Phase 2 complete: `out/cache/` exists on the cluster
>
> If either is missing, complete the relevant phase above first.

This is the main simulation run. Each Colony submission starts fresh cells
using the ParCa cache from Phase 2. You can submit multiple Colony runs with
different parameters without re-running Phase 1 or Phase 2.

### Steps in the dashboard

1. Navigate to **`http://localhost:9863/hpc/ccam`**

2. In the **Run Simulation** panel, fill in the **Command** field:

   ```
   uv run v2ecoli-colony --n-cells 4 --duration-min 50 --cache-dir out/cache
   ```

   **Key parameters:**

   | Parameter | Default | Description |
   |---|---|---|
   | `--n-cells` | `4` | Number of cells in the colony |
   | `--duration-min` | `50` | Simulated time in minutes of cell growth |
   | `--cache-dir` | `out/cache` | Location of the ParCa cache — **do not change** unless you re-ran ParCa to a different path |

3. Set SLURM resources:

   | Field | Recommended value | Notes |
   |---|---|---|
   | CPUs | `4` | One core per cell is a reasonable starting point |
   | Memory (GB) | `8` | Scale up for larger colonies or longer durations |
   | Time (min) | `60` | Add buffer for larger runs; adjust to `--duration-min` |

4. Click **Submit Job**

5. Monitor the run in the **Recent Jobs** list. State transitions:
   ```
   PENDING → RUNNING → COMPLETED
   ```
   Each state polls every 10 seconds automatically.

6. Results land in `{remote_workspace}/results/` (bind-mounted as
   `/app/results` inside the container). Retrieve them via `rsync` or
   `scp` after the job completes:

   ```bash
   rsync -avz \
     svc_vivarium@login.hpc.cam.uchc.edu:/projects/SMS/sms_api/prod/repos/v2ecoli/results/ \
     ./results/
   ```

### Running multiple colony experiments

Each **Submit Job** click creates an independent SLURM job. You can queue
several colony runs simultaneously (different `--n-cells`, `--duration-min`,
or seed values) — they will run in parallel (subject to cluster availability)
and write to separate subdirectories under `results/`.

To vary parameters, simply change the command field before each submission.
Example batch of three runs:

```
# Run 1 — small colony, short duration
uv run v2ecoli-colony --n-cells 2 --duration-min 30 --cache-dir out/cache

# Run 2 — standard colony
uv run v2ecoli-colony --n-cells 4 --duration-min 50 --cache-dir out/cache

# Run 3 — larger colony, longer duration
uv run v2ecoli-colony --n-cells 8 --duration-min 100 --cache-dir out/cache
```

All three can be submitted back-to-back from the dashboard before any of them
starts running.

---

## Monitoring running jobs

### Recent Jobs panel

The **Recent Jobs** panel on the HPC page (`/hpc/ccam`) shows all jobs
submitted from this workspace, with live state polling:

| Column | Meaning |
|---|---|
| Type badge | `BUILD` (purple) or `RUN` (blue) |
| Script | The `.sbatch` file name — encodes the job ID |
| State | `PENDING` / `RUNNING` / `COMPLETED` / `FAILED` / `CANCELLED` |
| Time | Submission timestamp |
| Cancel | Sends `scancel` immediately via the dashboard |

### Cluster Status panel

The **Cluster Status** panel shows active SLURM partitions and currently
running jobs across the cluster. Click **↻** to refresh on demand. This is
useful to check how busy the cluster is before submitting long jobs.

---

## Troubleshooting

### Connectivity chip stays yellow "not configured"

The `.pbg/hpc.env` file is missing or has empty required fields.
Check that these four fields are filled:

```
SLURM_SUBMIT_HOST=
SLURM_SUBMIT_USER=
SLURM_PARTITION=
HPC_REPO_BASE_PATH=
```

The missing fields are listed in the warning box that appears under the
chip. Reload the page after editing `hpc.env`.

### Connectivity chip stays red "unreachable"

Most common causes:

1. **VPN not connected** — reconnect and reload the page.
2. **Wrong key path** — verify `SLURM_SUBMIT_KEY_PATH` points to the correct
   private key file and that it has `600` permissions.
3. **Known hosts mismatch** — regenerate with
   `ssh-keyscan login.hpc.cam.uchc.edu > ~/.pbg/hpc/ccam_known_hosts` and
   update `SLURM_SUBMIT_KNOWN_HOSTS` in `hpc.env`.

### Build job fails — GHCR pull error

The build script pulls from `ghcr.io/vivarium-collective/v2ecoli`. Common
causes of failure:

1. **Image not yet pushed** — GitHub Actions CI is still running. Check the
   [Actions tab](https://github.com/vivarium-collective/v2ecoli/actions) and
   wait for the `Build and Push Workspace Image` workflow to complete, then
   click **Start Build** again.

2. **GHCR package is private** — the `vivarium-collective` org auto-syncs
   visibility, but on a brand-new package this can lag by one push. Fix:
   go to `github.com/orgs/vivarium-collective/packages`, find `v2ecoli`,
   open **Package settings → Danger Zone → Change package visibility → Public**.
   Subsequent pushes sync automatically.

3. **No GHCR image and no GitHub remote** — the dashboard couldn't infer
   the image URL. Verify `git remote get-url origin` returns a GitHub URL,
   or add `GHCR_IMAGE=ghcr.io/vivarium-collective/v2ecoli` to `.pbg/hpc.env`.

### Colony fails with "cache not found" or similar

ParCa has not run yet (or ran with a different `--cache-dir`). Run Phase 2
before re-submitting Colony.

### Job stuck in PENDING for a long time

The cluster queue is busy. Check the Cluster Status panel for active jobs.
If the partition shows no available nodes, you may need to wait. You can also
cancel the job from the Recent Jobs panel and resubmit with a lower resource
request.

### Results directory is empty after COMPLETED

Results are written to `{remote_workspace}/results/` on the cluster filesystem.
Retrieve them manually:

```bash
rsync -avz \
  svc_vivarium@login.hpc.cam.uchc.edu:/projects/SMS/sms_api/prod/repos/v2ecoli/results/ \
  ./results/
```

---

## Reference

### Dashboard pages

| URL | Description |
|---|---|
| `http://localhost:<port>/` | Main dashboard — workspace overview |
| `http://localhost:<port>/hpc/ccam` | CCAM HPC control page |

### API endpoints (for scripting / debugging)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/hpc/hpc:ccam/status` | SSH reachability + apptainer probe |
| `GET` | `/api/hpc/hpc:ccam/slurm` | Partition list + running jobs |
| `POST` | `/api/hpc/hpc:ccam/build` | Submit container build job |
| `GET` | `/api/hpc/hpc:ccam/build/<id>` | Poll build job state |
| `POST` | `/api/hpc/hpc:ccam/run` | Submit simulation job |
| `GET` | `/api/hpc/hpc:ccam/run/<job_id>` | Poll run job state |
| `POST` | `/api/hpc/hpc:ccam/run/<job_id>/cancel` | Cancel a job |
| `GET` | `/api/hpc/hpc:ccam/runs` | List recent jobs |

### `.pbg/hpc.env` field reference

| Variable | Required | Description |
|---|---|---|
| `SLURM_SUBMIT_HOST` | ✅ | Login node hostname |
| `SLURM_SUBMIT_USER` | ✅ | SSH username on the cluster |
| `SLURM_SUBMIT_KEY_PATH` | Recommended | Path to SSH private key; falls back to SSH agent |
| `SLURM_SUBMIT_KNOWN_HOSTS` | Recommended | Path to known_hosts; falls back to `~/.ssh/known_hosts` |
| `SLURM_PARTITION` | ✅ | SLURM partition name |
| `SLURM_QOS` | — | Quality-of-service name (recommended for CCAM) |
| `HPC_REPO_BASE_PATH` | ✅ | Remote base path where workspace files are synced |
| `HPC_IMAGE_BASE_PATH` | — | Remote base path for `.sif` image storage |
| `HPC_SIM_BASE_PATH` | — | Remote base path for simulation outputs |
| `HPC_LOG_BASE_PATH` | — | Remote base path for SLURM job logs |
| `GHCR_IMAGE` | — | Override GHCR image ref (e.g. `ghcr.io/vivarium-collective/v2ecoli`). Auto-inferred from git remote origin when empty. |
| `SINGULARITY_CMD` | — | `apptainer` (default) or `singularity` |
| `APPTAINER_TMPDIR` | — | Temporary directory for Apptainer builds (default: `/tmp/apptainer`) |

---

*Last updated: 2026-05-27 · Vivarium Dashboard `feat/hpc-backend-integration`*
