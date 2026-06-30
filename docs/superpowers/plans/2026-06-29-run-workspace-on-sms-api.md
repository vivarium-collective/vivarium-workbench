# Run a pbg-template Workspace on sms-api — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a `pbg-template` workspace run its composite on sms-api's generic compose `.pbg` path, with `pbest` removed entirely.

**Architecture:** sms-api owns a deterministic Singularity def + an embedded generic `run_pbg.py` runner (replacing pbest) and exposes `extra_pip_deps` on the generic `/run` endpoint; pbg-template makes `pbg_<slug>` pip-installable so its processes install into the container from git; vivarium-dashboard serializes a composite to a `.pbg` (full import-path addresses), submits via `SmsApiClient`, polls, and lands `results.zip`.

**Tech Stack:** Python 3.12 (sms-api), FastAPI, Singularity/apptainer on SLURM, process-bigraph, hatchling, pytest.

## Global Constraints

- Feature branch in every repo: `feat/run-workspace-on-sms-api` (off each repo's `origin/main`).
- sms-api: Python 3.12.9 pinned; line length 120; `make check` (ruff + mypy strict + deptry) must pass; after API changes run `make spec` + `make api_client`.
- No `pbest` import may remain in sms-api after Phase A (CI gate: `grep -rn pbest sms_api/` returns nothing outside generated client regen).
- Runner results contract (fixed by sms-api): the container runner writes outputs into `/experiment/output/`; sms-api zips that dir into `results.zip`.
- Generic runner CLI contract (fixed by `_build_run_command`): `<runner> <input-file> -o <dir> -n <steps>`.
- `.pbg` process addresses produced by the dashboard MUST be full import-path form `local:!<module>.<qualname>` (resolve via `importlib`, no `build_core`).
- The dashboard CLI MUST refuse to submit when the workspace git tree is dirty or HEAD is unpushed.

---

# Phase A — sms-api: remove pbest, own def + runner, expose deps

Work dir: a worktree of `sms-api` at `feat/run-workspace-on-sms-api` off `origin/main`.

### Task A1: Inline the `ContainerizationFileRepr` DTO

**Files:**
- Create: `sms_api/compose/container_def.py`
- Modify: `sms_api/compose/handlers.py`, `sms_api/compose/models.py`, `sms_api/compose/tables_orm.py`, `sms_api/compose/database_service.py` (import line only)
- Test: `tests/compose/test_container_def.py`

**Interfaces:**
- Produces: `class ContainerizationFileRepr(BaseModel): representation: str` in `sms_api.compose.container_def`.

- [ ] **Step 1: Write failing test**
```python
# tests/compose/test_container_def.py
from sms_api.compose.container_def import ContainerizationFileRepr

def test_dto_holds_representation():
    r = ContainerizationFileRepr(representation="Bootstrap: docker")
    assert r.representation == "Bootstrap: docker"
```
- [ ] **Step 2: Run, expect ImportError** — `uv run pytest tests/compose/test_container_def.py -v`
- [ ] **Step 3: Implement**
```python
# sms_api/compose/container_def.py
from pydantic import BaseModel

class ContainerizationFileRepr(BaseModel):
    representation: str
```
- [ ] **Step 4: Swap imports** in the 4 files: replace
  `from pbest.utils.input_types import ContainerizationFileRepr`
  with `from sms_api.compose.container_def import ContainerizationFileRepr`.
  (In `handlers.py` the import is part of a multi-name block — keep the other
  `pbest` names for now; they're removed in A3.)
- [ ] **Step 5: Run** `uv run pytest tests/compose/ -v` — expect PASS.
- [ ] **Step 6: Commit** — `git commit -am "refactor(compose): inline ContainerizationFileRepr DTO (drop pbest dependency, step 1)"`

### Task A2: Embedded generic runner + `build_pbg_def`

**Files:**
- Create: `sms_api/compose/run_pbg.py` (the runner that runs *inside* the container)
- Modify: `sms_api/compose/container_def.py` (add `build_pbg_def`)
- Test: `tests/compose/test_build_pbg_def.py`, `tests/compose/test_run_pbg.py`

**Interfaces:**
- Consumes: `ContainerizationFileRepr` (A1).
- Produces:
  - `sms_api/compose/run_pbg.py` runnable as `python run_pbg.py <input.pbg> -o <dir> -n <steps>`, writing `<dir>/final_state.json` (+ any emitter output).
  - `build_pbg_def(input_suffix: str, extra_pip_deps: list[str] | None = None) -> ContainerizationFileRepr` in `container_def.py`.

- [ ] **Step 1: Write the runner** (`run_pbg.py`) — pure, testable with a tmp outdir:
```python
# sms_api/compose/run_pbg.py
"""Generic process-bigraph runner executed inside the compose container.

CLI contract (fixed by sms-api's _build_run_command):
    python run_pbg.py <input-file> -o <outdir> -n <steps>

Writes results into /experiment/output (the bind-mounted, zipped dir).
The -o value is accepted for CLI compatibility but output always lands in
RESULTS_DIR so it matches sms-api's `zip -r ../results.zip` collection.
"""
from __future__ import annotations
import argparse, json, os
from pathlib import Path

RESULTS_DIR = Path(os.environ.get("PBG_RESULTS_DIR", "/experiment/output"))

def run(input_file: str, steps: int, results_dir: Path = RESULTS_DIR) -> Path:
    from process_bigraph import Composite  # imported lazily so tests can stub
    from bigraph_schema import default  # noqa: F401  (ensures core available)
    results_dir.mkdir(parents=True, exist_ok=True)
    document = json.loads(Path(input_file).read_text())
    composite = Composite(document)  # full-path local:! addresses resolve via importlib
    composite.run(steps)
    out = results_dir / "final_state.json"
    out.write_text(json.dumps(composite.serialize_state(), default=str))
    return out

def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser()
    p.add_argument("input_file")
    p.add_argument("-o", "--output", default=str(RESULTS_DIR))
    p.add_argument("-n", "--steps", type=int, default=1)
    args = p.parse_args(argv)
    run(args.input_file, args.steps)

if __name__ == "__main__":
    main()
```
> NOTE for implementer: confirm the exact process-bigraph load API against the
> installed version — `Composite(document)` + `composite.run(n)` +
> `composite.serialize_state()` are the documented entrypoints
> (`process_bigraph/composite.py:1803`). If the constructor needs an explicit
> `core`, pass `Composite(document, core=default_core())` using whatever
> `bigraph_schema` exposes; adjust the import accordingly. Keep `run()` a pure
> function taking `results_dir` so the test below doesn't touch `/experiment`.

- [ ] **Step 2: Write failing runner test** (stub a trivial inline process via a real tiny `.pbg`, or monkeypatch `Composite`):
```python
# tests/compose/test_run_pbg.py
import json
from pathlib import Path
from sms_api.compose import run_pbg

def test_run_writes_final_state(tmp_path, monkeypatch):
    class FakeComposite:
        def __init__(self, doc): self.doc = doc
        def run(self, n): self.n = n
        def serialize_state(self): return {"ran": self.n}
    monkeypatch.setattr(run_pbg, "Composite", FakeComposite, raising=False)
    # patch the lazy import inside run()
    import process_bigraph
    monkeypatch.setattr(process_bigraph, "Composite", FakeComposite)
    pbg = tmp_path / "m.pbg"; pbg.write_text(json.dumps({"state": {}, "schema": {}}))
    out = run_pbg.run(str(pbg), steps=5, results_dir=tmp_path / "output")
    assert json.loads(out.read_text())["ran"] == 5
```
- [ ] **Step 3: Run, iterate** `uv run pytest tests/compose/test_run_pbg.py -v` until PASS.
- [ ] **Step 4: Add `build_pbg_def`** to `container_def.py` — embeds the runner via heredoc:
```python
import importlib.resources as _res

_RUNNER_SRC = (_res.files("sms_api.compose") / "run_pbg.py").read_text()

_DEF_TEMPLATE = """\
Bootstrap: docker
From: python:3.12-slim-bookworm

%post
    set -eux
    pip install --no-cache-dir process-bigraph bigraph-schema pbg-emitters{extra_installs}
    mkdir -p /opt
    cat > /opt/run_pbg.py <<'PBG_RUNNER_EOF'
{runner_src}
PBG_RUNNER_EOF

%runscript
    exec python /opt/run_pbg.py "$@"
"""

def build_pbg_def(input_suffix: str, extra_pip_deps: list[str] | None = None) -> "ContainerizationFileRepr":
    extra = ""
    for dep in (extra_pip_deps or []):
        extra += f"\n    pip install --no-cache-dir --ignore-requires-python '{dep}'"
    rep = _DEF_TEMPLATE.format(extra_installs="", runner_src=_RUNNER_SRC) \
        .replace("%runscript", extra + "\n\n%runscript", 0) if False else None
    # Simpler: build deterministically without fragile replace
    post_extra = "".join(f"\n    pip install --no-cache-dir --ignore-requires-python '{d}'"
                         for d in (extra_pip_deps or []))
    representation = (
        "Bootstrap: docker\nFrom: python:3.12-slim-bookworm\n\n"
        "%post\n    set -eux\n"
        "    pip install --no-cache-dir process-bigraph bigraph-schema pbg-emitters"
        f"{post_extra}\n"
        "    mkdir -p /opt\n"
        "    cat > /opt/run_pbg.py <<'PBG_RUNNER_EOF'\n"
        f"{_RUNNER_SRC}\n"
        "PBG_RUNNER_EOF\n\n"
        "%runscript\n    exec python /opt/run_pbg.py \"$@\"\n"
    )
    return ContainerizationFileRepr(representation=representation)
```
> Implementer: delete the dead first-draft lines; keep only the deterministic
> `representation = (...)` construction. `input_suffix` is currently unused but
> kept in the signature for parity with the call site (the input filename is
> chosen by sms-api, not the def). Remove the param if mypy/ruff flags it unused
> AND the A3 call site is updated to match.

- [ ] **Step 5: Failing test for `build_pbg_def`**
```python
# tests/compose/test_build_pbg_def.py
from sms_api.compose.container_def import build_pbg_def

def test_def_installs_process_bigraph_and_embeds_runner():
    d = build_pbg_def("pbg").representation
    assert "Bootstrap: docker" in d
    assert "pip install --no-cache-dir process-bigraph bigraph-schema" in d
    assert "/opt/run_pbg.py" in d
    assert "%runscript" in d

def test_def_injects_extra_pip_deps():
    d = build_pbg_def("pbg", ["git+https://github.com/x/y.git@abc"]).representation
    assert "git+https://github.com/x/y.git@abc" in d
```
- [ ] **Step 6: Run** `uv run pytest tests/compose/test_build_pbg_def.py -v` — PASS.
- [ ] **Step 7: Commit** — `git commit -am "feat(compose): generic pbg runner + pbest-free build_pbg_def"`

### Task A3: Wire `build_pbg_def` into the handler; remove pbest imports

**Files:**
- Modify: `sms_api/compose/handlers.py:14-20,110-120`
- Test: `tests/compose/test_handlers_no_pbest.py`

**Interfaces:**
- Consumes: `build_pbg_def` (A2).

- [ ] **Step 1:** In `handlers.py`, delete the `from pbest...` imports (lines 14-20). Keep `_inject_pip_deps` (still used as a fallback? No — fold deps into `build_pbg_def`). Replace the def-generation block:
```python
    # was: with tempfile.TemporaryDirectory(...) as tmp_dir: singularity_rep = generate_container_def_file(...)
    suffix = simulation_request.simulation_file_type.get_files_suffix()
    singularity_rep = build_pbg_def(suffix, extra_pip_deps=extra_pip_deps)
```
  Remove the now-unused `tmp_dir`/`_inject_pip_deps` path for the generic case
  (delete `_inject_pip_deps` if no other caller — `grep` first). Add
  `from sms_api.compose.container_def import build_pbg_def, ContainerizationFileRepr`.
- [ ] **Step 2: Failing test** — assert no pbest + def is built:
```python
# tests/compose/test_handlers_no_pbest.py
import pathlib, sms_api.compose.handlers as h

def test_handlers_module_has_no_pbest_import():
    src = pathlib.Path(h.__file__).read_text()
    assert "pbest" not in src
```
- [ ] **Step 3: Run** `uv run pytest tests/compose/test_handlers_no_pbest.py -v` — PASS.
- [ ] **Step 4: Run** the existing compose handler tests `uv run pytest tests/compose/ -v` — fix fallout (signatures unchanged, so green).
- [ ] **Step 5: Commit** — `git commit -am "feat(compose): handler builds def via build_pbg_def; drop pbest imports"`

### Task A4: Expose `extra_pip_deps` on the generic endpoint

**Files:**
- Modify: `sms_api/api/routers/compose.py:119-143`
- Test: `tests/api/test_compose_router.py`

**Interfaces:**
- Consumes: `run_compose_simulation(..., extra_pip_deps=...)` (already supports it).

- [ ] **Step 1:** Add an optional param to `submit_simulation` and thread it through:
```python
async def submit_simulation(
    background_tasks: BackgroundTasks,
    uploaded_file: UploadFile,
    interval_time: float = 1.0,
    batch_submission: bool = False,
    extra_pip_deps: list[str] | None = Query(default=None),
) -> ComposeSimulationExperiment:
    ...
    return await run_compose_simulation(..., extra_pip_deps=extra_pip_deps)
```
- [ ] **Step 2: Failing test** asserting the param reaches the handler (monkeypatch `run_compose_simulation`, post a tiny `.pbg`, assert captured `extra_pip_deps`). Use the existing FastAPI `TestClient` fixture pattern in `tests/api/`.
- [ ] **Step 3: Run** — PASS.
- [ ] **Step 4:** `make spec && make api_client` (regenerates the OpenAPI client).
- [ ] **Step 5: Commit** — `git commit -am "feat(api): expose extra_pip_deps on generic compose /run; regen client"`

### Task A5: Migrate v2ecoli handler + delete the pbest dependency

**Files:**
- Modify: `sms_api/compose/handlers.py` (`run_compose_v2ecoli` already passes `extra_pip_deps`+`override_command` → no change needed once A3 routes through `build_pbg_def`), `pyproject.toml:66,140`, `uv.lock`
- Test: repo-wide pbest grep gate + `make check`

- [ ] **Step 1:** Verify v2ecoli path still works through `build_pbg_def` (it passes `extra_pip_deps=[v2ecoli git]` + `override_command` → the override path in `_build_run_command` is unchanged, so its `singularity exec ... v2ecoli_run.py` still runs; the *base def* is now pbest-free). No code change expected — confirm by reading.
- [ ] **Step 2:** Remove `"pbest==0.5.5",` from `pyproject.toml:66`; remove `"pbest.*"` from the mypy module override (`:140`). Run `uv lock`.
- [ ] **Step 3: Gate test**
```bash
grep -rn "pbest" sms_api/ ; test $? -ne 0   # expect: no matches (exit 1 from grep → test passes)
```
- [ ] **Step 4:** `make check` (ruff + mypy strict + deptry) and `uv run pytest`. Fix any deptry "unused/missing" findings.
- [ ] **Step 5: Commit** — `git commit -am "chore(compose): drop pbest dependency entirely"`
- [ ] **Step 6:** Push branch; open PR to sms-api `main`. Do NOT merge.

---

# Phase B — pbg-template: make `pbg_<slug>` pip-installable

Work dir: a worktree of `pbg-template` at `feat/run-workspace-on-sms-api`.

### Task B1: Declare the workspace package as an installable wheel

**Files:**
- Modify: `template/pyproject.toml.j2` (the `[tool.hatch.build...]` block, ~`:29-40`)
- Test: `tests/test_template_pip_installable.py`

- [ ] **Step 1:** Change the wheel target so the `pbg_<slug>` package is built (the
  template renders `{{ package_name }}` = `pbg_<slug>`). Replace the
  `bypass-selection = true` block with:
```toml
[tool.hatch.build.targets.wheel]
packages = ["{{ package_name }}"]
```
  (Confirm the j2 variable name for the package — `template-init.sh:24` derives
  `PACKAGE_PATH="pbg_${WS_NAME//-/_}"`; match whatever placeholder
  `pyproject.toml.j2` already uses for the package name.)
- [ ] **Step 2: Failing test** — render the template to a temp dir, build a wheel, assert the package is in it:
```python
# tests/test_template_pip_installable.py
import subprocess, sys, tempfile, zipfile, pathlib, shutil, os

def _render(tmp):  # minimal render of pyproject + package for the test
    # use the repo's existing render harness if present (template-init.sh),
    # else substitute the mustache vars directly for pyproject.toml.j2.
    ...

def test_wheel_contains_workspace_package(tmp_path):
    ws = _render(tmp_path)
    subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "-w", str(tmp_path/"wh"), str(ws)], check=True)
    whl = next((tmp_path/"wh").glob("*.whl"))
    names = zipfile.ZipFile(whl).namelist()
    assert any(n.startswith("pbg_") and n.endswith("__init__.py") for n in names)
```
> Implementer: reuse the existing template-render harness used by
> `tests/test_template_singularity_parity.py` if it exposes one; otherwise do a
> minimal mustache substitution for `pyproject.toml.j2` + a stub package dir.
- [ ] **Step 3:** Run, implement, PASS.
- [ ] **Step 4: Stronger integration check** (manual/CI):
```bash
# scaffold a throwaway workspace, commit it, install from a file git url into a clean venv
python -m venv /tmp/cleanv && /tmp/cleanv/bin/pip install "git+file:///path/to/scaffolded-ws@<sha>"
/tmp/cleanv/bin/python -c "import pbg_<slug>; print('ok')"
```
- [ ] **Step 5:** Confirm local editable still works: `uv sync` in a scaffolded ws + dashboard editable install unaffected (see memory: dashboard editable-install-from-main).
- [ ] **Step 6: Commit** — `git commit -am "feat(template): make pbg_<slug> a pip-installable wheel package"`

### Task B2: Document the run-on-sms-api flow

**Files:** Modify `template/NEXT_STEPS.md.j2`
- [ ] **Step 1:** Add a "Run on sms-api" subsection: push the workspace, then
  `vivarium-dashboard run-remote <composite>`; note it installs the workspace from
  `git+<repo>@<sha>`, so the composite's processes must live in the committed
  `pbg_<slug>` package and the tree must be pushed.
- [ ] **Step 2: Commit** — `git commit -am "docs(template): document running a workspace composite on sms-api"`. Push; open PR. Do NOT merge.

---

# Phase C — vivarium-dashboard: export + client + CLI

Work dir: the existing worktree `.wt/dashboard-sms` (`feat/run-workspace-on-sms-api`).

### Task C1: Address-rewrite helper (short → full import path)

**Files:**
- Create: `vivarium_dashboard/lib/pbg_export.py`
- Test: `tests/test_pbg_export_addresses.py`

**Interfaces:**
- Produces: `rewrite_local_addresses(document: dict, core) -> dict` — replaces every
  `local:<Name>` address with `local:!<module>.<qualname>` by looking the class up in
  `core` (the workspace's `build_core()` result). Leaves `local:!…` and non-`local:`
  addresses untouched. Raises `ValueError` listing any `local:<Name>` whose class is in
  a non-importable module (`__main__`, `__qualname__` containing `<locals>`).

- [ ] **Step 1: Failing test**
```python
# tests/test_pbg_export_addresses.py
from vivarium_dashboard.lib.pbg_export import rewrite_local_addresses

class _Reg:  # minimal stand-in for a process-bigraph core's link registry
    def __init__(self, mapping): self._m = mapping
    def access(self, name): return self._m.get(name)

class _Core:
    def __init__(self, mapping): self.link_registry = _Reg(mapping)

def test_rewrites_short_to_full_path():
    import collections
    cls = collections.OrderedDict  # any importable class
    core = _Core({"MyProc": cls})
    doc = {"state": {"p": {"address": "local:MyProc"}}}
    out = rewrite_local_addresses(doc, core)
    assert out["state"]["p"]["address"] == "local:!collections.OrderedDict"

def test_leaves_full_and_external_untouched():
    core = _Core({})
    doc = {"state": {"a": {"address": "local:!x.Y"}, "b": {"address": "pkg:mod.Z"}}}
    out = rewrite_local_addresses(doc, core)
    assert out["state"]["a"]["address"] == "local:!x.Y"
    assert out["state"]["b"]["address"] == "pkg:mod.Z"
```
> Implementer: confirm the real core's registry accessor name against
> `bigraph_schema` (`local_lookup_registry` reads `core.link_registry` —
> `protocols.py:38`). Walk the document recursively for any dict with an
> `"address"` key (addresses can be nested under `state`/`edges`); reuse the
> document shape from `process_bigraph` rather than assuming top-level only.
- [ ] **Step 2-4:** Run (fail) → implement `rewrite_local_addresses` (recursive walk; `f"local:!{cls.__module__}.{cls.__qualname__}"`; guard `<locals>`/`__main__`) → run (pass).
- [ ] **Step 5: Commit** — `git commit -am "feat(dashboard): pbg address-rewrite helper (short→full import path)"`

### Task C2: Composite → `.pbg` export

**Files:**
- Modify: `vivarium_dashboard/lib/pbg_export.py` (add `export_composite_pbg`)
- Test: `tests/test_pbg_export_roundtrip.py`

**Interfaces:**
- Consumes: `rewrite_local_addresses` (C1); the workspace's `build_core()` (resolved
  via the existing `lib/composite_resolve.py` import path) and `CompositeSpec`.
- Produces: `export_composite_pbg(ws_root, composite_id, out_path) -> Path` — builds the
  named composite, serializes to a document, rewrites addresses, ensures an emitter node
  is present, writes JSON to `out_path`.

- [ ] **Step 1: Failing roundtrip test** against a fixture workspace (use
  `tests/_fixtures/`): export a known composite, reload the JSON, assert it parses as a
  `Composite` and every `local:` address is `local:!…`.
- [ ] **Step 2-4:** Implement using `CompositeSpec.from_…` + `.to_document()`
  (`composite_spec.py:207`) → `rewrite_local_addresses` → inject the workspace default
  emitter if none present → `out_path.write_text(json.dumps(doc, default=str))`. Run to green.
- [ ] **Step 5: Commit** — `git commit -am "feat(dashboard): export a workspace composite to a portable .pbg"`

### Task C3: `SmsApiClient` compose methods

**Files:**
- Modify: `vivarium_dashboard/lib/sms_api_client.py`
- Test: `tests/test_sms_api_client_compose.py`

**Interfaces:**
- Produces on `SmsApiClient`:
  - `submit_compose(pbg_path: Path, interval_time: float, extra_pip_deps: list[str]) -> int` → POST `/compose/v1/simulation/run` (multipart), returns `simulation_database_id`.
  - `compose_status(sim_id: int) -> dict` → GET `/compose/v1/simulation/{id}/status`.
  - `download_compose_results(sim_id: int, dest: Path) -> Path` → GET `/compose/v1/simulation/{id}/results`, stream to `dest/results.zip`.

- [ ] **Step 1-4:** TDD against a mocked HTTP layer (the repo uses `requests`/`httpx`
  in `sms_api_client.py` — match it; reuse its existing auth/base-url handling). Assert
  the multipart field name is `uploaded_file` and `extra_pip_deps` is sent as repeated
  query params; assert status/result URLs. Run to green.
- [ ] **Step 5: Commit** — `git commit -am "feat(dashboard): SmsApiClient compose submit/status/results"`

### Task C4: `vivarium-dashboard run-remote` CLI

**Files:**
- Modify: `vivarium_dashboard/cli.py` (add the subcommand), reuse `lib/pbg_export.py` + `lib/sms_api_client.py`
- Create: `vivarium_dashboard/lib/remote_run.py` (orchestration: git coords, dirty-guard, submit→poll→land)
- Test: `tests/test_remote_run.py`

**Interfaces:**
- Consumes: `export_composite_pbg` (C2), `SmsApiClient` compose methods (C3).
- Produces: `run_remote(ws_root, composite_id, interval_time, client) -> Path` (returns the landed `results.zip` path) and a `run-remote` CLI command calling it.

- [ ] **Step 1: Failing test — dirty tree refusal**
```python
# tests/test_remote_run.py
import pytest
from vivarium_dashboard.lib.remote_run import git_pip_url, run_remote

def test_refuses_dirty_tree(tmp_git_repo_dirty):
    with pytest.raises(RuntimeError, match="uncommitted|dirty|unpushed"):
        git_pip_url(tmp_git_repo_dirty)
```
- [ ] **Step 2: Failing test — happy path** with a clean fixture repo + mocked client:
  assert `run_remote` calls `export_composite_pbg`, `submit_compose` with
  `extra_pip_deps=["git+<origin>@<sha>"]`, polls `compose_status` until `completed`,
  and writes `results.zip`.
- [ ] **Step 3-4:** Implement `git_pip_url(ws_root)` (read `origin` + `HEAD` sha via
  `subprocess git`; raise if `git status --porcelain` nonempty or HEAD not on any remote);
  `run_remote(...)` orchestration; wire `cli.py` `run-remote` subcommand (mirror the
  existing `serve`/`run-composite` command registration). Run to green.
- [ ] **Step 5: Commit** — `git commit -am "feat(dashboard): run-remote CLI — export, submit, poll, land results"`
- [ ] **Step 6:** Push; open PR to vivarium-dashboard `main`. Do NOT merge.

---

# Phase D — Integration & live E2E (manual gate)

- [ ] **D1:** With all three PRs' branches checked out, scaffold a throwaway workspace
  with one trivial process, commit + push it.
- [ ] **D2:** Point `SmsApiClient` at a live sms-api (GovCloud tunnel per
  `project_dashboard_remote_runs` memory — run the tunnel in its own terminal).
- [ ] **D3:** `vivarium-dashboard run-remote <composite> --interval 10`; confirm a
  populated `results.zip` with `final_state.json` lands. Record the run id.
- [ ] **D4:** If the default `singularity run` path mis-handles the generic `.pbg`
  (e.g. arg order), fall back to wiring a generic `override_command` mode in
  `_build_run_command` (escape hatch already in the design) — otherwise leave unused.

## Self-review notes (coverage)

- Spec ① (remove pbest + own def/runner + expose deps) → Tasks A1-A5. ✓
- Spec ② (installable package + docs) → Tasks B1-B2. ✓
- Spec ③ (export + client + CLI) → Tasks C1-C4. ✓
- Spec risks: results contract (resolved, baked into A2 + Global Constraints);
  installability flip (B1 step 4-5); E2E infra-gated (Phase D). ✓
- Full-path addressing requirement → C1; dirty-tree guard → C4; pbest grep gate → A5. ✓
