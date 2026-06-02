"""Render inline-SVG line charts from a study's runs.db.

The dashboard uses these to embed simulation visualisations directly into
the per-investigation HTML report (and the study-detail Charts panel).
SVGs are self-contained, render in any browser without JS, and survive
being downloaded as part of the report's offline-HTML payload.

Chart selection is currently bespoke to the dnaa-investigation readouts —
each entry in CHART_SPECS declares what to pull from the per-step state
and how to label the y-axis. As more studies emit data we'll generalise
this into a per-study chart-spec block (likely `readouts:` driven).
"""
from __future__ import annotations

import base64
import json
import re
import sqlite3
from pathlib import Path
from xml.sax.saxutils import escape

# DnaA monomer index (PD03831[c]) in monomer_ids; hardcoded fallback.
DNAA_MONOMER_IDX = 3861

# Each spec: (title, y-label, extractor(state)->float-or-None, caption)
CHART_SPECS = [
    {
        "key":     "dnaA_count",
        "title":   "DnaA monomer count over time",
        "y_label": "molecules / cell",
        "caption": "Aggregate DnaA via listeners.monomer_counts[idx 3861] (PD03831[c]). "
                   "Steady-state target band per Schmidt 2016 / Mori 2021: 300-800.",
        "extract": "monomer_index:3861",
    },
    {
        "key":     "free_vs_atp_pool",
        "title":   "Bulk pool composition: free DnaA vs DnaA-ATP",
        "y_label": "molecules / cell",
        "caption": "bulk[PD03831[c]] (free) and bulk[MONOMER0-160[c]] (DnaA-ATP complex). "
                   "Sum approximates the listener's aggregate; difference reveals "
                   "equilibrium drift.",
        "extract": "bulk_pair:PD03831[c]:MONOMER0-160[c]",
    },
    {
        "key":     "tx_init_events",
        "title":   "RNA transcription-initiation events per step",
        "y_label": "events / step (cell-wide)",
        "caption": "Sum of listeners.rnap_data.rna_init_event across all TUs. "
                   "A coarse rate proxy.",
        "extract": "listener_sum:listeners.rnap_data.rna_init_event",
    },
    {
        "key":     "dnaA_mrna",
        "title":   "DnaA mRNA copy number (EG10235_RNA)",
        "y_label": "molecules / cell",
        "caption": "Direct from listeners.rna_counts.mRNA_counts. Position-indexed by "
                   "the ParCa mRNA_TU_ids list.",
        "extract": "mrna_first",   # placeholder — needs mrna_index lookup
    },
]


def _extract(state: dict, extractor: str) -> float | None:
    if extractor.startswith("monomer_index:"):
        idx = int(extractor.split(":")[1])
        mc = state.get("listeners", {}).get("monomer_counts")
        if isinstance(mc, list) and len(mc) > idx:
            return float(mc[idx])
        return None

    if extractor.startswith("bulk_pair:"):
        _, a, b = extractor.split(":")
        bulk = state.get("bulk")
        if not isinstance(bulk, list):
            return None
        for row in bulk:
            if isinstance(row, list) and row and row[0] == a:
                # return tuple-like for bulk_pair; caller checks
                pa = row[1]
                break
        else:
            pa = None
        for row in bulk:
            if isinstance(row, list) and row and row[0] == b:
                pb = row[1]
                break
        else:
            pb = None
        return (pa, pb)   # caller will handle

    if extractor.startswith("listener_sum:"):
        path = extractor.split(":", 1)[1]
        cur = state
        for seg in path.split("."):
            if not isinstance(cur, dict) or seg not in cur:
                return None
            cur = cur[seg]
        if isinstance(cur, list):
            try:
                return float(sum(cur))
            except TypeError:
                return None
        if isinstance(cur, (int, float)):
            return float(cur)
        return None

    if extractor == "mrna_first":
        rc = state.get("listeners", {}).get("rna_counts")
        if isinstance(rc, dict):
            m = rc.get("mRNA_counts")
            if isinstance(m, list) and m:
                # Without sim_data we can't index by EG10235_RNA. Return median
                # of the top-20 most-expressed mRNAs as a stand-in until the
                # rna_id index lookup is wired through (dashboard doesn't have
                # access to the ParCa cache from this process).
                top20 = sorted(m, reverse=True)[:20]
                return float(top20[0]) if top20 else None
        return None
    return None


def _render_svg(title: str, y_label: str, xs: list[float], ys: list[float],
                width: int = 720, height: int = 220,
                ys2: list[float] | None = None, y2_label: str | None = None,
                target_band: tuple[float, float] | None = None,
                hline: float | None = None) -> str:
    """Render a single line chart as an inline SVG string. Pure-stdlib.

    If ys2 is provided, plots a second series alongside ys (used for
    free/ATP-pool comparison). target_band shades a horizontal range
    (used for the literature-target band on DnaA count). hline draws a
    horizontal dashed line at a specific y (used for at_most / at_least /
    equals pass criteria — the v4 tests style).
    """
    pad_l, pad_r, pad_t, pad_b = 56, 12, 28, 36
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    if not xs or not ys:
        return (f'<div class="chart-empty" style="padding:24px;color:#94a3b8;'
                f'font-style:italic">No data for "{escape(title)}".</div>')

    all_ys = list(ys) + (list(ys2) if ys2 else [])
    y_extras = []
    if target_band:
        y_extras.extend(target_band)
    if hline is not None:
        y_extras.append(hline)
    y_min = min(all_ys + y_extras)
    y_max = max(all_ys + y_extras)
    if y_min == y_max:
        y_min -= 1
        y_max += 1
    x_min, x_max = min(xs), max(xs)
    if x_min == x_max:
        x_max = x_min + 1

    def sx(x): return pad_l + (x - x_min) / (x_max - x_min) * plot_w
    def sy(y): return pad_t + plot_h - (y - y_min) / (y_max - y_min) * plot_h

    def _points(series):
        return " ".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y in zip(xs, series))

    band_rect = ""
    if target_band:
        lo, hi = target_band
        band_rect = (
            f'<rect x="{pad_l:.1f}" y="{sy(hi):.1f}" '
            f'width="{plot_w:.1f}" height="{(sy(lo)-sy(hi)):.1f}" '
            f'fill="#dcfce7" fill-opacity="0.55"/>'
            f'<text x="{pad_l + plot_w - 4:.1f}" y="{sy(hi) + 12:.1f}" '
            f'font-size="10" fill="#16a34a" text-anchor="end">'
            f'pass band {_fmt(lo)}–{_fmt(hi)}</text>'
        )
    hline_svg = ""
    if hline is not None:
        hline_svg = (
            f'<line x1="{pad_l:.1f}" y1="{sy(hline):.1f}" '
            f'x2="{pad_l+plot_w:.1f}" y2="{sy(hline):.1f}" '
            f'stroke="#16a34a" stroke-width="1.5" stroke-dasharray="4,4"/>'
            f'<text x="{pad_l + plot_w - 4:.1f}" y="{sy(hline) - 4:.1f}" '
            f'font-size="10" fill="#16a34a" text-anchor="end">'
            f'pass threshold {_fmt(hline)}</text>'
        )

    series_paths = (
        f'<polyline points="{_points(ys)}" fill="none" stroke="#2563eb" stroke-width="1.5"/>'
    )
    if ys2 is not None:
        series_paths += (
            f'<polyline points="{_points(ys2)}" fill="none" stroke="#dc2626" stroke-width="1.5"/>'
        )

    # Simple 4-tick y-axis labels
    yticks = [y_min + (y_max - y_min) * f for f in (0.0, 0.25, 0.5, 0.75, 1.0)]
    ytick_text = "".join(
        f'<text x="{pad_l-6:.1f}" y="{sy(y)+3:.1f}" font-size="10" fill="#64748b" '
        f'text-anchor="end">{_fmt(y)}</text>'
        f'<line x1="{pad_l:.1f}" y1="{sy(y):.1f}" x2="{pad_l+plot_w:.1f}" y2="{sy(y):.1f}" '
        f'stroke="#e2e8f0" stroke-dasharray="2,3"/>'
        for y in yticks
    )

    # X-axis (time): 5 ticks. Times are emitted in seconds; label in MINUTES
    # (Rashmi 2026-05-30: "mark the x axis time in minutes"). Tick positions are
    # unchanged (proportional to the seconds range); only the labels convert.
    xticks = [x_min + (x_max - x_min) * f for f in (0.0, 0.25, 0.5, 0.75, 1.0)]
    xtick_text = "".join(
        f'<text x="{sx(x):.1f}" y="{pad_t+plot_h+14:.1f}" font-size="10" fill="#64748b" '
        f'text-anchor="middle">{_fmt(x / 60.0)}</text>'
        for x in xticks
    )
    xtick_text += (
        f'<text x="{pad_l + plot_w / 2:.1f}" y="{pad_t+plot_h+30:.1f}" font-size="10" '
        f'fill="#64748b" text-anchor="middle">time (min)</text>'
    )

    # Legend
    legend = ""
    if ys2 is not None and y2_label is not None:
        legend = (
            f'<g transform="translate({pad_l+12},{pad_t+10})">'
            f'<rect width="10" height="3" fill="#2563eb"/>'
            f'<text x="14" y="4" font-size="11" fill="#1e293b">{escape(y_label)}</text>'
            f'<rect y="14" width="10" height="3" fill="#dc2626"/>'
            f'<text x="14" y="18" font-size="11" fill="#1e293b">{escape(y2_label)}</text>'
            f'</g>'
        )

    return f'''<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg"
       style="display:block;width:100%;height:auto;max-width:{width}px">
  <rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff"/>
  <text x="{width/2:.1f}" y="16" font-size="12" font-weight="600" fill="#0f172a"
        text-anchor="middle">{escape(title)}</text>
  {band_rect}
  {hline_svg}
  {ytick_text}
  {xtick_text}
  <line x1="{pad_l:.1f}" y1="{pad_t:.1f}" x2="{pad_l:.1f}" y2="{pad_t+plot_h:.1f}" stroke="#94a3b8"/>
  <line x1="{pad_l:.1f}" y1="{pad_t+plot_h:.1f}" x2="{pad_l+plot_w:.1f}" y2="{pad_t+plot_h:.1f}" stroke="#94a3b8"/>
  {series_paths}
  {legend}
  <text x="{pad_l-44:.1f}" y="{pad_t+plot_h/2:.1f}" font-size="10" fill="#64748b"
        transform="rotate(-90 {pad_l-44:.1f} {pad_t+plot_h/2:.1f})"
        text-anchor="middle">{escape(y_label)}</text>
</svg>'''


def _fmt(v: float) -> str:
    if v == 0:
        return "0"
    av = abs(v)
    if av >= 10_000:
        return f"{v/1000:.0f}k"
    if av >= 100:
        return f"{int(round(v))}"
    if av >= 1:
        return f"{v:.1f}"
    return f"{v:.2g}"


def _table_exists(conn, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


# Raster chart formats surfaced alongside inline SVGs. Each maps to the MIME
# type used to build a self-contained ``data:`` URI (so a downloaded report
# keeps its figures with no server round-trip — same self-contained philosophy
# as inlining SVG). GIF is included so animations (e.g. chromosome-cycle /
# colony movies rendered to .gif) play in the report.
_RASTER_CHART_MIME = {".png": "image/png", ".gif": "image/gif"}


def _static_chart_meta(asset_path: Path) -> dict:
    """Read the optional ``<name>.meta.json`` sidecar for a chart asset."""
    meta_path = asset_path.with_suffix(".meta.json")
    title, caption, simulations, interpretation = asset_path.stem, "", "", ""
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(meta, dict):
                title = str(meta.get("title", title)) or title
                caption = str(meta.get("caption", "")) or ""
                simulations = str(meta.get("simulations", "")) or ""
                interpretation = str(meta.get("interpretation", "")) or ""
        except Exception:
            pass
    return {"title": title, "caption": caption,
            "simulations": simulations, "interpretation": interpretation}


def discover_static_study_charts(charts_dir: Path) -> list[dict]:
    """Return chart records for pre-rendered figures under ``charts_dir``.

    Companion to ``render_study_charts``: where that one runs SQL against
    ``runs.db`` to draw fresh plots, this one surfaces figures the study has
    ALREADY rendered to disk (e.g. domain-specific chromosome maps,
    DnaA-box layouts, matplotlib trajectory PNGs, animated GIFs) so the
    dashboard's chart panel + the investigation report include them.

    Discovers two media families:

    * ``*.svg`` — inlined verbatim into the record's ``svg`` field
      (``media: "svg"``).
    * ``*.png`` / ``*.gif`` — embedded as a self-contained base64 ``data:``
      URI in the record's ``img`` field (``media: "png"`` / ``"gif"``), so the
      figure survives in a downloaded/standalone report with no server.
      A raster file is SKIPPED when a same-stem ``*.svg`` exists — the vector
      version wins (avoids showing ``foo.svg`` and ``foo.png`` twice).

    Convention: each ``<name>.{svg,png,gif}`` may have a sibling
    ``<name>.meta.json`` of shape::

        {
          "title":          "...",   # display heading
          "caption":        "...",   # one-line subtitle
          "simulations":    "...",   # multi-sentence: which runs produced this
          "interpretation": "..."    # multi-sentence: what the result means
        }

    The last two are optional and surface as separate paragraphs below
    the chart so an evaluator gets both the provenance and the read.
    Records are returned sorted by filename stem — the ``00_summary`` /
    ``01_*`` naming convention used in this workspace gives a natural
    display order across both media families.

    Returns ``[]`` if the directory doesn't exist, has no figures, or any
    I/O fails (treated as "no charts" rather than an error so the panel can
    still render the live charts).
    """
    if not charts_dir.exists() or not charts_dir.is_dir():
        return []
    out: list[dict] = []
    svg_stems = {p.stem for p in charts_dir.glob("*.svg")}
    # Vector SVGs — inlined verbatim.
    for svg_path in charts_dir.glob("*.svg"):
        try:
            svg_text = svg_path.read_text(encoding="utf-8")
        except Exception:
            continue
        out.append({
            "key": svg_path.stem,
            **_static_chart_meta(svg_path),
            "svg": svg_text,
            "media": "svg",
            "source": "static",
        })
    # Raster PNG/GIF — base64 data-URI so downloaded reports stay self-contained.
    for ext, mime in _RASTER_CHART_MIME.items():
        for img_path in charts_dir.glob("*" + ext):
            if img_path.stem in svg_stems:
                continue  # a vector version exists — prefer it
            try:
                b64 = base64.b64encode(img_path.read_bytes()).decode("ascii")
            except Exception:
                continue
            out.append({
                "key": img_path.stem,
                **_static_chart_meta(img_path),
                "img": f"data:{mime};base64,{b64}",
                "media": ext.lstrip("."),
                "source": "static",
            })
    out.sort(key=lambda r: r["key"])
    return out


def render_v4_test_charts(spec: dict,
                          runs_db: Path,
                          fallback_db: Path | None = None) -> list[dict]:
    """Render one inline-SVG chart per test in a v4 study yaml.

    For each entry in ``spec.tests``:
      - Extract the observable trajectory specified by ``test.measure.path``
        (+ optional ``index``) from runs_db's history. If runs_db has no
        runs OR the path isn't found, fall back to ``fallback_db`` (the
        workspace default-baseline).
      - Render a time-series SVG with the test's ``pass_if`` criterion
        overlaid (shaded band for in_range, dashed line for at_most /
        at_least).
      - Caption = test.question + the pass_if criterion as plain text.
      - Source = 'study' if drawn from runs_db, 'default-baseline' if
        from fallback_db.

    Returns [] if neither db has runs or all tests' paths are unresolvable.
    """
    tests = spec.get("tests") or []
    if not tests:
        return []

    # Collect every test's measure path up front, so a single JSON parse
    # per history row can extract ALL needed values. Avoids the N-tests ×
    # N-rows × O(state-size) cost of the naive per-test approach.
    path_specs: list[tuple[str, int | None]] = []
    seen_paths: set[tuple[str, int | None]] = set()
    for t in tests:
        measure = t.get("measure") or {}
        path = measure.get("path")
        if not path:
            continue
        key = (path, measure.get("index"))
        if key in seen_paths:
            continue
        seen_paths.add(key)
        path_specs.append(key)
    if not path_specs:
        return []

    # Choose the read source explicitly from runtime.default_emitter. SQLite
    # is the framework default; we only read zarr when the workspace has
    # opted in via `runtime.default_emitter: xarray`. No silent disk-probe —
    # that was the design bug in the prior attempt: it flipped sources based
    # on whatever happened to be on disk, hiding drift for the sqlite-default
    # workspaces.
    emitter_kind = _emitter_choice(spec, runs_db)
    study_dir = runs_db.parent if runs_db is not None else None

    sources: list[tuple[str, dict]] = []
    if emitter_kind == "xarray":
        zarr_path = _latest_zarr_for_study(study_dir) if study_dir else None
        if zarr_path is not None:
            sources.append(("study-zarr", _extract_paths_from_zarr(zarr_path, path_specs)))
    elif emitter_kind == "parquet":
        hive_root = _latest_parquet_for_study(study_dir) if study_dir else None
        if hive_root is not None:
            sources.append(("study-parquet", _extract_paths_from_parquet(hive_root, path_specs)))

    # SQLite chain: primary in the default sqlite mode, fallback in xarray
    # and parquet modes (covers per-test observables the alt store doesn't
    # carry, and the workspace default-baseline as a last resort).
    db_candidates = [(p, lbl) for p, lbl in
                     ((runs_db, "study"), (fallback_db, "default-baseline"))
                     if p is not None and p.is_file()]
    if not db_candidates and not sources:
        return []

    # For each db, extract all paths in ONE pass over the latest run's
    # history. Subsample every Nth row when a run is long, so we keep
    # ~200 points per chart regardless of original sample density.
    for db_path, label in db_candidates:
        sources.append((label, _extract_paths_from_db(db_path, path_specs)))

    charts: list[dict] = []
    for t in tests:
        measure = t.get("measure") or {}
        path = measure.get("path")
        if not path:
            continue
        idx = measure.get("index")
        key = (path, idx)

        # Per-test fallback in source order: zarr (if xarray) → study sqlite
        # → workspace default-baseline. First non-empty wins.
        xs, ys, used_source = [], [], None
        for label, src in sources:
            cand_xs, cand_ys = src.get(key, ([], []))
            if cand_xs:
                xs, ys, used_source = cand_xs, cand_ys, label
                break
        if not xs:
            continue

        band, hline = _pass_if_to_overlay(t.get("pass_if") or {})
        title = f"{t.get('name','(unnamed)')}  ({t.get('classification','test')})"
        caption_bits = []
        if t.get("question"):
            caption_bits.append(t["question"])
        crit_text = _pass_if_to_text(t.get("pass_if") or {})
        if crit_text:
            caption_bits.append(f"Pass: {crit_text}")
        if t.get("status"):
            caption_bits.append(f"Current verdict: {t['status']}")
        if used_source == "default-baseline":
            suffix = " (parquet)" if emitter_kind == "parquet" else ""
            caption_bits.append(
                "⚠ Drawn from workspace default-baseline"
                + suffix
                + " (study has no runs for this observable yet)."
            )
        charts.append({
            "key": f"v4-{t.get('name','test')}",
            "title": title,
            "caption": "  ·  ".join(caption_bits),
            "svg": _render_svg(title, _y_label_from_path(path, idx),
                                xs, ys, target_band=band, hline=hline),
            "source": "live",
            "data_source": used_source,
        })
    return charts


def _emitter_choice(spec: dict, runs_db: Path | None) -> str:
    """Return ``'xarray'``, ``'parquet'``, or ``'sqlite'`` per workspace config.

    Resolves ``runtime.default_emitter`` from (1) the study spec's runtime
    block, then (2) workspace.yaml's runtime block, defaulting to
    ``'sqlite'`` (the framework's documented default). Deliberately does
    NOT probe disk state — workspaces that haven't opted into xarray /
    parquet must not silently flip read sources, since that hides drift.
    """
    _ACCEPTED = ("xarray", "sqlite", "parquet")
    spec_rt = (spec or {}).get("runtime") or {}
    if isinstance(spec_rt, dict) and spec_rt.get("default_emitter") in _ACCEPTED:
        return spec_rt["default_emitter"]
    if runs_db is not None:
        # runs_db lives at <ws>/studies/<slug>/runs.db
        ws_yaml = runs_db.parent.parent.parent / "workspace.yaml"
        if ws_yaml.is_file():
            try:
                import yaml as _yaml
                ws = _yaml.safe_load(ws_yaml.read_text(encoding="utf-8")) or {}
                ws_rt = ws.get("runtime") or {}
                if isinstance(ws_rt, dict) and ws_rt.get("default_emitter") in _ACCEPTED:
                    return ws_rt["default_emitter"]
            except (OSError, Exception):  # noqa: BLE001 — read-fail = default
                pass
    return "sqlite"


def _latest_zarr_for_study(study_dir: Path) -> Path | None:
    """Find the most-recent ``runs.*.zarr`` directory under ``study_dir``.

    XArrayEmitter runs write per-run zarr directories at
    ``<study>/runs.<run_id>.zarr``. Sort by mtime descending and return the
    first directory with a populated ``experiment_id=*`` partition. Returns
    ``None`` if no zarr stores exist or none have data yet.
    """
    if not study_dir or not study_dir.is_dir():
        return None
    candidates = sorted(
        (p for p in study_dir.glob("runs.*.zarr") if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for zarr_dir in candidates:
        try:
            if next(zarr_dir.glob("experiment_id=*"), None) is not None:
                return zarr_dir
        except OSError:
            continue
    return None


def _extract_paths_from_zarr(
    zarr_path: Path,
    path_specs: list[tuple[str, int | None]],
    max_points: int = 200,
) -> dict[tuple[str, int | None], tuple[list[float], list[float]]]:
    """Single-pass extraction of N observable paths from a per-run zarr store.

    Mirrors :func:`_extract_paths_from_db`'s signature. Each ``path_spec``
    is ``(dotted-or-slash path, optional index)``. The path's LAST
    component is the zarr leaf-node name (XArrayEmitter view leaves are
    keyed by leaf name, not by the full nested path); for vector leaves
    (e.g. ``listeners.monomer_counts``), ``index`` selects within the
    coord dimension (``id_<leaf>``).

    Concatenates across generations into a single trace per path, then
    subsamples to ``max_points``. Returns ``{(path, index): (times, values)}``
    with empty tuples for paths that didn't resolve.
    """
    out: dict[tuple[str, int | None], tuple[list[float], list[float]]] = {
        key: ([], []) for key in path_specs
    }
    if not path_specs or not zarr_path.exists():
        return out
    try:
        import xarray as xr
    except ImportError:
        return out
    try:
        dt = xr.open_datatree(str(zarr_path), engine="zarr")
    except Exception:
        return out

    leaf_for_spec: dict[tuple[str, int | None], str] = {}
    for path, idx in path_specs:
        parts = [p for p in str(path).replace(".", "/").split("/") if p]
        if not parts:
            continue
        if parts[0] == "agents" and len(parts) >= 3:
            parts = parts[2:]
        leaf_for_spec[(path, idx)] = parts[-1]

    by_leaf: dict[str, list[tuple[str, int | None]]] = {}
    for key, leaf in leaf_for_spec.items():
        by_leaf.setdefault(leaf, []).append(key)

    try:
        for node in dt.subtree:
            if node.name not in by_leaf:
                continue
            parent = node.parent
            if parent is None:
                continue
            specs_for_leaf = by_leaf[node.name]
            gen_keys: list[tuple[int, str, str]] = []
            for var_name in (node.data_vars or {}):
                if not var_name.startswith("generation="):
                    continue
                try:
                    gen_n = int(var_name.split("=", 1)[1])
                except ValueError:
                    continue
                t_name = f"time_gen={gen_n}"
                if t_name not in (parent.data_vars or {}):
                    continue
                gen_keys.append((gen_n, var_name, t_name))
            gen_keys.sort()
            for path, idx in specs_for_leaf:
                times: list[float] = []
                values: list[float] = []
                for gen_n, var_name, t_name in gen_keys:
                    v_arr = node[var_name]
                    t_arr = parent[t_name].values
                    if idx is not None and "id_" + node.name in v_arr.dims:
                        try:
                            v_arr = v_arr.isel({"id_" + node.name: int(idx)})
                        except (IndexError, KeyError, ValueError):
                            continue
                    elif v_arr.ndim > 1:
                        continue
                    v_vals = v_arr.values
                    n = min(len(t_arr), len(v_vals))
                    for i in range(n):
                        times.append(float(t_arr[i]))
                        values.append(float(v_vals[i]))
                if not times:
                    continue
                paired = sorted(zip(times, values), key=lambda tv: tv[0])
                if max_points and len(paired) > max_points:
                    stride = max(1, len(paired) // max_points)
                    paired = paired[::stride]
                out[(path, idx)] = (
                    [t for t, _ in paired],
                    [v for _, v in paired],
                )
    except Exception:
        return out
    return out


def _latest_parquet_for_study(study_dir: Path) -> Path | None:
    """Find the most-recent parquet hive root under ``study_dir``.

    ParquetEmitter runs write hive-partitioned parquet at
    ``<study>/parquet-runs/<experiment_id>/history/experiment_id=.../...``.
    Pick the most recently modified ``<experiment_id>`` subdir and return
    its ``history/`` directory as the hive root. Returns ``None`` if no
    parquet runs exist or none have a populated ``history/`` dir yet.
    """
    if not study_dir or not study_dir.is_dir():
        return None
    parquet_runs = study_dir / "parquet-runs"
    if not parquet_runs.is_dir():
        return None
    candidates = sorted(
        (p for p in parquet_runs.iterdir() if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for exp_dir in candidates:
        history = exp_dir / "history"
        if not history.is_dir():
            continue
        try:
            if next(history.glob("experiment_id=*"), None) is not None:
                return history
        except OSError:
            continue
    return None


def _extract_paths_from_parquet(
    hive_root: Path,
    path_specs: list[tuple[str, int | None]],
    max_points: int = 200,
) -> dict[tuple[str, int | None], tuple[list[float], list[float]]]:
    """Single-pass extraction of N observable paths from a hive-partitioned
    parquet run via one DuckDB query.

    Mirrors :func:`_extract_paths_from_db`'s signature. Each ``path_spec``
    is ``(dotted-or-slash path, optional index)``. ParquetEmitter flattens
    nested observable paths into column names by replacing the separator
    with ``__`` (e.g. ``listeners.mass.cell_mass`` → column
    ``listeners__mass__cell_mass``). For array-valued columns, ``idx``
    selects element ``idx+1`` (DuckDB lists are 1-indexed).

    Subsamples to ~``max_points`` per spec. Returns
    ``{(path, index): (times, values)}`` with empty tuples for paths that
    didn't resolve.
    """
    out: dict[tuple[str, int | None], tuple[list[float], list[float]]] = {
        key: ([], []) for key in path_specs
    }
    if not path_specs or not hive_root.exists():
        return out
    try:
        import duckdb
    except ImportError:
        return out

    # Resolve column name per spec. The path's last components join via __;
    # for v2ecoli single-cell composites the listener stores are scoped
    # under agents/0/, so try the literal column first and fall back to a
    # name that's stripped of an ``agents.<id>.`` prefix. Skip paths with
    # characters that aren't safe as a column identifier — same defensive
    # filter as the sqlite branch.
    def _flatten(path: str) -> str:
        return re.sub(r"[./]+", "__", path).strip("_")

    supported: list[tuple[str, int | None, str]] = []
    for path, idx in path_specs:
        if not re.match(r"^[A-Za-z0-9_./]+$", str(path)):
            continue
        col = _flatten(str(path))
        supported.append((path, idx, col))
    if not supported:
        return out

    glob = str(hive_root).replace("'", "''") + "/**/*.pq"
    try:
        conn = duckdb.connect(":memory:")
    except Exception:
        return out
    try:
        try:
            schema_rows = conn.execute(
                f"DESCRIBE SELECT * FROM read_parquet('{glob}', hive_partitioning=1) LIMIT 0"
            ).fetchall()
        except Exception:
            return out
        available = {r[0] for r in schema_rows}
        if "global_time" not in available:
            return out
        # ``global_time`` resets to 0 at the start of each generation (every
        # generation is a freshly-built composite). Without accounting for
        # that, ORDER BY global_time overlaps all generations onto one cycle.
        # When the hive carries a ``generation`` partition, lay the lineage out
        # sequentially via a cumulative-time offset.
        has_gen = "generation" in available

        # Resolve final column per spec; fall back to stripping an
        # ``agents__<id>__`` prefix if present in the flattened column name
        # (mirrors the sqlite branch's $.agents.0.<path> fallback).
        resolved: list[tuple[tuple[str, int | None], str]] = []
        for path, idx, col in supported:
            chosen = None
            if col in available:
                chosen = col
            else:
                # try stripping a leading agents__<id>__
                stripped = re.sub(r"^agents__[^_]+__", "", col)
                if stripped in available:
                    chosen = stripped
            if chosen is None:
                continue
            resolved.append(((path, idx), chosen))
        if not resolved:
            return out

        # Build SELECT expressions: scalar columns pass through;
        # array-typed columns get a 1-indexed lookup. Probe the column
        # type from the schema (DESCRIBE returns (name, type, ...)).
        type_by_col = {r[0]: (r[1] or "") for r in schema_rows}

        # Extract one path at a time to keep SQL simple and to subsample
        # per-trace (paths can have different valid-row counts when null).
        for (path, idx), col in resolved:
            col_type = type_by_col.get(col, "")
            is_list = col_type.endswith("[]") or col_type.startswith(("LIST", "ARRAY"))
            if idx is not None and is_list:
                value_expr = f'"{col}"[{int(idx) + 1}]'
            elif idx is not None and not is_list:
                # idx supplied but column is scalar — skip to avoid SQL error
                continue
            else:
                value_expr = f'"{col}"'
            try:
                n_rows = conn.execute(
                    f"SELECT COUNT(*) FROM read_parquet('{glob}', hive_partitioning=1) "
                    f"WHERE {value_expr} IS NOT NULL"
                ).fetchone()[0] or 0
            except Exception:
                continue
            stride = max(1, n_rows // max_points) if n_rows > max_points else 1
            try:
                if has_gen:
                    # Cumulative lineage time: normalise each generation to
                    # start at 0 (gt - gmin), then offset by the summed
                    # durations of all prior generations. Correct whether
                    # global_time resets per gen (gmin≈0) or is already
                    # cumulative (gmin large → the subtraction is a no-op net
                    # of the offset). CAST so "10" sorts after "2".
                    sql = (
                        f"WITH base AS (SELECT CAST(generation AS BIGINT) AS g, "
                        f"  global_time AS gt, {value_expr} AS v "
                        f"  FROM read_parquet('{glob}', hive_partitioning=1) "
                        f"  WHERE {value_expr} IS NOT NULL AND generation IS NOT NULL), "
                        f"stats AS (SELECT g, MIN(gt) AS gmin, MAX(gt) AS gmax "
                        f"  FROM base GROUP BY g), "
                        f"off AS (SELECT g, gmin, COALESCE(SUM(gmax - gmin) OVER "
                        f"  (ORDER BY g ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING), 0) AS off "
                        f"  FROM stats), "
                        f"seq AS (SELECT (b.gt - o.gmin + o.off) AS t, b.v AS v, "
                        f"  row_number() OVER (ORDER BY b.g, b.gt) AS rn "
                        f"  FROM base b JOIN off o ON b.g = o.g) "
                        f"SELECT t, v FROM seq WHERE (rn - 1) % {max(1, stride)} = 0 ORDER BY t"
                    )
                elif stride > 1:
                    sql = (
                        f"SELECT global_time, v FROM ("
                        f"  SELECT global_time, {value_expr} AS v, "
                        f"         row_number() OVER (ORDER BY global_time) AS rn "
                        f"  FROM read_parquet('{glob}', hive_partitioning=1) "
                        f"  WHERE {value_expr} IS NOT NULL"
                        f") WHERE (rn - 1) % {stride} = 0 ORDER BY global_time"
                    )
                else:
                    sql = (
                        f"SELECT global_time, {value_expr} AS v "
                        f"FROM read_parquet('{glob}', hive_partitioning=1) "
                        f"WHERE {value_expr} IS NOT NULL "
                        f"ORDER BY global_time"
                    )
                rows = conn.execute(sql).fetchall()
            except Exception:
                continue
            times: list[float] = []
            values: list[float] = []
            for tm, v in rows:
                if tm is None or v is None:
                    continue
                try:
                    times.append(float(tm))
                    values.append(float(v))
                except (TypeError, ValueError):
                    continue
            if times:
                out[(path, idx)] = (times, values)
        return out
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _extract_paths_from_db(
    db_path: Path,
    path_specs: list[tuple[str, int | None]],
    max_points: int = 200,
) -> dict[tuple[str, int | None], tuple[list[float], list[float]]]:
    """Single-pass extraction of N observable paths from the latest run.

    Uses SQLite's ``json_extract`` to read ONLY the requested paths from
    each row's state blob. This avoids transferring the full state JSON
    (which can be hundreds of KB per row) from disk to Python — orders of
    magnitude faster than ``SELECT state`` + ``json.loads`` for large
    states. Subsamples to ~max_points per chart so the SVG renderer
    stays cheap.

    Returns ``{(path, index): (times, values)}``.
    """
    out: dict[tuple[str, int | None], tuple[list[float], list[float]]] = {
        key: ([], []) for key in path_specs
    }
    if not path_specs:
        return out
    try:
        conn = sqlite3.connect(str(db_path))
    except sqlite3.OperationalError:
        return out
    try:
        if not _table_exists(conn, "simulations") or not _table_exists(conn, "history"):
            return out
        row = conn.execute(
            "SELECT simulation_id FROM simulations ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return out
        sim_id = row[0]
        n_rows = conn.execute(
            "SELECT COUNT(*) FROM history WHERE simulation_id=?", (sim_id,)
        ).fetchone()[0] or 0
        stride = max(1, n_rows // max_points) if n_rows > 0 else 1

        # Build a json_extract column per (path, idx). Each pathspec maps
        # to one SQL expression that returns the scalar value (or NULL).
        # SQLite's path syntax accepts $.foo.bar (dotted, $-rooted) with
        # optional [N] array indices. Keys containing characters outside
        # the alnum / underscore set (e.g. ``bulk[MONOMER0-160]``-style
        # bulk lookups) aren't supported by json_extract — we skip those
        # paths entirely rather than raising, so one bad measure path
        # never kills the whole chart-render pass.
        supported = []
        for path, idx in path_specs:
            if not re.match(r"^[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*$", path):
                continue
            supported.append((path, idx))
        if not supported:
            return out

        # Two json_extract columns per path: the literal path and the
        # per-agent (agents/0/) form. v2ecoli single-cell composites scope
        # listener stores under agents/0/, so the emitter captures observables
        # there; the literal form covers non-agent composites. Coalesce per row.
        sql_paths = []  # flat list of (literal, agent) suffixes per supported
        for path, idx in supported:
            suffix = (f"[{int(idx)}]"
                      if idx is not None and isinstance(idx, int) else "")
            sql_paths.append(("$." + path + suffix,
                              "$.agents.0." + path + suffix))

        select_cols = ["global_time"] + [
            "json_extract(state, ?)" for _ in supported for _ in (0, 1)
        ]
        sql = (
            f"SELECT {', '.join(select_cols)} FROM history "
            f"WHERE simulation_id=? AND (step % ?) = 0 ORDER BY step ASC"
        )
        params = [p for pair in sql_paths for p in pair] + [sim_id, stride]
        cursor = conn.execute(sql, params)

        def _num(x):
            # json_extract returns '{}'/'[]' for empty containers — the literal
            # path's empty store must not shadow the agent-scoped value.
            if x is None or x in ("{}", "[]"):
                return None
            try:
                return float(x)
            except (TypeError, ValueError):
                return None

        for row_tuple in cursor:
            tm = row_tuple[0]
            for i, key in enumerate(supported):
                val = _num(row_tuple[1 + 2 * i])
                if val is None:
                    val = _num(row_tuple[2 + 2 * i])
                if val is None:
                    continue
                out[key][1].append(val)
                out[key][0].append(tm)
        return out
    finally:
        conn.close()


def _pick_first_nonempty_db(primary: Path,
                             fallback: Path | None) -> tuple[Path | None, str]:
    """Return (db_path, source_label) for the first db with ≥1 history row."""
    for cand, label in ((primary, "study"), (fallback, "default-baseline")):
        if cand is None or not cand.is_file():
            continue
        try:
            conn = sqlite3.connect(str(cand))
            try:
                if not _table_exists(conn, "history"):
                    continue
                n = conn.execute("SELECT COUNT(*) FROM history").fetchone()[0]
                if n > 0:
                    return (cand, label)
            finally:
                conn.close()
        except sqlite3.OperationalError:
            continue
    return (None, "none")


def _load_latest_run(db_path: Path) -> tuple[list[dict], list[float], str | None]:
    """Return (parsed_states, times, simulation_id) for the latest run in db_path."""
    conn = sqlite3.connect(str(db_path))
    try:
        if not _table_exists(conn, "simulations") or not _table_exists(conn, "history"):
            return [], [], None
        row = conn.execute(
            "SELECT simulation_id FROM simulations ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return [], [], None
        sim_id = row[0]
        rows = conn.execute(
            "SELECT step, global_time, state FROM history "
            "WHERE simulation_id=? ORDER BY step ASC",
            (sim_id,),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return [], [], sim_id
    times = [r[1] for r in rows]
    parsed = [json.loads(r[2]) for r in rows]
    return parsed, times, sim_id


def _resolve_path(state: dict, path: str, index=None):
    """Walk a dotted path through state; index into the leaf array if given."""
    node = state
    for seg in path.split("."):
        if isinstance(node, dict) and seg in node:
            node = node[seg]
        else:
            return None
    if index is None:
        return node
    if isinstance(node, list) and isinstance(index, int) and 0 <= index < len(node):
        return node[index]
    return None


def _pass_if_to_overlay(pass_if: dict):
    """Translate pass_if to (target_band, hline) for _render_svg."""
    op = pass_if.get("op")
    if op == "in_range":
        lo = pass_if.get("low")
        hi = pass_if.get("high")
        if lo is not None and hi is not None:
            return (float(lo), float(hi)), None
    if op in ("at_most", "at_least", "equals"):
        v = pass_if.get("value")
        if v is not None:
            return None, float(v)
    return None, None


def _pass_if_to_text(pass_if: dict) -> str:
    op = pass_if.get("op")
    if op == "in_range":
        return f"in [{pass_if.get('low')}, {pass_if.get('high')}]"
    if op == "at_most":
        return f"≤ {pass_if.get('value')}"
    if op == "at_least":
        return f"≥ {pass_if.get('value')}"
    if op == "equals":
        return f"= {pass_if.get('value')}"
    if op == "at_most_abs":
        return f"|·| ≤ {pass_if.get('value')}"
    return ""


def _y_label_from_path(path: str, index=None) -> str:
    """Best-effort axis label from a dotted observable path."""
    tail = path.split(".")[-1]
    if index is not None:
        return f"{tail}[{index}]"
    return tail


def render_study_charts(runs_db: Path,
                        run_name: str | None = None) -> list[dict]:
    """Return a list of {key, title, caption, svg} for the latest run in runs.db.

    Returns an empty list (not an error) when the db is missing, the run
    name isn't found, or all extractors come back empty.

    Schema fallback: if runs.db doesn't have the dnaa-style
    ``simulations``/``history`` tables but DOES have a perf-style
    ``runs``/``ticks`` pair (the colonies-01 perf harness), render
    N-sweep scaling charts instead. Extend this with new schemas as
    studies introduce them.
    """
    if not runs_db.exists():
        return []

    # Perf-harness schema (colonies-01-hpc-readiness): runs + ticks tables.
    conn = sqlite3.connect(str(runs_db))
    try:
        if _table_exists(conn, "runs") and _table_exists(conn, "ticks"):
            charts = _render_perf_sweep_charts(conn)
            if charts:
                return charts
    finally:
        conn.close()

    # dnaa-style schema fallback.
    conn = sqlite3.connect(str(runs_db))
    try:
        if not _table_exists(conn, "simulations") or not _table_exists(conn, "history"):
            return []
        if run_name:
            row = conn.execute(
                "SELECT simulation_id FROM simulations WHERE name=? "
                "ORDER BY started_at DESC LIMIT 1", (run_name,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT simulation_id FROM simulations "
                "ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return []
        sim_id = row[0]
        rows = conn.execute(
            "SELECT step, global_time, state FROM history WHERE simulation_id=? "
            "ORDER BY step ASC", (sim_id,)
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return []

    times = [r[1] for r in rows]
    parsed = [json.loads(r[2]) for r in rows]

    charts: list[dict] = []
    for spec in CHART_SPECS:
        extractor = spec["extract"]
        if extractor.startswith("bulk_pair:"):
            pairs = [_extract(s, extractor) for s in parsed]
            xs, ys, ys2 = [], [], []
            for t, p in zip(times, pairs):
                if p is None: continue
                a, b = p
                if a is None or b is None: continue
                xs.append(t); ys.append(float(a)); ys2.append(float(b))
            if not xs: continue
            svg = _render_svg(
                spec["title"], "free DnaA (bulk PD03831[c])",
                xs, ys, ys2=ys2, y2_label="DnaA-ATP (bulk MONOMER0-160[c])"
            )
        else:
            xs, ys = [], []
            for t, s in zip(times, parsed):
                v = _extract(s, extractor)
                if v is None: continue
                xs.append(t); ys.append(float(v))
            if not xs: continue
            band = (300.0, 800.0) if spec["key"] == "dnaA_count" else None
            svg = _render_svg(spec["title"], spec["y_label"],
                              xs, ys, target_band=band)
        charts.append({
            "key": spec["key"], "title": spec["title"],
            "caption": spec["caption"], "svg": svg,
        })
    return charts


# ---------------------------------------------------------------------------
# Perf-harness schema (runs + ticks)
#
# Schema (see studies/colonies-01-hpc-readiness/sims/run.py):
#   runs(run_id, sim_name, n_cells_initial, n_cells_final, duration_s,
#        seed, wall_seconds, peak_rss_mb, n_division_events, started_at,
#        completed_at, status, note)
#   ticks(run_id, tick, sim_time, wall_ms, per_cell_update_ms_sum,
#         pymunk_step_ms, live_cell_count, rss_mb)
#
# Renders four charts: N-vs-tick-wall (total), N-vs-per-cell-wall (the
# flat-scaling test), N-vs-peak-RSS, and the largest-N per-tick wall
# trace over time (shows variability + division spikes).
# ---------------------------------------------------------------------------

def _render_xy_svg(title: str, x_label: str, y_label: str,
                   xs: list[float], ys: list[float],
                   width: int = 720, height: int = 220) -> str:
    """Thin wrapper around _render_svg that swaps the default per-second
    x-axis tick labels for a custom one (used for N-on-x-axis charts)."""
    svg = _render_svg(title, y_label, xs, ys, width=width, height=height)
    # _render_svg appends "s" to x-axis tick labels (assumes seconds).
    # Replace those with bare values for non-time x-axes.
    import re
    svg = re.sub(r'(<text[^>]*text-anchor="middle"[^>]*>)(\d[^<]*)s(</text>)',
                 r'\1\2\3', svg)
    return svg


def _embed_gif_chart(gif_path: Path, key: str, title: str, caption: str) -> dict | None:
    """If ``gif_path`` exists, return a chart dict whose ``svg`` field is an
    <img> tag with the GIF inlined as a base64 data URL. Returns None when
    the GIF is missing so the caller can skip the entry.

    The chart panel renders ``c.svg`` via innerHTML, so HTML tags work just
    as well as SVG markup. Embedding keeps the chart self-contained for the
    downloadable HTML investigation report."""
    if not gif_path.exists():
        return None
    import base64
    try:
        b64 = base64.b64encode(gif_path.read_bytes()).decode("ascii")
    except Exception:
        return None
    return {
        "key": key,
        "title": title,
        "caption": caption,
        "svg": (
            '<div style="text-align:center; padding:8px">'
            f'<img src="data:image/gif;base64,{b64}" '
            f'alt="{escape(title)}" '
            'style="max-width:100%; height:auto; border:1px solid #e2e8f0; '
            'border-radius:4px"></div>'
        ),
    }


def _render_perf_sweep_charts(conn) -> list[dict]:
    """Build N-sweep scaling charts from runs + ticks tables.

    Aggregates per-tick wall times by run (steady-state window = tick ≥ 10)
    and plots {avg_tick_ms, per_cell_ms, peak_rss_mb} versus
    n_cells_initial. Filters to ``sim_name LIKE 'nsweep-%'`` so the
    smoke run doesn't muddy the curve.
    """
    rows = conn.execute("""
        SELECT r.run_id, r.sim_name, r.n_cells_initial, r.peak_rss_mb,
               AVG(t.wall_ms)               AS avg_tick_ms,
               AVG(t.per_cell_update_ms_sum) AS avg_ecoli_ms,
               AVG(t.pymunk_step_ms)        AS avg_pymunk_ms,
               COUNT(t.tick)                AS ticks
        FROM runs r
        JOIN ticks t ON r.run_id = t.run_id AND t.tick >= 10
        WHERE r.status = 'ok' AND r.sim_name LIKE 'nsweep-%'
        GROUP BY r.run_id
        ORDER BY r.n_cells_initial
    """).fetchall()
    if not rows:
        return []

    ns           = [float(r[2] or 0) for r in rows]
    avg_ticks    = [float(r[4] or 0) for r in rows]
    per_cell_ms  = [(avg_ticks[i] / ns[i]) if ns[i] else 0.0 for i in range(len(rows))]
    peak_rss     = [float(r[3] or 0) for r in rows]

    charts: list[dict] = []

    # Colony animation — colony.gif lives next to runs.db when present.
    # Surface it as the first chart so the Visualizations tab opens with
    # the most-immediate "is this actually growing and dividing?" answer.
    gif_chart = _embed_gif_chart(
        Path(conn.execute("PRAGMA database_list").fetchone()[2]).parent / "colony.gif",
        key="colony-animation",
        title="Colony growth + division (animated)",
        caption=(
            "Pure whole-cell E. coli colony, N=2 initial → divided to 4 daughters "
            "(force-divide after warmup tick). Each capsule is one cell; colour "
            "encodes lineage (daughters get hue-shifted variants of the mother). "
            "Regenerate via `python studies/colonies-01-hpc-readiness/sims/make_gif.py`."
        ),
    )
    if gif_chart:
        charts.append(gif_chart)
    charts.append({
        "key": "perf-tick-wall-vs-n",
        "title": "Per-tick wall time vs N (steady-state)",
        "caption": (
            "Mean wall time per composite tick, by initial cell count. "
            "Linear scaling here is the HPC-readiness signature — anything "
            "super-linear (GIL contention, O(N²) physics, scheduler) would "
            "bend up at large N."
        ),
        "svg": _render_xy_svg(
            "Per-tick wall time vs N",
            "N (initial cells)", "wall time (ms / tick)",
            ns, avg_ticks,
        ),
    })
    charts.append({
        "key": "perf-per-cell-vs-n",
        "title": "Per-cell wall time vs N (steady-state)",
        "caption": (
            "wall_ms / N. The `per-cell-cost-within-2x-reference` test "
            "passes when this stays within 2× of N=1. Flat-or-decreasing "
            "is the ideal shape (here ~73 ms/cell across N=1..8)."
        ),
        "svg": _render_xy_svg(
            "Per-cell wall time vs N",
            "N (initial cells)", "wall time (ms / cell / tick)",
            ns, per_cell_ms,
        ),
    })
    charts.append({
        "key": "perf-rss-vs-n",
        "title": "Peak RSS vs N",
        "caption": (
            "Process resident set size at end of run. RAM grows ~450 MB per "
            "added cell atop a ~1 GB Python/import baseline; this is the "
            "constraint that sets the per-node cell ceiling on HPC."
        ),
        "svg": _render_xy_svg(
            "Peak RSS vs N",
            "N (initial cells)", "RSS (MB)",
            ns, peak_rss,
        ),
    })

    # Per-tick wall trace for the largest-N run — shows steady-state vs
    # warmup / division spikes.
    largest = rows[-1]
    largest_run_id = largest[0]
    trace_rows = conn.execute(
        "SELECT tick, wall_ms FROM ticks WHERE run_id=? ORDER BY tick",
        (largest_run_id,),
    ).fetchall()
    if trace_rows:
        ticks_x = [float(r[0]) for r in trace_rows]
        walls_y = [float(r[1]) for r in trace_rows]
        svg = _render_xy_svg(
            f"Per-tick wall trace — {largest[1]} (N={int(largest[2])})",
            "tick", "wall time (ms)",
            ticks_x, walls_y,
        )
        charts.append({
            "key": "perf-tick-trace-largest",
            "title": f"Per-tick wall trace — {largest[1]}",
            "caption": (
                "Wall-time per tick over the whole run for the largest N "
                "in the sweep. Useful for spotting outliers, warmup effects, "
                "and division-event spikes."
            ),
            "svg": svg,
        })

    return charts
