# Using vivarium-workbench with a workspace

This document explains the **deployment relationship** between
`vivarium-workbench` and a process-bigraph *workspace*: how the dashboard is
obtained, where it runs, and why. For the conceptual data model see
[ARCHITECTURE.md](ARCHITECTURE.md).

> The prototype workspace scaffold is
> [pbg-template](https://github.com/vivarium-collective/pbg-template); the facts
> below come from its `template/pyproject.toml.j2`, `template-init.sh`, and
> `scripts/serve.sh`.

---

## TL;DR — the direction of the dependency

**The workspace depends on vivarium-workbench, not the other way around.**

- vivarium-workbench is a **pip dependency** of the workspace's `pyproject.toml`.
- It is **not** a git submodule and **not** vendored into the workspace.
- It is installed into the **workspace's own virtualenv**, and the
  `vivarium-workbench serve` CLI is run from there.
- The dashboard runs *inside the workspace's venv on purpose* — it imports the
  workspace's own Python package (`build_core()`) and any installed `pbg-*`
  simulation stacks to build and run composites. A dashboard installed in some
  *other* environment could not see those.

```
  workspace repo (e.g. my-project, scaffolded from pbg-template)
    pyproject.toml  ──depends-on──▶  vivarium-workbench  (pip package)
    .venv/                                    │
      ├── vivarium_workbench/   ◀─ installed ─┘
      ├── my_project/           ◀─ the workspace's OWN package (build_core)
      ├── process_bigraph/      ◀─ the simulation engine
      └── pbg_superpowers/, pbg_*  ◀─ generators / sim stacks
```

So: a workspace is the *project*; vivarium-workbench is a *library/tool* that
project pulls in to get a UI. One installed dashboard is not "shared" across
workspaces by reference — each workspace's venv has its own copy, and a running
server is bound to exactly one workspace (`--workspace <dir>`).

---

## How a workspace declares the dependency

In the scaffolded `pyproject.toml`, the dashboard is listed as a plain
dependency alongside the engine:

```toml
[project]
dependencies = [
    "process-bigraph",
    "bigraph-schema",
    "bigraph-viz>=2.0.3",
    # ... pyyaml, jsonschema, jinja2, plotly, matplotlib ...
    "vivarium-workbench",          # the web UI; provides the `vivarium-workbench serve` CLI
]

[tool.hatch.metadata]
allow-direct-references = true     # permits the git source below
```

Because the dashboard is **not on PyPI during beta**, `pbg-template`'s
`template-init.sh` appends a `[tool.uv.sources]` pin to a git URL at scaffold
time (it always uses the git source — never a committed local path — so CI,
Docker, and collaborators all resolve identically):

```toml
[tool.uv.sources]
vivarium-workbench = { git = "https://github.com/vivarium-collective/vivarium-dashboard.git", branch = "main" }
```

The git ref can be overridden at init via `VIVARIUM_DASHBOARD_REF`.

> **Rename note.** The distribution was renamed `vivarium-dashboard` →
> `vivarium-workbench`. The `vivarium_dashboard` import package, the
> `vivarium-dashboard` / `vdash` / `vivarium-dashboard-publish` CLIs, and the
> `VIVARIUM_DASHBOARD_*` env vars all keep working as deprecated aliases during
> the migration window. The one consumer-facing change is the **dependency
> name** (`"vivarium-dashboard"` → `"vivarium-workbench"` in `[project]` and the
> `[tool.uv.sources]` key); the git URL still points at the old repo path until
> the GitHub repo is renamed (it auto-redirects).

---

## Installing & serving (from a workspace)

```bash
# 1. create the workspace venv and install everything (resolves the git pin)
uv venv .venv && source .venv/bin/activate
uv pip install -e ".[dev]"

# 2. sanity check
python3 scripts/lint-workspace.py        # → "workspace lint: OK"

# 3. serve the dashboard against this workspace
bash scripts/serve.sh                     # convenience wrapper
#   ≡  vivarium-workbench serve --workspace .
```

`scripts/serve.sh` is a thin shim that **prefers the workspace venv's**
`.venv/bin/vivarium-workbench`, falling back to a system-wide one, then runs
`serve --workspace <workspace-root>`. The dashboard renders the workspace once,
picks a free port (or `--port`), prints the URL, and serves until Ctrl-C.

---

## Local development against a sibling checkout

When you are hacking on the dashboard itself (this repo), don't change the
committed git pin — instead override it with an **editable install into the
workspace's venv**, pointing at your local clone:

```bash
# from inside the workspace, with its venv active
uv pip install -e /path/to/vivarium-dashboard      # or ../vivarium-dashboard
```

Now `vivarium-workbench serve --workspace .` runs your working copy against a
real workspace. This is the standard inner loop: edit the dashboard here, serve
a workspace there. (The dashboard's own test suite uses a similar trick — it
spawns the server as a subprocess with the fixture workspace prepended to
`PYTHONPATH`; see `tests/conftest.py`.)

---

## Who imports whom (beyond `serve`)

The workspace's helper scripts also import the dashboard as a *library*, not just
via the CLI — further confirming the dependency direction. In a scaffolded
workspace:

| Script | Imports from the dashboard |
|---|---|
| `scripts/lint-workspace.py` | `vivarium_workbench.lib.investigations` (spec validation) |
| `scripts/render-dashboard.py` | `vivarium_workbench.lib.report.render_dashboard` |
| `scripts/add-dataset.sh` (py) | `vivarium_workbench.lib.workspace_yaml` (load/save/validate) |
| `scripts/publish_investigation_reports.py` | the `vivarium-workbench-publish` CLI |

These degrade gracefully (skip dashboard-specific checks) if the dashboard
isn't installed, but in the normal flow it always is.

---

## What the workspace brings that the dashboard does not

The dashboard ships no science. The workspace supplies, into the shared venv:

- its **own package** (`package_path` in `workspace.yaml`) exposing
  `build_core()` — the registry of types/processes the dashboard instantiates
  composites against;
- the **`.pbg/schemas/`** JSON-Schema validators (shipped by pbg-template) the
  dashboard enforces at save time;
- any installed **`pbg-*` simulation stacks** (e.g. a `v2ecoli` workflow) the
  dashboard discovers and can delegate runs to.

This is why the install order matters and why the server must run from the
workspace venv: the dashboard is generic; the workspace + its venv make it
specific to one body of research.
