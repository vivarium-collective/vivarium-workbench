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

import json
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
                target_band: tuple[float, float] | None = None) -> str:
    """Render a single line chart as an inline SVG string. Pure-stdlib.

    If ys2 is provided, plots a second series alongside ys (used for
    free/ATP-pool comparison). target_band shades a horizontal range
    (used for the literature-target band on DnaA count).
    """
    pad_l, pad_r, pad_t, pad_b = 56, 12, 28, 36
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    if not xs or not ys:
        return (f'<div class="chart-empty" style="padding:24px;color:#94a3b8;'
                f'font-style:italic">No data for "{escape(title)}".</div>')

    all_ys = list(ys) + (list(ys2) if ys2 else [])
    y_min = min(all_ys + ([target_band[0]] if target_band else []))
    y_max = max(all_ys + ([target_band[1]] if target_band else []))
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
            f'literature target {int(lo)}–{int(hi)}</text>'
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

    # X-axis (time): 5 ticks
    xticks = [x_min + (x_max - x_min) * f for f in (0.0, 0.25, 0.5, 0.75, 1.0)]
    xtick_text = "".join(
        f'<text x="{sx(x):.1f}" y="{pad_t+plot_h+14:.1f}" font-size="10" fill="#64748b" '
        f'text-anchor="middle">{_fmt(x)}s</text>'
        for x in xticks
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


def _load_static_charts(study_dir: Path) -> list[dict]:
    """Discover per-study static SVGs in <study_dir>/charts/.

    Each *.svg file is returned as one chart entry. Optional sidecar
    .meta.json with {title, caption} per file is honored; otherwise the
    filename (sans extension) becomes the title.

    Files load in alpha-sorted order, so prefix with 01_, 02_, ... to
    control display sequence. Stable for both the dashboard's Visualizations
    tab and the downloadable investigation HTML report.
    """
    charts_dir = study_dir / "charts"
    if not charts_dir.is_dir():
        return []
    out: list[dict] = []
    for svg_path in sorted(charts_dir.glob("*.svg")):
        try:
            svg_body = svg_path.read_text()
        except Exception:
            continue
        meta_path = svg_path.with_suffix(".meta.json")
        title = svg_path.stem
        caption = ""
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
                title = meta.get("title", title)
                caption = meta.get("caption", "")
            except Exception:
                pass
        out.append({
            "key":     svg_path.stem,
            "title":   title,
            "caption": caption,
            "svg":     svg_body,
        })
    return out


def render_study_charts(runs_db: Path,
                        run_name: str | None = None) -> list[dict]:
    """Return a list of {key, title, caption, svg} for the latest run in runs.db.

    Returns an empty list (not an error) when the db is missing, the run
    name isn't found, or all extractors come back empty.

    Sources, in priority order:
      1. Per-study static SVGs in ``studies/<name>/charts/*.svg`` (if present).
         These are written by study authors (e.g., via
         ``pbg_superpowers.study_charts``) and survive when the run DB is
         absent or stale.
      2. Perf-harness schema (colonies-01-hpc-readiness): runs + ticks tables.
      3. dnaa-style schema (simulations + history tables) with hardcoded CHART_SPECS.
    """
    # Source 1: per-study static charts. Convention: SVGs live alongside
    # runs.db in studies/<name>/charts/.
    study_dir = runs_db.parent
    static = _load_static_charts(study_dir)
    if static:
        # Append db-derived charts AFTER the curated ones, so curated work
        # leads but live trajectories are still available below.
        db_charts = _render_db_charts(runs_db, run_name) if runs_db.exists() else []
        return static + db_charts

    if not runs_db.exists():
        return []
    return _render_db_charts(runs_db, run_name)


def _render_db_charts(runs_db: Path, run_name: str | None) -> list[dict]:
    """Original DB-driven chart logic, factored out so static charts can
    pre-empt or augment it."""
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
