"""PR-A: study readouts type/unit/shape-checked against the composite's contract.

available_observables gains a `types` map (per-leaf {type, units, min, max}),
and validate_readouts flags a DECLARED unit/shape disagreement (never fabricates
one when a port carries no units). The dimensional check reuses the same
core._compute_unit_scale primitive composite_audit already uses.
"""
from __future__ import annotations

from vivarium_workbench.lib.readout_validation import available_observables


class TestTypesMap:
    def test_types_carry_units_and_bounds(self):
        state = {"listeners": {"mass": 1.0, "flag": True}}
        schema = {"listeners": {
            "mass": {"_type": "float", "_units": "fg", "_min": 0},
            "flag": {"_type": "boolean"},
        }}
        res = available_observables(None, state, schema)
        assert "listeners.mass" in res["leaves"]
        t = res["types"]["listeners.mass"]
        assert t["type"] == "float" and t["units"] == "fg" and t["min"] == 0
        # a bare leaf has no units — reported as None/empty, never fabricated
        assert res["types"]["listeners.flag"].get("units") in (None, "")

    def test_types_empty_without_schema(self):
        res = available_observables(None, {"x": 1.0}, None)
        assert res.get("types", {}) == {}

    def test_existing_keys_additive(self):
        res = available_observables(None, {"listeners": {"mass": 1.0}},
                                    {"listeners": {"mass": {"_type": "float"}}})
        assert "leaves" in res and "catalogs" in res
