"""Branch-aware active investigation selection.

``_build_iset_summary_for_test`` marks the investigation whose slug matches
the workspace's current git branch with ``current: True`` (token-overlap
fallback when the branch isn't an exact/suffix match).
"""
import subprocess

from vivarium_workbench.lib.investigation_status import (
    build_iset_summary,
    study_run_slugs,
)


def _build_iset_summary_for_test(ws):
    run_slugs = study_run_slugs(ws)

    def _has_runs(slug, spec):
        return slug in run_slugs or bool((spec or {}).get("runs"))

    return build_iset_summary(ws, study_has_runs=_has_runs)


def _git_ws(tmp):
    (tmp / "workspace.yaml").write_text("name: demo\n", encoding="utf-8")
    for slug in ("dnaa-replication", "colonies"):
        inv = tmp / "investigations" / slug
        (inv / "studies").mkdir(parents=True)
        (inv / "investigation.yaml").write_text(
            f"name: {slug}\ntitle: {slug}\nstudies: []\n", encoding="utf-8")
    for c in (["init", "-q"], ["config", "user.email", "t@t"],
              ["config", "user.name", "t"], ["add", "-A"],
              ["commit", "-qm", "init"], ["branch", "-M", "main"],
              ["checkout", "-qb", "investigation/dnaa-replication-v3"]):
        subprocess.run(["git", *c], cwd=tmp, check=True)
    return tmp


def test_current_branch_marks_matching_investigation(tmp_path):
    ws = _git_ws(tmp_path)
    out = {i["name"]: i for i in _build_iset_summary_for_test(ws)}
    assert out["dnaa-replication"]["current"] is True
    assert out["colonies"]["current"] is False
