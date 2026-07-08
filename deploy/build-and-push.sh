#!/usr/bin/env bash
#
# Build + push the combined workbench image for the EKS cluster.
#
# The cluster nodes are x86_64, so this ALWAYS builds linux/amd64 (a native
# arm64 build from a Mac will not run there). Mirrors ../sms-api/kustomize/
# scripts/build_and_push.sh.
#
# Usage:
#   deploy/build-and-push.sh [version] [org]
#     version  image tag (default: short git sha)
#     org      ghcr org   (default: vivarium-collective)
#
# Requires: docker buildx + a ghcr login (`docker login ghcr.io`).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${1:-$(git -C "${ROOT_DIR}" rev-parse --short HEAD)}"
ORG="${2:-vivarium-collective}"
IMAGE="ghcr.io/${ORG}/vivarium-workbench:${VERSION}"

echo "building + pushing ${IMAGE} (linux/amd64)"
docker buildx build \
  --platform=linux/amd64 \
  -f "${ROOT_DIR}/Dockerfile" \
  -t "${IMAGE}" \
  "${ROOT_DIR}" \
  --push

echo "pushed ${IMAGE}"
echo "pin it in deploy/kustomize/overlays/<env>/kustomization.yaml (images: newTag)"
