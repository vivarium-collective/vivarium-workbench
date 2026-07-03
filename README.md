# vivarium-workbench

Web UI for [Process-Bigraph](https://github.com/vivarium-collective/process-bigraph)
workspaces. Browse composites, run studies, inspect state-trees, and render
visualizations — without writing dashboard boilerplate.

Point it at a workspace directory (one containing `workspace.yaml`) and it
serves an interactive UI over the workspace's registry, composites, studies,
and reports.

It's one codebase you run in three configurations — local authoring, a remote
**sms-api** compute backend, or a public read-only published snapshot. They
differ by *configuration* (env vars), not code. See
[Running modes](#running-modes).

> **Status:** in active beta. APIs and UI may change before 1.0.

For a deeper look at what the dashboard does, where data lives, the run/render
data lifecycles, and how it relates to its companion repos, see
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). For how the dashboard is deployed
alongside a workspace (it's a pip dependency installed into the workspace's
venv), see [docs/USAGE.md](docs/USAGE.md).

## Getting Started

### 1. Install

```bash
pip install vivarium-workbench         # or: uv pip install vivarium-workbench
```

> **Renamed:** the tool was `vivarium-dashboard`; it is now `vivarium-workbench`
> (it authors, runs, evaluates, and publishes across the whole lifecycle — a
> workbench, not a read-only dashboard). The old `vivarium-dashboard` / `vdash` /
> `vivarium-dashboard-publish` commands, the `vivarium_dashboard` import package,
> and the `VIVARIUM_DASHBOARD_*` env vars all still work as deprecated aliases
> (they emit a `DeprecationWarning`) and are removed in a future major release.
> The published static bundle is still the "read-only dashboard".

Not on PyPI yet during beta — install editable from a clone instead:

```bash
git clone https://github.com/vivarium-collective/vivarium-workbench.git ~/code/vivarium-dashboard
./.venv/bin/pip install -e ~/code/vivarium-dashboard   # into your workspace's venv
```

### 2. Get a workspace

The dashboard serves a [pbg-template](https://github.com/vivarium-collective/pbg-template)
workspace. Two ways to scaffold one — same files either way, pick whichever
interface you prefer:

**Standalone (no AI required).** On the pbg-template GitHub page, click
**Use this template → Create a new repository**, clone your new repo, then run:

```bash
bash use-this-template-init.sh
```

**With AI authoring.** Install the
[pbg-superpowers](https://github.com/vivarium-collective/pbg-superpowers)
Claude Code plugin and from inside Claude Code run:

```
/pbg-workspace my-project
```

See [pbg-superpowers' Getting Started](https://github.com/vivarium-collective/pbg-superpowers#getting-started)
for the full walkthrough.

### 3. Serve

```bash
cd my-workspace
vivarium-workbench serve --workspace .
# or, from inside a scaffolded workspace:
bash scripts/serve.sh
```

Open the printed URL.

### What to expect

You land on a UI with **seven tabs**: Workspace inputs, Registry, Composites,
Studies, Investigations, Visualizations, and GitHub Branches. Every action you
take — creating a study, registering an observable, kicking off a run — commits
to your active workstream branch, so there's a full git audit trail visible
under **GitHub Branches**. The dashboard reads the workspace's `.pbg/schemas/`
validators, so malformed YAML is caught at save time rather than at run time.
If you installed the **pbg-superpowers** plugin, every dashboard action can
also be driven by natural-language `/pbg-*` skills against the same files —
use whichever interface fits the moment. For the full AI-augmented authoring
experience, see the
[pbg-superpowers Getting Started](https://github.com/vivarium-collective/pbg-superpowers#getting-started).

## Running modes

The dashboard is a single codebase run in three configurations. The difference
is *configuration* (env vars), not code — one image, three "planes."

### Local — authoring (default)

What [Getting Started](#getting-started) sets up: serve a workspace, run studies
on the local engine, commit every action to a git branch.

```bash
vivarium-workbench serve --workspace .
```

### Remote compute — sms-api backend

Point the dashboard at an **sms-api** endpoint to build pinned `repo@commit`
simulator versions and run large batches on a remote backend (AWS GovCloud, or
an HPC cluster) instead of the local engine:

```bash
SMS_API_BASE=http://localhost:8080 vivarium-workbench serve --workspace .
```

- **Reaching a GovCloud sms-api:** it sits behind an internal load balancer, so
  tunnel to it with an SSM port-forward (after `aws sso login`):
  ```bash
  AWS_PROFILE=stanford-sso AWS_DEFAULT_REGION=us-gov-west-1 \
    sms-cdk/scripts/ptools-proxy.sh -s smsvpctest      # forwards localhost:8080
  ```
  Keep it running in its own terminal. `SMS_API_BASE` defaults to
  `http://localhost:8080`.
- **Using it:** the **Source** panel's **"sms-api builds"** scope lists the
  simulator versions sms-api has built. Register/switch a `repo@commit` build
  (sms-api builds it on demand), then submit runs — they execute remotely
  (Ray → AWS Batch → zarr/parquet on S3) and land back as study runs you can
  browse, with status polled in the UI.

### Public read-only — published snapshot

Publish a workspace to a **self-contained static bundle** servable by any static
host (CDN / object storage) with no backend — for sharing committed results
publicly:

```bash
vivarium-workbench-publish --workspace . --out ./bundle --base-path /dashboard/<name>
```

- Read-only: all mutating/compute routes are stripped; it serves committed
  investigations, studies, runs, composites, and registry. The same stripping is
  available on a *live* server via `VIVARIUM_WORKBENCH_READONLY=1`.
- The **Source** page becomes a navigator across sibling published workspaces;
  **"Sync to local"** reproduces a published `repo@commit` on your machine via
  `vivarium-workbench sync <url>` (clone at the commit + lockfile-pinned env +
  cache rebuild) — the round-trip back to local authoring.

These three planes — and the round-trip between them — are specified in
[`docs/superpowers/specs/2026-06-27-vivarium-server-three-plane-architecture-design.md`](docs/superpowers/specs/2026-06-27-vivarium-server-three-plane-architecture-design.md).

## Tabs at a glance

- **Workspace inputs** — workspace.yaml summary, dependencies, scaffolding status.
- **Registry** — every Process / Step / Composite the workspace can import.
- **Composites** — composite browser with an embedded
  [bigraph-loom](https://github.com/vivarium-collective/bigraph-loom) view of
  the state-tree (the bigraph), served from the `bigraph-loom` package at
  `/loom-explore`.
- **Studies** — canonical 8-section view (Purpose · Pipeline gate · Build ·
  Simulations · Readouts · Tests · Limitations · References) with phase chip
  and rolled-up `effective_status`.
- **Investigations** — DAG canvas grouping studies into research arcs, with
  a "+ New Investigation" creator. *(Final polish shipping in PR #18.)*
- **Visualizations** — render Visualization Steps wired into composites.
- **GitHub Branches** — active branch, push, open PR for the workstream.

## Companion repos

- **[pbg-superpowers](https://github.com/vivarium-collective/pbg-superpowers)** — the Claude Code plugin whose `/pbg-*` skills drive this dashboard's HTTP API. Use it for AI-assisted authoring.
- **[pbg-template](https://github.com/vivarium-collective/pbg-template)** — the workspace scaffold this dashboard serves. Includes the canonical `.pbg/schemas/` validators.
- **investigation-contracts** — event-log contracts shared by both spines. Not yet on PyPI; install editable from the repo: `pip install -e /path/to/investigation-contracts`.

## Migrating an older workspace

If your workspace has `investigations/<name>/spec.yaml` directories from
before schema_version 3, run the one-time migration:

```bash
vivarium-workbench migrate-investigations --workspace /path/to/workspace
# add --dry-run to preview
```

## License

TBD — license file pending before 1.0.
