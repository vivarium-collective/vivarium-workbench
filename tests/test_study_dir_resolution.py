"""The aliased /api/study-* handlers must find studies in studies/, not just investigations/."""
import yaml
import pytest


@pytest.fixture
def _ws(tmp_path, monkeypatch):
    """Workspace with one study in studies/ and one legacy investigation."""
    import vivarium_dashboard.server as srv
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
    monkeypatch.setattr(srv, "WORKSPACE", ws)
    return ws


def test_study_dir_prefers_studies(_ws):
    from vivarium_dashboard.server import _study_dir
    d = _study_dir("new-study")
    assert d == _ws / "studies" / "new-study"


def test_study_dir_falls_back_to_investigations(_ws):
    from vivarium_dashboard.server import _study_dir
    d = _study_dir("old-inv")
    assert d == _ws / "investigations" / "old-inv"


def test_study_spec_path_picks_study_yaml(_ws):
    from vivarium_dashboard.server import _study_spec_path
    p = _study_spec_path("new-study")
    assert p.name == "study.yaml"
    assert p == _ws / "studies" / "new-study" / "study.yaml"


def test_study_spec_path_picks_spec_yaml_for_legacy(_ws):
    from vivarium_dashboard.server import _study_spec_path
    p = _study_spec_path("old-inv")
    assert p.name == "spec.yaml"
    assert p == _ws / "investigations" / "old-inv" / "spec.yaml"


def test_iter_study_dirs_includes_both(_ws):
    from vivarium_dashboard.server import _iter_study_dirs
    names = sorted(d.name for d in _iter_study_dirs())
    assert names == ["new-study", "old-inv"]


def test_iter_study_dirs_honors_nested_layout(tmp_path, monkeypatch):
    """A `layout:` block (nested workspace/ layout) must be honored: the study
    list reads the layout-resolved studies dir, not the hardcoded root.

    Regression for the empty-sidebar bug on nested-layout workspaces, where
    _iter_study_dirs walked WORKSPACE/studies (absent) instead of
    workspace/studies (declared via layout:)."""
    import vivarium_dashboard.server as srv
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
    monkeypatch.setattr(srv, "WORKSPACE", ws)
    names = sorted(d.name for d in srv._iter_study_dirs())
    assert names == ["nested-study"]


def test_iter_study_dirs_flat_layout_still_works(tmp_path, monkeypatch):
    """No `layout:` block -> classic flat studies/<slug>/ is still discovered."""
    import vivarium_dashboard.server as srv
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
    monkeypatch.setattr(srv, "WORKSPACE", ws)
    names = sorted(d.name for d in srv._iter_study_dirs())
    assert names == ["flat-study"]
