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
 * Top-level store keys of a composite state — every key whose node is not a
 * process/step. Mirrors the dashboard's `all_store_paths`; used to seed the
 * View tab's emit selection so all states emit by default.
 */
export function topLevelStorePaths(state: any): string[] {
  const root = state?.state ?? state ?? {};
  return Object.entries(root)
    .filter(([, v]) => {
      if (v && typeof v === 'object' && !Array.isArray(v)) {
        const t = (v as { _type?: string })._type;
        return t !== 'process' && t !== 'step';
      }
      return true;  // scalar leaf — a store
    })
    .map(([k]) => k);
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
          value: node == null ? null : String(node),
          valueType: typeof node,
          path,
        } satisfies StoreNodeData,
        position: { x: 0, y: 0 },
      });
      return;
    }

    if (node._type === 'process' || node._type === 'step') {
      const id = pathKey(path);
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
          // Extra schema data consumed by ProcessNode (as any cast in the component)
          ...(Object.keys(inputPortsSchema).length ? { inputPortsSchema } : {}),
          ...(Object.keys(outputPortsSchema).length ? { outputPortsSchema } : {}),
        } as ProcessNodeData,
        position: { x: 0, y: 0 },
      });

      // Wire edges: inputs arrive at this process node from store nodes.
      // Convention: input wires leave the store's LEFT side and enter the process's LEFT side.
      for (const [port, target] of Object.entries(node.inputs ?? {})) {
        const tid = Array.isArray(target) ? (target as string[]).join('.') : String(target);
        edges.push({
          id: `${id}--in--${port}`,
          source: tid,
          target: id,
          type: 'floating',           // store end attaches at nearest circle point
          sourceHandle: 'left-out',   // store's left handle
          targetHandle: port,          // process's left input port
          label: port,
          animated: false,
          style: { strokeDasharray: '5,5' },  // wire convention: dashed
          markerEnd: WIRE_ARROW,       // arrow at the process's input port
          data: { edgeType: 'input' },
        });
      }
      // Wire edges: outputs leave this process node to store nodes.
      // Convention: output wires leave the process's RIGHT side and enter the store's RIGHT side.
      for (const [port, target] of Object.entries(node.outputs ?? {})) {
        const tid = Array.isArray(target) ? (target as string[]).join('.') : String(target);
        edges.push({
          id: `${id}--out--${port}`,
          source: id,
          target: tid,
          type: 'floating',           // store end attaches at nearest circle point
          sourceHandle: port,          // process's right output port
          targetHandle: 'right-in',    // store's right handle
          label: port,
          animated: false,
          style: { strokeDasharray: '5,5' },  // wire convention: dashed
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
          value: node._default != null ? String(node._default) : undefined,
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
      walk(child, [...path, key]);
    }

    // Add place edges from parent to each immediate child store
    if (path.length > 0) {
      for (const key of Object.keys(node)) {
        const childId = pathKey([...path, key]);
        edges.push({
          id: `place--${id}--${childId}`,
          source: id,
          target: childId,
          sourceHandle: 'bottom-place',  // parent store's bottom handle
          targetHandle: 'top-place',     // child store's top handle
          animated: false,
          style: { strokeWidth: 2.5 },  // place convention: thick solid
          data: { edgeType: 'place' },
        });
      }
    }
  }

  walk(root, []);
  return { nodes, edges };
}
