# syntax=docker/dockerfile:1
#
# vivarium-workbench DEMO image (combined). Approach A from docs/REFACTOR-PLAN.md
# §2B: the workbench must import the workspace's package (`pbg_v2ecoli`, via
# build_core) IN-PROCESS to render, so it needs the *same* environment v2ecoli
# runs in. Rather than depend on a published v2ecoli image, this mirrors
# ../v2ecoli/Dockerfile to build v2ecoli's locked environment, then overlays THIS
# repo's workbench into that venv and serves.
#
# NOT baked (workbench renders; it does not run sims — those go to sms-api/Batch):
# the upstream vEcoli checkout + Cython, the AWS CLI, and the Ray-on-Batch
# entrypoint from v2ecoli's Dockerfile are intentionally omitted. Add V2E_VECOLI_DIR
# + the upstream checkout only if the demo renders an upstream-`vecoli` composite.
#
# Build (from this repo root):
#   docker build -t ghcr.io/vivarium-collective/vivarium-workbench:dev .
FROM python:3.12-bookworm

# uv (pinned binary) for fast, lock-faithful installs — same as v2ecoli.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Build toolchain for v2ecoli's vendored Cython extensions + git for the git-main deps.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Self-contained venv (real wheel copies, not links into the BuildKit cache mount).
ENV UV_LINK_MODE=copy

# ─── v2ecoli locked environment (mirrors ../v2ecoli/Dockerfile) ──────────────
# Cloned from git so the build context here is just the workbench repo.
ARG V2ECOLI_REF=main
RUN git clone https://github.com/vivarium-collective/v2ecoli.git /app/v2ecoli \
 && git -C /app/v2ecoli checkout "${V2ECOLI_REF}"
WORKDIR /app/v2ecoli
# v2ecoli pins requires-python == 3.12.12 exactly; let uv fetch that interpreter.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv python install 3.12.12
# Sync the FULL combined env — crucially WITHOUT v2ecoli's
# `--no-install-package vivarium-workbench` skip, so every workbench dependency
# (bigraph-loom, pbg-basic-processes, investigation-contracts, …) is resolved from
# v2ecoli's lock alongside v2ecoli itself. This is the guaranteed-compatible env.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --extra ray --extra emitters \
 || uv sync --no-dev --extra ray
ENV PATH="/app/v2ecoli/.venv/bin:${PATH}"

# ─── overlay THIS repo's workbench code (deps already satisfied above) ───────
# `--no-deps`: every dependency was installed by the sync above, so this only
# swaps the pinned git-main workbench for the exact code in this build context,
# and installs the `vivarium-workbench` / `vwb` console scripts into the venv.
WORKDIR /app/vivarium-workbench
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /app/v2ecoli/.venv/bin/python --no-deps .

# ─── overlay the Pathway Tools Omics-Viewer plugin (pbg-ptools) ───────────────
# The workbench discovers this at runtime via its pbg-* distribution scan and
# renders the PTools viewer (self-gated on ui.ptools_server_url). It MUST be
# installed explicitly: the `--no-deps` workbench install above does not pull the
# `ptools` extra, so without this line the viewer would be absent. `--no-deps`
# again because its deps (vivarium-workbench, pyyaml) are already in the venv.
ARG PBG_PTOOLS_REF=main
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /app/v2ecoli/.venv/bin/python --no-deps \
        "pbg-ptools @ git+https://github.com/vivarium-collective/pbg-ptools.git@${PBG_PTOOLS_REF}"

# ─── overlay bigraph-loom (embedded state-tree explorer, served at /loom-explore) ─
# `bigraph-loom` is a workbench-only dep (pyproject.toml:47) that is NOT declared in
# v2ecoli's lock, so the `uv sync` from v2ecoli's lockfile above never installs it.
# `lib/static_serving.resolve_loom_asset()` imports it lazily, so a missing module
# passes the build-time sanity import and only throws ModuleNotFoundError at runtime
# — the always-visible loom panel fires a loom-asset request for ANY composite, so
# the Composite Explorer 500s. Install it explicitly here (pin identical to
# pyproject.toml:47). `--no-deps` because its deps are already in the venv.
ARG BIGRAPH_LOOM_REF=main
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /app/v2ecoli/.venv/bin/python --no-deps \
        "bigraph-loom @ git+https://github.com/vivarium-collective/bigraph-loom.git@${BIGRAPH_LOOM_REF}"

# Sanity: the workspace package, the workbench, the viewer plugin, and the loom
# explorer all import in one interpreter (the plugin's top-level imports exercise
# the workbench too). bigraph_loom is added here so a regression fails the BUILD
# rather than shipping a silent runtime ModuleNotFoundError (see the overlay above).
RUN python -c "import pbg_v2ecoli, vivarium_workbench, pbg_ptools.workbench_viewers, bigraph_loom; print('combined env ok')"

# ─── serve ───────────────────────────────────────────────────────────────────
# The workspace (v2ecoli's workspace.yaml + studies/investigations/.git/runs.db)
# is mounted from the private EBS PVC at /workspace (see deploy/). SMS_API_BASE is
# set by the overlay to the in-cluster sms-api service. Bind 0.0.0.0 in-container.
WORKDIR /app
EXPOSE 8000
ENTRYPOINT ["vivarium-workbench"]
CMD ["serve", "--workspace", "/workspace", "--host", "0.0.0.0", "--port", "8000"]
