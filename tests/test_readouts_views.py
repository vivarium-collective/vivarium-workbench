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
