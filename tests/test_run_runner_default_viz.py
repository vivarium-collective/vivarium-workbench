"""Tests for the default observables-over-time fallback in _render_viz."""
import json
import tempfile
from pathlib import Path

from vivarium_dashboard.lib import run_runner


def test_numeric_observables_filters_correctly():
    """_numeric_observables keeps numeric non-"time" keys, drops the rest."""
    gathered_filtered = {
        "by_sim": {
            "default": [
                {
                    "run_id": "r1",
                    "sim_name": "default",
                    "params": {},
                    "observables": {
                        "time": [0.0, 1.0, 2.0],        # excluded: "time"
                        "mass": [1.5, 1.6, 1.7],         # included: numeric
                        "growth_rate": [0.01, 0.02],      # included: numeric
                        "label": ["a", "b", "c"],         # excluded: non-numeric
                        "mixed": [1.0, "bad", 3.0],       # included: has numeric
                        "empty": [],                      # excluded: no values
                    },
                }
            ]
        },
        "schemas": {},
    }
    result = run_runner._numeric_observables(gathered_filtered)
    assert result == ["growth_rate", "mass", "mixed"], (
        f"Expected sorted numeric non-time keys, got: {result}"
    )
    assert "time" not in result
    assert "label" not in result
    assert "empty" not in result


def test_numeric_observables_empty_gathered():
    """Returns [] when gathered_filtered has no runs."""
    assert run_runner._numeric_observables({}) == []
    assert run_runner._numeric_observables({"by_sim": {}}) == []


def test_default_viz_synthesized_when_empty(monkeypatch):
    """When inline + canonical produce nothing, _render_viz falls back to the
    default observables-over-time figure and writes a non-empty viz.json."""
    monkeypatch.setattr(
        run_runner, "_render_default_viz",
        lambda **kw: {"observables_over_time": "<div>FIG</div>"},
    )
    monkeypatch.setattr(run_runner, "_render_canonical_viz", lambda **kw: {})

    with tempfile.TemporaryDirectory() as d:
        run_dir = Path(d)
        run_runner._render_viz(
            composite=None, run_dir=run_dir,
            spec_id="x", db_file="db", run_id="r", core=object(),
        )
        viz = json.loads((run_dir / "viz.json").read_text())

    assert "observables_over_time" in viz, (
        f"Expected default viz key in viz.json, got keys: {list(viz)}"
    )
    content = viz["observables_over_time"]
    if isinstance(content, dict):
        assert "FIG" in content.get("html", ""), (
            f"Expected 'FIG' in html field, got: {content}"
        )
    else:
        assert "FIG" in content, (
            f"Expected 'FIG' in content string, got: {content!r}"
        )
