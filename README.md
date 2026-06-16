# vivarium-dashboard

Local web UI for [Process-Bigraph](https://github.com/vivarium-collective/process-bigraph)
workspaces. Browse composites, run studies, inspect state-trees, and render
visualizations — without writing dashboard boilerplate.

Point it at a workspace directory (one containing `workspace.yaml`) and it
serves an interactive UI over the workspace's registry, composites, studies,
and reports.

> **Status:** in active beta. APIs and UI may change before 1.0.

For a deeper look at what the dashboard does, where data lives, the run/render
data lifecycles, and how it relates to its companion repos, see
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). For how the dashboard is deployed
alongside a workspace (it's a pip dependency installed into the workspace's
venv), see [docs/USAGE.md](docs/USAGE.md).

## Getting Started

### 1. Install

```bash
pip install vivarium-dashboard         # or: uv pip install vivarium-dashboard
```

Not on PyPI yet during beta — install editable from a clone instead:

```bash
git clone https://github.com/vivarium-collective/vivarium-dashboard ~/code/vivarium-dashboard
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
vivarium-dashboard serve --workspace .
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

## Migrating an older workspace

If your workspace has `investigations/<name>/spec.yaml` directories from
before schema_version 3, run the one-time migration:

```bash
vivarium-dashboard migrate-investigations --workspace /path/to/workspace
# add --dry-run to preview
```

## License

TBD — license file pending before 1.0.
