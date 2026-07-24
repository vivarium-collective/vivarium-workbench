import { describe, it, expect } from 'vitest';
import type { Node } from '@xyflow/react';
import {
  isNoiseKey,
  storeKeysForProcess,
  isBookkeepingProcess,
  clusterProcesses,
} from '../layouts/affinity';
import { stateToReactFlow } from '../convert';

/**
 * Build a process node the way the app really does — by running the real
 * `stateToReactFlow` over a minimal composite state — so the node carries
 * EXACTLY the port fields convert.ts attaches (raw joined `*PortsSchema` plus
 * the resolved absolute `*PortsTarget`). Wire targets are given as ARRAYS,
 * their true form; the joined string is a lossy display encoding and must
 * never be the input to clustering.
 *
 * The process lands at path `['agents','0',label]`, so its parent store — the
 * scope wire targets are relative to — is `agents.0`.
 */
function proc(
  label: string,
  inputs: Record<string, string[]>,
  outputs: Record<string, string[]> = {},
): Node {
  const state = {
    agents: {
      '0': {
        [label]: { _type: 'process', address: 'local:X', config: {}, inputs, outputs },
      },
    },
  };
  const { nodes } = stateToReactFlow(state);
  const found = nodes.find((n) => n.id === `agents.0.${label}`);
  if (!found) throw new Error(`test fixture did not produce a process node for ${label}`);
  return found as unknown as Node;
}

describe('isNoiseKey', () => {
  it('rejects process-private bookkeeping stores', () => {
    for (const k of ['_layer_token_7', 'next_update_time', 'process.foo',
                     'process_state.dnaa_hydrolysis', 'request', 'timestep',
                     'global_time', 'pinned_flux_targets', 'allocate.ecoli-x']) {
      expect(isNoiseKey(k)).toBe(true);
    }
  });

  it('keeps real biological stores', () => {
    for (const k of ['bulk', 'listeners', 'unique.RNA', 'unique.active_ribosome',
                     'boundary', 'environment', 'unique.promoter']) {
      expect(isNoiseKey(k)).toBe(false);
    }
  });
});

describe('convert.ts port target resolution', () => {
  it('attaches BOTH the raw joined schema and the resolved absolute target', () => {
    const node = proc('p', { a: ['unique', 'RNA'] }, { b: ['..', 'bulk'] });
    const data = node.data as any;
    // Raw joined form is unchanged — it feeds the port tooltips + Inspector.
    expect(data.inputPortsSchema).toEqual({ a: 'unique.RNA' });
    expect(data.outputPortsSchema).toEqual({ b: '...bulk' });
    // Resolved absolute form is new, and is what clustering consumes.
    expect(data.inputPortsTarget).toEqual({ a: 'agents.0.unique.RNA' });
    expect(data.outputPortsTarget).toEqual({ b: 'agents.bulk' });
  });

  it('resolves interior ".." with pop semantics, not by deleting the token', () => {
    const node = proc('p', { a: ['unique', '..', 'bulk'], b: ['bulk', '..'] });
    const data = node.data as any;
    expect(data.inputPortsTarget).toEqual({ a: 'agents.0.bulk', b: 'agents.0' });
  });
});

describe('storeKeysForProcess', () => {
  it('truncates keys to depth 2 and counts port multiplicity', () => {
    const keys = storeKeysForProcess(
      proc('p', { a: ['unique', 'RNA', 'foo'], b: ['unique', 'RNA', 'bar'], c: ['bulk'] }),
    );
    expect(keys.get('unique.RNA')).toBe(2);
    expect(keys.get('bulk')).toBe(1);
  });

  it('merges input and output ports', () => {
    const keys = storeKeysForProcess(
      proc('p', { a: ['bulk'] }, { b: ['bulk'], c: ['listeners'] }),
    );
    expect(keys.get('bulk')).toBe(2);
    expect(keys.get('listeners')).toBe(1);
  });

  it('drops noise keys entirely', () => {
    const keys = storeKeysForProcess(
      proc('p', { a: ['bulk'], b: ['_layer_token_3'], c: ['timestep'] }),
    );
    expect([...keys.keys()]).toEqual(['bulk']);
  });
});

describe('storeKeysForProcess — relative-navigation wire targets', () => {
  // Process parent scope is `agents.0` throughout (see `proc`).

  it('keys a plain in-scope target by its own name', () => {
    expect([...storeKeysForProcess(proc('p', { a: ['bulk'] }))]).toEqual([['bulk', 1]]);
  });

  it('keys a nested in-scope target at full depth', () => {
    expect([...storeKeysForProcess(proc('p', { a: ['unique', 'RNA'] }))])
      .toEqual([['unique.RNA', 1]]);
  });

  it('truncates a deeper in-scope target to keyDepth segments', () => {
    expect([...storeKeysForProcess(proc('p', { a: ['unique', 'RNA', 'foo'] }))])
      .toEqual([['unique.RNA', 1]]);
  });

  it('never yields the meaningless "." key for a bare ".." (parent-scope) target', () => {
    // Real fixture case: v2ecoli-baseline's `division` process wires its
    // `agents` output port to `['..']`. That resolves to `agents` — OUTSIDE
    // the process's own parent store — so it has no local store identity and
    // must not surface as a cluster key at all.
    const keys = storeKeysForProcess(proc('division', {}, { agents: ['..'] }));
    expect(keys.has('.')).toBe(false);
    expect(keys.size).toBe(0);
  });

  it('skips an up-then-into-a-sibling target (outside the process scope)', () => {
    // ['..','bulk'] from parent `agents.0` resolves to `agents.bulk`, which is
    // NOT under `agents.0`. It is a different store from `agents.0.bulk` and
    // must not be conflated with it.
    const keys = storeKeysForProcess(proc('p', {}, { a: ['..', 'bulk'] }));
    expect(keys.has('bulk')).toBe(false);
    expect(keys.size).toBe(0);
  });

  it('skips a multi-segment out-of-scope boundary target', () => {
    const keys = storeKeysForProcess(proc('p', {}, { a: ['..', 'boundary', 'external'] }));
    expect(keys.has('boundary.external')).toBe(false);
    expect(keys.size).toBe(0);
  });

  it('skips a target that walks above the composite root', () => {
    const keys = storeKeysForProcess(proc('p', {}, { a: ['..', '..', 'x'] }));
    expect(keys.size).toBe(0);
  });

  it('resolves a leading "." to the same scope', () => {
    const keys = storeKeysForProcess(proc('p', {}, { a: ['.', 'bulk'] }));
    expect(keys.has('.')).toBe(false);
    expect([...keys.keys()]).toEqual(['bulk']);
  });

  it('pops on an interior ".." instead of dropping the token', () => {
    // ['unique','..','bulk'] resolves to `agents.0.bulk` — the SAME store as
    // ['bulk']. The previous string-munging fix produced a phantom
    // 'unique.bulk' key here.
    const keys = storeKeysForProcess(proc('p', { a: ['unique', '..', 'bulk'], b: ['bulk'] }));
    expect(keys.has('unique.bulk')).toBe(false);
    expect([...keys]).toEqual([['bulk', 2]]);
  });

  it('skips a target that navigates in and straight back out to the parent', () => {
    // ['bulk','..'] resolves to `agents.0` — the parent store itself, not a
    // store the process is wired into. The previous fix yielded a phantom
    // 'bulk' key here.
    const keys = storeKeysForProcess(proc('p', { a: ['bulk', '..'] }));
    expect(keys.has('bulk')).toBe(false);
    expect(keys.size).toBe(0);
  });

  it('drops pure navigation targets while keeping real keys from the same process', () => {
    const keys = storeKeysForProcess(
      proc('division', { bulk: ['bulk'] }, { agents: ['..'], environment: ['.'] }),
    );
    expect(keys.has('.')).toBe(false);
    expect(keys.has('')).toBe(false);
    expect(keys.get('bulk')).toBe(1);
    expect([...keys.keys()]).toEqual(['bulk']);
  });

  it('never emits a key containing an empty or dot-only segment', () => {
    const keys = storeKeysForProcess(
      proc('p', { a: ['..'], b: ['.'], c: ['..', '..'], d: ['bulk'] }),
    );
    for (const k of keys.keys()) {
      expect(k.split('.').every((seg) => seg !== '' && seg !== '.' && seg !== '..')).toBe(true);
    }
    expect([...keys.keys()]).toEqual(['bulk']);
  });
});

describe('isBookkeepingProcess', () => {
  it('matches what defaultHiddenIds already hides', () => {
    expect(isBookkeepingProcess('unique_update_4')).toBe(true);
    expect(isBookkeepingProcess('allocator_2')).toBe(true);
    expect(isBookkeepingProcess('rnap_data_listener')).toBe(true);
    expect(isBookkeepingProcess('ecoli-transcript-initiation')).toBe(false);
  });
});

// `proc` places every process at `agents.0.<label>`, so React Flow node ids —
// which are what clusterProcesses reports — carry that prefix.
const pid = (label: string) => `agents.0.${label}`;
const pids = (...labels: string[]) => labels.map(pid).sort();

describe('clusterProcesses', () => {
  it('groups processes sharing a mid-frequency store', () => {
    const nodes = [
      proc('a', { x: ['unique', 'RNA'], h: ['bulk'] }),
      proc('b', { x: ['unique', 'RNA'], h: ['bulk'] }),
      proc('c', { y: ['unique', 'promoter'], h: ['bulk'] }),
      proc('d', { y: ['unique', 'promoter'], h: ['bulk'] }),
    ];
    const { clusters } = clusterProcesses(nodes, { hubFraction: 0.9 });
    const byKey = Object.fromEntries(clusters.map((c) => [c.key, [...c.processIds].sort()]));
    expect(byKey['unique.RNA']).toEqual(pids('a', 'b'));
    expect(byKey['unique.promoter']).toEqual(pids('c', 'd'));
  });

  it('excludes hub stores as cluster keys', () => {
    const nodes = ['a', 'b', 'c', 'd'].map((n) =>
      proc(n, { h: ['bulk'], s: n === 'd' ? ['unique', 'oriC'] : ['unique', 'RNA'] }));
    const { hubs, clusters } = clusterProcesses(nodes, { hubFraction: 0.75 });
    expect(hubs).toContain('bulk');
    expect(hubs).not.toContain('unique.oriC');
    expect(clusters.map((c) => c.key)).not.toContain('bulk');
  });

  it('routes hub-only processes to a labeled terminal bucket', () => {
    // NOTE: four processes, not the three in the task brief. `hubCut` has a
    // hard floor of 3 processes (a store touched by fewer than 3 of them is
    // never a hub, whatever hubFraction says), so a 3-process graph cannot
    // produce a hub at all and the bucket would never form.
    const nodes = [
      proc('a', { h: ['bulk'] }),
      proc('b', { h: ['bulk'] }),
      proc('c', { h: ['bulk'], s: ['unique', 'RNA'] }),
      proc('d', { h: ['bulk'], s: ['unique', 'RNA'] }),
    ];
    const { clusters } = clusterProcesses(nodes, { hubFraction: 0.6 });
    const bucket = clusters.find((c) => c.key === '~hub-only');
    expect(bucket?.processIds).toEqual(pids('a', 'b'));
    expect(bucket?.label).toMatch(/bulk/);
    expect(clusters.find((c) => c.key === 'unique.RNA')?.processIds).toEqual(pids('c', 'd'));
  });

  it('routes a process with NO store keys at all to the hub-only bucket', () => {
    // Real fixture case: `global_clock` wires only to `global_time` and
    // `timestep`, both filtered as noise, so its key map is empty. It must
    // still be assigned — never dropped, never crash on an absent first key.
    const nodes = [
      proc('global_clock', { t: ['global_time'] }, { s: ['timestep'] }),
      proc('a', { h: ['bulk'] }),
      proc('b', { h: ['bulk'] }),
      proc('c', { h: ['bulk'] }),
    ];
    const { clusters } = clusterProcesses(nodes, { hubFraction: 0.6 });
    const bucket = clusters.find((c) => c.key === '~hub-only');
    expect(bucket?.processIds).toContain(pid('global_clock'));
    // and it is assigned exactly once, to exactly one cluster
    const owning = clusters.filter((c) => c.processIds.includes(pid('global_clock')));
    expect(owning).toHaveLength(1);
  });

  it('diverts processes touching too many distinct keys to cross-cutting', () => {
    const many: Record<string, string[]> = {};
    for (let i = 0; i < 11; i++) many[`p${i}`] = ['unique', `s${i}`];
    const nodes = [
      proc('hub', many),
      proc('a', { s: ['unique', 's0'] }),
      proc('b', { s: ['unique', 's0'] }),
    ];
    const { clusters } = clusterProcesses(nodes, { hubFraction: 0.9, hubProcessKeyLimit: 8 });
    expect(clusters.find((c) => c.key === '~cross-cutting')?.processIds).toEqual([pid('hub')]);
  });

  it('excludes bookkeeping processes entirely', () => {
    const nodes = [
      proc('unique_update_1', { s: ['unique', 'RNA'] }),
      proc('real', { s: ['unique', 'RNA'] }),
    ];
    const ids = clusterProcesses(nodes, { hubFraction: 0.9 }).clusters.flatMap((c) => c.processIds);
    expect(ids).toEqual([pid('real')]);
  });

  it('returns an empty result for a graph with no processes', () => {
    expect(clusterProcesses([])).toEqual({ clusters: [], hubs: [] });
  });

  it('is deterministic across runs', () => {
    const nodes = ['a', 'b', 'c'].map((n) => proc(n, { s: ['unique', 'RNA'], t: ['bulk'] }));
    expect(JSON.stringify(clusterProcesses(nodes)))
      .toBe(JSON.stringify(clusterProcesses(nodes)));
  });

  it('orders clusters largest-first with the terminal buckets last', () => {
    const nodes = [
      proc('a', { s: ['unique', 'RNA'], h: ['bulk'] }),
      proc('b', { s: ['unique', 'RNA'], h: ['bulk'] }),
      proc('c', { s: ['unique', 'RNA'], h: ['bulk'] }),
      proc('d', { s: ['unique', 'promoter'], h: ['bulk'] }),
      proc('e', { s: ['unique', 'promoter'], h: ['bulk'] }),
      proc('f', { h: ['bulk'] }),
    ];
    const { clusters } = clusterProcesses(nodes, { hubFraction: 0.8 });
    expect(clusters.map((c) => c.key))
      .toEqual(['unique.RNA', 'unique.promoter', '~hub-only']);
  });
});
