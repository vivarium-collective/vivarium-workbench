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

/** Bookkeeping processes, matching what convert.ts's defaultHiddenIds hides
 *  (same predicate: case-sensitive, `includes('unique_update')` rather than
 *  `startsWith`, so a mid-label id like `foo_unique_update_3` still hides). */
export function isBookkeepingProcess(label: string): boolean {
  const n = label || '';
  return n.includes('unique_update') || n.startsWith('allocator') || n.includes('listener');
}

/**
 * Extracts the leading run of REAL (non-navigation) path segments from a wire
 * target, up to `keyDepth` of them. Leading empty / `'.'` / `'..'` segments —
 * the relative-scope navigation prefix — are discarded first, because
 * `Array.join('.')` (convert.ts's lossy encoding of a wire path array) mixes
 * navigation and real segments into one dot-delimited string with no
 * separator between them: `['..','bulk'].join('.')` is `'...bulk'`, and a
 * naive `split('.').slice(0, keyDepth)` counts the empty navigation segments
 * against the depth budget, truncating away the real segment entirely (real
 * fixture cases: `division`'s `agents` port wires to `['..']`; ordinary
 * sibling/boundary wiring like `['..','boundary','external']` has the same
 * shape). Returns `[]` when nothing real remains (bare `'.'`, `'..'`,
 * `'../..'`, or `''`) — such a target carries no local store identity and
 * must be skipped by the caller, not turned into a key.
 */
function realPathSegments(target: string, keyDepth: number): string[] {
  const segments = target.split('.').filter((seg) => seg !== '' && seg !== '.' && seg !== '..');
  return segments.slice(0, keyDepth);
}

/** Store keys this process touches -> number of ports wired to each. */
export function storeKeysForProcess(node: Node, keyDepth = 2): Map<string, number> {
  const data = node.data as unknown as ProcessNodeData;
  const out = new Map<string, number>();
  const add = (schema: Record<string, string> | undefined) => {
    for (const target of Object.values(schema ?? {})) {
      if (!target) continue;
      const raw = String(target);
      const segments = realPathSegments(raw, keyDepth);
      if (segments.length === 0) continue;
      const key = segments.join('.');
      if (!key || isNoiseKey(key)) continue;
      out.set(key, (out.get(key) ?? 0) + 1);
    }
  };
  add(data.inputPortsSchema);
  add(data.outputPortsSchema);
  return out;
}
