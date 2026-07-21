"""Hatchling build hook — build the vendored bigraph-loom bundle into the wheel.

`vivarium_workbench/loom/` is bigraph-loom's *source* (vendored in Task 8, which
dropped the `bigraph-loom @ git+...` dependency). The thing the server actually
serves is the Vite build output, `vivarium_workbench/loom/_dist`, resolved at
runtime by ``vivarium_workbench.loom_assets.asset_dir()``.

`_dist` is gitignored — a generated artifact, not source — so nothing in a clean
clone produces it. Before this hook, only the Docker image ran
`scripts/build_loom.sh`; every other install path (``pip install
vivarium-workbench``, ``uv pip install "vivarium-workbench @ git+https://..."``
— how workspaces consume this) built a wheel with the full TS source and **no
bundle**, and served a blank 404 Composite Explorer with no error to explain it.
The `artifacts` entry in pyproject packages `_dist` correctly when it exists;
the gap was purely that nothing built it.

This hook closes that gap by running the build as part of the wheel build.

Target policy differs on purpose:

- **wheel** — the bundle is REQUIRED. A wheel is what gets shipped and
  installed by people who cannot fix it themselves, so a missing toolchain is a
  hard error with an actionable message rather than a silently broken artifact.
- **editable** (``pip install -e .``) — best-effort. A contributor working on
  the Python side shouldn't be blocked by a missing Node, and they can run
  ``scripts/build_loom.sh`` when they need the Explorer. Warn, don't fail.

An already-built `_dist` is reused (see ``_is_fresh``) so repeat builds and the
Docker image — which runs the script explicitly before installing — don't pay
for a redundant ``npm ci``.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

# Set to a non-empty value to skip the loom build entirely. Escape hatch for
# environments that supply _dist by other means (or genuinely don't need the
# Explorer, e.g. a docs build); deliberately explicit, never inferred.
SKIP_ENV = "VIVARIUM_WORKBENCH_SKIP_LOOM_BUILD"


class LoomBuildHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict) -> None:
        root = Path(self.root)
        loom_dir = root / "vivarium_workbench" / "loom"
        dist_dir = loom_dir / "_dist"

        if os.environ.get(SKIP_ENV):
            self.app.display_waiting(
                f"{SKIP_ENV} set — skipping the loom bundle build")
            return

        # Nothing to build against (e.g. an sdist that excluded the source).
        if not (loom_dir / "package.json").is_file():
            self._missing(version, f"no loom source at {loom_dir}")
            return

        if self._is_fresh(loom_dir, dist_dir):
            self.app.display_info(f"loom bundle already built: {dist_dir}")
            return

        script = root / "scripts" / "build_loom.sh"
        if shutil.which("npm") is None:
            self._missing(version, "npm not found on PATH")
            return

        self.app.display_waiting("building the loom bundle (npm run build)…")
        try:
            subprocess.run(["bash", str(script)], cwd=str(root), check=True)
        except subprocess.CalledProcessError as exc:
            self._missing(version, f"{script.name} failed (exit {exc.returncode})")
            return

        if not (dist_dir / "index.html").is_file():
            self._missing(version, f"{script.name} ran but produced no {dist_dir}")

    @staticmethod
    def _is_fresh(loom_dir: Path, dist_dir: Path) -> bool:
        """True when `_dist` exists and no loom source file is newer than it.

        Deliberately coarse — an mtime comparison, not a content hash. Getting
        this wrong in the "stale" direction costs a rebuild; getting it wrong in
        the "fresh" direction would ship an out-of-date bundle, so anything
        ambiguous (missing entry point, unreadable mtime) counts as stale.
        """
        entry = dist_dir / "index.html"
        if not entry.is_file():
            return False
        try:
            built_at = entry.stat().st_mtime
            src = loom_dir / "src"
            candidates = list(src.rglob("*")) if src.is_dir() else []
            candidates += [loom_dir / "package.json", loom_dir / "vite.config.ts"]
            return all(p.stat().st_mtime <= built_at
                       for p in candidates if p.is_file())
        except OSError:
            return False

    def _missing(self, version: str, reason: str) -> None:
        """Hard-fail a wheel build; warn for anything else (notably editable)."""
        msg = (
            f"loom bundle not built — {reason}.\n"
            f"The Composite Explorer is served from vivarium_workbench/loom/_dist, "
            f"which is generated (gitignored), not source.\n"
            f"Install Node 20+ and re-run, build it manually with "
            f"scripts/build_loom.sh, or set {SKIP_ENV}=1 to ship without it."
        )
        if version == "editable":
            self.app.display_warning(f"warning: {msg}")
            return
        raise RuntimeError(msg)


# Hatchling discovers the hook class by scanning this module; the explicit
# alias keeps the entry point stable if the class is ever renamed.
hatch_build_hook = LoomBuildHook


def get_build_hook():  # pragma: no cover - hatchling calls the class directly
    return LoomBuildHook


if __name__ == "__main__":  # pragma: no cover - manual smoke check
    print("this module is a hatchling build hook; run a build instead",
          file=sys.stderr)
