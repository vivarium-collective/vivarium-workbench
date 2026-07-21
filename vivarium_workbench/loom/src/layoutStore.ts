// src/layoutStore.ts — per-composite node-position cache in localStorage.
//
// Goals:
//   1. Manual drags survive page reloads (and tab swaps).
//   2. Collapsing/expanding a store does NOT re-layout already-positioned nodes.
//   3. Only newly-visible nodes need fresh auto-layout positions.
//
// Stored shape, one key per composite id:
//   localStorage["bigraph-loom:layout:<composite-id>"] = JSON {
//     [nodeId]: { x: number, y: number }
//   }
//
// Out of scope: server-side persistence, cross-machine sync, multi-user.

import type { Node } from '@xyflow/react';

const KEY_PREFIX = 'bigraph-loom:layout:';

export type LayoutPositions = Record<string, { x: number; y: number }>;

function keyFor(compositeId: string | null | undefined): string | null {
  if (!compositeId) return null;
  return KEY_PREFIX + compositeId;
}

/** Read saved positions for a composite. Returns {} if none or on parse error. */
export function loadLayout(compositeId: string | null | undefined): LayoutPositions {
  const k = keyFor(compositeId);
  if (!k) return {};
  try {
    const raw = window.localStorage.getItem(k);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return {};
    return parsed as LayoutPositions;
  } catch {
    return {};
  }
}

/** Overwrite saved positions for a composite. */
export function saveLayout(compositeId: string | null | undefined, positions: LayoutPositions): void {
  const k = keyFor(compositeId);
  if (!k) return;
  try {
    window.localStorage.setItem(k, JSON.stringify(positions));
  } catch {
    // Storage quota / disabled cookies: persistence is a nice-to-have, never fail loud.
  }
}

/** Wipe the saved layout for one composite (used by the "Reset layout" button). */
export function clearLayout(compositeId: string | null | undefined): void {
  const k = keyFor(compositeId);
  if (!k) return;
  try {
    window.localStorage.removeItem(k);
  } catch { /* see saveLayout */ }
}

/** Extract {id → position} from a node list. Used to snapshot for persistence. */
export function positionsFromNodes(nodes: Node[]): LayoutPositions {
  const out: LayoutPositions = {};
  for (const n of nodes) {
    if (n.position) out[n.id] = { x: n.position.x, y: n.position.y };
  }
  return out;
}

/**
 * Pin nodes whose IDs have a saved position; everything else stays at the
 * caller-supplied (auto-layout) position. Returns a new array — does NOT mutate.
 */
export function applySavedPositions(nodes: Node[], saved: LayoutPositions): Node[] {
  return nodes.map((n) => {
    const p = saved[n.id];
    return p ? { ...n, position: { x: p.x, y: p.y } } : n;
  });
}

/** Tiny debounce; trailing only. Generic on argument tuple. */
export function debounce<A extends unknown[]>(
  fn: (...args: A) => void,
  ms: number,
): (...args: A) => void {
  let t: ReturnType<typeof setTimeout> | null = null;
  return (...args: A) => {
    if (t) clearTimeout(t);
    t = setTimeout(() => {
      t = null;
      fn(...args);
    }, ms);
  };
}
