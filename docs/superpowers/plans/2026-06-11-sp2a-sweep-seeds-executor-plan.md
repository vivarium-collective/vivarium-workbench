# SP2a — Sweep/Seeds Executor (delegated ensemble, MVP) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Program:** Active Investigation Framework, Layer 1 / SP2a. **REDESIGNED** after grounding: a sweep/seeds run must NOT be N independent dashboard subprocesses — it must delegate to v2ecoli's `meta_composite`/`v2ecoli-workflow` ensemble machinery, which packs all points into ONE parquet hive store (`variant`/`lineage_seed` dims) the existing analysis reads.

**Goal (MVP):** `run-variant` on a variant declaring `kind: sweep`/`kind: seeds` translates it into a v2ecoli-workflow config, invokes `v2ecoli-workflow` once (forcing parquet), and records ONE ensemble run pointing at the packed store. v2ecoli-gated with a clear guard; per-point dashboard viewing (RunReader partition-filter) + grid-aware enforcement are explicit follow-ups, NOT in this MVP.

**Architecture (approved scope):** Delegate, don't reimplement. The dashboard becomes a thin translator + subprocess invoker; the existing post-run `study_outcomes.sync` records the one packed-store dir as one run (via `reconcile_runs`/`backfill_study_runs`, which already emits one row per `out/<run_id>/` dir). Force `emitter: parquet` (the xarray branch doesn't pack — `lineage.py:190-196`).

**Tech:** Python, pytest. Repo: vivarium-dashboard only (reads v2ecoli's config schema; invokes its CLI). `.venv/bin/python`.

**Grounded anchors:** workflow config schema `v2ecoli/v2ecoli/configs/default.json` + `workflow/variants.py:29-127`; CLI `v2ecoli-workflow --config <path> --out <dir>` (`workflow/run.py:102-138`, `pyproject.toml:70`); `target` = `"<proc>.<key>"` into the ParCa cache (`baseline.py:474-484`); plug-in point `_post_study_run_variant_for_test` (server.py:4959, single-call site ~5059); record-back via `backfill_study_runs` (one row per `out/<run_id>/`, `backfill_runs.py:39-56`).

**MUST stay untouched:** `_run_composite_subprocess` (single-run path for non-sweep variants), `study_outcomes.sync`, the post-run block. Delegation is an additional branch BEFORE the single-run call.

---

## Task 1: Variant → workflow-config translator (pure)

**Files:** Create `vivarium_dashboard/lib/ensemble_config.py`; Test `tests/test_ensemble_config.py`.

- [ ] **Step 1: Failing tests.**
```python
from vivarium_dashboard.lib.ensemble_config import build_workflow_config, is_delegatable_sweep

def test_seeds_maps_to_n_init_sims():
    cfg = build_workflow_config(
        variant={"name": "ens", "kind": "seeds", "n_seeds": 5, "generations": 2},
        experiment_id="run-abc", out_dir="/s/out/run-abc")
    assert cfg["n_init_sims"] == 5 and cfg["generations"] == 2
    assert cfg["emitter"] == "parquet"                       # forced
    assert cfg["experiment_id"] == "run-abc"
    assert cfg["out_dir"].endswith("/out/run-abc")

def test_sweep_maps_to_variants_with_proc_key_targets():
    cfg = build_workflow_config(
        variant={"name": "sw", "kind": "sweep",
                 "sweep_over": {"ecoli-metabolism.kcat": [1, 2, 3]}},
        experiment_id="run-x", out_dir="/s/out/run-x")
    v = cfg["variants"]
    assert v["kcat"]["target"] == "ecoli-metabolism.kcat"    # <proc>.<key> preserved
    assert v["kcat"]["value"] == [1, 2, 3]

def test_multi_key_sweep_uses_prod():
    cfg = build_workflow_config(
        variant={"kind": "sweep", "sweep_over": {"a.x": [1,2], "b.y": [3,4]}},
        experiment_id="r", out_dir="/o")
    assert cfg["variants"]["op"] == "prod"

def test_sweep_over_bare_key_is_not_delegatable():
    # a bare composite-param name (no "<proc>.") can't be a workflow target
    assert is_delegatable_sweep({"kind": "sweep", "sweep_over": {"b": [1,2]}}) is False
    assert is_delegatable_sweep({"kind": "sweep", "sweep_over": {"proc.b": [1,2]}}) is True
    assert is_delegatable_sweep({"kind": "seeds", "n_seeds": 3}) is True
    assert is_delegatable_sweep({"name": "plain"}) is False    # not a sweep at all
```
- [ ] **Step 2: fail. Step 3: implement** `build_workflow_config(variant, experiment_id, out_dir, *, base=None) -> dict` (start from `v2ecoli/configs/default.json` defaults if readable, else an inline default; set `n_init_sims` from `n_seeds`; `variants` from `sweep_over` — each key's `target` is the key verbatim (must already be `<proc>.<key>`), `value` the list; multi-key → add `op: "prod"`; `generations` from the variant (default 1); `experiment_id`, `out_dir`; FORCE `emitter: "parquet"`). And `is_delegatable_sweep(variant) -> bool`: True iff `kind == "seeds"` (with n_seeds≥1) OR (`kind == "sweep"` AND every `sweep_over` key contains a `.` i.e. is a `<proc>.<key>` target).
- [ ] **Step 4: pass. Step 5: commit** — `feat(ensemble): variant->v2ecoli-workflow config translator + delegatability check`

## Task 2: Delegation availability detection

**Files:** `vivarium_dashboard/lib/ensemble_config.py`; Test same.

- [ ] **Step 1: Failing test.**
```python
def test_delegation_available_requires_v2ecoli_workflow(tmp_workspace_v2ecoli, tmp_workspace_other):
    from vivarium_dashboard.lib.ensemble_config import delegation_available
    assert delegation_available(tmp_workspace_v2ecoli) is True   # has v2ecoli-workflow console script / import
    assert delegation_available(tmp_workspace_other) is False
```
- [ ] **Step 2: fail. Step 3: implement** `delegation_available(ws_root) -> bool`: True iff the workspace `.venv` exposes `v2ecoli-workflow` (check `<ws>/.venv/bin/v2ecoli-workflow` exists, or `workspace.yaml` package_path == v2ecoli). Keep it a cheap filesystem/yaml check (no import of v2ecoli into the dashboard process).
- [ ] **Step 4: pass. Step 5: commit** — `feat(ensemble): delegation_available v2ecoli detection`

## Task 3: Wire delegation into the variant-run handler (guarded)

**Files:** Modify `vivarium_dashboard/server.py` (`_post_study_run_variant_for_test`, before the `_run_composite_subprocess` call ~5059); Test `tests/test_study_run_variant_ensemble.py`.

- [ ] **Step 1: Failing test** (stub the subprocess; assert delegation path builds the config + invokes the CLI, and a plain variant is unchanged):
```python
def test_sweep_variant_delegates_to_workflow(tmp_v2ecoli_study, monkeypatch):
    invoked = {}
    monkeypatch.setattr(server, "_invoke_v2ecoli_workflow",
                        lambda cfg_path, out_dir, ws_root, timeout_s: invoked.update(cfg=cfg_path, out=out_dir) or _ok())
    resp, code = server.Handler._post_study_run_variant_for_test(ws_root, body_for_seeds_variant)
    assert code == 200 and invoked  # delegated, not _run_composite_subprocess
    cfg = json.loads(Path(invoked["cfg"]).read_text())
    assert cfg["n_init_sims"] >= 1 and cfg["emitter"] == "parquet"

def test_plain_variant_unchanged(tmp_study, monkeypatch):
    calls = []; monkeypatch.setattr(server, "_run_composite_subprocess", lambda *a, **k: calls.append(1) or _ok())
    server.Handler._post_study_run_variant_for_test(ws_root, body_for_plain_variant)
    assert len(calls) == 1   # single-run path untouched

def test_sweep_without_v2ecoli_errors_clearly(tmp_other_study):
    resp, code = server.Handler._post_study_run_variant_for_test(other_ws, body_for_sweep_variant)
    assert code >= 400 and "ensemble" in resp.get("error", "").lower()  # clear guard, no half-run
```
- [ ] **Step 2: fail. Step 3: implement.** In the handler, after variant resolution: if `is_delegatable_sweep(variant)`:
  - if NOT `delegation_available(ws_root)` → return a clear 4xx error ("ensemble sweep/seeds requires a v2ecoli workspace + `<proc>.<key>` sweep targets").
  - else: `experiment_id = run_id` (`cr.generate_run_id`, server.py:5047); `out_dir = study_dir/"out"/run_id`; `cfg = build_workflow_config(variant, experiment_id, str(out_dir))`; write `cfg` JSON to `out_dir/config.json`; call new `_invoke_v2ecoli_workflow(cfg_path, out_dir, ws_root, timeout_s)` — a subprocess runner mirroring `_run_composite_subprocess`'s timeout/return contract that runs `<ws>/.venv/bin/v2ecoli-workflow --config <cfg> --out <out_dir>`; on success fall through to the EXISTING post-run block (canonical-viz, scripts, analyses, `study_outcomes.sync`, `_sync_parent_investigation`) UNCHANGED — `reconcile_runs` picks up the one `out/<run_id>/` dir as one run.
  - else (not a sweep) → the existing single `_run_composite_subprocess` path, untouched.
- [ ] **Step 4: pass. Step 5: commit** — `feat(server): run-variant delegates kind:sweep/seeds to v2ecoli-workflow (packed ensemble); single-run path unchanged`

## Task 4: Record-back verification + manual integration note

**Files:** Test `tests/test_study_run_variant_ensemble.py`.

- [ ] **Step 1:** with a stubbed `_invoke_v2ecoli_workflow` that writes a minimal parquet hive store under `out/<run_id>/`, run the handler + assert `study.yaml runs[]` gains ONE entry (`name == run_id`, `emitter.kind == parquet`, store path pointing at `out/<run_id>/`) — confirming the single-ensemble-entry record-back via the existing `reconcile_runs`/`record_runs` with zero changes.
- [ ] **Step 2:** Full suite `tests/test_ensemble_config.py tests/test_study_run_variant_ensemble.py` green; existing run-variant tests green (single-run path unchanged).
- [ ] **MANUAL VERIFY (pending, document — needs the v2ecoli venv):** on v2e-invest, add a `kind: seeds` variant to a baseline study, `run-variant`, confirm one `v2ecoli-workflow` run produces `out/<run_id>/parquet/<exp>/history/variant=…/lineage_seed=…/…` and one study run entry.
- [ ] **Step 3: commit** — `test(ensemble): single-entry record-back + manual integration note`

---

## Self-Review
- Coverage: translator + delegatability (T1), v2ecoli detection (T2), guarded delegation wiring with single-run path preserved (T3), one-entry record-back (T4). Matches the approved scoped-MVP design.
- Explicit deferrals (NOT in this MVP, documented as follow-ups): RunReader variant/lineage_seed partition-filter (pbg-emitters) for per-point dashboard viz; grid-aware SP1 enforcement; N-loop fallback for non-v2ecoli workspaces (currently a clear error).
- No placeholders: grounded config schema + file:lines; forced-parquet is justified by the xarray-non-packing finding.

## Notes for the executor
- `.venv/bin/python -m pytest`. Read `v2ecoli/configs/default.json` + `workflow/variants.py` for the exact config keys; do not invent keys. The `target` must stay `<proc>.<key>` verbatim — the dashboard does NOT translate composite-param names to process addresses (that's why bare-key sweeps are non-delegatable + error clearly).
- Mirror `_run_composite_subprocess`'s subprocess/timeout/return contract for `_invoke_v2ecoli_workflow`; do not edit `_run_composite_subprocess` itself.
- The single-run path (plain variant) and the post-run `sync` block must be byte-unchanged — delegation is an added guarded branch.
- Don't modify the real v2e-invest; the manual verify is documented, not automated.
