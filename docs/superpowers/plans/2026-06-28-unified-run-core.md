# Unified Run Core + Durable Persistence (SP-A+B) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Collapse the Composite Explorer and study-run paths onto one `invoke_run()` core (run-id + route-by-source), make runs durable + Sim-DB-tracked, and let a run be saved as a study variant.

**Architecture:** `invoke_run()` owns only the shared prelude — generate the deterministic `run_id` and resolve the execution `target` (local vs deployment) — and returns a `RunPlan`. Each caller keeps its existing launch (explorer: `save_metadata` + `spawn_detached`, async; study: `run_composite_subprocess`, sync, which writes `runs_meta` itself). This avoids a double `runs_meta` write while unifying the id+routing seam where SP-D's deployment execution later plugs in.

**Tech Stack:** Python 3.12 stdlib, sqlite3, pytest with `monkeypatch`/`tmp_path`. YAML edits via the repo's existing `ruamel.yaml`/`yaml` usage.

## Global Constraints
- **No new dependencies.** Reuse `lib/composite_runs.py` (`generate_run_id`/`save_metadata`/`complete_metadata`/`connect`/`query_run_meta`).
- **Behavior-preserving refactor:** the two existing callers' HTTP response shapes do NOT change (explorer 202 `{run_id,status:"running"}`; study 200 `{simulation_id,...}`). Only the *internal* run-id/target path is unified + the prune is dropped.
- **Local execution only.** `target="deployment"` (a workspace with `.viv-build.json`) raises `RunTargetUnavailable` → 409; SP-D fills it in.
- **Tests:** fakes only — never spawn a real subprocess or touch sms-api. Run from the worktree: `cd /Users/eranagmon/code/vdash-unified-run && PYTHONPATH=$PWD /Users/eranagmon/code/v2ecoli/.venv/bin/python -m pytest <path> -v`.

---

### Task 1: `run_core` — id + target seam
**Files:** Create `vivarium_dashboard/lib/run_core.py`; Test `tests/test_run_core.py`.
**Interfaces — Produces:**
- `class RunTargetUnavailable(RuntimeError)`
- `run_target_for(workspace: Path) -> str` — `"deployment"` iff `<workspace>/.viv-build.json` exists, else `"local"`.
- `@dataclass RunPlan: run_id: str; spec_id: str; db_path: Path; config: dict; label: str | None; n_steps: int | None; target: str`
- `invoke_run(workspace, *, spec_id, config, db_path, label=None, n_steps=None, target=None) -> RunPlan` — `target = target or run_target_for(workspace)`; raises `RunTargetUnavailable` if `target == "deployment"`; else `run_id = composite_runs.generate_run_id(spec_id, config)` and returns the `RunPlan`.

- [ ] **Step 1: Write the failing tests**
```python
from pathlib import Path
import pytest
from vivarium_dashboard.lib import run_core
from vivarium_dashboard.lib.run_core import RunTargetUnavailable

def test_target_local_without_viv_build(tmp_path):
    assert run_core.run_target_for(tmp_path) == "local"

def test_target_deployment_with_viv_build(tmp_path):
    (tmp_path / ".viv-build.json").write_text('{"simulator_id": 66}')
    assert run_core.run_target_for(tmp_path) == "deployment"

def test_invoke_run_local_returns_plan_with_run_id(tmp_path):
    plan = run_core.invoke_run(tmp_path, spec_id="pkg.composites.x",
                               config={"k": 2}, db_path=tmp_path / "runs.db", label="L", n_steps=5)
    assert plan.run_id.startswith("pkg.composites.x__")
    assert plan.target == "local" and plan.config == {"k": 2} and plan.label == "L"

def test_invoke_run_deployment_raises(tmp_path):
    (tmp_path / ".viv-build.json").write_text("{}")
    with pytest.raises(RunTargetUnavailable):
        run_core.invoke_run(tmp_path, spec_id="x", config={}, db_path=tmp_path / "r.db")
```
- [ ] **Step 2: Run → fail** (`No module named run_core`).
- [ ] **Step 3: Implement** `vivarium_dashboard/lib/run_core.py`:
```python
"""Unified run core: the shared prelude every dashboard run goes through —
generate the run_id and resolve WHERE it executes (local subprocess vs the
deployment). Callers keep their own launch + persistence policy; this owns the
id + the routing seam SP-D's deployment execution plugs into."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from vivarium_dashboard.lib import composite_runs


class RunTargetUnavailable(RuntimeError):
    """The resolved execution target can't run this here yet (e.g. a remote
    build needs deployment-side execution — SP-D)."""


def run_target_for(workspace: Path) -> str:
    """A materialized remote build (WS3's .viv-build.json stamp) runs on the
    deployment; a plain local workspace runs locally."""
    return "deployment" if (Path(workspace) / ".viv-build.json").is_file() else "local"


@dataclass
class RunPlan:
    run_id: str
    spec_id: str
    db_path: Path
    config: dict
    label: str | None
    n_steps: int | None
    target: str


def invoke_run(workspace, *, spec_id, config, db_path,
               label=None, n_steps=None, target=None) -> RunPlan:
    target = target or run_target_for(Path(workspace))
    if target == "deployment":
        raise RunTargetUnavailable(
            "this composite's source is a remote build — deployment-side "
            "execution is not available yet (SP-D). Run from a local workspace.")
    run_id = composite_runs.generate_run_id(spec_id, config)
    return RunPlan(run_id=run_id, spec_id=spec_id, db_path=Path(db_path),
                   config=dict(config or {}), label=label, n_steps=n_steps, target=target)
```
- [ ] **Step 4: Run → pass.**  **Step 5: Commit** `feat(run-core): invoke_run id + route-by-source seam`.

---

### Task 2: SP-B durability — drop the explorer prune-to-20
**Files:** Modify `vivarium_dashboard/lib/composite_test_run_views.py:111` (remove the `prune_runs` call); Test `tests/test_composite_test_run_durable.py`.
**Interfaces — Consumes:** none. Removes silent eviction so `simulations_index` keeps showing every run.

- [ ] **Step 1: Write the failing test** (drive the run path with fakes — monkeypatch `cr.generate_run_id`, `run_registry.count_running`→0, `run_registry.spawn_detached`→a fake pid — submit 25 runs for one spec, assert all 25 `runs_meta` rows remain):
```python
from pathlib import Path
from vivarium_dashboard.lib import composite_test_run_views as v
from vivarium_dashboard.lib import composite_runs as cr

def test_runs_are_durable_no_prune(tmp_path, monkeypatch):
    ws = tmp_path
    (ws / ".pbg").mkdir()
    monkeypatch.setattr(v.run_registry, "count_running", lambda *a, **k: 0)
    monkeypatch.setattr(v.run_registry, "spawn_detached", lambda *a, **k: 4321)
    n = 0
    def _id(spec_id, params=None, now=None):
        nonlocal n; n += 1
        return f"{spec_id}__{n}__abcdef"
    monkeypatch.setattr(v.cr, "generate_run_id", _id)
    for _ in range(25):
        body, status = v.composite_test_run(ws, {"id": "pkg.composites.x", "overrides": {}, "steps": 1})
        assert status == 202
    conn = cr.connect(ws / ".pbg" / "composite-runs.db")
    count = conn.execute("SELECT COUNT(*) FROM runs_meta WHERE spec_id=?", ("pkg.composites.x",)).fetchone()[0]
    assert count == 25  # nothing pruned
```
- [ ] **Step 2: Run → fail** (only 20 rows remain — prune evicted 5).
- [ ] **Step 3: Implement** — delete the prune call at `composite_test_run_views.py:111`:
```python
    conn = cr.connect(db_file)
    try:
        # SP-B: runs are durable — no prune-to-20 eviction. Deletion is an
        # explicit Sim-DB action (composite_runs.delete_run), not auto-eviction.
        cr.save_metadata(conn, spec_id=spec_id, run_id=run_id,
```
(Leave `composite_runs.prune_runs` / `PRUNE_KEEP` defined but unused — a caller may opt in later; just stop calling it here.)
- [ ] **Step 4: Run → pass.**  **Step 5: Commit** `feat(runs): durable runs — drop explorer prune-to-20 (SP-B)`.

---

### Task 3: `composite_runs.delete_run` (explicit deletion)
**Files:** Modify `vivarium_dashboard/lib/composite_runs.py`; Test `tests/test_composite_runs.py` (append).
**Interfaces — Produces:** `delete_run(conn, *, run_id) -> bool` — deletes the `runs_meta` row; returns `True` if a row was removed.

- [ ] **Step 1: Write the failing test**
```python
def test_delete_run_removes_row(tmp_path):
    import time
    from vivarium_dashboard.lib import composite_runs as cr
    conn = cr.connect(tmp_path / "r.db")
    cr.save_metadata(conn, spec_id="s", run_id="s__1__a", params={}, label="L",
                     started_at=time.time(), n_steps=1)
    assert cr.delete_run(conn, run_id="s__1__a") is True
    assert cr.query_run_meta(conn, run_id="s__1__a") is None
    assert cr.delete_run(conn, run_id="nope") is False
```
- [ ] **Step 2: Run → fail.**  **Step 3: Implement** (after `complete_metadata`):
```python
def delete_run(conn: sqlite3.Connection, *, run_id: str) -> bool:
    """Explicitly delete a run's metadata row. Returns True if a row was removed.
    (Store artifacts under .pbg/runs/<run_id>/ are removed by the caller that
    knows the workspace root.)"""
    cur = conn.execute("DELETE FROM runs_meta WHERE run_id=?", (run_id,))
    conn.commit()
    return cur.rowcount > 0
```
- [ ] **Step 4: Run → pass.**  **Step 5: Commit** `feat(runs): explicit delete_run (SP-B)`.

---

### Task 4: Refactor the explorer onto `invoke_run`
**Files:** Modify `vivarium_dashboard/lib/composite_test_run_views.py` (~line 92); Test `tests/test_composite_test_run_views.py` (existing parity tests must still pass + add a remote-build guard test).
**Interfaces — Consumes:** `run_core.invoke_run` (Task 1).

- [ ] **Step 1: Write the failing test** (a remote-build workspace → 409, not a silent local run):
```python
def test_composite_test_run_on_remote_build_409(tmp_path, monkeypatch):
    from vivarium_dashboard.lib import composite_test_run_views as v
    (tmp_path / ".pbg").mkdir()
    (tmp_path / ".viv-build.json").write_text('{"simulator_id": 66}')
    body, status = v.composite_test_run(tmp_path, {"id": "pkg.composites.x", "overrides": {}})
    assert status == 409
    assert "deployment" in (body.get("error") or "").lower()
```
- [ ] **Step 2: Run → fail** (currently runs locally / spawns).
- [ ] **Step 3: Implement** — replace the `generate_run_id` line with `invoke_run`, catching the seam:
```python
    from vivarium_dashboard.lib import run_core
    try:
        plan = run_core.invoke_run(ws_root, spec_id=spec_id, config=overrides,
                                   db_path=db_file, label=label, n_steps=0)
    except run_core.RunTargetUnavailable as e:
        return {"error": str(e)}, 409
    run_id = plan.run_id
```
(Keep everything after — `connect`, `save_metadata`, `spawn_detached`, the 202 — unchanged. `label` is the existing computed label.)
- [ ] **Step 4: Run → pass** (new 409 test + the existing `test_composite_test_run_views.py` parity tests).
- [ ] **Step 5: Commit** `refactor(explorer): run via invoke_run (route-by-source) (SP-A)`.

---

### Task 5: Refactor study runs onto `invoke_run`
**Files:** Modify `vivarium_dashboard/lib/study_runs.py` (baseline ~line 115, variant ~lines 322/343); Test `tests/test_study_runs.py` (existing parity + a remote-build guard test).
**Interfaces — Consumes:** `run_core.invoke_run`.

- [ ] **Step 1: Write the failing test** (study run on a remote-build workspace → 409):
```python
def test_study_run_baseline_on_remote_build_409(tmp_path, monkeypatch):
    from vivarium_dashboard.lib import study_runs
    # minimal study with a baseline composite under a remote-build workspace
    (tmp_path / ".viv-build.json").write_text("{}")
    sd = tmp_path / "studies" / "demo"; sd.mkdir(parents=True)
    (sd / "study.yaml").write_text("name: demo\nbaseline:\n  - {name: core, composite: pkg.composites.cell}\n")
    body, status = study_runs.run_study_baseline(tmp_path, {"study": "demo"})
    assert status == 409
```
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** — at each `run_id = cr.generate_run_id(spec_id, full_params)` site (baseline + the two variant sites), substitute:
```python
    from vivarium_dashboard.lib import run_core
    try:
        plan = run_core.invoke_run(ws_root, spec_id=spec_id, config=full_params,
                                   db_path=db_file, label=label, n_steps=params_n_steps)
    except run_core.RunTargetUnavailable as e:
        return {"error": str(e)}, 409
    run_id = plan.run_id
```
(`run_composite_subprocess(...)` still receives `run_id=run_id` and writes `runs_meta` itself — unchanged. Confirm `db_file` is in scope at each site; it is, just above each `generate_run_id`.)
- [ ] **Step 4: Run → pass** (new guard test + existing `test_study_runs.py`).
- [ ] **Step 5: Commit** `refactor(studies): run via invoke_run (route-by-source) (SP-A)`.

---

### Task 6: `save_run_as_variant`
**Files:** Create `vivarium_dashboard/lib/study_variants.py`; Test `tests/test_study_variants.py`.
**Interfaces — Produces:** `save_run_as_variant(workspace, *, run_id, source_db, study, variant_name) -> tuple[dict, int]` — reads the run's `(spec_id, params)` from `source_db`'s `runs_meta`, appends `{name, composite, parameter_overrides}` to the target `study.yaml`'s `variants:` (idempotent on `name`), returns `({"study", "variant", "composite"}, 200)`; `404` if study or run missing.

- [ ] **Step 1: Write the failing test**
```python
import time, yaml
from pathlib import Path
from vivarium_dashboard.lib import composite_runs as cr
from vivarium_dashboard.lib import study_variants

def test_save_run_as_variant_appends_to_study_yaml(tmp_path):
    src = tmp_path / "composite-runs.db"
    conn = cr.connect(src)
    cr.save_metadata(conn, spec_id="pkg.composites.cell", run_id="r1",
                     params={"k": 5}, label="fast", started_at=time.time(), n_steps=3)
    sd = tmp_path / "studies" / "demo"; sd.mkdir(parents=True)
    (sd / "study.yaml").write_text("name: demo\nbaseline:\n  - {name: core, composite: pkg.composites.cell}\n")
    body, status = study_variants.save_run_as_variant(
        tmp_path, run_id="r1", source_db=src, study="demo", variant_name="fast")
    assert status == 200 and body["composite"] == "pkg.composites.cell"
    spec = yaml.safe_load((sd / "study.yaml").read_text())
    var = [v for v in spec["variants"] if v["name"] == "fast"][0]
    assert var["composite"] == "pkg.composites.cell" and var["parameter_overrides"] == {"k": 5}
    # idempotent on name
    study_variants.save_run_as_variant(tmp_path, run_id="r1", source_db=src, study="demo", variant_name="fast")
    spec2 = yaml.safe_load((sd / "study.yaml").read_text())
    assert len([v for v in spec2["variants"] if v["name"] == "fast"]) == 1

def test_save_run_as_variant_missing_study_404(tmp_path):
    src = tmp_path / "r.db"; cr.connect(src)
    body, status = study_variants.save_run_as_variant(tmp_path, run_id="x", source_db=src, study="nope", variant_name="v")
    assert status == 404
```
- [ ] **Step 2: Run → fail.**  **Step 3: Implement** `vivarium_dashboard/lib/study_variants.py`:
```python
"""Save a run (its composite + config) as a named study variant. A study
variant IS a saved (composite, config) run — the data-model unification (SP-B)."""
from __future__ import annotations

from pathlib import Path

import yaml

from vivarium_dashboard.lib import composite_runs


def _study_yaml(workspace: Path, study: str) -> Path | None:
    for base in (Path(workspace) / "studies" / study,):
        p = base / "study.yaml"
        if p.is_file():
            return p
    # nested investigations/<inv>/studies/<study>/study.yaml
    for p in Path(workspace).glob(f"investigations/*/studies/{study}/study.yaml"):
        return p
    return None


def save_run_as_variant(workspace, *, run_id, source_db, study, variant_name):
    sf = _study_yaml(Path(workspace), study)
    if sf is None:
        return {"error": f"study not found: {study}"}, 404
    conn = composite_runs.connect(source_db)
    meta = composite_runs.query_run_meta(conn, run_id=run_id)
    if meta is None:
        return {"error": f"run not found: {run_id}"}, 404
    composite = meta.get("spec_id")
    config = meta.get("params") or {}
    spec = yaml.safe_load(sf.read_text(encoding="utf-8")) or {}
    variants = spec.setdefault("variants", [])
    entry = {"name": variant_name, "composite": composite, "parameter_overrides": config}
    for i, v in enumerate(variants):
        if isinstance(v, dict) and v.get("name") == variant_name:
            variants[i] = entry  # idempotent overwrite by name
            break
    else:
        variants.append(entry)
    sf.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
    return {"study": study, "variant": variant_name, "composite": composite}, 200
```
- [ ] **Step 4: Run → pass.**  **Step 5: Commit** `feat(studies): save a run as a study variant (SP-B)`.

## Self-Review
**Spec coverage:** SP-A (`invoke_run` + route-by-source seam) → Tasks 1, 4, 5; SP-B (durable persistence) → Task 2; explicit delete → Task 3; save-as-variant → Task 6. The `deployment` seam is exercised (Tasks 1/4/5 guard tests) and left for SP-D. **Placeholder scan:** none — every step has runnable code + commands. **Type consistency:** `invoke_run(...) -> RunPlan` with `run_id`/`target`/`config`/`db_path` is used identically in Tasks 4–5; `delete_run(conn, *, run_id) -> bool` and `save_run_as_variant(workspace, *, run_id, source_db, study, variant_name) -> (dict, int)` match their tests; reuses real `composite_runs` signatures (`generate_run_id(spec_id, params)`, `save_metadata(conn, *, spec_id, run_id, params, label, started_at, n_steps)`, `query_run_meta(conn, *, run_id)`).

**Open-question resolutions (from the spec):** (1) save-as-variant records the variant in `study.yaml` and leaves the run's row in its source store (no copy) — simplest, Sim DB already cross-references; (2) no auto-eviction — `prune_runs` stays defined but uncalled; (3) `launch()` shape resolved to "`invoke_run` returns a `RunPlan`; the caller launches" (avoids the double `runs_meta` write).

## Follow-on (separate plans)
- **SP-C** — shared "Configure & Run" UI embedded in Composites / Explorer / study Runs (+ a Save-as-variant button calling Task 6, + a delete action calling Task 3).
- **SP-D** — fill the `target="deployment"` seam: an sms-api composite-resolve/run path so remote-build composites run on the deployment.
