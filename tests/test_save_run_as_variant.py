"""Tests for save_run_as_variant in vivarium_dashboard.lib.study_variants."""
import time
import yaml
from vivarium_dashboard.lib import composite_runs as cr
from vivarium_dashboard.lib import study_variants


def test_save_run_as_variant_appends_to_study_yaml(tmp_path):
    src = tmp_path / "composite-runs.db"
    conn = cr.connect(src)
    cr.save_metadata(conn, spec_id="pkg.composites.cell", run_id="r1",
                     params={"k": 5}, label="fast", started_at=time.time(), n_steps=3)
    sd = tmp_path / "studies" / "demo"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(
        "name: demo\nbaseline:\n  - {name: core, composite: pkg.composites.cell}\n"
    )
    body, status = study_variants.save_run_as_variant(
        tmp_path, run_id="r1", source_db=src, study="demo", variant_name="fast")
    assert status == 200 and body["composite"] == "pkg.composites.cell"
    spec = yaml.safe_load((sd / "study.yaml").read_text())
    var = [v for v in spec["variants"] if v["name"] == "fast"][0]
    assert var["composite"] == "pkg.composites.cell" and var["parameter_overrides"] == {"k": 5}
    # idempotent on name
    study_variants.save_run_as_variant(
        tmp_path, run_id="r1", source_db=src, study="demo", variant_name="fast")
    spec2 = yaml.safe_load((sd / "study.yaml").read_text())
    assert len([v for v in spec2["variants"] if v["name"] == "fast"]) == 1


def test_save_run_as_variant_missing_study_404(tmp_path):
    src = tmp_path / "r.db"
    cr.connect(src)
    body, status = study_variants.save_run_as_variant(
        tmp_path, run_id="x", source_db=src, study="nope", variant_name="v")
    assert status == 404


def test_save_run_as_variant_missing_run_404(tmp_path):
    src = tmp_path / "r.db"
    cr.connect(src)  # empty DB — no runs
    sd = tmp_path / "studies" / "demo"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text("name: demo\n")
    body, status = study_variants.save_run_as_variant(
        tmp_path, run_id="noexist", source_db=src, study="demo", variant_name="v")
    assert status == 404


def test_save_run_as_variant_v4_writes_conditions_variants(tmp_path):
    import time, yaml
    from vivarium_dashboard.lib import composite_runs as cr
    from vivarium_dashboard.lib import study_variants
    src = tmp_path / "r.db"; conn = cr.connect(src)
    cr.save_metadata(conn, spec_id="pkg.composites.cell", run_id="r1",
                     params={"k": 5}, label="fast", started_at=time.time(), n_steps=3)
    sd = tmp_path / "studies" / "demo"; sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 4, "name": "demo",
        "conditions": {"baseline": {"composite": "pkg.composites.cell"}, "variants": []},
    }))
    body, status = study_variants.save_run_as_variant(
        tmp_path, run_id="r1", source_db=src, study="demo", variant_name="fast")
    assert status == 200
    spec = yaml.safe_load((sd / "study.yaml").read_text())
    assert "variants" not in spec  # NOT written to the ignored top-level
    var = [v for v in spec["conditions"]["variants"] if v["name"] == "fast"][0]
    assert var["composite"] == "pkg.composites.cell" and var["parameter_overrides"] == {"k": 5}
