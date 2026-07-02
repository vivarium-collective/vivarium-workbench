"""The committed-artifact fallback for generator default state.

When a generator declares no ``default_state_ref``, ``CompositeSpec.default_state``
returns None and the Composite Explorer shows "not generated yet". The resolver
falls back to the regen script's committed artifact at
``reports/composite-state/<id>.json`` so every generator's wiring renders without
per-generator annotation.
"""
import json

from vivarium_workbench.lib.composite_resolve import _committed_default_state


def _write_artifact(tmp_path, cid, payload):
    d = tmp_path / "reports" / "composite-state"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{cid}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_fallback_reads_committed_state(tmp_path):
    _write_artifact(tmp_path, "v2ecoli.composites.baseline",
                    {"state": {"agents": {"0": {}}, "global_time": 0.0}})
    st = _committed_default_state(tmp_path, "v2ecoli.composites.baseline")
    assert isinstance(st, dict) and "agents" in st and "global_time" in st


def test_fallback_missing_file_returns_none(tmp_path):
    assert _committed_default_state(tmp_path, "v2ecoli.composites.nope") is None


def test_fallback_malformed_json_returns_none(tmp_path):
    d = tmp_path / "reports" / "composite-state"
    d.mkdir(parents=True)
    (d / "bad.json").write_text("{not json", encoding="utf-8")
    assert _committed_default_state(tmp_path, "bad") is None


def test_fallback_artifact_without_state_key_returns_none(tmp_path):
    _write_artifact(tmp_path, "x", {"wiring_status": "unavailable"})
    assert _committed_default_state(tmp_path, "x") is None


def test_fallback_non_dict_state_returns_none(tmp_path):
    _write_artifact(tmp_path, "x", {"state": "not-a-dict"})
    assert _committed_default_state(tmp_path, "x") is None
