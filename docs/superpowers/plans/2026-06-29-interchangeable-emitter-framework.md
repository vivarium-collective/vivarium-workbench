# Contract-driven, fully-interchangeable Emitter Framework (every emitter a Step) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans`. Strict TDD per step: write failing test → run/fail → implement → run/pass → commit. Every commit ends with `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`. Spans THREE repos — respect the cross-repo merge order at the bottom.

**Goal:** Make emitters self-describe a minimal contract in pbg-emitters, generalize `XArrayEmitter` so it is a normal process-bigraph **Step** (no external driver), and make vivarium-dashboard emitter-agnostic via one broker that injects/reads/labels/charts every emitter uniformly. XArray becomes the dashboard default; SQLite is opt-in.

**Architecture:** Every emitter (`SQLite`, `XArray`, `Parquet`, `RAM`) is a `process_bigraph.emitter.Emitter` Step — it declares its ports via `config['emit']` and `inputs()` returns that map; `update(state)` receives the flat wired state. `XArrayEmitter`'s hardwired `agents/<agent_id>` envelope (transducer.py:215) and lineage partition (`generation=len(agent_id)`) are replaced by configurable `emit_root` (default `()`) + a partition strategy (default `flat`); v2ecoli passes a `colony` config to reproduce its current multi-cell zarr layout. Emitters self-describe an `EmitterContract` (just `output_kind` + store-location key) in pbg-emitters. The dashboard broker (`lib/emitters.py`) holds the single `output_kind → reader/injector/label/chart` dispatch; all scattered `if kind ==` branches route through it.

**Tech Stack:** Python ≥3.11, process-bigraph `Composite`/`Emitter`/`Step`, bigraph-schema, `pbg_emitters[sqlite,xarray]`, xarray/zarr, pytest.

## Global Constraints
- **Never break existing reads:** SQLite, Parquet, RAM, and v2ecoli's existing colony zarr stores chart identically. No reader module, store-path scheme, or partition layout changes. v2ecoli's colony layout must be reproduced **from config**, byte-for-byte.
- Emitters self-describe in pbg-emitters; the dashboard re-encodes zero per-emitter knowledge.
- After the refactor the dashboard holds **zero** `if kind ==`/`if emitter_kind ==` branches — only the broker's internal dispatch table.
- Every emitter is a **Step** (no external driver; `EmitterContract` has **no** `driving_mode`).
- `xarray`, `zarr`, `pbg-emitters[xarray]` become dashboard **runtime** deps.
- Default emitter → `xarray`; SQLite opt-in via `runtime.default_emitter: sqlite`; broker auto-falls-back to SQLite when no valid xarray view can form.
- Do **not** edit process-bigraph (RAMEmitter contract via a pbg-emitters registry shim).

## Settled design facts (from investigation)
- `process_bigraph.emitter.Emitter` (emitter.py:142-147): `config_schema={'emit':'schema'}`; `inputs(self): return self.config['emit']`; `update(self, state)` gets the **flat wired state**. `add_emitter_to_composite` builds `config['emit'] = {port: 'node' ...}` from `collect_input_ports`. This is how SQLite/Parquet/RAM are injected — and how XArray will be.
- The ONLY reason XArray isn't a Step today: `transducer.py:215` `agent_path=("agents", self.partition.agent_id)` + `_base.py:56-78,117` lineage partition. These are v2ecoli colony assumptions, not intrinsic.
- Contract delta beyond bigraph-schema `inputs()/outputs()/config_schema`: just `output_kind` (+ which config key holds the store path). `output_kind` is partly inferable today (`explorer_data._resolve_run_source` sniffs by suffix); declaring it lets the broker delete the scattered branches.

---

## File Structure
```
pbg-emitters/
├── pbg_emitters/
│   ├── contract.py                      [NEW] EmitterContract(output_kind, output_uri_config_key)
│   ├── contracts.py                     [NEW] register_contract/contract_for + RAM literal shim
│   ├── __init__.py                      [MODIFY] export EmitterContract, contract_for, register_contract
│   ├── sqlite_emitter.py                [MODIFY] emitter_contract() classmethod
│   ├── parquet_emitter.py               [MODIFY] emitter_contract() classmethod
│   └── xarray_emitter/
│       ├── emitter.py                   [MODIFY] emitter_contract(); emit_root + partition config; Step-injectable defaults
│       ├── transducer.py                [MODIFY] line 215 agent_path → configurable emit_root
│       ├── _base.py                     [MODIFY] partition strategy (flat default | colony)
│       └── storage.py                   [MODIFY] flat vs colony partition construction
├── tests/test_emitter_contract.py       [NEW]
├── tests/test_xarray_step_flat.py       [NEW] XArray as a Step, flat partition
└── tests/test_xarray_colony_config.py   [NEW] colony layout reproduced from config

v2ecoli/
├── v2ecoli/library/xarray_run.py        [MODIFY] pass colony config into XArrayEmitter-as-Step; retire external loop
└── (its tests)                          [MODIFY] colony zarr layout byte-identical gate

vivarium-dashboard/
├── pyproject.toml                       [MODIFY] runtime deps pbg-emitters[sqlite,xarray], xarray, zarr
├── vivarium_dashboard/lib/
│   ├── emitters.py                      [NEW] broker (single dispatch locus)
│   ├── study_charts.py                  [MODIFY] 536-595,613-647 → broker
│   ├── simulations_index.py             [MODIFY] 601,650-692 → broker
│   ├── registry.py                      [MODIFY] 221-248 → broker
│   ├── run_runner.py                    [MODIFY] 98-121,318-366 → broker (uniform Step injection)
│   ├── composite_subprocess.py          [MODIFY] 142-160 default; DELETE 216-292 v2ecoli xarray branch
│   └── explorer_data.py                 [MODIFY] 127-145 + kind read branches → broker
└── tests/                               test_emitters_broker.py [NEW], test_emitters_e2e.py [NEW], + modified suites
```

---

## TASK 1 — pbg-emitters: `EmitterContract` (output_kind only) + classmethods + RAM shim
Reuses the prior design's Task-1 detail, **minus `driving_mode`/`external_driver`**.
- `contract.py`: `@dataclass(frozen=True) EmitterContract(output_kind: str, output_uri_config_key: str | None = None)`; `__post_init__` validates `output_kind ∈ {"sqlite","zarr","parquet","ram"}`.
- `contracts.py`: `register_contract(key, contract)` + `contract_for(key)` (class `.emitter_contract()` wins, else registry by name); RAM literal `EmitterContract("ram", None)` registered as `"ram"`/`"RAMEmitter"` (lazy import, no process-bigraph edit).
- `emitter_contract()` classmethods: `SQLiteEmitter`→`("sqlite","db_file")`, `ParquetEmitter`→`("parquet","out_uri")`, `XArrayEmitter`→`("zarr","out_uri")`.
- Export from `__init__.py`. **Tests** (`test_emitter_contract.py`): enum validation; each emitter self-describes; `contract_for` resolves by name + class; RAM via shim.
- **Commit:** `feat(contract): EmitterContract(output_kind) + classmethods + RAM shim`.

## TASK 2 — pbg-emitters: generalize `XArrayEmitter` into a Step (the crux)
**Files:** `xarray_emitter/{emitter.py, transducer.py, _base.py, storage.py}`, `tests/test_xarray_step_flat.py`.

**Design / Interfaces:**
- `XArrayEmitter.config_schema` gains: `emit_root` (`list`, default `[]` → path `()`), `partition` (`map`, default `{"strategy": "flat"}`). Existing `view`/`transducer`/`writer` keep working; provide defaults so a broker injection with only `emit`/`out_uri` constructs a valid flat emitter.
- `transducer.py:215`: replace `agent_path = ("agents", self.partition.agent_id)` with `emit_root = tuple(self.emit_root)` (threaded from config; default `()`), `emit_data = dict_to_paths((), get_in(data, emit_root))`. With `emit_root=()` the transducer consumes the flat wired state — exactly what `Emitter.update(state)` delivers to a Step.
- Time: read the wired `global_time` port (the Emitter base injects `global_time`) rather than a bare `("time",)` envelope key; accept both for back-compat.
- `_base.py`/`storage.py`: partition strategy. `flat` → a single degenerate partition (`experiment_id=<run_id>`, `variant=0`, `lineage_seed=0`, `generation=1`), no `agent_id`/lineage semantics. `colony` → the CURRENT model (`agent_id`, `generation=len(agent_id)`, `parent=agent_id[:-1]`) selected by config. Factor the lineage logic behind `strategy`.
- `agent_id` becomes an **optional declared port**: in `flat` mode it is absent; in `colony` mode it is wired (the `config['emit']` includes it) and read from state.
- The generic view helpers (`view_from_emit_paths`, `extract_output_metadata_from_state`, `filter_view_to_existing_leaves` — scalar+vector, from the prior design) live in `xarray_emitter/` and build the `view`/`output_metadata` from the emit ports so a broker injection auto-derives a valid view.

**Tests** (`test_xarray_step_flat.py`): inject `XArrayEmitter` into a tiny flat composite via the standard `config['emit']`/`inputs()` path (no `agents` envelope, no external loop), run N steps, assert a zarr store is written and `comparative_viz._extract_trace_from_zarr` charts a scalar AND a vector leaf (re-proves the old "open risks": single-partition reader compat + vector coord discovery). Buffer-flush quirk (`assert not include_static` when buffer exactly full at close) handled with `buffer_size=3` + close try/except.
- **Commit:** `feat(xarray): XArrayEmitter is a generic Step (emit_root + flat partition; agent_id optional port)`.

## TASK 3 — v2ecoli: pass colony config into `XArrayEmitter`-as-Step
**Files:** `v2ecoli/library/xarray_run.py` (+ tests).
- Replace the external `run_multigen_xarray` loop with constructing/configuring `XArrayEmitter` (or the composite that contains it) with `emit_root=["agents", <id>]` + `partition={"strategy":"colony", ...}`, so v2ecoli's multi-cell/lineage zarr layout is produced from config.
- **Test:** run a v2ecoli colony fixture both ways and assert the produced zarr store is byte-/structure-identical to the pre-change layout (back-compat gate). Keep a thin `run_multigen_xarray` shim if other callers exist.
- **Commit:** `feat(v2ecoli): drive XArrayEmitter as a Step with colony partition config`.

## TASK 4 — dashboard broker skeleton + route READ / LABEL / CHART
Reuses prior Task-3 detail. `lib/emitters.py`: `resolve_contract`, `output_kind` (with `xarray→zarr` alias), `read_source` (delegates `_resolve_run_source`), `reader_for(kind)` (delegates existing readers), `label_for_run` (ports `_emitter_for_row`), `default_emitter` (ports `_emitter_choice`, fallback flipped to `xarray` via `DEFAULT_EMITTER` constant), `chart_source`. Route `study_charts`, `simulations_index._emitter_for_row`, `registry._mark_default_emitter`, `explorer_data` read branches through it. **Tests:** broker dispatch + updated suites. **Commit:** `feat(broker): emitters.py + route read/label/chart`.

## TASK 5 — broker `run_with_emitter` uniform Step injection + route WRITE paths; delete v2ecoli branch
- `run_with_emitter(name, *, state, run_id, emit_paths, out_dir, core, steps, db_file=None, progress_cb=None, spec=None)`: resolve contract → inject the emitter as a **Step** uniformly (SQLite via `inject_sqlite_emitter`; Parquet via `install_default_emitters`; RAM via the process-bigraph convention; **XArray** via the same `config['emit']`/`inject_emitter_for_paths` mechanism now that it's a Step, with `out_uri`/`emit_root=()` + auto-derived view; empty-view → auto-fallback to sqlite) → build `Composite` → run `steps` calling `progress_cb` → return `{output_kind, store_path, steps, run_id}`.
- Route `run_runner.py:318-366` (delete `_flush_parquet_emitters`, call broker) and `composite_subprocess.py` (collapse 142-160 default + **DELETE 216-292 v2ecoli xarray branch** → broker call).
- **Tests:** `run_with_emitter` for sqlite/parquet/ram/xarray; run_runner now calls broker. **Commit:** `feat(broker): uniform Step injection for all emitters; delete v2ecoli xarray branch`.

## TASK 6 — flip default to xarray + deps + cross-emitter e2e
- `pyproject.toml`: `pbg-emitters[sqlite]`→`pbg-emitters[sqlite,xarray]`; add `xarray>=2026.04`, `zarr~=3.1.6` to runtime deps. `DEFAULT_EMITTER="xarray"`. Update `study.schema.json` default-emitter description.
- **e2e** (`test_emitters_e2e.py`): parametrized over `["sqlite","xarray","parquet","ram"]` — tiny composite → `run_with_emitter` → read back via broker; assert scalar round-trips; for xarray assert `read_source→("zarr",…)` + vector leaf charts.
- **Commit:** `feat: default emitter → xarray + runtime deps + cross-emitter e2e`.

---

## Cross-repo merge order & verification
1. **pbg-emitters PR first** (Tasks 1, 2): contract + XArrayEmitter-as-Step. Dashboard + v2ecoli both depend on it.
2. **v2ecoli PR** (Task 3) and **dashboard PR** (Tasks 4-6) depend on pbg-emitters@main. Local: both `.venv`s install pbg-emitters editable from the working tree, so cross-clone verification works before pushing; temporarily git-pin CI to the pbg-emitters branch until it merges, then flip to `@main`.

## Open risks
- **R1 — Emitter base + optional `agent_id` port / arbitrary `emit_root` without editing process-bigraph.** Task 2 must confirm `config['emit']`/`inputs()` can carry an optional `agent_id` port and that `emit_root=()` reads the flat wired state cleanly. If a process-bigraph change is unavoidable, STOP and surface it (we prefer not to edit process-bigraph).
- **R2 — colony-from-config byte-for-byte (Task 3 gate).** v2ecoli's existing zarr layout must be reproduced exactly from the `colony` config, else existing colony runs/readers drift.
- **R3 — ragged per-tick vector observables** can't form a stable view → broker auto-falls-back to sqlite (logged).
