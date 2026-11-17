"""Handler tests for v3 study baseline CRUD."""
import yaml
import pytest


@pytest.fixture
def _study_ws(tmp_path):
    """Workspace with one v3 study with a single baseline entry."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text(
        'schema_version: 2\nname: ws\ncreated: "2026-05-14"\n'
        'plugin_version: 0.6.1\npackage_path: pkg\n'
    )
    sd = ws / "studies" / "s1"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3, "name": "s1",
        "baseline": [{"name": "core", "composite": "pkg.composites.foo", "params": {}}],
        "variants": [], "runs": [], "visualizations": [], "interventions": [],
    }))
    return ws


def test_baseline_add_appends(_study_ws):
    from vivarium_workbench.lib.study_crud_mutations import study_baseline_add as _post_study_baseline_add_for_test
    resp, code = _post_study_baseline_add_for_test(
        _study_ws,
        {"study": "s1", "name": "alt",
         "composite": "pkg.composites.bar", "params": {"k": 1}},
    )
    assert code == 200
    spec = yaml.safe_load((_study_ws / "studies" / "s1" / "study.yaml").read_text())
    assert spec["baseline"] == [
        {"name": "core", "composite": "pkg.composites.foo", "params": {}},
        {"name": "alt", "composite": "pkg.composites.bar", "params": {"k": 1}},
    ]


def test_baseline_add_preserves_comments(tmp_path):
    """Pointing a study at a composite from the GUI must not strip the
    scaffold's guide comments (issue #1618, bug 4 / #7)."""
    from vivarium_workbench.lib.study_crud_mutations import study_baseline_add
    ws = tmp_path / "ws"
    (ws / "studies" / "s1").mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    sf = ws / "studies" / "s1" / "study.yaml"
    sf.write_text(
        "# guide comment that must survive\n"
        "schema_version: 4\n"
        "name: s1\n"
        "baseline:\n"
        "  - name: core\n"
        "    composite: pkg.composites.foo\n"
        "    params: {}\n",
        encoding="utf-8",
    )
    resp, code = study_baseline_add(
        ws, {"study": "s1", "name": "alt", "composite": "pkg.composites.bar"},
    )
    assert code == 200
    text = sf.read_text(encoding="utf-8")
    assert "# guide comment that must survive" in text
    spec = yaml.safe_load(text)
    assert [b["name"] for b in spec["baseline"]] == ["core", "alt"]


def test_baseline_set_repoints_in_place(_study_ws):
    """study-baseline-set updates composite/params WITHOUT renaming the entry
    (issue #1618, blocker A — replaces the add-then-remove suffix dance)."""
    from vivarium_workbench.lib.study_crud_mutations import study_baseline_set
    resp, code = study_baseline_set(
        _study_ws,
        {"study": "s1", "name": "core",
         "composite": "pkg.composites.bar", "params": {"k": 2}},
    )
    assert code == 200
    assert resp == {"ok": True, "name": "core"}
    spec = yaml.safe_load((_study_ws / "studies" / "s1" / "study.yaml").read_text())
    assert spec["baseline"] == [
        {"name": "core", "composite": "pkg.composites.bar", "params": {"k": 2}},
    ]  # name unchanged, no <name>-<suffix> entry


def test_baseline_set_404_unknown_entry(_study_ws):
    from vivarium_workbench.lib.study_crud_mutations import study_baseline_set
    resp, code = study_baseline_set(
        _study_ws, {"study": "s1", "name": "nope", "composite": "pkg.composites.bar"},
    )
    assert code == 404


def test_baseline_set_preserves_comments(tmp_path):
    from vivarium_workbench.lib.study_crud_mutations import study_baseline_set
    ws = tmp_path / "ws"
    (ws / "studies" / "s1").mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    sf = ws / "studies" / "s1" / "study.yaml"
    sf.write_text(
        "# keep me\n"
        "schema_version: 4\n"
        "name: s1\n"
        "baseline:\n"
        "  - name: core\n"
        "    composite: replace_me.composites.placeholder\n"
        "    params: {}\n",
        encoding="utf-8",
    )
    resp, code = study_baseline_set(
        ws, {"study": "s1", "name": "core", "composite": "pkg.composites.real"},
    )
    assert code == 200
    text = sf.read_text(encoding="utf-8")
    assert "# keep me" in text
    spec = yaml.safe_load(text)
    assert spec["baseline"][0]["composite"] == "pkg.composites.real"
    assert spec["baseline"][0]["name"] == "core"


def test_baseline_add_default_empty_params(_study_ws):
    from vivarium_workbench.lib.study_crud_mutations import study_baseline_add as _post_study_baseline_add_for_test
    resp, code = _post_study_baseline_add_for_test(
        _study_ws,
        {"study": "s1", "name": "alt", "composite": "pkg.composites.bar"},
    )
    assert code == 200
    spec = yaml.safe_load((_study_ws / "studies" / "s1" / "study.yaml").read_text())
    alt = next(b for b in spec["baseline"] if b["name"] == "alt")
    assert alt["params"] == {}


def test_baseline_add_rejects_missing_composite(_study_ws):
    from vivarium_workbench.lib.study_crud_mutations import study_baseline_add as _post_study_baseline_add_for_test
    resp, code = _post_study_baseline_add_for_test(
        _study_ws,
        {"study": "s1", "name": "alt"},
    )
    assert code == 400
    assert "composite" in resp.get("error", "").lower()


def test_baseline_add_rejects_duplicate_name(_study_ws):
    from vivarium_workbench.lib.study_crud_mutations import study_baseline_add as _post_study_baseline_add_for_test
    resp, code = _post_study_baseline_add_for_test(
        _study_ws,
        {"study": "s1", "name": "core", "composite": "pkg.composites.other"},
    )
    assert code == 409
    assert "core" in resp.get("error", "")


def test_baseline_add_rejects_missing_name(_study_ws):
    from vivarium_workbench.lib.study_crud_mutations import study_baseline_add as _post_study_baseline_add_for_test
    resp, code = _post_study_baseline_add_for_test(
        _study_ws,
        {"study": "s1", "composite": "pkg.composites.other"},
    )
    assert code == 400


def test_baseline_remove_succeeds(_study_ws):
    """Removing a baseline entry that no variant references → 200."""
    from vivarium_workbench.lib.study_crud_mutations import (
        study_baseline_add as _post_study_baseline_add_for_test,
        study_baseline_remove as _post_study_baseline_remove_for_test,
    )
    _post_study_baseline_add_for_test(
        _study_ws, {"study": "s1", "name": "alt", "composite": "pkg.composites.bar"},
    )
    resp, code = _post_study_baseline_remove_for_test(
        _study_ws, {"study": "s1", "name": "alt"},
    )
    assert code == 200
    spec = yaml.safe_load((_study_ws / "studies" / "s1" / "study.yaml").read_text())
    assert [b["name"] for b in spec["baseline"]] == ["core"]


def test_baseline_remove_404_unknown(_study_ws):
    from vivarium_workbench.lib.study_crud_mutations import study_baseline_remove as _post_study_baseline_remove_for_test
    resp, code = _post_study_baseline_remove_for_test(
        _study_ws, {"study": "s1", "name": "ghost"},
    )
    assert code == 404


def test_baseline_remove_409_when_variant_references_it(_study_ws):
    """Refuses to remove a baseline entry that variants depend on."""
    from vivarium_workbench.lib.study_crud_mutations import (
        study_variant_add as _post_study_variant_add_for_test,
        study_baseline_remove as _post_study_baseline_remove_for_test,
    )
    _post_study_variant_add_for_test(
        _study_ws, {"study": "s1", "name": "fast", "base_composite": "core"},
    )
    resp, code = _post_study_baseline_remove_for_test(
        _study_ws, {"study": "s1", "name": "core"},
    )
    assert code == 409
    err = resp.get("error", "")
    assert "fast" in err  # error names the referencing variant(s)


def test_baseline_remove_400_when_would_be_empty(_study_ws):
    """Refuses to remove the last baseline entry."""
    from vivarium_workbench.lib.study_crud_mutations import study_baseline_remove as _post_study_baseline_remove_for_test
    resp, code = _post_study_baseline_remove_for_test(
        _study_ws, {"study": "s1", "name": "core"},
    )
    assert code == 400
    assert "empty" in resp.get("error", "").lower()
