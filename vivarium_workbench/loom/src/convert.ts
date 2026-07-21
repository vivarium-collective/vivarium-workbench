// src/convert.ts — composite-state → React Flow nodes + edges.
// Data shapes match ProcessNodeData and StoreNodeData from src/types.ts.

import { MarkerType } from '@xyflow/react';
import type { StoreNodeData, ProcessNodeData } from './types';

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
  data?: { edgeType: 'input' | 'output' | 'bidirectional' | 'place' };
};

/** Arrowhead used on directional wires (input + output edges).
 *  Place edges stay un-arrowed — they're nesting relationships, not flow. */
const WIRE_ARROW = { type: MarkerType.ArrowClosed, width: 14, height: 14, color: '#475569' };

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
      out.add(path.join('.'));
    }
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
        } satisfies StoreNodeData,
        position: { x: 0, y: 0 },
      });
      return;
    }

    if (node._type === 'process' || node._type === 'step') {
      const id = pathKey(path);
      const parentPath = path.slice(0, -1);  // wire targets are relative to this
      const inputPorts = Object.keys(node.inputs ?? {});
      const outputPorts = Object.keys(node.outputs ?? {});

      // Build inputPortsSchema / outputPortsSchema from wiring targets (informational)
      const inputPortsSchema: Record<string, string> = {};
      const outputPortsSchema: Record<string, string> = {};
      for (const [port, target] of Object.entries(node.inputs ?? {})) {
        inputPortsSchema[port] = Array.isArray(target) ? (target as string[]).join('.') : String(target);
      }
      for (const [port, target] of Object.entries(node.outputs ?? {})) {
        outputPortsSchema[port] = Array.isArray(target) ? (target as string[]).join('.') : String(target);
      }

      nodes.push({
        id,
        type: 'process',
        data: {
          label: path[path.length - 1] ?? '<root>',
          nodeType: 'process',
          processType: node._type ?? 'process',
          address: node.address ?? '',
          config: node.config ?? {},
          interval: node.interval,
          path,
          inputPorts,
          outputPorts,
          description: node.doc ?? node._doc ?? node.description ?? undefined,
          // Port TYPE schemas (from the process spec's _inputs/_outputs), shown
          // as separate sections in the inspector. Distinct from the wiring
          // (inputPortsSchema/outputPortsSchema = where each port connects).
          inputSchema: node._inputs ?? undefined,
          outputSchema: node._outputs ?? undefined,
          // Extra schema data consumed by ProcessNode (as any cast in the component)
          ...(Object.keys(inputPortsSchema).length ? { inputPortsSchema } : {}),
          ...(Object.keys(outputPortsSchema).length ? { outputPortsSchema } : {}),
        } as ProcessNodeData,
        position: { x: 0, y: 0 },
      });

      // Wire edges: inputs arrive at this process node from store nodes.
      // Convention: input wires leave the store's LEFT side and enter the process's LEFT side.
      for (const [port, target] of Object.entries(node.inputs ?? {})) {
        const tid = pathKey(resolveWirePath(parentPath, target));
        edges.push({
          id: `${id}--in--${port}`,
          source: tid,
          target: id,
          type: 'floating',           // store end attaches at nearest circle point
          sourceHandle: 'left-out',   // store's left handle
          targetHandle: port,          // process's left input port
          label: port,
          animated: false,
          style: { stroke: '#94a3b8', strokeDasharray: '5,5' },  // wire convention: dashed (inline stroke so image export captures it)
          markerEnd: WIRE_ARROW,       // arrow at the process's input port
          data: { edgeType: 'input' },
        });
      }
      // Wire edges: outputs leave this process node to store nodes.
      // Convention: output wires leave the process's RIGHT side and enter the store's RIGHT side.
      for (const [port, target] of Object.entries(node.outputs ?? {})) {
        const tid = pathKey(resolveWirePath(parentPath, target));
        edges.push({
          id: `${id}--out--${port}`,
          source: id,
          target: tid,
          type: 'floating',           // store end attaches at nearest circle point
          sourceHandle: port,          // process's right output port
          targetHandle: 'right-in',    // store's right handle
          label: port,
          animated: false,
          style: { stroke: '#94a3b8', strokeDasharray: '5,5' },  // wire convention: dashed (inline stroke so image export captures it)
          markerEnd: WIRE_ARROW,       // arrow at the store's incoming side
          data: { edgeType: 'output' },
        });
      }
      return;
    }

    if ('_type' in node) {
      // Typed store leaf (bigraph-schema typed value)
      nodes.push({
        id: pathKey(path),
        type: 'store',
        data: {
          label: path[path.length - 1] ?? '<root>',
          nodeType: 'store',
          value: node._default != null ? (displayValue(node._default) ?? undefined) : undefined,
          valueType: String(node._type),
          path,
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
        } satisfies StoreNodeData,
        position: { x: 0, y: 0 },
      });
    }

    for (const [key, child] of Object.entries(node)) {
      if (key === DECLARED_EMIT_PATHS_KEY) continue;  // metadata, not a store
      walk(child, [...path, key]);
    }

    // Add place edges from parent to each immediate child store
    if (path.length > 0) {
      for (const key of Object.keys(node)) {
        if (key === DECLARED_EMIT_PATHS_KEY) continue;
        const childId = pathKey([...path, key]);
        edges.push({
          id: `place--${id}--${childId}`,
          source: id,
          target: childId,
          sourceHandle: 'bottom-place',  // parent store's bottom handle
          targetHandle: 'top-place',     // child store's top handle
          animated: false,
          style: { stroke: '#64748b', strokeWidth: 2.5 },  // place convention: thick solid (inline stroke for export)
          data: { edgeType: 'place' },
        });
      }
    }
  }

  walk(root, []);
  return { nodes, edges };
}
