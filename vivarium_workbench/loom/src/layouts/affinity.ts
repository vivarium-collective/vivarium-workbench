// src/layouts/affinity.ts — group processes by the stores they wire into.
//
// Reads inputPortsSchema/outputPortsSchema off process nodes (attached by
// convert.ts), which are already wire paths relative to the process's
// parent store. Pure: no React, no DOM, no React Flow beyond the Node type.

import type { Node } from '@xyflow/react';
import type { ProcessNodeData } from '../types';

/** Stores that are process-private plumbing, never a meaningful group key.
 *  The store-side counterpart of convert.ts's defaultHiddenIds. */
export const NOISE_KEY_PREFIXES = [
  '_layer_token', 'process.', 'process_state.', 'request',
  'next_update_time', 'pinned_flux_targets', 'timestep',
  'global_time', 'allocate.', '_',
];

export function isNoiseKey(key: string): boolean {
  return NOISE_KEY_PREFIXES.some((p) => key === p || key.startsWith(p));
}

/** Bookkeeping processes, matching what defaultHiddenIds already hides. */
export function isBookkeepingProcess(label: string): boolean {
  const n = (label || '').toLowerCase();
  return n.startsWith('unique_update') || n.startsWith('allocator') || n.includes('listener');
}

/** Store keys this process touches -> number of ports wired to each. */
export function storeKeysForProcess(node: Node, keyDepth = 2): Map<string, number> {
  const data = node.data as unknown as ProcessNodeData;
  const out = new Map<string, number>();
  const add = (schema: Record<string, string> | undefined) => {
    for (const target of Object.values(schema ?? {})) {
      if (!target) continue;
      const key = String(target).split('.').slice(0, keyDepth).join('.');
      if (!key || isNoiseKey(key)) continue;
      out.set(key, (out.get(key) ?? 0) + 1);
    }
  };
  add(data.inputPortsSchema);
  add(data.outputPortsSchema);
  return out;
}
