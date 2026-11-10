"""The per-process mtime-keyed spec-YAML cache (study_spec.read_spec_yaml_cached).

An investigation page re-read each member study.yaml ~8× across the status / figures /
load_spec / acceptance builders; routing them through one mtime-keyed cache parses each
file once (measured: build_iset_detail 4.4s -> 1.1s, summaries 2.5s -> 0.7s). This pins the
two correctness properties the cache must hold: returned copies are independent (callers may
mutate), and an edit (mtime bump) invalidates.
"""
import time

from vivarium_workbench.lib.study_spec import read_spec_yaml_cached


def test_returns_deep_copy_so_mutation_does_not_leak(tmp_path):
    f = tmp_path / "study.yaml"
    f.write_text("name: a\nstatus: planned\nruns: [{name: r1}]\n")
    a = read_spec_yaml_cached(f)
    a["status"] = "ran"
    a["runs"][0]["name"] = "mutated"   # nested mutation too
    b = read_spec_yaml_cached(f)
    assert b["status"] == "planned"
    assert b["runs"][0]["name"] == "r1"


def test_mtime_bump_invalidates(tmp_path):
    f = tmp_path / "study.yaml"
    f.write_text("name: a\nstatus: planned\n")
    assert read_spec_yaml_cached(f)["status"] == "planned"
    time.sleep(0.01)
    f.write_text("name: a\nstatus: ran\n")
    assert read_spec_yaml_cached(f)["status"] == "ran"


def test_missing_or_malformed_returns_empty_dict(tmp_path):
    assert read_spec_yaml_cached(tmp_path / "nope.yaml") == {}
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: a\n  : : bad indent :\n")
    assert read_spec_yaml_cached(bad) == {}
    nonmap = tmp_path / "list.yaml"
    nonmap.write_text("- a\n- b\n")       # a YAML list, not a mapping
    assert read_spec_yaml_cached(nonmap) == {}


def test_study_yaml_run_rows_uses_cache_and_invalidates(tmp_path):
    """_study_yaml_run_rows (called ~2x/study by _study_has_active_run across the whole
    investigations list) now reads study.yaml through the mtime cache — the dominant
    /api/investigations cost. It still reflects an edit (mtime bump)."""
    import yaml
    from vivarium_workbench.lib import study_spec as ss
    sd = tmp_path / "studies" / "s1"
    sd.mkdir(parents=True)
    (tmp_path / "workspace.yaml").write_text("name: w\n")
    (sd / "study.yaml").write_text(yaml.safe_dump({"name": "s1", "runs": [{"name": "r1"}]}))
    assert len(ss._study_yaml_run_rows(tmp_path, "s1")) == 1
    import time as _t; _t.sleep(0.01)
    (sd / "study.yaml").write_text(yaml.safe_dump({"name": "s1", "runs": []}))
    assert ss._study_yaml_run_rows(tmp_path, "s1") == []
