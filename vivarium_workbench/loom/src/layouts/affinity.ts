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

export interface AffinityOptions {
  /**
   * A store cannot be a cluster key once it is touched by at least
   * `Math.max(3, Math.round(hubFraction * n))` processes — NOT the raw
   * `hubFraction * n` the name suggests. The `Math.round` means the true
   * cutoff can sit up to half a process below the nominal fraction (at
   * n=27, hubFraction=0.30 asks for df >= 8.1 but rounds the cut down to 8),
   * and the `Math.max(3, …)` floor keeps tiny graphs from being unable to
   * form a hub at all. This is intentional and matches a validated
   * prototype — do not "fix" the rounding (switching to `Math.ceil` was
   * measured to reshuffle the real fixture's clustering substantially,
   * dropping `unique.active_RNAP` as a cluster entirely). On the real
   * v2ecoli baseline (n=27) `unique.RNA` sits at df=8 — one process below
   * the naive 30% threshold — so it becomes a hub only because of the
   * rounding; the grouping is genuinely sensitive to this boundary.
   */
  hubFraction?: number;
  /** A process touching more than this many distinct non-hub keys is
   *  cross-cutting and is not forced into any one cluster. */
  hubProcessKeyLimit?: number;
  /**
   * How many leading path segments identify a store (default 2, e.g.
   * `unique.RNA` rather than `unique.RNA.foo`). Aliasing hazard: a
   * depth-1 store name (e.g. `unique`, if that ever existed as a store in
   * its own right) is indistinguishable from the depth-`keyDepth`
   * truncation of any deeper path rooted at the same first segment — both
   * collapse to the same string key and are counted as one store.
   */
  keyDepth?: number;
}

export interface Cluster {
  key: string;
  label: string;
  processIds: string[];
}

export interface AffinityResult {
  clusters: Cluster[];
  hubs: string[];
}

/** Terminal bucket for processes whose every store is a hub (or which touch no
 *  store at all, e.g. a clock wired only to `global_time`/`timestep`). */
export const HUB_ONLY_KEY = '~hub-only';
/** Terminal bucket for processes spread across so many stores that no single
 *  one identifies them. */
export const CROSS_CUTTING_KEY = '~cross-cutting';

/**
 * Group processes into named clusters by the stores they wire into.
 *
 * The rule: **each process joins the most widely SHARED non-hub store it
 * touches.** Hubs (stores nearly everything touches — `bulk`, `listeners`) are
 * excluded as keys because they group nothing; rare stores are excluded by
 * construction because "most widely shared" prefers the popular one.
 *
 * Two alternatives were prototyped against the real v2ecoli baseline and
 * rejected on measured results, so do not swap this rule out casually:
 *  - TF-IDF distinctiveness (`ports × log(n/df)`): 36 clusters for 46
 *    processes, 27 singletons keyed on junk like `_layer_token_7`. Rare stores
 *    are process-PRIVATE, not distinctive.
 *  - Jaccard agglomerative clustering: 12-14 clusters, 7-8 singletons, and its
 *    largest cluster shared no distinctive store so it could not be labeled.
 *
 * On a tie in shared count, prefer the DEEPER store path (more dot-separated
 * segments), then break remaining ties lexically. A deeper key names a more
 * specific store, and specificity is what makes a cluster meaningful —
 * `unique.active_ribosome` says more about what a process does than
 * `boundary`. Port multiplicity (how many ports a process wires to a
 * candidate key) was tried first and rejected: it split the
 * `polypeptide-elongation` requester/evolver pair apart (`boundary` vs
 * `unique.active_ribosome`, tied at df=6) purely because the requester only
 * READS the ribosome store (1 port) while the evolver reads AND writes it (2
 * ports) — an artifact of the requester/evolver partition scheme, not
 * biology. Depth reunites that pair (and the other partition pairs) at
 * identical cluster quality (10 clusters / 4 singletons on the real
 * fixture). Do not restore port multiplicity as the second tiebreak.
 *
 * Deterministic: every tie is broken to a total order, and both the cluster
 * list and each member list are sorted.
 */
export function clusterProcesses(nodes: Node[], opts: AffinityOptions = {}): AffinityResult {
  const { hubFraction = 0.30, hubProcessKeyLimit = 8, keyDepth = 2 } = opts;

  const procs = nodes.filter(
    (n) => n.type === 'process'
      && !isBookkeepingProcess(String((n.data as { label?: unknown })?.label ?? '')),
  );
  const n = procs.length;
  if (n === 0) return { clusters: [], hubs: [] };

  // Per-process key maps, plus document frequency (how many processes touch each key).
  const touches = new Map<string, Map<string, number>>();
  const df = new Map<string, number>();
  for (const p of procs) {
    const keys = storeKeysForProcess(p, keyDepth);
    touches.set(p.id, keys);
    for (const k of keys.keys()) df.set(k, (df.get(k) ?? 0) + 1);
  }

  // Floor of 3: in a tiny graph "most processes" is a couple of processes, and
  // calling that a hub would erase the only groupings there are.
  const hubCut = Math.max(3, Math.round(hubFraction * n));
  const hubs = [...df.entries()].filter(([, c]) => c >= hubCut).map(([k]) => k).sort();
  const hubSet = new Set(hubs);

  const grouped = new Map<string, string[]>();
  const push = (key: string, id: string) => {
    const list = grouped.get(key);
    if (list) list.push(id); else grouped.set(key, [id]);
  };

  for (const p of procs) {
    const keys = touches.get(p.id)!;
    const candidates = [...keys.entries()].filter(([k]) => !hubSet.has(k));
    // No non-hub key at all — including the no-key-whatsoever case.
    if (candidates.length === 0) { push(HUB_ONLY_KEY, p.id); continue; }
    if (candidates.length > hubProcessKeyLimit) { push(CROSS_CUTTING_KEY, p.id); continue; }
    // Most widely SHARED non-hub key wins; ties break on path depth (deeper
    // — more dot-separated segments — is more specific), then lexically so
    // the result is stable.
    const depth = (key: string) => key.split('.').length;
    candidates.sort((a, b) =>
      (df.get(b[0])! - df.get(a[0])!) || (depth(b[0]) - depth(a[0])) || a[0].localeCompare(b[0]));
    push(candidates[0][0], p.id);
  }

  const hubLabel = hubs.length ? `${hubs.slice(0, 3).join(' · ')} only` : 'ungrouped';
  const clusters: Cluster[] = [...grouped.entries()]
    .map(([key, ids]) => ({
      key,
      label: key === HUB_ONLY_KEY ? hubLabel : key === CROSS_CUTTING_KEY ? 'cross-cutting' : key,
      processIds: ids.sort(),
    }))
    .sort((a, b) => {
      const rank = (k: string) => (k === CROSS_CUTTING_KEY ? 1 : k === HUB_ONLY_KEY ? 2 : 0);
      return (rank(a.key) - rank(b.key))
        || (b.processIds.length - a.processIds.length)
        || a.key.localeCompare(b.key);
    });

  return { clusters, hubs };
}
