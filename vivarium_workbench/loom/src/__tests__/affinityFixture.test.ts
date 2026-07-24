// Quality guard: run the real clustering over the real v2ecoli baseline
// composite and assert the OUTPUT IS READABLE — a handful of recognizable
// groups, not singleton soup. An earlier TF-IDF prototype scored 36 clusters
// for 46 processes (27 singletons, keyed on junk like `_layer_token_7`) while
// every unit test stayed green; this file is what catches that class of
// regression. If it fails, print the grouping and re-tune `hubFraction` —
// never loosen the thresholds.
import { describe, it, expect } from 'vitest';
import type { Node } from '@xyflow/react';
import { stateToReactFlow } from '../convert';
import { clusterProcesses } from '../layouts/affinity';
import fixture from './fixtures/v2ecoli-baseline.json';

const { nodes } = stateToReactFlow((fixture as any).state);
const result = clusterProcesses(nodes as unknown as Node[], { hubFraction: 0.30 });
const sizes = result.clusters.map((c) => c.processIds.length);
const total = sizes.reduce((a, b) => a + b, 0);

describe('affinity clustering on the real v2ecoli baseline', () => {
  it('finds the expected hub stores', () => {
    expect(result.hubs).toEqual(expect.arrayContaining(['bulk', 'listeners']));
  });

  it('pins the exact hub set — guards the hubFraction rounding boundary', () => {
    // `unique.RNA` sits at df=8 of n=27 (29.6%), one process below the
    // nominal 30% cut, and is a hub only because `hubCut` rounds
    // `0.30 * 27 = 8.1` DOWN to 8 (see AffinityOptions.hubFraction's doc
    // comment). That makes this boundary fragile: a change to the rounding,
    // the floor, or hubFraction's default would silently reshuffle the
    // fixture's clustering rather than fail loudly. Pin the exact set so any
    // such change is caught here.
    expect(result.hubs).toEqual(['bulk', 'environment', 'listeners', 'unique.RNA']);
  });

  it('produces a readable number of clusters, not singleton soup', () => {
    expect(result.clusters.length).toBeGreaterThanOrEqual(5);
    expect(result.clusters.length).toBeLessThanOrEqual(14);
    expect(sizes.filter((s) => s === 1).length / result.clusters.length).toBeLessThan(0.5);
  });

  it('assigns every non-bookkeeping process exactly once', () => {
    const ids = result.clusters.flatMap((c) => c.processIds);
    expect(new Set(ids).size).toBe(ids.length);
    expect(total).toBeGreaterThan(20);
  });

  it('keys clusters on recognizable biological stores', () => {
    const keys = result.clusters.map((c) => c.key);
    expect(keys).toEqual(expect.arrayContaining([
      'unique.active_ribosome', 'boundary', 'unique.promoter',
      'unique.active_RNAP', 'unique.full_chromosome',
    ]));
    // No cluster is keyed on process-private plumbing.
    for (const k of keys) expect(k).not.toMatch(/^_layer_token/);
  });

  /** Which cluster owns the process whose id ends with `suffix`. */
  const ownerOf = (suffix: string) =>
    result.clusters.find((c) => c.processIds.some((id) => id.endsWith(suffix)))?.key;

  it('keeps requester/evolver partition pairs together', () => {
    // Path-depth tiebreak (preferred over port multiplicity — see
    // affinity.ts's clusterProcesses doc comment) reunites
    // `polypeptide-elongation`'s pair under `unique.active_ribosome`
    // alongside the rest of the translation cluster, so all three
    // requester/evolver stems now hold the property outright.
    for (const stem of ['transcript-elongation', 'polypeptide-elongation', 'rna-degradation']) {
      const req = ownerOf(`${stem}_requester`);
      expect(req).toBeDefined();
      expect(ownerOf(`${stem}_evolver`)).toBe(req);
    }
  });
});
