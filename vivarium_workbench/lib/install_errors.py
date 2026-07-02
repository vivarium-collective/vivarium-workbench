"""Parse pip/uv install error logs and produce structured, actionable guidance.

Common failure modes for pbg-* package installs:

- The package's deps have no wheels for the current Python ABI
- The package is pre-release only (and `--prerelease=allow` wasn't passed)
- Native extension build failed (missing system library)
- The git source path doesn't exist
- Network failure
"""
from __future__ import annotations
import re
from dataclasses import dataclass


@dataclass
class InstallDiagnosis:
    category: str
    summary: str
    suggestion: str
    raw_excerpt: str

    def as_dict(self) -> dict:
        return {
            "category": self.category,
            "summary": self.summary,
            "suggestion": self.suggestion,
            "raw_excerpt": self.raw_excerpt,
        }


def diagnose(log: str) -> InstallDiagnosis | None:
    """Return a structured diagnosis or None if no known pattern matches."""
    if not log:
        return None

    # Pattern: "we only found wheels for `<pkg>` (v<version>) with the following Python ABI tags: cp37m, cp38, ..."
    m = re.search(
        r"only found wheels for `([^`]+)`.*?Python ABI tags?:\s*([^\n]+)",
        log, re.DOTALL,
    )
    if m:
        pkg = m.group(1)
        tags = m.group(2)
        versions = sorted(set(int(v) for v in re.findall(r"cp3(\d+)", tags)))
        if versions:
            min_v, max_v = versions[0], versions[-1]
            range_label = f"3.{min_v}" if min_v == max_v else f"3.{min_v}–3.{max_v}"
            return InstallDiagnosis(
                category="python_version",
                summary=f"`{pkg}` has no wheels for your Python version.",
                suggestion=(
                    f"Recreate the workspace venv with Python {range_label}:\n"
                    f"  rm -rf .venv\n"
                    f"  uv venv .venv --python 3.{max_v}\n"
                    f"  uv pip install --python .venv/bin/python3 -e \".[dev]\"\n"
                    f"Then click Install again."
                ),
                raw_excerpt=m.group(0)[:400],
            )

    # Pattern: "Pre-releases are available for `<pkg>` in the requested range"
    m = re.search(r"Pre-releases are available for `([^`]+)`", log)
    if m:
        pkg = m.group(1)
        return InstallDiagnosis(
            category="prerelease",
            summary=f"`{pkg}` is only available as a pre-release.",
            suggestion=(
                f"Retry with pre-releases enabled. Run in your terminal:\n"
                f"  uv pip install --python .venv/bin/python3 --prerelease=allow -e <path>\n"
                f"Once installed, the dashboard's Registry tab will pick it up automatically."
            ),
            raw_excerpt=m.group(0)[:400],
        )

    # Pattern: native extension build failed
    if re.search(r"(fatal error: |command '(gcc|cc|clang)' failed|error: command 'pkg-config' failed)", log):
        m = re.search(r"(fatal error: [^\n]+|command '[^']+' failed[^\n]*)", log)
        return InstallDiagnosis(
            category="build",
            summary="Native extension build failed.",
            suggestion=(
                "The package needs system libraries to compile. Check its README for "
                "build dependencies (libraries to install with `brew install` or `apt-get install`)."
            ),
            raw_excerpt=m.group(0)[:400] if m else log[-400:],
        )

    # Pattern: requirements conflict
    if "requirements are unsatisfiable" in log:
        # Try to extract the conflict description
        m = re.search(r"(Because [^\n]+(?:\n[^\n]+){0,3}requirements are unsatisfiable)", log, re.DOTALL)
        excerpt = m.group(1)[:500] if m else log[-500:]
        return InstallDiagnosis(
            category="conflict",
            summary="Dependency conflict.",
            suggestion=(
                "uv couldn't find a set of versions that satisfy all the package's requirements. "
                "Check the snippet below for the specific conflict. You may need to relax a pin "
                "in pyproject.toml or upgrade an existing workspace dep."
            ),
            raw_excerpt=excerpt,
        )

    # Pattern: 404 / repo not found
    if re.search(r"(404|repository not found|fatal: repository .+ not found)", log, re.IGNORECASE):
        return InstallDiagnosis(
            category="not_found",
            summary="Source not found.",
            suggestion=(
                "The git URL or pypi name returned 404. Check the source URL in the catalog "
                "entry, or `gh repo view <name>` to confirm it exists."
            ),
            raw_excerpt=log[-400:],
        )

    # Pattern: network
    if re.search(r"(connection refused|timed out|temporary failure in name resolution)", log, re.IGNORECASE):
        return InstallDiagnosis(
            category="network",
            summary="Network failure.",
            suggestion="Retry. If it persists, check your connection or proxy.",
            raw_excerpt=log[-400:],
        )

    return None
