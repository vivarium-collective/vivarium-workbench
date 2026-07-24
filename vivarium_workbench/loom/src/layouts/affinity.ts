// src/layouts/affinity.ts — group processes by the stores they wire into.
//
// Reads inputPortsTarget/outputPortsTarget off process nodes (attached by
// convert.ts): the RESOLVED ABSOLUTE store path each port wires to, with
// relative `.`/`..` navigation already applied by convert.ts's resolveWirePath.
// The sibling inputPortsSchema/outputPortsSchema fields are the RAW targets
// joined with '.', which is lossy and un-parseable (`['..','bulk']` joins to
// `'...bulk'`, `['a','..','b']` to `'a....b'`) — they are display strings and
// must never be used here.
// Pure: no React, no DOM, no React Flow beyond the Node type.

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
 * Re-expresses a RESOLVED ABSOLUTE store path relative to `parentPath`, the
 * process's own parent store — the scope its wiring is written against.
 *
 * Returns `null` when the target does not lie strictly under that parent, i.e.
 * the wire navigated out of the process's own scope (`['..']` → the parent's
 * parent) or landed on the parent store itself (`['bulk','..']` → the parent).
 * Neither carries a local store identity, so the caller must SKIP such a port
 * rather than invent a key for it — the real fixture case is v2ecoli-baseline's
 * `division` process, whose `agents` port wires to `['..']`.
 */
function segmentsUnderParent(parentPath: string[], absolute: string): string[] | null {
  const abs = absolute.split('.').filter((seg) => seg !== '');
  if (abs.length <= parentPath.length) return null;      // the parent itself, or above it
  for (let i = 0; i < parentPath.length; i++) {
    if (abs[i] !== parentPath[i]) return null;           // a sibling branch, not ours
  }
  return abs.slice(parentPath.length);
}

/** Store keys this process touches -> number of ports wired to each. */
export function storeKeysForProcess(node: Node, keyDepth = 2): Map<string, number> {
  const data = node.data as unknown as ProcessNodeData;
  // data.path includes the process's own name; its wiring scope is the parent.
  const parentPath = (data.path ?? []).slice(0, -1);
  const out = new Map<string, number>();
  const add = (targets: Record<string, string> | undefined) => {
    for (const absolute of Object.values(targets ?? {})) {
      if (absolute == null) continue;
      const segments = segmentsUnderParent(parentPath, String(absolute));
      if (!segments || segments.length === 0) continue;
      const key = segments.slice(0, keyDepth).join('.');
      if (!key || isNoiseKey(key)) continue;
      out.set(key, (out.get(key) ?? 0) + 1);
    }
  };
  add(data.inputPortsTarget);
  add(data.outputPortsTarget);
  return out;
}
