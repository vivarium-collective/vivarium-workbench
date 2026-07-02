"""Investigation status derivation + iset-summary, as library code.

Extracted from ``server.py`` so the FastAPI app (and tests) can build the
``/api/investigation-summaries`` payload without reaching into the 16.9k-line stdlib server.
Everything here is parameterized by ``ws_root`` (no module-level WORKSPACE
global) and the one workspace-coupled dependency — "does this study have any
runs?" — is *injected* as ``study_has_runs`` so the server's runs.db reader
stays where it is.

``server.py`` keeps thin back-compat shims (``_iter_iset_dirs``,
``_build_iset_summary_for_test``, …) that delegate here.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Callable, Iterator

import yaml

from vivarium_workbench.lib.workspace_paths import WorkspacePaths

# Sets used by compute_investigation_status. Module scope so the derivation
# rules are inspectable / overridable from tests.
_STUDY_STATUS_FAILED = frozenset({"failed", "invalid"})
_STUDY_STATUS_COMPLETE = frozenset({"complete", "ran"})
# Terminal "done" states for the INVESTIGATION roll-up only (Simulate ->
# Evaluate -> Decide): an all-evaluated investigation reads "complete".
_STUDY_STATUS_DONE_ROLLUP = _STUDY_STATUS_COMPLETE | frozenset({"evaluated", "decided"})
_STUDY_STATUS_RUNNING = frozenset({"running", "implementing", "runnable", "analyzing"})
_STUDY_STATUS_PLANNED = frozenset({"planned", "planning"})


def compute_investigation_status(
    study_statuses: list[str],
    has_runs: list[bool] | None = None,
) -> str:
    """Derive an investigation's effective status from its member studies.

    Rules, applied in order (first match wins):

    1. Any child in ``{failed, invalid}`` -> ``"failed"``.
    2. All children in the done roll-up (non-empty) -> ``"complete"``.
    3. Any child in ``{running, implementing, runnable, analyzing}`` -> ``"running"``.
    4. At least one child done OR with accumulated runs, but not all -> ``"in_progress"``.
    5. Otherwise (empty, or all planned/planning/unknown) -> ``"planning"``.

    ``has_runs`` is an optional parallel list of bools (one per study).
    """
    statuses = list(study_statuses or [])
    has_runs = list(has_runs or [False] * len(statuses))

    if any(s in _STUDY_STATUS_FAILED for s in statuses):
        return "failed"
    if statuses and all(s in _STUDY_STATUS_DONE_ROLLUP for s in statuses):
        return "complete"
    if any(s in _STUDY_STATUS_RUNNING for s in statuses):
        return "running"
    if any(s in _STUDY_STATUS_DONE_ROLLUP for s in statuses) or any(has_runs):
        return "in_progress"
    return "planning"


def iter_iset_dirs(ws_root: Path) -> Iterator[Path]:
    """Yield ``investigations/<name>/`` dirs that contain an investigation.yaml."""
    root = WorkspacePaths.load(ws_root).dir("investigations")
    if not root.is_dir():
        return
    for d in sorted(root.iterdir()):
        if d.is_dir() and (d / "investigation.yaml").is_file():
            yield d


def iset_lifecycle(ws_root: Path, slug: str) -> str:
    """Git lifecycle of an investigation: 'merged' if its dir exists in the
    merge-base with main (already on main), else 'wip'. Any git error -> 'wip'."""
    rel = WorkspacePaths.load(ws_root).rel("investigations") + f"/{slug}/investigation.yaml"
    try:
        base = subprocess.run(["git", "merge-base", "HEAD", "main"], cwd=str(ws_root),
                              capture_output=True, text=True)
        ref = base.stdout.strip() if base.returncode == 0 else "main"
        r = subprocess.run(["git", "cat-file", "-e", f"{ref}:{rel}"], cwd=str(ws_root),
                           capture_output=True, text=True)
        return "merged" if r.returncode == 0 else "wip"
    except Exception:
        return "wip"


def current_branch_slug(ws_root: Path) -> str | None:
    """The investigation slug matching the workspace's current git branch, or None."""
    try:
        br = subprocess.run(["git", "-C", str(ws_root), "branch", "--show-current"],
                            capture_output=True, text=True, timeout=2).stdout.strip()
    except Exception:
        return None
    if not br:
        return None
    slugs = [d.name for d in iter_iset_dirs(ws_root)]
    if br in slugs:
        return br
    for s in slugs:
        if br == f"investigation/{s}" or br.endswith("/" + s):
            return s
    brtok = set(t for t in re.split(r"[/_\-.]+", br.lower()) if t)
    best, best_n = None, 0
    for s in slugs:
        stok = set(t for t in re.split(r"[/_\-.]+", s.lower()) if t)
        n = len(brtok & stok)
        if n > best_n:
            best, best_n = s, n
    return best if best_n > 0 else None


# Callable injected for "does study <slug> (with parsed spec) have any runs?".
StudyHasRuns = Callable[[str, dict], bool]


def read_study_status(
    ws_root: Path, slug: str, *, study_has_runs: StudyHasRuns
) -> tuple[str, bool]:
    """Read (status, has_runs) for a member study referenced by an iset.

    Returns ``("planning", False)`` if the study can't be located or parsed.
    ``study_has_runs(slug, spec)`` supplies the runs-presence signal (injected so
    the workspace-coupled runs.db reader stays in the caller).
    """
    try:
        sp = WorkspacePaths.load(ws_root).study_dir(slug) / "study.yaml"
    except FileNotFoundError:
        sp = ws_root / "investigations" / slug / "spec.yaml"
    if sp.is_file():
        try:
            spec = yaml.safe_load(sp.read_text(encoding="utf-8")) or {}
        except Exception:
            return "planning", False
        status = spec.get("status") or "planning"
        return status, study_has_runs(slug, spec)
    return "planning", False


def build_iset_summary(
    ws_root: Path, *, study_has_runs: StudyHasRuns
) -> list[dict]:
    """Build the ``/api/investigation-summaries`` payload: one summary dict per investigation,
    each carrying an ``effective_status`` derived from its member studies."""
    out: list[dict] = []
    current_slug = current_branch_slug(ws_root)
    for d in iter_iset_dirs(ws_root):
        try:
            spec = yaml.safe_load((d / "investigation.yaml").read_text(encoding="utf-8")) or {}
        except Exception as e:
            out.append({"name": d.name, "error": f"parse failed: {e}"})
            continue
        study_slugs = list(spec.get("studies") or [])
        statuses_and_runs = [
            read_study_status(ws_root, s, study_has_runs=study_has_runs)
            for s in study_slugs
        ]
        statuses = [s for s, _ in statuses_and_runs]
        has_runs = [r for _, r in statuses_and_runs]
        out.append({
            "name":             spec.get("name", d.name),
            "title":            spec.get("title", spec.get("name", d.name)),
            "status":           spec.get("status", "planning"),
            "effective_status": compute_investigation_status(statuses, has_runs=has_runs),
            "description":      spec.get("description", ""),
            "question":         spec.get("question", ""),
            "hypothesis":       spec.get("hypothesis", ""),
            "n_studies":        len(study_slugs),
            "studies":          study_slugs,
            "lifecycle":        iset_lifecycle(ws_root, spec.get("name", d.name)),
            "current":          (d.name == current_slug),
        })
    return out


def study_run_slugs(ws_root: Path) -> set[str]:
    """Study slugs with at least one recorded run, via the simulations index.

    The library-native runs-presence signal for the FastAPI ``/api/investigation-summaries``
    route, so it need not import the stdlib server's runs.db reader.
    """
    from vivarium_workbench.lib.simulations_index import list_simulations

    slugs: set[str] = set()
    for row in list_simulations(ws_root):
        s = row.get("study_slug")
        if s:
            slugs.add(s)
    return slugs
