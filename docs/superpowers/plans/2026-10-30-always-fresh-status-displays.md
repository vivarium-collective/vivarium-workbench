# Always-Fresh Status Displays Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every mechanical status badge in the workbench derive from evidence (runs.db + report cards) on read so it can never go stale, backed by a minimal schema-aware `reconcile` that persists only `execution_status`.

**Architecture:** A new pure `StudyEvidence` bundle (`lib/study_evidence.py`) computed once per study per request from the same anchors the live surfaces already use. The existing derivations (`study_ontology.derive_execution_status`/`derive_verdict`) and the investigation-level rollups (`investigation_status._study_display_status`/`compute_investigation_status`) gain an `evidence` parameter and apply a fixed precedence (mechanical = evidence wins; verdict/expert-review = authored wins). A separate `reconcile` writes the derived `execution_status` back, mapped to the served workspace's own schema and validated before write. Display freshness needs no write; reconcile is explicit (endpoints) + called from the post-run flush.

**Tech Stack:** Python 3, FastAPI, pydantic, PyYAML, SQLite (runs.db), pytest. Vanilla JS frontend (no bundler) — JS reads the same payload keys, so no JS changes are required for freshness (the server payload becomes fresh).

**Spec:** docs/specs/2026-10-30-always-fresh-status-displays.md

## Global Constraints

- vivarium-workbench repo only; lands on `main`. No discovery-engine change.
- No AI attribution in commit messages or PR body (CLAUDE.md overrides any default).
- The evidence→status core is a **pure function** of `(study_spec, evidence)`; testable with a fixture workspace, no network.
- **Precedence (load-bearing):** mechanical execution ← latest-run evidence wins over any stored `simulation_status`/`status` (even a stored `failed` a newer successful run supersedes); `verdict` and `expert_review` ← authored wins, never auto-written or overridden.
- Gate/evaluation pass-fail is **display-only**, never persisted to a stored field. Reconcile writes **only** `execution_status`.
- **Schema-mapped writes:** reconcile resolves the served workspace's `.pbg/schemas/study.schema.json`, writes the derived execution value into whatever mechanical field+enum that workspace has, and validates the value against that enum before writing. Never writes a value/field invalid for the target schema. No workspace migration is a precondition.
- Evidence is read **once per study per request** (request-scoped), never once per badge.
- TDD throughout: every new function gets a test watched to fail first.

## Review Focus

- **Mixed run history (failed→completed):** a study whose runs.db has a failed row and a later completed row must derive execution `completed`, not `failed`. (Test in Task 1.)
- **Schema with neither `execution_status` nor `completed` enum:** reconcile must map `completed`→the workspace's nearest valid value (e.g. `ran`) and never write an out-of-enum value. (Test in Task 2 + Task 4.)
- **Authored human verdict present:** derive and reconcile must leave an authored `verdict` untouched. (Test in Task 3 + Task 4.)
- **Report cards but zero runs, or runs but zero cards (the S5 "runs failed / tests passed" case):** execution and gate are independent axes and must not be forced to agree. (Test in Task 1.)
- **Many-study investigation performance:** evidence read once per study, not once per badge — a payload build over N studies opens each runs.db at most once. (Test in Task 5.)

---

## Task 1: `StudyEvidence` bundle + pure evidence probes

**Files:**
- Create: `vivarium_workbench/lib/study_evidence.py`
- Test: `tests/test_study_evidence.py`

**Interfaces:**
- Consumes: `lib/study_spec.read_runs_db_for_study` (latest run row + status), `lib/study_spec.has_graded_report_cards` / the report-card `*.verdict.json` reader, `lib/workspace_paths` for the study dir. **Step 1 confirms their exact current signatures and the literal run-status strings written into runs.db** (grep the run writer in `lib/run_runner.py` / `lib/run_store.py` / `lib/composite_runs.py`).
- Produces:
  - `@dataclass(frozen=True) class StudyEvidence: execution: str; gate: str; evaluation: str; tests_passed: int; tests_total: int; n_runs: int` where `execution ∈ {"completed","failed","running","not_run"}`, `gate ∈ {"passed","failed","not_started"}`, `evaluation ∈ {"evaluated","not_evaluated"}`.
  - `def evidence_for_study(ws_root: Path, slug: str) -> StudyEvidence` — pure; reads runs.db + report cards; no writes.

- [ ] **Step 1: Confirm evidence-source signatures and run-status literals.** Read `read_runs_db_for_study`, `has_graded_report_cards`, and the report-card verdict reader; grep the run writer for the exact status strings stored per run. Record them in the test as the fixture's ground truth. (No code yet.)

- [ ] **Step 2: Write the failing test** `tests/test_study_evidence.py`:

```python
def test_evidence_completed_and_gate_passed_for_ran_study(fixture_ws):
    ev = evidence_for_study(fixture_ws, "s3-protrusions-to-enclosure")
    assert ev.execution == "completed"   # runs.db has completed rows
    assert ev.gate == "passed"           # all graded report cards pass
    assert (ev.tests_passed, ev.tests_total) == (2, 2)

def test_latest_run_wins_over_earlier_failure(fixture_ws):
    # a study with a failed row then a completed row
    ev = evidence_for_study(fixture_ws, "mixed-history")
    assert ev.execution == "completed"

def test_execution_and_gate_are_independent(fixture_ws):
    # runs failed but a report card passed (the S5 shape) -> don't force agreement
    ev = evidence_for_study(fixture_ws, "runs-failed-tests-passed")
    assert ev.execution == "failed"
    assert ev.gate == "passed"

def test_no_runs_no_cards_is_not_run(fixture_ws):
    ev = evidence_for_study(fixture_ws, "untouched")
    assert ev.execution == "not_run" and ev.gate == "not_started"
```

- [ ] **Step 3: Run tests, verify they fail** (`pytest tests/test_study_evidence.py -v`) — Expected: FAIL (module/function not defined). Build the required fixture studies under `tests/_fixtures/` if absent (runs.db rows + `viz/report_card/*.verdict.json`).

- [ ] **Step 4: Implement `study_evidence.py`.** Mapping: `execution` = `completed` if ≥1 completed/ran row and none running; `failed` if rows exist and all failed; `running` if any running; `not_run` if none. `gate` = `passed` if all graded cards pass; `failed` if any fail; `not_started` if none graded. `evaluation` = `evaluated` if any graded card/conclusion exists else `not_evaluated`. `tests_*` from the live behavior-test computation (shared with Task 5's B1).

- [ ] **Step 5: Run tests, verify pass.** Then run the whole suite.

- [ ] **Step 6: Commit** (`feat(status): pure StudyEvidence bundle from runs.db + report cards`).

---

## Task 2: Schema-aware mechanical-field resolver + value mapper

**Files:**
- Create: `vivarium_workbench/lib/study_schema.py`
- Test: `tests/test_study_schema.py`

**Interfaces:**
- Consumes: `lib/workspace_paths` to locate `.pbg/schemas/study.schema.json`. **Step 1 confirms where/how the workspace study schema is read today** (grep `study.schema.json`).
- Produces:
  - `def mechanical_exec_field(ws_root: Path) -> tuple[str, frozenset[str]]` — returns the non-deprecated mechanical execution field name and its enum for the served workspace (canonical → `("execution_status", {proposed,planned,running,completed,failed})`; older viva-genesis → `("simulation_status", {not_run,running,ran,failed})`). Resolves `x-replaced-by` / deprecation markers; prefers the canonical field when present.
  - `def map_execution_value(canonical: str, enum: frozenset[str]) -> str` — maps a canonical `StudyEvidence.execution` value to the nearest valid enum member (`completed`→`ran` when `completed` absent but `ran` present; `not_run` passthrough; etc.). Raises if no safe mapping exists (caller then skips the write).

- [ ] **Step 1: Confirm schema-read path.** (No code.)
- [ ] **Step 2: Write failing test** with two inline schema fixtures (canonical + older):

```python
def test_resolves_canonical_field():
    field, enum = mechanical_exec_field(canonical_ws)
    assert field == "execution_status" and "completed" in enum

def test_resolves_older_simulation_status_field():
    field, enum = mechanical_exec_field(older_ws)
    assert field == "simulation_status" and "completed" not in enum and "ran" in enum

def test_maps_completed_to_ran_when_absent():
    assert map_execution_value("completed", frozenset({"not_run","running","ran","failed"})) == "ran"

def test_maps_identity_when_present():
    assert map_execution_value("completed", frozenset({"planned","running","completed","failed"})) == "completed"
```

- [ ] **Step 3: Run, verify fail.**
- [ ] **Step 4: Implement `study_schema.py`.**
- [ ] **Step 5: Run tests + suite, verify pass.**
- [ ] **Step 6: Commit** (`feat(status): schema-aware mechanical-field resolver + value mapper`).

---

## Task 3: Evidence-aware derivation (precedence rule)

**Files:**
- Modify: `vivarium_workbench/lib/study_ontology.py` (`derive_execution_status`, `derive_verdict`)
- Test: `tests/test_study_ontology.py` (extend; create if absent)

**Interfaces:**
- Consumes: `StudyEvidence` (Task 1).
- Produces: back-compatible signatures with a new optional param:
  - `derive_execution_status(spec: dict, node_status: str = "", evidence: "StudyEvidence | None" = None) -> str`
  - `derive_verdict(spec: dict, evidence: "StudyEvidence | None" = None)` (evidence does **not** author a verdict; it only promotes execution — verdict still authored-only, so evidence is accepted for signature symmetry but must never fabricate a verdict).
- Precedence: when `evidence` is given, mechanical execution = `evidence.execution` mapped to the ontology's EXEC vocab, **overriding** a stale stored `not_run`/`planned`/`simulation_status`; an authored terminal value in `spec["execution_status"]` that a human set still wins only if there is no contradicting newer run. `derive_verdict` unchanged except it never reads evidence to invent a verdict.

- [ ] **Step 1: Write failing tests:**

```python
def test_evidence_promotes_stale_not_run():
    spec = {"simulation_status": "not_run", "status": "planned"}
    ev = StudyEvidence(execution="completed", gate="passed", evaluation="evaluated", tests_passed=2, tests_total=2, n_runs=5)
    assert derive_execution_status(spec, evidence=ev) == "completed"

def test_authored_verdict_never_overridden_by_evidence():
    spec = {"verdict": "refuted"}
    ev = StudyEvidence(execution="completed", gate="passed", evaluation="evaluated", tests_passed=1, tests_total=1, n_runs=1)
    assert derive_verdict(spec, evidence=ev) == "refuted"

def test_no_evidence_preserves_legacy_behavior():
    spec = {"simulation_status": "not_run", "status": "planned"}
    assert derive_execution_status(spec) == "planned"   # unchanged when evidence=None
```

- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** the evidence branch + precedence; keep the `evidence=None` path byte-identical to today.
- [ ] **Step 4: Run tests + suite, verify pass** (existing ontology tests must stay green).
- [ ] **Step 5: Commit** (`feat(status): evidence-aware derivation with fixed precedence`).

---

## Task 4: `reconcile_study_status` + `reconcile_investigation`

**Files:**
- Create: `vivarium_workbench/lib/study_reconcile.py`
- Test: `tests/test_study_reconcile.py`

**Interfaces:**
- Consumes: `evidence_for_study` (T1), `mechanical_exec_field`/`map_execution_value` (T2), `lib/atomic_io` for the write, `lib/workspace_paths`.
- Produces:
  - `def reconcile_study_status(ws_root: Path, slug: str) -> dict` → `{"changed": bool, "field": str, "before": Any, "after": Any, "skipped": str|None}`. Writes the mapped execution value into the resolved mechanical field **only when it differs**; never touches `verdict`/`expert_review`; never writes an out-of-enum value (skips with a reason).
  - `def reconcile_investigation(ws_root: Path, slug: str) -> dict` → `{"studies": [per-study results]}` looping member studies.

- [ ] **Step 1: Write failing tests** (copy a fixture ws into tmp_path so writes are isolated):

```python
def test_reconcile_writes_mapped_execution_value(tmp_ws_older_schema):
    out = reconcile_study_status(tmp_ws, "s3-...")
    assert out["changed"] and out["field"] == "simulation_status" and out["after"] == "ran"

def test_reconcile_never_touches_verdict(tmp_ws):
    before = read_yaml(study_path)["verdict"]
    reconcile_study_status(tmp_ws, "s3-...")
    assert read_yaml(study_path)["verdict"] == before

def test_reconcile_is_noop_when_already_fresh(tmp_ws):
    reconcile_study_status(tmp_ws, "s3-...")
    out = reconcile_study_status(tmp_ws, "s3-...")   # second call
    assert out["changed"] is False

def test_reconcile_stays_in_workspace(tmp_ws):
    # byte-compare every file outside the target study.yaml is unchanged
    ...
```

- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** `study_reconcile.py` (atomic write; drift check; verdict untouched).
- [ ] **Step 4: Run tests + suite, verify pass.**
- [ ] **Step 5: Commit** (`feat(status): schema-aware reconcile of execution_status`).

---

## Task 5: Wire derive-on-read into study-card + graph payloads, and B1

**Files:**
- Modify: `vivarium_workbench/lib/report_views.py` (study-card payload; behavior-tests verdict for B1)
- Modify: `vivarium_workbench/lib/investigation_graph_views.py` (study nodes)
- Modify (if B1 shared): `vivarium_workbench/lib/behavior_test_card.py`
- Test: `tests/test_report_views_freshness.py`

**Scope note — all card sub-badges ride the one threaded evidence:** the card
payload feeds the Progress pill, the **status dot** (Accepted/Investigating/…),
the **computed gate verdict**, and the **rigor split (pins X/Y)**. All of these
derive from the single `evidence_for_study` read threaded here — the status dot
and computed gate use `evidence.execution`/`evidence.gate` instead of the frozen
`gate_status`/`confidence`/`pipeline_gate.gate_evaluator`, and the rigor split
uses the live behavior-test outcomes (B1) instead of `runs[].outcomes`. The test
asserts each of these four sub-badges is fresh, not just the main pill.

**Interfaces:**
- Consumes: `evidence_for_study` (T1), evidence-aware `derive_*` (T3).
- Produces: the study-card payload keys `execution_status`/`verdict` (and graph node fields) are now computed with `evidence=evidence_for_study(...)`; the `"tests"`/`passed`/`total` keys (B1) are computed live from the same evidence, not from frozen `runs[].outcomes`. Evidence is read **once per study** in the iteration and threaded into both the card and graph node builders.

- [ ] **Step 1: Locate** the card-payload build loop and the behavior-tests block (function names from the interface sheet), and the graph node build. (No code.)
- [ ] **Step 2: Write failing test** — build the payload for the fixture investigation whose stored fields say `not_run` but evidence says completed; assert the card's `execution_status == "completed"`, `tests` reflect live counts, and (perf) that `evidence_for_study` is called once per study (monkeypatch-count).

- [ ] **Step 3: Run, verify fail.**
- [ ] **Step 4: Implement** the threading; add a tiny request-scoped cache so a study probed by both card and graph in one request reads runs.db once.
- [ ] **Step 5: Run tests + suite, verify pass.**
- [ ] **Step 6: Commit** (`feat(status): study card + graph derive from evidence; B1 live test counts`).

---

## Task 6: B2 — evidence-fresh investigation rollup (list == detail)

**Files:**
- Modify: `vivarium_workbench/lib/investigation_status.py` (`_study_display_status`, `compute_investigation_status`)
- Modify: `vivarium_workbench/lib/report_views.py` (detail route rollup call site)
- Modify: `vivarium_workbench/lib/plan_board.py` (the A→E plan-progress rows)
- Test: `tests/test_investigation_rollup_freshness.py`

**Scope note — the plan-progress board (A→E rows):** `plan_board.py` builds the
A→E rows from members' frozen `gate_status` + `findings[].status`. Thread the
per-study evidence in so each row's completion reflects `evidence.gate`/
`evidence.execution` rather than the frozen fields. The test asserts the board
advances for an investigation whose stored gate fields are stale but whose
evidence shows passed gates.

**Interfaces:**
- Consumes: `evidence_for_study` (T1), evidence-aware derivation (T3).
- Produces: `_study_display_status(...)` and `compute_investigation_status(...)` accept an `evidence`-per-study input (new optional param; signature change) and derive mechanical status from it; the investigation **detail** rollup is switched to the same computation the **summary** route uses, both now evidence-fresh.

- [ ] **Step 1: Write failing test** — same investigation via summary route and detail route return the **same** effective status, and both reflect evidence (not the frozen `status`).
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** the param threading + unify the detail rollup on the summary computation.
- [ ] **Step 4: Run tests + suite, verify pass.**
- [ ] **Step 5: Commit** (`fix(status): investigation detail rollup evidence-fresh, matches summary (B2)`).

---

## Task 7: Reconcile endpoints + post-run flush hook

**Files:**
- Modify: `vivarium_workbench/api/app.py` (two POST routes)
- Modify: `vivarium_workbench/lib/models.py` (response models)
- Modify: `vivarium_workbench/lib/study_runs.py` (`_run_post_run_flush` calls reconcile)
- Test: `tests/test_reconcile_endpoints.py`

**Interfaces:**
- Consumes: `reconcile_study_status`/`reconcile_investigation` (T4).
- Produces:
  - `POST /api/study/{slug}/reconcile-status` → `ReconcileResult` model.
  - `POST /api/investigation/{slug}/reconcile-status` → `ReconcileInvestigationResult` model.
  - A call to `reconcile_study_status` appended to `_run_post_run_flush` so a completed run self-heals the stored `execution_status`.
  - Both routes use the `get_workspace` dependency; CSRF is handled by the global `_csrf_mw` middleware, so no per-route guard is needed (see appendix). Pattern: `def route(req: SomeBody, ws: Path = Depends(get_workspace)) -> dict`.

- [ ] **Step 1: Confirm the POST route pattern** from `api/app.py` L4537 (appendix has it). (No code.)
- [ ] **Step 2: Write failing test** using the `dashboard_client` fixture: POST the study reconcile endpoint against a fixture ws whose stored field is stale; assert 200 + `changed: true` + the study.yaml now reflects evidence; POST again → `changed: false`. A separate test: after a simulated run flush, the stored field is reconciled.
- [ ] **Step 3: Run, verify fail.**
- [ ] **Step 4: Implement** routes + models + the flush hook.
- [ ] **Step 5: Run tests + suite, verify pass.**
- [ ] **Step 6: Commit** (`feat(status): reconcile endpoints + post-run self-heal`).

---

## Notes for the executor

- Tasks 1 and 2 are foundational and independent; 3 depends on 1; 4 on 1+2; 5 on 1+3; 6 on 1+3; 7 on 4. 5 and 6 can be done in either order.
- The frontend needs **no** changes for freshness — the JS reads the same payload keys, which become fresh server-side. (A later, separate task could surface a "Reconcile" button; out of scope here.)
- After all tasks: run `reconcile-investigation` on the eukaryogenesis workspace as the acceptance check (S1–S5 execution flips to true state, gate shows live).

---

## Appendix: confirmed interfaces (ground truth — resolves the "Step 1 confirm" steps)

Package root is `vivarium_workbench/lib/` (not top-level `lib/`). All verified on this worktree.

**Task 1 — evidence sources & vocab:**
- `study_spec.read_runs_db_for_study(ws_root: Path, name: str) -> list[dict]` (L426) — newest-first; row keys include `run_id, status, n_steps, completed_at, source`. **No `heartbeat_at`** (so runs.db can never drive "running"; only study.yaml `runs[]` can).
- `runs_meta.status` vocabulary written by `composite_runs`: **`running, completed, failed, orphaned, cancelled`** (+ `ran` / `completed` only via the `simulations`-table and `.pbg/runs.jsonl` merge fallbacks). Treat "completed" = `study_spec._COMPLETE_STATUSES = {complete, completed, ran, done}` (L1653); `orphaned`/`cancelled` are NOT success.
- Gate from report cards: `study_spec.has_graded_report_cards(ws_root, slug) -> bool` (L1607); per-card verdict via `study_spec._report_card_verdict_and_subject(path) -> (verdict, subject, counts)` (L1387), verdict ∈ `{within_tol, drift, mismatch, ungraded}`. **Map:** gate `passed` = all graded cards `within_tol`; `failed` = any `mismatch` **or** any `drift`; `not_started` = none graded. (Report-card vocab is a DIFFERENT axis from study verdict `{supported,qualified,inconclusive,refuted}`.)
- Live behavior-test counts for B1: read the latest run's `.pbg/runs/<run_id>/report.json` (and `test_diff.json`) — these are the live per-run evaluation artifacts (vs. frozen `study.yaml runs[].outcomes`). Confirm `report.json` shape in Step 1 and derive `tests_passed/tests_total` from it.
- **Fixtures: none ship a `runs.db` or `*.verdict.json`.** Build evidence at runtime in `tmp_path` via `composite_runs.save_metadata(...)` + `complete_metadata(..., status=...)` and by writing `studies/<slug>/viz/report_card/<card>.verdict.json`. Copy patterns from `tests/test_composite_runs.py` + `tests/test_report_card_promotion.py`. The `dashboard_client` fixture (`tests/conftest.py` L196) spawns the real server for endpoint tests.

**Task 2 — schema resolution:** read `WorkspacePaths(ws_root).pbg / "schemas" / "study.schema.json"` (pattern: `field_migrate.migrate_fields` L120-149). Parse `schema["properties"]` for the mechanical field + its `enum`, honoring `deprecated` + scalar `x-replaced-by` (reuse the logic shape of `field_migrate.safe_renames_from_schema` L40). Canonical mechanical field = `execution_status`; older viva-genesis = `simulation_status` (enum `{not_run,running,ran,failed}`).

**Task 3 — derivation signatures:** `derive_execution_status(spec, node_status="")` (L47, returns `EXEC_VALUES`), `derive_verdict(spec)` (L82). Add optional `evidence` param to both; keep the `evidence=None` path byte-identical. `derive_confidence` is **not** here — it's `discovery_engine.study_verdict.derive_confidence` (used by graph L135); leave it untouched (no engine change, confidence not in scope).

**Task 5 — card/graph anchors:** card loop in `report_views.build_iset_detail(ws_root, name)` (L899), `for slug in investigation_member_slugs(spec)` (L970); execution/verdict set at **L1180/L1181**; behavior-tests `_beh_summary` at L1091-1107; `_eff_status` at L1051 (+ report-card safeguard L1058-1067). Graph: `investigation_graph_views.build_investigation_graph(ws_root, inv_slug)` (L72), nodes at L127-129, execution/verdict at **L144-147**, report-card promotion L105-114.

**Task 6 — rollup/B2:** `investigation_status._study_display_status(ws_root, slug, spec)` (L86) — already takes the **spec + ws_root** (add an `evidence` param or let it call `evidence_for_study`). `compute_investigation_status(study_statuses: list[str], has_runs=None)` (L135) — takes **status strings**. **B2 root cause:** `report_views` L1206 builds `member_statuses` from each study's **raw `status`**, while the card/graph use the fresher `_eff_status` (computed at L1051). Fix = feed the evidence-fresh effective statuses (not raw `status`) into the L1206 rollup. Plan board: `plan_board.py` A→E rows.

**Task 7 — endpoints/flush:** POST pattern (`api/app.py` L4537): `def route(req: SomeBody, ws: Path = Depends(get_workspace)) -> dict`. **CSRF is a global middleware** (`_csrf_mw`, L666) — a new POST needs **no** per-route guard (correct the earlier note; `VIVARIUM_WORKBENCH_DISABLE_CSRF=1` bypasses in tests). `get_workspace` at L435 (overridable via `dependency_overrides`). Flush insertion point: `study_runs._run_post_run_flush(...)` (L170) — add the reconcile call around **L348**, after `auto_evaluate.evaluate_on_run_completion` (L336-338) and before `lifecycle_mutations._sync_parent_investigation` (L349).
