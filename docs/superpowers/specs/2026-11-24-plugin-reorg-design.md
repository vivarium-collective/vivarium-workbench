# Plugin reorganization — design

**Goal:** make the plugin boundary a first-class, well-trodden path, so more
plugins (external and first-party) land easily and the first-party features
(Chat, Cycle) can be extracted cleanly *when we choose to* — without a core
rewrite.

**Why:** external collaborators should be able to iterate opt-in without touching
the core stack (the driver behind the plugin seam / viva-knowledge-layer), and we
want to eventually lift Chat and Cycle out of the 9000-line `api/app.py` into
plugins. This reorg lays the ground for both; it does **not** do the extraction.

## Non-goals / anti-goals

- **Not** extracting Chat or Cycle now. That is deferred and gated on an explicit
  decision; this work only removes the obstacles.
- **Not** a repo-wide reshuffle of `app.py`'s ~257 routes. Speculative and
  high-regression; much of it is only knowable once a feature is actually
  extracted (with a real consumer and the suite as guard).
- **Not** a new module marketplace. The existing **Marketplace tab** (PR #600,
  `lib/federation.py`) federates *workspace modules* (studies/composites/
  investigations). Plugins are *code extensions* (routers/panels) — a separate
  axis. We borrow its UX ideas, not its code.

## Approaches considered

- **(A) Full reshuffle now** — move every feature's route-block into a router
  module and carve every Chat/Cycle coupling up front. *Rejected:* speculative
  (YAGNI — no commitment to extract yet), high regression risk across 257 routes,
  and the feature-specific shape is only clear at extraction time.
- **(B) Phased, YAGNI-scoped** — *chosen.* Ship the additive, reusable
  improvements now (authoring kit, registry/convention, the two general seams the
  recon identified as blockers); fold the feature-specific surgery into the
  eventual per-feature extraction. Each phase is independently shippable and
  changes nothing for a workspace with no plugins enabled.
- **(C) Docs-only** — just write conventions. *Rejected:* leaves authoring
  non-turnkey and the general seams still missing, so "extract later" stays hard.

## Plan (phased; each phase its own PR-sized unit)

### Phase 1 — Authoring kit (additive, no behavior change)
- `vwb new-plugin <name>` scaffolder: writes a runnable skeleton (`pyproject.toml`
  with the `vivarium_workbench.plugins` entry-point, `plugin.py`, `api.py` router,
  `static/plugin.json` + a panel JS on the `ctx` contract, one test).
- Promote `tests/_fixtures/plugins/exampleplugin` into the worked reference the
  docs point at (keep it as the loader's test fixture too).
- Expand `docs/plugins.md` into a step-by-step "author a plugin in minutes."

### Phase 2 — Registry & layout convention (additive, docs + light data)
- `docs/plugins-registry.md` + a `plugins.yaml` catalog: the convention —
  external/experimental plugins live in their own `viva-<name>` repo under
  `vivarium-collective`, declare the entry-point, pin a plugin `api_version`;
  enable per-workspace. A curated list of known plugins (knowledge-layer = first
  entry) with how to enable each.
- Document `api_version` compatibility (`base.py::WORKBENCH_PLUGIN_API_VERSION`)
  and the fail-soft behavior.

### Phase 3 — The two general seams (small additive core edits, suite-guarded)
These are the couplings the Chat/Cycle recon flagged as the real blockers; both
are reusable by *any* plugin, so carving them now is requested prep, not
speculation.
- **Plugin AI-exclusion:** add `ai_excluded: list[str]` to the plugin contract
  (`PluginContributions` + `reg.ai_excluded([...])`) and have the loader feed
  plugin exclusions into the policy `lib/ai_tools.py` aggregates — so any plugin
  (and a future Chat plugin) can declare "don't expose these routes to the AI."
- **Host API on the panel ctx:** surface the stable host hooks a panel needs
  (workspace-view refresh — today the `_vivRefreshDiscoveryViews` global — and
  `VivPanelDock`) as documented `ctx.host.*`, so a future Cycle plugin calls a
  stable API instead of a `window` global.

## Deferred — part of the eventual extraction, NOT this reorg
- Splitting the shared dock math out of `static/chat-core.js` (needed only when
  Chat extracts; it's shared with `panel-dock.js` / `process-code.js`).
- Moving Chat's (`/api/ai/*`, `/api/chat/turn`) and Cycle's
  (`/api/discovery-cycle/*`) route-blocks into `api/routers/<feature>.py` — the
  first commit of each feature's extraction. The route deps already live in
  `api/deps.py` precisely to allow this.
- Any repo-wide `app.py` route reshuffle.
