"""Tests for composite_run_views.build_run_artifact's zarr-native Viz/Report
fallback — real xarray/zarr I/O throughout (no mocking of the reader layer),
exercising the actual code path a GovCloud/Ray-dispatched run hits.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from vivarium_workbench.lib.composite_runs import connect, save_metadata
from vivarium_workbench.lib.composite_run_views import build_run_artifact


def make_fake_zarr(store_path, n_steps=4):
    pytest.importorskip("xarray")
    import xarray as xr

    emit = list(range(n_steps))
    part = xr.Dataset({"time_gen=1": ("emitstep_gen=1", [float(s) for s in emit])})
    mass = xr.Dataset({"generation=1": ("emitstep_gen=1", [100.0 + s for s in emit])})
    dt = xr.DataTree.from_dict({
        "experiment_id=e/variant=0/lineage_seed=0": part,
        "experiment_id=e/variant=0/lineage_seed=0/cell_mass": mass,
    })
    dt.to_zarr(str(store_path), mode="w")


def _seed_zarr_run(ws: Path, *, run_id: str, store_path: Path):
    (ws / ".pbg").mkdir(parents=True, exist_ok=True)
    conn = connect(ws / ".pbg" / "composite-runs.db")
    save_metadata(conn, spec_id="pkg.batch_baseline", run_id=run_id,
                  params={"store_path": str(store_path)}, label="",
                  started_at=10.0, n_steps=3, log_path=None)
    conn.close()


@pytest.mark.parametrize("name", ["viz", "report"])
def test_falls_back_to_zarr_render_when_local_file_absent(tmp_path, name):
    """No .pbg/runs/<run_id>/viz.json or report.html was ever written (this
    run never executed locally) — but its store_path resolves to a real zarr
    store, so the artifact renders real data instead of 404ing."""
    ws = tmp_path
    store = ws / "runs.r1.zarr"
    make_fake_zarr(store)
    _seed_zarr_run(ws, run_id="r1", store_path=store)

    content, media_type, download_name, status = build_run_artifact(ws, "r1", name)
    assert status == 200
    assert media_type == "text/html"
    html = content.decode("utf-8")
    assert "cell_mass" in html
    assert "Plotly.newPlot" in html


def test_report_fallback_notes_no_formal_report_card(tmp_path):
    ws = tmp_path
    store = ws / "runs.r2.zarr"
    make_fake_zarr(store)
    _seed_zarr_run(ws, run_id="r2", store_path=store)

    content, _mt, _dl, status = build_run_artifact(ws, "r2", "report")
    assert status == 200
    assert "no formal report card was generated" in content.decode("utf-8")


def test_unknown_run_with_no_local_file_still_404s(tmp_path):
    (tmp_path / ".pbg").mkdir(parents=True)
    content, _mt, _dl, status = build_run_artifact(tmp_path, "no-such-run", "viz")
    assert status == 404
    assert content == b""


def test_falls_back_through_sms_api_for_s3_only_run(tmp_path, monkeypatch):
    """The full chain for a GovCloud-dispatched run: store_path is s3://
    (nothing local), remote_origin carries a simulation_id, so the fallback
    fetches a tar.gz from sms-api (mocked), extracts it, and renders real
    data from the extracted zarr store — not just a local-store shortcut.

    Row shaped exactly like remote_simulations.py's _normalize() output (a
    run known to sms-api but never landed into a local runs.db): db_path is
    None, store_path is the s3:// URI. Seeding via a real local sqlite db
    instead would give db_path a real (local) path to that db file itself,
    masking the remote-fallback path entirely — see test_simulations_index.py
    for the same lesson learned on build_simulation_run_zip's tests."""
    import tarfile

    from vivarium_workbench.lib import composite_run_views as crv
    from vivarium_workbench.lib import simulations_index as si

    ws = tmp_path
    (ws / ".pbg").mkdir(parents=True)
    row = {
        "run_id": "r4", "spec_id": "", "sim_name": "r4", "label": "r4",
        "status": "completed", "n_steps": None, "progress_step": None,
        "started_at": 10.0, "completed_at": 10.0,
        "db_path": None, "store_path": "s3://bucket/vecoli-output/exp-113",
        "emitter": "xarray", "studies": [], "study_slug": None,
        "investigation_slug": None,
        "remote_origin": {"deployment": "build #50", "simulation_id": 113,
                           "experiment_id": "r4", "backend": "aws",
                           "s3_uri": "s3://bucket/vecoli-output/exp-113"},
    }
    monkeypatch.setattr(si, "list_simulations", lambda workspace: [row])

    zarr_src = tmp_path / "src.zarr"
    make_fake_zarr(zarr_src)
    tar_path = tmp_path / "fake.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tf:
        tf.add(zarr_src, arcname="exp-113/store.zarr")

    def _fake_download_data(self, simulation_id, dest_dir, timeout=None):
        out = Path(dest_dir) / f"sim_{simulation_id}.tar.gz"
        out.write_bytes(tar_path.read_bytes())
        return out

    monkeypatch.setattr(
        "vivarium_workbench.lib.sms_api_client.SmsApiClient.download_data",
        _fake_download_data,
    )

    content, _mt, _dl, status = crv.build_run_artifact(ws, "r4", "viz")
    assert status == 200
    html = content.decode("utf-8")
    assert "cell_mass" in html
    assert "Plotly.newPlot" in html


def test_local_viz_json_present_is_unaffected_by_the_fallback(tmp_path):
    """A run WITH a real local viz.json (the ordinary local-execution case)
    must render from that file, not fall through to the zarr path at all —
    confirms the fallback only fires when the local file is genuinely absent."""
    ws = tmp_path
    run_dir = ws / ".pbg" / "runs" / "r3"
    run_dir.mkdir(parents=True)
    (run_dir / "viz.json").write_text('{"my_viz": "<p>hand-rendered</p>"}')

    content, _mt, _dl, status = build_run_artifact(ws, "r3", "viz")
    assert status == 200
    assert b"hand-rendered" in content
    assert b"Plotly.newPlot" not in content


_NO_OUTPUT = "No output was rendered for this panel"


@pytest.mark.parametrize(
    ("value", "shown"),
    [
        # inline Visualization steps: render_results -> {"html": ...}, stored as-is by run_runner
        ({"html": "<p>dict-panel</p>"}, "<p>dict-panel</p>"),
        ({"html": "<p>dict-extra-keys</p>", "kind": "gif"}, "<p>dict-extra-keys</p>"),
        # the documented shape (run_viz): a bare html string
        ("<p>string-panel</p>", "<p>string-panel</p>"),
        # nothing to show: say so, never the repr
        ({"html": ""}, _NO_OUTPUT),
        ({"html": None}, _NO_OUTPUT),
        ({"html": "   "}, _NO_OUTPUT),
        ("", _NO_OUTPUT),
    ],
    ids=["dict", "dict-extra-keys", "string", "dict-empty", "dict-none", "dict-blank", "string-empty"],
)
def test_viz_panel_value_shapes(tmp_path, value, shown):
    run_dir = tmp_path / ".pbg" / "runs" / "rs"
    run_dir.mkdir(parents=True)
    (run_dir / "viz.json").write_text(json.dumps({"panel": value}))

    content, _mt, _dl, status = build_run_artifact(tmp_path, "rs", "viz")
    page = content.decode("utf-8")
    assert status == 200
    assert shown in page
    assert "{'html'" not in page and "'html':" not in page   # no Python repr leaks
    if shown != _NO_OUTPUT:
        assert _NO_OUTPUT not in page                         # a real panel gets no notice


def test_viz_json_html_dict_values_render_as_html_not_a_python_repr(tmp_path):
    """The failing file from workbench #1577: ``render_results`` gives ``{name: {"html":
    "<img ...>"}}`` for inline viz steps while run_viz writes ``{name: html}``, and one
    viz.json can hold both. The page must embed the HTML either way, never ``str(dict)``;
    a panel with no html says so."""
    ws = tmp_path
    run_dir = ws / ".pbg" / "runs" / "r5"
    run_dir.mkdir(parents=True)
    (run_dir / "viz.json").write_text(json.dumps({
        "multibody_viz": {"html": '<img alt="colony-gif" src="data:image/gif;base64,R0lGODlh">'},
        "cell_mass_traces": {"html": ""},
        "string_shaped": "<p>plain-string-panel</p>",
    }))

    content, _mt, _dl, status = build_run_artifact(ws, "r5", "viz")
    page = content.decode("utf-8")
    assert status == 200
    assert '<img alt="colony-gif"' in page          # the dict's html is embedded
    assert "plain-string-panel" in page             # the documented string shape still works
    assert "{'html'" not in page                    # no Python repr leaks into the page
    assert "No output was rendered for this panel" in page  # the empty panel says so
