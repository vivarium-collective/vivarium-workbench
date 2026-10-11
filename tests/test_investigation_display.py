"""Investigation display-hardening: the investigation DETAIL must pass through
`competing_frameworks` (the comparative-framing lenses) and the investigation
SUMMARY must carry `verdict_status` (the authored rollup a reviewer lands on),
so the dashboard can render them. Both were dropped before this change."""
import subprocess

from vivarium_workbench.lib.report_views import build_iset_detail
from vivarium_workbench.lib.investigation_status import build_iset_summary


def _git_ws(tmp):
    inv = tmp / "investigations" / "og-demo"
    inv.mkdir(parents=True)
    (inv / "investigation.yaml").write_text(
        "schema_version: 2\n"
        "name: og-demo\n"
        "title: Origins demo\n"
        "status: planning\n"
        "question: Can geology become an evolving chemical ecology?\n"
        "executive:\n"
        "  what_is_this: A staged falsifiable campaign.\n"
        "  verdict: Planned.\n"
        "  verdict_status: planning\n"
        "competing_frameworks:\n"
        "  - {name: Classical alkaline-vent, relation: primary-lens}\n"
        "  - {name: 2026 reassessment, relation: challenger}\n"
        "studies: []\n",
        encoding="utf-8",
    )
    for c in (["init", "-q"], ["config", "user.email", "t@t"],
              ["config", "user.name", "t"], ["add", "-A"],
              ["commit", "-qm", "init"], ["branch", "-M", "main"]):
        subprocess.run(["git", *c], cwd=tmp, check=True)
    return tmp


def test_iset_detail_passes_competing_frameworks(tmp_path):
    ws = _git_ws(tmp_path)
    detail = build_iset_detail(ws, "og-demo")
    assert detail is not None
    cf = detail.get("competing_frameworks")
    assert isinstance(cf, list) and len(cf) == 2, "competing_frameworks dropped"
    names = {f.get("name") for f in cf}
    assert "Classical alkaline-vent" in names
    assert cf[0].get("relation") == "primary-lens"


def test_iset_detail_competing_frameworks_empty_when_absent(tmp_path):
    ws = _git_ws(tmp_path)
    # Strip the field; detail must degrade to [] (never KeyError) so the UI hides it.
    y = (ws / "investigations" / "og-demo" / "investigation.yaml")
    y.write_text("\n".join(
        l for l in y.read_text().splitlines()
        if not l.startswith("competing_frameworks")
        and not l.strip().startswith("- {name: ")), encoding="utf-8")
    detail = build_iset_detail(ws, "og-demo")
    assert detail.get("competing_frameworks") == []


def test_iset_summary_carries_verdict_status(tmp_path):
    ws = _git_ws(tmp_path)
    rows = build_iset_summary(ws, study_has_runs=lambda _slug: False)
    row = next((r for r in rows if r.get("name") == "og-demo"), None)
    assert row is not None, "og-demo not in summary"
    assert row.get("verdict_status") == "planning", \
        "verdict_status not surfaced in investigation summary"
