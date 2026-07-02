"""Dashboard resolves nested investigation studies (Phase 2)."""
import subprocess

from vivarium_workbench.lib.investigation_status import (
    build_iset_summary,
    read_study_status,
    study_run_slugs,
)
from vivarium_workbench.lib.report_views import build_iset_detail


def _has_runs_fn(ws):
    run_slugs = study_run_slugs(ws)

    def _has_runs(slug, spec):
        return slug in run_slugs or bool((spec or {}).get("runs"))

    return _has_runs


def _build_iset_summary_for_test(ws):
    return build_iset_summary(ws, study_has_runs=_has_runs_fn(ws))


def _build_iset_detail_for_test(ws, name):
    detail = build_iset_detail(ws, name)
    if detail is None:
        return {"error": f"investigation '{name}' not found"}, 404
    return detail, 200


def _read_study_status(ws, slug):
    return read_study_status(ws, slug, study_has_runs=_has_runs_fn(ws))


def _nested_ws(tmp):
    (tmp / "workspace.yaml").write_text("name: demo\n", encoding="utf-8")
    inv = tmp / "investigations" / "inv-a"
    (inv / "studies" / "s1").mkdir(parents=True)
    (inv / "investigation.yaml").write_text(
        "name: inv-a\ntitle: A\nstudies:\n  - s1\n", encoding="utf-8")
    (inv / "studies" / "s1" / "study.yaml").write_text(
        "schema_version: 3\nname: s1\ninvestigation: inv-a\nstatus: complete\n"
        "baseline:\n  - {composite: x, name: b}\n", encoding="utf-8")
    return tmp


def test_read_study_status_resolves_nested(tmp_path):
    ws = _nested_ws(tmp_path)
    status, _has_runs = _read_study_status(ws, "s1")
    assert status == "complete"  # flat-only resolution would return "planning"


def test_detail_resolves_nested_study(tmp_path):
    ws = _nested_ws(tmp_path)
    detail, code = _build_iset_detail_for_test(ws, "inv-a")
    assert code == 200
    s1 = {s["name"]: s for s in detail["studies"]}["s1"]
    assert s1["status"] == "complete"  # nested study's real status, not "planning"


def test_lifecycle_badge_main_vs_branch(tmp_path):
    ws = _nested_ws(tmp_path)
    for c in (["init", "-q"], ["config", "user.email", "t@t"],
              ["config", "user.name", "t"], ["add", "-A"],
              ["commit", "-qm", "init"], ["branch", "-M", "main"],
              ["checkout", "-qb", "feat/x"]):
        subprocess.run(["git", *c], cwd=ws, check=True)
    invb = ws / "investigations" / "inv-b"
    (invb / "studies").mkdir(parents=True)
    (invb / "investigation.yaml").write_text("name: inv-b\nstudies: []\n", encoding="utf-8")
    out = {i["name"]: i for i in _build_iset_summary_for_test(ws)}
    assert out["inv-a"]["lifecycle"] == "merged"
    assert out["inv-b"]["lifecycle"] == "wip"


def test_report_dir_and_helper(tmp_path):
    from vivarium_workbench.lib.workspace_paths import WorkspacePaths
    from vivarium_workbench.lib.download_views import (
        resolve_iset_report as _iset_report_file,
    )
    ws = _nested_ws(tmp_path)
    wp = WorkspacePaths.load(ws)
    assert wp.report_dir("inv-a") == ws / "investigations" / "inv-a" / "reports"
    assert _iset_report_file(ws, "inv-a") is None              # no report yet
    rep = ws / "investigations" / "inv-a" / "reports"; rep.mkdir(parents=True)
    (rep / "index.html").write_text("<h1>report</h1>", encoding="utf-8")
    assert _iset_report_file(ws, "inv-a") == rep / "index.html"
