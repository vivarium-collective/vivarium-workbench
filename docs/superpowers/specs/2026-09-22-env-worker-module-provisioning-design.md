# Env-worker module provisioning — design

**Status:** approved (Eran, 2026-09-22 — option B, runtime-install feature, no bake).

## Problem

Catalog UI-install lands a module in the **workbench pod's PVC venv** (`/workspace/.venv`)
and records it in `workspace.yaml` `imports:`. Composites run in a **separate env-worker
pod** that runs a fixed baked image, mounts **no PVC** (RWO), and does **no runtime
install** — so a UI-installed module is invisible to the simulator. Composites resolve in
the UI (file-based, via `find_composite_path`) but fail to *run* (their processes/
generators never register in the worker). This blocks the UI-install workflow for any
non-baked model (e.g. viva-tumor-tcell).

## Approach

The env-worker cannot read the PVC, so the **workbench pushes** install-specs to the
worker; the worker `pip install`s them into a **writable dir it already has** (`/scratch`,
an existing emptyDir) with `--target`, prepends that dir to `sys.path`, and *then* builds
its registry — so discovery sees the packages.

Specs are the ones catalog-install **already records** in `workspace.yaml imports:`:
- `mode: pypi` → `pip install <pypi_name>`
- `mode: reference` (git) → `pip install "git+<source>@<ref>"` (network install — NOT the
  `external/<name>` editable path, which does not exist in the worker pod)

## Components (mostly vivarium-workbench)

1. **Worker install (`env_worker.py`)** — a `_provision_modules(specs)` helper that installs
   each spec into `PROVISION_TARGET` (default `/scratch/env-worker-site`, overridable via
   `VIVARIUM_ENV_WORKER_SITE`), prepends it to `sys.path`, and returns per-module results.
   Exposed as an `install_modules` JSON-RPC method in the `_handle` ladder, and invoked
   once at worker startup path.
2. **Spec derivation + delivery (`env_worker_provision.py` + `env_worker_pool.py`)** — read
   `workspace.yaml imports:` → list of `{name, spec}` install specs; call
   `worker.install_modules(specs)` once on cold `_acquire`, before the worker is cached.
3. **Status surface (`module_import_doctor.py` / `catalog.py`)** — an `env_worker` availability
   field per module so the Catalog tab can show "in env-worker ✓ / not provisioned /
   failed".
4. **Catalog tab UI (`walkthrough.js`)** — an "in env-worker" pill + a "Provision" action.
5. **viva-api** — no new volume needed (`/scratch` reused). Only external dependency:
   **egress from the worker pod to PyPI + github.com** (a cluster NetworkPolicy question for
   the RENCI deploy; does not affect local dev).

## Decisions

- Install to `/scratch/<site>` + `sys.path` prepend; never mutate base image site-packages.
- Install once per worker (workers are per-session, `ttl 3600s`); no cross-worker cache in v1.
- Auto-provision on cold acquire (MVP); Catalog "Provision" button = explicit re-trigger.
- `git+<url>@<ref>` for reference-mode; `pypi_name` for pypi-mode; skip anything not meant to
  import.

## Risks

- **Egress** (blocking on-cluster): worker → PyPI + github.com. Confirm with Phil.
- Cold-start install latency (mitigated: light pure-Python pkgs).
- Private modules would need a token (viva-tumor-tcell is public — fine now).

## Out of scope (v1)

Cross-worker install cache; lockfile closures beyond a module's own `uv.lock`; private-repo
auth; a persistent module-cache volume.
