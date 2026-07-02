"""The aliased /api/study-* handlers must find studies in studies/, not just investigations/."""
import yaml
import pytest


@pytest.fixture
def _ws(tmp_path):
    """Workspace with one study in studies/ and one legacy investigation."""
    ws = tmp_path / "ws"
    # A v3 study under studies/
    sd = ws / "studies" / "new-study"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3, "name": "new-study", "created": "2026-05-14",
        "status": "ran", "objective": "obj",
        "baseline": {"composite": "pkg.foo", "params": {}},
        "variants": [], "runs": [], "visualizations": [],
        "conclusion": None, "parent_studies": [],
    }))
    # A legacy investigation under investigations/
    legacy = ws / "investigations" / "old-inv"
    legacy.mkdir(parents=True)
    (legacy / "spec.yaml").write_text(yaml.safe_dump({
        "schema_version": 2, "name": "old-inv", "created": "2026-04-01",
        "composites": [{"name": "main", "source": "pkg.bar"}],
    }))
    return ws


def test_study_dir_prefers_studies(_ws):
    from vivarium_workbench.lib.study_spec import study_dir
    d = study_dir(_ws, "new-study")
    assert d == _ws / "studies" / "new-study"


def test_study_dir_falls_back_to_investigations(_ws):
    from vivarium_workbench.lib.study_spec import study_dir
    d = study_dir(_ws, "old-inv")
    assert d == _ws / "investigations" / "old-inv"


def test_study_spec_path_picks_study_yaml(_ws):
    from vivarium_workbench.lib.study_spec import study_spec_path
    p = study_spec_path(_ws, "new-study")
    assert p.name == "study.yaml"
    assert p == _ws / "studies" / "new-study" / "study.yaml"


def test_study_spec_path_picks_spec_yaml_for_legacy(_ws):
    from vivarium_workbench.lib.study_spec import study_spec_path
    p = study_spec_path(_ws, "old-inv")
    assert p.name == "spec.yaml"
    assert p == _ws / "investigations" / "old-inv" / "spec.yaml"


def test_iter_study_dirs_includes_both(_ws):
    from vivarium_workbench.lib.investigations_index import _iter_study_dirs
    names = sorted(d.name for d in _iter_study_dirs(_ws))
    assert names == ["new-study", "old-inv"]


def test_iter_study_dirs_honors_nested_layout(tmp_path, monkeypatch):
    """A `layout:` block (nested workspace/ layout) must be honored: the study
    list reads the layout-resolved studies dir, not the hardcoded root.

    Regression for the empty-sidebar bug on nested-layout workspaces, where
    _iter_study_dirs walked WORKSPACE/studies (absent) instead of
    workspace/studies (declared via layout:)."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text(yaml.safe_dump({
        "name": "demo",
        "layout": {"studies": "workspace/studies"},
    }))
    sd = ws / "workspace" / "studies" / "nested-study"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3, "name": "nested-study", "created": "2026-06-12",
        "status": "ran", "objective": "obj",
        "baseline": {"composite": "pkg.foo", "params": {}},
        "variants": [], "runs": [], "visualizations": [],
        "conclusion": None, "parent_studies": [],
    }))
    from vivarium_workbench.lib.investigations_index import _iter_study_dirs
    names = sorted(d.name for d in _iter_study_dirs(ws))
    assert names == ["nested-study"]


def test_iter_study_dirs_flat_layout_still_works(tmp_path, monkeypatch):
    """No `layout:` block -> classic flat studies/<slug>/ is still discovered."""
    ws = tmp_path / "ws"
    sd = ws / "studies" / "flat-study"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3, "name": "flat-study", "created": "2026-06-12",
        "status": "ran", "objective": "obj",
        "baseline": {"composite": "pkg.foo", "params": {}},
        "variants": [], "runs": [], "visualizations": [],
        "conclusion": None, "parent_studies": [],
    }))
    from vivarium_workbench.lib.investigations_index import _iter_study_dirs
    names = sorted(d.name for d in _iter_study_dirs(ws))
    assert names == ["flat-study"]


def test_iter_study_dirs_includes_investigation_nested_studies(tmp_path, monkeypatch):
    """A study nested under investigations/<inv>/studies/<slug>/ (the v3
    investigation-collection layout, e.g. v2ecoli ketchup/pdmp/colonies) must be
    discovered. Regression: _iter_study_dirs used to yield the investigation dir
    itself (which holds investigation.yaml, not study.yaml) and dropped every
    nested study -> /api/investigations missing them entirely."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text(yaml.safe_dump({
        "name": "demo",
        "layout": {"investigations": "workspace/investigations"},
    }))
    inv = ws / "workspace" / "investigations" / "my-inv"
    inv.mkdir(parents=True)
    (inv / "investigation.yaml").write_text(yaml.safe_dump({
        "schema_version": 2, "name": "my-inv", "title": "T", "studies": ["nested"],
    }))
    sd = inv / "studies" / "nested"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 4, "name": "nested", "created": "2026-06-12",
        "status": "ran", "objective": "obj",
        "baseline": {"composite": "pkg.foo", "params": {}},
        "variants": [], "runs": [], "visualizations": [],
        "conclusion": None, "parent_studies": [],
    }))
    from vivarium_workbench.lib.investigations_index import _iter_study_dirs
    names = sorted(d.name for d in _iter_study_dirs(ws))
    # The nested study is found; the investigation collection dir is NOT
    # mistaken for a study.
    assert names == ["nested"]
