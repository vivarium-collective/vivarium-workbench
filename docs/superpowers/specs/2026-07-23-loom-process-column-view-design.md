# bigraph-loom: process-column view, store-affinity clustering, and semantic zoom

**Date:** 2026-07-23
**Status:** design approved, ready for planning
**Scope:** `vivarium_workbench/loom/` (the vendored loom source that the workbench builds)

## Problem

The v2ecoli `baseline` composite renders in loom's Wiring tab as an unreadable hairball. 55 processes with roughly eight ports each produce on the order of 400 wire edges spanning the full canvas. Two distinct failures compound:

- **Browsability.** Processes are hand-placed in a square-ish grid block to the right of the store hierarchy (`layout.ts:180-201`). There is no ordering a reader can exploit, no grouping, and no way to scan the process inventory.
- **Legibility.** Every wire is drawn at all times. No arrangement of 55 process nodes makes 400 crossing edges readable, so fixing position alone does not fix the view.

The fix must be infrastructural rather than a one-off layout hack: loom currently hardcodes a single layout with no seam for alternatives.

## Goals

1. Processes browsable in an ordered, grouped single column to the left of the stores.
2. Groups derived automatically from the composite, with no authoring burden on composite writers.
3. Edge density reduced to something readable by default, with full detail on demand.
4. Process cards that reveal progressively more detail as the user zooms in.
5. A genuine extension seam, so a third and fourth view mode cost little.
6. Processes that **advertise what they do** — port semantics, config semantics, and the governing math or logic — in a structured, machine-readable form.
7. Config actually present in the composite document, instead of empty on 45 of 46 processes.

## Non-goals

- Reconciling the standalone `bigraph-loom` repo with this vendored copy. They have diverged (the vendored tree is roughly three weeks ahead, carrying `defaultHiddenIds`, `declaredEmitPaths`, collapsible inspector sections, and retuned ELK spacing). Reconciliation is real work and is deliberately deferred; this spec builds in the vendored copy only.
- Replacing the existing hierarchy layout. It remains the default.
- **Requiring** contract authoring. The document format gains an optional `_contract` key (§5) and processes may declare one, but every process without it falls back to its docstring and renders usefully on day one. Clustering (§2) remains fully automatic and never depends on authored metadata.
- Serializing resolved runtime config. Only the declared, JSON-safe form is persisted — see §7.

---

## 1. Infrastructure: a layout-mode registry

Today `applyLayout(nodes, edges)` is imported directly at `App.tsx:13` and invoked at exactly two sites — the rebuild effect (`App.tsx:264`) and the `Re-layout` handler (`App.tsx:347`). Node and edge renderers are two hardcoded object literals (`App.tsx:34-35`). There is no registry of any kind.

Introduce one:

```ts
// src/layouts/types.ts
export interface LayoutResult {
  nodes: Node[];
  bands?: GroupBand[];      // cluster headers: label, yStart, yEnd, key store id
}

export interface LayoutMode {
  id: 'hierarchy' | 'process-column' | string;
  label: string;
  run(nodes: Node[], edges: Edge[], ctx: LayoutContext): Promise<LayoutResult>;
  edgeVisibility?(edges: Edge[], focus: FocusContext, nodes: Node[]): Edge[];
  Rail?: React.ComponentType<RailProps>;
  /** Semantic-zoom tiers, ordered low to high. Omitted means fixed cards. */
  tiers?: ZoomTier[];
}
```

`src/layouts/registry.ts` exports the ordered mode list. `App.tsx` holds a `mode` state value and dispatches through the registry at both existing call sites. A segmented control (`Hierarchy | Process column`) sits in the toolbar next to `Re-layout`.

The existing ELK layout moves verbatim into `src/layouts/hierarchy.ts` as the first registry entry. **Its output must not change**; the existing `src/__tests__/layout.test.ts` is the regression gate and is not to be edited except for its import path.

### Rationale

The alternative — a new top-level tab beside `Wiring` — was rejected. It would duplicate toolbar, inspector, and selection wiring, and it splits the user's mental model of "the graph". A mode toggle keeps one graph with two arrangements, and makes the registry itself the deliverable.

---

## 2. Store-affinity clustering

`src/layouts/affinity.ts`, a pure module with no React or React Flow dependency.

### Algorithm

1. **Collect store keys per process.** Every wired path from `inputs` and `outputs`, resolved against the process's parent store path (handling `..` and `.` exactly as `convert.ts:49-57` does), truncated to depth 2 relative to that parent. Track port multiplicity per key.

2. **Filter bookkeeping stores.** Drop keys matching the noise set: `_layer_token*`, `process.*`, `process_state.*`, `request`, `next_update_time`, `pinned_flux_targets`, `timestep`, `global_time`, `allocate.*`, and any leading-underscore key.

   This mirrors, for stores, what `defaultHiddenIds` (`convert.ts:116`) already does for processes. **This step is the single largest correctness lever** — see the validation section below.

3. **Identify hub stores.** A key touched by at least `hubFraction × n` processes is a hub and is disqualified as a cluster key. Default `hubFraction = 0.30`.

4. **Assign each process** to the *most widely shared* non-hub key it touches, breaking ties by port multiplicity then lexically. Processes whose keys are all hubs fall into a terminal bucket labeled by the hubs they do touch (e.g. `bulk · listeners only`), not a bare `~global`.

5. **Divert hub processes.** A process touching more than `hubProcessKeyLimit` distinct non-hub keys (default 8) is not meaningfully "about" any one of them; it goes to a dedicated `cross-cutting` band. In the baseline, `ecoli-chromosome-structure` touches eleven distinct unique stores and would otherwise land wherever the tie-break happened to fall.

6. **Order clusters** by the mean y of their key store from the store-side layout (barycenter), so column order tracks store order and wires stay short. Processes are ordered alphabetically within a cluster.

Deterministic and pure, therefore directly unit-testable.

### Why not IDF, and why not Jaccard

The first prototype scored candidate keys by `ports × log(n / df)` — classic TF-IDF distinctiveness. **This is backwards for this problem.** Rare stores are process-*private* plumbing, not distinctive shared structure. On the real baseline it produced 36 clusters for 46 processes, 27 of them singletons, keyed on `_layer_token_7` and `next_update_time`. The useful signal is *mid-frequency*: stores shared by a meaningful minority.

Jaccard-similarity agglomerative clustering over process store-sets was also prototyped and **rejected on measured results**: 12–14 clusters with 7–8 singletons, and its largest cluster shared no distinctive store at all, so it could not even be labeled. Hub stores dominate set similarity and fragment everything else.

### Validation against the real composite

Run against `v2ecoli/reports/composite-state/v2ecoli.composites.baseline.json` (46 processes, 27 after bookkeeping-process filtering), at `hubFraction = 0.30`. Hubs excluded: `bulk`, `listeners`, `environment`, `unique.RNA`. Result — 9 clusters, 3 singletons:

| Cluster key | Processes | Reads as |
|---|---|---|
| `unique.active_ribosome` | polypeptide-elongation ×2, polypeptide-initiation, rna-degradation ×2, chromosome-structure | translation |
| `boundary` | metabolism, exchange_data, media_update, division | environment / exchange |
| `unique.promoter` | tf-binding, tf-unbinding, transcript-initiation, counts_deriver | transcriptional regulation |
| `unique.active_RNAP` | transcript-elongation ×2 | transcription elongation |
| `unique.full_chromosome` | chromosome-replication, mark_d_period | replication |
| `ppgpp_state`, `shape`, `unique.DnaA_box` | one each | ppGpp, shape, equilibrium |
| hub-only | complexation, protein-degradation, rna-maturation, two-component, emitter, global_clock | bulk chemistry |

The `_requester`/`_evolver` partition pairs group together automatically, with no name matching.

**`hubFraction` sits on a knee.** At 0.25–0.30 translation and transcription separate cleanly. At 0.35–0.40 they collapse into a single eight-member `unique.RNA` cluster. Rather than hardcode a value on the knee, it is exposed as a **granularity slider** in the rail (coarse ↔ fine, mapping to `hubFraction` roughly 0.45 → 0.20), defaulting to 0.30.

**Caveat for implementation:** this fixture was captured 2026-06-26 and `v2ecoli/composites/baseline.py` has changed three times since (`5c6fc5b9` ShapeStep growth tracking, `8bd0a1ac` process-swap removal, `ba42ad2c` 3D structural extraction). Thresholds must be re-validated against a freshly generated state before the defaults are locked.

Note that the composite's own description string — "55-process partitioned whole-cell E. coli model", hardcoded at `baseline.py:526` and shown in the Explorer header — does **not** match its contents. The fixture holds 46 wired components (45 steps, 1 process). Count processes by walking the state document, never from that string.

---

## 3. Process-column layout

`src/layouts/processColumn.ts`.

- Processes occupy a single column at `x = 0`, stacked by prefix-sum of per-card height at the current zoom tier, with a 44 px gap and a labeled band between clusters. Within a cluster, cards are separated by 16 px.
- Stores are laid out by the existing hierarchy ELK pass, translated right by `columnWidth + gutter`, where `columnWidth` is the current tier's card width (180 / 220 / 320 px, §5) and `gutter = 180 px`.
- Returns `bands: GroupBand[]` describing each cluster's label and y-range, consumed by both the rail and an optional canvas-side band annotation.

Because the column is one-dimensional, **reflow is a prefix sum, not a graph layout** — O(n), no ELK invocation. This is what makes tier changes (§5) cheap enough to run on every zoom-band crossing at 345 nodes, which the investigation graph's full-re-render approach would not sustain.

---

## 4. Focus and edge visibility

`edgeVisibility` for the process-column mode:

- **Nothing focused (default):** draw only *aggregate* edges — one edge per (cluster → key store), roughly ten edges instead of roughly 400. Place edges (the store hierarchy) are always drawn; they are structural and few.
- **Focused** — hover, click, or pin: that process's real per-port edges at full strength; all other wires dimmed to about 8% opacity.
- Pins accumulate, so two processes' wiring can be compared side by side.

The existing `retargetEdgesToVisible` dedupe (`panels/filterHidden.ts:51`) already collapses multi-port process→store fans to a single edge and is reused for the aggregate pass.

---

## 5. The process contract

A process should advertise what it *does*: what it takes from each input, what it provides on each output, what each config parameter controls, and the math or logic connecting them — plus prose a human or an LLM can read.

Most of this is already latent in the document and simply unsurfaced:

| Contract element | Available today? |
|---|---|
| identity | `address` — 46/46 |
| port names | `inputs`/`outputs` keys — 46/46 |
| port **types** | `_inputs`/`_outputs`, full bigraph-schema types — 45/46 |
| prose + math | `doc` — 45/46 have a docstring, **14/46** contain equations |
| config names/types/defaults | `config_schema` class attribute — 17+ process classes |
| config **values** | `config` — **1/46** (see §7) |
| **port semantics** | **nothing** — no way to say what a process does with a port |
| **config semantics** | **nothing** |

The math convention already exists informally. `ecoli-transcript-initiation`'s docstring reads:

```
TranscriptInitiation — distributes activated RNAPs across TUs by weighted multinomial sampling.
    n_to_activate = round(f_active · n_total_RNAP) - n_active
    p_i = max(0, basal_prob_i + ∑_j delta_prob[i,j] · bound_TF_j)
    initiations ~ Multinomial(n_to_activate, p_i / ∑_i p_i)
```

That is exactly the right content, trapped in freeform prose that no tool can parse.

### The shape

`ProcessContract` lands in **process-bigraph**, so any process in any repo advertises itself identically and every consumer — loom, report generators, doc builders, LLM agents — reads one shape. It is serialized into the composite document as `_contract`, beside `_inputs`/`_outputs`.

```python
@dataclass
class ProcessContract:
    summary: str                              # one line: what this process does
    description: str = ""                     # prose for a human or an LLM
    inputs: dict[str, str] = field(default_factory=dict)   # port -> what is read and why
    outputs: dict[str, str] = field(default_factory=dict)  # port -> what is written
    config: dict[str, str] = field(default_factory=dict)   # param -> what it controls
    math: list[str] = field(default_factory=list)          # equation lines, unicode text
    symbols: dict[str, str] = field(default_factory=dict)  # symbol -> meaning, with units
    assumptions: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
```

Port and config semantics are the genuinely new information. Today a reader can see that `RNAs` is a `unique_array[17 fields]` but nothing tells them the process *appends newly initiated transcripts, one per multinomial draw*. Where the relationship is mathematical, `math` carries it; where it is conditional or procedural, prose in the port entry carries it.

### Adoption without a flag day

A process may declare `contract` explicitly. When it does not, the contract is **derived from the docstring**: first line becomes `summary`, indented lines containing equation markers (`=`, `~`, `∑`, `∏`, `←`, or a distribution name) become `math`, remaining prose becomes `description`. Port and config semantics stay empty until authored.

This means all 45 processes with docstrings render something useful on day one, and processes upgrade incrementally. No migration is required, and nothing regresses.

### Contract completeness

Because the contract declares port names, they can be checked against the real `_inputs`/`_outputs` keys. Loom surfaces a small completeness indicator (e.g. `4/9 ports documented`) and flags contract entries naming a port that no longer exists. This makes contract drift visible rather than silent, and gives incremental authoring an obvious target.

---

## 6. Semantic zoom on process cards

The workbench's investigation graph achieves semantic zoom with three discrete bands and **no `transform: scale()`** — it re-lays-out at band-specific card widths and toggles sections, so text is never scaled to unreadability (`static/aig-graph.js:91-99`, `static/walkthrough.js:6079-6089`). That code is vanilla JS bound to `document.getElementById` and dashboard-only helpers; it is **not importable** into loom's React/TypeScript tree. The *idiom* ports; the code does not.

Loom has React Flow's real continuous zoom, so tiers are driven from `useStore(s => s.transform[2])` inside `ProcessNode.tsx`:

Five tiers, each revealing exactly one new kind of information so the card never jumps two steps at once:

| Tier | Zoom | Width | Adds |
|---|---|---|---|
| **glyph** | < 0.25 | 180 | kind-colored bar, name |
| **ports** | 0.25–0.5 | 220 | kind badge, `6 in / 3 out`, interval, port **names** |
| **types** | 0.5–0.9 | 300 | port **types** (abbreviated), address, config parameter **names** |
| **contract** | 0.9–1.6 | 380 | contract `summary`, `math` lines, per-port semantics, config **values** |
| **full** | > 1.6 | 460 | `symbols` with units, `description`, assumptions, references, completeness indicator |

Card height is content-driven at every tier; the widths above are fixed.

**Type strings must be abbreviated.** A real port type runs past 300 characters — `unique_array[TU_index:integer|transcript_length:integer|is_mRNA:boolean|…]`. The `types` tier renders `unique_array[17 fields]`, with the full string on hover and in the Inspector. Rendering them raw would defeat the entire purpose of the view.

Following the investigation graph's discipline: **font sizes are identical across all five tiers**. Legibility at low zoom comes from dropping content, never from shrinking text. Thresholds keep effective rendered type at or above roughly 9 CSS px. Full detail remains reachable at any tier via the existing Inspector.

Additions:
- A per-card **⤢ pin-expanded** toggle holds one card at `near` regardless of zoom, so details stay readable while zoomed out.
- Tier crossings are debounced (≈120 ms) and hysteretic (bands overlap by ≈0.03 zoom) so cards do not flicker while the user scrolls across a threshold.

Semantic zoom applies to the process-column mode only in this iteration. The hierarchy mode keeps fixed-size cards, since a tier change there would require a full ELK re-run.

---

## 7. Fixing the missing config

Only **1 of 46** processes carries a non-empty `config` in the composite document. Investigation established this is an **unfinished feature, not a design decision**:

- `v2ecoli/composites/_helpers.py:1017` reads `getattr(instance, '_raw_config', {})`. A repo-wide grep for `_raw_config` returns **exactly one hit — that read**. Nothing ever assigns it.
- None of the 26 `make_edge(` call sites in `v2ecoli/` passes `config=`.
- The config *does* exist: `baseline.py:378-417` loads it from the ParCa cache and `_make_instance` passes it to the constructor, where `EcoliStep.__init__` (`v2ecoli/library/ecoli_step.py:111-124`) stores it as `self.parameters` and never calls `super().__init__()`, so `bigraph_schema.edge.Edge`'s `config` property is never populated either.
- Nothing downstream strips it. `serialize(schema: Link, …)` (`bigraph-schema/methods/serialize.py:696-729`) reads `state['config']` and faithfully reflects the empty dict.
- `shape_step` is the sole exception because `baseline.py:901-908` declares it as a dict literal, bypassing `make_edge` entirely. It is correspondingly the only node lacking `_inputs`/`_outputs` and `doc`.

Two independent fixes, cheapest first:

**(a) Attach `config_schema` — names, types, defaults.** Declared on 17+ v2ecoli process classes, e.g. `transcript_initiation.py:221-272` with ~40 entries carrying bigraph types and defaults (`'cell_density': {'_type': 'quantity[g/L]', '_default': 1100.0}`). The workbench's `_attach_process_docs` (`env_worker.py:427-451`) **already imports the class from `node['address']`** to fetch the docstring; attaching `config_schema` in that same walk is roughly five lines with no new imports and no new I/O. There is precedent — the Registry tab already renders `config_schema` (`env_worker.py:204-209`, `walkthrough.js:1603`).

**(b) Populate declared config values.** Set `instance._raw_config` in `_make_instance`, or pass `config=` through `make_edge`. Use the **declared** (pre-`resolve_config`) form, which is JSON-safe by construction — `config_resolver.py:14-42` stores callables as `{"_function": …, "_data": …}` refs precisely so configs can round-trip. The existing `_summarize_large_values` decorator (`env_worker.py:406-424`) already caps oversized arrays.

**Never serialize `instance.parameters`.** The resolved runtime config holds live bound methods (20 `'_type': 'method'` entries across 9 modules), pint Quantities, `UnitStructArray`s, and multi-thousand-element arrays sourced from a 165 MB dill. Methods cannot be JSON-encoded at all.

Fix (a) alone makes the `types` tier's config-names row work everywhere. Fix (b) makes the `contract` tier's config-values row real. Both are prerequisites for those tiers showing anything on v2ecoli, and both live outside loom — in v2ecoli and the workbench's env worker.

---

## 8. Persistence

- `View` (`viewStore.ts:27-33`) gains `mode?: string` and `pins?: string[]`. `normalizeView` (`:138`) defaults absent `mode` to `'hierarchy'`, so every previously saved view and shared `?view=` link keeps working unchanged.
- Layout localStorage keys become mode-scoped: `bigraph-loom:layout:<compositeId>:<mode>`, so hand-dragged positions in one mode do not corrupt the other.
- Viewport zoom remains unstored, per the existing deliberate choice (`viewStore.ts:10`). The semantic-zoom tier is therefore derived, never persisted.

---

## 9. Module boundaries

`App.tsx` is 775 lines and already holds all application state. Adding mode, focus, pins, and tier state inline would make it materially worse. Two hooks are extracted as part of this work:

- `useLayoutMode()` — owns mode selection, registry dispatch, node positions, and band data.
- `useFocus()` — owns hover/selection/pin state and derives edge visibility.

`App.tsx` becomes composition over these. This is targeted at the code being touched; no unrelated refactoring.

**Hazard:** there is a documented `hiddenRef` race at `App.tsx:71-81`, and object-identity preservation in the two `setNodes` reducers (`:273-286`, `:350-362`) that stops React Flow remounting the whole graph on every collapse. Both must be read before editing and preserved.

### Files

New: `layouts/types.ts`, `layouts/registry.ts`, `layouts/hierarchy.ts` (moved), `layouts/affinity.ts`, `layouts/processColumn.ts`, `panels/ProcessRail.tsx`, `hooks/useLayoutMode.ts`, `hooks/useFocus.ts`.

Modified: `App.tsx` (mode/focus wiring, toolbar control), `nodes/ProcessNode.tsx` (tiers), `layoutStore.ts` (mode-scoped keys), `viewStore.ts` (mode + pins), `App.css` (bands, dimming, tiers), `layout.ts` (thin re-export shim for back-compat).

---

## 10. Phasing

The work is large enough that it should land in reviewable increments, each independently useful and each leaving the app working:

- **P1 — registry seam.** `layouts/types.ts`, `registry.ts`, `hierarchy.ts` (moved verbatim), `useLayoutMode()`, toolbar control with a single registered mode. No visible change; the regression gate is that hierarchy output is unchanged.
- **P2 — clustering.** `affinity.ts` plus its tests and the baseline fixture. Pure module, no UI. Validates cluster quality against the current 55-process baseline before anything depends on it.
- **P3 — column layout.** `processColumn.ts` registered as the second mode, fixed-size cards, all edges drawn. First visible result.
- **P4 — focus and edge culling.** `useFocus()`, aggregate edges, dimming, pins. This is where legibility actually arrives.
- **P5 — rail.** `ProcessRail.tsx`, search, scroll-sync, granularity slider.
- **P6 — semantic zoom.** The five tiers in `ProcessNode.tsx`, prefix-sum reflow, type abbreviation, hysteresis, pin-expanded.

Three phases live **outside loom** and unblock the richer tiers. They touch different repos and can run in parallel with P1–P5:

- **P7 — config values** (v2ecoli). Populate `_raw_config` with the declared config, per §7(b). One-line root fix plus a test asserting non-empty config on the baseline. Unblocks the `contract` tier's config row.
- **P8 — config schema** (vivarium-workbench). Attach `config_schema` in `_attach_process_docs`, per §7(a). Roughly five lines. Unblocks the `types` tier's config-names row.
- **P9 — process contract** (process-bigraph, then v2ecoli). The `ProcessContract` dataclass, `_contract` serialization, and the docstring-derived fallback. Unblocks the `contract` and `full` tiers.

P1 and P2 are independent and can proceed in parallel. P3 depends on both. P6 renders whatever P7–P9 have delivered and degrades gracefully when they have not: a tier with no data for a row omits that row rather than showing an empty box.

## 11. Testing

- `affinity.test.ts` — noise-store filtering, hub exclusion, hub-process diversion, cluster stability, determinism. Includes the explicit regression that unfiltered noise stores produce singleton explosion.
- `processColumn.test.ts` — column geometry, no card overlap at any tier, band y-ranges, prefix-sum reflow correctness.
- `hierarchy.test.ts` — the existing `layout.test.ts`, import path only, as the no-change gate.
- `registry.test.ts` — every registered mode satisfies the interface.
- `semanticZoom.test.tsx` — the five tier thresholds, hysteresis, pin-expanded override, and that each tier renders exactly the rows it owns.
- `ProcessRail.test.tsx` — search filtering, click-to-focus, selection scroll-sync, granularity slider.
- `viewStore.test.ts` — a saved view lacking `mode` normalizes to `hierarchy`.
- `contract.test.ts` (loom) — docstring-derived fallback extracts summary, math lines, and description; a declared `_contract` wins over the fallback; completeness counts documented ports against real `_inputs`/`_outputs` keys; a contract naming a nonexistent port is flagged.
- `typeAbbrev.test.ts` (loom) — `unique_array[a:integer|b:float|…]` renders as `unique_array[N fields]`; short types pass through unchanged; the full string is preserved for the hover title.
- `test_process_contract.py` (process-bigraph) — a declared contract round-trips through serialize/realize into `_contract`; a process without one still yields a derived contract; contract absence never raises.
- `test_config_present.py` (v2ecoli) — the baseline composite yields non-empty `config` on substantially all processes, and every config value is JSON-serializable. This is the regression gate for §7 — it is precisely the assertion whose absence let config sit empty on 45/46 processes unnoticed.

**Fixture:** `src/__tests__/fixtures/v2ecoli-baseline.json`, a freshly generated baseline state, serving as the clustering-quality fixture, the contract-rendering fixture, and a render performance smoke test. Regenerate it after P7–P9 land so it carries real config and contracts.

## Success criteria

1. The v2ecoli baseline opens in process-column mode showing under 20 edges by default, versus roughly 400 today.
2. Clusters are recognizable to a modeler as transcription, translation, replication, regulation, environment, and bulk chemistry, without any annotation added to `baseline.py`.
3. Any process is locatable within about two seconds via rail search.
4. Hierarchy mode output is byte-identical to today.
5. Tier changes and mode switches stay visually smooth at 345 nodes.
6. Zooming into a process card walks the full ladder — name, ports, port types, contract math, symbols — with no row showing an empty placeholder.
7. Config is non-empty on substantially all baseline processes, not 1 of 46.
8. Every process with a docstring renders a contract without anyone authoring one; processes that declare a structured contract render port and config semantics on top.
