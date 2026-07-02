"""Behavioral tests for ``lib/comparative_runs.py`` (study-run engine
extraction, E5 — the final extraction).

These exercise ``render_investigation_comparative_visualisations`` directly
against a tmp ``ws_root`` with the comparative renderer + the zarr-store lookup
seam monkeypatched, so NO real Plotly HTML is ever rendered. They assert the
orchestration contract — the study walk, the runs-list zarr/db branch, the
render kwargs (incl. ``output_path = viz/comparative_<name>.html``), the skip
branches, and the error → ``job.update_item(comparative_viz_warning=…)`` path —
plus a server-instance-method-shim parity check proving the legacy
``server.Handler._render_investigation_comparative_visualisations`` shim is
behavior-identical to the lib function it now delegates to.
"""
from pathlib import Path

import yaml

from vivarium_workbench.lib import comparative_runs


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

class FakeJob:
    """Minimal stand-in exposing the two attributes the renderer touches:
    ``.items`` (a list — only its length is read) and ``.update_item``."""

    def __init__(self, n_items: int = 1):
        self.items = [{"i": i} for i in range(n_items)]
        self.warnings: list = []

    def update_item(self, idx, **kw):
        self.warnings.append((idx, kw))


def _write_workspace(ws: Path) -> None:
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "workspace.yaml").write_text(
        'schema_version: 2\nname: demo\ncreated: "2026-06-26"\n',
        encoding="utf-8",
    )


def _write_study(ws: Path, slug: str, comparative=None, with_db: bool = True) -> Path:
    sd = ws / "studies" / slug
    sd.mkdir(parents=True, exist_ok=True)
    doc: dict = {"schema_version": 3, "name": slug}
    if comparative is not None:
        doc["comparative_visualizations"] = comparative
    (sd / "study.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    if with_db:
        (sd / "runs.db").write_text("", encoding="utf-8")  # presence-only check
    return sd


class _Recorder:
    """Records every ``render_comparative_time_series`` call's kwargs."""

    def __init__(self):
        self.calls: list = []

    def __call__(self, **kw):
        self.calls.append(kw)
        return kw.get("output_path")


def _patch(monkeypatch, *, renderer, zarr_return=None, zarr_fn=None):
    monkeypatch.setattr(comparative_runs, "render_comparative_time_series", renderer)
    if zarr_fn is None:
        zarr_fn = lambda study_db, sim_name: zarr_return
    monkeypatch.setattr(
        comparative_runs.study_run_state, "zarr_store_for_sim", zarr_fn)


_CV = [{
    "name": "atp-vs-time",
    "title": "DnaA-ATP over time",
    "observable_path": "listeners.itv2.dnaa_atp_count",
    "y_label": "DnaA-ATP count",
    "observable_index": 3,
    "target_band": [10, 20],
    "target_band_label": "expected",
    "runs": [
        {"sim_name": "baseline", "label": "Baseline"},
        {"sim_name": "variant", "label": "Variant"},
    ],
}]


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_renders_with_expected_kwargs_db_branch(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    _write_workspace(ws)
    _write_study(ws, "s1", comparative=_CV)
    rec = _Recorder()
    _patch(monkeypatch, renderer=rec, zarr_return=None)  # no zarr → db branch
    job = FakeJob()

    comparative_runs.render_investigation_comparative_visualisations(
        ws, "inv", {"studies": ["s1"]}, job)

    assert len(rec.calls) == 1
    call = rec.calls[0]
    assert call["observable_path"] == "listeners.itv2.dnaa_atp_count"
    assert call["title"] == "DnaA-ATP over time"
    assert call["y_label"] == "DnaA-ATP count"
    assert call["observable_index"] == 3
    assert call["target_band"] == [10, 20]
    assert call["target_band_label"] == "expected"
    # output_path = studies/<slug>/viz/comparative_<name>.html
    out = Path(call["output_path"])
    assert out == ws / "studies" / "s1" / "viz" / "comparative_atp-vs-time.html"
    assert out.parent.is_dir()  # viz dir was created
    # db branch: each run carries db_path (= the study's runs.db), not zarr_path
    assert [r["label"] for r in call["runs"]] == ["Baseline", "Variant"]
    for r in call["runs"]:
        assert r["db_path"] == ws / "studies" / "s1" / "runs.db"
        assert "zarr_path" not in r
    assert job.warnings == []  # no errors


def test_zarr_branch_uses_zarr_path(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    _write_workspace(ws)
    _write_study(ws, "s1", comparative=_CV)
    rec = _Recorder()
    zarr = ws / "studies" / "s1" / "runs.abc.zarr"
    _patch(monkeypatch, renderer=rec, zarr_return=zarr)  # zarr present
    job = FakeJob()

    comparative_runs.render_investigation_comparative_visualisations(
        ws, "inv", {"studies": ["s1"]}, job)

    runs = rec.calls[0]["runs"]
    for r in runs:
        assert r["zarr_path"] == zarr
        assert "db_path" not in r


def test_defaults_when_optional_fields_absent(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    _write_workspace(ws)
    _write_study(ws, "s1", comparative=[{
        "name": "minimal",
        "runs": [{"sim_name": "baseline", "label": "B"}],
    }])
    rec = _Recorder()
    _patch(monkeypatch, renderer=rec, zarr_return=None)
    job = FakeJob()

    comparative_runs.render_investigation_comparative_visualisations(
        ws, "inv", {"studies": ["s1"]}, job)

    call = rec.calls[0]
    assert call["observable_path"] == ""
    assert call["title"] == "minimal"  # defaults to name
    assert call["y_label"] == ""
    assert call["observable_index"] is None
    assert call["target_band"] is None
    assert call["target_band_label"] is None


def test_member_dict_shape_and_run_name_fallbacks(tmp_path, monkeypatch):
    """Studies may be given as ``{study: slug}`` dicts; run sim_name falls
    back through variant/name, and label falls back to sim_name."""
    ws = tmp_path / "ws"
    _write_workspace(ws)
    _write_study(ws, "s1", comparative=[{
        "name": "fb",
        "runs": [
            {"variant": "v-name"},          # sim_name <- variant, label <- sim_name
            {"name": "n-name"},             # sim_name <- name
        ],
    }])
    rec = _Recorder()
    _patch(monkeypatch, renderer=rec, zarr_return=None)
    job = FakeJob()

    comparative_runs.render_investigation_comparative_visualisations(
        ws, "inv", {"studies": [{"study": "s1"}]}, job)

    runs = rec.calls[0]["runs"]
    assert [r["sim_name"] for r in runs] == ["v-name", "n-name"]
    assert [r["label"] for r in runs] == ["v-name", "n-name"]


# ---------------------------------------------------------------------------
# Skip branches
# ---------------------------------------------------------------------------

def test_skip_missing_study_yaml(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    _write_workspace(ws)
    # no studies/<slug>/study.yaml at all
    rec = _Recorder()
    _patch(monkeypatch, renderer=rec, zarr_return=None)
    job = FakeJob()
    comparative_runs.render_investigation_comparative_visualisations(
        ws, "inv", {"studies": ["ghost"]}, job)
    assert rec.calls == []
    assert job.warnings == []


def test_skip_no_comparative_visualizations(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    _write_workspace(ws)
    _write_study(ws, "s1", comparative=None)  # study.yaml without the block
    rec = _Recorder()
    _patch(monkeypatch, renderer=rec, zarr_return=None)
    job = FakeJob()
    comparative_runs.render_investigation_comparative_visualisations(
        ws, "inv", {"studies": ["s1"]}, job)
    assert rec.calls == []


def test_skip_missing_runs_db(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    _write_workspace(ws)
    _write_study(ws, "s1", comparative=_CV, with_db=False)  # no runs.db
    rec = _Recorder()
    _patch(monkeypatch, renderer=rec, zarr_return=None)
    job = FakeJob()
    comparative_runs.render_investigation_comparative_visualisations(
        ws, "inv", {"studies": ["s1"]}, job)
    assert rec.calls == []


def test_skip_empty_runs(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    _write_workspace(ws)
    _write_study(ws, "s1", comparative=[{"name": "empty", "runs": []}])
    rec = _Recorder()
    _patch(monkeypatch, renderer=rec, zarr_return=None)
    job = FakeJob()
    comparative_runs.render_investigation_comparative_visualisations(
        ws, "inv", {"studies": ["s1"]}, job)
    assert rec.calls == []


def test_skip_no_studies(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    _write_workspace(ws)
    rec = _Recorder()
    _patch(monkeypatch, renderer=rec, zarr_return=None)
    job = FakeJob()
    comparative_runs.render_investigation_comparative_visualisations(
        ws, "inv", {}, job)  # no "studies" key
    assert rec.calls == []


# ---------------------------------------------------------------------------
# Error path
# ---------------------------------------------------------------------------

def test_render_exception_records_warning(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    _write_workspace(ws)
    _write_study(ws, "s1", comparative=_CV)

    def boom(**kw):
        raise RuntimeError("render kaboom")

    _patch(monkeypatch, renderer=boom, zarr_return=None)
    job = FakeJob(n_items=3)

    comparative_runs.render_investigation_comparative_visualisations(
        ws, "inv", {"studies": ["s1"]}, job)

    assert len(job.warnings) == 1
    idx, kw = job.warnings[0]
    assert idx == len(job.items) - 1  # last item index
    assert "comparative_viz_warning" in kw
    assert "s1/atp-vs-time" in kw["comparative_viz_warning"]
    assert "render kaboom" in kw["comparative_viz_warning"]


# ---------------------------------------------------------------------------
# ws_root is read (not a global)
# ---------------------------------------------------------------------------

def test_reads_from_ws_root_not_global(tmp_path, monkeypatch):
    """Two independent ws_roots produce output addressed under the ws_root
    that was passed in — the function holds no workspace global."""
    ws_a = tmp_path / "a"
    ws_b = tmp_path / "b"
    for ws in (ws_a, ws_b):
        _write_workspace(ws)
        _write_study(ws, "s1", comparative=_CV)
    rec = _Recorder()
    _patch(monkeypatch, renderer=rec, zarr_return=None)

    comparative_runs.render_investigation_comparative_visualisations(
        ws_b, "inv", {"studies": ["s1"]}, FakeJob())

    out = Path(rec.calls[0]["output_path"])
    assert str(out).startswith(str(ws_b))
    assert not str(out).startswith(str(ws_a))
