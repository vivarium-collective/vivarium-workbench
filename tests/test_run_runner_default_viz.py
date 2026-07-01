"""Tests for the default observables-over-time fallback in _render_viz."""
import json
import tempfile
from pathlib import Path

from vivarium_dashboard.lib import run_runner


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
