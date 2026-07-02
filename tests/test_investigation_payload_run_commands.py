"""Task 9: the investigation-report per-study builder exposes run_commands.

The investigation report SPA renders run-command chips off each per-study
object. The study objects the report iterates are the resolved study specs
(``/api/study/<slug>`` → ``load_study_detail_spec``, wired in Task 6), but the
investigation-detail projection (``build_iset_detail`` → ``/api/investigation``)
must ALSO carry ``run_commands`` so the single source is consistent across both
endpoints. This test pins the per-study projection's ``run_commands.baseline``.
"""
import yaml

from vivarium_workbench.lib import report_views


def _make_ws(tmp_path):
    ws = tmp_path / "iset_ws"
    slug = "demo-study"
    inv = "demo-inv"
    pkg = "pbg_demo"
    composite_id = f"{pkg}.composites.demo"

    ws.mkdir(parents=True)
    (ws / "workspace.yaml").write_text(
        yaml.safe_dump({"name": "demo", "package_path": pkg}), encoding="utf-8"
    )

    study_dir = ws / "studies" / slug
    study_dir.mkdir(parents=True)
    (study_dir / "study.yaml").write_text(
        yaml.safe_dump({
            "schema_version": 4,
            "name": slug,
            "question": "Does the demo composite run correctly?",
            "conditions": {
                "baseline": {"composite": composite_id, "params": {"n_steps": 5}},
                "variants": [
                    {"name": "var-one", "composite": composite_id,
                     "parameter_overrides": {"n_steps": 10}},
                ],
            },
        }),
        encoding="utf-8",
    )

    inv_dir = ws / "investigations" / inv
    inv_dir.mkdir(parents=True)
    (inv_dir / "investigation.yaml").write_text(
        yaml.safe_dump({
            "name": inv,
            "title": "Demo investigation",
            "studies": [slug],
        }),
        encoding="utf-8",
    )
    return ws, inv, slug


def test_iset_detail_per_study_has_run_commands(tmp_path):
    ws, inv, slug = _make_ws(tmp_path)
    detail = report_views.build_iset_detail(ws, inv)
    assert detail is not None
    studies = detail["studies"]
    by_name = {s["name"]: s for s in studies}
    assert slug in by_name
    rc = by_name[slug].get("run_commands")
    assert rc is not None, "per-study projection must carry run_commands"
    assert rc["baseline"] == f"vdash run study {slug}"


def test_iset_baseline_matches_single_source(tmp_path):
    """The chip string must come from study_run_commands, not a literal."""
    from vivarium_workbench.lib.run_commands import study_run_commands
    ws, inv, slug = _make_ws(tmp_path)
    spec = yaml.safe_load(
        (ws / "studies" / slug / "study.yaml").read_text(encoding="utf-8")
    )
    expected = study_run_commands(spec, slug)
    detail = report_views.build_iset_detail(ws, inv)
    rc = {s["name"]: s for s in detail["studies"]}[slug]["run_commands"]
    assert rc["baseline"] == expected["baseline"]
