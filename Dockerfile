# syntax=docker/dockerfile:1
#
# vivarium-workbench server image — the TOOL, not the science environment.
#
# This image contains the workbench and its own declared dependencies, built
# from THIS repo's uv.lock. It deliberately does NOT contain a workspace
# package (v2ecoli) or a science/compute stack.
#
# ─── why (issue #932) ────────────────────────────────────────────────────────
# The previous design installed the workbench INTO the v2ecoli venv, copied out
# of a published per-commit workspace image (WORKSPACE_IMAGE). That welded the
# tool to the science environment and had four consequences:
#
#   1. ~4.5 GB of GPU/ML stack the server never touches (nvidia 2.7 GB, torch
#      1.1 GB, triton 689 MB, ray 190 MB) rode along in a container that serves
#      a web UI and spawns subprocesses.
#   2. The same 7.2 GB environment was paid for TWICE — once baked in, once on
#      the PVC as <workspace>/.venv, which is the copy that actually runs the
#      science (see below).
#   3. Building required pulling a base from a GovCloud ECR repo, for which no
#      GitHub Actions credential exists (no OIDC federation into that account).
#   4. The build-time smoke test did `import v2ecoli` -> polars, which needs
#      AVX/AVX2/FMA/BMI. QEMU does not emulate those, so a cross-build from an
#      ARM Mac ALWAYS died with "Illegal instruction" — there was no working
#      path to build this image from Apple Silicon at all.
#
# None of it was load-bearing. `EnvironmentResolver.resolve_interpreter()`
# already sends every env worker to the WORKSPACE's own interpreter — verified
# live on sms-api-stanford-test: `/workspace/.venv/bin/python`, while the server
# process ran on the baked `/app/v2ecoli/.venv/bin/python`. All 10 `v2ecoli`
# imports in env_worker.py sit inside `try:` blocks ("best-effort
# self-registration; kept separate so absent/faked v2ecoli"), so nothing
# hard-depends on it.
#
# ─── the contract this creates ───────────────────────────────────────────────
# The workspace supplies the science environment; this image supplies the tool
# and spawns workers into that environment. `<workspace>/.venv` is therefore
# REQUIRED, not a nice-to-have — see lib/env_resolver.py, which now says so
# loudly rather than silently falling back to this thin server venv.
#
# ─── on the `--no-deps` chain this replaces ──────────────────────────────────
# The old build used `--no-deps` throughout and then force-pinned
# process-bigraph/bigraph-schema to specific commits, because the venv came
# from sms-ecoli's lock and a real re-resolve risked upgrading substrate
# packages sms-ecoli was not tested against (backlog item 44 was a live
# production crash from exactly that skew: ModuleNotFoundError
# 'process_bigraph.artifacts').
#
# Building from THIS repo's own uv.lock removes that entire class of bug
# structurally — there is no foreign lock to skew against, so the floors in
# pyproject.toml (`process-bigraph>=1.8.2`, `bigraph-schema>=1.4.3`) are simply
# what gets installed. tests/test_process_bigraph_pin.py, which asserted the
# Dockerfile's hand-pins matched uv.lock, no longer has pins to guard.

FROM python:3.12-bookworm

# uv (pinned binary) for fast, lock-faithful installs.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# build-essential: some deps still build C extensions from sdist.
# git: needed for the git-sourced deps at BUILD time, and at RUNTIME too (the
#      live served /workspace is a real git working copy the app commits to).
# Node/npm: builds the vendored bigraph-loom bundle below.
#
# NodeSource's legacy `curl setup_20.x | bash -` piped installer was deprecated
# and now no-ops silently on some runners, leaving `apt-get install nodejs` to
# resolve Debian bookworm's nodejs — which ships WITHOUT npm (a separate
# package) → the loom build died on `npm: command not found`. Use NodeSource's
# supported keyring + apt-repo method instead, and assert node+npm exist at
# build time so a future regression fails loudly here, not 60 layers later.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential git ca-certificates curl gnupg \
 && mkdir -p /etc/apt/keyrings \
 && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
      | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
 && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" \
      > /etc/apt/sources.list.d/nodesource.list \
 && apt-get update && apt-get install -y --no-install-recommends nodejs \
 && node --version && npm --version \
 && rm -rf /var/lib/apt/lists/*

# BUILD-time only: real wheel copies, not links into the BuildKit cache mount.
# Reset to uv's Linux default (hardlink) at the end of this file so it does NOT
# ship in the runtime image -- see the note above the ENTRYPOINT.
ENV UV_LINK_MODE=copy

WORKDIR /app/vivarium-workbench
COPY . .

# The workbench + its own declared dependencies, straight from this repo's
# uv.lock. `--frozen` uses the lock as-is (a drifted lock fails the build rather
# than silently re-resolving); `--no-dev` keeps test/lint tooling out of the
# shipped image.
#
# This does NOT pull a workspace package: v2ecoli is not a dependency of this
# project at all (the local v2ecoli dev environment is its own project,
# demos/v2ecoli/, which this image does not install). Every core
# dependency resolves from a git source declared in [tool.uv.sources], so no
# private registry is involved.
#
# Deliberately WITHOUT the `[chat]` extra: this image is what hosted workbenches
# run, and the AI chat stays out of hosted deployments until the chat hardening
# work (#1238, with its limits follow-up #1247) is merged, released and
# deployed. That work is what makes chat safe to serve to anonymous visitors
# (off by default on a non-loopback bind, forged-history rejection, provider
# isolation). Adding `--extra chat` back here belongs in that release. Local
# installs are unaffected: `pip install 'vivarium-workbench[chat]'` still gets
# the chat. This governs this image only: a workspace that depends on
# `vivarium-workbench[chat]` brings the chat into any image built from that
# workspace's own environment, so such an image must not be hosted before then.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

ENV PATH="/app/vivarium-workbench/.venv/bin:${PATH}"

# This image ships only the workbench and its own dependencies, so
# EnvironmentResolver must NOT silently fall back to it for workspace work —
# that would run analysis code in an interpreter that cannot import the
# workspace package and fail deep inside a worker call. Require each workspace
# to bring its own .venv and fail loudly at the seam instead (#932).
ENV VIVARIUM_WORKBENCH_REQUIRE_WORKSPACE_VENV=1

# The Omics-Viewer plugin. Deliberately NOT a dependency in pyproject.toml: the
# dependency arrow runs pbg-ptools -> vivarium-workbench (leaf -> host), so the
# workbench must not depend back on it. `--no-deps` keeps its own
# vivarium-workbench requirement from resolving a SECOND copy over the one just
# installed from this build context.
ARG PBG_PTOOLS_REF=main
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --no-deps \
        "pbg-ptools @ git+https://github.com/vivarium-collective/pbg-ptools.git@${PBG_PTOOLS_REF}"

# ─── vendored bigraph-loom bundle ────────────────────────────────────────────
# Built here so a missing bundle fails the BUILD rather than shipping a silent
# runtime 500 (the always-visible loom panel fires a loom-asset request for ANY
# composite).
RUN bash scripts/build_loom.sh

# ─── Perfetto trace viewer (the Runs table's "⏱ Trace") ───────────────────────
# A pinned Perfetto UI release (Apache-2.0), mirrored from ui.perfetto.dev and
# verified file-by-file against hashes pinned in lib/perfetto_ui.py, then served
# by the workbench at <base-path>/perfetto/ -- so opening a trace needs no
# outside network at runtime (GovCloud). ~64 MB. A failed verification fails the
# build. Without this step the server falls back to https://ui.perfetto.dev.
ENV VIVARIUM_WORKBENCH_PERFETTO_UI_DIR=/app/perfetto-ui
RUN python -m vivarium_workbench.lib.perfetto_ui --dest /app/perfetto-ui

# Sanity: everything the SERVER itself imports resolves in one interpreter, and
# the loom bundle actually landed on disk. `vivarium_workbench.api.app` is
# imported explicitly and separately — it is the exact module chain that crashed
# in production (item 44); a bare `import vivarium_workbench` does not eagerly
# pull it in, so that regression previously shipped silently past this check and
# surfaced only as a live CrashLoopBackOff.
#
# `import v2ecoli` is deliberately GONE (#932): the workspace package is not in
# this image, is imported best-effort at runtime from the mounted workspace, and
# was the line that made this build impossible under QEMU.
RUN python -c "\
import vivarium_workbench, pbg_ptools.workbench_viewers; \
import vivarium_workbench.api.app; \
from vivarium_workbench.loom_assets import asset_dir; \
d = asset_dir(); \
assert (d / 'index.html').is_file(), f'loom bundle missing: {d}'; \
print('workbench server env ok')"

# ─── runtime link mode ───────────────────────────────────────────────────────
# Undo the build-time UV_LINK_MODE=copy above so it is not baked into the image.
#
# At RUNTIME uv is used to provision a venv per served workspace (workbench#937,
# REFACTOR-PLAN §2A.7). `copy` defeats the sharing that makes that affordable:
# measured in this image, three venvs installing the same wheels cost 299 MB
# under `copy` versus 103 MB under the default `hardlink` -- N x versus ~1 x,
# where the marginal cost of a workspace is only what genuinely differs from the
# ones already present. `copy` was only ever here to keep wheels from linking
# into a BuildKit cache mount, which has no bearing on a persistent PVC.
#
# PRECONDITION, easy to get wrong: hardlinking requires the uv CACHE and the
# target venv on the SAME filesystem. In the deployment the venvs live on the
# workbench PVC, so the cache must be moved there too (a subPath mount over
# ~/.cache/uv) -- otherwise uv silently falls back to copying and this setting
# buys nothing. Verified 2026-08-26 that the default cache (/root/.cache/uv) is
# on the container's ephemeral overlay fs, a DIFFERENT device from /workspace.
ENV UV_LINK_MODE=hardlink

# ─── image provenance (#1114) ────────────────────────────────────────────────
# A published image otherwise carries no record of the commit it was built
# from, and nothing enforces that its version tag means anything -- see #1114
# for the real cost that created (9 of 80 published versions turned out to have
# no recoverable commit at all).
#
# Deliberately placed HERE, after every expensive layer above (apt/uv sync/npm
# build/sanity check): VERSION/VCS_REF/BUILD_DATE are unique per build, so
# declaring them earlier would invalidate the dependency-install cache on every
# single build for no benefit.
ARG VERSION=dev
ARG VCS_REF=unknown
ARG BUILD_DATE=unknown
LABEL org.opencontainers.image.title="vivarium-workbench" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.source="https://github.com/vivarium-collective/vivarium-workbench" \
      org.opencontainers.image.created="${BUILD_DATE}"

# Same three values, readable straight off a running pod with no registry
# access -- the thing you actually want when identifying what's deployed:
#   kubectl exec -n <ns> deploy/workbench -- cat /app/BUILD_INFO.json
RUN printf '{"version":"%s","revision":"%s","created":"%s"}\n' \
        "${VERSION}" "${VCS_REF}" "${BUILD_DATE}" > /app/BUILD_INFO.json

# ─── serve ───────────────────────────────────────────────────────────────────
# The workspace (workspace.yaml + studies/investigations/.git/runs.db AND its
# own .venv) is mounted from the private EBS PVC at /workspace (see deploy/).
# SMS_API_BASE is set by the overlay to the in-cluster sms-api service.
WORKDIR /app
EXPOSE 8000
ENTRYPOINT ["vivarium-workbench"]
CMD ["serve", "--workspace", "/workspace", "--host", "0.0.0.0", "--port", "8000"]
