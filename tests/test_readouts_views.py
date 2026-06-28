from vivarium_dashboard.lib.readouts_views import _merge_readouts


AVAIL = {"leaves": [
    "agents.0.listeners.mass.instantaneous_growth_rate",
    "agents.0.listeners.mass.cell_mass",
]}


def _row_by_path(rows, path):
    return next(r for r in rows if r["store_path"] == path)


def test_emit_leaves_become_rows_with_short_names():
    rows = _merge_readouts({"readouts": []}, AVAIL)
    paths = {r["store_path"] for r in rows}
    assert "agents.0.listeners.mass.cell_mass" in paths
    r = _row_by_path(rows, "agents.0.listeners.mass.cell_mass")
    assert r["name"] == "cell_mass"
    assert r["emit_status"] == "emitted"
    assert r["annotated"] is False


def test_authored_annotation_matches_by_lineage_stripped_path():
    spec = {"readouts": [{
        "name": "instantaneous_growth_rate", "status": "available",
        "store_path": "listeners.mass.instantaneous_growth_rate",
        "description": "the screen metric", "units": "1/s",
    }]}
    rows = _merge_readouts(spec, AVAIL)
    r = _row_by_path(rows, "agents.0.listeners.mass.instantaneous_growth_rate")
    assert r["name"] == "instantaneous_growth_rate"
    assert r["annotated"] is True
    assert r["description"] == "the screen metric"
    assert r["units"] == "1/s"
    assert r["emit_status"] == "emitted"
    # no duplicate raw row for the same leaf
    assert sum(1 for x in rows
               if x["store_path"].endswith("instantaneous_growth_rate")) == 1


def test_authored_available_not_in_plan_is_orphan():
    spec = {"readouts": [{
        "name": "phantom", "status": "available",
        "store_path": "listeners.does_not_exist",
    }]}
    rows = _merge_readouts(spec, AVAIL)
    r = _row_by_path(rows, "listeners.does_not_exist")
    assert r["emit_status"] == "not_in_emit_plan"
    assert r["annotated"] is True


def test_derived_metric_without_store_path_is_exempt():
    spec = {"readouts": [{
        "name": "effective_knob_count", "status": "derived-needed",
        "notes": "computed analysis scalar",
    }]}
    rows = _merge_readouts(spec, AVAIL)
    r = next(r for r in rows if r["name"] == "effective_knob_count")
    assert r["emit_status"] == "derived"
    assert r["store_path"] == ""
    assert r["annotated"] is True


def test_available_authored_without_store_path_flagged():
    spec = {"readouts": [{"name": "needs_path", "status": "available"}]}
    rows = _merge_readouts(spec, AVAIL)
    r = next(r for r in rows if r["name"] == "needs_path")
    assert r["emit_status"] == "not_in_emit_plan"
    assert r["store_path"] == ""


def test_legacy_observables_key_overlays(monkeypatch):
    """Fix 3: authored annotations under ``observables:`` still match emit leaves."""
    spec = {"observables": [{
        "name": "cm",
        "status": "available",
        "store_path": "listeners.mass.cell_mass",
    }]}
    rows = _merge_readouts(spec, AVAIL)
    r = _row_by_path(rows, "agents.0.listeners.mass.cell_mass")
    assert r["annotated"] is True
    assert r["name"] == "cm"
    assert r["emit_status"] == "emitted"


def test_duplicate_store_path_neither_flagged_not_in_emit_plan():
    """Fix 4: two authored readouts sharing a store_path that IS an emit leaf →
    neither should appear as not_in_emit_plan."""
    spec = {"readouts": [
        {"name": "cm_first", "status": "available",
         "store_path": "listeners.mass.cell_mass"},
        {"name": "cm_second", "status": "available",
         "store_path": "listeners.mass.cell_mass"},
    ]}
    rows = _merge_readouts(spec, AVAIL)
    orphans = [r for r in rows if r["emit_status"] == "not_in_emit_plan"]
    assert orphans == [], (
        f"Expected no not_in_emit_plan rows for dup store_path covered by emit leaf, "
        f"got: {orphans}"
    )


def test_build_study_readouts_honors_nested_workspace_layout(tmp_path):
    """A workspace.yaml that nests studies under workspace/studies (v2ecoli
    layout) must still resolve the study — not 404 'study not found'."""
    import yaml as _yaml
    from vivarium_dashboard.lib.readouts_views import build_study_readouts

    (tmp_path / "workspace.yaml").write_text(_yaml.safe_dump({
        "name": "ws",
        "layout": {"studies": "workspace/studies",
                   "investigations": "workspace/investigations"},
    }))
    sd = tmp_path / "workspace" / "studies" / "demo"
    sd.mkdir(parents=True)
    # No baseline -> the worker returns 422 (found, but no composite), proving
    # the study was RESOLVED via the layout rather than 404'd.
    (sd / "study.yaml").write_text(_yaml.safe_dump({"name": "demo", "readouts": []}))

    body, status = build_study_readouts(tmp_path, "demo")
    assert status != 404, body
    assert body.get("error") != "study not found: demo"


def test_build_study_readouts_extracts_v4_conditions_baseline(tmp_path):
    """A schema_version 4 study carries its baseline composite under
    conditions.baseline.composite — the worker must project it (not 422 with
    'study has no baseline composite')."""
    import yaml as _yaml
    from vivarium_dashboard.lib.readouts_views import build_study_readouts

    sd = tmp_path / "studies" / "v4demo"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(_yaml.safe_dump({
        "schema_version": 4,
        "name": "v4demo",
        "conditions": {"baseline": {"composite": "some.composite.ref"}},
        "readouts": [],
    }))
    body, status = build_study_readouts(tmp_path, "v4demo")
    # The composite ref won't build here, but baseline extraction must succeed:
    assert body.get("error") != "study has no baseline composite", body
