"""Unit tests for lib.investigation_figures (Investigation Figures feature)."""
import io
import zipfile

import yaml

from vivarium_workbench.lib import investigation_figures as figs


def _write(p, content):
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        p.write_bytes(content)
    else:
        p.write_text(content, encoding="utf-8")


def _make_ws(tmp_path, *, figures_block=None):
    """Minimal workspace: investigation 'inv' with two figure studies.

    fig-07 has a panel + a stitched composite (figure_7.svg + .png sibling);
    fig-99 has a panel but NO composite (exercises the panel-only path)."""
    inv = {"name": "inv", "title": "Inv", "studies": ["fig-07", "fig-99"]}
    if figures_block is not None:
        inv["figures"] = figures_block
    _write(tmp_path / "investigations" / "inv" / "investigation.yaml", yaml.safe_dump(inv))

    _write(tmp_path / "studies" / "fig-07" / "study.yaml", yaml.safe_dump({
        "name": "fig-07", "title": "Fig 7 — example", "claim": "seven works",
        "visualizations": [
            {"name": "7a", "address": "image:visualizations/panel-a.svg", "chart": "image"},
            {"name": "Figure 7 (composite)",
             "address": "image:visualizations/figure_7.svg", "chart": "image"},
        ],
    }))
    vd7 = tmp_path / "studies" / "fig-07" / "visualizations"
    _write(vd7 / "panel-a.svg", "<svg/>")
    _write(vd7 / "figure_7.svg", "<svg>composite7</svg>")
    _write(vd7 / "figure_7.png", b"\x89PNG\r\n\x1a\nfake")

    _write(tmp_path / "studies" / "fig-99" / "study.yaml", yaml.safe_dump({
        "name": "fig-99", "title": "Fig 99",
        "visualizations": [
            {"name": "99a", "address": "image:visualizations/only.svg", "chart": "image"},
        ],
    }))
    _write(tmp_path / "studies" / "fig-99" / "visualizations" / "only.svg", "<svg/>")
    return tmp_path


def test_autodiscovers_composites_and_panels(tmp_path):
    r = figs.build_investigation_figures(_make_ws(tmp_path), "inv")
    assert r["n_composites"] == 1
    c = r["composites"][0]
    assert c["study"] == "fig-07"
    assert c["number"] == 7                 # parsed from figure_7
    assert c["title"] == "Fig 7 — example"
    assert c["caption"] == "seven works"    # from study.claim
    assert c["png_rel"] and c["png_rel"].endswith("figure_7.png")  # sibling picked up
    # files = every panel + composite across BOTH member studies
    arcs = {f["arcname"] for f in r["files"]}
    assert arcs == {"fig-07/panel-a.svg", "fig-07/figure_7.svg", "fig-99/only.svg"}


def test_relative_ws_root_resolves(tmp_path, monkeypatch):
    # publish.py passes ws_root='.'; absolute-vs-relative relative_to() must not
    # silently drop every file (the bug that made snapshot staging emit nothing).
    _make_ws(tmp_path)
    monkeypatch.chdir(tmp_path)
    r = figs.build_investigation_figures(".", "inv")
    assert r["n_composites"] == 1
    assert len(r["files"]) == 3


def test_overrides_number_caption_order(tmp_path):
    ws = _make_ws(tmp_path, figures_block=[
        {"study": "fig-07", "number": 2, "caption": "custom", "order": 5},
    ])
    c = figs.build_investigation_figures(ws, "inv")["composites"][0]
    assert (c["number"], c["caption"], c["order"]) == (2, "custom", 5)


def test_include_false_hides_study(tmp_path):
    ws = _make_ws(tmp_path, figures_block=[{"study": "fig-07", "include": False}])
    r = figs.build_investigation_figures(ws, "inv")
    assert r["n_composites"] == 0
    assert all(f["study"] != "fig-07" for f in r["files"])  # its panels drop too


def test_investigation_zip_contents(tmp_path):
    blob = figs.build_figures_zip(_make_ws(tmp_path), "inv")
    assert blob
    names = set(zipfile.ZipFile(io.BytesIO(blob)).namelist())
    # Panels live under <study>/; the stitched composite figures are grouped under
    # final/ (svg + png), matching the downloadable bundle layout.
    assert names == {
        "fig-07/panel-a.svg", "fig-99/only.svg",
        "final/figure_7.svg", "final/figure_7.png",
    }


def test_study_zip_contents(tmp_path):
    ws = _make_ws(tmp_path)
    blob = figs.build_study_figures_zip(ws, "fig-07")
    names = set(zipfile.ZipFile(io.BytesIO(blob)).namelist())
    assert names == {"panel-a.svg", "figure_7.svg"}   # declared image viz only
    assert figs.build_study_figures_zip(ws, "no-such-study") is None


def _make_html_ws(tmp_path):
    """Investigation whose member study declares only INTERACTIVE (html) charts,
    the way a v2ecoli comparison study does (address: html:viz/<file>.html)."""
    _write(tmp_path / "investigations" / "inv" / "investigation.yaml",
           yaml.safe_dump({"name": "inv", "title": "Inv", "studies": ["html-01"]}))
    _write(tmp_path / "studies" / "html-01" / "study.yaml", yaml.safe_dump({
        "name": "html-01", "title": "HTML charts",
        "visualizations": [
            {"name": "mass", "address": "html:viz/mass.html", "chart": "html"},
            {"name": "flux", "address": "html:viz/flux.html", "chart": "html"},
        ],
    }))
    _write(tmp_path / "studies" / "html-01" / "viz" / "mass.html", "<html>mass</html>")
    _write(tmp_path / "studies" / "html-01" / "viz" / "flux.html", "<html>flux</html>")
    return tmp_path


def test_html_charts_count_as_figures(tmp_path):
    # An investigation of interactive HTML charts still reports a figure total
    # (drives n_figures → the `↓ figures` button shows) even with no image viz.
    r = figs.build_investigation_figures(_make_html_ws(tmp_path), "inv")
    arcs = {f["arcname"] for f in r["files"]}
    assert arcs == {"html-01/mass.html", "html-01/flux.html"}
    assert r["n_composites"] == 0            # html is a panel, never a stitched composite


def test_html_charts_ride_along_in_zip(tmp_path):
    # …and the `↓ figures` zip carries the HTML files (type-agnostic bundle).
    blob = figs.build_figures_zip(_make_html_ws(tmp_path), "inv")
    assert blob
    names = set(zipfile.ZipFile(io.BytesIO(blob)).namelist())
    assert names == {"html-01/mass.html", "html-01/flux.html"}


def test_study_outputs_zip_includes_html_report(tmp_path):
    # A study whose output is an HTML dashboard (declared via embed_visualizations),
    # with a sibling asset in its viz/ dir. `↓ outputs` must bundle both.
    _write(tmp_path / "studies" / "dash-01" / "study.yaml", yaml.safe_dump({
        "name": "dash-01", "title": "Dashboard study",
        "embed_visualizations": [{"name": "dash", "url": "/studies/dash-01/viz/index.html"}],
    }))
    _write(tmp_path / "studies" / "dash-01" / "viz" / "index.html", "<html>dashboard</html>")
    _write(tmp_path / "studies" / "dash-01" / "viz" / "data.json", "{}")
    blob = figs.build_study_outputs_zip(tmp_path, "dash-01")
    assert blob is not None
    names = set(zipfile.ZipFile(io.BytesIO(blob)).namelist())
    assert "viz/index.html" in names           # the HTML report itself
    assert "viz/data.json" in names            # sibling assets travel with it
    # a study with neither figures nor embedded HTML → None
    _write(tmp_path / "studies" / "empty-01" / "study.yaml", yaml.safe_dump({"name": "empty-01"}))
    assert figs.build_study_outputs_zip(tmp_path, "empty-01") is None


def test_study_outputs_zip_includes_figures_too(tmp_path):
    # An image-figure study: outputs.zip is a superset of figures.zip, images
    # organized under figures/.
    ws = _make_ws(tmp_path)
    blob = figs.build_study_outputs_zip(ws, "fig-07")
    assert blob is not None
    names = set(zipfile.ZipFile(io.BytesIO(blob)).namelist())
    assert {"figures/panel-a.svg", "figures/figure_7.svg"} <= names


def test_resolve_figure_file(tmp_path):
    ws = _make_ws(tmp_path)
    assert figs.resolve_figure_file(ws, "inv", 7, "svg").name == "figure_7.svg"
    assert figs.resolve_figure_file(ws, "inv", 7, "png").name == "figure_7.png"
    assert figs.resolve_figure_file(ws, "inv", 7, "gif") is None    # bad ext
    assert figs.resolve_figure_file(ws, "inv", 99, "svg") is None   # no composite


def test_missing_investigation_is_empty(tmp_path):
    _make_ws(tmp_path)
    assert figs.build_investigation_figures(tmp_path, "nope") == {
        "composites": [], "files": [], "n_composites": 0, "stale": [], "n_stale": 0,
    }


def test_summary_carries_n_figures(tmp_path):
    # The investigation card gates its ↓ figures action on this count, which now
    # reflects EVERY downloadable figure file (panels + composites across member
    # studies), not just stitched composites — so a panel-only investigation still
    # gets the action. This ws has 3 figure files (fig-07: panel-a + figure_7;
    # fig-99: only).
    from vivarium_workbench.lib import investigation_status as ist
    ws = _make_ws(tmp_path)
    out = ist.build_iset_summary(ws, study_has_runs=lambda s, spec: False)
    row = [r for r in out if r["name"] == "inv"][0]
    assert row["n_figures"] == 3


def test_publish_staging_paths(tmp_path):
    # Locks the exact zip paths the frontend builds in snapshot mode:
    #   <base>/figures/<inv>/figures.zip  and  <base>/figures/studies/<slug>.zip
    ws = _make_ws(tmp_path)
    out_dir = tmp_path / "bundle"
    inv_zip = figs.build_figures_zip(ws, "inv")
    (out_dir / "figures" / "inv" / "figures.zip").parent.mkdir(parents=True)
    (out_dir / "figures" / "inv" / "figures.zip").write_bytes(inv_zip)
    for slug in ("fig-07", "fig-99"):
        blob = figs.build_study_figures_zip(ws, slug)
        assert blob is not None
        dst = out_dir / "figures" / "studies" / f"{slug}.zip"
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(blob)
    assert (out_dir / "figures" / "inv" / "figures.zip").is_file()
    assert (out_dir / "figures" / "studies" / "fig-07.zip").is_file()
    assert (out_dir / "figures" / "studies" / "fig-99.zip").is_file()


def _make_path_field_ws(tmp_path):
    """Investigation whose study declares figures via the bare `path:` field
    (no `address:` scheme) — the non-canonical form some workspaces used. The
    figures must still count toward n_figures + ride along in the `↓ figures` zip."""
    _write(tmp_path / "investigations" / "inv" / "investigation.yaml",
           yaml.safe_dump({"name": "inv", "title": "Inv", "studies": ["pf-01"]}))
    _write(tmp_path / "studies" / "pf-01" / "study.yaml", yaml.safe_dump({
        "name": "pf-01", "title": "Path-field figures",
        "visualizations": [
            {"name": "pop", "path": "viz/pop.html"},
            {"name": "spatial", "path": "viz/spatial.html"},
        ],
    }))
    _write(tmp_path / "studies" / "pf-01" / "viz" / "pop.html", "<html>pop</html>")
    _write(tmp_path / "studies" / "pf-01" / "viz" / "spatial.html", "<html>spatial</html>")
    return tmp_path


def test_path_field_html_counts_as_figures(tmp_path):
    r = figs.build_investigation_figures(_make_path_field_ws(tmp_path), "inv")
    arcs = {f["arcname"] for f in r["files"]}
    assert arcs == {"pf-01/pop.html", "pf-01/spatial.html"}


def test_path_field_html_rides_along_in_zip(tmp_path):
    blob = figs.build_figures_zip(_make_path_field_ws(tmp_path), "inv")
    assert blob
    names = set(zipfile.ZipFile(io.BytesIO(blob)).namelist())
    assert names == {"pf-01/pop.html", "pf-01/spatial.html"}
