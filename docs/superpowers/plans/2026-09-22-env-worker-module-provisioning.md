# Env-worker module provisioning — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or
> superpowers:executing-plans. Steps use `- [ ]` checkboxes.

**Goal:** make catalog-installed workspace modules available in the env-worker at run time,
so UI-install works end-to-end (composites actually run), driven from the Catalog tab.

**Architecture:** the workbench (which holds the PVC) reads the workspace's declared
install-specs and pushes them to the env-worker over a new `install_modules` JSON-RPC; the
worker `pip install`s them into a writable `--target` on the existing `/scratch` emptyDir,
prepends it to `sys.path`, then builds its registry.

**Tech stack:** Python 3.11+, process-bigraph, vivarium-workbench FastAPI + env-worker
JSON-RPC, vanilla-JS Catalog tab. Tests: pytest.

**Spec:** `docs/superpowers/specs/2026-09-22-env-worker-module-provisioning-design.md`

## Global Constraints
- No new viva-api volume — install into `/scratch` (existing writable emptyDir). Target dir
  overridable via env `VIVARIUM_ENV_WORKER_SITE` (default `/scratch/env-worker-site`).
- `mode: reference` → `git+<source>@<ref>`; `mode: pypi` → `<pypi_name>`. Never install the
  `external/<name>` editable path (absent in the worker).
- Best-effort + never-raise on the worker: a failed module install is a reported result, not
  a worker crash (mirrors the existing discovery soft-degrade).
- No AI attribution in commits.

---

## Task 1: Worker-side provision helper + `install_modules` RPC (`env_worker.py`)

**Files:** Modify `vivarium_workbench/env_worker.py`; Test `tests/test_env_worker_provision.py`.

**Produces:** `_provision_modules(specs: list[dict], *, target: str|None=None) -> list[dict]`
returning `[{name, ok, detail}]`; a `install_modules` method in `_handle`; a module-level
`_provision_target()` resolver.

- [ ] Write failing unit test: `_provision_modules([{name,'mode':'pypi','pypi_name':'x'}...])`
      builds the right pip argv (monkeypatch subprocess), prepends target to `sys.path`,
      returns per-module `{name, ok, detail}`; a failing install → `ok:False` with detail,
      not a raise.
- [ ] Implement `_provision_target()` (env `VIVARIUM_ENV_WORKER_SITE` else `/scratch/env-worker-site`),
      `_pip_args_for_spec(spec, target)` (pypi vs git+url@ref), and `_provision_modules(...)`:
      `subprocess.run([sys.executable,'-m','pip','install','--target',target, <pkg>], ...)`,
      then ensure `target` is at the front of `sys.path` + `PYTHONPATH`. Skip specs with no
      installable form.
- [ ] Add `install_modules` to the `_handle` ladder → returns `{results: _provision_modules(params['modules'])}`.
- [ ] Run tests green. Commit.

## Task 2: Spec derivation + push on cold acquire (`env_worker_provision.py`, `env_worker_pool.py`)

**Files:** Create `vivarium_workbench/lib/env_worker_provision.py`; Modify
`vivarium_workbench/lib/env_worker_pool.py`; Test `tests/test_env_worker_provision_specs.py`.

**Consumes:** Task 1's `install_modules` RPC. **Produces:**
`install_specs_from_workspace(ws_root) -> list[dict]` (each `{name, mode, pypi_name?|source,ref}`).

- [ ] Write failing test: `install_specs_from_workspace` on a fixture `workspace.yaml` with a
      pypi import and a reference (git) import returns the two specs in worker-ready shape;
      `mode: reference`-that-is-browse-only and malformed entries are skipped.
- [ ] Implement `install_specs_from_workspace` reading `workspace.yaml imports:` (reuse the
      shapes catalog-install writes: pypi_name / source+ref / mode).
- [ ] In `WorkerPool._acquire`, after `launcher.launch(...)` returns a fresh worker and before
      it's cached, call `worker.call('install_modules', {'modules': specs})` once (best-effort,
      log failures; never block acquisition on a provision error). Guard with a flag so it runs
      only on cold miss.
- [ ] Test the derivation; smoke-test the acquire path with a fake worker. Commit.

## Task 3: env-worker availability status (`module_import_doctor.py`, `catalog.py`)

**Files:** Modify `vivarium_workbench/lib/module_import_doctor.py` and
`vivarium_workbench/lib/catalog.py`; Test extends `tests/test_module_import_doctor.py`.

**Produces:** an `env_worker` field per catalog module (`available|not_provisioned|failed|unknown`).

- [ ] Write failing test: `build_catalog` annotates each installed module with an `env_worker`
      status field (default `unknown` when the worker can't be probed — never raises).
- [ ] Implement a best-effort worker probe (reuse the pool's `discover_composites`/a light
      probe) → per-module availability; annotate in `build_catalog` (~`:560`).
- [ ] Run tests green. Commit.

## Task 4: Catalog tab UI — pill + Provision action (`walkthrough.js`)

**Files:** Modify `vivarium_workbench/static/walkthrough.js`.

- [ ] Add an "in env-worker ✓ / not provisioned / failed" pill next to `srcBadge` in
      `_moduleActionFor` (~`:4703`), keyed on the `env_worker` field.
- [ ] Add a "Provision to env-worker" action (mirrors `_installFromMarketplace`) → POST that
      triggers a worker `install_modules` for that module + refreshes status.
- [ ] Manual/integration check (no unit harness for JS). Commit.

## Task 5 (companion, cluster-validated): viva-api egress note

**Files:** `docs/` note only in this repo; the actual NetworkPolicy is RENCI-side.

- [ ] Document the egress requirement (worker → PyPI + github.com) in the spec's risks and the
      Phil action list. No code change unless a NetworkPolicy must be added in viva-api.

---

## Self-review
- Spec coverage: Tasks 1-2 = the runnable core (install + push); 3-4 = the Catalog surface; 5
  = the one external dependency. ✓
- No placeholders: each task names exact files + functions + test intent; code sketched in the
  helper signatures. Full code written at implementation.
- Type consistency: `install_modules` params `{modules: [spec]}`, spec shape stable across
  Tasks 1-2; `env_worker` status enum stable across Tasks 3-4.
