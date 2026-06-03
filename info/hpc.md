# HPC Integration — Implementation Execution Protocol

> **Status as of 2026-05-26.** All implementation phases (0–6) are **complete**.
> The dispatch layer, API routes, UI, and tests are merged on
> `feat/hpc-backend-integration`. Suite: **903 passed, 0 failures.**
> What remains is a **live end-to-end test against the CCAM cluster** — that
> requires `workspace/.pbg/hpc.env` filled with real credentials and VPN access.
> See the "First live dispatch checklist" section below.
>
> **Security rule (non-negotiable):** no real hostnames, usernames, SSH key
> material, fingerprints, or filesystem paths belonging to any real cluster
> may appear anywhere in version-controlled source. All sensitive values live
> in a gitignored `workspace/.pbg/hpc.env` file, loaded via Pydantic Settings.
> The pattern mirrors `~/sms/sms-api/sms_api/config.py` exactly.

> **NOTE**: SAVE ALL THE SUGGESTED COMMITS UNTIL THE END.

> **NOTE**: COMPLETE EACH PHASE ONE AT A TIME, CAREFULLY. BEFORE MOVING ON TO THE NEXT GIVEN PHASE, STANDBY FOR THE USER's WORD TO TELL YOU TO "proceed".

> **NOTE**: Testing any deployments-on, and accessing-in-general the CCAM HPC requires that the user is on their org's vpn. IN THE CASE THAT IT IS NEEDED, PLEASE PROMPT THE USER TO CONNECT TO THEIR VPN WHEN NEEDED.
---

## Current state

| Area | Status |
|---|---|
| **#8 Phases A–D, F–G** | Complete — workspaceless boot, New Workspace UI with `hpc:ccam` option, `POST /api/workspaces/create`, `compute_backend` persisted in `workspace.yaml`, catalog backend chip |
| **#8 Phase E** (Singularity.def) | Complete — `Singularity.def.j2` + `hpc.env.example` in `pbg-template/template/`; `_maybe_remove_singularity` / `_check_singularity_for_hpc` in `workspace_create.py` |
| **#10 Phase 1** (pydantic-settings + gitignore) | Complete — `pydantic-settings>=2.3` in `pyproject.toml`; `.env`, `*.hpc.env` in `.gitignore` |
| **#10 Phase 2** (`hpc_settings.py`) | Complete — `HpcSettings`, `load_hpc_settings`, `get_hpc_settings` (lru_cache), `require_configured`, `HpcNotConfiguredError` |
| **#10 Phase 4** (`hpc_dispatch.py`) | Complete — `open_socket`, `_ssh` (ControlMaster + fallback), `check_connectivity`, `check_slurm`, `rsync_workspace`, `submit_build_job`, `submit_run_job`, `get_job_status`, `cancel_job`, `build_run_script`, `build_image_script` |
| **#10 Phase 5** (server routes) | Complete — `_dispatch_hpc_get/post`, all `/api/hpc/<backend>/*` handlers, `/api/compute-backends`, `/hpc/<backend>` page |
| **#10 Phase 6** (UI) | Complete — `hpc_dashboard.html.j2` (connectivity chip, build panel, cluster status, run form + job history), `hpc-dispatch.js` |
| **#10 Phase 7** (tests) | Complete — `test_hpc_settings.py`, `test_hpc_dispatch.py`, `test_hpc_ui_integration.py`, `test_template_singularity_parity.py`; 903 passed, 0 failures |
| **#15** (post-merge test failures) | Complete — 903 passed, 0 failures (2026-05-26) |
| **Live CCAM dispatch** | **Not yet validated** — all code is in place; first real dispatch requires credentials + VPN (see checklist below) |

---

## First live dispatch checklist

Everything needed to run a real workflow on CCAM from the dashboard:

1. **Create an HPC workspace** via the dashboard "New Workspace" dialog; choose `hpc:ccam` as the compute backend. This scaffolds `Singularity.def` from `pbg-template`.
2. **Fill in credentials** — copy `workspace/.pbg/hpc.env.example` → `workspace/.pbg/hpc.env` and set:
   - `SLURM_SUBMIT_HOST=login.hpc.cam.uchc.edu` (external; `haproxy-ssh` only reachable in-cluster)
   - `SLURM_SUBMIT_USER=<your-hpc-username>`
   - `SLURM_SUBMIT_KEY_PATH=~/.ssh/id_ed25519_hpc` (or whichever key is authorized on CCAM)
   - `SLURM_SUBMIT_KNOWN_HOSTS=<path>` — generate: `ssh-keyscan login.hpc.cam.uchc.edu > ~/.pbg/hpc/ccam_known_hosts`
   - `SLURM_PARTITION=<ccam-partition>`, `SLURM_QOS=<ccam-qos>`
   - `HPC_REPO_BASE_PATH=<remote-path-for-workspace-sync>`
   - Optionally: `HPC_IMAGE_BASE_PATH`, `HPC_SIM_BASE_PATH`, `HPC_LOG_BASE_PATH`
3. **Connect to VPN** — CCAM login node is not reachable without the org VPN.
4. **Open the HPC tab** in the dashboard — the connectivity chip should turn green (SSH reachable + apptainer found).
5. **Build the Singularity image** — click "Build Now"; this rsyncs the workspace and runs `sbatch build-<id>.sh` on the cluster. Poll status until `COMPLETED`.
6. **Submit a run** — enter a command (e.g. `vivarium-dashboard run-composite ...`), set resources, click "Submit to SLURM". Poll status; cancel via the job history table if needed.

---

## GHCR troubleshooting — "is the image actually public?"

> **TL;DR:** `curl -sI https://ghcr.io/v2/<org>/<repo>/manifests/<tag>` returning `401` is **not** a visibility verdict — it's the Docker Registry HTTP API v2 auth challenge, which every GHCR image (public or private) returns to unauthenticated clients. Always complete the bearer-token dance before drawing any conclusion.

### The right probes

```bash
# 1. Anonymous bearer token + retry. If this returns 200, the image is public.
TOKEN=$(curl -s "https://ghcr.io/token?service=ghcr.io&scope=repository:<org>/<repo>:pull" | jq -r .token)
curl -sI -H "Authorization: Bearer $TOKEN" \
  "https://ghcr.io/v2/<org>/<repo>/manifests/sha-<short>"
# Expected outcomes:
#   HTTP/2 200  → image is public AND this tag exists
#   HTTP/2 404  → image is public, tag does not exist (try a different tag)
#   HTTP/2 401  → image is actually private (or token request itself failed)

# 2. Or: confirm via docker pull while logged OUT of ghcr.
docker logout ghcr.io
docker pull ghcr.io/<org>/<repo>:sha-<short>
```

### Which tags actually exist on which branches

The workflow's tag matrix (`.github/workflows/build-and-push.yml`, metadata-action):

| Tag | Pushed when |
|---|---|
| `sha-<short>` | every push, every branch — **the canonical pin for reproducibility** |
| `<branch-name>` (slashes → hyphens) | every push, every branch |
| `:latest` | **only on default-branch pushes** (`enable={{is_default_branch}}`) |

So `:latest` will 404 against an anonymous bearer token whenever the only recent pushes are to feature branches. That is expected — probe `sha-<short>` or the branch tag instead.

```bash
# List the tags GHCR actually has for this package:
gh api '/orgs/<org>/packages/container/<repo>/versions' \
  --jq '.[] | .metadata.container.tags' | head -20
```

### What auto-sync does and doesn't do

The `Sync GHCR package visibility to repo visibility` step in `build-and-push.yml`:

- **Org-owned packages:** calls `PATCH /orgs/{org}/packages/.../visibility` with the workflow's default `GITHUB_TOKEN` and the `packages: write` permission. Works out of the box.
- **User-owned packages:** the symmetric `/user/packages/.../visibility` endpoint requires a PAT with `write:packages`. The workflow emits a `::notice::` pointing to the one-time UI link (Package settings → "Manage Actions access" → connect the repo) and stops. Optionally users can set a `GHCR_PAT` secret to skip the manual step.

In neither case does the step *check* the result with `curl -sI` — because, again, that probe is not a reliable visibility verdict.

### "Inherited Access" is unrelated

The "Inherited Access" panel at
`https://github.com/orgs/<org>/packages/container/<repo>/settings` maps
**org-member roles** (Read/Write/Admin) from the source repo onto the package.
It controls *authenticated* org users — it has no effect on anonymous public
pulls. Leave it as-is when debugging public-visibility issues.

The production HPC environment is defined in
`~/sms/sms-api/kustomize/config/sms-api-rke/`. Key values extracted here
**for reference only** — they must never be pasted into vivarium-dashboard
source or tests.

| Setting | Variable name | Notes |
|---|---|---|
| SLURM submit host | `SLURM_SUBMIT_HOST` | `haproxy-ssh` (in-cluster k8s DNS); external: `login.hpc.cam.uchc.edu` or direct `mantis-sub-{3..10}.cam.uchc.edu` |
| SLURM user | `SLURM_SUBMIT_USER` | service account; user needs SSH key authorised |
| SSH key | `SLURM_SUBMIT_KEY_PATH` | mounted from k8s secret in prod; path to `~/.ssh/id_*` locally |
| SSH known hosts | `SLURM_SUBMIT_KNOWN_HOSTS` | path to a known_hosts file generated via `ssh-keyscan` — **never inline the fingerprint** |
| Partition | `SLURM_PARTITION` | non-default; must appear in every sbatch script |
| QOS | `SLURM_QOS` | same requirement |
| Image base | `HPC_IMAGE_BASE_PATH` | remote path where `.sif` files are stored |
| Sim base | `HPC_SIM_BASE_PATH` | remote path for simulation outputs |
| Log base | `HPC_LOG_BASE_PATH` | remote path for sbatch `--output` logs |
| Repo base | `HPC_REPO_BASE_PATH` | remote path where workspace rsync targets land |

HAProxy (in-cluster) round-robins SSH port 22 to the submit nodes. A
dashboard running outside the k8s cluster cannot reach `haproxy-ssh` by
that name and must use the externally resolvable hostname instead.

The `sms-api` uses `asyncssh`/`SSHService` inside async FastAPI. The
`vivarium-dashboard` is synchronous stdlib — dispatch uses `subprocess` +
SSH ControlMaster sockets (no extra deps) mirroring the same logical flow.

---

## Execution protocol — ordered phases

Work through these in sequence. Each phase ends with a commit hand-off
(per the CLAUDE.md cadence rule). Do not begin the next phase until the
user has committed and optionally pushed the previous one.

---

### Phase 0 — Fix post-merge test failures (Todo #15)

**Prerequisite gate.** Three independent regressions must be green before
layering new code on top. Run in parallel; single commit covers all three.

#### 15-A — `pbg-template` schema: `const` too strict

Schema validation rejects valid v4 study / v2 investigation YAML because a
`const` constraint is too narrow. Relax to `enum` or remove the const
assertion. Locate in `server.py` or `lib/` wherever schema YAML is applied.

#### 15-B — DELETE `/api/simulation-run`: run still in listing after delete

Record is deleted from the DB but the listing query doesn't filter it out.
Fix the SQL / ORM query so deleted rows are excluded.

#### 15-C — `POST /api/investigation-create-from-composite`: no `spec.yaml`

Handler creates the investigation object in memory but never writes
`spec.yaml` to disk. Add the write step.

**Verification:**

```bash
uv run python -m pytest --tb=no -q
# target: 0 failures (791 passed, 9 skipped, 2 xfailed)
```

**Commit:** `fix(tests): resolve post-merge 15-A/B/C failures`

---

### Phase 1 — `pydantic-settings` dependency + gitignore hardening

#### 1a. Add dependency

In `pyproject.toml` `[project.dependencies]`:

```toml
"pydantic-settings>=2.3",
```

`pydantic` is already pulled in via `pydantic-ai`. `pydantic-settings` is
the official first-party extension; no transitive surprises.

#### 1b. Extend `.gitignore`

```gitignore
# Environment / secrets — never commit real values
.env
.env.*
!.env.example
!*.env.example
*.hpc.env
```

**Commit:** `chore(deps): add pydantic-settings; harden .gitignore for env files`

---

### Phase 2 — `hpc_settings.py` — Pydantic Settings config layer

New file: `vivarium_dashboard/lib/hpc_settings.py`

All HPC configuration is workspace-scoped. Settings are loaded from
`workspace/.pbg/hpc.env` (gitignored) via Pydantic Settings, falling back
to process environment variables. The `get_hpc_settings()` factory is
`@lru_cache`'d per workspace path string — identical pattern to
`sms_api.config.get_settings()`.

```python
"""Workspace-scoped HPC / SLURM configuration.

Loaded from workspace/.pbg/hpc.env (gitignored — never committed).
All sensitive fields default to "" so the dashboard boots cleanly on
workspaces that have no HPC configured; dispatch code validates non-empty
before opening any SSH connection.

Pattern mirrors ~/sms/sms-api/sms_api/config.py.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class HpcSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    # SSH connection — all sensitive, no real defaults
    slurm_submit_host: str = ""       # login node hostname or IP
    slurm_submit_user: str = ""       # SSH username on the HPC
    slurm_submit_key_path: str = ""   # path to private key file
    slurm_submit_known_hosts: str = ""  # path to known_hosts file for this host
                                        # generate: ssh-keyscan <host> > that file

    # SLURM job submission
    slurm_partition: str = ""
    slurm_qos: str = ""
    slurm_node_list: str = ""         # optional --nodelist constraint

    # Remote filesystem base paths (all on the HPC, sensitive)
    hpc_image_base_path: str = ""     # where .sif images are stored
    hpc_sim_base_path: str = ""       # where simulation outputs land
    hpc_log_base_path: str = ""       # where sbatch --output logs go
    hpc_repo_base_path: str = ""      # rsync target for workspace files

    # Non-sensitive tunables with safe defaults
    singularity_cmd: str = "apptainer"   # or "singularity"
    timeout_connect: int = 5             # SSH ConnectTimeout seconds


def load_hpc_settings(workspace: Path) -> HpcSettings:
    env_file = workspace / ".pbg" / "hpc.env"
    if env_file.is_file():
        return HpcSettings(_env_file=str(env_file))
    return HpcSettings()   # reads from process environment


@lru_cache(maxsize=8)
def get_hpc_settings(workspace_str: str) -> HpcSettings:
    return load_hpc_settings(Path(workspace_str))
```

**No real values appear anywhere in this file.** The `slurm_submit_host`
field has no default because there is no safe universal hostname to provide.

#### Validation gate shared by all dispatch functions

```python
class HpcNotConfiguredError(RuntimeError):
    def __init__(self, missing: list[str]):
        self.missing = missing
        super().__init__(
            f"HPC settings not configured: {missing}. "
            "Fill in workspace/.pbg/hpc.env — see .pbg/hpc.env.example"
        )

_REQUIRED_FIELDS = (
    "slurm_submit_host", "slurm_submit_user",
    "slurm_partition", "hpc_repo_base_path",
)

def require_configured(settings: HpcSettings) -> None:
    missing = [f for f in _REQUIRED_FIELDS if not getattr(settings, f)]
    if missing:
        raise HpcNotConfiguredError(missing)
```

Route handlers catch `HpcNotConfiguredError` and return:

```json
{
  "error": "hpc_not_configured",
  "hint": "Fill in workspace/.pbg/hpc.env — see .pbg/hpc.env.example",
  "missing_fields": ["slurm_submit_host", "slurm_partition"]
}
```

HTTP 503 — actionable, no sensitive data in the response.

**Test:** `tests/test_hpc_settings.py` — unit tests using a `tmp_path`
fixture to write a fake `.pbg/hpc.env`; verify field loading, env-var
override, missing file → empty defaults, `require_configured` raises on
empty fields. No real values in any test.

**Commit:** `feat(hpc): hpc_settings.py — Pydantic Settings config layer`

---

### Phase 3 — `Singularity.def.j2` in pbg-template (Todo #8 Phase E)

**Coupled repo:** `../pbg-template` (branch `dynamic-workspace-images`).

#### 3a. Add `Singularity.def.j2` to `pbg-template/template/`

The Singularity definition must be 1:1 with the workspace `Dockerfile`.
Translate Docker directives to Singularity equivalents:

```singularity
Bootstrap: docker
From: {{ base_image }}   {# extracted from Dockerfile FROM line #}

%post
    apt-get update -qq
    apt-get install -y --no-install-recommends git rsync
    rm -rf /var/lib/apt/lists/*
    pip install --no-cache-dir uv

%files
    pyproject.toml /app/pyproject.toml
    vivarium_dashboard /app/vivarium_dashboard

%environment
    export PATH=/app/.venv/bin:$PATH
    export PYTHONPATH=/app:$PYTHONPATH

%runscript
    exec uv run vivarium-dashboard serve \
        --workspace /app --host 0.0.0.0 --port 9863 "$@"
```

The `.j2` uses Jinja2 template syntax so `template-init.sh` can substitute
the base image tag. If the template is rendered at scaffold time by the
dashboard (Phase 3b), Python's `string.Template` is used instead (no Jinja2
import in `lib/`).

#### 3b. Render `Singularity.def` at workspace scaffold time

In `vivarium_dashboard/lib/workspace_create.py`, update `create_workspace()`
to render `Singularity.def.j2` → `Singularity.def` when `backend.startswith("hpc:")`.
Use `string.Template` (stdlib). The source template is found via
`_find_pbg_template()` (already exists).

`_maybe_remove_singularity()` already no-ops for `hpc:` backends and deletes
the file for `local` — no change needed there once the template exists.
`_check_singularity_for_hpc()` will return `None` (no warning) once the file
is present.

#### 3c. Add `.pbg/hpc.env.example` to `pbg-template/template/.pbg/`

```bash
# HPC SSH + SLURM configuration — workspace-scoped.
# Copy to hpc.env and fill in your values.
# NEVER commit hpc.env — it contains paths to private keys.
# Generate known_hosts: ssh-keyscan <your-login-node> > hpc_known_hosts

SLURM_SUBMIT_HOST=
SLURM_SUBMIT_USER=
SLURM_SUBMIT_KEY_PATH=
SLURM_SUBMIT_KNOWN_HOSTS=

SLURM_PARTITION=
SLURM_QOS=

HPC_IMAGE_BASE_PATH=
HPC_SIM_BASE_PATH=
HPC_LOG_BASE_PATH=
HPC_REPO_BASE_PATH=

SINGULARITY_CMD=apptainer
```

#### 3d. Add `hpc.env` to `pbg-template/template/.gitignore`

```gitignore
.pbg/hpc.env
.pbg/hpc_known_hosts
```

#### 3e. Test: `tests/test_template_singularity_parity.py`

Verify the rendered `Singularity.def` matches the `Dockerfile` on base image
tag, Python package install command, and entrypoint/runscript.

**Commits (two repos):**
```
git -C ../pbg-template commit -m "feat(template): Singularity.def.j2, hpc.env.example, .gitignore hpc.env"
git commit -m "feat(workspace-create): render Singularity.def from .j2 for hpc: backends"
```

---

### Phase 4 — `hpc_dispatch.py` — SSH + SLURM execution layer (Todo #10 Phases A–C)

New file: `vivarium_dashboard/lib/hpc_dispatch.py`

#### Design: stdlib subprocess + ControlMaster sockets

No new runtime dependencies. SSH ControlMaster provides connection reuse
without `paramiko` or `asyncssh`. Pattern:

```
# First call — open persistent control socket (non-blocking):
ssh -N -f -M -S ~/.pbg/hpc/sockets/<backend>.sock \
    -o ControlPersist=300 \
    -o ConnectTimeout=<timeout> \
    -i <key_path> \
    [-o UserKnownHostsFile=<known_hosts>] \
    <user>@<host>

# Subsequent commands reuse the socket:
ssh -S ~/.pbg/hpc/sockets/<backend>.sock <user>@<host> <cmd>

# Fallback if socket is stale (exit 255):
ssh -o BatchMode=yes -o ConnectTimeout=<timeout> \
    -i <key_path> <user>@<host> <cmd>
```

Socket directory: `~/.pbg/hpc/sockets/` (created on first use).
Log directory: `workspace/.pbg/hpc/` (created on first use).

#### Public dispatch functions

```python
def check_connectivity(settings: HpcSettings) -> dict
    # ssh exit; which apptainer || which singularity
    # returns {"reachable": bool, "singularity_available": bool,
    #          "singularity_cmd": str | None, "message": str}

def check_slurm(settings: HpcSettings) -> dict
    # squeue -u $USER (running jobs) + sinfo (partitions)
    # returns {"partitions": [...], "jobs": [...], "error": str | None}

def rsync_workspace(settings: HpcSettings, local_ws: Path) -> None
    # rsync -az --delete --no-o --no-g --omit-dir-times
    #   --exclude=.venv/ --exclude=.git/ --exclude=__pycache__/
    #   --exclude=.pbg/runs/ --exclude=.pbg/state.json
    #   <local_ws>/ <user>@<host>:<hpc_repo_base>/<ws_name>/

def submit_build_job(settings: HpcSettings, local_ws: Path) -> dict
    # rsync_workspace, then sbatch the build script
    # returns {"build_id": str, "slurm_job_id": int, "log_path": str}

def get_job_status(settings: HpcSettings, slurm_job_id: int) -> dict
    # squeue -j <id> → scontrol fallback (per sms-api pattern; sacct deprecated)
    # returns {"job_id": int, "state": str, "reason": str | None,
    #          "start_time": str | None, "elapsed": str | None}

def cancel_job(settings: HpcSettings, slurm_job_id: int) -> None
    # scancel <id>

def submit_run_job(
    settings: HpcSettings, local_ws: Path,
    command: str, resources: dict,
) -> dict
    # writes sbatch script, sbatch submits it, returns job_id
    # returns {"slurm_job_id": int, "log_path": str}
```

#### sbatch script structure

Every generated script uses the partition and QOS from `HpcSettings`:

```bash
#!/bin/bash
#SBATCH --job-name=vivarium-<ws>-<run_id>
#SBATCH --partition=<slurm_partition>
#SBATCH --qos=<slurm_qos>
#SBATCH --cpus-per-task=<cpus>
#SBATCH --mem=<mem_gb>G
#SBATCH --time=<time_min>
#SBATCH --output=<hpc_log_base>/<ws>/%x-%j.out
#SBATCH --error=<hpc_log_base>/<ws>/%x-%j.err

set -euo pipefail
# try apptainer first, fall back to singularity
SIF_CMD=$(command -v apptainer 2>/dev/null || command -v singularity)
cd <hpc_repo_base>/<ws_name>
"$SIF_CMD" exec \
    --bind <hpc_repo_base>/<ws_name>/results:/app/results \
    <ws_name>.sif \
    <command>
```

#### Error masking

All SSH stdout/stderr captured before logging is passed through a `_mask()`
function that redacts the key path value and any string matching the
`slurm_submit_user` (in case it appears in error messages). Mirrors
`github_auth.mask_token()`.

**Test:** `tests/test_hpc_dispatch.py` — `monkeypatch` on `subprocess.run`
and `subprocess.Popen`. Covers: connectivity check (reachable / unreachable /
singularity missing), rsync argument shape, sbatch script content (partition
/ QOS / apptainer fallback line), `squeue` → `scontrol` fallback, `scancel`,
ControlMaster socket path, log file written to `.pbg/hpc/`. No real values
in any test fixture or parametrize call.

**Commit:** `feat(hpc): hpc_dispatch.py — SSH + SLURM execution layer`

---

### Phase 5 — Server routes (Todo #10 Phases A-2, B, C)

~11 new handler methods in `server.py` following the existing `_get_` /
`_post_` naming pattern. Route dispatch: add a prefix check
`path.startswith("/api/hpc/")` and `path.startswith("/hpc/")` before the
existing `_GET_ROUTE_MAP` / `_POST_ROUTE_MAP` lookups; parse `backend` from
the path segment and thread `WORKSPACE` into `get_hpc_settings()`.

#### New routes

| Method | Path | Handler | Notes |
|---|---|---|---|
| GET | `/api/hpc/<backend>/status` | `_get_hpc_status` | SSH reachability + singularity check |
| GET | `/api/hpc/<backend>/slurm` | `_get_hpc_slurm` | `squeue` + `sinfo` |
| POST | `/api/hpc/<backend>/build` | `_post_hpc_build` | rsync + sbatch build job |
| GET | `/api/hpc/<backend>/build/<id>` | `_get_hpc_build_status` | poll build job |
| GET | `/api/hpc/<backend>/build/<id>/log` | `_get_hpc_build_log` | tail build log |
| POST | `/api/hpc/<backend>/run` | `_post_hpc_run` | submit SLURM run job |
| GET | `/api/hpc/<backend>/run/<job_id>` | `_get_hpc_run_status` | `squeue` → `scontrol` |
| POST | `/api/hpc/<backend>/run/<job_id>/cancel` | `_post_hpc_run_cancel` | `scancel` |
| GET | `/api/hpc/<backend>/runs` | `_get_hpc_runs` | recent jobs from `.pbg/hpc/jobs.db` |
| GET | `/api/compute-backends` | `_get_compute_backends` | dynamic backend list |
| GET | `/hpc/<backend>` | `_get_hpc_page` | renders `hpc_dashboard.html.j2` |

All handlers catch `HpcNotConfiguredError` → 503 with structured JSON.
All handlers catch general exceptions → 500 with masked error detail.

**Commit:** `feat(serve): /api/hpc/* routes and /api/compute-backends`

---

### Phase 6 — UI (Todo #10 Phase D)

#### `vivarium_dashboard/templates/hpc_dashboard.html.j2`

Full-page template rendered by `_get_hpc_page()`. Four sections:

1. **Connectivity chip** — `GET /api/hpc/<backend>/status` on page load;
   green "SSH reachable + apptainer found" or red "SSH unreachable" chip.
2. **Build panel** — "Build Now" button → `POST .../build`; log viewer
   (auto-scroll, collapsible); polls `GET .../build/<id>` every 5 s.
3. **Cluster status** — `GET .../slurm`; partition list, running job count.
4. **Run form + job history** — command input, CPU / memory / time sliders,
   "Submit to SLURM" → `POST .../run`; job history table polling
   `GET .../run/<id>` every 10 s; cancel button → `POST .../run/<id>/cancel`.

#### `vivarium_dashboard/static/hpc-dispatch.js`

`initHpcPage(backend)` called on DOMContentLoaded. Uses existing `jget` /
`jpost` helpers. Implements:
- Connectivity poll on load (no interval — one-shot)
- Build log auto-scroll via 5 s `setInterval`
- Run status table refresh via 10 s `setInterval`; clears on all jobs terminal
- Cancel confirm dialog before `POST .../cancel`

#### `vivarium_dashboard/static/workspace-switcher.js`

Replace the hardcoded `const BACKENDS = ["local", "hpc:ccam"]` array with:

```javascript
async function loadBackends() {
    const data = await jget('/api/compute-backends');
    return data.backends ?? [{ id: "local", label: "Local machine" }];
}
```

The `GET /api/compute-backends` response shape:

```json
{
  "backends": [
    {"id": "local",    "label": "Local machine",  "description": "Run on this host"},
    {"id": "hpc:ccam", "label": "HPC: CCAM",       "description": "CCAM cluster via SLURM + Apptainer/Singularity"}
  ]
}
```

Reachability is **not** included in this response (avoids an SSH round-trip
on every modal open). The `GET /api/hpc/<backend>/status` endpoint provides
reachability on-demand from the HPC tab.

**Test:** `tests/test_hpc_ui_integration.py` — `dashboard_client` fixture;
verifies: HPC tab renders when `compute_backend` is `hpc:*`, HPC tab absent
for `local`, `/api/compute-backends` returns both backends, misconfigured
workspace returns 503 with `hpc_not_configured` error.

**Commit:**
```
git commit -m "feat(ui): hpc_dashboard.html.j2 + hpc-dispatch.js + workspace-switcher fetch"
```

---

### Phase 7 — All tests + acceptance criteria

```bash
uv run python -m pytest tests/test_hpc_settings.py        # Phase 2
uv run python -m pytest tests/test_hpc_dispatch.py        # Phase 4
uv run python -m pytest tests/test_hpc_ui_integration.py  # Phase 6
uv run python -m pytest tests/test_workspace_create.py    # Phase 3 regression
uv run python -m pytest --tb=no -q                        # full suite, 0 failures
```

**Final acceptance checklist (from todo.md #10):**

- [ ] `hpc:ccam` workspace shows an "HPC" tab with connectivity status, build controls, cluster status, run form, job history
- [ ] `GET /api/hpc/ccam/status` returns SSH reachability + singularity/apptainer detection
- [ ] `POST /api/hpc/ccam/build` rsyncs workspace, submits sbatch build job, returns build ID
- [ ] Build status pollable via `GET /api/hpc/ccam/build/<id>`
- [ ] Build log accessible via `GET /api/hpc/ccam/build/<id>/log`
- [ ] `POST /api/hpc/ccam/run` submits SLURM job, returns job ID
- [ ] `GET /api/hpc/ccam/run/<job_id>` returns job state (`squeue` → `scontrol` fallback)
- [ ] `POST /api/hpc/ccam/run/<job_id>/cancel` runs `scancel`
- [ ] `GET /api/compute-backends` returns backend list; modal dropdown uses it
- [ ] SSH ControlMaster socket used for connection reuse; falls back to one-shot on stale socket
- [ ] No hostnames, usernames, key paths, or fingerprints in any log output or API response
- [ ] `local` workspaces show no HPC tab; existing Run flow unchanged
- [ ] Unconfigured HPC workspace returns HTTP 503 `hpc_not_configured` with `missing_fields`
- [ ] All Phase 7 tests pass

**Commit:** `test(hpc): test_hpc_settings, test_hpc_dispatch, test_hpc_ui_integration`

---

## Commit sequence summary

```
Phase 0:  fix(tests): resolve post-merge 15-A/B/C failures
Phase 1:  chore(deps): add pydantic-settings; harden .gitignore for env files
Phase 2:  feat(hpc): hpc_settings.py — Pydantic Settings config layer + tests
Phase 3a: git -C ../pbg-template commit -m "feat(template): Singularity.def.j2, hpc.env.example, .gitignore hpc.env"
Phase 3b: feat(workspace-create): render Singularity.def from .j2 for hpc: backends
Phase 4:  feat(hpc): hpc_dispatch.py — SSH + SLURM execution layer + tests
Phase 5:  feat(serve): /api/hpc/* routes and /api/compute-backends
Phase 6:  feat(ui): hpc_dashboard.html.j2 + hpc-dispatch.js + workspace-switcher fetch
Phase 7:  test(hpc): test_hpc_settings, test_hpc_dispatch, test_hpc_ui_integration
```

---

## Risks and open questions

**`--fakeroot` not available on CCAM compute nodes for `svc_vivarium`.**
`/etc/subuid` has no entry for the service account on any node (confirmed
2026-05-26). `singularity build --fakeroot` will always fail with
`FATAL: could not use fakeroot: no mapping entry found in /etc/subuid`.
**Resolution:** build the Docker image locally via `docker build`, export a
tar with `docker save`, rsync the tar to the cluster, and build the `.sif`
on the cluster using `singularity build docker-archive://...` — no fakeroot
needed for OCI-to-SIF conversion. Implemented in `hpc_dispatch.py`:
`_build_docker_tar()` + `rsync_docker_tar()` + updated `build_image_script()`.

**SSH key management is out-of-band.** The dashboard cannot prompt for a
passphrase or add a key to the agent. If `ssh-add -l` returns nothing and no
key path is configured, the connectivity check fails with
`{"error": "ssh_auth_failed", "hint": "Run: ssh-add ~/.ssh/id_ed25519_hpc"}`.

**`haproxy-ssh` is not reachable from outside the k8s cluster.** Use
`login.hpc.cam.uchc.edu` or direct `mantis-sub-X.cam.uchc.edu` in
`SLURM_SUBMIT_HOST` when running the dashboard locally. A VPN may be
required depending on network configuration.

**Singularity vs Apptainer.** The sbatch template tries `apptainer` first,
falls back to `singularity`. `HpcSettings.singularity_cmd` can override to
force one or the other.

**Concurrent builds.** If two dashboard instances target the same remote
workspace, concurrent `singularity build` on the same `.sif` collides. Use
`mkdir .pbg-build-lock` as a remote mutex; 30 s timeout; return
`{"error": "build_in_progress"}` if lock exists.

**Multi-user workspace.** The `hpc_repo_base_path` is per-workspace;
different users on the same HPC will have their own subtrees as long as they
configure different base paths.

**No Pydantic in the current lib/.** `pydantic-settings` is the only new
dep introduced by the HPC integration path. It adds ~200 kB to the install.
If that is unacceptable, replace `BaseSettings` with a thin stdlib
`dataclasses` + `os.environ` pattern — but the sms-api precedent argues for
Pydantic Settings as the house style.

---

## Spec-driven HPC dispatch — `report_generator` + `core_bootstrap`

> Added 2026-06-04 as part of todo #22 (generalize colony-special-cased
> dispatch). The dashboard is the **consumer** of pbg-compliance; the
> workspace + `/pbg-expert ./` is the **producer**. The dashboard reads
> these spec fields and resolves them dynamically — it never imports
> workspace-specific packages directly.

### Producer/consumer split

When an investigation runs on HPC, two pieces of behaviour are
workspace-specific:

1. **What command to invoke as a per-task report generator** (e.g.
   `colony_report.py` for v2ecoli's colony composite; some other
   script for some other workspace's composite).
2. **How to bootstrap a `process_bigraph` `core` with the right type
   registrations for the workspace's composites** (e.g.
   `ECOLI_TYPES + EcoliWCM` for v2ecoli; something else for another
   workspace).

Neither is the dashboard's concern. Both are declared by the
workspace via spec fields the dashboard resolves at dispatch time.

### `report_generator` — spec field

Optional. Declared per-entry on `simulation_set[*].report_generator`,
or as a top-level fallback on `study.yaml:report_generator`. Per-entry
overrides top-level (matches the `pipeline_gate.prerequisites` shape).

```yaml
# study.yaml — top-level default applies to every entry without its own block
report_generator:
  script: reports/colony_report.py     # workspace-relative; bound via /workspace
  args:                                # rendered per task via str.format()
    duration: "{steps_clamped:5}"      # clamp helper: min(max(steps,1), N)
    seed: "{overrides[seed]}"          # bracket form for dict access
    n-adder: "{overrides[n_cells]}"    # missing keys raise 400 at dispatch
    out: "/app/out/colony/{run_id}.html"
  output_dir: out/colony               # populates Visualizations-tab discovery
  pullback_glob: "*.html"              # rsync-back pattern (optional; defaults to *)

simulation_set:
  - name: colonies-01
    base_model: v2ecoli.composites.colony.colony
    perturbation: { n_cells: 4 }
    # No report_generator → inherits top-level

  - name: nsweep-n8
    base_model: v2ecoli.composites.colony.colony
    perturbation: { n_cells: 8 }
    report_generator:                  # per-entry override
      script: reports/colony_report.py
      args:
        duration: "10"                 # static string is fine
        seed: "{overrides[seed]}"
        n-adder: "{overrides[n_cells]}"
        out: "/app/out/colony/n8_{run_id}.html"
```

**Templating syntax: Python `str.format()`**. Substitution context
populated by the dashboard at dispatch time:

| Key | Source | Example |
|-----|--------|---------|
| `{run_id}` | dashboard-generated run id | `colony__seed-0__n_cells-4__abc123` |
| `{overrides[KEY]}` | per-task `overrides` dict (built from `perturbation` + `seeds`) | `{overrides[seed]}` → `0` |
| `{steps}` | per-task resolved step count | `300` |
| `{steps_clamped:N}` | `min(max(steps, 1), N)` — bounded duration helper | `{steps_clamped:5}` → `5` |

Missing template keys cause the dispatch to fail with a 400 and a
human-readable error naming the missing key (no SLURM submission).

**Server resolution:** in `_post_investigation_run_hpc`, the absence of
a `report_generator` (top-level **and** per-entry) means "use the
default `run_investigation_task.py` runner path" — i.e. the existing
b64-payload dispatch. When at least one task has a resolved generator,
**all** tasks must resolve to one (mixing isn't allowed — return 400).

**UI gating:** the `study-detail.html` "generate report" checkbox is
hidden when no `report_generator` is declared anywhere on the study.
When declared, the checkbox is shown with label "generate report" (no
"colony" reference).

### `core_bootstrap` — spec field

Optional. Declared as `study.yaml:core_bootstrap` (top-level only —
per-entry overrides not supported here; a workspace has one core
shape). Value is a Python dotted path of the form `module:function` or
`module.function` (the trailing component is treated as the attribute).

```yaml
# study.yaml
core_bootstrap: pbg_colonies_demo.hpc:bootstrap_core
```

The named function takes **no arguments** and returns a configured
`process_bigraph` `Core` with all type/link registrations the
workspace's composites need. The runner imports + calls it via:

```python
mod_path, fn = core_bootstrap.rsplit(":", 1) if ":" in core_bootstrap \
    else core_bootstrap.rsplit(".", 1)
core = getattr(importlib.import_module(mod_path), fn)()
```

**No workspace-name fallback in `run_investigation_task.py`.** If
`core_bootstrap` is absent in the payload, the runner falls back in
order to:

1. `{pkg}.core.build_core()` where `pkg` comes from
   `workspace.yaml:package_path`,
2. `process_bigraph.core` default.

It **does not** try `viva_munk`, `v2ecoli`, or any other
workspace-specific import. Workspaces that need a custom core
declare one.

### Where these declarations come from

The standardized workflow (see memory
`project_v2ecoli_as_pbg_instance.md`) is `/pbg-expert ./`: an
agentic skill in `pbg-superpowers` that emits the declarations the
dashboard expects, plus any per-workspace bootstrap module (e.g.
`pbg_<wsname>/hpc.py:bootstrap_core()`).

Hand-authoring is also supported — the spec format is documented
here and validated by the workspace's `.pbg/schemas/`. Any workspace
that satisfies the schema works, regardless of how the YAML was
produced.

### Backwards compatibility

Workspaces without `report_generator` or `core_bootstrap` continue
to dispatch via the existing runner path with the default core.
The colony case **requires** the workspace to declare both fields
post-#22 — the v2ecoli/viva_munk hardcoded fallback is removed.

