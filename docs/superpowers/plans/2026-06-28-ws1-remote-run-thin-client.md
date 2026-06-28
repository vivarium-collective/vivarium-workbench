# WS1 — Remote-run thin client (two-phase) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Replace the dashboard's client-side daemon-thread remote-run pipeline with a thin, stateless **client-orchestrated two-phase flow** over sms-api, so durability lives in sms-api's Postgres and a dashboard restart never loses an in-flight run.

**Architecture:** sms-api separates *build* from *run* (`run_simulation` requires the build COMPLETE), so a single-submit client isn't possible. The thin client is two phases the **JS drives**: `build-start → poll build status → run-submit → poll run status → land-on-demand`. Each dashboard route is a thin, stateless mapper over one sms-api call. Builds on the approved `2026-06-26-remote-run-thin-client-design.md` (+ its build↔run addendum, PR #362).

**Tech Stack:** Python 3.12 stdlib `urllib` (existing `SmsApiClient`); FastAPI routes in `api/app.py`; vanilla JS panel in `static/study-detail.js`.

## Global Constraints
- **No new dependencies.** sms-api client is stdlib urllib. Tests monkeypatch the client — never hit a real sms-api.
- **Python-first, AI-free.** Builders are pure `(ws_root, body) -> (dict, status)` functions, module-bound externals for fakes (mirror `remote_run_views.remote_run_start`).
- **Behaviour CHANGES** (multi-step strip → two-phase build/run states) — this is NOT a byte-identical port; update the JS consumer + tests accordingly.
- Test from the worktree: `cd /Users/eranagmon/code/vdash-remote-dash && PYTHONPATH=$PWD /Users/eranagmon/code/v2ecoli/.venv/bin/python -m pytest <path> -v`.

## Validatability split (READ FIRST)
- **Headless-testable (do now, this plan's R1–R5):** the Python builders, route wiring, and deletion — fully unit-tested with fakes.
- **In-the-loop ONLY (R-UX, deferred):** the JS panel two-phase redesign + the live cutover. Per the design addendum, this "cannot be validated from a headless/sandboxed session" — it needs a live sms-api tunnel + a human watching the panel. Build it WITH the user, validated against `ptools-proxy.sh -s smsvpctest`.

---

### Task R1: Confirm the sms-api client surface (no rewrite expected)
**Files:** Verify `vivarium_dashboard/lib/sms_api_client.py`; Test `tests/test_sms_api_client.py`.
**Interfaces (already present — confirm signatures):** `upload_simulator(simulator: dict, force=False) -> dict` (→ `database_id`), `simulator_status(simulator_id) -> dict`, `run_simulation(*, simulator_id, num_generations, num_seeds, run_parca, observables, ...) -> dict` (→ `database_id`), `simulation_status(simulation_id) -> dict`, `download_data(simulation_id, dest_dir, timeout=None) -> Path`.

- [ ] **Step 1:** Confirm each method exists with the above signature (grep `def ` in `sms_api_client.py`). No code change expected — the two-phase flow uses only existing calls. If any is missing, add it TDD-style (test: monkeypatch `urlopen`, assert URL/verb), else proceed.
- [ ] **Step 2:** Commit only if a method was added: `git commit -m "feat(sms-api-client): <method> for thin-client"`. Otherwise skip.

---

### Task R2a: `remote-run-start` → build-start only
**Files:** Create builder in `vivarium_dashboard/lib/remote_run_views.py` (replace `remote_run_start`); Modify route `api/app.py:/api/remote-run-start`; Test `tests/test_remote_run_views_lib.py`.
**Interfaces — Produces:** `remote_run_build_start(ws_root, body) -> (dict, int)`. Happy path: `({"simulator_id": <int>, "phase": "building", "branch": <str>, "commit": <sha>}, 202)`. Same guard ladder as today (401 not-authenticated / 400 study-required / 409 no-remote / 409 unresolved-url / 404 study-not-found). Does push + `upload_simulator`; does **NOT** poll the build (the JS polls via status).

- [ ] **Step 1: Write failing tests** (mirror existing `test_remote_run_views_lib.py` fakes: module-bind `github_auth`, `git_status`, `study_spec`, `load_spec`, `SmsApiClient`):
```python
def test_build_start_returns_simulator_id_and_building_phase(monkeypatch, tmp_path):
    _wire_fakes(monkeypatch, authed=True, repo_url="https://github.com/o/r",
                study_exists=True, upload_returns={"database_id": 66})
    body, status = rrv.remote_run_build_start(tmp_path, {"study": "s"})
    assert status == 202
    assert body["simulator_id"] == 66 and body["phase"] == "building"

def test_build_start_unauthenticated_401(monkeypatch, tmp_path):
    _wire_fakes(monkeypatch, authed=False)
    assert rrv.remote_run_build_start(tmp_path, {"study": "s"})[1] == 401

def test_build_start_missing_study_400(monkeypatch, tmp_path):
    _wire_fakes(monkeypatch, authed=True)
    assert rrv.remote_run_build_start(tmp_path, {})[1] == 400
```
(Define `_wire_fakes` once in the test module, monkeypatching the module-bound externals — see the existing pattern around `remote_run_start`'s tests.)
- [ ] **Step 2: Run → fail** (`remote_run_build_start` undefined).
- [ ] **Step 3: Implement** `remote_run_build_start` — copy the guard ladder + repo/branch/spec resolution from `remote_run_start` (lines 56–77), then:
```python
    client = SmsApiClient(_sms_api_base())
    commit = git_status.remote_push_and_sha(ws_root)
    uploaded = client.upload_simulator(
        {"git_commit_hash": commit, "git_repo_url": repo_url, "git_branch": branch}
    )
    return {"simulator_id": uploaded["database_id"], "phase": "building",
            "branch": branch, "commit": commit}, 202
```
- [ ] **Step 4: Run → pass.**
- [ ] **Step 5:** Point the `api/app.py:/api/remote-run-start` route at `remote_run_build_start` (it already calls into `remote_run_views`); keep the `JSONResponse` wrap.
- [ ] **Step 6: Commit** `feat(remote-run): start = build-start only (two-phase thin client)`.

---

### Task R2b: `remote-run-submit` → issue the run
**Files:** Add builder `remote_run_submit` to `remote_run_views.py`; Add route `POST /api/remote-run-submit` in `api/app.py`; Test `tests/test_remote_run_views_lib.py`.
**Interfaces — Consumes:** `{simulator_id, study, num_generations?, num_seeds?, run_parca?}`. **Produces:** `remote_run_submit(ws_root, body) -> (dict, int)`; happy path `({"simulation_id": <int>, "phase": "running"}, 202)`; `404` if study spec missing; `400` if `simulator_id` missing; `401` if unauthenticated.

- [ ] **Step 1: Write failing test:**
```python
def test_submit_issues_run_and_returns_simulation_id(monkeypatch, tmp_path):
    _wire_fakes(monkeypatch, authed=True, study_exists=True,
                run_returns={"database_id": 199})
    body, status = rrv.remote_run_submit(tmp_path, {"simulator_id": 66, "study": "s"})
    assert status == 202 and body["simulation_id"] == 199 and body["phase"] == "running"

def test_submit_missing_simulator_id_400(monkeypatch, tmp_path):
    _wire_fakes(monkeypatch, authed=True, study_exists=True)
    assert rrv.remote_run_submit(tmp_path, {"study": "s"})[1] == 400
```
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** — auth + study-spec resolution (for `observables` via `study_spec.collect_study_observables`), then:
```python
    sim_id = body.get("simulator_id")
    if not sim_id:
        return {"error": "simulator_id is required"}, 400
    client = SmsApiClient(_sms_api_base())
    sim = client.run_simulation(
        simulator_id=int(sim_id),
        num_generations=int(body.get("num_generations") or 1),
        num_seeds=int(body.get("num_seeds") or 1),
        run_parca=bool(body.get("run_parca", True)),
        observables=observables,
    )
    return {"simulation_id": sim["database_id"], "phase": "running"}, 202
```
- [ ] **Step 4: Run → pass.**  **Step 5:** Wire the new route. **Step 6: Commit** `feat(remote-run): submit = issue run once build is done`.

---

### Task R3: `remote-run-status` → on-demand sms-api read
**Files:** Replace `remote_run_status` reader (currently reads `remote_run_jobs.manager`) with an on-demand sms-api mapper; Modify route `api/app.py:/api/remote-run-status`; Test `tests/test_remote_run_endpoints.py`.
**Interfaces — Consumes** query `?simulator_id=&simulation_id=`. **Produces:** `remote_run_status(params) -> (dict, int)`. If `simulation_id` present → map `simulation_status`; else if `simulator_id` present → map `simulator_status`; else `400`. Mapped shape: `{"phase": "building"|"running"|"done"|"failed", "raw_status": <str>, "error": <str|None>}`.

- [ ] **Step 1: Write failing tests** (fake client returns a status dict; assert the phase mapping for `completed`→`done`, `failed`→`failed`, `running`→`running`/`building`).
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** the mapper using the existing terminal-set logic (reuse `_TERMINAL_OK`/`_TERMINAL_BAD` semantics, relocated out of the to-be-deleted `remote_run_jobs`).
- [ ] **Step 4: Run → pass.**  **Step 5:** Wire route (drop the `manager` read). **Step 6: Commit** `feat(remote-run): status = on-demand sms-api read (no in-process state)`.

---

### Task R4: `remote-run-land` → explicit, on-demand
**Files:** Add builder `remote_run_land`; Add route `POST /api/remote-run-land`; Test `tests/test_remote_run_views_lib.py`.
**Interfaces — Consumes** `{simulation_id, study, experiment_id?, commit?, s3_uri?}`. **Produces:** `remote_run_land(ws_root, body) -> (dict, int)`; downloads via `client.download_data(simulation_id, tmpdir)` then `land_remote_run(study_dir, ...)`; returns `({"run_id": <str>}, 200)`; `404` if study missing.

- [ ] **Step 1: Write failing test** (fake `download_data` → a temp tar; fake `land_remote_run` → a run_id; assert the run_id is returned and `land_remote_run` got the study dir).
- [ ] **Step 2: Run → fail.**  **Step 3: Implement** (mirror the land step from `run_remote_pipeline` lines 199–213, but standalone). **Step 4: Run → pass.**  **Step 5:** Wire route. **Step 6: Commit** `feat(remote-run): explicit on-demand land route`.

---

### Task R5: Delete the daemon-thread machinery
**Files:** Modify `vivarium_dashboard/lib/remote_run_jobs.py` (delete `RemoteRunManager`, `RemoteRunJob`, `run_remote_pipeline`, `PipelineCtx`, `_poll`, `STEP_NAMES`, `manager`); remove their imports in `remote_run_views.py` + `api/app.py`; delete/retarget `tests/test_remote_run_jobs.py`.
**Precondition:** R2a–R4 merged and the JS panel (R-UX) cut over — do NOT delete while anything still imports the manager.

- [ ] **Step 1:** Grep for every importer: `grep -rn "remote_run_jobs\|RemoteRunManager\|run_remote_pipeline\|PipelineCtx" vivarium_dashboard/ tests/`. Confirm only dead references remain.
- [ ] **Step 2:** Delete the machinery; if `remote_run_jobs.py` becomes empty, delete the file and its imports.
- [ ] **Step 3:** Delete `tests/test_remote_run_jobs.py` (pipeline tests no longer apply); keep any still-relevant helper.
- [ ] **Step 4:** Run the full remote-run test set + `pytest -q` for the package; fix fallout. **Step 5: Commit** `refactor(remote-run): delete daemon pipeline; sms-api owns async/state`.

---

### Task R-UX (IN-THE-LOOP — not headless): JS two-phase panel + live cutover
**Files:** `static/study-detail.js` (remote-run panel handlers), `templates/study-detail.html`; live validation against the tunnel.
This is a UX redesign the JS drives and **must be validated live** — do it WITH the user:
- Panel flow: submit → `POST remote-run-start` (build) → poll `remote-run-status?simulator_id` → on build done, `POST remote-run-submit` → poll `remote-run-status?simulation_id` → on done, enable a **"Land results"** button → `POST remote-run-land`.
- Replace the 6-step strip with two phase indicators (Build / Run) + a Land action.
- Validation: bring up `ptools-proxy.sh -s smsvpctest` + GitHub login, drive one real run end-to-end through the panel, confirm build→run→land + a restart mid-run shows correct status (durability proof).
- Only after the panel is cut over and live-verified does Task R5's deletion land safely.

## Self-Review
**Spec coverage:** R1 (client), R2a (build-start), R2b (run-submit), R3 (on-demand status), R4 (land), R5 (delete) cover the addendum's revised phases; R-UX covers the JS redesign + live validation the addendum flags as in-the-loop. **Placeholder scan:** none — each Python task has concrete code/tests; R-UX is intentionally described not code-blocked because its shapes are settled live. **Type consistency:** route contracts (`simulator_id`/`simulation_id`/`phase`) are consistent across R2a→R3→R4; `_TERMINAL_OK`/`_TERMINAL_BAD` relocate from `remote_run_jobs` into the status mapper before R5 deletes their old home.

## Sequencing
R1 → R2a → R2b → R3 → R4 (all headless, additive — old pipeline still works until R5) → **R-UX in-the-loop** → R5 (delete, after the panel no longer needs the manager). R2a–R4 can land + be unit-tested without breaking the running panel because the old `remote-run-start`/`status` contract is only swapped once the JS is ready.
