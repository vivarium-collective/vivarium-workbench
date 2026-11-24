#!/usr/bin/env bash
#
# Bump pyproject.toml, tag, and dispatch a real image build+push, as one
# command -- so the compliant release sequence is shorter than the
# non-compliant one (edit nothing, dispatch a form, type a number). See #1114
# for why this exists: pyproject.toml had drifted seven releases behind the
# actually-published images, and reconstructing which commit produced a given
# tag required forensic log correlation -- 9 of 80 published versions turned
# out to have no recoverable commit at all.
#
# Usage:
#   deploy/bump-and-release.sh <version> [org]
#     version  strict semver, e.g. 0.3.86 (no leading "v", no pre-release/build
#              metadata -- this repo's own versioning has never used either)
#     org      ghcr org (default: vivarium-collective)
#
# Refuses, loudly, before touching anything, unless ALL of:
#   - the working tree is clean (no uncommitted changes)
#   - HEAD is on `main`
#   - local `main` is exactly in sync with `origin/main` (neither ahead nor behind)
#   - `version` is strict X.Y.Z semver and a real increase over pyproject.toml's
#     current version (major/minor/patch tuple comparison; no equal, no decrease)
#   - the tag `v<version>` does not already exist on the remote
#   - the ghcr tag `<version>` does not already exist
#
# On success: rewrites both pyprojects (the core and the viva-api backend
# plugin, packages/vivarium-workbench-vivaapi -- lockstep versions and `==` pins,
# via scripts/set_version.sh) and uv.lock, commits `chore: bump version to
# <version>`, creates an ANNOTATED tag `v<version>`, pushes commit + tag, then
# dispatches build-and-push.yml against that exact tag (not a branch, so the
# image is provably built from the commit the tag points at) and prints the
# consumer-side kustomize lines that now need to change.
#
# Requires: git, gh (authenticated), docker (for the ghcr-tag-exists check via
# `docker manifest inspect`, same auth precondition as deploy/build-and-push.sh).
set -euo pipefail

VERSION="${1:?usage: deploy/bump-and-release.sh <version> [org]}"
ORG="${2:-vivarium-collective}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYPROJECT="${ROOT_DIR}/pyproject.toml"

fail() { echo "refusing: $*" >&2; exit 1; }

# ─── strict semver shape, no leading "v", no pre-release/build metadata ─────
if [[ ! "${VERSION}" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]; then
  fail "'${VERSION}' is not strict X.Y.Z semver (no leading v, no pre-release/build suffix)"
fi

# ─── clean tree ──────────────────────────────────────────────────────────────
if [[ -n "$(git -C "${ROOT_DIR}" status --porcelain)" ]]; then
  fail "working tree is not clean -- commit, stash, or discard first"
fi

# ─── on main ─────────────────────────────────────────────────────────────────
CURRENT_BRANCH="$(git -C "${ROOT_DIR}" rev-parse --abbrev-ref HEAD)"
if [[ "${CURRENT_BRANCH}" != "main" ]]; then
  fail "on '${CURRENT_BRANCH}', not 'main' -- checkout main first"
fi

# ─── in sync with origin/main ────────────────────────────────────────────────
git -C "${ROOT_DIR}" fetch origin main --quiet
LOCAL_SHA="$(git -C "${ROOT_DIR}" rev-parse HEAD)"
REMOTE_SHA="$(git -C "${ROOT_DIR}" rev-parse origin/main)"
if [[ "${LOCAL_SHA}" != "${REMOTE_SHA}" ]]; then
  fail "local main (${LOCAL_SHA:0:12}) != origin/main (${REMOTE_SHA:0:12}) -- pull or push first"
fi

# ─── strict, monotonic semver bump over pyproject.toml ──────────────────────
CURRENT_VERSION="$(grep -m1 '^version = ' "${PYPROJECT}" | sed -E 's/^version = "(.*)"$/\1/')"
if [[ -z "${CURRENT_VERSION}" ]]; then
  fail "could not read current version from ${PYPROJECT}"
fi
IFS='.' read -r CUR_MAJOR CUR_MINOR CUR_PATCH <<< "${CURRENT_VERSION}"
IFS='.' read -r NEW_MAJOR NEW_MINOR NEW_PATCH <<< "${VERSION}"
IS_GREATER=0
if (( NEW_MAJOR > CUR_MAJOR )); then IS_GREATER=1
elif (( NEW_MAJOR == CUR_MAJOR && NEW_MINOR > CUR_MINOR )); then IS_GREATER=1
elif (( NEW_MAJOR == CUR_MAJOR && NEW_MINOR == CUR_MINOR && NEW_PATCH > CUR_PATCH )); then IS_GREATER=1
fi
if [[ "${IS_GREATER}" -ne 1 ]]; then
  fail "${VERSION} is not a strict increase over pyproject.toml's current ${CURRENT_VERSION}"
fi

# ─── tag v<version> must not already exist ──────────────────────────────────
git -C "${ROOT_DIR}" fetch origin "refs/tags/v${VERSION}" --quiet 2>/dev/null && \
  fail "tag v${VERSION} already exists on origin -- never silently overwrite a published tag"

# ─── ghcr tag <version> must not already exist ──────────────────────────────
if docker manifest inspect "ghcr.io/${ORG}/vivarium-workbench:${VERSION}" >/dev/null 2>&1; then
  fail "ghcr.io/${ORG}/vivarium-workbench:${VERSION} already exists -- never silently overwrite a published image"
fi

echo "all guards passed: ${CURRENT_VERSION} -> ${VERSION}, clean main in sync with origin, tag and image both unused"

# ─── bump, commit, tag, push ─────────────────────────────────────────────────
# docs/backend-adapters.md step P4b: the core and the viva-api backend plugin are released in lockstep.
# set_version.sh moves both versions and both `==` pins, re-locks, and runs scripts/check_release.py.
"${ROOT_DIR}/scripts/set_version.sh" "${VERSION}"
git -C "${ROOT_DIR}" add "${PYPROJECT}" "${ROOT_DIR}/packages/vivarium-workbench-vivaapi/pyproject.toml" "${ROOT_DIR}/uv.lock"
git -C "${ROOT_DIR}" commit -m "chore: bump version to ${VERSION}"
git -C "${ROOT_DIR}" tag -a "v${VERSION}" -m "v${VERSION}"
git -C "${ROOT_DIR}" push origin main
git -C "${ROOT_DIR}" push origin "v${VERSION}"

echo "committed + pushed chore: bump version to ${VERSION}, pushed annotated tag v${VERSION}"

# ─── dispatch the build against main, right after pushing to it ─────────────
# Dispatch against `main`, not the tag: the CI gate (#1114 part 3) requires
# `github.ref == refs/heads/main` and separately checks that `v<version>`
# points at the checked-out commit -- it verifies the tag and the build are
# the same commit by checking the tag FROM main, rather than by building from
# the tag ref directly. That's only true here because the bump commit was
# just pushed straight above with nothing landing on main in between.
RELEASE_SHA="$(git -C "${ROOT_DIR}" rev-parse HEAD)"
gh workflow run build-and-push.yml \
  --repo "${ORG}/vivarium-workbench" \
  --ref main \
  -f "version=${VERSION}"

echo "dispatched build-and-push.yml against main (${RELEASE_SHA:0:12}, == v${VERSION})"
echo
echo "once it lands, pin it on the consumer side (viva-api):"
echo "  kustomize/overlays/<env>/kustomization.yaml   images: ... newTag: ${VERSION}"
echo "  kustomize/config/<env>/shared.env             ENV_WORKER_MODULE_IMAGE=...:${VERSION}"
echo "these two must stay equal -- see #1114's own 'Consumer side' note for why."
