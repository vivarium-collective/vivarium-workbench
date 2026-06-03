# E2E Demo Recipe

## Prerequisites
- Branch feat/hpc-backend-integration checked out in vivarium-dashboard
- Commands run from vivarium-dashboard/ unless noted
- Access to a SLURM cluster with apptainer and a compatible .sif image

### Part A — Todo #20: HPC Run Pullback (loom-explore)

#### Step A1: Configure HPC credentials
```bash
cat > tests/_fixtures/ws_increase_demo/.pbg/hpc.env << 'ENVEOF'
slurm_submit_host="login.cluster.edu"
slurm_submit_user="your_user"
slurm_submit_key_path="/path/to/ssh_key"
slurm_partition="your_partition"
hpc_repo_base_path="/path/on/cluster"
ENVEOF
```

#### Step A2: Start the server

```vivarium-dashboard serve --workspace tests/_fixtures/ws_increase_demo```

#### ...or: 

```python -m vivarium_dashboard.server --workspace tests/_fixtures/ws_increase_demo```

**Expected:** Listening on http://127.0.0.1:8881

#### Step A3: Open loom-explore

- Browse to http://127.0.0.1:8881/static/loom-explore/index.html?composite=<base64>
- Or navigate via the dashboard's Composite Explorer tab and load the increase-demo composite

#### Step A4: Submit an HPC run
1. In loom-explore, ensure the HPC backend is selected (Run tab → compute backend dropdown)
2. Fill out run config (e.g. command v2ecoli-colony, 1 cell, 10 min)
3. Click Run on HPC
4. Expected: A run card appears in the HPC Results tab with RUNNING state

#### Step A5: Watch auto-pullback
1. Switch to the HPC Results tab
2. Expected: The SLURM state chip transitions PENDING → RUNNING → COMPLETED
3. On COMPLETED: auto-pullback fires → chip shows ⟳ Syncing… → then ✓ Synced
4. RunDetail shows "✓ Synced 1.2 MB in 3.5s" with bytes and duration
#### Step A6: Retry on failure (optional)
- If pullback fails (e.g. cluster unreachable), chip shows ✗ Failed
- Click ↻ Retry pullback button in RunDetail
- Expected: retry triggers, chip transitions to ⟳ Syncing… then ✓ Synced

---

### Part B — Todo #21: Investigation Array on HPC

#### Step B1: Start the server (if not already running)
vivarium-dashboard serve --workspace tests/_fixtures/ws_increase_demo
Step B2: Navigate to Studies tab
- Browse to http://127.0.0.1:8881/
- Click Studies tab (or Investigations depending on dashboard version)

#### Step B3: Select the baseline investigation
- From the investigation list, click baseline
- Expected: investigation detail page loads with the spec: 2 simulations (1 single, 1 sweep with 3 tasks = 4 total tasks)

#### Step B4: Choose HPC backend
- In the investigation header, find the Compute backend <select> dropdown
- Select the HPC backend (e.g. slurm-cluster)
- Expected: the "Run HPC array job" button becomes active

#### Step B5: Submit the array job
1. Click Run HPC array job
2. Expected: A new HPC Array Runs section appears below the local runs table
3. Shows a table with 4 rows (one per array task), each row showing PENDING

#### Step B6: Watch array status
- Table auto-refreshes every 10 seconds
- Expected: tasks transition PENDING → RUNNING → COMPLETED (or some may FAILED)

#### Step B7: Pullback per-task
- When a task reaches COMPLETED, a Pullback button appears in its row
- Click Pullback on one completed task
- Expected: chip shows ⟳ Syncing… → ✓ Synced
- Verify: pulled-back artifacts appear under workspace's runs.db or composite directory
Verification
- Confirm no regression: Local runs still work when backend is set to local
- Confirm idempotency: Clicking "Retry pullback" on an already-synced run returns the existing record (does not re-rsync)
- Confirm workstream: Each action commits to the active workstream branch

---

### Part C — Todo #22: Spec-driven report generation

**Prerequisite:** the workspace must declare a `report_generator` block
on the study (top-level on `study.yaml`, or per-entry on
`simulation_set[*].report_generator`). It must also declare
`core_bootstrap` (top-level on `study.yaml`) if the workspace's
composites need custom type/link registrations.

**The dashboard never imports workspace-specific packages.** The
producer of these spec declarations is the workspace itself — the
standardized way to add them is to `cd` into the workspace clone and
run an agentic coder skill:

```
cd /path/to/<workspace>
# inside Claude / opencode in that working directory:
/pbg-expert ./
```

`/pbg-expert` (a `pbg-superpowers` skill) emits the required
declarations + any per-workspace bootstrap module. Hand-authoring
per the schema in `info/hpc.md` works too.

#### Step C1: Confirm the study is post-#22 compliant
- Open the study's `study.yaml` in the workspace
- Confirm `report_generator:` is declared (top-level or on at least
  one `simulation_set[*]` entry). Example:

  ```yaml
  report_generator:
    script: reports/colony_report.py
    args:
      duration: "{steps_clamped:5}"
      seed: "{overrides[seed]}"
      n-adder: "{overrides[n_cells]}"
      out: "/app/out/colony/{run_id}.html"
    output_dir: out/colony
  ```

- Confirm `core_bootstrap:` is declared if the composites need it.
  Example:

  ```yaml
  core_bootstrap: pbg_<wsname>.hpc:bootstrap_core
  ```

  If not yet declared, run `/pbg-expert ./` in the workspace or
  hand-author per `info/hpc.md` ("Spec-driven HPC dispatch" section).

#### Step C2: Open the investigation detail page
- Refresh the dashboard's study detail page in the browser
- Expected: a `generate report` checkbox appears next to `include gated`
  in the HPC run controls. If it's hidden, the study has no
  `report_generator` declared — return to Step C1.

#### Step C3: Submit with report generation
- Optionally pick a subset of `simulation_set` entries
- Check `generate report`
- Click `Run HPC array job`
- Expected: array dispatch fires; each task invokes the declared
  script with the rendered args; outputs land in the workspace's
  declared `output_dir`

#### Step C4: Pull back + view the report
- Wait for tasks to reach COMPLETED, run pullback (as in Part B)
- Open the **Visualizations** tab on the study detail page
- Expected: each task's report HTML appears as an iframe-embedded
  card. Files under `output_dir` are auto-discovered.

#### Verification
- Confirm `generate report` is hidden on a study without
  `report_generator`. The dashboard never offers a control the
  workspace hasn't wired up.
- Confirm the dispatch failure is informative: clicking
  `generate report` on a partial declaration (some entries declare it,
  others don't, no top-level fallback) returns a 400 naming the
  missing entries.

#### Step C5: Verify auto-render after pullback (todo #23)
- Watch the HPC array status panel after submitting an array run
- After all per-task pullback chips reach `✓ synced`, a new status
  chip appears below the array status row:
  - `⟳ rendering report…` (transient, ~1–3 seconds)
  - then one of:
    - `✓ report rendered (N visualization[s]) — view` (clicking
      `view` reloads the page so the freshly-rendered HTML appears in
      the Visualizations tab)
    - `✓ all tasks synced — no spec-declared visualizations to render`
      (the study has no `visualizations:` block; report_generator
      outputs, if any, are already auto-discovered)
    - `✗ report render failed — retry` (clicking `retry` re-fires
      the POST)
- No manual click is required between submitting the array job and
  seeing the report (Acceptance criterion #5 from todo #21, closed
  via todo #23).
- If a task's SLURM run FAILED and its pullback also failed, the chip
  waits until that task's pullback chip settles (synced or failed)
  before firing or skipping the render.

---