# Plugin registry & conventions

Where workbench plugins live, how they're named and discovered, and the curated list
of known plugins. For *authoring* one, see [plugins.md](plugins.md); for the seam's
design, see [the plugin-system spec](superpowers/specs/2026-11-23-workbench-plugin-system-design.md).

> **Plugins vs. the Marketplace.** The **Marketplace** tab federates *workspace
> content* — studies, composites, investigations from other repos. **Plugins** are
> *code extensions* — a router + panels + skills a package adds to the workbench
> itself. Different axes; this page is about the latter.

## Where a plugin lives

Each plugin is **its own repository**, not a directory in this one:

- **Name:** the plugin's `name` is a url-safe slug (`^[a-z0-9][a-z0-9_-]*$`) — it
  namespaces every route (`/api/plugins/<name>`) and asset (`/plugins/<name>`).
- **Repo:** `vivarium-collective/viva-<name>` (e.g. `viva-knowledge-layer`).
- **Package:** `viva_<name_with_underscores>` (e.g. `viva_knowledge_layer`).
- **Entry-point:** the package declares one `vivarium_workbench.plugins` entry-point
  keyed by the plugin name. That is the whole discovery contract — install the
  package and the workbench finds it.

`vwb new-plugin <name>` lays all of this out correctly; prefer it to copying by hand.

Keeping each plugin in its own repo is deliberate: a collaborator can iterate and
release on their own cadence, with their own issues and CI, without a PR to core —
the point of the seam. First-party features extracted into plugins later (e.g. Chat,
Cycle) follow the same convention.

## Discovery, enablement, compatibility

- **Discovered** = the package is installed (entry-point visible). **Enabled** =
  additionally listed in a workspace's `workspace.yaml` `plugins:` *or* the
  `VIVARIUM_WORKBENCH_PLUGINS` env allowlist. A plugin runs only when both — default
  off. Full rules in [plugins.md](plugins.md#enabling-a-plugin-opt-in-default-off).
- **See what's here:** `vwb plugins [--workspace DIR]` lists installed plugins and
  whether each is enabled for that workspace (`--json` for scripts).
- **API version:** each plugin pins `api_version` (see
  `vivarium_workbench.plugins.WORKBENCH_PLUGIN_API_VERSION`, currently **1**). The
  contract — `WorkbenchPlugin` / `PluginRegistry` / the panel `ctx` — is stable within
  a major api version. A plugin built for a newer version than the host provides may
  not load cleanly; bump the pin only when you adopt a new contract feature.

## Known plugins

| Plugin | Repo | Adds | Status |
|---|---|---|---|
| `knowledge-layer` | [vivarium-collective/viva-knowledge-layer](https://github.com/vivarium-collective/viva-knowledge-layer) | MIRA knowledge store + Graph / Spec / Canvas / MIRA-store panels | active |

Adding a plugin here: open a PR to this file once the repo exists, declares the
entry-point, and installs cleanly. Keep the row to one line.
