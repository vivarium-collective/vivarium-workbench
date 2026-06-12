# SP2b-i — The never-fabricate observable guard — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Program:** Active Investigation Framework, Layer 1 / SP2b (readout vocabulary), piece i. Program spec: `pbg-superpowers/docs/specs/2026-06-11-active-investigation-framework-design.md`.

**Goal:** Wire the already-built-but-ORPHANED `readout_validation` into a live path so the agent can answer "what can this composite actually emit?" and is stopped from authoring phantom observables. Two dashboard endpoints + a `/pbg-study check-observables` skill step. All deterministic (the validator is pure given a built composite); dashboard renders, the skill guides re-authoring — dashboard AI-free.

**Verified:** `pbg_superpowers.readout_validation.available_observables(core, state, schema=None) -> {leaves, catalogs}` and `validate_readouts(spec, *, available=...) -> [{name,status,detail}]` (status ∈ ok|unresolved|not_in_structure|aspirational) are importable in the dashboard venv. The dashboard already builds composites: `_get_composite_state` (server.py ~13017) via `build_generator(entry)` (~2921) / spec-parse, with a TTL cache `_COMPOSITE_STATE_CACHE` (server.py:159). `not_in_structure` = the never-fabricate flag (selector references an observable the composite doesn't expose).

**Tech:** Python, pytest. Repos: vivarium-dashboard (the 2 endpoints), pbg-superpowers (the skill — Task 3, separate branch). `.venv/bin/python`.

---

## Task 1: `GET /api/observables?ref=<composite_id>`

**Files:** Modify `vivarium_dashboard/server.py`; Test `tests/test_observables_endpoint.py`.

- [ ] **Step 1: Failing test** (a composite ref → its emittable observables):
```python
def test_observables_endpoint_lists_leaves_and_catalogs(tmp_v2ecoli_ws):
    body, code = server.Handler._observables_for_ref_test(ws, "v2ecoli.composites.baseline.baseline")
    assert code == 200
    d = json.loads(body)
    assert isinstance(d["leaves"], list) and d["leaves"]          # emittable dotted paths
    assert isinstance(d["catalogs"], dict)                        # {observable: [labels]} for LabeledArray ports
    # a known leaf exists (e.g. a listeners.* path); monomer_counts catalog present if labeled
def test_observables_endpoint_unknown_ref_clear_error():
    body, code = server.Handler._observables_for_ref_test(ws, "nope.not.a.composite")
    assert code >= 400
```
- [ ] **Step 2: fail. Step 3: implement** `_observables_for_ref(ws_root, ref) -> (body, code)`: reuse the SAME build `_get_composite_state` uses (`build_generator(entry)` for a generator id, else spec-parse) to get the built `state` + the live `core`; call `available_observables(core, state, schema)` (lazy import from `pbg_superpowers.readout_validation`, tolerant if absent → clear error); return `{"leaves": [...], "catalogs": {...}}`. Share `_COMPOSITE_STATE_CACHE` (building a whole-cell composite is ~3s). Add the `do_GET` branch `/api/observables` + route. On build failure / unknown ref → a clear 4xx with the error.
- [ ] **Step 4: pass. Step 5: commit** — `feat(server): GET /api/observables — emittable observables (leaves + catalogs) for a composite`

## Task 2: `GET /api/study-observable-check?study=<slug>` (validate a study's readouts)

**Files:** Modify `vivarium_dashboard/server.py`; Test `tests/test_observables_endpoint.py`.

- [ ] **Step 1: Failing test** (a study with a phantom readout → it's flagged `not_in_structure`):
```python
def test_study_observable_check_flags_phantom(tmp_study_with_phantom_readout):
    body, code = server.Handler._study_observable_check_test(ws, "the-study")
    assert code == 200
    res = json.loads(body)["readouts"]
    assert any(r["name"] == "phantom-one" and r["status"] == "not_in_structure" for r in res)
    assert any(r["status"] == "ok" for r in res)                 # a real one passes
def test_study_observable_check_uncomputable_composite_clear_status(tmp_study_unbuildable):
    body, code = server.Handler._study_observable_check_test(ws, "the-study")
    # if the study's composite can't build, return a clear non-crash status, not a 500
    assert code in (200, 422)
```
- [ ] **Step 2: fail. Step 3: implement** `_study_observable_check(ws_root, slug) -> (body, code)`: load the study spec (`study_io`); resolve its baseline composite ref; build it (reuse Task 1's `_observables_for_ref` build to get `available = available_observables(...)`); call `validate_readouts(study_spec, available=available)`; return `{"readouts": [{name, status, detail}], "composite": ref}`. If the composite can't build → a clear status (e.g. all readouts `aspirational` + a note, or 422), never a 500. Deterministic — the dashboard renders the `not_in_structure` ones as phantom-observable warnings. Add the `do_GET` branch + route.
- [ ] **Step 4: pass. Step 5: commit** — `feat(server): GET /api/study-observable-check — validate a study's readouts against its composite (never-fabricate)`

## Task 3: `/pbg-study check-observables` skill (separate repo: pbg-superpowers)

**Files:** (pbg-superpowers, branch `feat/sp2b-i-check-observables-skill` off origin/main) `skills/pbg-study/SKILL.md` (add the `check-observables` subcommand) + a guard test.

- [ ] **Step 1:** Add a `check-observables <slug>` section to `skills/pbg-study/SKILL.md`: call `GET /api/study-observable-check?study=<slug>` (or `validate_readouts` directly); report the per-readout statuses; for `not_in_structure` readouts, tell the agent these reference observables the composite does NOT emit — propose fixing the selector against `GET /api/observables?ref=<composite>` (the real emittable set) or removing it; for `aspirational`, note they're run-time-resolved (bulk ids) — acceptable but unverifiable at author time; for `unresolved`, the readout dialect can't be parsed — re-author. NEVER invent an observable — only select from the composite's actual `leaves`/`catalogs`.
- [ ] **Step 2:** Add `tests/test_check_observables_skill.py` asserting `skills/pbg-study/SKILL.md` mentions `study-observable-check`/`/api/observables` + `not_in_structure`.
- [ ] **Step 3: pass. Commit** (pbg-superpowers) — `feat(pbg-study): check-observables subcommand — validate readouts against the composite's real observables`

## Task 4: Golden + suite

**Files:** Test `tests/test_observables_endpoint.py` (skipif v2e-invest absent).

- [ ] **Step 1 (skipif `/Users/eranagmon/code/v2e-invest` absent, READ-ONLY):** on a real v2e-invest study with `store_path`/`identifier` readouts, `_study_observable_check` returns a status for each readout (some `ok`, the prose/`derived` ones `unresolved`/`aspirational`), no crash; `_observables_for_ref` on its composite returns non-empty `leaves`. v2e-invest untouched.
- [ ] **Step 2:** `tests/test_observables_endpoint.py` green; existing server tests no new failures (pre-existing environmental ones verified via base). **Commit** — `test(observables): v2e-invest golden + suite`

---

## Self-Review
- Coverage: `/api/observables` (T1), `/api/study-observable-check` (T2), the skill (T3), golden (T4). Wires the orphaned `available_observables`/`validate_readouts`. Matches SP2b-i scope.
- AI-free: the endpoints + validation are deterministic (the validator is pure given the built composite); the dashboard renders the statuses; the re-authoring judgment is in the skill only.
- No placeholders: reuses `_get_composite_state`'s build + `available_observables`/`validate_readouts`; the cache is shared.
- Deferred (SP2b-ii/iii, not here): auto-migration; routing the evaluator through the resolver.

## Notes for the executor
- `.venv/bin/python -m pytest`. REUSE the composite build (`build_generator`/spec-parse like `_get_composite_state`) + `_COMPOSITE_STATE_CACHE` + `available_observables`/`validate_readouts` (lazy import, tolerant if pbg_superpowers predates them). Do NOT reimplement composite building or observable introspection.
- Building a whole-cell composite is ~3s — the endpoint MUST share the TTL cache; tests should use a small/stub composite where possible.
- The "never-fabricate" value IS `not_in_structure` surfacing — make sure a readout pointing at a non-existent observable is flagged, not silently passed.
- Don't modify the real v2e-invest; the golden is read-only.
