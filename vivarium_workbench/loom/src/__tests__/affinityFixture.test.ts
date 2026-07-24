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
    // `polypeptide-elongation` is deliberately absent here — see the
    // characterization test below for the measured reason.
    for (const stem of ['transcript-elongation', 'rna-degradation']) {
      const req = ownerOf(`${stem}_requester`);
      expect(req).toBeDefined();
      expect(ownerOf(`${stem}_evolver`)).toBe(req);
    }
  });

  it('CHARACTERIZES: the port-multiplicity tiebreak splits the polypeptide-elongation pair', () => {
    // Measured behavior of the specified rule, recorded rather than papered over.
    // Both halves touch exactly {environment, boundary, listeners,
    // unique.active_ribosome, bulk}; `boundary` and `unique.active_ribosome`
    // tie at df=6, so the tiebreak decides — and the requester, which only
    // READS the ribosome (1 port) while the evolver reads AND writes it
    // (2 ports), falls through to the lexical tiebreak and lands on `boundary`.
    // The 1-vs-2 port difference is an artifact of the requester/evolver split
    // itself, not biology, so this split is undesirable but is what the
    // prescribed scoring rule produces. Changing the tiebreak (e.g. preferring
    // the deeper store path) reunites the pair under `unique.active_ribosome`
    // at the same 10-cluster / 4-singleton quality — a deliberate decision to
    // be made upstream, not silently here. If you change the tiebreak, delete
    // this test rather than editing it.
    expect(ownerOf('ecoli-polypeptide-elongation_requester')).toBe('boundary');
    expect(ownerOf('ecoli-polypeptide-elongation_evolver')).toBe('unique.active_ribosome');
  });
});
