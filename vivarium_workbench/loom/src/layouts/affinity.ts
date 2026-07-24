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
 * True when a wire target is pure relative-scope navigation — `'.'`, `'..'`,
 * or any dot-joined chain of those (e.g. `'../..'`, or the `'..'`-only string
 * convert.ts produces by `Array.join('.')`-ing a `['..']` target) — with no
 * real store name in it. Such a target carries no local store identity, so it
 * must never become a cluster key (real fixture case: `division`'s `agents`
 * port wires to `['..']`, which truncation alone turns into the meaningless
 * key `'.'`). Checked on the RAW target, before truncation, because
 * truncation is what manufactures the bogus non-empty `'.'` key in the first
 * place — by the time a key exists it looks superficially like a normal
 * short key, not obviously noise the way `isNoiseKey`'s prefix list is.
 */
function isPureNavigationTarget(target: string): boolean {
  return target.split('.').every((seg) => seg === '' || seg === '.' || seg === '..');
}

/** Store keys this process touches -> number of ports wired to each. */
export function storeKeysForProcess(node: Node, keyDepth = 2): Map<string, number> {
  const data = node.data as unknown as ProcessNodeData;
  const out = new Map<string, number>();
  const add = (schema: Record<string, string> | undefined) => {
    for (const target of Object.values(schema ?? {})) {
      if (!target) continue;
      const raw = String(target);
      if (isPureNavigationTarget(raw)) continue;
      const key = raw.split('.').slice(0, keyDepth).join('.');
      if (!key || isNoiseKey(key)) continue;
      out.set(key, (out.get(key) ?? 0) + 1);
    }
  };
  add(data.inputPortsSchema);
  add(data.outputPortsSchema);
  return out;
}
