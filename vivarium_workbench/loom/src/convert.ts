// src/convert.ts — composite-state → React Flow nodes + edges.
// Data shapes match ProcessNodeData and StoreNodeData from src/types.ts.

import { MarkerType } from '@xyflow/react';
import type { StoreNodeData, ProcessNodeData } from './types';
import { storeColor } from './storeColor';

type RFNode =
  | { id: string; type: 'store'; data: StoreNodeData; position: { x: number; y: number } }
  | { id: string; type: 'process'; data: ProcessNodeData; position: { x: number; y: number } };

type RFEdge = {
  id: string;
  source: string;
  target: string;
  type?: string;
  sourceHandle?: string;
  targetHandle?: string;
  label?: string;
  animated?: boolean;
  style?: Record<string, string | number>;
  markerEnd?: { type: MarkerType; width?: number; height?: number; color?: string };
  data?: { edgeType: 'input' | 'output' | 'bidirectional' | 'place'; storeColor?: string };
};

/** Arrowhead used on directional wires (input + output edges).
 *  Place edges stay un-arrowed — they're nesting relationships, not flow. */
const WIRE_ARROW = { type: MarkerType.ArrowClosed, width: 14, height: 14, color: '#475569' };

/** A Composite Process built via `type(...)` (or other builtins) inherits the
 *  builtin's docstring — junk like "type(name, bases, dict, **kwds) -> a new
 *  type". Suppress those so a card shows no description rather than noise. */
const JUNK_DOC = /^\s*(type\(name, bases|Create and return|str\(object=|int\(\[|dict\(\)|list\(\)|tuple\(\)|object\(\)|The base class of the class hierarchy)/;
function cleanDoc(doc: unknown): string | undefined {
  if (typeof doc !== 'string') return undefined;
  const d = doc.trim();
  return d && !JUNK_DOC.test(d) ? doc : undefined;
}

/**
 * Compact display string for a store leaf value. CRITICAL for big composites:
 * a whole-cell `bulk` store is a multi-MB array of thousands of molecules —
 * `String(value)` on that stringifies megabytes into a node label and makes
 * rendering crawl. Summarize arrays as `Array(N)` and truncate long scalars.
 */
function displayValue(value: any): string | null {
  if (value == null) return null;
  if (Array.isArray(value)) return `Array(${value.length})`;
  const s = String(value);
  return s.length > 80 ? s.slice(0, 77) + '…' : s;
}

/**
 * Resolve a process port's wire target to an ABSOLUTE store path. Targets are
 * written RELATIVE to the process's parent store (e.g. a process at
 * `agents.0.foo` wiring port→`['bulk']` means `agents.0.bulk`, and
 * `['unique','RNA']` means `agents.0.unique.RNA`). `'..'` walks up. Without this
 * the joined target never matches a store node id and the wire is dropped.
 */
function resolveWirePath(parentPath: string[], target: unknown): string[] {
  const segs = Array.isArray(target) ? (target as unknown[]).map(String) : [String(target)];
  const out = [...parentPath];
  for (const seg of segs) {
    if (seg === '..') out.pop();
    else if (seg !== '.') out.push(seg);
  }
  return out;
}

/**
 * Top-level store keys of a composite state — every key whose node is not a
 * process/step. Mirrors the dashboard's `all_store_paths`; used to seed the
 * View tab's emit selection so all states emit by default.
 */
/**
 * Group-store node ids to collapse BY DEFAULT, so a huge whole-cell composite
 * opens as a light overview instead of laying out + rendering hundreds of deep
 * nodes (ELK layout of ~300 nested stores is the load bottleneck). Collapses
 * every container store at depth >= `minDepth` (e.g. agents.0.listeners and
 * below), leaving the top levels + processes visible. Users expand by
 * double-clicking or via the Nodes tab.
 */
/**
 * Metadata key the backend embeds INSIDE a served composite `state` tree to
 * carry its declared emit-all paths (see `composite_state_views.
 * _embed_declared_emit_paths` / `composite_resolve.declared_emit_paths`).
 * Nested inside `state` — not a sibling field on the outer payload — because
 * every hop that forwards a composite doc to loom (the dashboard's
 * `composite:load` postMessage, the `?stateUrl=` static fetch, the
 * `?composite=` URL param) forwards only the `state` sub-object and drops
 * payload-level siblings. Excluded from every state-tree walker below (it's
 * metadata, not a store).
 */
export const DECLARED_EMIT_PATHS_KEY = '_declared_emit_paths';

export function defaultCollapsedIds(state: any, minDepth = 3): Set<string> {
  const root = state?.state ?? state ?? {};
  const out = new Set<string>();
  function walk(node: any, path: string[]) {
    if (!node || typeof node !== 'object' || Array.isArray(node)) return;
    if (node._type === 'process' || node._type === 'step') return;
    if ('_type' in node) return;  // typed leaf store
    if (path.length >= minDepth && Object.keys(node).length > 0) {
      // Keep the `unique` store expanded by default so its sub-stores (RNA, DNA,
      // …) are visible; still recurse so those sub-stores collapse.
      if (path[path.length - 1] !== 'unique') {
        out.add(path.join('.'));
      }
    }
    for (const [k, v] of Object.entries(node)) {
      if (k === DECLARED_EMIT_PATHS_KEY) continue;
      walk(v, [...path, k]);
    }
  }
  walk(root, []);
  return out;
}

/**
 * Process node ids hidden BY DEFAULT to declutter the whole-cell view: the
 * per-generation `unique_update*` updaters, the `allocator_*` allocators, and
 * the `*listener*` listeners are bookkeeping processes that dominate the graph
 * with wires without being the biology. Node ids are the joined path; a hidden
 * process cascades via `isHiddenByAncestor`. Users can re-show any via the
 * Processes sidebar.
 */
export function defaultHiddenIds(state: any, onlyProcesses = false): Set<string> {
  // Also honor ?only=processes from the URL, so EVERY re-seed site (applyView,
  // composite (re)load, machine restore) hides stores — a process-contract view
  // that survives view application, not just the initial mount.
  if (!onlyProcesses) {
    try { onlyProcesses = new URLSearchParams(window.location.search).get('only') === 'processes'; }
    catch { /* non-browser / no location */ }
  }
  const root = state?.state ?? state ?? {};
  const out = new Set<string>();
  const isNoise = (name: string) =>
    name.includes('unique_update') ||
    name.startsWith('allocator') ||
    name.includes('listener');
  // The emitter (observation sink) is run-time plumbing auto-materialized by the
  // composite generator, not biology — hide it by default so Explore shows the
  // science graph. Re-showable via the Processes sidebar; its observables are
  // controlled in the card's Outputs tab. Matches `emitter` / `emitter_<i>`.
  const isEmitter = (name: string) => name === 'emitter' || name.startsWith('emitter_');
  // Bookkeeping STORES that clutter the biology view without being biology:
  // the allocator RNG, per-tick timing scratch, config-only stores, and the
  // top-level `global_time` duplicate (the per-agent `agents.0.global_time`
  // stays). Stores are typed leaves, so they're matched before the leaf return.
  const isNoiseStore = (name: string, path: string[]) =>
    name.startsWith('allocator') ||
    name === 'timestep' ||
    name === 'next_update_time' ||
    name === 'pinned_flux_targets' ||
    (name === 'global_time' && path.length === 1);
  function walk(node: any, path: string[]) {
    const name = path[path.length - 1] || '';
    // Noise STORES can be BARE-VALUE leaves (e.g. top-level `global_time = 0`,
    // not `{_type:'float'}`), so match on name/path BEFORE bailing on non-objects
    // — otherwise a scalar global_time is walked past and never hidden.
    if (path.length > 0 && isNoiseStore(name, path)) { out.add(path.join('.')); return; }
    // ?only=processes — a process-contract view (e.g. Fig 4b / Fig 7): hide EVERY
    // store (bare leaf, typed leaf, and group container) so only the process
    // card(s) remain. Processes are never hidden here — the branch below returns
    // before this can add them.
    const hideStore = () => { if (onlyProcesses && path.length > 0) out.add(path.join('.')); };
    if (!node || typeof node !== 'object' || Array.isArray(node)) { hideStore(); return; }
    if (node._type === 'process' || node._type === 'step') {
      // Hide auto-materialized emitter plumbing, but NOT a draft emitter authored
      // as part of a (conceptual) figure — that one is meant to be shown.
      if (isNoise(name) || (isEmitter(name) && node._draft !== true)) out.add(path.join('.'));
      return;
    }
    if ('_type' in node) { hideStore(); return; }  // other typed leaf store
    hideStore();                                   // group / container store
    for (const [k, v] of Object.entries(node)) {
      if (k === DECLARED_EMIT_PATHS_KEY) continue;
      walk(v, [...path, k]);
    }
  }
  walk(root, []);
  return out;
}

export function topLevelStorePaths(state: any): string[] {
  const root = state?.state ?? state ?? {};
  return Object.entries(root)
    .filter(([k, v]) => {
      if (k === DECLARED_EMIT_PATHS_KEY) return false;
      if (v && typeof v === 'object' && !Array.isArray(v)) {
        const t = (v as { _type?: string })._type;
        return t !== 'process' && t !== 'step';
      }
      return true;  // scalar leaf — a store
    })
    .map(([k]) => k);
}

/**
 * Declared emit-all paths for a composite. Two sources, preferred in order:
 *
 * 1. The served `state`'s own `_declared_emit_paths` metadata (see
 *    `DECLARED_EMIT_PATHS_KEY`) — the backend resolves this from the
 *    composite's `emitters=[...]` declaration (decorator or spec `emitters:`
 *    key) at serve time, independent of whether the composite has ever
 *    actually been run. This is the REAL shape the Explorer/loom receives
 *    for a browsed-not-yet-run composite.
 * 2. A legacy fallback: scan the state tree for an INSTALLED emitter step
 *    node (the `install_default_emitters` convention — a top-level `step`
 *    node keyed `emitter`/`emitter_<i>` whose `config.emit` lists the
 *    columns it emits and whose `inputs` map each to absolute path
 *    segments). Only present in a state that was built through the
 *    run-execution path (`install_default_emitters` is not called on the
 *    browse/view path), but kept as a fallback for that case and for any
 *    already-run/exported state that still carries the node.
 *
 * Paths are returned dot/slash-joined (`'/'`), matching `emitSet`'s
 * convention. `global_time` is excluded from both sources — it's always
 * emitted for the trajectory's time axis, not a real observable toggle.
 * Returns `[]` when the composite declares no emitter (nothing to seed
 * from), so callers fall back to `topLevelStorePaths`.
 */
export function declaredEmitPaths(state: any): string[] {
  const root = state?.state ?? state ?? {};
  if (!root || typeof root !== 'object') return [];

  const metadata = (root as Record<string, unknown>)[DECLARED_EMIT_PATHS_KEY];
  if (Array.isArray(metadata) && metadata.length) {
    return metadata.filter(
      (p): p is string => typeof p === 'string' && p.length > 0 && p !== 'global_time'
    );
  }

  const out: string[] = [];
  for (const [key, node] of Object.entries(root)) {
    if (key === DECLARED_EMIT_PATHS_KEY) continue;
    if (!node || typeof node !== 'object' || Array.isArray(node)) continue;
    const n = node as { _type?: string; config?: { emit?: unknown }; inputs?: Record<string, unknown> };
    if (n._type !== 'step' && n._type !== 'process') continue;
    if (!n.config?.emit || typeof n.config.emit !== 'object') continue;
    for (const [inputKey, target] of Object.entries(n.inputs ?? {})) {
      if (inputKey === 'global_time') continue;
      const parts = Array.isArray(target) ? (target as unknown[]).map(String) : [String(target)];
      if (parts.length) out.push(parts.join('/'));
    }
  }
  return out;
}

/**
 * Initial `emitSet` seed for a composite: its declared emit-all paths when
 * present (see `declaredEmitPaths`), else every top-level store (the prior
 * default — `topLevelStorePaths`). Used at every emitSet seed site so the
 * Composite Explorer's live Results view captures what the composite itself
 * declares, not just an arbitrary top-level-store guess.
 */
export function initialEmitSet(state: any): Set<string> {
  const declared = declaredEmitPaths(state);
  return new Set(declared.length ? declared : topLevelStorePaths(state));
}

export function stateToReactFlow(state: any): { nodes: RFNode[]; edges: RFEdge[] } {
  const nodes: RFNode[] = [];
  const edges: RFEdge[] = [];
  const root = state?.state ?? state ?? {};

  const pathKey = (path: string[]) => (path.length ? path.join('.') : '<root>');

  function walk(node: any, path: string[]) {
    if (!node || typeof node !== 'object' || Array.isArray(node)) {
      // Scalar leaf — render as a store with a display value
      nodes.push({
        id: pathKey(path),
        type: 'store',
        data: {
          label: path[path.length - 1] ?? '<root>',
          nodeType: 'store',
          value: displayValue(node),
          valueType: Array.isArray(node) ? 'array' : typeof node,
          path,
          figure: (node as { _figure?: string } | null)?._figure ?? undefined,
        } satisfies StoreNodeData,
        position: { x: 0, y: 0 },
      });
      return;
    }

    if (node._type === 'process' || node._type === 'step') {
      const id = pathKey(path);
      const parentPath = path.slice(0, -1);  // wire targets are relative to this
      // Show the DECLARED interface ports (`_inputs`/`_outputs`) so an UNWIRED
      // draft process still renders its ports; fall back to the wiring keys.
      const inputPorts = Object.keys((node as any)._inputs ?? node.inputs ?? {});
      const outputPorts = Object.keys((node as any)._outputs ?? node.outputs ?? {});

      // Build inputPortsSchema / outputPortsSchema from wiring targets (informational).
      // These keep the RAW target joined verbatim — it is what the port hover
      // tooltips and the Inspector display, so it must read exactly as authored.
      // NOTE it is deliberately lossy and NOT parseable: `['..','bulk'].join('.')`
      // is `'...bulk'`, indistinguishable from `['.','.','bulk']`. Anything that
      // needs the actual store must use the resolved *PortsTarget fields below,
      // never re-split these strings.
      const inputPortsSchema: Record<string, string> = {};
      const outputPortsSchema: Record<string, string> = {};
      // ...and the RESOLVED absolute dotted store path per port (relative
      // navigation applied via resolveWirePath's push/pop stack), which is what
      // clustering/grouping consumes. `''` means the composite root.
      const inputPortsTarget: Record<string, string> = {};
      const outputPortsTarget: Record<string, string> = {};
      for (const [port, target] of Object.entries(node.inputs ?? {})) {
        inputPortsSchema[port] = Array.isArray(target) ? (target as string[]).join('.') : String(target);
        inputPortsTarget[port] = resolveWirePath(parentPath, target).join('.');
      }
      for (const [port, target] of Object.entries(node.outputs ?? {})) {
        outputPortsSchema[port] = Array.isArray(target) ? (target as string[]).join('.') : String(target);
        outputPortsTarget[port] = resolveWirePath(parentPath, target).join('.');
      }

      const procData = {
          label: path[path.length - 1] ?? '<root>',
          nodeType: 'process',
          processType: node._type ?? 'process',
          address: node.address ?? '',
          config: node.config ?? {},
          // The declared config parameter surface: name -> {_type, _default}.
          // `config` above holds only the values EXPLICITLY set (usually {} —
          // the process runs on defaults), so the card reads the schema to show
          // every parameter's type + default, overlaying set values where present.
          configSchema: (node as { config_schema?: Record<string, unknown> }).config_schema ?? undefined,
          interval: node.interval,
          path,
          inputPorts,
          outputPorts,
          description: cleanDoc(node.doc ?? node._doc ?? node.description),
          // Port TYPE schemas (from the process spec's _inputs/_outputs), shown
          // as separate sections in the inspector. Distinct from the wiring
          // (inputPortsSchema/outputPortsSchema = where each port connects).
          inputSchema: node._inputs ?? undefined,
          outputSchema: node._outputs ?? undefined,
          contract: node._contract ?? undefined,
          // Simulation method / model type (e.g. "Agent-Based Model", "ODE").
          // When set, ProcessNode shows it in place of the "draft process" line.
          method: (node as { _method?: string })._method
            ?? (node._contract as { method?: string } | undefined)?.method
            ?? (node.config as { method?: string } | undefined)?.method
            ?? undefined,
          // Optional illustrative figure for this node (data-URI image or inline
          // SVG string on the spec's `_figure`, or config._figure). Shown when the
          // Detail → Figures toggle allows it. See ProcessNode / StoreNode.
          figure: (node as any)._figure ?? (node.config as { _figure?: string } | undefined)?._figure ?? undefined,
          // A Composite Process (its inner model is itself a composite) — the
          // card gets a drill-in affordance; double-click opens the inner view.
          isCompositeProcess: node.is_composite_process === true,
          // Draft process: interface-only (ports + contract), NO update dynamics
          // yet. Badged on the card so `local:X` isn't read as a runnable impl.
          isDraft: node._draft === true
            || /draft/i.test(String((node._contract as { status?: string } | undefined)?.status ?? '')),
          // Extra schema data consumed by ProcessNode (as any cast in the component)
          ...(Object.keys(inputPortsSchema).length ? { inputPortsSchema, inputPortsTarget } : {}),
          ...(Object.keys(outputPortsSchema).length ? { outputPortsSchema, outputPortsTarget } : {}),
        } as ProcessNodeData;
      nodes.push({
        id,
        type: 'process',
        data: procData,
        position: { x: 0, y: 0 },
      });

      // Per-port store color: every wire, its port dot, and its store share the
      // store's hue so source→target is traceable by color even when wires are
      // long or cross (#1). Keyed on the SAME store id (pathKey) the store node
      // and the wire use, so the three always agree. Stamped onto the process
      // data for ProcessNode to paint the port dots + swatches.
      const portColors: Record<string, string> = {};

      // Wire edges: inputs arrive at this process node from store nodes.
      // Convention: input wires leave the store's LEFT side and enter the process's LEFT side.
      for (const [port, target] of Object.entries(node.inputs ?? {})) {
        const tid = pathKey(resolveWirePath(parentPath, target));
        const col = storeColor(tid);
        portColors[port] = col;
        edges.push({
          id: `${id}--in--${port}`,
          source: tid,
          target: id,
          type: 'floating',           // store end attaches at nearest circle point
          sourceHandle: 'left-out',   // store's left handle
          targetHandle: port,          // process's left input port
          label: port,
          animated: false,
          style: { stroke: col, strokeDasharray: '5,4', strokeWidth: 2.5 },  // per-store hue; dashed (inline so image export captures it)
          markerEnd: { ...WIRE_ARROW, color: col },   // arrow tinted to the store
          data: { edgeType: 'input', storeColor: col },
        });
      }
      // Wire edges: outputs leave this process node to store nodes.
      // Convention: output wires leave the process's RIGHT side and enter the store's RIGHT side.
      for (const [port, target] of Object.entries(node.outputs ?? {})) {
        const tid = pathKey(resolveWirePath(parentPath, target));
        const col = storeColor(tid);
        portColors[port] = col;
        edges.push({
          id: `${id}--out--${port}`,
          source: id,
          target: tid,
          type: 'floating',           // store end attaches at nearest circle point
          sourceHandle: port,          // process's right output port
          targetHandle: 'right-in',    // store's right handle
          label: port,
          animated: false,
          style: { stroke: col, strokeDasharray: '5,4', strokeWidth: 2.5 },  // per-store hue; dashed (inline so image export captures it)
          markerEnd: { ...WIRE_ARROW, color: col },   // arrow tinted to the store
          data: { edgeType: 'output', storeColor: col },
        });
      }
      // Attach the resolved per-port colors to the process card.
      (procData as ProcessNodeData & { portColors?: Record<string, string> }).portColors = portColors;
      return;
    }

    // A place-graph subtree — a `tree[node]` / `node` / `map[node]` store, or any
    // node carrying a Milner `_control` tag (or whose children do) — is NOT a
    // leaf: recurse into its child nodes so its (possibly runtime-changing)
    // topology renders. Falls through to the container logic below.
    const _tt = String((node as { _type?: unknown })._type ?? '');
    const isNodeTree = _tt.startsWith('tree[node') || _tt === 'node' || _tt.startsWith('map[node')
      || '_control' in node
      || Object.entries(node).some(([k, v]) => !k.startsWith('_')
           && v && typeof v === 'object' && '_control' in (v as object));

    if ('_type' in node && !isNodeTree) {
      // Typed store leaf (bigraph-schema typed value). For a typed array/map,
      // fold the element type (`_data`) into the label — `array[concentration]`
      // reads more precisely than a bare `array`.
      const _n = node as { _type?: unknown; _data?: unknown; _value?: unknown; _default?: unknown; _figure?: string };
      const _base = String(_n._type);
      const _elem = typeof _n._data === 'string' ? _n._data : undefined;
      const valueType = (_elem && (_base === 'array' || _base === 'map'))
        ? `${_base}[${_elem}]` : _base;
      // Prefer the store's actual value (`_value`) over its schema `_default`, so
      // an edited/Applied input manifests on the node (they can otherwise diverge).
      const _rawVal = _n._value != null ? _n._value : _n._default;
      nodes.push({
        id: pathKey(path),
        type: 'store',
        data: {
          label: path[path.length - 1] ?? '<root>',
          nodeType: 'store',
          value: _rawVal != null ? (displayValue(_rawVal) ?? undefined) : undefined,
          valueType,
          path,
          figure: _n._figure ?? undefined,
          storeColor: storeColor(pathKey(path)),
        } satisfies StoreNodeData,
        position: { x: 0, y: 0 },
      });
      return;
    }

    // Plain container — treat as a group store then recurse into children
    const id = pathKey(path);
    if (path.length > 0) {
      nodes.push({
        id,
        type: 'store',
        data: {
          label: path[path.length - 1],
          nodeType: 'store',
          isGroup: true,
          path,
          // An optional illustration for the container itself (e.g. a `cell` /
          // `tissue` group). `_figure` is metadata, NOT a child store.
          figure: (node as { _figure?: string })._figure ?? undefined,
          storeColor: storeColor(pathKey(path)),
        } satisfies StoreNodeData,
        position: { x: 0, y: 0 },
      });
    }

    // Milner `_control` nodes wrap their children in a `contents` region. Render
    // that wrapper TRANSPARENTLY — the children hang directly off this node (a
    // `cell` shows its `chromosome`, not a `contents` box), matching the logical
    // paths wires use. Any stray direct children are still kept.
    const _contents = (node as { contents?: unknown }).contents;
    const _hoist = '_control' in node && _contents
      && typeof _contents === 'object' && !Array.isArray(_contents);
    const childEntries: Array<[string, unknown]> = _hoist
      ? [
          ...Object.entries(_contents as Record<string, unknown>),
          ...Object.entries(node).filter(([k]) => k !== 'contents'),
        ]
      : Object.entries(node);

    for (const [key, child] of childEntries) {
      // `_`-prefixed keys are metadata (_figure, _contract, …), not child stores.
      if (key === DECLARED_EMIT_PATHS_KEY || key.startsWith('_')) continue;
      walk(child, [...path, key]);
    }

    // Add place edges from parent to each immediate child store
    if (path.length > 0) {
      for (const [key] of childEntries) {
        if (key === DECLARED_EMIT_PATHS_KEY || key.startsWith('_')) continue;
        const childId = pathKey([...path, key]);
        edges.push({
          id: `place--${id}--${childId}`,
          source: id,
          target: childId,
          sourceHandle: 'bottom-place',  // parent store's bottom handle
          targetHandle: 'top-place',     // child store's top handle
          type: 'place',                 // org-chart elbow (edges/PlaceEdge)
          animated: false,
          // Containment convention: a light, thin slate connector that reads as
          // structural scaffolding and recedes behind the node cards, rather than
          // a heavy dark bracket that competes with the node borders.
          style: { stroke: '#c2cbd6', strokeWidth: 2.75 },  // inline stroke for SVG export
          data: { edgeType: 'place' },
        });
      }
    }
  }

  walk(root, []);
  return { nodes, edges };
}
