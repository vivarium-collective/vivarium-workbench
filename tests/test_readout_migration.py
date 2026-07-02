from vivarium_workbench.lib.readout_migration import lift_store_paths


def test_lifts_leading_dotted_path_from_notes():
    spec = {"readouts": [{
        "name": "instantaneous_growth_rate",
        "notes": "listeners.mass.instantaneous_growth_rate — % change low→high.",
    }]}
    out, n = lift_store_paths(spec)
    assert n == 1
    r = out["readouts"][0]
    assert r["store_path"] == "listeners.mass.instantaneous_growth_rate"
    assert r["notes"].startswith("listeners.mass.instantaneous_growth_rate")  # notes kept


def test_idempotent_and_skips_existing_store_path():
    spec = {"readouts": [{"name": "x", "store_path": "a.b", "notes": "c.d foo"}]}
    out, n = lift_store_paths(spec)
    assert n == 0
    assert out["readouts"][0]["store_path"] == "a.b"


def test_skips_derived_metric_without_dotted_notes():
    spec = {"readouts": [{
        "name": "effective_knob_count", "status": "derived-needed",
        "notes": "Number of candidates with >2% response (measured 3).",
    }]}
    out, n = lift_store_paths(spec)
    assert n == 0
    assert "store_path" not in out["readouts"][0]
