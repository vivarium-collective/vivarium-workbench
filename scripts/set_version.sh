#!/usr/bin/env bash
# Move the core and the backend plugin to one version (docs/backend-adapters.md step P4b).
#
#   scripts/set_version.sh 0.6.0
#
# The two distributions are released in lockstep: identical versions, `==` pins both ways. This rewrites the four
# places that carry the version --
#   pyproject.toml                                     version = "X"   and   vivaapi = ["vivarium-workbench-vivaapi==X"]
#   packages/vivarium-workbench-vivaapi/pyproject.toml version = "X"   and   "vivarium-workbench==X"
# -- then runs `uv lock` (which updates the two editable entries' versions in uv.lock) and
# scripts/check_release.py X. deploy/bump-and-release.sh calls it; release PRs use it too.
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${1:-}"
if [[ ! "${VERSION}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "usage: $0 X.Y.Z" >&2
  exit 2
fi
root="${repo_root}/pyproject.toml"
member="${repo_root}/packages/vivarium-workbench-vivaapi/pyproject.toml"

# Each substitution must hit exactly one line, or the file's shape changed and this script is out of date.
replace() {  # file, extended regex (a whole line), replacement
  local file="$1" pattern="$2" new="$3" n
  n="$(grep -cE "${pattern}" "${file}" || true)"
  if [[ "${n}" != "1" ]]; then
    echo "ERROR: expected exactly one line matching /${pattern}/ in ${file}, found ${n}" >&2
    exit 1
  fi
  sed -i.bak -E "s/${pattern}/${new}/" "${file}"
  rm -f "${file}.bak"
}

replace "${root}"   '^version = "[^"]*"$'                                   "version = \"${VERSION}\""
replace "${root}"   '^vivaapi = \["vivarium-workbench-vivaapi==[^"]*"\]$'    "vivaapi = [\"vivarium-workbench-vivaapi==${VERSION}\"]"
replace "${member}" '^version = "[^"]*"$'                                   "version = \"${VERSION}\""
replace "${member}" '^    "vivarium-workbench==[^"]*",$'                     "    \"vivarium-workbench==${VERSION}\","

(cd "${repo_root}" && uv lock)
python3 "${repo_root}/scripts/check_release.py" "${VERSION}"
