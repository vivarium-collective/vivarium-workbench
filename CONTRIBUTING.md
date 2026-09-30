# Contributing

## Running the test suite

A minimal install runs most of the suite; some tests need optional readers or a
sibling checkout and **skip cleanly** (via `pytest.importorskip`) when those are
absent.

### 1. Base install

```bash
uv venv
uv pip install -e ".[dev]"
uv run pytest
```

### 2. Optional run-store readers (parquet / zarr)

Tests that exercise the parquet/zarr run-store paths need `polars`, `xarray`
(+ `zarr`), and `pyarrow`. They **skip** without them. To run them, add the
`test` extra:

```bash
uv pip install -e ".[dev,test]"
```

> Note: zarr run-store reading is normally exercised with the **v2ecoli** venv,
> which ships `xarray`/`zarr`; the dashboard's own venv intentionally omits them.
>
> Known issue (separate from test setup): with current `polars` (1.41.x) three
> `tests/test_study_charts_parquet.py` array-column tests fail because
> `study_charts._extract_paths_from_parquet` returns empty — a polars-version
> incompatibility tracked separately, not a missing-dependency problem.

### 3. Investigation / study tests — editable `pbg-superpowers`

A group of tests exercises the investigation/study orchestration that lives in
[`pbg-superpowers`](https://github.com/vivarium-collective/pbg-superpowers)
(`study_io`, `run_registry`, `investigation_status`, `readout_validation`,
`feedback_actions`, `resolve_run_expected`, `resolve_seed_source`,
`needs_attention`, …). The version pinned in the lockfile predates those
symbols, so these tests need an **editable install of a current local
checkout**:

```bash
# from a sibling checkout of pbg-superpowers on a current branch
uv pip install -e ../pbg-superpowers --no-deps
```

Without it, those tests fail at import or assert against the older behaviour.
This is the same editable-install convention the dashboard already uses for
local `pbg-superpowers` development.

### Full suite

```bash
uv pip install -e ".[dev,test]"
uv pip install -e ../pbg-superpowers --no-deps   # current local checkout
uv run pytest
```

## Installing from a bare clone, and the v2ecoli environment

The root project installs with a plain `uv sync` from a clone that has **nothing
beside it** — no sibling checkouts. CI's `standalone` workflow enforces that
(`uv sync --locked`, then `vwb smoke`); run the same check locally with
`scripts/standalone_smoke.sh`. Do not add `path = "../<sibling>"` sources to the
root `pyproject.toml`.

The e-coli environment that used to be the root's `demo` extra
(`uv sync --extra demo`) is its own project, with `v2ecoli` and `pbg-ptools` as
editable sibling checkouts:

```bash
# layout: <dir>/vivarium-workbench, <dir>/v2ecoli, <dir>/pbg-ptools
cd demos/v2ecoli && uv sync
```

## Bumping the bundled Perfetto UI

The trace viewer (`lib/perfetto_ui.py`) pins one Perfetto UI release by version
plus three SHA-256s (its `index.html`, its `manifest.json` — which carries a hash
for every other file — and Perfetto's `LICENSE`). To move to a newer release, set
`PERFETTO_UI_VERSION` / `PERFETTO_UI_COMMIT` to a `ui.perfetto.dev/<version>/`
build, run `python -m vivarium_workbench.lib.perfetto_ui --print-hashes`, paste
the three hashes, and check the "Open trace" flow still works (the postMessage
protocol is Perfetto's public API, but verify it).

Also re-check that analytics stay off. The workbench opens Perfetto with `?testing=1`
(`static/perfetto-open.js`, `NO_ANALYTICS_QUERY`) because Perfetto loads Google Analytics
on `http://localhost:` / `http://127.0.0.1:` / `*.perfetto.dev` origins — which is how users
reach the workbench through an SSM tunnel. Grep the new `frontend_bundle.js` for
`createEmbedder` / `initAnalytics` and confirm testing mode still disables them (and still
does nothing else user-visible).

And re-check the fonts. The v58.3 `frontend.css` declares each font a second time with
URLs that climb out of the release directory (`../assets/assets/Roboto.woff2`,
`../../assets/…`, `../../../../assets/…` — 404s on `ui.perfetto.dev` too), so the
workbench serves the stylesheet with those pointed back at the bundle's own `assets/`
(`perfetto_ui.fix_stylesheet`; the file on disk stays the verified one). Open a trace from
a sub-path (`--base-path /workbench`) in a fresh browser profile and confirm there are no
404s for `…/assets/assets/*.woff2`; if a new release fixed its CSS, the rewrite is a no-op.
