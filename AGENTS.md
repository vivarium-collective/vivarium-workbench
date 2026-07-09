# Agent Instructions for vivarium-workbench

This file provides essential, high-signal guidance for AI agents working in the `vivarium-workbench` repository.

## Core Concept: Tool vs. Workspace

- **Crucial Distinction:** This repository contains the `vivarium-workbench` tooling. The data it operates on (simulations, studies, results) lives in a separate **workspace directory**, passed with the `--workspace` flag.
- **Git History:** This repo's git history is for the tool itself. The workbench creates commits in the *workspace's* git history, which serves as the scientific audit trail. Keep these two contexts separate.
- **Rename:** The project was renamed from `vivarium-dashboard` to `vivarium-workbench`. You will find many deprecated aliases (`vdash`, `vivarium-dashboard` command, `vivarium_dashboard` import package) for backward compatibility.

## Key Commands

- **Serve the UI:**
  ```bash
  # Serve a workspace from its directory
  vivarium-workbench serve --workspace /path/to/workspace
  ```

- **Run Tests (pytest):**
  ```bash
  # Run the full test suite
  pytest

  # Run a single test file
  pytest tests/test_composite_runs.py

  # Run a single test function and stop on first failure
  pytest tests/test_composite_runs.py::test_name -x

  # Run tests matching a keyword
  pytest -k "csrf or origin"
  ```

- **Publish a Static Bundle:**
  ```bash
  # Export a workspace as a read-only static site
  vivarium-workbench-publish --workspace /path/to/workspace --out /path/to/bundle
  ```

## Development Setup & Environment

- **Dependencies:** Managed with `uv`. Install with `uv pip install -e ".[dev,test]"`.
- **Execution Context:** The workbench **must** be run from within a workspace's virtual environment. This is because it needs to import the workspace's own Python package (to build composites) and its specific `pbg-*` dependencies.
- **Local Development Workflow:**
  1. Clone this `vivarium-workbench` repository.
  2. Have a separate workspace directory (e.g., scaffolded from `pbg-template`).
  3. From the workspace directory (with its venv active), run:
     ```bash
     uv pip install -e /path/to/your/vivarium-workbench-clone
     ```
  4. Now, running `vivarium-workbench serve --workspace .` from the workspace directory will use your local, editable version of the tool.

## Architecture & Conventions

- **Backend:** The server is a **FastAPI** application defined in `vivarium_workbench/api/app.py`. The old `server.py` is a deprecated shim; all new logic goes in `lib/` modules.
- **Domain Logic:** All business logic resides in `vivarium_workbench/lib/`.
- **Path Resolution:** **Always** use `vivarium_workbench.lib.workspace_paths` to resolve paths within a workspace (e.g., `studies/`, `composites/`). Do not hardcode these directory names, as they can be reconfigured in `workspace.yaml`.
- **Simulation Runs:** There are two distinct simulation engines. Be aware of which one is being used:
    1.  **Engine A (Detached):** For Composite Explorer runs (`/api/composite-test-run`). Spawns a fully detached `run-composite` subprocess. This is the more robust engine.
    2.  **Engine B (Synchronous):** For core study runs (`/api/study-run-*`). Runs a Python script as a synchronous subprocess *within the HTTP request*, with a long timeout. This is the less robust path and can leave stale runs if interrupted.
- **Linting & Typing:**
    - There is **no linter** configured.
    - **`mypy`** is used for type checking, but it is adopted incrementally. See `pyproject.toml` for the list of currently type-checked files.
- **CSRF Protection:** Mutating `POST`/`DELETE` endpoints are guarded by an origin check. For local testing with tools like `curl`, you can disable this by setting the environment variable `VIVARIUM_WORKBENCH_DISABLE_CSRF=1`.
